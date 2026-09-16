try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass
import os
import time
import pyotp
import requests
import logging
import re
import unicodedata
from typing import Optional, Dict, List, Tuple
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, JavascriptException
import gspread
from typing import Dict, List, Tuple

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

# Mapeamento CONSULTOR -> ID da planilha do consultor
consultores_planilhas = {
    "TESTE": "1hcEM77mebuhES0JX_UFju8dAQ44159x7a1OujH2bnys",
    #"CAROLINA": "1ZfShgR66DEWDnXeg8yzvYmOHIf3mBQvgGC-WkEMRCmA",

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

STATUS_IGNORAR = {
    "CANCELADO",
    "ATIVADO 100%",

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

def strip_accents_upper(s: str) -> str:
    if s is None:
        return ""
    s = str(s).strip()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.category(ch).startswith("M"))
    return s.upper()

def normalize_status_sheet(s: str) -> str:
    # STATUS da planilha: só remover acentos/caixa/espaços duplicados
    s = strip_accents_upper(s)
    s = " ".join(s.split())
    return s

def normalize_status_from_produto(produto_raw: str) -> str:
    """
    Regra pedida:
      - pegar apenas o que está APÓS o primeiro '-' (se houver)
      - remover qualquer conteúdo entre parênteses '()' onde quer que esteja
      - trim e uppercase, sem acentos
    Ex.: "MV - ATIVADO 100% (NEOCRM)" -> "ATIVADO 100%"
    """
    if not produto_raw:
        return ""
    txt = str(produto_raw)
    # pega parte após '-'
    if "-" in txt:
      txt = txt.split("-", 1)[1]
    # remove conteúdo entre parênteses em qualquer ponto
    import re
    txt = re.sub(r"\([^)]*\)", "", txt)
    txt = strip_accents_upper(txt).strip()
    txt = " ".join(txt.split())
    return txt

def parse_month_from_col10(dt_str: str) -> int:
    """
    Extrai o mês (2º número entre barras) de strings tipo '21/10/2025 18:03:56'.
    Retorna um int (1..12). Se falhar, retorna 0.
    Faz prints de auditoria mostrando o valor bruto e o mês extraído.
    """
    if not dt_str:
        print("[DEBUG parse_month_from_col10] valor vazio ou None -- retorna 0")
        return 0
    try:
        # imprime valor bruto para auditoria
        print(f"[DEBUG parse_month_from_col10] bruto='{dt_str}'")

        partes = dt_str.split("/")
        if len(partes) < 2:
            print("[DEBUG parse_month_from_col10] formato inesperado -- retorna 0")
            return 0

        mes_str = partes[1]
        # caso venha algo tipo '10/2025 18:03:56', corta em espaços ou barras adicionais
        mes_str = re.split(r"[\s/]", mes_str)[0]
        mes_int = int(re.sub(r"\D", "", mes_str)) if mes_str.isdigit() else int(mes_str)
        print(f"[DEBUG parse_month_from_col10] mês extraído -> {mes_int}")
        return mes_int
    except Exception as e:
        print(f"[DEBUG parse_month_from_col10] erro ao extrair mês de '{dt_str}': {e}")
        return 0

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
    Se houver múltiplas colunas com o mesmo nome (ex: STATUS),
    retorna a segunda ocorrência, se existir.
    """
    try:
        headers = ws.row_values(header_row)
        target_norm = normalize_header_name(header_name)

        def clean_text(h):
            # remove tudo que não for letra ou número
            return re.sub(r"[^A-Z0-9]", "", normalize_header_name(str(h)))

        target_clean = clean_text(header_name)
        matches = [i for i, h in enumerate(headers, start=1)
                   if clean_text(h) == target_clean]

        if not matches:
            # fallback permissivo: substring
            for i, h in enumerate(headers, start=1):
                if target_clean in clean_text(h):
                    matches.append(i)

        if not matches:
            print(f"[DEBUG HEADER PICK] '{header_name}' não encontrado. Headers disponíveis: {headers}")
            return None

        # Se houver 2 ou mais ocorrências, usar a segunda
        if len(matches) >= 2:
            chosen = matches[1]
        else:
            chosen = matches[0]

        print(f"[DEBUG HEADER PICK] '{header_name}' encontrado nas colunas {matches} - usando {chosen}")
        return chosen

    except Exception as e:
        print(f"[DEBUG find_column_index_by_header] erro: {e}")
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
  collectButtons(document, 'vaadin-button[title="Abrir atividade"]', btns);

  function safeText(el){ return (el ? (el.innerText || el.textContent || '') : '').trim(); }

  var pairs = [];
  for (var i=0;i<btns.length;i++){
    var b = btns[i];
    var anc = b;
    while (anc && anc.tagName && anc.tagName.toLowerCase() !== 'vaadin-grid-cell-content'){
      anc = anc.parentNode;
    }
    if (!anc) continue;

    var sib = anc.nextElementSibling;
    var arr = [];
    while (sib){
      arr.push(sib);
      sib = sib.nextElementSibling;
    }

    var col1  = safeText(arr[0]  || null); // numeroAtividade
    var col6  = safeText(arr[5]  || null); // produto bruto (para normalizar como STATUS)
    var col10 = safeText(arr[9]  || null); // data/hora dd/mm/yyyy HH:MM:SS

    pairs.push({ col1: col1, col6: col6, col10: col10 });
  }
  return pairs;
})();
"""


def fetch_sweep_mappings(driver, cnpj: str,
                         max_steps: int = 200,
                         step_px: int = 300,
                         sleep_after_vertical: float = 1.5,
                         sleep_between_steps: float = 0.5) -> List[Dict[str, str]]:
    try:
        buscar_atividade_cnpj(driver, cnpj)
    except Exception as e:
        logging.exception(f"[SWEEP] Falha ao executar buscar_atividade_cnpj para {cnpj}: {e}")

    time.sleep(1.5)

    try:
        tbl = driver.execute_script(_FIND_TABLE_JS)
        if tbl:
            logging.info("[SWEEP] table encontrada via JS shadow-aware.")
        else:
            logging.warning("[SWEEP] table NÃO encontrada via JS (vai tentar coleta global).")
    except JavascriptException as e:
        logging.exception(f"[SWEEP] JS find table falhou: {e}")
        tbl = None

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
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            logging.info("[SWEEP] Rolado window (sem table).")
    except Exception as e:
        logging.warning(f"[SWEEP] Falha ao rolar verticalmente: {e}")

    time.sleep(sleep_after_vertical)

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
    else:
        try:
            scroll_container = driver.find_element(By.TAG_NAME, "body")
        except Exception:
            scroll_container = None

    for step in range(max_steps):
        try:
            pairs = driver.execute_script(_GET_VISIBLE_PAIRS_JS)
        except Exception as e:
            logging.warning(f"[SWEEP] Falha JS coletando pares (step {step}): {e}")
            pairs = []

        for p in pairs:
            col1  = (p.get("col1") or "").strip()
            col6  = (p.get("col6") or "").strip()
            col10 = (p.get("col10") or "").strip()

            key = f"{col1}|{col6}|{col10}"
            if key in seen:
                continue
            seen.add(key)

            normalized_status = normalize_status_from_produto(col6)
            mes = parse_month_from_col10(col10)

            ordered.append({
                "numeroAtividade": col1,
                "rawStatusProduto": col6,
                "statusNeoNormalizado": normalized_status,
                "dataHora": col10,
                "mes": mes
            })

        try:
            if scroll_container:
                driver.execute_script("arguments[0].scrollLeft += arguments[1];", scroll_container, step_px)
            elif tbl:
                driver.execute_script("arguments[0].scrollLeft += arguments[1];", tbl, step_px)
            else:
                break
        except Exception as e:
            logging.warning(f"[SWEEP] Falha ao mover scrollLeft (step {step}): {e}")
            break

        try:
            if scroll_container:
                cur_left = driver.execute_script("return arguments[0].scrollLeft;", scroll_container) or 0
                sw = driver.execute_script("return arguments[0].scrollWidth || 0;", scroll_container) or 0
                cw = driver.execute_script("return arguments[0].clientWidth || 0;", scroll_container) or 0
                if sw and cw and cur_left + cw >= sw - 5:
                    logging.info("[SWEEP] Detectado fim do scroll horizontal.")
                    break
        except Exception:
            pass

        time.sleep(sleep_between_steps)

    logging.info(f"[SWEEP] Sweep finalizado. Itens coletados: {len(ordered)}")
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

# -------------------------
# Fluxo integrado por CNPJ (adaptado)
# -------------------------
def processar_planilha_consultor_por_cnpj(driver, ws_consultor):
    """
    Agora otimizada:
      - lê apenas CNPJ/CPF, STATUS, NUMERO PEDIDO
      - ignora linhas com STATUS ∈ STATUS_IGNORAR
      - ignora linhas já preenchidas em NUMERO PEDIDO
      - faz 1 sweep por CNPJ
      - recarrega a página do NEO após cada sweep
      - aplica batch update no final
    """

    headers = ws_consultor.row_values(1)
    if not headers:
        logging.warning("[CONSULTOR] Aba vazia ou sem headers.")
        return

    print("\n================ HEADERS DETECTADOS =================")
    for i, h in enumerate(headers, start=1):
        print(f"Coluna {i}: '{h}'")
    print("=====================================================\n")

    def find_col_idx(name):
        idx = find_column_index_by_header(ws_consultor, name, header_row=1)
        if not idx:
            raise RuntimeError(f"[CONSULTOR] Coluna obrigatória '{name}' não encontrada.")
        return idx

    col_cnpj = find_col_idx("CNPJ/CPF")
    col_status = find_col_idx("STATUS")
    col_num_pedido = find_column_index_by_header(ws_consultor, "NUMERO PEDIDO", header_row=1)

    if not col_num_pedido:
        ws_consultor.update_cell(1, len(headers) + 1, "NUMERO PEDIDO")
        col_num_pedido = len(headers) + 1
        headers.append("NUMERO PEDIDO")

    print(f"[DEBUG HEADERS] CNPJ/CPF={col_cnpj}, STATUS={col_status}, NUMERO_PEDIDO={col_num_pedido}")

    all_rows = ws_consultor.get_all_values()
    if len(all_rows) <= 1:
        logging.info("[CONSULTOR] Sem linhas de dados.")
        return

    from collections import defaultdict
    cnpj_to_rows = defaultdict(list)

    for r_idx, row in enumerate(all_rows[1:], start=2):  # dados começam na linha 2
        cnpj_val = normalize_cnpj(row[col_cnpj - 1] if col_cnpj - 1 < len(row) else "")
        if not cnpj_val:
            continue

        raw_status = row[col_status - 1] if col_status - 1 < len(row) else ""
        status_val = normalize_status_sheet(raw_status)

        # SE O STATUS ESTIVER NA LISTA DE IGNORADOS → PULA
        if status_val in STATUS_IGNORAR:
            print(f"[IGNORAR STATUS] Linha {r_idx} STATUS='{status_val}'")
            continue

        num_pedido_atual = str(row[col_num_pedido - 1] if col_num_pedido - 1 < len(row) else "").strip()
        # SE JÁ EXISTE NUMERO PEDIDO → PULA
        if num_pedido_atual:
            print(f"[IGNORAR JA PREENCHIDO] Linha {r_idx} NUMERO_PEDIDO='{num_pedido_atual}'")
            continue

        cnpj_to_rows[cnpj_val].append({
            "row_idx": r_idx,
            "status_sheet_norm": status_val
        })

    updates_data = []

    for cnpj, linhas in cnpj_to_rows.items():

        print(f"\n[PROCESSANDO CNPJ] {cnpj}")

        try:
            sweep = fetch_sweep_mappings(driver, cnpj)
        except Exception as e:
            logging.exception(f"[CONSULTOR] Falha no sweep para CNPJ {cnpj}: {e}")
            continue

        sweep_validos = []
        for item in sweep:
            mes = int(item.get("mes") or 0)
            if mes < 10:
                continue
            sweep_validos.append(item)

        for item in sweep_validos:
            numeroAtividade = item.get("numeroAtividade") or ""
            statusNeoNorm = normalize_status_sheet(item.get("statusNeoNormalizado") or "")

            if not numeroAtividade or not statusNeoNorm:
                continue

            print(f"[CAPTURA] col1='{numeroAtividade}' STATUS_NEO='{statusNeoNorm}'")

            for ln in linhas:
                if statusNeoNorm == ln["status_sheet_norm"]:
                    col_letter = col_idx_to_letter(col_num_pedido)
                    a1 = f"'{ws_consultor.title}'!{col_letter}{ln['row_idx']}"
                    updates_data.append({"range": a1, "values": [[numeroAtividade]]})
                    print(f"[MATCH] Linha {ln['row_idx']} -> NUMERO PEDIDO='{numeroAtividade}'")
                    break

        # **RECARREGAR A PÁGINA DO NEO ANTES DO PRÓXIMO CNPJ**
        driver.refresh()
        time.sleep(1.5)

    if updates_data:
        body = {"valueInputOption": "USER_ENTERED", "data": updates_data}
        try:
            ws_consultor.spreadsheet.values_batch_update(body)
            logging.info(f"[CONSULTOR] Batch aplicado com sucesso ({len(updates_data)} células).")
        except Exception as e:
            logging.exception(f"[CONSULTOR] Falha no batch update: {e}")
    else:
        logging.info("[CONSULTOR] Nenhum dado para atualizar.")

# -------------------------
# LOOP PRINCIPAL POR PLANILHAS DE CONSULTOR
def main_loop_por_planilhas(segredo_totp: str, email: str, senha: str):
    client = gsheet_client()

    while True:
        driver = None
        try:
            driver = login_neo(segredo_totp, email, senha, headless=False)

            # percorre cada planilha do consultor
            for consultor_nome, sheet_id in CONSULTOR_SHEETS.items():
                try:
                    ws = open_consultor_sheet_by_id(client, sheet_id)
                    if not ws:
                        logging.error(f"[MAIN] Não consegui abrir a planilha do consultor '{consultor_nome}' (id={sheet_id}).")
                        continue

                    logging.info(f"[MAIN] Processando planilha do consultor '{consultor_nome}'...")
                    processar_planilha_consultor_por_cnpj(driver, ws)
                    logging.info(f"[MAIN] Concluído para '{consultor_nome}'. Aguardando 5s para respeitar cota.")
                    time.sleep(5)
                except Exception as e:
                    logging.exception(f"[MAIN] Erro ao processar '{consultor_nome}': {e}")
                    # segue para a próxima planilha

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

    main_loop_por_planilhas(segredo, email, senha)
    