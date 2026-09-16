"""
z.py - VERSÃO CORRIGIDA
Bot de atualização de planilhas a partir da API NEO (producao-painel-integration-v2)
com regras de negócio específicas para a operação DTX_RIB.

Fluxo:
PT0 - utilitários, logs, agendamento, normalizações
PT1 - coleta da API (filtra por nomeEquipe == "DTX_RIB"), salva raw em .json
PT2 - leitura das planilhas dos consultores (por nomeUsuario normalizado) e guarda em memória
PT3 - casamento API x planilhas + regras de negócio + montagem de PRÉVIA de updates
PT4 - aplicação de datas (LISTA_STATUS_DATAS + ÚLTIMA ALTERAÇÃO)
PT5 - envio real pro Google Sheets + notificações Telegram + logs

Observações importantes:
- Notificações EMERGENCIAIS são memorizadas por (status, consultor, cnpj, numero_pedido)
  e NÃO são limpas pelo reset de ciclo. São auto-podadas quando o mesmo combo chega
  em "ATIVADO 100%" ou "CANCELADO".
- "numeroPedidoVinculado" só preenche COTAÇÃO se for valor válido (não "0", "00"...)
  E somente se a coluna COTAÇÃO estiver vazia.
- Rodamos de 15 em 15 minutos de 07:00 até 22:00. Às 23:00 rodamos com janela de 24h.

CORREÇÕES APLICADAS (v2.1):
- Verifica se status realmente mudou antes de criar update
- Só atualiza data de última alteração quando há mudança real
- Proteção adicional para status que não devem ser alterados
"""

import os
import json
import time
import traceback
from datetime import datetime, timedelta, time as dt_time
from collections import defaultdict

from matplotlib.pylab import indices
import requests

import config  # configs sensíveis


# ------------------------------------------------------------------------------
# GLOBAIS / NORMALIZAÇÕES DE CONFIG
# ------------------------------------------------------------------------------

# cache de emergenciais que NÃO deve ser limpo a cada ciclo
# chave: (status, consultor, cnpj, numero_pedido) -> {"since": datetime}
EMERGENCIAS_VIVAS = {}

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RAW_DIR = os.path.join(BASE_DIR, "data")
LOG_DIR = os.path.join(BASE_DIR, "logs")

# pegar mapeamento de consultor -> planilha independentemente do nome no config
CONSULTOR_PARA_SHEET = getattr(
    config,
    "CONSULTOR_PARA_SHEET",
    getattr(config, "consultores_planilhas", {})
)

# fallback: primeira planilha do dicionário
FALLBACK_SHEET_ID = getattr(config, "FALLBACK_SHEET_ID", None)
if not FALLBACK_SHEET_ID and CONSULTOR_PARA_SHEET:
    FALLBACK_SHEET_ID = list(CONSULTOR_PARA_SHEET.values())[0]

# normalizar equivalências do config para UPPER, porque a gente faz .upper() no evento
EQUIV_NUMEROLINHA_PARA_TIPO_NORM = {
    (k or "").strip().upper(): (v or "").strip().upper()
    for k, v in getattr(config, "EQUIV_NUMEROLINHA_PARA_TIPO", {}).items()
    if v is not None
}

EQUIV_SOLICITACAO_PARA_TIPO_NORM = {
    (k or "").strip().upper(): (v or "").strip().upper()
    for k, v in getattr(config, "EQUIV_SOLICITACAO_PARA_TIPO", {}).items()
    if v is not None
}

RENOV_HERDAR_ATE_STATUS = getattr(config, "RENOV_HERDAR_ATE_STATUS", {"CONCLUÍDO INSPEÇÃO"})

COTACAO_PODE_SOBRESCREVER = getattr(config, "COTACAO_PODE_SOBRESCREVER", False)

# ------------------------------------------------------------------------------
# PT0 - UTILITÁRIOS / LOG / AGENDA
# ------------------------------------------------------------------------------
def decidir_cotacao(cotacao_atual: str, numero_pedido_vinc: str,
                    sheet_id: str, cnpj: str, linha_idx: int, contexto: str):
    """
    Decide (e loga) se devemos escrever COTAÇÃO e com qual valor.
    Retorna o novo valor (string) ou None se não for pra escrever.
    Regras:
      - precisa passar pela trava de tamanho (evita CNPJ/CPF)
      - se já existe valor:
          * se COTACAO_PODE_SOBRESCREVER=True -> sobrescreve e loga "SOBRESCRITA"
          * se False -> não sobrescreve e loga "NÃO sobreescrita"
    """
    v = (numero_pedido_vinc or "").strip()
    if _valor_vazio_ou_zero(v):
        return None

    if not cotacao_valida_por_tamanho(v):
        escrever_log(f"[PT3][{sheet_id}] COTAÇÃO BLOQUEADA por tamanho ({contexto}) L{linha_idx} CNPJ={cnpj}: '{v}'")
        return None

    if cotacao_atual:
        if COTACAO_PODE_SOBRESCREVER:
            escrever_log(
                f"[PT3][{sheet_id}] COTAÇÃO SOBRESCRITA ({contexto}) L{linha_idx} CNPJ={cnpj}: "
                f"NEO='{v}' planilha_anterior='{cotacao_atual}'"
            )
            return v
        else:
            escrever_log(
                f"[PT3][{sheet_id}] COTAÇÃO NÃO sobreescrita ({contexto}) L{linha_idx} CNPJ={cnpj}: "
                f"NEO='{v}' planilha='{cotacao_atual}'"
            )
            return None

    # Não havia valor: vamos setar e logar
    escrever_log(
        f"[PT3][{sheet_id}] COTAÇÃO definida ({contexto}) L{linha_idx} CNPJ={cnpj}: '{v}'"
    )
    return v

def cotacao_valida_por_tamanho(valor: str, limite: int = 10) -> bool:
    """
    True se, após normalizar para apenas dígitos, o tamanho for >0 e < limite.
    Evita cair CNPJ/CPF (11/14 dígitos) em COTAÇÃO.
    """
    v_num = normalizar_numero_pedido(valor)
    return 0 < len(v_num) < limite

def normalizar_numero_pedido(v: str) -> str:
    if v is None:
        return ""
    return "".join(ch for ch in str(v) if ch.isdigit())

def resolver_status_para_planilha(status_bruto: str):
    """
    Devolve uma tupla:
      (status_bruto, status_norm, status_final)

    - status_bruto  -> exatamente o que veio da API
    - status_norm   -> limpinho ("MV - ... (NEOCRM)" -> "...")
    - status_final  -> depois de aplicar STATUS_EQUIVALENTES
    """
    if not status_bruto:
        return "", "", ""

    # 1) normalizar
    status_norm = normalizar_status_neo(status_bruto)

    # 2) tentar equivalência primeiro pelo BRUTO
    if status_bruto in config.STATUS_EQUIVALENTES:
        status_final = config.STATUS_EQUIVALENTES[status_bruto]
        return status_bruto, status_norm, status_final

    # 3) tentar equivalência pelo NORMALIZADO
    if status_norm in config.STATUS_EQUIVALENTES:
        status_final = config.STATUS_EQUIVALENTES[status_norm]
        return status_bruto, status_norm, status_final

    # 4) se não achou, o que vai pra planilha é o normalizado mesmo
    return status_bruto, status_norm, status_norm

def status_esta_na_lista(lista: set, status_bruto: str, status_norm: str, status_final: str) -> bool:
    return (
        status_bruto in lista
        or status_norm in lista
        or status_final in lista
    )

