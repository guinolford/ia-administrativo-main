import os
import re
import io
import json
import pdfplumber
from typing import List, Optional
import unicodedata
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from google.oauth2 import service_account
from pdf2image import convert_from_path
from paddleocr import PaddleOCR
import torch
from PIL import Image, ImageEnhance, ImageFilter

# ==== TENTATIVAS OPCIONAIS (FALLBACK) ====
try:
    import easyocr  # opcional (fallback)
    EASY_AVAILABLE = True
except Exception:
    EASY_AVAILABLE = False

try:
    import cv2  # opcional para melhor pré-processamento
    import numpy as np
    CV2_AVAILABLE = True
except Exception:
    CV2_AVAILABLE = False

# ========= CONFIG =========
SERVICE_ACCOUNT_FILE = "service_account.json"
DRIVE_ID = "1fae-_Meqs_xNDhLI2jkIGchTk3VWsf1h"
OUTPUT_DIR = "downloads"
JSON_OUT = "dados.json"
PROCESSED_FOLDERS = "processed_folders.json"
SCOPES = ['https://www.googleapis.com/auth/drive']

POPPLER_PATH = r"C:\poppler-24.08.0\Library\bin"
DPI = 200
DEBUG_SAVE_IMAGES = False  # se True, salva as imagens pré-processadas por página

BLACKLIST = {
    "jucesp", "docusign", "sp", "ribeir", "rireir", "acirp",
    "secretaria", "rg", "cpf", "cnpj", "contrato", "declaro", "autorizo",
    "empresa", "titular", "nome", "documento", "cpf-cnpj", "assinatura",
    "secretarla", "junta", "comercial", "alvará", "alvara", "recibo", "ato",
    "constitutivo", "jurídic", "firma", "entrega", "cartorio", "licanciamento",
    "licenciamento", "administração", "integrado", "representações", "parágrafo", "único",
    "instrução", "normativa", "representaçoes", "cadastro", "identificaçao", "representant",
    "servicos", "arbitral", "anexo", "serviços", "representacoes", "eu", "civil", "codigo",
    "rua", "sucesp", "responsabilidade", "motivo", "solicitação", "evento", "preenchimento",
    "reiteração", "observação", "impedimento", "protocolo", "logradouro", "complemento", "cep",
    "registro", "nacional", "matriz", "sociedade", "ficha", "cadastral", "outras", "descrever",
    "federal", "testemunhas", "sociais", "divergências", "microempreendedor"
}
CLUES = [
    "eu,", "portador do rg", "portador do cpf", "declaro", "titular",
    "(titular)", "nome:", "sr.", "sra.", "senhor", "nome completo",
    "identidade", "cpf", "rg", "portador", "brasileir", "portador",
    "cédula", "residente", "domiciliado", "eu"
]

# ========= GOOGLE AUTH =========
creds = service_account.Credentials.from_service_account_file(
    SERVICE_ACCOUNT_FILE, scopes=SCOPES
)
service = build('drive', 'v3', credentials=creds)

# ========= OCR ENGINES =========
# PaddleOCR atualizado; o aviso de depreciação do .ocr() é ok, funciona bem.
paddle_ocr = PaddleOCR(lang='pt', use_textline_orientation=True)
print("Usando PaddleOCR <<<<<<<")

easy_reader = None
if EASY_AVAILABLE:
    # pt + en costuma ajudar em contratos
    easy_reader = easyocr.Reader(['pt', 'en'], gpu=False, verbose=False)

# ========= Tratamento =========
def strip_accents(text: str) -> str:
    """Remove acentos de uma string."""
    return "".join(
        c for c in unicodedata.normalize("NFD", text)
        if unicodedata.category(c) != "Mn"
    )

# ========= UTILS =========
def load_json(path):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

def ensure_dir(path):
    os.makedirs(path, exist_ok=True)

def download_file(file_id, filename):
    request = service.files().get_media(fileId=file_id)
    fh = io.FileIO(filename, 'wb')
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return filename

# ========= PRÉ-PROCESSAMENTO DE IMAGEM =========
def preprocess_pil(img: Image.Image) -> Image.Image:
    """
    Pré-processamento suave (evita destruir detalhes):
    - grayscale
    - leve redução de ruído
    - aumento moderado de contraste
    (sem upscaling agressivo)
    """
    img = img.convert("L")
    img = img.filter(ImageFilter.MedianFilter(size=3))
    img = ImageEnhance.Contrast(img).enhance(1.6)
    return img

