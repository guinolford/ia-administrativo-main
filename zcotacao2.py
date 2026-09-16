try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass
import os
# (Código integrado — cole no lugar do seu arquivo atual)
import time
import pyotp
import requests
import logging
import re
from typing import Optional, Dict, List, Tuple
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, JavascriptException

# Google Sheets
import gspread

# -------------------------
# CONFIGURAÇÃO / CONSTANTES
# -------------------------

# Telegram (usei o token que você forneceu)
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
RESPONSAVEIS = {
    "M": os.getenv("RESPONSAVEIS_M", ""),  # Mariana
    "P": os.getenv("RESPONSAVEIS_P", ""),  # Pedro
    "R": os.getenv("RESPONSAVEIS_R", ""),  # Raissa
    "-": os.getenv("RESPONSAVEIS_FALLBACK", os.getenv("TELEGRAM_CHAT_ID", "")),  # fallback
}  # Chat ID(s)

# Google service account json (preencher com o caminho real)
SERVICE_ACCOUNT_FILE = os.getenv("SERVICE_ACCOUNT_FILE", os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json"))

# ID da planilha geral (sheet com CNPJ, STATUS, CONSULTOR, COTACAO)
PLANILHA_GERAL_ID = "1jmLwNTYad3ssofXElENwxmjD57I4LwU6tLvMUpW8cjM"

# Mapeamento CONSULTOR -> ID da planilha do consultor
consultores_planilhas = {
    "TESTE": "1hcEM77mebuhES0JX_UFju8dAQ44159x7a1OujH2bnys",

    ###########

    "ALINE ARAUJO": "1lqJb2f2zQZKw-QeR2VHHn5xZfsVJVG0LpeEohcH7BDQ",
    "ADERLANE MENDES": "1mT5lyCfmSmoVMgldO4g1apxCUZUkUqVN_zb_3q2W3Ss",
    "LETICIA OHARA": "1qjcSCEZTUDF2UUlAQyzcqmPM3LN8POnPOPgLfiWXlB0",
    "NATHALIA DADALTE": "1KaGQGcgVwm9-1QkUVKaYLkXbdaz3Omp1J449uzJwh5k",
    "JULIA PENAFORE": "1fsQ5gnQsjyC9pQnv8LIsMUMMOhWFT5-_i0Bz3aavDc8",
    "GABRIEL BARROS": "1RcaLGDoZXnX--biOA5zCqDjXYzTBKEWRf-nxe-099wA",
    "MEL SILVA": "11snG2IXYYIrNf-s_iOhImlYrV4Ra9hxC6To67Aom38A",
    "OTÁVIO SOUZA": "1p7ju-JyA_z3BdVV12WLzSt-vE7HGOEmnJwtrs4fsOOI",
    "OTAVIO SOUZA": "1p7ju-JyA_z3BdVV12WLzSt-vE7HGOEmnJwtrs4fsOOI",
    "THIAGO ROSSINI": "1huv98_foTTWs0pDt5qiPm4JD8MsaVkoo_K7PnfS7C6w",
    "JULIA VICTORIA": "1Nsa83nNrkYheWOnXx8EtH3z6UU7yYgpue0Bk028uucQ",
    "BRUNA LOPES": "1-jFBNJ567YkrzatuOjhjwhjIU9DAGORHwRVqFOnv_TY",
    "VERONICA MACEDO": "1qEfjqpH2MbCif8JgyBNK3nm5bZ6efkuWSimKMkOUtgk",

    "GABRIEL BRITTO": "1OeH9XlghRW3huQ6qn0s6C4rHLA_e-Fs21JFlh3dNtWk"
}

CONSULTOR_SHEETS = {k.upper().strip(): v for k, v in consultores_planilhas.items()}

# Coluna/slot alvo no grid do NEO (ex.: "3" no HTML <th id="vaadin-grid-cell-3"...>).
COTACAO_SLOT_SUFFIX = 3

# Arquivo de log local
LOG_FILE = "bot_neo.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler()
    ],
)
logger = logging.getLogger("NEO")

# STATUS que disparam a busca
STATUS_GATILHO = {
    "CONCLUÍDO", "VALIDAÇÃO PENDENTE", "ANÁLISE DE CRÉDITO", "REPROVADO CREDITO", "AGUARDANDO NOTA FISCAL",
    "ATIVADO 100%", "CONCLUÍDO INSPEÇÃO", "CONCLUÍDO INSPEÇÃO (CPC)", "AGUARDANDO ENTREGA"
}

# Tempo entre ciclos (segundos) -
SLEEP_BETWEEN_CYCLES = 905

