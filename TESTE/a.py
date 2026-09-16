"""
diagnostico_raquel.py
Script para investigar por que a planilha da Raquel não está atualizando
"""
import json
import os
from datetime import datetime, timedelta
import gspread
from google.oauth2.service_account import Credentials
import requests

import config

# ========================================
# CONFIGURAÇÕES
# ========================================
RAQUEL_SHEET_ID = "1VbJqwBqtd9p8lhdoca19Cp6F7VLd3yrS1H9T9p6Fb_o"
CNPJ_ALVO = "09091072000166"  # sem formatação
CONSULTOR_ALVO = "RAQUEL SOUZA"

# ========================================
# FUNÇÕES AUXILIARES
# ========================================
def normalizar_cnpj(cnpj: str) -> str:
    if not cnpj:
        return ""
    return "".join(ch for ch in cnpj if ch.isdigit())

def normalizar_consultor(nome: str) -> str:
    if not nome:
        return ""
    if "_" in nome:
        return nome.split("_", 1)[1].strip().upper()
    return nome.strip().upper()

def normalizar_status_neo(status_bruto: str) -> str:
    if not status_bruto:
        return ""
    
    s = status_bruto.strip()
    
    prefixes = [
        "MV - ", "MV-", "MV ",
        "FB - ", "FB-", "FB ",
    ]
    s_up = s.upper()
    for p in prefixes:
        if s_up.startswith(p.upper()):
            s = s[len(p):].lstrip()
            break
    
    if " (" in s:
        s = s.split(" (", 1)[0]
    
    return s.strip().upper()

def resolver_status_para_planilha(status_bruto: str):
    if not status_bruto:
        return "", "", ""
    
    status_norm = normalizar_status_neo(status_bruto)
    
    if status_bruto in config.STATUS_EQUIVALENTES:
        status_final = config.STATUS_EQUIVALENTES[status_bruto]
        return status_bruto, status_norm, status_final
    
    if status_norm in config.STATUS_EQUIVALENTES:
        status_final = config.STATUS_EQUIVALENTES[status_norm]
        return status_bruto, status_norm, status_final
    
    return status_bruto, status_norm, status_norm

def normalizar_numero_pedido(v: str) -> str:
    if v is None:
        return ""
    return "".join(ch for ch in str(v) if ch.isdigit())