def preprocess_cv2(pil_img: Image.Image) -> Image.Image:
    """
    Pré-processamento com OpenCV (se disponível):
    - grayscale
    - blur leve
    - Otsu threshold (binarização adaptativa simples)
    Converte de volta para PIL.
    """
    if not CV2_AVAILABLE:
        return preprocess_pil(pil_img)

    img = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2GRAY)
    img = cv2.GaussianBlur(img, (3, 3), 0)
    _, img = cv2.threshold(img, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return Image.fromarray(img)

def preprocess_image(img: Image.Image) -> Image.Image:
    # Use OpenCV se disponível; caso contrário, PIL
    try:
        return preprocess_cv2(img)
    except Exception:
        return preprocess_pil(img)

# ========= OCR HELPERS =========
def ocr_with_paddle(img_path: str) -> List[str]:
    """
    Roda OCR com Paddle e retorna uma lista de linhas de texto.
    """
    lines = []
    try:
        result = paddle_ocr.predict(img_path) 
        if result and isinstance(result, list):
            # result é [ [ [box, (text, score)], ... ] ] na API antiga
            for det_line in result[0]:
                txt = det_line[1][0]
                if txt:
                    lines.append(txt.strip())
    except Exception as e:
        print(f"[PaddleOCR] Erro: {e}")
    return lines

def ocr_with_easy(img_path: str) -> List[str]:
    """
    OCR com EasyOCR (fallback). Retorna lista de linhas (parágrafos).
    """
    if not EASY_AVAILABLE or easy_reader is None:
        return []
    try:
        # detail=0 para retornar apenas textos; paragraph=True para juntar
        out = easy_reader.readtext(img_path, detail=0, paragraph=True)
        # garantir lista de strings
        return [s.strip() for s in out if isinstance(s, str) and s.strip()]
    except Exception as e:
        print(f"[EasyOCR] Erro: {e}")
        return []

def best_ocr_lines(img_path: str) -> List[str]:
    """
    Usa Paddle por padrão. Se vier muito fraco (poucas linhas ou quase sem dígitos/letras),
    tenta EasyOCR e retorna o melhor resultado (maior quantidade de caracteres).
    """
    paddle_lines = ocr_with_paddle(img_path)
    score_paddle = sum(len(x) for x in paddle_lines)

    # critério simples de "fraco": pouco texto total
    if score_paddle < 30 and EASY_AVAILABLE:
        easy_lines = ocr_with_easy(img_path)
        score_easy = sum(len(x) for x in easy_lines)
        return easy_lines if score_easy > score_paddle else paddle_lines

    return paddle_lines

# ========= EXTRAÇÃO (CPFs/RGs/Nomes) =========
CPF_MASKED = re.compile(r'\b\d{3}\.\d{3}\.\d{3}-\d{2}\b')
CPF_11DIG = re.compile(r'(?<!\d)(\d{11})(?!\d)')  # 11 dígitos sem máscara
RG_FLEX = re.compile(r'\b\d{1,2}\.?\d{3}\.?\d{3}-?[0-9Xx]\b')

def format_cpf_11(digits11: str) -> str:
    return f"{digits11[0:3]}.{digits11[3:6]}.{digits11[6:9]}-{digits11[9:11]}"

def dedupe_keep_order(items: List[str]) -> List[str]:
    seen = set()
    out = []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out

def extract_cpfs_from_text(text: str, clues: list[str] | None = None, blacklist: set[str] | None = None) -> list[str]:
    if clues is None:
        clues = ["cpf", "cadastro de pessoa física"]
    if blacklist is None:
        blacklist = {"rg", "123456", "0000000"}

    # regex padrão CPF
    import re
    pattern = r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b"
    candidatos = re.findall(pattern, text)

    # aplicar blacklist
    cpfs = [c for c in candidatos if c not in blacklist]

    # aplicar clues (só pega se aparecer perto de "CPF" no texto)
    filtrados = []
    for cpf in cpfs:
        for clue in clues:
            if clue.lower() in text.lower()[max(0, text.lower().find(cpf) - 15): text.lower().find(cpf) + 15]:
                filtrados.append(cpf)
                break

    return list(set(filtrados))

def extract_rgs_from_text(text: str, clues: list[str] | None = None, blacklist: set[str] | None = None) -> list[str]:
    if clues is None:
        clues = ["rg", "identidade", "cédula de identidade"]
    if blacklist is None:
        blacklist = {"123456", "0000000", "cpf"}  # exemplos a evitar

    # regex aproximado de RG (varia muito por estado)
    import re
    pattern = r"\b\d{1,3}\.?\d{3}\.?\d{3}\b"
    candidatos = re.findall(pattern, text)

    # aplicar blacklist
    rgs = [r for r in candidatos if r not in blacklist]

    # aplicar clues (tem que estar próximo de "RG" ou equivalente)
    filtrados = []
    for rg in rgs:
        for clue in clues:
            if clue.lower() in text.lower()[max(0, text.lower().find(rg) - 15): text.lower().find(rg) + 15]:
                filtrados.append(rg)
                break

    return list(set(filtrados))

NAME_PATTERN = re.compile(
    r"\b([A-ZÀ-Ý][A-Za-zÀ-ÿ'`-]+(?:\s+[A-ZÀ-Ý][A-Za-zÀ-ÿ'`-]+){1,6})\b"
)

def _dedupe_keep_order(items):
    seen = set()
    out = []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out

def _is_name_like(s: str, blacklist, clues=None) -> bool:
    # Normaliza
    s = s.strip()
    s_norm = strip_accents(s).lower()

    # Regras básicas de formato
    if len(s) < 5 or len(s) > 80:
        return False
    if len(s.split()) < 2:
        return False
    if any(ch.isdigit() for ch in s):
        return False

    # Blacklist híbrida
    for b in blacklist:
        b_norm = strip_accents(b).lower()
        if len(b_norm) <= 4:
            # palavras curtas → só se isolada
            if re.search(rf"\b{re.escape(b_norm)}\b", s_norm):
                return False
        else:
            # palavras maiores → substring já basta
            if b_norm in s_norm:
                return False

    # Clues (se usados), também normalizados
    if clues:
        if not any(strip_accents(c).lower() in s_norm for c in clues):
            return False

    return True

def extract_names_with_rules(
    text: str,
    cpfs: list[str],
    blacklist: set[str] | None = None,
    clues: list[str] | None = None,
) -> list[str]:
    """
    Retorna lista de nomes (strings) baseada em:
      - 'Eu, <NOME>, ...'
      - '<NOME>, portador(a) ...'
      - 'portador(a) do CPF/RG ... <NOME>'
      - proximidade com CPFs
    Aplica filtro de 'cara de nome' e BLACKLIST (case-insensitive).
    """
    if blacklist is None:
        blacklist = set(BLACKLIST)
    # transforma blacklist toda em minúsculas
    blacklist = {w.lower() for w in blacklist}

    if clues is None:
        clues = list(CLUES)
    # transforma clues em minúsculas
    clues = [c.lower() for c in clues]

    candidates = []
    lines = [ln.strip() for ln in text.splitlines()]

    for i, line in enumerate(lines):
        clean = " ".join(line.split())  # normaliza espaços
        clean_lower = clean.lower()     # versão minúscula para buscas

        # --- Caso 1: "Eu, <NOME>, ..."
        if "eu," in clean_lower:
            m_seg = re.search(r"(?i)eu,\s*([^,\n]{3,100})", clean)
            if m_seg:
                seg = m_seg.group(1)
                for m in NAME_PATTERN.finditer(seg):
                    cand = m.group(1).strip()
                    if _is_name_like(cand, blacklist):
                        candidates.append(cand)
                        break

        # --- Caso 2: "<NOME>, portador(a) ..."
        m_before = re.search(
            r"(?i)^\s*([A-ZÀ-Ý][A-Za-zÀ-ÿ'`-]+(?:\s+[A-ZÀ-Ý][A-Za-zÀ-ÿ'`-]+){1,6})\s*,\s*portador[a]?\b",
            clean
        )
        if m_before:
            cand = m_before.group(1).strip()
            if _is_name_like(cand, blacklist):
                candidates.append(cand)

        # --- Caso 3: "portador(a) do CPF/RG ... <NOME>"
        m_after = re.search(
            r"(?i)portador[a]?\s+do\s+(?:cpf|rg)[^A-Za-zÀ-ÿ]*"
            r"(?:\d{3}\.?\d{3}\.?\d{3}-?\d{2}|[0-9Xx\.\-]{5,})"
            r"[^A-Za-zÀ-ÿ]*([A-ZÀ-Ý][A-Za-zÀ-ÿ'`-]+(?:\s+[A-ZÀ-Ý][A-Za-zÀ-ÿ'`-]+){1,6})",
            clean
        )
        if m_after:
            cand = m_after.group(1).strip()
            if _is_name_like(cand, blacklist):
                candidates.append(cand)

        # --- Caso 4: linha contém alguma 'clue' personalizada
        if any(clue in clean_lower for clue in clues):
            window = clean + " " + (lines[i+1] if i+1 < len(lines) else "")
            for m in NAME_PATTERN.finditer(window):
                cand = m.group(1).strip()
                if _is_name_like(cand, blacklist):
                    candidates.append(cand)
                    break

    # --- Caso 5: proximidade com cada CPF (±2 linhas)
    if cpfs:
        for cpf in cpfs:
            for i, line in enumerate(lines):
                if cpf in line:
                    ctx = " ".join(lines[max(0, i-2):min(len(lines), i+3)])
                    for m in NAME_PATTERN.finditer(ctx):
                        cand = m.group(1).strip()
                        if _is_name_like(cand, blacklist):
                            candidates.append(cand)
                            break

    return _dedupe_keep_order(candidates)

def extract_data_from_text(text: str) -> dict:
    cpfs = extract_cpfs_from_text(text)
    rgs = extract_rgs_from_text(text)
    nomes = extract_names_with_rules(text, cpfs)
    return {"cpfs": cpfs, "rgs": rgs, "nomes": nomes}

# ========= CORE: LEITURA HÍBRIDA POR PÁGINA =========
def page_text_native(pdf_path: str, page_index: int) -> str:
    """
    Tenta extrair texto nativo (pdfplumber) da página especificada (0-based).
    Retorna string (possivelmente vazia).
    """
    try:
        with pdfplumber.open(pdf_path) as pdf:
            if page_index < 0 or page_index >= len(pdf.pages):
                return ""
            txt = pdf.pages[page_index].extract_text() or ""
            return txt.strip()
    except Exception as e:
        print(f"[pdfplumber] Erro na página {page_index+1}: {e}")
        return ""

def page_image_via_poppler(pdf_path: str, page_index: int, dpi: int) -> Optional[Image.Image]:
    """
    Renderiza UMA página específica como imagem usando Poppler (pdf2image).
    """
    try:
        imgs = convert_from_path(
            pdf_path,
            dpi=dpi,
            poppler_path=POPPLER_PATH,
            first_page=page_index + 1,
            last_page=page_index + 1
        )
        if imgs:
            return imgs[0]
    except Exception as e:
        print(f"[pdf2image] Erro ao renderizar página {page_index+1}: {e}")
    return None

def pdf_to_text_hybrid(pdf_path: str, txt_out: Optional[str] = None, dpi: int = DPI) -> str:
    """
    Para cada página:
      - tenta texto nativo (pdfplumber),
      - se insuficiente, renderiza a página via Poppler e aplica OCR (+ pré-processamento).
    Gera prints linha a linha e retorna todo o texto concatenado.
    """
    full_text = []
    page_count = 0

    # obtém número de páginas de forma robusta
    try:
        with pdfplumber.open(pdf_path) as pdf:
            total_pages = len(pdf.pages)
    except Exception as e:
        print("[pdfplumber] Falhou ao abrir PDF para contar páginas. Caindo para OCR global...", e)
        # OCR de todo o arquivo (último recurso)
        pages = convert_from_path(pdf_path, dpi=dpi, poppler_path=POPPLER_PATH)
        for i, p in enumerate(pages):
            print(f"[Página {i+1}] OCR (fallback global)...")
            img = preprocess_image(p)
            tmp_img_path = f"{pdf_path}_p{i+1}.png"
            img.save(tmp_img_path)
            lines = best_ocr_lines(tmp_img_path)
            os.remove(tmp_img_path)
            for L in lines:
                print(L)
                full_text.append(L)
        if txt_out:
            with open(txt_out, "w", encoding="utf-8") as f:
                f.write("\n".join(full_text))
        return "\n".join(full_text)

    # fluxo página a página
    for i in range(total_pages):
        native = page_text_native(pdf_path, i)
        if native and len(native) >= 30:
            print(f"[Página {i+1}] Texto nativo lido ({len(native)} chars).")
            for ln in native.splitlines():
                if ln.strip():
                    print(ln.strip())
                    full_text.append(ln.strip())
        else:
            print(f"[Página {i+1}] Pouco texto nativo. Renderizando imagem + OCR...")
            pil_img = page_image_via_poppler(pdf_path, i, dpi=dpi)
            if pil_img is None:
                print(f"[Página {i+1}] Falha ao renderizar. Pulando.")
                continue

            proc = preprocess_image(pil_img)

            if DEBUG_SAVE_IMAGES:
                debug_dir = os.path.join(os.path.dirname(pdf_path), "_debug_imgs")
                ensure_dir(debug_dir)
                proc.save(os.path.join(debug_dir, f"page_{i+1}_preproc.png"))

            tmp_img_path = f"{pdf_path}_p{i+1}.png"
            proc.save(tmp_img_path)

            lines = best_ocr_lines(tmp_img_path)
            try:
                os.remove(tmp_img_path)
            except Exception:
                pass

            if not lines:
                print(f"[Página {i+1}] OCR não retornou texto.")
            else:
                for L in lines:
                    print(L)
                    full_text.append(L)

        page_count += 1

    print(f"Total de páginas processadas: {page_count}")

    text_joined = "\n".join(full_text)
    if txt_out:
        with open(txt_out, "w", encoding="utf-8") as f:
            f.write(text_joined)
    return text_joined

def agrupar_por_pessoa(data: dict) -> dict:
    """
    Agrupa nomes, cpfs e rgs detectados.
    - Se tiver nome + cpf + rg -> entra em 'pessoas'
    - Se faltar algum dado -> vai para 'incompletos'
    """
    pessoas = []
    incompletos = []

    nomes = data.get("nomes", [])
    cpfs = data.get("cpfs", [])
    rgs = data.get("rgs", [])

    # heurística simples: emparelha por índice (ordem encontrada)
    for i in range(max(len(nomes), len(cpfs), len(rgs))):
        grupo = {
            "nome": nomes[i] if i < len(nomes) else None,
            "cpf": cpfs[i] if i < len(cpfs) else None,
            "rg": rgs[i] if i < len(rgs) else None,
        }
        if grupo["nome"] and grupo["cpf"] and grupo["rg"]:
            pessoas.append(grupo)
        else:
            incompletos.append(grupo)

    return {"pessoas": pessoas, "incompletos": incompletos}

# ========= PIPELINE =========
def process_drive():
    processed_folders = load_json(PROCESSED_FOLDERS)
    results = load_json(JSON_OUT)

    query = f"'{DRIVE_ID}' in parents and mimeType = 'application/vnd.google-apps.folder'"
    folders = service.files().list(q=query, fields="files(id, name)").execute().get("files", [])

    for folder in folders:
        folder_id, folder_name = folder["id"], folder["name"]
        if folder_id in processed_folders:
            print(f"Pasta já processada: {folder_name}")
            continue

        print(f"Processando pasta: {folder_name}")
        folder_dir = os.path.join(OUTPUT_DIR, folder_name)
        ensure_dir(folder_dir)

        # pega só PDFs desta pasta
        q = f"'{folder_id}' in parents and mimeType='application/pdf'"
        files = service.files().list(q=q, fields="files(id, name)").execute().get("files", [])
        if not files:
            print("Nenhum PDF na pasta.")
            continue

        total_files = len(files)
        for idx, f in enumerate(files, start=1):
            file_id, file_name = f["id"], f["name"]
            local_pdf = os.path.join(folder_dir, file_name)
            print(f"[{idx}/{total_files}] Baixando {file_name}...")
            download_file(file_id, local_pdf)

            txt_out = os.path.join(folder_dir, file_name.rsplit(".", 1)[0] + ".txt")
            text = pdf_to_text_hybrid(local_pdf, txt_out, dpi=DPI)

            data = extract_data_from_text(text)
            agrupado = agrupar_por_pessoa(data)

            results[file_name] = {
                "pasta": folder_name,
                "pessoas": agrupado["pessoas"],
                "incompletos": agrupado["incompletos"]
            }
            save_json(JSON_OUT, results)

            print(f"[{idx}/{total_files}] Processamento finalizado para {file_name}.\n")

        # marca a pasta como processada após todos os PDFs
        processed_folders[folder_id] = True
        save_json(PROCESSED_FOLDERS, processed_folders)

        print(f"Todos os {total_files} PDFs da pasta '{folder_name}' foram processados com sucesso.\n")
        #return  # sai após o primeiro PDF

# ========= MAIN =========
if __name__ == "__main__":
    process_drive()