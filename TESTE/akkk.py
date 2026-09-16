import gspread
from google.oauth2.service_account import Credentials

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

CRED_PATH = "sua_credencial.json"  # caminho pro novo JSON
SHEET_ID = "1FIShVAIxQSVBRmvuJNlS92K23pNuFGurKgMJZmNeZgw"

def testar():
    try:
        creds = Credentials.from_service_account_file(CRED_PATH, scopes=SCOPES)
        cliente = gspread.authorize(creds)
        sh = cliente.open_by_key(SHEET_ID)
        ws = sh.sheet1
        print(f"✅ Conexão OK! Aba: '{ws.title}' | Linhas: {len(ws.get_all_values())}")
    except Exception as e:
        print(f"❌ Erro: {e}")

testar()