# -------------------------
# HELPERS: Telegram / Logs
# -------------------------
def enviar_telegram_T(mensagem: str):
    chat_id = RESPONSAVEIS.get("T")
    if not chat_id:
        logging.error("[TELEGRAM] Chat ID 'T' não configurado.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": mensagem, "parse_mode": "HTML"}
    try:
        resp = requests.post(url, data=payload, timeout=6)
        if resp.status_code != 200:
            logging.error(f"[TELEGRAM] Falha ao enviar: {resp.status_code} {resp.text}")
        else:
            logging.info("[TELEGRAM] Notificação enviada.")
    except requests.RequestException as e:
        logging.exception(f"[TELEGRAM] Exception ao enviar mensagem: {e}")

def log_and_notify_on_empty(cnpj: str, consultor: str, valor: str):
    msg = f"[ALERTA] CNPJ: {cnpj} | Consultor: {consultor} | Valor capturado: {repr(valor)}"
    logging.warning(msg)
    enviar_telegram_T(msg)

# -------------------------
#  HELPER P/ NORMALIZAÇÃO
# -------------------------
def normalize_cnpj(cnpj: str) -> str:
    if cnpj is None:
        return ""
    return re.sub(r"\D", "", str(cnpj))

def normalize_header_name(h):
    if not h:
        return ""
    import unicodedata, re
    s = str(h)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.category(ch).startswith("M"))
    s = re.sub(r"\s+", "", s)
    s = s.replace("/", "")
    return s.upper()

def get_value(row_data, target_name):
    target_norm = normalize_header_name(target_name)
    for k, v in row_data.items():
        if normalize_header_name(k) == target_norm:
            return v
    return ""

def col_idx_to_letter(idx: int) -> str:
    letters = []
    while idx > 0:
        idx, rem = divmod(idx - 1, 26)
        letters.append(chr(ord('A') + rem))
    return "".join(reversed(letters))

def build_consultor_rowmap(ws) -> Dict[str, list]:
    rowmap = {}
    try:
        all_rows = ws.get_all_values()
        for r_idx, row in enumerate(all_rows, start=1):
            for cell in row:
                norm = normalize_cnpj(cell)
                if norm:
                    rowmap.setdefault(norm, []).append(r_idx)
    except Exception:
        return {}
    return rowmap

# -------------------------
# NEO: Login & captura (seu código original)
# -------------------------
def login_neo(segredo_totp: str, email: str, senha: str, headless: bool = False):
    chrome_options = Options()
    if headless:
        chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--start-maximized")
    driver = webdriver.Chrome(options=chrome_options)
    wait = WebDriverWait(driver, 20)

    driver.get("https://datavoxx.neosales.com.br/login")

    wait.until(EC.presence_of_element_located(
        (By.XPATH, "//label[contains(text(), 'Login')]/following::input[1]"))).send_keys(email)
    wait.until(EC.presence_of_element_located(
        (By.XPATH, "//label[contains(text(), 'Senha')]/following::input[1]"))).send_keys(senha)

    if segredo_totp:
        totp_codigo = pyotp.TOTP(segredo_totp).now()
        logging.info(f"[NEO] Código TOTP gerado.")
        wait.until(EC.presence_of_element_located(
            (By.XPATH, "//label[contains(text(), 'Codigo 2FA')]/following::input[1]"))).send_keys(totp_codigo)

    wait.until(EC.element_to_be_clickable((By.ID, "btnLogar"))).click()
    wait.until(EC.presence_of_element_located((By.XPATH, "//a[.//span[text()='Home']]")))
    driver.get("https://datavoxx.neosales.com.br/painel-producao/pedido")
    logging.info("[NEO] Login bem-sucedido e página de pedidos aberta.")
    return driver

def buscar_atividade_cnpj(driver, cnpj: str, timeout: int = 20) -> Optional[webdriver.remote.webdriver.WebElement]:
    wait = WebDriverWait(driver, timeout)
    wait.until(EC.element_to_be_clickable((By.XPATH, "//label[text()='Lista Cnpj']"))).click()
    campo_focado = driver.switch_to.active_element
    campo_focado.send_keys(cnpj)
    driver.find_element(By.ID, "btnPesquisar").click()
    time.sleep(2)
    try:
        botoes = wait.until(EC.presence_of_all_elements_located((By.XPATH, "//vaadin-button[@title='Abrir atividade']")))
    except TimeoutException:
        botoes = []
    if not botoes:
        logging.info(f"[{cnpj}] Nenhum botão 'Abrir atividade' encontrado.")
        return None
    logging.info(f"[{cnpj}] Botão 'Abrir atividade' encontrado.")
    return botoes[0]

def get_column_index_from_header(driver, header_id: str) -> Optional[int]:
    ths = driver.find_elements(By.XPATH, "//th[starts-with(@id, 'vaadin-grid-cell-')]")
    for idx, th in enumerate(ths):
        try:
            if th.get_attribute("id") == header_id:
                return idx
        except Exception:
            continue
    return None

