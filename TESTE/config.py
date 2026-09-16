# ==============================
# config.py - VERSÃO CORRIGIDA
# ==============================
import os
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

from datetime import time as dt_time

import gspread
from google.oauth2.service_account import Credentials
import requests

# --- HORÁRIOS / SLEEP ---
BUSINESS_START = dt_time(6, 0, 0)
BUSINESS_END = dt_time(22, 0, 0)
NIGHT_RUN_HOUR = 23  # 23:00 puxa 24h
NIGHT_RUN_MINUTE = 0 # XX:00 puxa 24h
ERROR_SLEEP = 300     # 5 minutos

# --- API NEO / PRODUÇÃO ---
API_URL = os.getenv("API_URL", "https://datavoxx.neosales.com.br/producao-painel-integration-v2")
TOKEN_ESTRUTURA = os.getenv("TOKEN_ESTRUTURA", "")
TOKEN_USUARIO = os.getenv("TOKEN_USUARIO", "")
PAINEL_ID = os.getenv("PAINEL_ID", "14885")   # Exemplo: "14885"

# --- TELEGRAM ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")  # Chat ID pra notificações pra mim
TELEGRAM_CHAT_IDS_GERAL = [s.strip() for s in os.getenv("TELEGRAM_CHAT_IDS_GERAL", "").split(",") if s.strip()]

TELEGRAM_ASSISTENTE = [s.strip() for s in os.getenv("TELEGRAM_ASSISTENTE", os.getenv("TELEGRAM_CHAT_ID", "")).split(",") if s.strip()]

# Mapeamento Grupo → Chat ID do Assistente Administrativo
# BKO na planilha retorna uma sigla que mapeia para um chat_id
RESPONSAVEIS = {
    "M": os.getenv("RESPONSAVEIS_M", ""),  # Mariana
    "P": os.getenv("RESPONSAVEIS_P", ""),  # Pedro
    "R": os.getenv("RESPONSAVEIS_R", ""),  # Raissa
    "-": os.getenv("RESPONSAVEIS_FALLBACK", os.getenv("TELEGRAM_CHAT_ID", "")),  # fallback
}

COTACAO_PODE_SOBRESCREVER = False # Se True, a cotação pode ser sobrescrita por
                                  # número de pedido vinculado

    # --- PLANILHAS POR CONSULTOR ---
    # Somente o ID da planilha.
CONSULTOR_PARA_SHEET = {
  #  "GUILHERME HENRIQUE": "1hcEM77mebuhES0JX_UFju8dAQ44159x7a1OujH2bnys",   # Fallback, 'reserva'

    "ALINE ARAUJO": "1qMGvo-dAN4lC3tOjdyp6OxRfMY7A_XBi7QcYnPwsvhs",
    "ADERLANE MENDES": "1rbg1SsaIyhoAEJ79NU-IQw6KHaHG3D-8MLCBXSXonj4",
    "LETICIA OHARA": "1bZC755yNZF_Hkn5og5gO2sbKuu1G4hkvBBTX-o3EELg",
    "NATHALIA DADALTE": "1FATSH41vlX4EoYjwGGVMBihDjAEKrjzDTWfg5oecBWU",
    "JULIA PENAFORE": "1bAc8rTLrKbp_CydDUaGkT3dZ2iVU2MLoaAYFeislJbg",
    "MEL SILVA": "1x1S_8tmF6D-X2vF7ZStMICQbfjshdgwlI6asf05XUTc",
    "OTÁVIO SOUZA": "1iOv35k_-jLflq4jhaf0HEY1SIX66mWUq1YbBfTlmqrI",
    "OTAVIO SOUZA": "1iOv35k_-jLflq4jhaf0HEY1SIX66mWUq1YbBfTlmqrI",
    "BRUNA LOPES": "1T49UvWpngMAYKFbwsr0BcsRE7Y0OOvtaxTiAmDy6yGs",
    "CAROLINA SOUZA": "1FIShVAIxQSVBRmvuJNlS92K23pNuFGurKgMJZmNeZgw",
    "VITOR MOREIRA": "1HlZxKpdkPKaJ0VTQAWf7O0uepWEeo9ZQFzWdJSWMPaE",
    "CAMILA LEMES": "1HV3BX9tYto2ZQ7w6AADZzjJvadd1kTEozsJQphkFgoA",
    "ELIAS CHIERENTIN": "1yiCXKCkMb0UINI1NAgD2NkRuA8yLOZvFUoZRa75P4Zk",
    "NATALIA RIBEIRO": "1XKW7jH8pXV-uKMk0xvangswW9yMynjhOuWxMN79R6Yw",
    "LETICIA FERREIRA VIDAL": "1md9QBCZ1r0zacpVEMmjm94BeSoUOCvWXVAqKp19u97g",
    "JOAO GABRIEL DOS SANTOS CRISOSTOMO": "1DHr9FyCPQjmsAr4cKNK-1U4Vff0Ka2N05z_CxCa6jUw",
    "IZADORA PEREIRA COSTA NAVES": "16McHyCbhghZEjdxLqHqcpsTPa3eKYeEUdlLqKTsA6v8",
    "LISA LEAO": "1viJA4F83jEtbicDbLBkzpa2-vkgMCtPRvdUtV1OYIms",
    "RAQUEL SOUZA": "1VbJqwBqtd9p8lhdoca19Cp6F7VLd3yrS1H9T9p6Fb_o",
    "Maria Eduarda Santos": "1BgpJh2yq5OPeZ_K55g_bYKhCrmBi0jX6KNqThBdWRpk",
    "Lara Martins": "13y00Th_K69u4sdBfDzHYnkizOtFgVVR-z_HS0w3tj1E",

}

