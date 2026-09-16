################################
# Bot de Atualização de Status #
################################
import requests
import gspread
import unicodedata
import time
import re
from datetime import datetime, time as dt_time, timedelta
from googleapiclient.discovery import build
from google.oauth2 import service_account
import json, tempfile
import os
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

import hashlib
import uuid

# === CONFIGURAÇÕES NEO / TELEGRAM / PLANILHAS ===
API_PRODUCAO_URL = os.getenv("API_PRODUCAO_URL", "https://datavoxx.neosales.com.br/producao-painel-integration-v2")
API_PEDIDOS_URL = os.getenv("API_PEDIDOS_URL", "https://datavoxx.neosales.com.br/pedido-movimentacao-integration")

TOKEN_ESTRUTURA = os.getenv("TOKEN_ESTRUTURA", "")
TOKEN_USUARIO = os.getenv("TOKEN_USUARIO", "")
PAINEL_ID = os.getenv("PAINEL_ID", "14885")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
TELEGRAM_ASSISTENTE = [s.strip() for s in os.getenv("TELEGRAM_ASSISTENTE", os.getenv("TELEGRAM_CHAT_ID", "")).split(",") if s.strip()]

SERVICE_ACCOUNT_FILE = os.getenv("SERVICE_ACCOUNT_FILE", os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json"))
SCOPES_SHEETS = ["https://www.googleapis.com/auth/spreadsheets"]
_SHEETS_CREDS = service_account.Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES_SHEETS)
sheets_service = build("sheets", "v4", credentials=_SHEETS_CREDS)

# === MAPAS / CONFIGS DE CONSULTOR ===
consultores_planilhas = {
    "GUILHERME HENRIQUE": "1UNX-rPLcvfix2D5ivS4T6aHZwsXGu9LHqsvVlGgWvw8",   # Fallback, 'reserva'

    # "ALINE ARAUJO": "1lqJb2f2zQZKw-QeR2VHHn5xZfsVJVG0LpeEohcH7BDQ",
    # "ADERLANE MENDES": "1mT5lyCfmSmoVMgldO4g1apxCUZUkUqVN_zb_3q2W3Ss",
    # "LETICIA OHARA": "1qjcSCEZTUDF2UUlAQyzcqmPM3LN8POnPOPgLfiWXlB0",
    # "NATHALIA DADALTE": "1KaGQGcgVwm9-1QkUVKaYLkXbdaz3Omp1J449uzJwh5k",
    # "JULIA PENAFORE": "1fsQ5gnQsjyC9pQnv8LIsMUMMOhWFT5-_i0Bz3aavDc8",
    # "GABRIEL BARROS": "1RcaLGDoZXnX--biOA5zCqDjXYzTBKEWRf-nxe-099wA",
    # "MEL SILVA": "11snG2IXYYIrNf-s_iOhImlYrV4Ra9hxC6To67Aom38A",
    # "OTÁVIO SOUZA": "1p7ju-JyA_z3BdVV12WLzSt-vE7HGOEmnJwtrs4fsOOI",
    # "OTAVIO SOUZA": "1p7ju-JyA_z3BdVV12WLzSt-vE7HGOEmnJwtrs4fsOOI",
    # "THIAGO ROSSINI": "1huv98_foTTWs0pDt5qiPm4JD8MsaVkoo_K7PnfS7C6w",
    # "JULIA VICTORIA DIAS CRISPIN": "1Nsa83nNrkYheWOnXx8EtH3z6UU7yYgpue0Bk028uucQ",
    # "BRUNA LOPES": "1-jFBNJ567YkrzatuOjhjwhjIU9DAGORHwRVqFOnv_TY",
    # "VERONICA MACEDO": "1qEfjqpH2MbCif8JgyBNK3nm5bZ6efkuWSimKMkOUtgk",
    # "CAROLINA SOUZA": "1ZfShgR66DEWDnXeg8yzvYmOHIf3mBQvgGC-WkEMRCmA",
    # "RIB_PEDRO TREVISANI": "1aXrJWbZ7txEzb8M9O49EI5qNnWToiyXdiY3g8Sz1MPM",
    # "GABRIELLE BORGES": "1sD8sqTIAzZkYz_HgTKQ7fKLFrY7pJBBv5JNlmePwb3A",

    # "GABRIEL BRITTO": "1OeH9XlghRW3huQ6qn0s6C4rHLA_e-Fs21JFlh3dNtWk"
}

# Mapeamento Grupo → Chat ID do Assistente Administrativo
RESPONSAVEIS = {
    "M": os.getenv("RESPONSAVEIS_M", ""),  # Mariana
    "P": os.getenv("RESPONSAVEIS_P", ""),  # Pedro
    "R": os.getenv("RESPONSAVEIS_R", ""),  # Raissa
    "-": os.getenv("RESPONSAVEIS_FALLBACK", os.getenv("TELEGRAM_CHAT_ID", "")),  # fallback
}

# cache de sheets por planilha_id para reduzir reauth em um mesmo ciclo
_SHEET_CACHE = {}

ULTIMA_ALTERACAO_COLUMN = "ULTIMA ALTERACAO"

# Caminho padrão do arquivo de eventos
NEO_EVENT_STORE_PATH = "neo_eventos.json"

ESTADO_PATH = "estado_dia.json"

# === UTILIDADES ===
def _gerar_chave_evento(evento: dict, fonte: str) -> str:
    """
    Gera uma chave simples para deduplicação do evento.
    Usa: numeroPedido | numeroAtividade | cpf_norm | status_raw | data_evento_iso | fonte
    """
    numero_pedido = str(evento.get("numeroPedido") or evento.get("numero_pedido") or "")
    numero_ativ = str(evento.get("numeroAtividade") or evento.get("numeroAtividade") or "")
    cpf_raw = evento.get("cpfCnpj") or evento.get("cpf_cnpj") or evento.get("cpf") or ""
    cpf_norm = normalizar_cnpj_cpf(cpf_raw)
    # status cru exatamente como veio (string)
    status_raw = (evento.get("etapaNome") or evento.get("nomeEtapa") or evento.get("etapa") or "").strip()
    # data evento em iso (se parse possível)
    dt = parse_possible_datetime(evento)
    data_iso = dt.isoformat() if dt else (evento.get("data") or evento.get("dataHora") or "")
    parts = [numero_pedido, numero_ativ, cpf_norm, status_raw, str(data_iso), fonte or ""]
    key = "|".join(parts)
    return key