def get_cell_text_for_slot_or_index(driver, botao_element):
    if botao_element is None:
        print("[DEBUG] Botão é None")
        return None
    try:
        fallback_cells = botao_element.find_elements(
            By.XPATH,
            "./ancestor::vaadin-grid-cell-content/following-sibling::vaadin-grid-cell-content"
        )
        if len(fallback_cells) >= 2:
            valor = fallback_cells[1].text.strip()
            print(f"[DEBUG] Valor capturado: '{valor}'")
            return valor
        elif fallback_cells:
            valor = fallback_cells[0].text.strip()
            print(f"[DEBUG] Valor capturado (primeira célula à direita): '{valor}'")
            return valor
        else:
            print("[DEBUG] Nenhuma célula encontrada à direita do botão")
            return ""
    except Exception as e:
        print(f"[DEBUG] Falha ao capturar valor à direita do botão: {e}")
        return ""

# -------------------------
# Google Sheets helpers
# -------------------------
def gsheet_client():
    return gspread.service_account(filename=SERVICE_ACCOUNT_FILE)

def read_planilha_geral_records(client):
    try:
        sh = client.open_by_key(PLANILHA_GERAL_ID)
        ws = sh.get_worksheet(0)
        all_values = ws.get_all_values()
        if not all_values:
            raise ValueError("Planilha geral está vazia.")

        required_headers = {"CNPJ/CPF", "STATUS", "CONSULTOR", "COTAÇÃO"}
        # tentamos as primeiras 3 linhas como candidatas (um pouco mais robusto)
        candidate_headers = []
        max_header_scan = min(3, len(all_values))
        for i in range(max_header_scan):
            candidate_headers.append(all_values[i])

        header = None
        header_row_idx = None
        for i, cand in enumerate(candidate_headers):
            # normalizar cada célula candidata: remover espaços e uppercase (mantemos acentos por ora)
            normalized = { (h.strip().upper() if isinstance(h, str) else "").replace(" ", "") for h in cand if (isinstance(h, str) and h.strip()) }
            if required_headers.issubset(normalized) or required_headers.issubset({h.strip().upper() for h in cand if isinstance(h, str) and h.strip()}):
                header = cand
                header_row_idx = i
                break

        if header is None:
            # fallback: tentar heurística com normalize_header_name (remove acentos)
            for i, cand in enumerate(candidate_headers):
                normalized2 = { normalize_header_name(h) for h in cand if isinstance(h, str) and h.strip() }
                req_norm = { normalize_header_name(x) for x in required_headers }
                if req_norm.issubset(normalized2):
                    header = cand
                    header_row_idx = i
                    break

        if header is None:
            raise ValueError("Nenhuma linha com headers obrigatórios encontrada.")

        rows = all_values[header_row_idx + 1 :]

        header_map = {}
        for idx, col in enumerate(header):
            if isinstance(col, str) and col.strip() != "":
                header_map[col.strip()] = idx  # zero-based index

        records = []
        start_row_number = header_row_idx + 2
        for offset, row in enumerate(rows):
            real_row_idx = start_row_number + offset
            record = {}
            for col_name, col_idx in header_map.items():
                value = row[col_idx] if col_idx < len(row) else ""
                record[col_name] = value
            record["__row__"] = real_row_idx
            records.append(record)

        logging.info(f"[GERAL] Headers escolhidos (linha {header_row_idx} zero-based): {header}")
        # retornamos header_row_idx também para uso posterior
        return records, ws, header_map, header_row_idx

    except Exception as e:
        logger.error(f"Erro ao ler planilha geral: {e}")
        raise

def open_consultor_sheet_by_id(client, sheet_id):
    try:
        sh = client.open_by_key(sheet_id)
        ws = sh.sheet1
        return ws
    except Exception:
        return None

def find_row_index_by_cnpj(ws, cnpj: str) -> Optional[int]:
    norm_target = normalize_cnpj(cnpj)
    if not norm_target:
        return None
    try:
        all_rows = ws.get_all_values()
        for row_idx, row in enumerate(all_rows, start=1):
            for cell_value in row:
                if normalize_cnpj(cell_value) == norm_target:
                    return row_idx
    except Exception:
        pass
    return None

def find_column_index_by_header(ws, header_name: str, header_row: int = 1) -> Optional[int]:
    """
    Procura a coluna `header_name` na linha `header_row` (1-based).
    Retorna índice 1-based da coluna (compatível com gspread) ou None.
    Faz comparação usando normalize_header_name (remoção de acentos, espaços).
    """
    try:
        headers = ws.row_values(header_row)  # header_row é 1-based
        target_norm = normalize_header_name(header_name)
        for i, h in enumerate(headers, start=1):
            if normalize_header_name(str(h)) == target_norm:
                return i
        # fallback permissivo: busca por substring normalizada
        for i, h in enumerate(headers, start=1):
            if target_norm in normalize_header_name(str(h)):
                return i
    except Exception:
        pass
    return None

def update_consultor_cotacao(ws, row_idx: int, col_idx: int, valor: str):
    ws.update_cell(row_idx, col_idx, valor)