# ========================================
# PASSO 1: BUSCAR DADOS DA API
# ========================================
def buscar_api():
    print("\n" + "="*60)
    print("PASSO 1: BUSCANDO DADOS DA API")
    print("="*60)
    
    agora = datetime.now()
    inicio_24h = agora - timedelta(hours=24)
    
    payload = {
        "tokenEstrutura": config.TOKEN_ESTRUTURA,
        "tokenUsuario": config.TOKEN_USUARIO,
        "dataHoraInicioCarga": inicio_24h.strftime("%Y-%m-%d %H:%M:%S"),
        "dataHoraFimCarga": agora.strftime("%Y-%m-%d %H:%M:%S"),
        "painelId": config.PAINEL_ID,
        "outputFormat": "json",
    }
    
    print(f"Janela: {inicio_24h.strftime('%Y-%m-%d %H:%M:%S')} até {agora.strftime('%Y-%m-%d %H:%M:%S')}")
    
    try:
        resp = requests.post(
            config.API_URL,
            headers={"Content-Type": "application/json"},
            json=payload,
            timeout=120
        )
        
        if resp.status_code != 200:
            print(f"❌ ERRO: API retornou {resp.status_code}")
            print(f"Resposta: {resp.text[:500]}")
            return []
        
        # ===== DIAGNÓSTICO DA RESPOSTA =====
        print(f"\n🔍 DEBUG da resposta:")
        print(f"   Tipo: {type(resp.text)}")
        print(f"   Tamanho: {len(resp.text)} caracteres")
        print(f"   Primeiros 200 chars: {resp.text[:200]}")
        
        try:
            dados = resp.json()
            print(f"   ✅ JSON parseado com sucesso")
            print(f"   Tipo do dados: {type(dados)}")
            
            # Se for string ao invés de lista
            if isinstance(dados, str):
                print(f"   ⚠️  API retornou STRING, tentando parsear novamente...")
                dados = json.loads(dados)
                print(f"   Novo tipo: {type(dados)}")
            
        except Exception as json_error:
            print(f"   ❌ ERRO ao parsear JSON: {json_error}")
            print(f"   Resposta raw: {resp.text[:500]}")
            return []
        
        if not isinstance(dados, list):
            print(f"❌ ERRO: Esperava lista, recebi {type(dados)}")
            print(f"   Conteúdo: {str(dados)[:200]}")
            return []
        # ===== FIM DO DIAGNÓSTICO =====
        
        print(f"✅ API respondeu com {len(dados)} registros totais")
        
        # Filtrar por equipe
        filtrados_equipe = [item for item in dados if item.get("nomeEquipe") == "DTX_RIB"]
        print(f"📊 Após filtro nomeEquipe=DTX_RIB: {len(filtrados_equipe)} registros")
        
        # Filtrar por Raquel
        raquel_eventos = []
        for ev in filtrados_equipe:
            consultor = normalizar_consultor(ev.get("nomeUsuario", ""))
            if consultor == CONSULTOR_ALVO:
                raquel_eventos.append(ev)
        
        print(f"👤 Eventos da Raquel: {len(raquel_eventos)}")
        
        # Filtrar por CNPJ específico
        cnpj_eventos = []
        for ev in raquel_eventos:
            cnpj = normalizar_cnpj(ev.get("cpfCnpj", ""))
            if cnpj == CNPJ_ALVO:
                cnpj_eventos.append(ev)
        
        print(f"🎯 Eventos do CNPJ {CNPJ_ALVO}: {len(cnpj_eventos)}")
        
        if cnpj_eventos:
            print("\n📋 DETALHES DOS EVENTOS ENCONTRADOS:")
            for i, ev in enumerate(cnpj_eventos, 1):
                status_bruto = ev.get("nomeEtapa", "")
                status_bruto_full, status_norm, status_final = resolver_status_para_planilha(status_bruto)
                
                print(f"\n  Evento {i}:")
                print(f"    • Status Bruto API: '{status_bruto}'")
                print(f"    • Status Normalizado: '{status_norm}'")
                print(f"    • Status Final (planilha): '{status_final}'")
                print(f"    • Número Pedido: {ev.get('numeroPedido', '---')}")
                print(f"    • Número Linha: {ev.get('numeroLinha', '---')}")
                print(f"    • Solicitação: {ev.get('solicitacao', '---')}")
                print(f"    • Data/Hora Atualização: {ev.get('dataHoraAtualizacao', '---')}")
                print(f"    • Data Cadastro: {ev.get('dataCadastro', '---')}")
                print(f"    • Telefone: {ev.get('numeroTelefoneItem', '---')}")
                print(f"    • Item ID: {ev.get('itemId', '---')}")
                
                # Verificar se seria ignorado
                ignorado = False
                motivo_ignorado = []
                
                if status_final in config.STATUS_IGNORAR:
                    ignorado = True
                    motivo_ignorado.append(f"STATUS_IGNORAR contém '{status_final}'")
                
                if status_bruto in config.STATUS_IGNORAR:
                    ignorado = True
                    motivo_ignorado.append(f"STATUS_IGNORAR contém '{status_bruto}'")
                
                if status_norm in config.STATUS_IGNORAR:
                    ignorado = True
                    motivo_ignorado.append(f"STATUS_IGNORAR contém '{status_norm}'")
                
                if ignorado:
                    print(f"    ⚠️  SERIA IGNORADO: {', '.join(motivo_ignorado)}")
                else:
                    print(f"    ✅ NÃO seria ignorado")
        else:
            print("\n⚠️  NENHUM evento encontrado para este CNPJ nas últimas 24h")
            print("     Isso pode significar:")
            print("     1. O pedido não foi atualizado no NEO nas últimas 24h")
            print("     2. O CNPJ está diferente no NEO")
            print("     3. O consultor está diferente no NEO")
            
            # Vamos mostrar TODOS os eventos da Raquel para debug
            if raquel_eventos:
                print(f"\n📋 TODOS os {len(raquel_eventos)} eventos da Raquel (para debug):")
                for i, ev in enumerate(raquel_eventos[:10], 1):  # primeiros 10
                    print(f"   {i}. CNPJ: {ev.get('cpfCnpj', '---')} | "
                          f"Pedido: {ev.get('numeroPedido', '---')} | "
                          f"Status: {ev.get('nomeEtapa', '---')}")
                if len(raquel_eventos) > 10:
                    print(f"   ... e mais {len(raquel_eventos) - 10} eventos")
        
        return cnpj_eventos
        
    except Exception as e:
        print(f"❌ ERRO ao consultar API: {e}")
        import traceback
        traceback.print_exc()
        return []