# FALLBACK_SHEET_ID = "1hcEM77mebuhES0JX_UFju8dAQ44159x7a1OujH2bnys"

# --- SERVICE ACCOUNT DO GOOGLE ---
# Caminho absoluto para o arquivo JSON de credenciais
# Isso garante que funcione mesmo se rodar de outra pasta no terminal
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SERVICE_ACCOUNT_FILE = os.path.join(BASE_DIR, "service_account.json")

# Escopo recomendado
SCOPES_SHEETS = ["https://www.googleapis.com/auth/spreadsheets"]

STATUS_IGNORAR = {
    "ABRIR TROCA DE CARTEIRA",
    "MV - ABRIR TROCA DE CARTEIRA",
    "MV - TROCA DE CARTEIRA EM ANÁLISE",
    "TROCA DE CARTEIRA EM ANÁLISE",
    "TROCA DE CARTEIRA EM ANALISE",
    "PENDÊNCIA PARCEIROS (NEOCRM)",
    "PENDÊNCIA PARCEIROS"
}

# ==================== CORREÇÃO PROBLEMA 3 ====================
# Adicionado "TROCA NEGADA - CANCELADO"
STATUS_IGNORAR_GSHEETS = {
    "AGUARDANDO TEMPO INPUT",
    "TRATATIVA SUPORTE",
    "CANCELADO",
    "ATIVADO 100%",
    "AGUARDANDO CONCLUIR TT",
    "TROCA NEGADA",
    "TROCA NEGADA - CANCELADO",  # ← NOVO
}

TIPO_PEDIDO_IGNORAR_LIST = {
    # "",
}

# ==================== CORREÇÃO PROBLEMA 1 ====================
# Adicionados mapeamentos para PENDENTE MKT (se vier com prefixo)
STATUS_EQUIVALENTES = {  # "STATUS DO NEO": "STATUS QUE VAI PARA A PLANILHA"
    "MV - REPROVADO - ATUAR (NEOCRM)": "REPROVADO CREDITO",
    "FB - REPROVADO - ATUAR (NEOCRM)": "REPROVADO CREDITO",
    "MV - PRD - SUPORTE (NEOCRM)": "PRD - SUPORTE",
    "MV - PENDENTE MKT (NEOCRM)": "PENDENTE MKT",  # ← NOVO
    "FB - PENDENTE MKT (NEOCRM)": "PENDENTE MKT",  # ← NOVO
}