# -------------------------
# NOVAS FUNÇÕES: sweep + mapeamento + updates
# -------------------------

# Equivalencia exemplo — edite para seus casos reais (prefixo -> lista de nomes que aparecem em 'PRODUTOS' da GERAL)
EQUIVALENCIA = {
    "MV": ["MÓVEL", "PASSAPORTE", "CHIP DADOS", "CLARO MONITOR", "PACOTE ADICIONAL"],
    "FB": ["FIXA", "BANDA LARGA", "TV"],
}

# JS helpers (shadow-aware)
_FIND_TABLE_JS = r"""
return (function(){
  function query(node, sel){
    try {
      var q = node.querySelector(sel);
      if(q) return q;
    } catch(e){}
    var children = node.children || [];
    for(var i=0;i<children.length;i++){
      var c = children[i];
      if(c.shadowRoot){
        var r = query(c.shadowRoot, sel);
        if(r) return r;
      }
      var r2 = query(c, sel);
      if(r2) return r2;
    }
    return null;
  }
  try { var t = document.getElementById('table'); if(t) return t; } catch(e){}
  var t2 = query(document, 'table#table');
  if(t2) return t2;
  var t3 = query(document, 'table');
  if(t3) return t3;
  return null;
})();
"""

_FIND_SCROLLABLE_CONTAINER_JS = r"""
return (function(el){
  function isScrollable(n){
    try {
      return n && n.scrollWidth && n.clientWidth && n.scrollWidth > n.clientWidth;
    } catch(e){ return false; }
  }
  var p = el;
  while(p){
    if(isScrollable(p)) return p;
    p = p.parentNode;
  }
  function findRec(node){
    try {
      if(isScrollable(node)) return node;
    } catch(e){}
    var children = node.children || [];
    for(var i=0;i<children.length;i++){
      var res = findRec(children[i]);
      if(res) return res;
    }
    try {
      if(node.shadowRoot){
        var ch = node.shadowRoot.children || [];
        for(var j=0;j<ch.length;j++){
          var rr = findRec(ch[j]);
          if(rr) return rr;
        }
      }
    } catch(e){}
    return null;
  }
  var found = findRec(document.body);
  if(found) return found;
  return null;
})(arguments[0]);
"""

_GET_VISIBLE_PAIRS_JS = r"""
return (function(){
  function collectButtons(root, selector, list){
    try { Array.from(root.querySelectorAll(selector)).forEach(e => list.push(e)); } catch(e){}
    var children = root.children || [];
    for(var i=0;i<children.length;i++){
      var c = children[i];
      if(c.shadowRoot) collectButtons(c.shadowRoot, selector, list);
      collectButtons(c, selector, list);
    }
  }
  var btns = [];
  collectButtons(document, 'vaadin-button[title=\"Abrir atividade\"]', btns);
  var pairs = [];
  for(var i=0;i<btns.length;i++){
    var b = btns[i];
    var anc = b;
    while(anc && anc.tagName && anc.tagName.toLowerCase() !== 'vaadin-grid-cell-content'){
      anc = anc.parentNode;
    }
    if(!anc) continue;
    var sib = anc.nextElementSibling;
    var arr = [];
    while(sib){
      arr.push(sib);
      sib = sib.nextElementSibling;
    }
    var col2 = (arr[1] ? (arr[1].innerText || arr[1].textContent || '') : '').trim();
    var col6 = (arr[5] ? (arr[5].innerText || arr[5].textContent || '') : '').trim();
    pairs.push({col2: col2, col6: col6});
  }
  return pairs;
})();
"""