def carregar_estado_dia():
    """Carrega o estado persistido do dia (se existir)."""
    if not os.path.exists(ESTADO_PATH):
        return {}  # silencioso se não existir
    try:
        with open(ESTADO_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[WARN] Falha ao ler {ESTADO_PATH}: {e}")
        return {}

def salvar_estado_dia(estado: dict):
    """Salva o estado atual no disco (modo atômico)."""
    tmp = f"{ESTADO_PATH}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(estado, f, ensure_ascii=False, indent=2)
        os.replace(tmp, ESTADO_PATH)
    except Exception as e:
        print(f"[WARN] Falha ao salvar estado em {ESTADO_PATH}: {e}")

def _carregar_store(caminho: str):
    """
    Lê o arquivo JSON e retorna (lista_de_eventos, conjunto_chaves, mapa_nums_por_cpf).
    Tolerante a corrupções parciais e garante compatibilidade com versões antigas do formato.
    """
    if not os.path.exists(caminho):
        return [], set(), {}

    try:
        with open(caminho, "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError as e:
                print(f"[WARN] JSON corrompido detectado ({e}). Tentando recuperação parcial...")
                # tenta ler manualmente até o ponto válido
                f.seek(0)
                texto = f.read()
                # tenta decodificar truncando gradualmente
                for corte in range(len(texto), 0, -200):
                    try:
                        data = json.loads(texto[:corte])
                        print(f"[INFO] Recuperação parcial bem-sucedida ({len(data)} eventos).")
                        break
                    except Exception:
                        continue
                else:
                    print(f"[ERROR] Falha ao recuperar JSON corrompido. Iniciando vazio.")
                    return [], set(), {}

        if not isinstance(data, list):
            print(f"[WARN] Arquivo {caminho} existe mas não contém lista JSON. Ignorando conteúdo.")
            return [], set(), {}

        chave_set = set()
        nums_por_cpf = {}

        for ev in data:
            try:
                k = ev.get("_dedup_key") or _gerar_chave_evento(ev.get("raw", ev), ev.get("fonte", ""))
            except Exception:
                k = None
            if k:
                chave_set.add(k)

            cpf = ev.get("cpfCnpj") or ev.get("cpf_cnpj") or ev.get("cpf") or ""
            cpf_norm = normalizar_cnpj_cpf(cpf)
            if cpf_norm:
                nums = nums_por_cpf.setdefault(cpf_norm, set())
                nums.add((str(ev.get("numeroPedido") or ""), str(ev.get("numeroAtividade") or "")))

        return data, chave_set, nums_por_cpf

    except Exception as e:
        print(f"[WARN] Falha ao ler {caminho}: {e}. Iniciando store vazio.")
        return [], set(), {}

def _salvar_store(lista_eventos: list, caminho: str):
    """
    Salva a lista no arquivo JSON de forma atômica e segura.
    Usa escrita temporária e substituição garantida.
    Tolerante a caracteres Unicode inválidos.
    """
    try:
        dirpath = os.path.dirname(caminho) or "."
        tmp_fd, tmp_path = tempfile.mkstemp(prefix="store_", suffix=".tmp", dir=dirpath)
        os.close(tmp_fd)

        # Escreve em UTF-8 e ignora símbolos que não possam ser codificados
        with open(tmp_path, "w", encoding="utf-8", errors="ignore") as f:
            json.dump(lista_eventos, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())  # força gravação em disco

        os.replace(tmp_path, caminho)
        print(f"[INFO] Store salvo com sucesso ({len(lista_eventos)} eventos).")

    except Exception as e:
        print(f"[ERROR] Falha ao salvar store em {caminho}: {e}")
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except:
                pass

def registrar_eventos_json(eventos: list, fonte: str, caminho: str = None):
    """
    Registra eventos (brutos) no arquivo JSON global de auditoria, evitando duplicatas
    com base em 5 campos:
        cpfCnpj, numeroPedido, numeroAtividade, nomeEtapa, dataHoraAtualizacao
    Mantém o histórico completo sem sobrescrições.
    """
    caminho = caminho or NEO_EVENT_STORE_PATH
    if not eventos:
        return 0

    try:
        store_list, existing_keys, existing_nums_por_cpf = _carregar_store(caminho)
    except Exception as e:
        print(f"[WARN] Falha ao carregar store existente: {e}")
        store_list, existing_keys, existing_nums_por_cpf = [], set(), {}

    adicionados = 0
    nums_por_cpf_lote = {}

    for ev in eventos:
        try:
            if not isinstance(ev, dict):
                continue

            # -------------------------
            # Campos base para deduplicação
            cpf_raw = ev.get("cpfCnpj") or ev.get("cpf_cnpj") or ev.get("cpf") or ""
            cpf_norm = normalizar_cnpj_cpf(cpf_raw)
            numero_pedido = str(ev.get("numeroPedido") or ev.get("numero_pedido") or "")
            numero_ativ = str(ev.get("numeroAtividade") or ev.get("numero_atividade") or "")
            nome_etapa = (ev.get("nomeEtapa") or ev.get("etapaNome") or ev.get("etapa") or "").strip()
            data_atualizacao = (
                ev.get("dataHoraAtualizacao")
                or ev.get("dataHora")
                or ev.get("data")
                or ev.get("dataHoraRegistro")
                or ""
            )

            # Chave composta para deduplicação
            chave_dedup = f"{cpf_norm}|{numero_pedido}|{numero_ativ}|{nome_etapa}|{data_atualizacao}"

            if chave_dedup in existing_keys:
                print(f"[DEBUG] Evento ignorado (duplicado): {chave_dedup}")
                continue
            existing_keys.add(chave_dedup)

            # -------------------------
            # Checagem de inconsistências de numPedido/Atividade
            if cpf_norm:
                existente = existing_nums_por_cpf.get(cpf_norm)
                if existente and (numero_pedido, numero_ativ) not in existente:
                    print(f"[AVISO] CNPJ {cpf_norm} já tem numeros diferentes no histórico. Novos: (pedido={numero_pedido}, ativ={numero_ativ}).")
                lote_set = nums_por_cpf_lote.setdefault(cpf_norm, set())
                if lote_set and (numero_pedido, numero_ativ) not in lote_set:
                    print(f"[AVISO] No ciclo atual, CNPJ {cpf_norm} apareceu com numeros distintos: já vimos {lote_set} e agora (pedido={numero_pedido}, ativ={numero_ativ}).")
                lote_set.add((numero_pedido, numero_ativ))
                existing_nums_por_cpf.setdefault(cpf_norm, set()).add((numero_pedido, numero_ativ))

            # -------------------------
            # Extração da data e normalização
            dt = parse_possible_datetime(ev)
            data_evento_iso = (
                dt.isoformat()
                if dt
                else (data_atualizacao or "")
            )

            # -------------------------
            # Registro completo
            registro = {
                "id": uuid.uuid4().hex,
                "timestamp_coleta": datetime.now().isoformat(),
                "fonte": fonte,
                "numeroPedido": numero_pedido,
                "numeroAtividade": numero_ativ,
                "cpfCnpj": cpf_raw,
                "cpf_norm": cpf_norm,
                "nomeEquipe": ev.get("nomeEquipe") or ev.get("equipe") or "",
                "consultor_raw": ev.get("nomeUsuario") or ev.get("consultor") or "",
                "status_raw": nome_etapa,
                "status_normalizado": normalizar_status(nome_etapa),
                "data_evento_iso": data_evento_iso,
                "raw": ev,  # evento cru para auditoria
                "_dedup_key": chave_dedup
            }

            store_list.append(registro)
            adicionados += 1

        except Exception as e:
            print(f"[WARN] Falha ao processar evento para registro: {e}. Evento: {ev}")

    # -------------------------
    # Salvar se houve novos registros
    if adicionados > 0:
        try:
            _salvar_store(store_list, caminho)
            print(f"[INFO] Registrados {adicionados} novo(s) evento(s) em {caminho}. Total no arquivo: {len(store_list)}")
        except Exception as e:
            print(f"[WARN] Falha ao salvar store após adicionar eventos: {e}")
    else:
        print("[INFO] Nenhum novo evento registrado (todos duplicados).")

    return adicionados

def normalize_for_compare(texto: str) -> str:
    """Normaliza texto para comparação (remove acentos, espaços extras e coloca em maiúsculas)."""
    if not texto:
        return ""
    texto = str(texto).strip().upper()
    texto = unicodedata.normalize("NFD", texto)
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    return texto

def enviar_telegram(mensagem):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": mensagem}
    try:
        response = requests.post(url, data=payload)
        if response.status_code == 200:
            print("[TELEGRAM] Mensagem enviada")
        else:
            print(f"[TELEGRAM] Falha ao enviar mensagem: {response.text}")
    except Exception as e:
        print(f"[TELEGRAM] Erro: {e}")

def enviar_telegram_assistente(mensagem: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    for chat_id in TELEGRAM_ASSISTENTE:
        payload = {"chat_id": chat_id, "text": mensagem}
        try:
            response = requests.post(url, data=payload)
            if response.status_code == 200:
                print(f"[TELEGRAM] Mensagem enviada para {chat_id}")
            else:
                print(f"[TELEGRAM] Falha ao enviar para {chat_id}: {response.text}")
        except Exception as e:
            print(f"[TELEGRAM] Erro ao enviar para {chat_id}: {e}")


def normalizar_cnpj_cpf(valor: str) -> str:
    if not valor:
        return ""
    return re.sub(r'\D', '', str(valor))

def get_bko_por_consultor(consultor):
    consultor = (consultor or "").strip().upper()
    grupo_m = ["OTAVIO SOUZA", "NATHALIA DADALTE", "GABRIEL BRITTO", "JULIA VICTORIA"]  # Mariana
    grupo_p = ["LETICIA OHARA", "ADERLANE MENDES", "GABRIEL BARROS"]  # Pedro
    grupo_r = ["ALINE ARAUJO", "MEL SILVA", "VIVIAN MARTINS"]  # Raissa
    grupo_t = ["OMNI ASSESSORIA"]  # T.I.

    if consultor in grupo_m:
        return "M"
    elif consultor in grupo_p:
        return "P"
    elif consultor in grupo_r:
        return "R"
    elif consultor in grupo_t:
        return "T"
    else:
        return "-"  # Valor padrão caso não caia em nenhum grupo

def enviar_telegram_por_consultor(consultor, mensagem):
    # Envia mensagem para o administrador responsável pelo consultor (BKO).
    grupo = get_bko_por_consultor(consultor)
    if grupo == "-":
        print(f"[AVISO] Consultor '{consultor}' não pertence a nenhum grupo configurado.")
        grupo = "T"

    chat_id = RESPONSAVEIS.get(grupo)
    if not chat_id:
        print(f"[ERRO] Grupo '{grupo}' não possui Chat ID configurado.")
        grupo = "T"
        chat_id = RESPONSAVEIS.get("T")

    if not chat_id:
        print("[ERRO] Grupo 'T' também não possui Chat ID Configurado. Mensagem Não Enviada.")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": mensagem,
        "parse_mode": "HTML"
    }
    try:
        requests.post(url, data=payload, timeout=5)
        print(f"[TELEGRAM-BKO] Mensagem enviada ao grupo {grupo} (consultor: {consultor})")
    except requests.RequestException as e:
        print(f"[ERRO] Falha ao enviar mensagem para {chat_id}: {e}")

def parse_possible_datetime(item):
    """
    Tenta extrair um timestamp de um dicionário (vários nomes possíveis).
    Retorna objeto datetime ou None.
    """
    possible_keys = ["dataHora", "dataHoraCarga", "dataHoraRegistro", "dataHoraCriacao", "data"]
    for k in possible_keys:
        v = item.get(k)
        if v:
            # já é datetime?
            if isinstance(v, datetime):
                return v
            # tentar vários formatos comuns
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%d/%m/%Y %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%fZ"):
                try:
                    return datetime.strptime(v, fmt)
                except Exception:
                    continue
            # se não conseguiu parse, ignoramos essa key
    return None

def escolher_mais_recente(lista):
    """Retorna o item mais recente segundo parse_possible_datetime; se empatar, retorna o último da lista."""
    if not lista:
        return None
    melhor = None
    melhor_dt = None
    for item in lista:
        dt = parse_possible_datetime(item)
        if dt:
            if (melhor_dt is None) or (dt > melhor_dt):
                melhor = item
                melhor_dt = dt
        else:
            # sem timestamp, consideramos a ordem: o último encontrado sobrescreve
            melhor = item
    return melhor if melhor is not None else lista[-1]

def normalizar_status(status: str) -> str:
    if not status:
        return ""
    # remove prefixo antes do "-" (primeiro traço)
    partes = status.split("-", 1)
    if len(partes) > 1:
        status = partes[1].lstrip()
    else:
        status = status.strip()
    # remove qualquer "(...)" no final, junto com espaços antes
    status = re.sub(r"\s*\([^)]*\)", "", status).strip()
    return status

TIPO_PEDIDO_IGNORAR_LIST = []       # Funcional, tipos para serem ignorados 

STATUS_EQUIVALENCE = {
    normalizar_status("MV - REPROVADO - ATUAR (NEOCRM)"): "REPROVADO CREDITO",
    normalizar_status("MV - PRD - SUPORTE (NEOCRM)"): "--PRD - SUPORTE",


    # normalizar_status("MV - ANÁLISE DE CRÉDITO (CPC) (NEOCRM)"): "--PRD - SUPORTE",

    # normalizar_status("MV - REPROVADO - ATUAR (NEOCRM)"): "REPROVADO CREDITO",
}

STATUS_DATE_LISTS = {
    "FILA INPUT": ["DATA DE ACEITE"],
    "VALIDAÇÃO PENDENTE": ["DATA DE INPUT"],
    "PENDENTE INSTALAÇÃO": ["DATA DE INPUT", "DATA DE ACEITE"],
    "CONECTADO": ["DATA DE ATIVAÇÃO"],
    "CONCLUÍDO INSPEÇAO": ["DATA DE ATIVAÇÃO"]
}

IGNORED_STATUSES = {
    normalizar_status(s) 
    for s in [
        "ABRIR TROCA DE CARTEIRA",
        "MV - ABRIR TROCA DE CARTEIRA",
        "MV - TROCA DE CARTEIRA EM ANÁLISE",
        "TROCA DE CARTEIRA EM ANÁLISE",
        "TROCA DE CARTEIRA EM ANALISE"
    ]
}

IGNORED_STATUSES_NORM = {normalizar_status(s) for s in (IGNORED_STATUSES or [])}

IGNORED_STATUS_GSHEETS_LIST = [
        "AGUARDANDO TEMPO INPUT",
        "TRATATIVA SUPORTE",
        "CANCELADO",
        "ATIVADO 100%",
        "AGUARDANDO CONCLUIR TT"
]

IGNORED_STATUS_GSHEETS_NORM = { normalizar_status(s) for s in IGNORED_STATUS_GSHEETS_LIST if s }
TIPO_PEDIDO_IGNORAR_NORM = { normalize_for_compare(s) for s in TIPO_PEDIDO_IGNORAR_LIST if s }

PLANILHA_TIPOS_CANON = {
    "RENOVACAO": "RENOVAÇÃO",
    "NOVO": "NOVO",
    "PORTAB_CROSS_PJ": "PORTAB CRUZADA PJ/ PJ",
    "TRANSF_TIT_PJ": "TRANSF. TITULAR PJ/PJ",
    "TRANSF_TIT_PF_PRE": "TRANSF. TITULAR PF/PJ PRÉ",
    "TRANSF_TIT_PF_POS": "TRANSF. TITULAR PF/PJ PÓS",
}

# normalizar sempre para comparação: maiúsculas, sem acento, sem espaços duplicados
EQUIV_NUMEROLINHA_TO_TIPO = {
    PLANILHA_TIPOS_CANON["RENOVACAO"]: [
        "VOZ - Renovação", "VOZ - RENOVAÇÃO", "VOZ - RENOVACAO"  # você adiciona/ajusta aqui
    ],
    PLANILHA_TIPOS_CANON["NOVO"]: [
        "VOZ - Novo", "VOZ - NOVO"
    ],
    PLANILHA_TIPOS_CANON["PORTAB_CROSS_PJ"]: [
        "VOZ - Portabilidade",  # placeholders
    ],
    PLANILHA_TIPOS_CANON["TRANSF_TIT_PJ"]: [
        "VOZ - Tranf. Titularidade" 
    ],
    PLANILHA_TIPOS_CANON["TRANSF_TIT_PF_PRE"]: [
        # "VOZ - TRANSF. TITULAR PF/PJ PRÉ"  # placeholders
    ],
    PLANILHA_TIPOS_CANON["TRANSF_TIT_PF_POS"]: [
        # "VOZ - TRANSF. TITULAR PF/PJ PÓS"  # placeholders
    ],
}

EQUIV_SOLICITACAO_TO_TIPO = {
    PLANILHA_TIPOS_CANON["RENOVACAO"]: ["RENOVACAO", "Renovacao", "RENOVAÇÃO", "Renovação", "REN", "Ren"],
    PLANILHA_TIPOS_CANON["NOVO"]: ["NOVO", "Novo"],
    PLANILHA_TIPOS_CANON["PORTAB_CROSS_PJ"]: ["PORTABILIDADE PJ/PJ", "PORTAB CRUZADA PJ/PJ"],
    PLANILHA_TIPOS_CANON["TRANSF_TIT_PJ"]: ["TRANSFERENCIA PJ/PJ", "TRANSF TITULAR PJ/PJ"],
    PLANILHA_TIPOS_CANON["TRANSF_TIT_PF_PRE"]: ["TRANSF PF/PJ PRE", "TRANSF PF PRE"],
    PLANILHA_TIPOS_CANON["TRANSF_TIT_PF_POS"]: ["TRANSF PF/PJ POS", "TRANSF PF POS"],
}

# === Mapeamento sigla -> lista de produtos (normaliza apenas para comparação; mantem texto com acentos na planilha) ===
EQUIVALENCIA = {
    "MV": ["MÓVEL", "PASSAPORTE", "CHIP DE DADOS", "CLARO MONITOR", "PACOTE ADICIONAL"],
    "FB": ["FIXA", "BANDA LARGA", "TV"],
    "AVA": ["AVANÇADOS", "GOTO"]
}

def _norm(s: str) -> str:
    return _normalize_header_key(s or "")  # já remove acento/espacos dup/upper

def _match_in_list(needle: str, haystack: list) -> bool:
    n = _norm(needle)
    for h in haystack:
        if n == _norm(h):
            return True
    return False

def _normalize_header_key(s: str) -> str:
    """Normaliza um nome de coluna / chave: aplica NFKC, remove acentos, controla invisíveis e põe em UPPER."""
    if not s:
        return ""
    nk = unicodedata.normalize("NFKC", str(s))
    # remover caracteres de controle/formatacao
    nk = "".join(ch for ch in nk if unicodedata.category(ch)[0] != "C")
    # separar diacríticos (NFKD) e eliminar combining marks para 'desacentuar'
    nk = unicodedata.normalize("NFKD", nk)
    nk = "".join(ch for ch in nk if not unicodedata.combining(ch))
    return " ".join(nk.upper().split())

def extrair_prefixo(status_cru: str) -> str:
    if not status_cru:
        return ""
    part = str(status_cru).split("-", 1)[0].strip()
    return normalize_for_compare(part)

def status_eh_ignorado_por_raw(status_cru: str, ignored_set: set = None) -> bool:
    if not status_cru:
        return False
    normalized = normalizar_status(status_cru)
    base = ignored_set or IGNORED_STATUSES_NORM
    return normalized in base

def escolher_status_valido(eventos, cpf_cnpj: str = None, ignored_set: set = None):
    if not eventos:
        return None, None

    # normalizar ignored_set
    if not ignored_set:
        ignored_norm = set()
    else:
        ignored_norm = {normalizar_status(s) for s in ignored_set}

    cpf_filter = normalizar_cnpj_cpf(cpf_cnpj) if cpf_cnpj else None

    # varre do mais recente para o mais antigo
    for evento in reversed(eventos):
        # se precisar filtrar por cpf/cnpj do evento, faz aqui
        if cpf_filter:
            cpf_evento = evento.get("cpfCnpj") or evento.get("cpf_cnpj") or evento.get("cpf") or ""
            if normalizar_cnpj_cpf(cpf_evento) != cpf_filter:
                continue

        etapa_raw = (evento.get("etapaNome") or evento.get("nomeEtapa") or evento.get("etapa") or "").strip()
        etapa = normalizar_status(etapa_raw)
        if not etapa:
            continue

        # se não estiver na lista de ignorados, devolve
        if etapa not in ignored_norm:
            return etapa, evento

        # caso esteja em ignored_norm, continua retrocedendo
    return None, None

def _format_sheet_columns_as_date(spreadsheet_id: str, sheet_id: int, col_indices: list, service_account_file: str = None):
    """
    Recebe col_indices 1-based (ex: 30 = AD). Aplica numberFormat DATE dd/MM/yyyy nas colunas.
    """
    if not col_indices:
        return

    requests = []
    for col in sorted(set(col_indices)):
        start_col = int(col) - 1
        end_col = int(col)  # end exclusive
        requests.append({
            "repeatCell": {
                "range": {
                    "sheetId": int(sheet_id),
                    "startColumnIndex": start_col,
                    "endColumnIndex": end_col
                },
                "cell": {
                    "userEnteredFormat": {
                        "numberFormat": {
                            "type": "DATE",
                            "pattern": "dd/MM/yyyy"
                        }
                    }
                },
                "fields": "userEnteredFormat.numberFormat"
            }
        })

    if not requests:
        return

    body = {"requests": requests}
    try:
        print(f"[DEBUG] Chamando batchUpdate numberFormat para {spreadsheet_id} sheetId={sheet_id} cols(1-based)={sorted(set(col_indices))}")
        sheets_service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body=body).execute()
        print("[DEBUG] numberFormat aplicado (dd/MM/yyyy).")
    except Exception as e:
        print(f"[WARN] Falha ao aplicar numberFormat via API: {e}")

def force_date_parsing(spreadsheet_id: str, col_indices: list, start_row: int = 3, max_rows_scan: int = 2000):
    if not col_indices:
        return

    # pega primeira aba
    ss = sheets_service.spreadsheets().get(spreadsheetId=spreadsheet_id, fields="sheets.properties").execute()
    first_sheet = ss.get("sheets", [{}])[0]
    sheet_name = first_sheet.get("properties", {}).get("title")
    sheet_id = first_sheet.get("properties", {}).get("sheetId")

    if not sheet_name:
        print(f"[WARN] Não consegui detectar primeira aba para {spreadsheet_id}. Pulando force_date_parsing.")
        return

    for col in sorted(set(col_indices)):
        col_letter = col_num_to_letter(col)
        # pega até max_rows_scan nessa coluna
        range_check = f"'{sheet_name}'!{col_letter}1:{col_letter}{max_rows_scan}"
        try:
            resp = sheets_service.spreadsheets().values().get(spreadsheetId=spreadsheet_id, range=range_check).execute()
            values = resp.get("values", [])
        except Exception as e:
            print(f"[WARN] Falha ao ler coluna {col_letter} de {spreadsheet_id}: {e}")
            continue

        # detectar última linha não-vazia (considera strings com conteúdo)
        last_row = start_row - 1
        for idx, row in enumerate(values, start=1):
            cell = row[0] if row else ""
            if isinstance(cell, str):
                if cell.strip() != "":
                    last_row = idx
            else:
                # se for número/datetime já interpretado
                last_row = idx

        if last_row < start_row:
            print(f"[DEBUG] Coluna {col_letter} sem dados úteis após linha {start_row-1} — pulando.")
            continue

        range_str = f"'{sheet_name}'!{col_letter}{start_row}:{col_letter}{last_row}"
        print(f"[DEBUG] Force parse range: {range_str}")

        # pega os valores existentes para reescrever (preenchendo vazios)
        try:
            resp2 = sheets_service.spreadsheets().values().get(spreadsheetId=spreadsheet_id, range=range_str).execute()
            values2 = resp2.get("values", [])
        except Exception as e:
            print(f"[WARN] Falha ao obter valores para {range_str}: {e}")
            continue

        # garantir tamanho correto
        expected_len = last_row - start_row + 1
        while len(values2) < expected_len:
            values2.append([""])

        # limpar leading apostrophe e espaços
        new_vals = []
        for row in values2:
            if row and isinstance(row[0], str):
                val_clean = row[0].lstrip("'").strip()
                new_vals.append([val_clean])
            elif row:
                new_vals.append([row[0]])
            else:
                new_vals.append([""])

        # reescrever com USER_ENTERED para forçar interpretação
        body = {"values": new_vals}
        try:
            sheets_service.spreadsheets().values().update(
                spreadsheetId=spreadsheet_id,
                range=range_str,
                valueInputOption="USER_ENTERED",
                body=body
            ).execute()
            print(f"[DEBUG] Reescrito {range_str} com USER_ENTERED (len={len(new_vals)}).")
        except Exception as e:
            print(f"[WARN] Falha ao reescrever valores para {range_str}: {e}")
            continue

    # Retorna sheet_id caso precise
    return sheet_id

# conversões coluna <-> número/letter
def col_num_to_letter(n:int) -> str:
    result = ''
    while n > 0:
        n, rem = divmod(n-1, 26)
        result = chr(65 + rem) + result
    return result

def letter_to_colnum(letter: str) -> int:
    num = 0
    for c in letter.upper():
        num = num * 26 + (ord(c) - ord('A') + 1)
    return num

# === GSpread helpers ===

def obter_sheet_por_consultor(consultor_limpo):
    """
    Retorna sheet (gspread worksheet) para o consultor. Faz cache por planilha_id.
    """
    planilha_id = consultores_planilhas.get(consultor_limpo)
    if not planilha_id:
        print(f"[WARN] Consultor '{consultor_limpo}' sem planilha mapeada. Usando planilha reserva.")
        planilha_id = list(consultores_planilhas.values())[0]  # fallback para a primeira conhecida

    if planilha_id in _SHEET_CACHE:
        return _SHEET_CACHE[planilha_id]

    client = gspread.service_account(filename='service_account.json')
    sh = client.open_by_key(planilha_id)
    time.sleep(1)  # evitar throttling
    sheet = sh.sheet1
    _SHEET_CACHE[planilha_id] = sheet
    return sheet

# === Funções de consulta NEO ===

def consultar_status_neo_producao(inicio, fim):
    payload = {
        "tokenEstrutura": TOKEN_ESTRUTURA,
        "tokenUsuario": TOKEN_USUARIO,
        "dataHoraInicioCarga": inicio.strftime("%Y-%m-%d %H:%M:%S"),
        "dataHoraFimCarga": fim.strftime("%Y-%m-%d %H:%M:%S"),
        "painelId": PAINEL_ID,
        "outputFormat": "json"
    }
    headers = {"Content-Type": "application/json"}
    try:
        response = requests.post(API_PRODUCAO_URL, json=payload, headers=headers, timeout=60)
        response.raise_for_status()
        dados = response.json()
        if isinstance(dados, dict) and not dados.get("success", True):
            print(f"[PRODUÇÃO] Erro: {dados.get('erro')}")
            return []
        print(f"[PRODUÇÃO] Registros recebidos: {len(dados)}")
        return dados
    except Exception as e:
        print(f"[PRODUÇÃO] Falha ao consultar API: {e}")
        return []

def consultar_status_neo_pedidos(numeros_pedidos, inicio, fim):
    payload = {
        "token": TOKEN_ESTRUTURA,
        "numeroPedido": numeros_pedidos,
        "dataHoraInicioCarga": inicio.strftime("%Y-%m-%d %H:%M:%S"),
        "dataHoraFimCarga": fim.strftime("%Y-%m-%d %H:%M:%S"),
        "painelId": PAINEL_ID,
        "outputFormat": "json"
    }
    headers = {"Content-Type": "application/json"}
    try:
        response = requests.post(API_PEDIDOS_URL, json=payload, headers=headers, timeout=60)
        response.raise_for_status()
        dados = response.json()
        if isinstance(dados, dict) and not dados.get("success", True):
            print(f"[PEDIDOS] Erro: {dados.get('erro')}")
            return []
        print(f"[PEDIDOS] Registros recebidos: {len(dados)}")
        return dados
    except Exception as e:
        print(f"[PEDIDOS] Falha ao consultar API: {e}")
        return []

# --- Parâmetros de sleep dinâmico ---
ERROR_SLEEP = 300         # 5 minutos em caso de erro
DAY_SUCCESS_SLEEP = 1800   # 30 minutos entre 07:00 e 21:00 (taxa mais rápida)
NIGHT_SUCCESS_SLEEP = 7200 # 2 horas entre 22:01 e 05:59 (volume maior)
        # --- Helpers de horário / dia útil ---
BUSINESS_START = dt_time(7, 0, 0)
BUSINESS_END = dt_time(21, 0, 0)

def is_weekday(d: datetime) -> bool:
    # segunda=0 ... domingo=6
    return d.weekday() < 5

def is_business_time(now: datetime) -> bool:
    return is_weekday(now) and (BUSINESS_START <= now.time() < BUSINESS_END)

def next_business_start(now: datetime) -> datetime:
    # se já passou do BUSINESS_END -> começar a contar a partir de amanhã
    candidate_date = now.date()
    if now.time() > BUSINESS_END:
        candidate_date = candidate_date + timedelta(days=1)
    elif now.time() < BUSINESS_START:
        candidate_date = candidate_date  # hoje às 06:00 (provavelmente já passou se hora < 6)
    else:
        # estamos dentro do horário comercial: próximo business start é amanhã às 06:00
        candidate_date = candidate_date + timedelta(days=1)

    # avançar até encontrar um dia útil
    while True:
        if datetime.combine(candidate_date, dt_time(0, 0)).weekday() < 5:
            break
        candidate_date = candidate_date + timedelta(days=1)

    return datetime.combine(candidate_date, BUSINESS_START)

def sleep_until_next_business_start(now: datetime = None):
    now = now or datetime.now()
    wake = next_business_start(now)
    sleep_seconds = (wake - now).total_seconds()
    if sleep_seconds < 0:
        sleep_seconds = 0
    print(f"[INFO] Fora do horário comercial / fim de semana. Dormindo até {wake} (~{int(sleep_seconds)}s)...")
    time.sleep(sleep_seconds)

# def sleep_until_next_business_start(now: datetime = None):
#     now = now or datetime.now()
#     sleep_seconds = 20 * 60  # 20 minutos
#     wake = now + timedelta(seconds=sleep_seconds)
#     print(f"[INFO] Modo teste: dormindo por 20 minutos, até {wake.strftime('%H:%M:%S')}...")
#     time.sleep(sleep_seconds)

def sleep_during_business_or_until_next(day_sleep_seconds: int):
    now = datetime.now()
    if is_business_time(now):
        print(f"[INFO] Dentro do horário comercial. Aguardando {day_sleep_seconds} segundos antes do próximo ciclo...")
        time.sleep(day_sleep_seconds)
    else:
        sleep_until_next_business_start(now)

def error_sleep_respecting_business_window(error_sleep_seconds: int):
    now = datetime.now()

    # se já não estivermos em dia útil/horário, vamos direto para o próximo início
    if not is_business_time(now):
        sleep_until_next_business_start(now)
        return

    # se ao somar o ERROR_SLEEP vamos cair fora do horário comercial ou entrar no fim de semana,
    # então dormimos até o próximo BUSINESS_START
    after = now + timedelta(seconds=error_sleep_seconds)
    after_time = after.time()
    after_weekday = after.weekday()

    # condição: após o sono cair fora do horário (hora maior que BUSINESS_END)
    falls_out_of_business_hours = (after_time > BUSINESS_END) or (after_weekday >= 5)
    if falls_out_of_business_hours:
        print(f"[INFO] ERROR_SLEEP de {error_sleep_seconds}s atravessaria o fim do expediente/fim de semana. Acordo será no próximo dia útil às 07:00.")
        sleep_until_next_business_start(now)
    else:
        print(f"[INFO] Dormindo {error_sleep_seconds} segundos (erro). Voltarei dentro do horário comercial.")
        time.sleep(error_sleep_seconds)

def preparar_batch_update(sheet, updates: list):
    requests = []
    for row_idx, col_idx, valor in updates:
        a1 = gspread.utils.rowcol_to_a1(row_idx, col_idx)
        requests.append({
            "range": a1,
            "values": [[valor]]
        })
    if requests:
        sheet.batch_update(requests)

# --- Novo: build_sheet_info (centraliza leitura + índices) ---
def build_sheet_info(sheet):
    todas_linhas = sheet.get_all_values() or []
    header_raw = todas_linhas[0] if todas_linhas else []
    header_norm = [_normalize_header_key(col) for col in header_raw]
    time.sleep(1)  # evitar throttling

    # localizar colunas básicas
    idx_cnpj = None
    try:
        idx_cnpj = header_raw.index("CNPJ/CPF") + 1
    except ValueError:
        try:
            idx_cnpj = header_norm.index(_normalize_header_key("CNPJ/CPF")) + 1
        except ValueError:
            idx_cnpj = None

    idx_status = None
    try:
        idx_status = max(i+1 for i, col in enumerate([h.upper() for h in header_raw]) if col == "STATUS")
    except Exception:
        try:
            idx_status = max(i+1 for i, col in enumerate(header_norm) if col == _normalize_header_key("STATUS"))
        except Exception:
            idx_status = None

    ultima_norm = _normalize_header_key(ULTIMA_ALTERACAO_COLUMN)
    idx_ultima = header_norm.index(ultima_norm) + 1 if ultima_norm in header_norm else None

    # heurística para localizar variantes de colunas
    def _find_idx_variants_local(header_norm_list, variants):
        for v in variants:
            v_norm = _normalize_header_key(v)
            if v_norm in header_norm_list:
                return max(i for i, h in enumerate(header_norm_list) if h == v_norm) + 1
        # heurística por tokens
        for v in variants:
            parts = [p for p in _normalize_header_key(v).split() if p]
            if not parts:
                continue
            for i, h in enumerate(header_norm_list):
                if all(p in h for p in parts):
                    return i + 1
        return None

    # índices já existentes
    idx_produtos = _find_idx_variants_local(header_norm, ["PRODUTOS", "PRODUTO", "PRODUTO(S)"])
    idx_tipo_pedido = _find_idx_variants_local(header_norm, ["TIPO DE PEDIDO", "TIPO_PEDIDO", "TIPO PEDIDO", "TIPO", "TIPO DE SOLICITACAO", "TIPO DE SOLICITAÇÃO"])

    # novas colunas solicitadas
    idx_observacao = _find_idx_variants_local(header_norm, ["OBSERVAÇÃO", "OBSERVACAO", "OBS"])
    idx_data_portabilidade = _find_idx_variants_local(header_norm, ["DATA DE PORTABILIDADE", "DATA_PORTABILIDADE", "DATA PORTABILIDADE"])
    idx_status_portabilidade = _find_idx_variants_local(header_norm, ["STATUS PORTABILIDADE", "STATUS_PORTABILIDADE", "STATUS DE PORTABILIDADE"])
    idx_chip = _find_idx_variants_local(header_norm, ["CHIP, E-SIM E/OU APARELHOS (MODELOS E QUANTIDADES)", "CHIP", "APARELHOS", "E-SIM"])
    idx_cotacao = _find_idx_variants_local(header_norm, ["COTAÇÃO", "COTACAO", "COTAÇÃO (COTAÇÃO)", "COTACAO (COTAÇÃO)"])
    idx_numero_pedido = _find_idx_variants_local(header_norm, ["NUMERO PEDIDO", "NUMERO_PEDIDO", "N PEDIDO", "NUMERO DO PEDIDO"])

    # localizar colunas de data para cada status a partir de STATUS_DATE_LISTS (agora lista por status -> múltiplas col)
    date_cols_idx = {}  # status_norm_header -> [col_idx, ...]
    for status_raw, col_names in (STATUS_DATE_LISTS or {}).items():
        status_norm = _normalize_header_key(normalizar_status(status_raw))
        cols_idx_list = []
        for col_name in col_names:
            col_norm = _normalize_header_key(col_name)
            if col_norm in header_norm:
                idx_col = max(i for i, h in enumerate(header_norm) if h == col_norm) + 1
                cols_idx_list.append(idx_col)
        if cols_idx_list:
            date_cols_idx[status_norm] = cols_idx_list

    # cpf -> lista de linhas (pois pode haver múltiplas entradas para mesmo cpf)
    cpf_row_map = {}
    if idx_cnpj and todas_linhas:
        for row_idx, linha in enumerate(todas_linhas[1:], start=2):
            try:
                val = normalizar_cnpj_cpf(linha[idx_cnpj - 1])
            except Exception:
                val = ""
            if val:
                cpf_row_map.setdefault(val, []).append(row_idx)

    # tentar extrair planilha_id / sheet_id (com fallback)
    planilha_id = None
    sheet_id = None
    try:
        planilha_id = sheet.spreadsheet.id
    except Exception:
        planilha_id = None
    try:
        sheet_id = getattr(sheet, "id", None) or (sheet._properties.get("sheetId") if hasattr(sheet, "_properties") else None)
    except Exception:
        sheet_id = None

    return {
        "sheet": sheet,
        "todas_linhas": todas_linhas,
        "header_raw": header_raw,
        "header_norm": header_norm,
        "idx_cnpj": idx_cnpj,
        "idx_status": idx_status,
        "idx_ultima": idx_ultima,
        "date_cols_idx": date_cols_idx,  # agora status_norm -> [col_idx,...]
        "idx_produtos": idx_produtos,
        "idx_tipo_pedido": idx_tipo_pedido,
        "idx_observacao": idx_observacao,
        "idx_data_portabilidade": idx_data_portabilidade,
        "idx_status_portabilidade": idx_status_portabilidade,
        "idx_chip": idx_chip,
        "idx_cotacao": idx_cotacao,
        "idx_numero_pedido": idx_numero_pedido,
        "cpf_row_map": cpf_row_map,
        "planilha_id": planilha_id,
        "sheet_id": sheet_id
    }

# --- Refactor: preparar_updates_por_cnpj (usa info pronto) ---
def preparar_updates_por_cnpj(
    info: dict,
    eventos: list,
    equivalencia: dict,
    ignored_set: set = None,   # mantido p/ compatibilidade, mas usaremos IGNORED_STATUSES explicitamente
    estado: dict = None        # mantido p/ compatibilidade; não usado aqui
    ):

    updates = []
    updated_rows = []
    modified_date_columns = set()

    if not info or not eventos:
        return updates, updated_rows, modified_date_columns

    # --- indices/caches da planilha ---
    todas_linhas = info.get("todas_linhas", [])
    header_norm = info.get("header_norm", [])
    idx_cnpj = info.get("idx_cnpj")
    idx_status = info.get("idx_status")
    idx_ultima = info.get("idx_ultima")
    date_cols_idx = info.get("date_cols_idx", {})  # status_norm -> [col_idx,...]
    idx_produtos = info.get("idx_produtos")
    idx_tipo_pedido = info.get("idx_tipo_pedido")
    idx_observacao = info.get("idx_observacao")
    idx_data_portabilidade = info.get("idx_data_portabilidade")
    idx_status_portabilidade = info.get("idx_status_portabilidade")
    idx_chip = info.get("idx_chip")
    idx_cotacao = info.get("idx_cotacao")
    idx_numero_pedido = info.get("idx_numero_pedido")

    if not idx_cnpj or not idx_status:
        return updates, updated_rows, modified_date_columns

    # --- equivalência de status (NEO cru -> texto planilha) ---
    raw_status_equiv = globals().get("STATUS_EQUIVALENCE", {}) or {}
    status_equiv_map = {}
    for k, v in raw_status_equiv.items():
        try:
            k_norm = normalize_for_compare(k)
            v_norm = normalizar_status(v)
            status_equiv_map[k_norm] = v_norm
        except Exception:
            continue

    def map_status_for_logic(etapa_raw: str) -> str:
        s_norm = normalizar_status(etapa_raw)

        # Se o status cru bate direto com a equivalência, devolve o mapeado sem nova normalização
        for k, v in STATUS_EQUIVALENCE.items():
            if normalize_for_compare(etapa_raw) == normalize_for_compare(k):
                return v  # devolve exatamente como no mapa, sem cortar no traço

        # caso normal (fora da equivalência explícita)
        mapped = status_equiv_map.get(normalize_for_compare(s_norm), s_norm)
        return mapped

    # --- helpers locais de normalização / equivalência ---
    def _norm(s: str) -> str:
        return _normalize_header_key(s or "")

    def _match_in_list(needle: str, haystack: list) -> bool:
        n = _norm(needle)
        for h in (haystack or []):
            if n == _norm(h):
                return True
        return False

    # Tenta resolver o TIPO DE PEDIDO da planilha para um evento.
    # PRIORIDADE: numeroLinha -> (se falhar) solicitacao
    def resolver_tipo_planilha_para_evento(ev: dict) -> str | None:
        numero_linha = ev.get("numeroLinha") or ""
        solicitacao  = ev.get("solicitacao") or ""

        # Caso especial: todas as transferências têm o mesmo numeroLinha
        if _norm(numero_linha) == _norm("VOZ - Tranf. Titularidade"):
            # usar solicitacao como determinante
            for tipo_planilha, termos in (globals().get("EQUIV_SOLICITACAO_TO_TIPO") or {}).items():
                if _match_in_list(solicitacao, termos):
                    return tipo_planilha
            # se não bater, logar e ignorar
            print(f"[MAPEAMENTO] Transferência com numeroLinha genérico, solicitacao='{solicitacao}' sem equivalência -> evento ignorado.")
            return None

        # Caminho normal para os demais
        if numero_linha:
            for tipo_planilha, termos in (globals().get("EQUIV_NUMEROLINHA_TO_TIPO") or {}).items():
                if _match_in_list(numero_linha, termos):
                    return tipo_planilha

        if solicitacao:
            for tipo_planilha, termos in (globals().get("EQUIV_SOLICITACAO_TO_TIPO") or {}).items():
                if _match_in_list(solicitacao, termos):
                    return tipo_planilha

        return None


    # Exceção: pode atualizar linha sem numero_pedido?
    # Regra: apenas se a linha for "NOVO" e existir "RENOVAÇÃO" irmã no mesmo CNPJ.
    def can_update_without_numero(row_tipo: str, rows_mesmo_cnpj: list) -> bool:
        if _norm(row_tipo) == _norm(PLANILHA_TIPOS_CANON["NOVO"]):
            for r in rows_mesmo_cnpj:
                if _norm(r.get("tipo_raw") or r.get("tipo_comp") or "") == _norm(PLANILHA_TIPOS_CANON["RENOVACAO"]):
                    return True
        return False

    # --- detectar CPF/CNPJ alvo a partir dos eventos ---
    first_cpf = None
    for ev in eventos:
        cpf_candidate = ev.get("cpfCnpj") or ev.get("cpf_cnpj") or ev.get("cpf")
        if cpf_candidate:
            first_cpf = normalizar_cnpj_cpf(cpf_candidate)
            break
    if not first_cpf:
        return updates, updated_rows, modified_date_columns

    # --- construir linhas do sheet para este cpf ---
    rows = []
    for row_idx, linha in enumerate(todas_linhas[1:], start=2):
        try:
            val = normalizar_cnpj_cpf(linha[idx_cnpj - 1])
        except Exception:
            val = ""
        if not val or val != first_cpf:
            continue

        produto_raw = linha[idx_produtos - 1] if idx_produtos and idx_produtos - 1 < len(linha) else ""
        tipo_raw = linha[idx_tipo_pedido - 1] if idx_tipo_pedido and idx_tipo_pedido - 1 < len(linha) else ""
        produto_comp = normalize_for_compare(produto_raw)
        tipo_comp = _norm(tipo_raw) if tipo_raw else ""
        status_sheet = linha[idx_status - 1] if idx_status - 1 < len(linha) else ""
        chip_raw = linha[idx_chip - 1] if idx_chip and idx_chip - 1 < len(linha) else ""
        data_port_sheet = linha[idx_data_portabilidade - 1] if idx_data_portabilidade and idx_data_portabilidade - 1 < len(linha) else ""
        cotacao_sheet = linha[idx_cotacao - 1] if idx_cotacao and idx_cotacao - 1 < len(linha) else ""
        numero_ped_sheet = ""
        if idx_numero_pedido and idx_numero_pedido - 1 < len(linha):
            numero_ped_sheet = (linha[idx_numero_pedido - 1] or "").strip()

        # bloqueio por status ignorado na planilha
        if status_sheet and normalizar_status(status_sheet) in IGNORED_STATUS_GSHEETS_NORM:
            print(f"[INFO] Linha {row_idx} do CNPJ {first_cpf} tem STATUS '{status_sheet}' listado em IGNORED_STATUS_GSHEETS — ignorando esta linha.")
            continue

        rows.append({
            "row_idx": row_idx,
            "produto_raw": produto_raw,
            "produto_comp": produto_comp,
            "tipo_raw": tipo_raw,
            "tipo_comp": tipo_comp,
            "status_sheet": status_sheet,
            "status_mem": status_sheet,
            "locked_after_inspecao": False,
            "chip_raw": chip_raw,
            "data_port_sheet": data_port_sheet,
            "cotacao_sheet": cotacao_sheet,
            "numero_ped": numero_ped_sheet
        })

    if not rows:
        return updates, updated_rows, modified_date_columns

    # --- filtrar EVENTOS por IGNORED_STATUSES (somente eventos NEO) ---
    eventos_filtrados = []
    for ev in eventos:
        etapa_raw = (ev.get("etapaNome") or ev.get("nomeEtapa") or ev.get("etapa") or "").strip()
        if not etapa_raw:
            continue
        if normalizar_status(etapa_raw) in {normalizar_status(s) for s in (globals().get("IGNORED_STATUSES") or set())}:
            print(f"[INFO] Evento ignorado (NEO) para CNPJ {first_cpf}: '{etapa_raw}' consta em IGNORED_STATUSES.")
            continue
        eventos_filtrados.append(ev)

    # ordenar eventos cronologicamente (mais antigo -> mais recente)
    eventos_ordenados = sorted(eventos_filtrados, key=lambda e: (parse_possible_datetime(e) or datetime.min))

    # --- equivalência produto -> lista de produtos alvo (MV/FB/AVA) ---
    equivalencia_norm = {
        normalize_for_compare(k): [normalize_for_compare(p) for p in v]
        for k, v in (equivalencia or {}).items()
    }

    # --- flags do histórico global para o CNPJ (para regras REN/TRANSF existentes) ---
    ACTIVATE_NORM = normalizar_status("ATIVADO 100%")
    INSPECAO_NORM = normalizar_status("CONCLUÍDO INSPEÇAO")

    mapped_history_statuses = []
    for ev in eventos_ordenados:
        etapa_raw = (ev.get("etapaNome") or ev.get("nomeEtapa") or ev.get("etapa") or "").strip()
        if not etapa_raw:
            continue
        mapped_history_statuses.append(map_status_for_logic(etapa_raw))

    has_any_inspecao = any(s == INSPECAO_NORM for s in mapped_history_statuses)
    has_any_ativado = any(s == ACTIVATE_NORM for s in mapped_history_statuses)

    # --- Identificação de grupos de linhas por TIPO para regras de travamento preservadas ---
    REN_TIPO_N = _norm("RENOVAÇÃO")
    NOVO_TIPO_N = _norm("NOVO")
    PORT_TIPO_N = _norm("PORTAB CRUZADA PF/ PJ")

    # TRANSF: separar PJ/PJ (bloqueador) de PF/PJ PRÉ/PÓS (não bloqueiam)
    TRANSF_PJ_N     = _norm(PLANILHA_TIPOS_CANON["TRANSF_TIT_PJ"])
    TRANSF_PF_PRE_N = _norm(PLANILHA_TIPOS_CANON["TRANSF_TIT_PF_PRE"])
    TRANSF_PF_POS_N = _norm(PLANILHA_TIPOS_CANON["TRANSF_TIT_PF_POS"])

    linhas_ren  = [r for r in rows if r.get("tipo_comp") == REN_TIPO_N]
    linhas_novo = [r for r in rows if r.get("tipo_comp") == NOVO_TIPO_N]
    linhas_port = [r for r in rows if r.get("tipo_comp") == PORT_TIPO_N]

    transf_rows_pj     = [r for r in rows if r.get("tipo_comp") == TRANSF_PJ_N]
    transf_rows_pfpre  = [r for r in rows if r.get("tipo_comp") == TRANSF_PF_PRE_N]
    transf_rows_pfpos  = [r for r in rows if r.get("tipo_comp") == TRANSF_PF_POS_N]

    transf_present_pj    = bool(transf_rows_pj)     # só PJ/PJ é "bloqueador"
    transf_present_pfpre = bool(transf_rows_pfpre)  # não bloqueia
    transf_present_pfpos = bool(transf_rows_pfpos)  # não bloqueia

    # --- TRANSF (PJ/PJ): detectar 'ativado' dentro do histórico ---
    transf_activated_pj = False
    if transf_present_pj:
        for ev in eventos_ordenados:
            etapa_raw = (ev.get("etapaNome") or ev.get("nomeEtapa") or ev.get("etapa") or "").strip()
            if not etapa_raw:
                continue
            status_mapped = map_status_for_logic(etapa_raw)
            if status_mapped != ACTIVATE_NORM:
                continue
            # checar se este evento seria roteado especificamente para TRANSF PJ/PJ
            tipo_ev = resolver_tipo_planilha_para_evento(ev)
            if tipo_ev and _norm(tipo_ev) == TRANSF_PJ_N:
                transf_activated_pj = True
                break


    # Se TRANSF PJ/PJ presente e ainda não ativado, travar demais linhas (exceto as próprias PJ/PJ)
    if transf_present_pj and not transf_activated_pj:
        aguardando = "AGUARDANDO CONCLUIR TT"
        affected = []
        for r in rows:
            if r in transf_rows_pj:  # apenas as linhas PJ/PJ continuam evoluindo
                continue
            prev = r.get("status_mem", "")
            r["status_mem"] = aguardando
            if prev != aguardando:
                affected.append(r["row_idx"])
        if affected:
            print(f"[AUDITORIA] CNPJ {first_cpf}: TRANSF PJ/PJ presente e não ativado. Forçando '{aguardando}' nas linhas {affected} até TRANSF PJ/PJ atingir 'ATIVADO 100%'.")


    # --- RENOVAÇÃO travada em CONCLUÍDO INSPEÇAO se houve inspeção e não houve ativação e não tem chip ---
    for linha in linhas_ren:
        chip_present = bool(str(linha.get("chip_raw") or "").strip())
        if has_any_inspecao and (not has_any_ativado) and (not chip_present):
            linha["status_mem"] = "CONCLUÍDO INSPEÇAO"
            linha["locked_after_inspecao"] = True
            print(f"[AUDITORIA] CNPJ {first_cpf} linha {linha['row_idx']}: RENOVAÇÃO travada em 'CONCLUÍDO INSPEÇÃO' (sem chip e sem ativação).")

    # --- Núcleo novo: aplicar eventos com prioridade (numeroPedido -> numeroLinha -> solicitacao) ---
    for ev in eventos_ordenados:
        etapa_cru = (ev.get("etapaNome") or ev.get("nomeEtapa") or ev.get("etapa") or "").strip()
        if not etapa_cru:
            continue
        status_mapped = map_status_for_logic(etapa_cru)
        numero_id_api = str(ev.get("numeroPedido") or ev.get("numeroAtividade") or "").strip()

        # --- NOVO FILTRO OBRIGATÓRIO: garantir que o evento pertence à família de produto correta (MV/FB/AVA) ---
        prefixo_raw = extrair_prefixo(etapa_cru)
        prefixo_norm = normalize_for_compare(prefixo_raw)

        if prefixo_norm in equivalencia_norm:
            produtos_alvo = set(equivalencia_norm[prefixo_norm])
            # Se o evento pertence a uma família específica, só prossegue se o produto da linha estiver nessa família
            linhas = [r for r in rows if r.get("produto_comp") in produtos_alvo]
            if not linhas:
                # Nenhuma linha compatível com o prefixo (MV/FB/AVA)
                print(f"[MAPEAMENTO] CNPJ {first_cpf}: evento '{etapa_cru}' (prefixo {prefixo_raw}) ignorado — nenhum produto compatível ({produtos_alvo}).")
                continue
        else:
            # Caso o prefixo não exista na equivalência, logar e seguir (não bloqueia, mas alerta)
            print(f"[AVISO] CNPJ {first_cpf}: evento '{etapa_cru}' sem prefixo reconhecido (MV/FB/AVA) — prosseguindo sem filtro de produto.")

        # 1) Vincular por numeroPedido (se houver) — chave operacional primária
        alvo_por_numero = []
        if numero_id_api:
            for linha in rows:
                if (linha.get("numero_ped") or "").strip():
                    if _norm(linha["numero_ped"]) == _norm(numero_id_api):
                        alvo_por_numero.append(linha)

        if alvo_por_numero:
            for linha in alvo_por_numero:
                # Se TRANSF está travando e a linha não é TRANSF, já está travada acima.
                # Aqui apenas consolidamos o status, se não estiver travado.
                if not (transf_present_pj and not transf_activated_pj and linha not in transf_rows_pj):
                    linha["status_mem"] = status_mapped
            continue  # já resolvido por numeroPedido

        # 2) Sem match por numeroPedido: decidir destino por numeroLinha; se falhar, por solicitacao
        tipo_alvo = resolver_tipo_planilha_para_evento(ev)
        if not tipo_alvo:
            # Política B: logar e ignorar
            print(f"[MAPEAMENTO] CNPJ {first_cpf}: numeroLinha='{ev.get('numeroLinha')}' e solicitacao='{ev.get('solicitacao')}' sem equivalência -> evento ignorado.")
            continue

        # Selecionar linhas cujo TIPO DE PEDIDO bate com o tipo_alvo
        linhas_alvo = [r for r in rows if _norm(r.get("tipo_raw") or r.get("tipo_comp") or "") == _norm(tipo_alvo)]
        if not linhas_alvo:
            # Sem linha desse tipo para este CNPJ: nada a fazer
            continue

        for linha in linhas_alvo:
            row_idx = linha["row_idx"]
            numero_ped_sheet = linha.get("numero_ped") or ""

            # 2.1) Se a linha está sem número e o evento traz numeroPedido, preenche “NUMERO PEDIDO” primeiro
            if (not numero_ped_sheet) and numero_id_api:
                if idx_numero_pedido:
                    updates.append((row_idx, idx_numero_pedido, numero_id_api))
                    linha["numero_ped"] = numero_id_api
                    modified_date_columns.add(int(idx_numero_pedido))
                # cai para atualizar o STATUS abaixo

            # 2.2) Se continua sem número (sheet) e evento também não tem numeroPedido:
            if (not linha.get("numero_ped")) and (not numero_id_api):
                if not can_update_without_numero(tipo_alvo, rows):
                    # exceção não satisfeita: não atualiza
                    continue

            # 2.3) Aplicar STATUS (respeitando travas já impostas acima)
            if not (transf_present_pj and not transf_present_pj and linha not in transf_rows_pj):
                linha["status_mem"] = status_mapped

    # --- Compatibilidade adicional: aplicar equivalência MV/FB/AVA quando a linha não recebeu status dos eventos acima ---
    def eventos_para_linha_mapeados(linha):
        produto_comp = linha.get("produto_comp")
        evs = []
        for ev in eventos_ordenados:
            etapa_raw = (ev.get("etapaNome") or ev.get("nomeEtapa") or ev.get("etapa") or "").strip()
            if not etapa_raw:
                continue
            prefixo = extrair_prefixo(etapa_raw)
            if not prefixo:
                continue
            pref_norm = normalize_for_compare(prefixo)
            if pref_norm not in equivalencia_norm:
                continue
            produtos_alvo = set(equivalencia_norm[pref_norm])
            if produto_comp in produtos_alvo:
                evs.append((ev, map_status_for_logic(etapa_raw)))
        return evs

    for linha in rows:
        # Se TRANSF presente e não ativado, outras linhas já ficaram fixas; a(s) TRANSF pode(m) evoluir
        if transf_present_pj and not transf_activated_pj and linha not in transf_rows_pj:
            continue

        # Se a linha não mudou o status_mem (segue igual ao da planilha), tentar MV/FB/AVA
        if linha.get("status_mem") == linha.get("status_sheet"):
            evs_linha = eventos_para_linha_mapeados(linha)
            if evs_linha:
                candidate = linha.get("status_mem", "")
                for ev, status_mapped in evs_linha:
                    candidate = status_mapped
                linha["status_mem"] = candidate

    # --- Log quando TRANSF PJ/PJ finalmente ativa (após aplicação do núcleo) ---
    if transf_present_pj:
        transf_now_activated_pj = any(normalizar_status(r.get("status_mem", "")) == ACTIVATE_NORM for r in transf_rows_pj)
        if transf_now_activated_pj and (not transf_activated_pj):
            print(f"[AUDITORIA] CNPJ {first_cpf}: TRANSF PJ/PJ atingiu 'ATIVADO 100%'. Liberando demais linhas para fluxo normal.")

    # --- Se todas as linhas do CNPJ atingiram 'ATIVADO 100%', limpar OBSERVAÇÃO ---
    all_ativados = all(
        normalizar_status(r.get("status_mem", "")) == ACTIVATE_NORM
        for r in rows
    )
    if all_ativados and idx_observacao:
        for r in rows:
            updates.append((r["row_idx"], idx_observacao, ""))
        print(f"[AUDITORIA] CNPJ {first_cpf}: todas as linhas atingiram 'ATIVADO 100%'. Limpando coluna 'OBSERVAÇÃO'.")

    # --- Preparar updates: escreve STATUS + datas mapeadas + COTAÇÃO ---
    for linha in rows:
        row_idx = linha["row_idx"]
        novo_status_cru = linha.get("status_mem", "") or ""
        status_para_escrever = normalizar_status(novo_status_cru) or ""
        valor_atual = linha.get("status_sheet") or ""

        if str(valor_atual).strip() != str(status_para_escrever).strip():
            updates.append((row_idx, idx_status, status_para_escrever))
            cols_changed = [("STATUS", idx_status)]

            # Última alteração
            if idx_ultima:
                data_hoje = datetime.now().strftime("%d/%m/%Y")
                updates.append((row_idx, idx_ultima, data_hoje))
                cols_changed.append((ULTIMA_ALTERACAO_COLUMN, idx_ultima))
                modified_date_columns.add(int(idx_ultima))

            # datas por STATUS (múltiplas colunas possíveis)
            status_key_norm = _normalize_header_key(status_para_escrever)
            idx_date_cols = date_cols_idx.get(status_key_norm, [])
            if idx_date_cols:
                for idx_date_col in idx_date_cols:
                    try:
                        current_val = todas_linhas[row_idx - 1][idx_date_col - 1]
                    except Exception:
                        current_val = ""
                    if not current_val or str(current_val).strip() == "":
                        # pega a data do evento mais recente que mapeou para esse status
                        data_evento_str = None
                        for ev in reversed(eventos_ordenados):
                            ev_status_raw = (ev.get("etapaNome") or ev.get("nomeEtapa") or ev.get("etapa") or "").strip()
                            if not ev_status_raw:
                                continue
                            ev_status_mapped = map_status_for_logic(ev_status_raw)
                            if ev_status_mapped == status_para_escrever:
                                dt = parse_possible_datetime(ev)
                                if dt:
                                    data_evento_str = dt.strftime("%d/%m/%Y")
                                    break
                        if not data_evento_str:
                            data_evento_str = datetime.now().strftime("%d/%m/%Y")
                        updates.append((row_idx, idx_date_col, data_evento_str))
                        cols_changed.append((f"DATA@{idx_date_col}", idx_date_col))
                        modified_date_columns.add(int(idx_date_col))

            # COTAÇÃO via numeroPedidoVinculado (sem sobrescrever)
            if idx_cotacao:
                try:
                    current_val = todas_linhas[row_idx - 1][idx_cotacao - 1]
                except Exception:
                    current_val = ""
                if not current_val or str(current_val).strip() == "":
                    numero_vinc = None
                    for ev in reversed(eventos_ordenados):
                        npv = ev.get("numeroPedidoVinculado") or ev.get("numero_pedido_vinculado") or ev.get("pedidoVinculado") or ""
                        if npv:
                            npv_str = str(npv).strip()
                            digits = re.sub(r"\D", "", npv_str)
                            if len(digits) > 3 and not all(ch == "0" for ch in digits):
                                numero_vinc = npv_str
                                break
                    if numero_vinc:
                        updates.append((row_idx, idx_cotacao, numero_vinc))
                        cols_changed.append(("COTAÇÃO", idx_cotacao))
                        modified_date_columns.add(int(idx_cotacao))
                        print(f"[AUDITORIA] CNPJ {first_cpf} linha {row_idx}: preenchida 'COTAÇÃO' = '{numero_vinc}'.")

            updated_rows.append((row_idx, cols_changed))

    return updates, updated_rows, modified_date_columns

# --- CONFIGURAÇÕES DE NOTIF (enviados para o Assistente) ---
NTF_ASCT_LIST = [
    "FILA INPUT", "REPROVADO GAR OU BIOMETRIA", "REPROVADO CREDITO", "AGUARDANDO ENTREGA", "PENDENCIA COMERCIAL",
    "PENDENTE INSTALACAO"
    ]       # Lista de STATUS que necessitam de atenção do Assistente de Contratos.
NTF_ASCT_SET = {e.strip().upper() for e in NTF_ASCT_LIST}   

while True:
    print("\n[INFO] Iniciando novo ciclo...")
    try:
        # carregar estado persistido do dia (se você já implementou carregar_estado_dia)
        try:
            estado = carregar_estado_dia()
        except Exception:
            estado = {}
            print("[WARN] Não foi possível carregar estado persistido do dia. Inicializando estado vazio.")

        fim = datetime.now()
        inicio = fim - timedelta(minutes=90) #1440

        # --- Coleta PRODUÇÃO e filtro da equipe
        dados_producao = consultar_status_neo_producao(inicio, fim)
        dados_empresa = [item for item in dados_producao if item.get("nomeEquipe") == "DTX_RIB"]
        print(f"[PRODUÇÃO] Registros DTX_RIB: {len(dados_empresa)}")

        if not dados_empresa:
            print("[INFO] Nenhum registro de PRODUÇÃO para DTX_RIB. Aguardando próximo ciclo.")
            sleep_during_business_or_until_next(DAY_SUCCESS_SLEEP)
            continue

        # --- Persistir eventos tratados (após filtro de equipe) para auditoria
        try:
            registrar_eventos_json(dados_empresa, fonte="PRODUCAO")
        except Exception as e:
            print(f"[WARN] Falha ao registrar eventos de PRODUCAO em JSON: {e}")

        # Helper: normaliza o "número" (numeroPedido ou numeroAtividade) para comparar entre APIs
        def numero_equivalente(item):
            return str(item.get("numeroPedido") or item.get("numeroAtividade") or "").strip()

        # --- Agrupa produção por numero equivalente
        prod_por_numero = {}
        for item in dados_empresa:
            numero = numero_equivalente(item)
            if not numero:
                continue
            prod_por_numero.setdefault(numero, []).append(item)

        numeros_equivalentes = list(prod_por_numero.keys())
        if not numeros_equivalentes:
            print("[AVISO] Nenhum numeroPedido válido encontrado em produção.")
            sleep_during_business_or_until_next(DAY_SUCCESS_SLEEP)
            continue

        # --- Coleta PEDIDOS usando os numeros equivalentes (sem filtrar por nomeEquipe, pois nem sempre existe)
        dados_pedidos_bruto = consultar_status_neo_pedidos(numeros_equivalentes, inicio, fim)
        print(f"[PEDIDOS] Registros recebidos: {len(dados_pedidos_bruto)}")

        # --- Cruzar com Produção (numeroAtividade vs numeroPedido) e anexar
        dados_pedidos = []
        for p in dados_pedidos_bruto:
            numero_ativ = str(p.get("numeroAtividade") or "").strip()
            if numero_ativ in numeros_equivalentes:
                dados_pedidos.append(p)

        print(f"[PEDIDOS] Registros cruzados (correspondentes à produção): {len(dados_pedidos)}")

        # --- Persistir eventos de pedidos também (obs: por enquanto estamos gravando tudo que cruzou)
        try:
            registrar_eventos_json(dados_pedidos, fonte="PEDIDOS")
        except Exception as e:
            print(f"[WARN] Falha ao registrar eventos de PEDIDOS em JSON: {e}")

        # --- Agrupa pedidos por numeroEquivalente (numeroAtividade/numeroPedido)
        pedidos_por_numero = {}
        for p in dados_pedidos:
            numero_ativ = numero_equivalente(p)
            if not numero_ativ:
                continue
            pedidos_por_numero.setdefault(numero_ativ, []).append(p)

        print(f"[INFO] Eventos registrados em arquivo JSON (quando aplicável).")

        # --- Reset estruturas do ciclo
        sheet_info_by_planilha = {}
        sheet_obj_by_planilha = {}
        updates_by_planilha = {}
        updated_rows_by_planilha = {}
        modified_date_columns_by_planilha = {}

        all_numbers = set(prod_por_numero.keys()) | set(pedidos_por_numero.keys())
        print(f"[INFO] Pedidos com eventos a processar: {len(all_numbers)}")

        # --- Agora usamos apenas STATUS_DATE_LISTS (status -> [col_names...])
        #     status_date_map_norm_global terá as chaves normalizadas e os valores como listas de nomes de coluna
        status_date_map_norm_global = {
            _normalize_header_key(normalizar_status(status_raw)): col_list
            for status_raw, col_list in (STATUS_DATE_LISTS or {}).items()
        }

        # --- Processamento por pedido (unindo eventos de produção + pedidos)
        for numero in sorted(all_numbers):
            eventos = []
            eventos.extend(prod_por_numero.get(numero, []))
            eventos.extend(pedidos_por_numero.get(numero, []))
            eventos_ordenados = sorted(eventos, key=lambda e: (parse_possible_datetime(e) or datetime.min))

            vistos = set()
            eventos_bucket = {}  # {(planilha_id, cpf_norm): [evento, ...], ...}

            for evento in eventos_ordenados:
                cpf = evento.get("cpfCnpj") or evento.get("cpf_cnpj") or evento.get("cpf")
                consultor_raw = evento.get("nomeUsuario") or evento.get("consultor") or ""
                etapa_raw = (evento.get("etapaNome") or evento.get("nomeEtapa") or evento.get("etapa") or "").strip()
                dt_evento = parse_possible_datetime(evento)
                data_evento_str = dt_evento.strftime("%d/%m/%Y") if dt_evento else None

                if not (cpf and consultor_raw and etapa_raw):
                    continue

                if "_" in consultor_raw:
                    consultor_limpo = consultor_raw.split("_", 1)[1].strip().upper()
                else:
                    consultor_limpo = consultor_raw.strip().upper()

                cpf_norm = normalizar_cnpj_cpf(cpf)
                chave = (cpf_norm, consultor_limpo, normalizar_status(etapa_raw), data_evento_str)
                if chave in vistos:
                    continue
                vistos.add(chave)

                planilha_id = consultores_planilhas.get(consultor_limpo)
                if not planilha_id:
                    print(f"[WARN] Consultor '{consultor_limpo}' sem planilha mapeada. Usando planilha reserva.")
                    planilha_id = list(consultores_planilhas.values())[0]

                # obter sheet + info (cache)
                if planilha_id not in sheet_info_by_planilha:
                    try:
                        sheet = obter_sheet_por_consultor(consultor_limpo)
                        sheet_obj_by_planilha[planilha_id] = sheet
                        info = build_sheet_info(sheet)
                        sheet_info_by_planilha[planilha_id] = info
                    except Exception as e:
                        print(f"[ERRO] Falha ao abrir/interpretar planilha {planilha_id}: {e}")
                        # colocar placeholder vazio para evitar retries toda vez
                        sheet_info_by_planilha[planilha_id] = {
                            "sheet": None, "todas_linhas": [], "header_norm": [], "idx_cnpj": None,
                            "idx_status": None, "idx_ultima": None, "date_cols_idx": {}, "cpf_row_map": {}
                        }

                eventos_bucket.setdefault((planilha_id, cpf_norm), []).append(evento)

            # processa buckets deste pedido
            for (planilha_id, cpf_norm), eventos_do_cpf in eventos_bucket.items():
                info = sheet_info_by_planilha.get(planilha_id) or {}
                if not info or not info.get("todas_linhas"):
                    continue

                # passar estado (se sua versão de preparar_updates_por_cnpj aceitar)
                try:
                    prepared_updates, prepared_rows, prepared_mod_dates = preparar_updates_por_cnpj(
                        info=info,
                        eventos=eventos_do_cpf,
                        equivalencia=EQUIVALENCIA,
                        ignored_set=IGNORED_STATUSES,
                        estado=estado
                    )
                except TypeError:
                    # fallback caso a função não aceite 'estado' (compatibilidade)
                    prepared_updates, prepared_rows, prepared_mod_dates = preparar_updates_por_cnpj(
                        info=info,
                        eventos=eventos_do_cpf,
                        equivalencia=EQUIVALENCIA,
                        ignored_set=IGNORED_STATUSES
                    )

                # merge updates (último vence)
                updates_by_planilha.setdefault(planilha_id, {})
                cell_map = updates_by_planilha[planilha_id]
                for r, c, v in prepared_updates:
                    cell_map[(r, c)] = v

                # logs/rows
                updated_rows_by_planilha.setdefault(planilha_id, [])
                for r_idx, cols_changed in prepared_rows:
                    # extrair status aplicado se houver
                    status_aplicado = None
                    for (rr, cc, vv) in prepared_updates:
                        if rr == r_idx and cc == info.get("idx_status"):
                            status_aplicado = vv
                            break
                    updated_rows_by_planilha[planilha_id].append((r_idx, cols_changed, status_aplicado))

                # modified date columns
                modified_date_columns_by_planilha.setdefault(planilha_id, set())
                if prepared_mod_dates:
                    modified_date_columns_by_planilha[planilha_id].update(prepared_mod_dates)

        # aplicar updates por planilha (chunked)
        CHUNK_SIZE = 100
        for planilha_id, cell_map in updates_by_planilha.items():
            sheet = sheet_obj_by_planilha.get(planilha_id) or (sheet_info_by_planilha.get(planilha_id, {}).get("sheet"))
            if not sheet:
                print(f"[ERRO] Não foi possível recuperar objeto sheet para planilha {planilha_id}. Pulando.")
                continue

            updates_list = [(row, col, val) for (row, col), val in cell_map.items()]
            if not updates_list:
                continue

            success_all_chunks = True
            for i in range(0, len(updates_list), CHUNK_SIZE):
                chunk = updates_list[i:i+CHUNK_SIZE]
                print(f"[INFO] Enviando batch de {len(chunk)} updates para planilha {planilha_id} (chunk {i//CHUNK_SIZE + 1})...")

                for attempt in range(3):
                    try:
                        preparar_batch_update(sheet, chunk)
                        print(f"[OK] Batch enviado com sucesso (chunk {i//CHUNK_SIZE + 1}) [tentativa {attempt+1}/3].")
                        time.sleep(1.5)
                        break
                    except Exception as e:
                        print(f"[WARN] Falha no batch_update (tentativa {attempt+1}/3) para planilha {planilha_id}: {e}")
                        time.sleep(5)
                        if attempt == 2:
                            success_all_chunks = False
                            print(f"[ERRO] Falha permanente no batch_update (chunk {i//CHUNK_SIZE + 1}) após 3 tentativas.")

            if success_all_chunks:
                rows_info = updated_rows_by_planilha.get(planilha_id, [])
                if rows_info:
                    for row_idx, cols_changed, status_aplicado in rows_info:
                        cols_descr = ", ".join(f"{name}@{col}" for name, col in cols_changed)
                        if status_aplicado:
                            print(f"[OK] STATUS '{status_aplicado}' aplicado na planilha {planilha_id} - linha {row_idx}. Colunas alteradas: {cols_descr}")
                        else:
                            print(f"[OK] Atualização aplicada na planilha {planilha_id} - linha {row_idx}. Colunas alteradas: {cols_descr}")
                        time.sleep(1.5)
                    linhas = [str(r) for r, _, _ in rows_info]
                    status_list = [s for _, _, s in rows_info if s]
                    status_descr = ", ".join(sorted(set(status_list))) if status_list else ""
                    resumo_status = f"; STATUS='{status_descr}'" if status_descr else ""
                    print(f"[RESUMO] Atualizadas {len(rows_info)} linha(s) na planilha (id={planilha_id}): linhas = {', '.join(linhas)}{resumo_status}")
                else:
                    print(f"[INFO] Batch enviado para planilha {planilha_id}, mas não havia mapeamento de linhas para log.")
            else:
                print(f"[ERRO] Alguns chunks falharam para planilha {planilha_id}. Verifique os erros acima.")

            # aplicar parsing/format se necessário
            modified_cols = modified_date_columns_by_planilha.get(planilha_id)
            if modified_cols:
                cols_to_apply = sorted(int(c) for c in modified_cols)
                print(f"[DEBUG] Vai forçar parsing para planilha {planilha_id} cols(1-based)={cols_to_apply}")
                sheet_obj = sheet_obj_by_planilha.get(planilha_id)
                spreadsheet_id = sheet_obj.spreadsheet.id if sheet_obj else planilha_id

                for attempt in range(3):
                    try:
                        time.sleep(5)
                        sheet_first_sheet_id = force_date_parsing(spreadsheet_id, cols_to_apply, start_row=3)
                        if sheet_first_sheet_id:
                            _format_sheet_columns_as_date(spreadsheet_id, sheet_first_sheet_id, cols_to_apply)
                            print(f"[OK] Datas formatadas corretamente (tentativa {attempt+1}/3) para planilha {planilha_id}.")
                        else:
                            sheet_id_fallback = sheet_obj._properties.get("sheetId") if sheet_obj and hasattr(sheet_obj, "_properties") else None
                            if sheet_id_fallback:
                                _format_sheet_columns_as_date(spreadsheet_id, sheet_id_fallback, cols_to_apply)
                                print(f"[OK] Datas formatadas via fallback sheetId (tentativa {attempt+1}/3) para planilha {planilha_id}.")
                        break
                    except Exception as e:
                        print(f"[WARN] Falha ao forçar parsing/formatação (tentativa {attempt+1}/3) para planilha {planilha_id}: {e}")
                        time.sleep(5)
                        if attempt == 2:
                            print(f"[ERRO] Falha permanente ao formatar datas para planilha {planilha_id} após 3 tentativas.")

                    # salvar estado atualizado do dia (persistência entre execuções)
            try:
                salvar_estado_dia(estado)
                print("[OK] Estado diário salvo com sucesso.")
            except Exception as e:
                print(f"[WARN] Falha ao salvar estado ao final do ciclo: {e}")

            print("[INFO] Ciclo finalizado com sucesso.")
            _SHEET_CACHE.clear()
            sleep_during_business_or_until_next(DAY_SUCCESS_SLEEP)
    except Exception as e:
        print(f"[ERROR] Ciclo principal falhou: {e}")
        # tenta salvar estado antes de encerrar/sleep
        try:
            salvar_estado_dia(estado)
        except Exception:
            pass
        # respeitar janela comercial ao dormir após erro
        try:
            error_sleep_respecting_business_window(ERROR_SLEEP)
        except Exception:
            # fallback simples
            time.sleep(ERROR_SLEEP)
        continue