from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
import time
import undetected_chromedriver as uc

# detecta sua versão instalada automaticamente
chrome_version = "140.0.7339.208"  # seu Chrome atual

driver = uc.Chrome(version_main=140)  # principal = 140

CNPJ_TESTE = "59.270.512/0001-10"  # Placeholder de teste
LOGIN_PLACEHOLDER = "46448914870"

def iniciar_driver():
    """
    Inicia um Chrome "menos detectável" via undetected-chromedriver.
    Substitua pelo seu fluxo normal. Mantém o navegador aberto (detach não funciona com uc), então
    o script vai manter a janela enquanto o processo existir.
    """
    chrome_options = Options()
    chrome_options.add_argument("--start-maximized")
    # sugestões úteis (opcionais):
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    # user-agent simples (troque se quiser)
    chrome_options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
    )

    # Inicia o undetected chromedriver (ele resolve driver automaticamente)
    driver = uc.Chrome(options=chrome_options)
    # reduz a chance de navigator.webdriver = true (uc já tenta cuidar disso)
    driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    return driver

def acessar_site(driver, url):
    """Acessa o site indicado."""
    driver.get(url)
    print(f"Acessando: {url}")


def inserir_cnpj_e_buscar(driver, cnpj):
    """Insere o CNPJ no campo e realiza a busca."""
    try:
        campo_cnpj = WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.ID, "ctl00_cphContent_frmBuscaSimples_txtPalavraChave"))
        )
        campo_cnpj.clear()
        campo_cnpj.send_keys(cnpj)
        campo_cnpj.send_keys(Keys.ENTER)
        print(f"CNPJ '{cnpj}' inserido e busca iniciada.")
    except Exception as e:
        print("Erro ao inserir o CNPJ ou iniciar a busca:", e)


def aguardar_resultados_e_extrair(driver):
    """Aguarda os resultados e retorna os elementos NIRE encontrados."""
    try:
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.ID, "ctl00_cphContent_gdvResultadoBusca_gdvContent"))
        )
        tabela = driver.find_element(By.ID, "ctl00_cphContent_gdvResultadoBusca_gdvContent")
        linhas = tabela.find_elements(By.CSS_SELECTOR, "tbody > tr")

        if not linhas:
            print("Nenhum resultado encontrado.")
            return []

        # Captura os links clicáveis de NIRE
        links_nire = []
        for tr in linhas:
            try:
                link = tr.find_element(By.TAG_NAME, "a")
                links_nire.append(link)
            except:
                continue

        print(f"Encontrados {len(links_nire)} NIRE(s).")
        return links_nire

    except Exception as e:
        print("Erro ao capturar os resultados:", e)
        return []


def clicar_primeiro_nire(driver, links_nire):
    """Clica no primeiro NIRE encontrado."""
    try:
        if links_nire:
            primeiro = links_nire[0]
            print(f"Clicando no NIRE: {primeiro.text}")
            primeiro.click()
            WebDriverWait(driver, 10).until(EC.url_contains("Pre_Visualiza.aspx"))
            print("Redirecionado para a página de pré-visualização.")
        else:
            print("Nenhum NIRE disponível para clicar.")
    except Exception as e:
        print("Erro ao clicar no NIRE:", e)


def selecionar_opcao_e_confirmar(driver):
    """Seleciona a opção desejada e clica em OK."""
    try:
        # rola até o final da página para garantir visibilidade
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(1)

        # seleciona o radio com ID fixo
        opcao = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.ID, "ctl00_cphContent_frmPreVisualiza_rblTipoDocumento_3"))
        )
        opcao.click()
        print("Opção 'Cópia Digitalizada de Documentos Arquivados...' selecionada.")

        # clica no botão OK
        botao_ok = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.ID, "ctl00_cphContent_frmPreVisualiza_btnEmitir"))
        )
        botao_ok.click()
        print("Botão 'OK' clicado com sucesso.")
    except Exception as e:
        print("Erro ao selecionar a opção ou clicar em OK:", e)

def clicar_login_govbr(driver):
    """Clica no botão 'Entrar com govBR' e aguarda a página de login."""
    try:
        botao_govbr = WebDriverWait(driver, 15).until(
            EC.element_to_be_clickable((By.ID, "ctl00_cphContent_btLoginGovBR"))
        )
        botao_govbr.click()
        print("Botão 'Entrar com govBR' clicado, aguardando página de login...")
    except Exception as e:
        print("Erro ao clicar no botão govBR:", e)

def inserir_cpf_e_continuar(driver, login):
    """Insere o CPF no campo e clica no botão Continuar."""
    try:
        campo_cpf = WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.ID, "accountId"))
        )
        campo_cpf.clear()
        campo_cpf.send_keys(login)
        print(f"CPF/LOGIN '{login}' inserido.")

        botao_continuar = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.ID, "enter-account-id"))
        )
        botao_continuar.click()
        print("Botão 'Continuar' clicado com sucesso.")
    except Exception as e:
        print("Erro ao inserir CPF ou clicar em Continuar:", e)

def main():
    url = "https://www.jucesponline.sp.gov.br/"  # URL real
    driver = iniciar_driver()
    acessar_site(driver, url)
    inserir_cnpj_e_buscar(driver, CNPJ_TESTE)
    links_nire = aguardar_resultados_e_extrair(driver)
    clicar_primeiro_nire(driver, links_nire)
    selecionar_opcao_e_confirmar(driver)
    clicar_login_govbr(driver)
    inserir_cpf_e_continuar(driver, LOGIN_PLACEHOLDER)
    print("Processo completo. Navegador permanece aberto para inspeção.")


if __name__ == "__main__":
    main()