def fetch_sweep_mappings(driver, cnpj: str,
                         max_steps: int = 200,
                         step_px: int = 300,
                         sleep_after_vertical: float = 3.0,
                         sleep_between_steps: float = 0.5) -> List[Dict[str, str]]:
    """
    Executa a sequência:
      - buscar_atividade_cnpj(driver, cnpj) -> foca o grid
      - encontra a table (shadow-aware)
      - rola o scroller correto verticalmente (se houver)
      - espera sleep_after_vertical
      - sweep horizontal: em cada passo executa _GET_VISIBLE_PAIRS_JS para coletar pares {col2,col6}
      - avança scrollLeft no container detectado
    Retorna lista ordenada de dicts unique por 'col2||col6'.
    """
    # 1) Garante que fizemos a pesquisa do CNPJ no grid
    try:
        buscar_atividade_cnpj(driver, cnpj)
    except Exception as e:
        logging.exception(f"[SWEEP] Falha ao executar buscar_atividade_cnpj para {cnpj}: {e}")
        # continuamos; talvez o grid já esteja preenchido

    time.sleep(1.5)  # espera breve para o grid começar a renderizar

    # 2) encontra table via JS (shadow-aware)
    try:
        tbl = driver.execute_script(_FIND_TABLE_JS)
        if tbl:
            logging.info("[SWEEP] table encontrada via JS shadow-aware.")
        else:
            logging.warning("[SWEEP] table NÃO encontrada via JS (vai tentar coleta global).")
    except JavascriptException as e:
        logging.exception(f"[SWEEP] JS find table falhou: {e}")
        tbl = None

    # 3) rolar o scroller adequado verticalmente
    try:
        if tbl:
            sc = driver.execute_script(_FIND_SCROLLABLE_CONTAINER_JS, tbl)
            if sc:
                driver.execute_script("arguments[0].scrollTop = arguments[0].scrollHeight;", sc)
                logging.info("[SWEEP] Rolado scroller encontrado (ancestor/fallback).")
            else:
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                logging.info("[SWEEP] Rolado window (fallback).")
        else:
            # sem table, tenta rolar vaadin-scroller ao menos
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            logging.info("[SWEEP] Rolado window (sem table).")
    except Exception as e:
        logging.warning(f"[SWEEP] Falha ao rolar verticalmente: {e}")

    # 4) espera entre vertical e inicio do sweep
    time.sleep(sleep_after_vertical)

    # 5) sweep horizontal
    seen = set()
    ordered = []
    scroll_container = None
    if tbl:
        try:
            scroll_container = driver.execute_script(_FIND_SCROLLABLE_CONTAINER_JS, tbl)
            if not scroll_container:
                scroll_container = tbl
        except Exception:
            scroll_container = tbl

    # fallback: se não temos table/scroll container, operamos com document.body (algumas funções JS trabalharão globalmente)
    if not scroll_container:
        try:
            body_elem = driver.find_element(By.TAG_NAME, "body")
            scroll_container = body_elem
        except Exception:
            scroll_container = None

    for step in range(max_steps):
        # collect pairs visible now
        try:
            pairs = driver.execute_script(_GET_VISIBLE_PAIRS_JS)
        except Exception as e:
            logging.warning(f"[SWEEP] Falha JS coletando pares (step {step}): {e}")
            pairs = []

        for p in pairs:
            key = f"{(p.get('col2') or '').strip()}||{(p.get('col6') or '').strip()}"
            if key not in seen:
                seen.add(key)
                ordered.append({"col2": (p.get("col2") or "").strip(), "col6": (p.get("col6") or "").strip()})

        # attempt to scroll horizontally
        try:
            if scroll_container:
                driver.execute_script("arguments[0].scrollLeft += arguments[1];", scroll_container, step_px)
            elif tbl:
                driver.execute_script("arguments[0].scrollLeft += arguments[1];", tbl, step_px)
            else:
                # nothing to scroll
                break
        except Exception as e:
            logging.warning(f"[SWEEP] Falha ao mover scrollLeft (step {step}): {e}")
            break

        # optionally check if we've reached the end
        try:
            if scroll_container:
                cur_left = driver.execute_script("return arguments[0].scrollLeft;", scroll_container) or 0
                sw = driver.execute_script("return arguments[0].scrollWidth || 0;", scroll_container) or 0
                cw = driver.execute_script("return arguments[0].clientWidth || 0;", scroll_container) or 0
                if sw and cw and cur_left + cw >= sw - 5:
                    logging.info("[SWEEP] Detectado fim do scroll horizontal.")
                    break
        except Exception:
            # ignore and continue
            pass

        time.sleep(sleep_between_steps)

    logging.info(f"[SWEEP] Sweep finalizado. Pares coletados: {len(ordered)}")
    return ordered

# Normalização e mapeamento
def normalize_produto_prefix(produto_raw: str) -> str:
    if not produto_raw:
        return ""
    try:
        left = str(produto_raw).split("-", 1)[0]
        return left.replace(" ", "").upper()
    except Exception:
        return str(produto_raw).strip().upper()

def build_prefix_to_cotacao_from_sweep(sweep_mappings: list) -> Dict[str, str]:
    prefix_map: Dict[str, str] = {}
    for item in sweep_mappings:
        cot = (item.get("col2") or "").strip()
        prod_raw = (item.get("col6") or "").strip()
        if not cot or cot == "0":
            continue
        prefix = normalize_produto_prefix(prod_raw)
        if not prefix:
            continue
        if prefix not in prefix_map:
            prefix_map[prefix] = cot
    return prefix_map

from typing import Dict, List, Tuple

def validate_prefixes_against_equivalencia(
    prefix_map: Dict[str, str], 
    equivalencia_map: Dict[str, List[str]]
) -> Tuple[bool, List[str]]:
    unknowns = [p for p in prefix_map.keys() if p not in equivalencia_map]
    return len(unknowns) == 0, unknowns