def formatar_doc_br(doc: str) -> str:
    """
    Recebe algo tipo "12345678901" (CPF) ou "12345678000199" (CNPJ)
    e devolve com máscara.
    - 11 dígitos -> CPF: 000.000.000-00
    - 14 dígitos -> CNPJ: 00.000.000/0000-00
    Se não bater, devolve o próprio doc.
    """
    if not doc:
        return ""
    # tira tudo que não é dígito
    nums = "".join(ch for ch in str(doc) if ch.isdigit())
    if len(nums) == 11:
        return f"{nums[0:3]}.{nums[3:6]}.{nums[6:9]}-{nums[9:11]}"
    if len(nums) == 14:
        return f"{nums[0:2]}.{nums[2:5]}.{nums[5:8]}/{nums[8:12]}-{nums[12:14]}"
    # caso estranho: devolve original
    return doc

def ensure_dir(path: str):
    if not os.path.exists(path):
        os.makedirs(path, exist_ok=True)


def agora_local() -> datetime:
    return datetime.now()


def formatar_data_arquivo(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d_%H-%M-%S")


def escrever_log(msg: str):
    ensure_dir(LOG_DIR)
    agora = agora_local()
    nome = f"z-{agora.strftime('%d-%m-%Y')}_{agora.strftime('%H-%M')}.log"
    caminho = os.path.join(LOG_DIR, nome)
    linha = f"[{agora.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n"
    with open(caminho, "a", encoding="utf-8") as f:
        f.write(linha)
    print(linha, end="")


def normalizar_consultor(nome: str) -> str:
    if not nome:
        return ""
    if "_" in nome:
        return nome.split("_", 1)[1].strip().upper()
    return nome.strip().upper()


def normalizar_cnpj(cnpj: str) -> str:
    if not cnpj:
        return ""
    return "".join(ch for ch in cnpj if ch.isdigit())


def normalizar_status_neo(status_bruto: str) -> str:
    """
    Normaliza status que vêm do NEO tipo:
    - "MV - AGUARDANDO ACEITE (NEOCRM) (CPC)"
    - "FB - CONECTADO (NEOCRM)"
    - "PRD - SUPORTE (NEOCRM)"
    Regras:
      1. tira prefixos conhecidos (MV, FB, PRD, MV -, FB -, PRD - ...)
      2. corta tudo que vier depois de " ("
      3. UPPER final
    """
    if not status_bruto:
        return ""

    s = status_bruto.strip()

    # 1) remover prefixos conhecidos
    prefixes = [
        "MV - ", "MV-", "MV ",
        "FB - ", "FB-", "FB ",
    ]
    s_up = s.upper()
    for p in prefixes:
        if s_up.startswith(p.upper()):
            # remove exatamente o tamanho do prefixo original (não do upper)
            s = s[len(p):].lstrip()
            break

    # 2) cortar sufixos entre parênteses
    if " (" in s:
        s = s.split(" (", 1)[0]

    # 3) upper final
    return s.strip().upper()


def eh_dia_util(dt: datetime) -> bool:
    return dt.weekday() < 5


def arredondar_para_prox_15min(dt: datetime) -> datetime:
    minuto = (dt.minute // 15) * 15
    base = dt.replace(minute=minuto, second=0, microsecond=0)
    if base < dt:
        base = base + timedelta(minutes=15)
    return base


def proximo_horario_execucao(agora: datetime) -> datetime:
    """
    Regras:
    - fim de semana: sempre próxima segunda 07:00
    - se for exatamente o horário noturno (NIGHT_RUN_HOUR:NIGHT_RUN_MINUTE):
        -> próxima execução = próximo dia útil 07:00
    - se for antes de 07:00: próxima execução = hoje 07:00
    - se for entre 07:00 e 22:00: próxima execução = próximo múltiplo de 15min (até 22:00)
    - se for entre 22:00 e horário noturno: próxima execução = horário noturno de hoje
    - se já passou do horário noturno: próxima execução = próximo dia útil 07:00
    """
    # 1) fim de semana
    if not eh_dia_util(agora):
        dias_ate_seg = (7 - agora.weekday()) % 7
        if dias_ate_seg == 0:
            dias_ate_seg = 1
        prox = (agora + timedelta(days=dias_ate_seg)).replace(
            hour=7, minute=0, second=0, microsecond=0
        )
        return prox

    # 2) parâmetros de horário
    h = agora.time()
    business_start = config.BUSINESS_START          # ex: 07:00
    business_end = config.BUSINESS_END              # ex: 22:00
    night_run = dt_time(
        getattr(config, "NIGHT_RUN_HOUR", 23),       # ex: 0
        getattr(config, "NIGHT_RUN_MINUTE", 0),      # ex: 9
        0,
    )

    # 3) se for exatamente o horário noturno -> próxima = próximo dia útil 07:00
    if h.hour == night_run.hour and h.minute == night_run.minute:
        dia = agora + timedelta(days=1)
        while not eh_dia_util(dia):
            dia = dia + timedelta(days=1)
        return dia.replace(hour=business_start.hour, minute=0, second=0, microsecond=0)

    # 4) antes do horário comercial -> hoje 07:00
    if h < business_start:
        return agora.replace(hour=business_start.hour, minute=0, second=0, microsecond=0)

    # 5) entre 07:00 e 22:00 -> próximo múltiplo de 15min (mas não passa de 22:00)
    if business_start <= h <= business_end:
        prox = arredondar_para_prox_15min(agora)
        if prox.time() <= business_end:
            return prox
        # se o arredondamento passou de 22:00, cai nas regras abaixo

    # 6) entre 22:00 e horário noturno -> agenda pro horário noturno de hoje
    if business_end < h < night_run:
        return agora.replace(
            hour=night_run.hour,
            minute=night_run.minute,
            second=0,
            microsecond=0,
        )

    # 7) >= horário noturno -> próximo dia útil 07:00
    dia = agora + timedelta(days=1)
    while not eh_dia_util(dia):
        dia = dia + timedelta(days=1)
    return dia.replace(hour=business_start.hour, minute=0, second=0, microsecond=0)


def eh_janela_24h(dt: datetime) -> bool:
    hour = getattr(config, "NIGHT_RUN_HOUR", 23)
    minute = getattr(config, "NIGHT_RUN_MINUTE", 0)
    t = dt.time()
    return t.hour == hour and t.minute == minute


def enviar_telegram_bko(msg: str, sigla_bko: str = None):
    try:
        if sigla_bko:
            config.enviar_telegram_por_sigla(sigla_bko, msg)
        else:
            config.enviar_telegram_bko_geral(msg)
    except Exception as e:
        escrever_log(f"[TELEGRAM] Falha ao enviar: {e}")


# ------------------------------------------------------------------------------
# PT1 - COLETA DA API
# ------------------------------------------------------------------------------

def salvar_raw_api(dados: list, agora: datetime):
    pasta_dia = os.path.join(RAW_DIR, agora.strftime("%Y-%m-%d"))
    ensure_dir(pasta_dia)
    nome_arq = os.path.join(pasta_dia, f"{formatar_data_arquivo(agora)}.json")
    with open(nome_arq, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=4)
    escrever_log(f"[PT1] Raw salvo em {nome_arq}")


def api_buscar_eventos(janela_minutos: int, max_tentativas: int = 3) -> list:
    """
    Busca eventos da API com retry automático.
    
    Args:
        janela_minutos: Janela de tempo para buscar eventos
        max_tentativas: Número máximo de tentativas (padrão: 3)
    
    Returns:
        Lista de eventos filtrados
        
    Raises:
        RuntimeError: Se todas as tentativas falharem
    """
    agora = agora_local()
    inicio = agora - timedelta(minutes=janela_minutos)

    payload = {
        "tokenEstrutura": config.TOKEN_ESTRUTURA,
        "tokenUsuario": config.TOKEN_USUARIO,
        "dataHoraInicioCarga": inicio.strftime("%Y-%m-%d %H:%M:%S"),
        "dataHoraFimCarga": agora.strftime("%Y-%m-%d %H:%M:%S"),
        "painelId": config.PAINEL_ID,
        "outputFormat": "json",
    }

    headers = {"Content-Type": "application/json"}

    api_url = getattr(config, "API_PRODUCAO_URL", getattr(config, "API_URL", None))
    if not api_url:
        raise RuntimeError("URL da API não encontrada no config (API_PRODUCAO_URL ou API_URL).")

    # ===== LOOP DE RETRY =====
    ultimo_erro = None
    
    for tentativa in range(1, max_tentativas + 1):
        try:
            timeout_segundos = 120  # Aumentado de 60s para 120s
            
            escrever_log(
                f"[PT1] Tentativa {tentativa}/{max_tentativas}: "
                f"Consultando API NEO janela={janela_minutos}min (timeout={timeout_segundos}s)..."
            )
            
            resp = requests.post(
                api_url, 
                headers=headers, 
                json=payload, 
                timeout=timeout_segundos
            )
            
            if resp.status_code != 200:
                raise RuntimeError(f"API retornou {resp.status_code}: {resp.text}")

            try:
                dados = resp.json()
            except Exception:
                raise RuntimeError("API não retornou JSON válido.")

            total = len(dados)
            filtrados = [item for item in dados if item.get("nomeEquipe") == "DTX_RIB"]
            
            escrever_log(
                f"[PT1] OK - API respondeu com sucesso (tentativa {tentativa}): "
                f"{total} registros. Apos filtro nomeEquipe=DTX_RIB: {len(filtrados)}"
            )

            salvar_raw_api(filtrados, agora)
            return filtrados
            
        except requests.exceptions.Timeout as e:
            ultimo_erro = e
            escrever_log(
                f"[PT1] TIMEOUT na tentativa {tentativa}/{max_tentativas}: {e}"
            )
            
        except requests.exceptions.RequestException as e:
            ultimo_erro = e
            escrever_log(
                f"[PT1] ERRO DE REDE na tentativa {tentativa}/{max_tentativas}: {e}"
            )
            
        except Exception as e:
            ultimo_erro = e
            escrever_log(
                f"[PT1] ERRO na tentativa {tentativa}/{max_tentativas}: {e}"
            )
        
        # Se não foi a última tentativa, aguarda antes de tentar novamente
        if tentativa < max_tentativas:
            # Backoff exponencial: 5s, 10s, 20s
            tempo_espera = 5 * (2 ** (tentativa - 1))
            escrever_log(
                f"[PT1] Aguardando {tempo_espera}s antes da proxima tentativa..."
            )
            time.sleep(tempo_espera)
    
    # Se chegou aqui, todas as tentativas falharam
    msg_erro = f"API NEO falhou apos {max_tentativas} tentativas. Ultimo erro: {ultimo_erro}"
    raise RuntimeError(msg_erro)

# ------------------------------------------------------------------------------
# PT1.b - EMERGÊNCIAIS
# ------------------------------------------------------------------------------

def _key_emerg(evento: dict) -> tuple:
    consultor = normalizar_consultor(evento.get("nomeUsuario", ""))
    cnpj = normalizar_cnpj(evento.get("cpfCnpj", ""))
    status = (evento.get("nomeEtapa") or "").strip()
    numero_pedido = (evento.get("numeroPedido") or "").strip()
    return (status, consultor, cnpj, numero_pedido)


def _poda_emergenciais(ev: dict):
    consultor = normalizar_consultor(ev.get("nomeUsuario", ""))
    cnpj = normalizar_cnpj(ev.get("cpfCnpj", ""))
    numero_pedido = (ev.get("numeroPedido") or "").strip()
    chaves_remover = []
    for (st, cons, cnpj2, nump) in list(EMERGENCIAS_VIVAS.keys()):
        if cons == consultor and cnpj2 == cnpj and nump == numero_pedido:
            chaves_remover.append((st, cons, cnpj2, nump))
    for ch in chaves_remover:
        EMERGENCIAS_VIVAS.pop(ch, None)
        escrever_log(f"[EMERG] Removido do cache (resolvido): {ch}")


def detectar_e_notificar_emergenciais(eventos: list, planilhas_info: dict, consultor_para_sheet: dict):
    agora = agora_local()
    for ev in eventos:
        # pega o que veio
        status_bruto_api = (ev.get("nomeEtapa") or "").strip()

        # resolve pros três formatos
        status_bruto, status_norm, status_final = resolver_status_para_planilha(status_bruto_api)

        # se chegou um que resolve o caso (ATIVADO 100% / CANCELADO), podar
        if status_esta_na_lista({"ATIVADO 100%", "CANCELADO"}, status_bruto, status_norm, status_final):
            _poda_emergenciais(ev)
            continue

        # não é emergencial? pula
        if not status_esta_na_lista(config.STATUS_EMERGENCIA, status_bruto, status_norm, status_final):
            continue

        consultor = normalizar_consultor(ev.get("nomeUsuario", ""))
        cnpj_raw = (ev.get("cpfCnpj") or "").strip()
        cnpj_norm = normalizar_cnpj(cnpj_raw)
        cnpj_formatado = formatar_doc_br(cnpj_raw or cnpj_norm)

        sheet_id = consultor_para_sheet.get(consultor)

        # chave do cache TEM que usar o status_final, senão não bate depois
        num_pedido = (ev.get("numeroPedido") or "").strip()
        chave = (status_final, consultor, cnpj_norm, num_pedido)
        if chave in EMERGENCIAS_VIVAS:
            continue  # já avisei esse combo

        # tenta achar BKO daquela linha
        sigla_bko = None
        if sheet_id and sheet_id in planilhas_info:
            info = planilhas_info[sheet_id]
            idxs = info["indices"]
            if idxs["BKO"] is not None and idxs["CNPJ/CPF"] is not None:
                for linha in info["linhas"]:
                    if normalizar_cnpj(linha[idxs["CNPJ/CPF"]]) == cnpj_norm:
                        sigla_bko = (linha[idxs["BKO"]] or "").strip()
                        break

        msg = (
            "⚠️ STATUS EMERGENCIAL DETECTADO\n"
            f"Consultor: {consultor or '---'}\n"
            f"CNPJ/CPF: {cnpj_formatado}\n"
            f"Nº Pedido: {num_pedido or '---'}\n"
            f"Status (planilha): {status_final}\n"
        )
        enviar_telegram_bko(msg, sigla_bko)

        EMERGENCIAS_VIVAS[chave] = {"since": agora}
        escrever_log(f"[EMERG] Registrado emergencial {chave}")



# ------------------------------------------------------------------------------
# PT2 - LEITURA DAS PLANILHAS
# ------------------------------------------------------------------------------

def obter_cliente_gsheets():
    return config.get_gspread_client()


def localizar_indices_header(header: list) -> dict:
    indices = {
        "BKO": None,
        "CONSULTOR": None,
        "DATA DE INPUT": None,
        "DATA DE ACEITE": [],
        "DATA DE ATIVAÇÃO": [],
        "CNPJ/CPF": None,
        "TIPO DE PEDIDO": None,
        "PRODUTOS": None,
        "STATUS": [],
        "OBSERVAÇÃO": None,
        "ÚLTIMA ALTERAÇÃO": None,
        "COTAÇÃO": None,
        "CHIP...": None,
        "NUMERO PEDIDO": None,
    }

    alvo_ultima = config.ULTIMA_ALTERACAO_COLUNA.upper()

    for idx, col in enumerate(header):
        col_norm = (col or "").strip().upper()
        if col_norm == "BKO" and indices["BKO"] is None:
            indices["BKO"] = idx
        elif col_norm == "CONSULTOR" and indices["CONSULTOR"] is None:
            indices["CONSULTOR"] = idx
        elif col_norm == "DATA DE INPUT" and indices["DATA DE INPUT"] is None:
            indices["DATA DE INPUT"] = idx
        elif col_norm == "DATA DE ACEITE":
            indices["DATA DE ACEITE"].append(idx)
        elif col_norm == "DATA DE ATIVAÇÃO":
            indices["DATA DE ATIVAÇÃO"].append(idx)
        elif col_norm in ("CNPJ/CPF", "CNPJ", "CPF") and indices["CNPJ/CPF"] is None:
            indices["CNPJ/CPF"] = idx
        elif col_norm == "TIPO DE PEDIDO" and indices["TIPO DE PEDIDO"] is None:
            indices["TIPO DE PEDIDO"] = idx
        elif col_norm == "PRODUTOS" and indices["PRODUTOS"] is None:
            indices["PRODUTOS"] = idx
        elif col_norm == "STATUS":
            indices["STATUS"].append(idx)
        elif col_norm in ("OBSERVAÇÃO", "OBSERVACAO") and indices["OBSERVAÇÃO"] is None:
            indices["OBSERVAÇÃO"] = idx
        elif (col_norm in ("ÚLTIMA ALTERAÇÃO", "ULTIMA ALTERACAO")) or (col_norm == alvo_ultima):
            if indices["ÚLTIMA ALTERAÇÃO"] is None:
                indices["ÚLTIMA ALTERAÇÃO"] = idx
        elif col_norm == "COTAÇÃO" and indices["COTAÇÃO"] is None:
            indices["COTAÇÃO"] = idx
        elif col_norm.startswith("CHIP"):
            indices["CHIP..."] = idx
        elif col_norm in ("NUMERO PEDIDO", "NÚMERO PEDIDO") and indices["NUMERO PEDIDO"] is None:
            indices["NUMERO PEDIDO"] = idx

    if indices["DATA DE ACEITE"]:
        indices["DATA DE ACEITE"] = indices["DATA DE ACEITE"][1] if len(indices["DATA DE ACEITE"]) > 1 else indices["DATA DE ACEITE"][0]
    else:
        indices["DATA DE ACEITE"] = None

    if indices["DATA DE ATIVAÇÃO"]:
        indices["DATA DE ATIVAÇÃO"] = indices["DATA DE ATIVAÇÃO"][1] if len(indices["DATA DE ATIVAÇÃO"]) > 1 else indices["DATA DE ATIVAÇÃO"][0]
    else:
        indices["DATA DE ATIVAÇÃO"] = None

    if indices["STATUS"]:
        indices["STATUS"] = indices["STATUS"][1] if len(indices["STATUS"]) > 1 else indices["STATUS"][0]
    else:
        indices["STATUS"] = None

    return indices


def carregar_planilhas_por_consultor(eventos: list):
    cliente = obter_cliente_gsheets()

    consultores = {normalizar_consultor(ev.get("nomeUsuario", "")) for ev in eventos}

    planilhas_ids = set()
    consultor_para_sheet = {}
    for cons in consultores:
        sheet_id = CONSULTOR_PARA_SHEET.get(cons, FALLBACK_SHEET_ID)
        consultor_para_sheet[cons] = sheet_id
        planilhas_ids.add(sheet_id)

    planilhas_info = {}

    for sheet_id in planilhas_ids:
        try:
            sh = cliente.open_by_key(sheet_id)
            ws = sh.sheet1
            all_values = ws.get_all_values()
            if not all_values:
                continue
            header = all_values[0]
            linhas = all_values[1:]
            indices = localizar_indices_header(header)

            planilhas_info[sheet_id] = {
                "worksheet": ws,
                "header": header,
                "indices": indices,
                "linhas": linhas,
            }

            escrever_log(f"[PT2] Carregando planilhas de {len(consultores)} consultores "
    f"presentes nos {len(eventos)} eventos da API")
            time.sleep(10)
        except Exception as e:
            escrever_log(f"[PT2] Erro ao carregar planilha {sheet_id}: {e}")
            time.sleep(10)

    return planilhas_info, consultor_para_sheet


# ------------------------------------------------------------------------------
# PT3 - CASAMENTO E MONTAGEM DE PRÉVIA
# ------------------------------------------------------------------------------
def _valor_vazio_ou_zero(v):
    if v is None:
        return True
    v = str(v).strip()
    if v == "":
        return True
    if all(ch == "0" for ch in v):
        return True
    return False

def casar_evento_com_linha(evento: dict, linhas_cnpj: list, indices: dict):
    numero_pedido_api_raw = (evento.get("numeroPedido") or "").strip()
    numero_pedido_api = normalizar_numero_pedido(numero_pedido_api_raw)
    numero_linha_api = (evento.get("numeroLinha") or "").strip().upper()
    solicitacao_api = (evento.get("solicitacao") or "").strip().upper()

    # 1) por número normalizado
    if numero_pedido_api and indices["NUMERO PEDIDO"] is not None:
        for idx_linha, linha in linhas_cnpj:
            num_planilha = normalizar_numero_pedido((linha[indices["NUMERO PEDIDO"]] or "").strip())
            if num_planilha == numero_pedido_api:
                return idx_linha, linha
        # API trouxe número e há múltiplas linhas -> NÃO cair em fallback por tipo
        if len(linhas_cnpj) > 1:
            return None, None
        # Se só há 1 linha, vamos permitir fallback por tipo mais abaixo

    # 2) fallback por numeroLinha/solicitacao -> tipo
    tipo_equiv = EQUIV_NUMEROLINHA_PARA_TIPO_NORM.get(numero_linha_api)
    if not tipo_equiv and solicitacao_api:
        tipo_equiv = EQUIV_SOLICITACAO_PARA_TIPO_NORM.get(solicitacao_api)

    if tipo_equiv and indices["TIPO DE PEDIDO"] is not None:
        for idx_linha, linha in linhas_cnpj:
            if (linha[indices["TIPO DE PEDIDO"]] or "").strip().upper() == tipo_equiv:
                return idx_linha, linha

    return None, None

def _avisar_multiplos_novos_sem_numero(sheet_id, linhas_cnpj, indices):
    qtd_sem_numero = 0
    sigla_bko = None
    consultor_nome = None
    cnpj_raw = None

    for _, linha in linhas_cnpj:
        tipo = (linha[indices["TIPO DE PEDIDO"]] or "").strip().upper() if indices["TIPO DE PEDIDO"] is not None else ""
        num = (linha[indices["NUMERO PEDIDO"]] or "").strip() if indices["NUMERO PEDIDO"] is not None else ""
        if tipo == "NOVO" and not num:
            qtd_sem_numero += 1
            if sigla_bko is None and indices["BKO"] is not None:
                sigla_bko = (linha[indices["BKO"]] or "").strip()
            if consultor_nome is None and indices["CONSULTOR"] is not None:
                consultor_nome = (linha[indices["CONSULTOR"]] or "").strip()
            if cnpj_raw is None and indices["CNPJ/CPF"] is not None:
                cnpj_raw = (linha[indices["CNPJ/CPF"]] or "").strip()

    if qtd_sem_numero >= 2:
        msg = (
            f"🔔 Necessário preencher 'NUMERO PEDIDO'\n"
            f"Consultor: {consultor_nome or '---'}\n"
            f"CNPJ/CPF: {cnpj_raw or '---'}\n"
            f"Linhas com 'TIPO DE PEDIDO' = 'NOVO' sem número: {qtd_sem_numero}"
        )
        enviar_telegram_bko(msg, sigla_bko)


def _limpar_observacao_se_tudo_ativado(sheet_id, linhas_cnpj, indices, updates_por_sheet):
    status_final = {}
    for idx, linha in linhas_cnpj:
        atual = (linha[indices["STATUS"]] or "").strip() if indices["STATUS"] is not None else ""
        status_final[idx] = atual

    for upd in updates_por_sheet[sheet_id]:
        ri = upd["row_index"]
        if ri in status_final and "STATUS" in upd["values"]:
            status_final[ri] = upd["values"]["STATUS"]

    if not status_final:
        return

    if all(st == "ATIVADO 100%" for st in status_final.values()):
        for idx, linha in linhas_cnpj:
            if indices["OBSERVAÇÃO"] is not None:
                obs_atual = (linha[indices["OBSERVAÇÃO"]] or "").strip()
                if obs_atual:
                    updates_por_sheet[sheet_id].append({
                        "row_index": idx,
                        "values": {"OBSERVAÇÃO": ""},
                        "cnpj": normalizar_cnpj(linha[indices["CNPJ/CPF"]]) if indices["CNPJ/CPF"] is not None else "",
                    })


def _pegar_sigla_bko_primeira(linhas_cnpj, indices):
    if indices["BKO"] is None:
        return None
    for _, linha in linhas_cnpj:
        v = (linha[indices["BKO"]] or "").strip()
        if v:
            return v
    return None


from collections import defaultdict

def montar_updates(eventos: list, planilhas_info: dict, consultor_para_sheet: dict) -> dict:
    from collections import defaultdict
    updates_por_sheet = defaultdict(list)

    # eventos por CNPJ
    eventos_por_cnpj = defaultdict(list)
    for ev in eventos:
        cnpj_norm = normalizar_cnpj(ev.get("cpfCnpj", ""))
        eventos_por_cnpj[cnpj_norm].append(ev)

    for sheet_id, info in planilhas_info.items():
        indices = info["indices"]
        linhas = info["linhas"]

        # agrupar planilha por CNPJ
        linhas_por_cnpj = defaultdict(list)
        for i, linha in enumerate(linhas, start=2):
            cnpj_planilha = ""
            if indices["CNPJ/CPF"] is not None and indices["CNPJ/CPF"] < len(linha):
                cnpj_planilha = normalizar_cnpj(linha[indices["CNPJ/CPF"]])
            if not cnpj_planilha:
                continue
            linhas_por_cnpj[cnpj_planilha].append((i, linha))

        for cnpj, linhas_cnpj in linhas_por_cnpj.items():
            eventos_cnpj = eventos_por_cnpj.get(cnpj, [])
            if not eventos_cnpj:
                continue

            for ev in eventos_cnpj:
                status_bruto = (ev.get("nomeEtapa") or "").strip()
                numero_pedido_api = (ev.get("numeroPedido") or "").strip()
                numero_linha_api = (ev.get("numeroLinha") or "").strip()
                solicitacao_api = (ev.get("solicitacao") or "").strip()

                # 1) se tem equivalente explícito, usa
                if status_bruto in config.STATUS_EQUIVALENTES:
                    novo_status = config.STATUS_EQUIVALENTES[status_bruto]
                    status_origem = "equivalente"
                else:
                    novo_status = normalizar_status_neo(status_bruto)
                    status_origem = "normalizado"

                # 2) se mesmo assim estiver na lista de ignorar, sai
                if novo_status in config.STATUS_IGNORAR:
                    escrever_log(
                        f"[PT3][{sheet_id}] IGNORADO: CNPJ={cnpj} pedido={numero_pedido_api} "
                        f"status='{status_bruto}'->'{novo_status}' em STATUS_IGNORAR"
                    )
                    continue

                # tenta casar normalmente
                idx_linha, linha_planilha = casar_evento_com_linha(ev, linhas_cnpj, indices)

                # =========================
                # CASO SEM MATCH
                # =========================
                if idx_linha is None:
                    if len(linhas_cnpj) == 1:
                        only_idx, only_line = linhas_cnpj[0]

                        cnpj_raw = ""
                        if indices.get("CNPJ/CPF") is not None:
                            cnpj_raw = (only_line[indices["CNPJ/CPF"]] or "").strip()

                        consultor_raw = ""
                        if indices.get("CONSULTOR") is not None:
                            consultor_raw = (only_line[indices["CONSULTOR"]] or "").strip()
                        if not consultor_raw:
                            consultor_raw = (ev.get("nomeUsuario") or "").strip()

                        # ===== CORREÇÃO: Verificar se status mudou =====
                        status_atual_planilha = (only_line[indices["STATUS"]] or "").strip() if indices["STATUS"] is not None else ""
                        
                        update_vals = {}
                        
                        # Só adiciona STATUS se mudou
                        if novo_status != status_atual_planilha:
                            update_vals["STATUS"] = novo_status
                        else:
                            escrever_log(
                                f"[PT3][{sheet_id}] FALLBACK-1LINHA STATUS INALTERADO: CNPJ={cnpj_raw or cnpj} "
                                f"L{only_idx} status='{status_atual_planilha}' (sem mudança)"
                            )

                        if numero_pedido_api and indices["NUMERO PEDIDO"] is not None:
                            num_atual = (only_line[indices["NUMERO PEDIDO"]] or "").strip()
                            if not num_atual:
                                update_vals["NUMERO PEDIDO"] = numero_pedido_api

                        numero_pedido_vinc = (ev.get("numeroPedidoVinculado") or "").strip()
                        if indices["COTAÇÃO"] is not None:
                            cotacao_atual = (only_line[indices["COTAÇÃO"]] or "").strip()
                            novo_valor_cot = decidir_cotacao(
                                cotacao_atual, numero_pedido_vinc, sheet_id, cnpj, only_idx, "fallback-1linha"
                            )
                            if novo_valor_cot is not None:
                                update_vals["COTAÇÃO"] = novo_valor_cot

                        # ===== CORREÇÃO: Só adiciona se houver mudanças =====
                        if update_vals:
                            updates_por_sheet[sheet_id].append({
                                "row_index": only_idx,
                                "values": update_vals,
                                "cnpj": cnpj,
                                "cnpj_raw": cnpj_raw,
                            })
                            escrever_log(
                                f"[PT3][{sheet_id}] FALLBACK-1LINHA: CNPJ={cnpj_raw or cnpj} consultor='{consultor_raw}' "
                                f"pedido={numero_pedido_api or '---'} numeroLinha='{numero_linha_api or ''}' "
                                f"solicitacao='{solicitacao_api or ''}' aplicado na única linha L{only_idx} "
                                f"mudancas={list(update_vals.keys())}"
                            )
                        else:
                            escrever_log(
                                f"[PT3][{sheet_id}] FALLBACK-1LINHA SEM MUDANÇAS: CNPJ={cnpj_raw or cnpj} "
                                f"L{only_idx} (evento ignorado)"
                            )

                    else:
                        sigla_bko = _pegar_sigla_bko_primeira(linhas_cnpj, indices)

                        cnpj_raw = ""
                        consultor_raw = ""
                        if linhas_cnpj:
                            first_idx, first_line = linhas_cnpj[0]
                            if indices.get("CNPJ/CPF") is not None:
                                cnpj_raw = (first_line[indices["CNPJ/CPF"]] or "").strip()
                            if indices.get("CONSULTOR") is not None:
                                consultor_raw = (first_line[indices["CONSULTOR"]] or "").strip()
                        if not consultor_raw:
                            consultor_raw = (ev.get("nomeUsuario") or "").strip()
                        cnpj_formatado = formatar_doc_br(cnpj_raw or cnpj)

                        msg = (
                            "⚠️ ROBÔ NÃO CONSEGUIU CASAR O EVENTO COM NENHUMA LINHA\n"
                            f"Consultor: {consultor_raw or '---'}\n"
                            f"CNPJ: {cnpj_formatado}\n"
                            f"Nº Pedido: {numero_pedido_api or '---'}\n"
                            "Motivo: há mais de uma linha para este CNPJ na planilha e o robô não sabe qual atualizar.\n"
                            "Favor ajustar manualmente."
                        )
                        enviar_telegram_bko(msg, sigla_bko)
                        escrever_log(
                            f"[PT3][{sheet_id}] SEM MATCH (CNPJ com múltiplas linhas): consultor='{consultor_raw}' "
                            f"CNPJ={cnpj_raw or cnpj} pedido={numero_pedido_api or '---'} "
                            f"numeroLinha='{numero_linha_api or ''}' solicitacao='{solicitacao_api or ''}' -> avisado BKO"
                        )

                    continue  # fim do SEM MATCH

                # =========================
                # DAQUI PRA BAIXO TEMOS UMA LINHA ALVO
                # =========================

                # Guarda de CONFLITO DE NÚMERO (não atualiza a linha errada e redireciona)
                if numero_pedido_api and indices["NUMERO PEDIDO"] is not None:
                    num_api_norm = normalizar_numero_pedido(numero_pedido_api)
                    num_atual_raw = (linha_planilha[indices["NUMERO PEDIDO"]] or "").strip()
                    num_atual_norm = normalizar_numero_pedido(num_atual_raw)

                    if num_atual_norm and num_atual_norm != num_api_norm:
                        escrever_log(
                            f"[PT3][{sheet_id}] CONFLITO NUMERO: CNPJ={cnpj} L{idx_linha} "
                            f"planilha='{num_atual_raw}' api='{numero_pedido_api}' -> pulando linha"
                        )
                        # Redireciona update para as linhas com o MESMO número da API
                        for idx_linha2, linha2 in linhas_cnpj:
                            num2_norm = normalizar_numero_pedido((linha2[indices['NUMERO PEDIDO']] or '').strip()) \
                                        if indices['NUMERO PEDIDO'] is not None else ""
                            if num2_norm == num_api_norm:
                                # ===== CORREÇÃO: Verificar se status mudou =====
                                status_atual2 = (linha2[indices["STATUS"]] or "").strip() if indices["STATUS"] is not None else ""
                                if status_atual2 != novo_status:
                                    updates_por_sheet[sheet_id].append({
                                        "row_index": idx_linha2,
                                        "values": {"STATUS": novo_status},
                                        "cnpj": cnpj,
                                    })
                                    escrever_log(
                                        f"[PT3][{sheet_id}] REDIRECIONADO: CNPJ={cnpj} L{idx_linha2} "
                                        f"recebeu '{novo_status}' (match por número)"
                                    )
                        continue  # não siga com a linha errada

                if indices["STATUS"] is not None:
                    status_atual = (linha_planilha[indices["STATUS"]] or "").strip()
                else:
                    status_atual = ""

                if status_atual in config.STATUS_IGNORAR_GSHEETS:
                    escrever_log(
                        f"[PT3][{sheet_id}] LINHA TRAVADA: CNPJ={cnpj} L{idx_linha} STATUS_ATUAL='{status_atual}'"
                    )
                    continue

                if indices["TIPO DE PEDIDO"] is not None:
                    tipo_linha = (linha_planilha[indices["TIPO DE PEDIDO"]] or "").strip().upper()
                else:
                    tipo_linha = ""

                if tipo_linha in config.TIPO_PEDIDO_IGNORAR_LIST:
                    escrever_log(
                        f"[PT3][{sheet_id}] TIPO_IGNORAR: CNPJ={cnpj} L{idx_linha} TIPO='{tipo_linha}'"
                    )
                    continue

                # TT PJ/PJ trava o resto (menos outras TT)
                if tipo_linha == "TRANSF. TITULAR PJ/PJ" and novo_status != "ATIVADO 100%":
                    # ===== CORREÇÃO: Verificar se status mudou =====
                    status_atual_real = (linha_planilha[indices["STATUS"]] or "").strip() if indices["STATUS"] is not None else ""
                    
                    update_vals = {}
                    
                    if novo_status != status_atual_real:
                        update_vals["STATUS"] = novo_status

                    if numero_pedido_api and indices["NUMERO PEDIDO"] is not None:
                        numero_atual = (linha_planilha[indices["NUMERO PEDIDO"]] or "").strip()
                        if not numero_atual:
                            update_vals["NUMERO PEDIDO"] = numero_pedido_api

                    numero_pedido_vinc = (ev.get("numeroPedidoVinculado") or "").strip()
                    if indices["COTAÇÃO"] is not None:
                        cotacao_atual = (linha_planilha[indices["COTAÇÃO"]] or "").strip()
                        novo_valor_cot = decidir_cotacao(
                            cotacao_atual, numero_pedido_vinc, sheet_id, cnpj, idx_linha, "TT PJ/PJ"
                        )
                        if novo_valor_cot is not None:
                            update_vals["COTAÇÃO"] = novo_valor_cot

                    if update_vals:
                        updates_por_sheet[sheet_id].append({
                            "row_index": idx_linha,
                            "values": update_vals,
                            "cnpj": cnpj,
                        })

                    for idx_linha2, linha2 in linhas_cnpj:
                        if idx_linha2 == idx_linha:
                            continue
                        tipo2 = (linha2[indices["TIPO DE PEDIDO"]] or "").strip().upper() if indices["TIPO DE PEDIDO"] is not None else ""
                        if tipo2 == "TRANSF. TITULAR PJ/PJ":
                            continue

                        # ✅ CORREÇÃO 1: verificar se já está em STATUS_IGNORAR_GSHEETS
                        status_atual2 = (linha2[indices["STATUS"]] or "").strip() if indices["STATUS"] is not None else ""
                        if status_atual2 in config.STATUS_IGNORAR_GSHEETS:
                            escrever_log(
                                f"[PT3][{sheet_id}] TT PJ/PJ: L{idx_linha2} já travada "
                                f"(status='{status_atual2}') -> pulando"
                            )
                            continue

                        # ✅ CORREÇÃO 2: verificar se já é "AGUARDANDO CONCLUIR TT"
                        if status_atual2 == "AGUARDANDO CONCLUIR TT":
                            escrever_log(
                                f"[PT3][{sheet_id}] TT PJ/PJ: L{idx_linha2} já em "
                                f"'AGUARDANDO CONCLUIR TT' -> sem mudança"
                            )
                            continue

                        updates_por_sheet[sheet_id].append({
                            "row_index": idx_linha2,
                            "values": {"STATUS": "AGUARDANDO CONCLUIR TT"},
                            "cnpj": cnpj,
                        })
                    escrever_log(f"[PT3][{sheet_id}] TT PJ/PJ travou CNPJ={cnpj}, mas poupou outras TT")
                    continue

                # RENOVAÇÃO sem chip (mesma lógica que você já tinha)
                if tipo_linha == "RENOVAÇÃO":
                    tem_chip = False
                    if indices["CHIP..."] is not None:
                        tem_chip = bool((linha_planilha[indices["CHIP..."]] or "").strip())
                    if not tem_chip:
                        if status_atual == "CONCLUÍDO INSPEÇÃO" and novo_status != "ATIVADO 100%":
                            escrever_log(
                                f"[PT3][{sheet_id}] RENOVAÇÃO sem chip TRAVADA em CONCLUÍDO INSPEÇÃO: "
                                f"CNPJ={cnpj} L{idx_linha} novo='{novo_status}'"
                            )
                            continue

                # ===== CORREÇÃO PROBLEMA 2: Verificar se status mudou =====
                status_atual_real = (linha_planilha[indices["STATUS"]] or "").strip() if indices["STATUS"] is not None else ""

                # monta update da linha principal
                update_vals = {}

                # Só adiciona STATUS se realmente mudou
                if novo_status != status_atual_real:
                    update_vals["STATUS"] = novo_status
                else:
                    escrever_log(
                        f"[PT3][{sheet_id}] STATUS INALTERADO: CNPJ={cnpj} L{idx_linha} "
                        f"status='{status_atual_real}' (sem mudança)"
                    )

                if numero_pedido_api and indices["NUMERO PEDIDO"] is not None:
                    numero_atual = (linha_planilha[indices["NUMERO PEDIDO"]] or "").strip()
                    if not numero_atual:
                        update_vals["NUMERO PEDIDO"] = numero_pedido_api

                numero_pedido_vinc = (ev.get("numeroPedidoVinculado") or "").strip()
                if indices["COTAÇÃO"] is not None:
                    cotacao_atual = (linha_planilha[indices["COTAÇÃO"]] or "").strip()
                    novo_valor_cot = decidir_cotacao(
                        cotacao_atual, numero_pedido_vinc, sheet_id, cnpj, idx_linha, "update padrão"
                    )
                    if novo_valor_cot is not None:
                        update_vals["COTAÇÃO"] = novo_valor_cot

                # ===== CORREÇÃO PROBLEMA 2: Só criar update se houver mudanças =====
                if update_vals:
                    updates_por_sheet[sheet_id].append({
                        "row_index": idx_linha,
                        "values": update_vals,
                        "cnpj": cnpj,
                    })
                    escrever_log(
                        f"[PT3][{sheet_id}] UPDATE: CNPJ={cnpj} L{idx_linha} "
                        f"mudancas={list(update_vals.keys())} status_api='{status_bruto}'->'{novo_status}' ({status_origem})"
                    )
                else:
                    escrever_log(
                        f"[PT3][{sheet_id}] SEM MUDANÇAS: CNPJ={cnpj} L{idx_linha} "
                        f"status_api='{status_bruto}'->'{novo_status}' (evento ignorado)"
                    )

                # =====================================================
                # MESMO NUMERO PEDIDO -> TODAS AS OUTRAS
                # =====================================================
                if numero_pedido_api and indices["NUMERO PEDIDO"] is not None:
                    num_api_norm = normalizar_numero_pedido(numero_pedido_api)
                    for idx_linha2, linha2 in linhas_cnpj:
                        if idx_linha2 == idx_linha:
                            continue
                        num2_norm = normalizar_numero_pedido((linha2[indices["NUMERO PEDIDO"]] or "").strip())
                        if num2_norm != num_api_norm:
                            continue

                        status_atual2 = (linha2[indices["STATUS"]] or "").strip() if indices["STATUS"] is not None else ""
                        if status_atual2 in config.STATUS_IGNORAR_GSHEETS:
                            continue

                        tipo2 = (linha2[indices["TIPO DE PEDIDO"]] or "").strip().upper() if indices["TIPO DE PEDIDO"] is not None else ""
                        if tipo2 in config.TIPO_PEDIDO_IGNORAR_LIST:
                            continue

                        # ===== CORREÇÃO: Só atualiza se status realmente mudou =====
                        if status_atual2 != novo_status:
                            updates_por_sheet[sheet_id].append({
                                "row_index": idx_linha2,
                                "values": {"STATUS": novo_status},
                                "cnpj": cnpj,
                            })
                            escrever_log(
                                f"[PT3][{sheet_id}] MESMO NUMERO PEDIDO: CNPJ={cnpj} L{idx_linha2} "
                                f"recebeu '{novo_status}' porque NUMERO PEDIDO={numero_pedido_api}"
                            )
                        else:
                            escrever_log(
                                f"[PT3][{sheet_id}] MESMO NUMERO (sem mudança): CNPJ={cnpj} L{idx_linha2} "
                                f"já estava em '{status_atual2}'"
                            )


                # =====================================================
                # HERANÇA RENOVAÇÃO -> RENOV_COM_OUTRO (nova forma)
                # =====================================================
                if tipo_linha == "RENOVAÇÃO":
                    # herda sempre que:
                    # - outra linha está em RENOV_COM_OUTRO
                    # - outra linha NÃO tem numero pedido
                    # - novo_status NÃO é ATIVADO 100%
                    if novo_status != "ATIVADO 100%":
                        for idx_linha2, linha2 in linhas_cnpj:
                            if idx_linha2 == idx_linha:
                                continue
                            tipo2 = (linha2[indices["TIPO DE PEDIDO"]] or "").strip().upper() if indices["TIPO DE PEDIDO"] is not None else ""
                            if tipo2 in config.RENOV_COM_OUTRO:
                                num2 = (linha2[indices["NUMERO PEDIDO"]] or "").strip() if indices["NUMERO PEDIDO"] is not None else ""
                                if num2:
                                    continue  # se já tem número, não herda
                                status_atual2 = (linha2[indices["STATUS"]] or "").strip() if indices["STATUS"] is not None else ""
                                if status_atual2 in config.STATUS_IGNORAR_GSHEETS:
                                    continue
                                
                                # ===== CORREÇÃO: Só atualiza se status realmente mudou =====
                                if status_atual2 != novo_status:
                                    updates_por_sheet[sheet_id].append({
                                        "row_index": idx_linha2,
                                        "values": {"STATUS": novo_status},
                                        "cnpj": cnpj,
                                    })
                                    escrever_log(
                                        f"[PT3][{sheet_id}] HERANÇA RENOV (sempre): CNPJ={cnpj} L{idx_linha2} "
                                        f"recebeu '{novo_status}' por estar em RENOV_COM_OUTRO e sem número"
                                    )
                                else:
                                    escrever_log(
                                        f"[PT3][{sheet_id}] HERANÇA RENOV (sem mudança): CNPJ={cnpj} L{idx_linha2} "
                                        f"já estava em '{status_atual2}'"
                                    )

            # fim do CNPJ
            _avisar_multiplos_novos_sem_numero(sheet_id, linhas_cnpj, indices)
            _limpar_observacao_se_tudo_ativado(sheet_id, linhas_cnpj, indices, updates_por_sheet)

    return updates_por_sheet

# ------------------------------------------------------------------------------
# PT4 - DATAS
# ------------------------------------------------------------------------------
def aplicar_datas_nas_prévias(updates_por_sheet: dict, planilhas_info: dict):
    hoje_str = agora_local().strftime("%d/%m/%Y")
    ultima_key = "ÚLTIMA ALTERAÇÃO"  # sem variações

    for sheet_id, updates in updates_por_sheet.items():
        info = planilhas_info[sheet_id]
        indices = info["indices"]
        linhas = info["linhas"]

        for upd in updates:
            row_idx = upd["row_index"]
            vals = upd["values"]
            status_novo = vals.get("STATUS")

            # Guardamos se já havia alterações (STATUS/COTAÇÃO/NUMERO PEDIDO etc.)
            tinha_alteracao = bool(vals)

            # 1) Datas dirigidas por STATUS (apenas se mapeado e célula vazia)
            datas_adicionadas = False
            if status_novo and status_novo in config.LISTA_STATUS_DATAS:
                for col_data_nome in config.LISTA_STATUS_DATAS[status_novo]:
                    if col_data_nome in indices and indices[col_data_nome] is not None:
                        col_idx = indices[col_data_nome]
                        # snapshot da linha lida no PT2
                        linha_atual = linhas[row_idx - 2] if 0 <= (row_idx - 2) < len(linhas) else []
                        valor_atual_data = (linha_atual[col_idx] or "").strip() if col_idx < len(linha_atual) else ""
                        if not valor_atual_data:
                            vals[col_data_nome] = hoje_str
                            datas_adicionadas = True

            # 2) 'ÚLTIMA ALTERAÇÃO' só se houve alteração nesta linha
            if (tinha_alteracao or datas_adicionadas) and (ultima_key in indices and indices[ultima_key] is not None):
                vals[ultima_key] = hoje_str

# ----------------------------------------------------------------------
# PT5 - ESCRITA NO G SHEETS (merge + values_batch_update + limite 100)
# ----------------------------------------------------------------------
def escrever_planilhas(updates_por_sheet: dict, planilhas_info: dict):
    def col_idx_to_a1(col_idx_zero_based: int) -> str:
        """0 -> A, 1 -> B, 25 -> Z, 26 -> AA ..."""
        col_idx = col_idx_zero_based + 1  # vira 1-based
        letras = ""
        while col_idx > 0:
            col_idx, resto = divmod(col_idx - 1, 26)
            letras = chr(65 + resto) + letras
        return letras

    for sheet_id, updates in updates_por_sheet.items():
        if not updates:
            continue

        info = planilhas_info[sheet_id]
        ws = info["worksheet"]
        indices = info["indices"]
        sheet_title = ws.title
        sh = ws.spreadsheet  # vamos usar values_batch_update nele

        # 1) merge por linha
        # row_index -> {col_nome: valor, ...}
        rows_map = {}
        for upd in updates:
            row_idx = upd["row_index"]
            vals = upd["values"]
            if row_idx not in rows_map:
                rows_map[row_idx] = {}
            rows_map[row_idx].update(vals)

        # 2) transforma em lista de "data" (range + values)
        data_payload = []
        for row_idx, cols_dict in rows_map.items():
            for col_nome, valor in cols_dict.items():
                if col_nome not in indices or indices[col_nome] is None:
                    continue
                col_a1 = col_idx_to_a1(indices[col_nome])
                a1_range = f"{sheet_title}!{col_a1}{row_idx}"
                data_payload.append({
                    "range": a1_range,
                    "values": [[valor]],
                })

        if not data_payload:
            continue

        escrever_log(f"[PT5] Atualizando planilha {sheet_id} -> {len(data_payload)} celulas...")

        # 3) manda em blocos de até 100 células
        for i in range(0, len(data_payload), 100):
            chunk = data_payload[i:i + 100]
            body = {
                "value_input_option": "USER_ENTERED",
                "data": chunk
            }
            try:
                sh.values_batch_update(body)
            except Exception as e:
                escrever_log(
                    f"[PT5] Erro ao atualizar {sheet_id} (chunk {i // 100 + 1}) -> {e}"
                )
            # se ainda tem mais chunk na mesma planilha, uma pausa curtinha
            if i + 100 < len(data_payload):
                time.sleep(5)

        # 4) pausa entre planilhas
        time.sleep(10)


def notificar_inicio_ciclo(dt: datetime):
    msg = f"🤖 ROBÔ iniciou ciclo em {dt.strftime('%d/%m/%Y %H:%M:%S')}. Favor não editar planilhas até a conclusão."
    enviar_telegram_bko(msg)


def notificar_fim_ciclo(dt: datetime, proximo: datetime):
    msg = (
        f"✅ ROBÔ concluiu ciclo em {dt.strftime('%d/%m/%Y %H:%M:%S')}.\n"
        f"Próxima execução prevista: {proximo.strftime('%d/%m/%Y %H:%M:%S')}."
    )
    enviar_telegram_bko(msg)


# ------------------------------------------------------------------------------
# CICLO PRINCIPAL
# ------------------------------------------------------------------------------

def rodar_ciclo():
    """
    Executa um ciclo completo de atualização.
    
    MUDANÇA IMPORTANTE: A notificação de início só é enviada APÓS
    a API responder com sucesso, evitando falsa expectativa.
    """
    agora = agora_local()
    janela_minutos = 1020 if eh_janela_24h(agora) else 90

    try:
        # PT1 - TENTA BUSCAR DA API (com retry automático)
        escrever_log("[CICLO] Iniciando busca na API...")
        eventos = api_buscar_eventos(janela_minutos)
        
        # ✅ SÓ NOTIFICA INÍCIO DEPOIS QUE A API RESPONDEU
        notificar_inicio_ciclo(agora)

        # PT2
        planilhas_info, consultor_para_sheet = carregar_planilhas_por_consultor(eventos)

        # PT1.b
        detectar_e_notificar_emergenciais(eventos, planilhas_info, consultor_para_sheet)

        # PT3
        updates_por_sheet = montar_updates(eventos, planilhas_info, consultor_para_sheet)

        # PT4
        aplicar_datas_nas_prévias(updates_por_sheet, planilhas_info)

        # PT5
        escrever_planilhas(updates_por_sheet, planilhas_info)

    except RuntimeError as e:
        # Erro específico da API (após esgotar tentativas)
        msg_erro = (
            f"ROBO NAO CONSEGUIU EXECUTAR O CICLO\n"
            f"Horario: {agora.strftime('%d/%m/%Y %H:%M:%S')}\n"
            f"Motivo: {str(e)}\n\n"
            f"O robo tentara novamente no proximo horario agendado."
        )
        escrever_log(f"[CICLO-ERRO] {msg_erro}")
        
        # Notifica TODOS os responsáveis sobre a falha
        enviar_telegram_bko(f"⚠️ {msg_erro}")  # Emoji só no Telegram
        
        # Também manda pro chat de erro
        try:
            config.enviar_telegram_erro(f"⚠️ {msg_erro}")
        except Exception as te:
            escrever_log(f"[TELEGRAM-ERRO] Falhou ao enviar erro: {te}")
        
        time.sleep(config.ERROR_SLEEP)
        return
        
    except Exception as e:
        # Outros erros não esperados
        msg_erro = (
            f"ERRO INESPERADO NO ROBO\n"
            f"Horario: {agora.strftime('%d/%m/%Y %H:%M:%S')}\n"
            f"Erro: {str(e)}\n\n"
            f"Verificar logs para mais detalhes."
        )
        escrever_log(f"[CICLO-ERRO] {msg_erro}")
        escrever_log(traceback.format_exc())
        
        # Notifica apenas o chat de erro (não precisa alarmar todos)
        try:
            config.enviar_telegram_erro(f"❌ {msg_erro}")  # Emoji só no Telegram
        except Exception as te:
            escrever_log(f"[TELEGRAM-ERRO] Falhou ao enviar erro: {te}")
        
        time.sleep(config.ERROR_SLEEP)
        return

    escrever_log("[CICLO] OK - Finalizado com sucesso.")

def main():
    escrever_log("=== z.py iniciado ===")
    while True:
        agora = agora_local()
        rodar_ciclo()
        proximo = proximo_horario_execucao(agora_local())
        notificar_fim_ciclo(agora_local(), proximo)
        dormir = (proximo - agora_local()).total_seconds()
        if dormir > 0:
            time.sleep(dormir)


if __name__ == "__main__":
    main()