# ========================================
# PASSO 2: VERIFICAR PLANILHA
# ========================================
def verificar_planilha():
    print("\n" + "="*60)
    print("PASSO 2: VERIFICANDO PLANILHA DA RAQUEL")
    print("="*60)
    
    try:
        # Conectar
        creds = Credentials.from_service_account_file(
            config.SERVICE_ACCOUNT_FILE,
            scopes=config.SCOPES_SHEETS
        )
        client = gspread.Client(auth=creds)
        
        print(f"Abrindo planilha: {RAQUEL_SHEET_ID}")
        sh = client.open_by_key(RAQUEL_SHEET_ID)
        ws = sh.sheet1
        
        all_values = ws.get_all_values()
        
        if not all_values:
            print("❌ Planilha vazia!")
            return None
        
        header = all_values[0]
        linhas = all_values[1:]
        
        print(f"✅ Planilha carregada: {len(linhas)} linhas de dados")
        
        # Localizar índices
        indices = {}
        for idx, col in enumerate(header):
            col_norm = (col or "").strip().upper()
            if col_norm in ("CNPJ/CPF", "CNPJ", "CPF"):
                indices["CNPJ/CPF"] = idx
            elif col_norm == "STATUS":
                if "STATUS" not in indices:
                    indices["STATUS"] = []
                indices["STATUS"].append(idx)
            elif col_norm == "TIPO DE PEDIDO":
                indices["TIPO DE PEDIDO"] = idx
            elif col_norm in ("NUMERO PEDIDO", "NÚMERO PEDIDO"):
                indices["NUMERO PEDIDO"] = idx
            elif col_norm == "CONSULTOR":
                indices["CONSULTOR"] = idx
            elif col_norm == "BKO":
                indices["BKO"] = idx
            elif col_norm == "PRODUTOS":
                indices["PRODUTOS"] = idx
            elif col_norm in ("OBSERVAÇÃO", "OBSERVACAO"):
                indices["OBSERVAÇÃO"] = idx
        
        # Pegar segunda coluna STATUS se houver
        if isinstance(indices.get("STATUS"), list):
            if len(indices["STATUS"]) > 1:
                indices["STATUS"] = indices["STATUS"][1]
            else:
                indices["STATUS"] = indices["STATUS"][0] if indices["STATUS"] else None
        
        print(f"\n📊 Colunas identificadas:")
        for nome, idx in indices.items():
            if idx is not None:
                print(f"   {nome}: coluna {idx} ({header[idx] if idx < len(header) else '???'})")
        
        # Procurar linhas do CNPJ
        if indices.get("CNPJ/CPF") is None:
            print("\n❌ Coluna CNPJ/CPF não encontrada!")
            return None
        
        linhas_cnpj = []
        for i, linha in enumerate(linhas, start=2):
            if indices["CNPJ/CPF"] < len(linha):
                cnpj_planilha = normalizar_cnpj(linha[indices["CNPJ/CPF"]])
                if cnpj_planilha == CNPJ_ALVO:
                    linhas_cnpj.append((i, linha))
        
        print(f"\n🎯 Linhas encontradas com CNPJ {CNPJ_ALVO}: {len(linhas_cnpj)}")
        
        if linhas_cnpj:
            print("\n📋 DETALHES DAS LINHAS NA PLANILHA:")
            for row_idx, linha in linhas_cnpj:
                print(f"\n  Linha {row_idx}:")
                
                if indices.get("BKO") is not None and indices["BKO"] < len(linha):
                    print(f"    • BKO: {linha[indices['BKO']]}")
                
                if indices.get("CONSULTOR") is not None and indices["CONSULTOR"] < len(linha):
                    print(f"    • Consultor: {linha[indices['CONSULTOR']]}")
                
                if indices.get("STATUS") is not None and indices["STATUS"] < len(linha):
                    print(f"    • Status Atual: '{linha[indices['STATUS']]}'")
                
                if indices.get("TIPO DE PEDIDO") is not None and indices["TIPO DE PEDIDO"] < len(linha):
                    print(f"    • Tipo de Pedido: {linha[indices['TIPO DE PEDIDO']]}")
                
                if indices.get("NUMERO PEDIDO") is not None and indices["NUMERO PEDIDO"] < len(linha):
                    print(f"    • Número Pedido: {linha[indices['NUMERO PEDIDO']]}")
                
                if indices.get("PRODUTOS") is not None and indices["PRODUTOS"] < len(linha):
                    print(f"    • Produtos: {linha[indices['PRODUTOS']]}")
                
                if indices.get("OBSERVAÇÃO") is not None and indices["OBSERVAÇÃO"] < len(linha):
                    obs = linha[indices['OBSERVAÇÃO']].strip()
                    if obs:
                        print(f"    • Observação: {obs}")
                
                # Verificar se está travada
                if indices.get("STATUS") is not None and indices["STATUS"] < len(linha):
                    status_atual = linha[indices["STATUS"]].strip()
                    if status_atual in config.STATUS_IGNORAR_GSHEETS:
                        print(f"    🔒 LINHA TRAVADA (status em STATUS_IGNORAR_GSHEETS)")
        else:
            print("\n⚠️  Nenhuma linha encontrada com este CNPJ na planilha")
        
        return {
            "header": header,
            "indices": indices,
            "linhas_cnpj": linhas_cnpj
        }
        
    except Exception as e:
        print(f"❌ ERRO ao abrir planilha: {e}")
        import traceback
        traceback.print_exc()
        return None