# Encontrar linhas na planilha GERAL
def find_rows_matching_cnpj_and_products(records: list, cnpj_norm: str, product_names: list) -> list:
    matches = []
    product_names_upper = [p.upper() for p in product_names]
    for rec in records:
        cnpj_cell = rec.get("CNPJ/CPF") or rec.get("CNPJ") or rec.get("CNPJ / CPF") or ""
        if normalize_cnpj(cnpj_cell) != cnpj_norm:
            continue
        produtos_cell = str(rec.get("PRODUTOS") or rec.get("PRODUTO") or "")
        produtos_upper = produtos_cell.upper()
        if not produtos_upper:
            continue
        for prod in product_names_upper:
            if prod in produtos_upper:
                matches.append((rec["__row__"], prod))
    return matches

def build_geral_updates_for_prefix_map(geral_ws, records: list, cnpj_norm: str,
                                       prefix_map: Dict[str, str],
                                       equivalencia_map: Dict[str, list],
                                       header_row_idx: int):
    """
    Retorna lista de tuplas: (row_idx, col_idx_cotacao, valor, prefixo, matched_prod)
    header_row_idx: zero-based (como retornado em read_planilha_geral_records)
    """
    # convert header_row_idx (zero-based) para linha 1-based para gspread
    header_row_1based = header_row_idx + 1
    # tenta encontrar coluna 'COTAÇÃO' na linha correta
    col_idx_cotacao = find_column_index_by_header(geral_ws, "COTAÇÃO", header_row=header_row_1based) \
                      or find_column_index_by_header(geral_ws, "COTACAO", header_row=header_row_1based) \
                      or find_column_index_by_header(geral_ws, "Cotação", header_row=header_row_1based)
    if not col_idx_cotacao:
        raise RuntimeError("Coluna 'COTAÇÃO' não encontrada na planilha GERAL (na linha de headers detectada).")
    updates = []
    for prefixo, cotacao in prefix_map.items():
        produto_lista = equivalencia_map.get(prefixo, [])
        if not produto_lista:
            continue
        matched_rows = find_rows_matching_cnpj_and_products(records, cnpj_norm, produto_lista)
        for (row_idx, matched_prod) in matched_rows:
            updates.append((row_idx, col_idx_cotacao, cotacao, prefixo, matched_prod))
    return updates

def apply_batch_updates_to_geral(target_ws, updates: List[Tuple[int,int,str,str,str]]):
    """
    Aplica updates na worksheet target_ws (pode ser a planilha do consultor).
    updates: list de (row_idx, col_idx, valor, prefixo, matched_prod)
    Tenta primeiro values_batch_update no objeto spreadsheet; em caso de falha tenta update_cell por célula.
    """
    if not updates:
        logging.info("[GERAL] Nenhuma atualização para aplicar (target sheet vazia).")
        return

    # obtém objeto Spreadsheet (sh) a partir da worksheet; caso falhe tenta reabrir via API
    sh = None
    try:
        sh = target_ws.spreadsheet
    except Exception:
        logging.warning("[GERAL] Não foi possível obter target_ws.spreadsheet diretamente; tentando reabrir via client.")
        try:
            client = gsheet_client()
            # Tentamos encontrar pela title (fallback frágil), ideal é abrir por key - se não tiver, retornamos erro
            sh = client.open(target_ws.spreadsheet.title)
        except Exception as e:
            logging.exception(f"[GERAL] Falha ao reabrir spreadsheet do target_ws: {e}")
            enviar_telegram_T(f"[ERRO] Não foi possível abrir a planilha do consultor para gravação: {e}")
            return

    data = []
    seen_ranges = set()
    for row_idx, col_idx, valor, prefixo, matched_prod in updates:
        # col_idx aqui já deve ser 1-based (conforme find_column_index_by_header)
        col_letter = col_idx_to_letter(col_idx)
        a1 = f"'{target_ws.title}'!{col_letter}{row_idx}"
        if a1 in seen_ranges:
            continue
        seen_ranges.add(a1)
        data.append({"range": a1, "values": [[valor]]})

    body = {"valueInputOption": "USER_ENTERED", "data": data}
    try:
        logging.info(f"[GERAL] Enviando batch ({len(data)} células) para a planilha '{target_ws.spreadsheet.title}' (aba '{target_ws.title}').")
        sh.values_batch_update(body)
        logging.info("[GERAL] Batch aplicado com sucesso (via values_batch_update).")
        return
    except Exception as e:
        logging.exception(f"[GERAL] Falha no batch para a planilha (tentando fallback por célula): {e}")

    # Fallback por célula individual (mais lento)
    for row_idx, col_idx, valor, prefixo, matched_prod in updates:
        try:
            target_ws.update_cell(row_idx, col_idx, valor)
            logging.info(f"[GERAL][FALLBACK] Valor '{valor}' escrito na linha {row_idx}, col {col_idx} da planilha '{target_ws.spreadsheet.title}'.")
        except Exception as e2:
            logging.exception(f"[GERAL][FALLBACK] Falha escrever célula ({row_idx},{col_idx}) na planilha '{target_ws.spreadsheet.title}': {e2}")
            enviar_telegram_T(f"[ERRO GRAVACAO] Falha ao gravar planilha do consultor (sheet='{target_ws.spreadsheet.title}', aba='{target_ws.title}') linha {row_idx}: {e2}")