LISTA_STATUS_DATAS = {
    "FILA INPUT": ["DATA DE ACEITE"],
    "VALIDAÇÃO PENDENTE": ["DATA DE INPUT"],
    "PENDENTE INSTALAÇÃO": ["DATA DE INPUT", "DATA DE ACEITE"],
    "CONECTADO": ["DATA DE ATIVAÇÃO"],
    "CONCLUÍDO INSPEÇÃO": ["DATA DE ATIVAÇÃO"]
}

ULTIMA_ALTERACAO_COLUNA = "ÚLTIMA ALTERAÇÃO"

EQUIV_NUMEROLINHA_PARA_TIPO = {
    "VOZ - Renovação": "RENOVAÇÃO",
    "VOZ - Novo": "NOVO",
    "VOZ - Tranf. Titularidade": "",
    "VOZ - Portabilidade": "",
}


EQUIV_SOLICITACAO_PARA_TIPO = {
    "PORTAB CRUZADA PF/ PJ": "PORTAB CRUZADA PF/ PJ",
    "PORTAB CRUZADA Pré PF/ PJ": "PORTAB CRUZADA PF/ PJ",
    "PORTADO": "PORTADO",
    "TRANSF. TITULAR PJ / PJ": "TRANSF. TITULAR PJ/PJ",
    "TRANSF. TITULAR PF(pré) / PJ": "TRANSF. TITULAR PF/PJ PRÉ",
    "TRANSF. TITULAR PF / PJ": "TRANSF. TITULAR PF/PJ PÓS",
    "RENOVAÇÃO": "RENOVAÇÃO",
    "NOVO": "NOVO",
}

RENOV_COM_OUTRO = {
    "NOVO",
    "PORTADO",
    "TRANSF. TITULAR PF/PJ PRÉ",
    "TRANSF. TITULAR PF/PJ PÓS",
    "PORTAB CRUZADA PF/ PJ"
}

STATUS_EMERGENCIA = {
    "MV - REPROVADO - ATUAR (NEOCRM)",
    "FB - REPROVADO - ATUAR (NEOCRM)",
    "MV - PENDENCIA COMERCIAL (NEOCRM)",
    "FB - PENDENCIA COMERCIAL (NEOCRM)",
    "PENDENCIA COMERCIAL",
    "PENDÊNCIA PARCEIROS (NEOCRM)",
    "PENDÊNCIA PARCEIROS",
    "MV - ABRIR TROCA DE CARTEIRA (NEOCRM)",
}

# ============== DEFS ================
def _telegram_send(chat_id: str, text: str):
    if not TELEGRAM_TOKEN:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception:
        # aqui não vamos levantar erro, deixamos o bot seguir
        pass

def enviar_telegram_bko_geral(msg: str):
    for chat_id in TELEGRAM_CHAT_IDS_GERAL:
        _telegram_send(chat_id, msg)

def enviar_telegram_por_sigla(sigla: str, msg: str):
    chat_id = RESPONSAVEIS.get(sigla or "-", RESPONSAVEIS.get("-", TELEGRAM_CHAT_ID))
    if chat_id:
        _telegram_send(chat_id, msg)

def enviar_telegram_assistente(msg: str):
    for chat_id in TELEGRAM_ASSISTENTE:
        _telegram_send(chat_id, msg)

def enviar_telegram_erro(msg: str):
    """
    Envia apenas para o canal de erros (um-para-um, não broadcast).
    Usa TELEGRAM_CHAT_ID.
    """
    if TELEGRAM_CHAT_ID:
        _telegram_send(TELEGRAM_CHAT_ID, msg)

def get_gspread_client():
    import json as _json
    # Prod/Docker: permite injetar o JSON via env sem arquivo
    _sa_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    if _sa_json:
        info = _json.loads(_sa_json)
        creds = Credentials.from_service_account_info(info, scopes=SCOPES_SHEETS)
        return gspread.Client(auth=creds)
    # Local: usa arquivo (que está no .gitignore)
    creds = Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE, scopes=SCOPES_SHEETS
    )
    return gspread.Client(auth=creds)