# ========================================
# PASSO 3: SIMULAR CASAMENTO
# ========================================
def simular_casamento(eventos, info_planilha):
    print("\n" + "="*60)
    print("PASSO 3: SIMULANDO CASAMENTO API ↔ PLANILHA")
    print("="*60)
    
    if not eventos:
        print("⚠️  Sem eventos da API para casar")
        return
    
    if not info_planilha or not info_planilha["linhas_cnpj"]:
        print("⚠️  Sem linhas na planilha para casar")
        return
    
    indices = info_planilha["indices"]
    linhas_cnpj = info_planilha["linhas_cnpj"]
    
    for i, ev in enumerate(eventos, 1):
        print(f"\n🔄 Tentando casar Evento {i}:")
        
        numero_pedido_api = (ev.get("numeroPedido") or "").strip()
        numero_linha_api = (ev.get("numeroLinha") or "").strip().upper()
        solicitacao_api = (ev.get("solicitacao") or "").strip().upper()
        status_bruto = ev.get("nomeEtapa", "")
        _, _, status_final = resolver_status_para_planilha(status_bruto)
        
        print(f"   Critérios de busca:")
        print(f"   • Número Pedido API: {numero_pedido_api or '(vazio)'}")
        print(f"   • Número Linha API: {numero_linha_api or '(vazio)'}")
        print(f"   • Solicitação API: {solicitacao_api or '(vazio)'}")
        
        # Tentar casar por número de pedido
        match_encontrado = False
        
        if numero_pedido_api and indices.get("NUMERO PEDIDO") is not None:
            num_api_norm = normalizar_numero_pedido(numero_pedido_api)
            
            for row_idx, linha in linhas_cnpj:
                if indices["NUMERO PEDIDO"] < len(linha):
                    num_planilha = normalizar_numero_pedido(linha[indices["NUMERO PEDIDO"]] or "")
                    
                    if num_planilha == num_api_norm:
                        match_encontrado = True
                        print(f"\n   ✅ MATCH por número de pedido na linha {row_idx}")
                        
                        # Verificar se atualizaria
                        if indices.get("STATUS") is not None and indices["STATUS"] < len(linha):
                            status_atual = linha[indices["STATUS"]].strip()
                            print(f"      Status Atual Planilha: '{status_atual}'")
                            print(f"      Status Novo API: '{status_final}'")
                            
                            if status_atual == status_final:
                                print(f"      ⚠️  STATUS IGUAL - não atualizaria (correção funcionando)")
                            elif status_atual in config.STATUS_IGNORAR_GSHEETS:
                                print(f"      🔒 LINHA TRAVADA - não atualizaria")
                            else:
                                print(f"      ✅ ATUALIZARIA de '{status_atual}' para '{status_final}'")
                        break
        
        if not match_encontrado:
            if len(linhas_cnpj) == 1:
                row_idx, linha = linhas_cnpj[0]
                print(f"\n   ⚠️  Sem match por número, mas há APENAS 1 LINHA (L{row_idx})")
                print(f"      → Usaria FALLBACK de 1 linha")
                
                if indices.get("STATUS") is not None and indices["STATUS"] < len(linha):
                    status_atual = linha[indices["STATUS"]].strip()
                    print(f"      Status Atual Planilha: '{status_atual}'")
                    print(f"      Status Novo API: '{status_final}'")
                    
                    if status_atual == status_final:
                        print(f"      ⚠️  STATUS IGUAL - não atualizaria (correção funcionando)")
                    elif status_atual in config.STATUS_IGNORAR_GSHEETS:
                        print(f"      🔒 LINHA TRAVADA - não atualizaria")
                    else:
                        print(f"      ✅ ATUALIZARIA de '{status_atual}' para '{status_final}'")
            else:
                print(f"\n   ❌ SEM MATCH e {len(linhas_cnpj)} linhas")
                print(f"      → Enviaria notificação ao BKO pedindo ajuste manual")

# ========================================
# MAIN
# ========================================
def main():
    print("\n" + "="*70)
    print(" DIAGNÓSTICO DA PLANILHA DA RAQUEL ".center(70, "="))
    print("="*70)
    print(f"\nConsultor: {CONSULTOR_ALVO}")
    print(f"CNPJ Alvo: {CNPJ_ALVO}")
    print(f"Planilha ID: {RAQUEL_SHEET_ID}")
    print(f"Data/Hora: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    
    # Passo 1
    eventos = buscar_api()
    
    # Passo 2
    info_planilha = verificar_planilha()
    
    # Passo 3
    if eventos or info_planilha:
        simular_casamento(eventos, info_planilha)
    
    print("\n" + "="*70)
    print(" FIM DO DIAGNÓSTICO ".center(70, "="))
    print("="*70 + "\n")

if __name__ == "__main__":
    main()