# -------------------------
# Fluxo integrado por CNPJ (adaptado)
# -------------------------

def processar_todas_linhas_em_batch(driver, client, geral_ws, records, header_row_idx):
    """
    Versão atualizada que:
      - agrupa targets por CNPJ e consultor,
      - para cada CNPJ faz sweep -> prefix_map,
      - para cada consultor associado abre a planilha do consultor e grava as cotações lá.
    """
    logging.info("[BATCH] Iniciando processamento em batch de todas as linhas lidas.")

    # 1) identificar linhas elegíveis (mantendo consultor e linha da Geral)
    targets = []
    for rec in records:
        cnpj_raw = get_value(rec, "CNPJ/CPF") or get_value(rec, "CNPJ") or get_value(rec, "CNPJ / CPF")
        cnpj = normalize_cnpj(cnpj_raw)
        status = str(get_value(rec, "STATUS")).strip()
        cotacao_atual = str(get_value(rec, "COTAÇÃO") or get_value(rec, "COTACAO")).strip()
        consultor = str(get_value(rec, "CONSULTOR")).strip()
        geral_row = rec.get("__row__")

        if not cnpj:
            continue
        if status.upper() not in STATUS_GATILHO:
            continue
        if cotacao_atual != "":
            continue

        targets.append({"cnpj": cnpj, "orig": cnpj_raw, "consultor": consultor, "geral_row": geral_row})

    if not targets:
        logging.info("[BATCH] Nenhuma linha elegível para captura de cotação.")
        return

    # Agrupa por CNPJ -> lista de targets (cada target contém consultor e geral_row)
    cnpj_to_targets = {}
    for t in targets:
        cnpj_to_targets.setdefault(t["cnpj"], []).append(t)

    logging.info(f"[BATCH] Serão processados {len(cnpj_to_targets)} CNPJ(s) únicos.")

    # Processa cada CNPJ (um sweep por CNPJ)
    for cnpj, target_list in cnpj_to_targets.items():
        try:
            logging.info(f"[BATCH] Executando sweep no NEO para CNPJ {cnpj}...")
            sweep_mappings = fetch_sweep_mappings(driver, cnpj)
            logging.info(f"[BATCH] Sweep retornou {len(sweep_mappings)} itens para {cnpj}.")
            prefix_map = build_prefix_to_cotacao_from_sweep(sweep_mappings)
            if not prefix_map:
                logging.info(f"[BATCH] Nenhum prefixo/cotação válido encontrado para {cnpj}. Pulando.")
                continue

            ok, unknowns = validate_prefixes_against_equivalencia(prefix_map, EQUIVALENCIA)
            if not ok:
                msg = f"[ERRO PREFIXOS] Prefixos desconhecidos detectados: {unknowns} para CNPJ {cnpj}"
                logging.error(msg)
                enviar_telegram_T(msg)
                continue

            # Para recuperar matched_prod por prefix, usamos a GERAL: procurar todas as linhas da GERAL com esse CNPJ e produtos
            # matched_rows -> lista de tuples (linha_geral, produto_matched)
            # vamos construir um mapa prefixo -> list of matched_prod (p/ contexto/diagnóstico)
            prefix_to_matched = {}
            for prefixo in prefix_map.keys():
                produto_lista = EQUIVALENCIA.get(prefixo, [])
                matched_rows = find_rows_matching_cnpj_and_products(records, cnpj, produto_lista)
                # matched_rows pode ter várias ocorrências; guardamos apenas os produtos
                prefix_to_matched[prefixo] = [m[1] for m in matched_rows] or []

            # Agora, para cada consultor que tem linhas elegíveis naquele CNPJ, escrevemos na planilha do consultor
            for target in target_list:
                consultor_raw = target.get("consultor", "")
                consultor_key = consultor_raw.upper().strip() if isinstance(consultor_raw, str) else ""
                sheet_id = CONSULTOR_SHEETS.get(consultor_key)
                if not sheet_id:
                    msg = f"[ERRO MAPEAMENTO] Consultor '{consultor_raw}' não mapeado em CONSULTOR_SHEETS (CNPJ {cnpj})."
                    logging.error(msg)
                    enviar_telegram_T(msg)
                    continue

                # abre planilha do consultor
                ws_consultor = open_consultor_sheet_by_id(client, sheet_id)
                if not ws_consultor:
                    msg = f"[ERRO ABRIR SHEET] Não foi possível abrir a planilha do consultor '{consultor_raw}' (id={sheet_id}) para CNPJ {cnpj}."
                    logging.error(msg)
                    enviar_telegram_T(msg)
                    continue

                # encontra a linha do consultor para este CNPJ
                row_idx_consultor = find_row_index_by_cnpj(ws_consultor, cnpj)
                if not row_idx_consultor:
                    msg = f"[ERRO LINHA] Não encontrei CNPJ {cnpj} na planilha do consultor '{consultor_raw}' (id={sheet_id})."
                    logging.warning(msg)
                    enviar_telegram_T(msg)
                    continue

                # encontra coluna 'COTAÇÃO' na planilha do consultor (assume header na primeira linha)
                col_idx_cotacao_consultor = find_column_index_by_header(ws_consultor, "COTAÇÃO", header_row=1) \
                                            or find_column_index_by_header(ws_consultor, "COTACAO", header_row=1) \
                                            or find_column_index_by_header(ws_consultor, "Cotação", header_row=1)
                if not col_idx_cotacao_consultor:
                    msg = f"[ERRO COLUNA] Coluna 'COTAÇÃO' não encontrada na planilha do consultor '{consultor_raw}' (id={sheet_id})."
                    logging.error(msg)
                    enviar_telegram_T(msg)
                    continue

                # prepara updates: se houver múltiplos prefixos, podemos priorizar uma lógica (ex: gravar o primeiro encontrado)
                # aqui gravamos todos os prefixos diferentes em linhas/separadores iguais: se quiser apenas 1, adapte.
                updates_for_this_consultor = []
                for prefixo, cotacao in prefix_map.items():
                    # matched_prod list pode ter vários valores; escolhemos o primeiro para contexto
                    matched_prod = prefix_to_matched.get(prefixo, [])
                    matched_prod_val = matched_prod[0] if matched_prod else ""
                    # escrever a cotação na linha do consultor encontrada
                    updates_for_this_consultor.append((row_idx_consultor, col_idx_cotacao_consultor, cotacao, prefixo, matched_prod_val))

                if not updates_for_this_consultor:
                    logging.info(f"[BATCH] Nenhum update para aplicar na planilha do consultor '{consultor_raw}' para CNPJ {cnpj}.")
                    continue

                # aplica batch na planilha do consultor
                try:
                    apply_batch_updates_to_geral(ws_consultor, updates_for_this_consultor)
                except Exception as e:
                    logging.exception(f"[BATCH] Falha ao aplicar updates para consultor '{consultor_raw}' (CNPJ {cnpj}): {e}")
                    enviar_telegram_T(f"[ERRO] Falha ao aplicar updates para consultor '{consultor_raw}' (CNPJ {cnpj}): {e}")
                    continue

            # opcional: refresh na página do NEO após processar este CNPJ para evitar stale DOM
            try:
                driver.refresh()
                logging.info("[BATCH] driver.refresh() executado após sweep.")
            except Exception:
                pass

            time.sleep(2)

        except Exception as e:
            logging.exception(f"[BATCH] Falha processando CNPJ {cnpj}: {e}")
            enviar_telegram_T(f"[ERRO] Falha ao processar CNPJ {cnpj}: {e}")
            continue

    logging.info("[BATCH] Processamento em batch finalizado.")

# -------------------------
# Loop principal (re-login a cada ciclo)
# -------------------------
def main_loop(segredo_totp: str, email: str, senha: str):
    client = gsheet_client()

    while True:
        driver = None
        try:
            # Cria um driver novo e loga no início de cada ciclo (evita logout silencioso)
            driver = login_neo(segredo_totp, email, senha, headless=False)
            # lê planilha GERAL
            try:
                records, geral_ws, geral_header_map, header_row_idx = read_planilha_geral_records(client)
            except Exception as e:
                logging.exception(f"Erro ao ler planilha geral: {e}")
                enviar_telegram_T(f"[ERRO] Não foi possível ler a planilha geral: {e}")
                # fecha driver e aguarda antes de tentar de novo
                try:
                    driver.quit()
                except Exception:
                    pass
                time.sleep(60)
                continue

            logging.info(f"Iniciando ciclo: {len(records)} linhas lidas da planilha geral.")

            try:
                processar_todas_linhas_em_batch(driver, client, geral_ws, records, header_row_idx)
            except Exception as e:
                logging.exception(f"Erro no processamento em batch: {e}")
                enviar_telegram_T(f"[EXCEÇÃO] Erro no processamento em batch: {e}")

        finally:
            if driver:
                try:
                    driver.quit()
                    logging.info("[MAIN] Driver fechado ao final do ciclo.")
                except Exception:
                    pass

        logging.info(f"Ciclo finalizado. Aguardando {SLEEP_BETWEEN_CYCLES} segundos antes do próximo ciclo.")
        time.sleep(SLEEP_BETWEEN_CYCLES)

# -------------------------
# ENTRYPOINT (exemplo)
# -------------------------
if __name__ == "__main__":
    segredo = "MMZTIYLGMY3GCLLFGQZDELJUGZRTMLJYHE3DKLJUGBSTMOLCMJSGMYRSG4======"
    email = "guilherme.souza@datavoxx"
    senha = "Tii@2025"

    main_loop(segredo, email, senha)