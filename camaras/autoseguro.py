"""Consulta de placas no Autoseguro (https://www.autoseguro.gob.cl/).

O Autoseguro informa se um veículo tem encargo por roubo vigente. O site é
pensado para consulta humana, então a integração é *assistida*:

* Uma única thread controla um navegador Chromium **visível** (Playwright).
* Para cada placa da fila ela abre o site e preenche "Por placa patente".
* O site exige reCAPTCHA: a consulta fica em ``aguardando_captcha`` até o
  operador resolvê-lo nessa janela. Só depois disso a plataforma envia o
  formulário. Ela não tenta burlar o captcha.
* O site devolve o resultado como JSON embutido na página
  (``var IsResponse = true; var response = {...}``). Lemos esse objeto e o
  classificamos com a mesma regra do JavaScript do site (``Inicio.js``); o
  texto da janela de resultado só é usado se o objeto não puder ser lido.
* Há intervalo mínimo entre consultas e cache por placa, para não
  sobrecarregar o serviço.

Os seletores e as palavras da classificação por texto ficam no
``config.yaml`` (seção ``autoseguro``), para ajustar sem mexer no código caso o
site mude.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from pathlib import Path
from urllib.parse import quote

from .config import AutoseguroConfig
from .storage import STATUS_FINAIS, Banco

log = logging.getLogger(__name__)


def classificar(texto: str, cfg: AutoseguroConfig) -> str:
    """Classifica o texto do resultado em ``encargo``, ``sem_encargo`` ou ``indeterminado``.

    As frases negativas são verificadas primeiro porque contêm as positivas
    ("no presenta encargo" contém "presenta encargo").
    """
    t = " ".join(texto.lower().split())
    if any(p in t for p in cfg.palavras_sem_encargo):
        return "sem_encargo"
    if any(p in t for p in cfg.palavras_encargo):
        return "encargo"
    return "indeterminado"


def _avaliar_especies(especies: list[dict]) -> tuple[str, list[str]]:
    """Porta de ``evaluarConsultaVehiculoDesdeEspecies`` do Inicio.js.

    Cada "especie" é uma peça do veículo com encargo (1 placa dianteira, 2 placa
    traseira, 3 VIN/veículo, 4 motor); ``FechaSolucionEspecie`` preenchida
    significa que a peça foi recuperada. Devolve o resultado como o site o
    mostra e as peças ainda pendentes.
    """
    nomes = {1: "placa dianteira", 2: "placa traseira", 3: "veículo (VIN)", 4: "motor"}
    achada = {t: False for t in nomes}
    vin_vigente = False
    for e in especies:
        tipo = e.get("TipoEspecie")
        recuperada = e.get("FechaSolucionEspecie") is not None
        if tipo in achada:
            achada[tipo] = achada[tipo] or recuperada
        if tipo == 3 and not recuperada:
            vin_vigente = True
    if vin_vigente or not achada[3]:
        return "Encargo policial vigente", [nomes[3]]
    faltam = [nomes[t] for t in (1, 2, 4) if not achada[t]]
    return ("Encargo parcial", faltam) if faltam else ("Sin encargo", [])


def classificar_resposta(resp: dict) -> tuple[str, str]:
    """Classifica o objeto ``response`` que o Autoseguro embute na página de resultado.

    Segue o Inicio.js: ``Cod`` 0 é sucesso, 20 é limite diário atingido e 1 é
    erro com mensagem em ``Desc``. Com sucesso, só há encargo quando
    ``CodTipoEncargo == 1`` e existem peças (``Especies``) com ``EUN != 0``.
    """
    cod = resp.get("Cod")
    if cod == 20:
        return "erro", "Autoseguro: limite diário de consultas atingido"
    if cod != 0:
        return "erro", f"Autoseguro: {resp.get('Desc') or f'código {cod}'}"
    r = resp.get("Result") or {}
    especies = r.get("Especies") or []
    if r.get("CodTipoEncargo") != 1 or not especies or r.get("EUN") == 0:
        return "sem_encargo", "Sin encargo"
    resultado, faltam = _avaliar_especies(especies)
    pecas = "; ".join(
        f"{e.get('Descripcion', '?')}: {'recuperada' if e.get('FechaSolucionEspecie') else 'pendente'}"
        for e in especies)
    if resultado == "Sin encargo":
        return "sem_encargo", f"Sin encargo (todas as peças recuperadas). {pecas}"
    if resultado == "Encargo parcial":
        return "encargo", f"Encargo parcial, pendente: {', '.join(faltam)}. {pecas}"
    return "encargo", f"Encargo policial vigente. {pecas}"


# Lê o resultado embutido; null enquanto a página ainda é o formulário vazio.
_JS_RESPOSTA = "() => (typeof IsResponse !== 'undefined' && IsResponse && typeof response === 'object') ? response : null"


def link_manual(placa: str, cfg: AutoseguroConfig) -> str:
    """Link para o operador consultar no navegador dele (o site não aceita a placa pela URL,
    então a placa vai só como referência no fragmento)."""
    return f"{cfg.url}#placa={quote(placa)}"


class ConsultorAutoseguro:
    def __init__(self, cfg: AutoseguroConfig, banco: Banco, pasta_evidencias: str | Path):
        self.cfg = cfg
        self.banco = banco
        self.pasta = Path(pasta_evidencias)
        self.pasta.mkdir(parents=True, exist_ok=True)
        self._fila: queue.Queue[str] = queue.Queue()
        self._enfileiradas: set[str] = set()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._parar = threading.Event()

    # API pública ---------------------------------------------------------
    def iniciar(self) -> None:
        if self.cfg.ativo and self.cfg.modo == "assistido" and self._thread is None:
            self._thread = threading.Thread(target=self._loop, daemon=True, name="autoseguro")
            self._thread.start()

    def parar(self) -> None:
        self._parar.set()

    def precisa_consultar(self, placa: str) -> bool:
        c = self.banco.consulta(placa)
        if c is None:
            return True
        if c["status"] in STATUS_FINAIS:
            return time.time() - c["atualizado_em"] > self.cfg.cache_horas * 3600
        return c["status"] == "erro"

    def enfileirar(self, placa: str, forcar: bool = False) -> str:
        """Coloca a placa na fila. Devolve o status atual da consulta."""
        if not self.cfg.ativo:
            return "desativado"
        if self.cfg.modo != "assistido":
            return "manual"
        with self._lock:
            if placa in self._enfileiradas:
                return "pendente"
            if not forcar and not self.precisa_consultar(placa):
                return self.banco.consulta(placa)["status"]
            self._enfileiradas.add(placa)
        self.banco.salvar_consulta(placa, "pendente")
        self._fila.put(placa)
        return "pendente"

    # Worker ----------------------------------------------------------------
    def _loop(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            log.error("Playwright não instalado: pip install playwright && playwright install chromium")
            return
        with sync_playwright() as pw:
            navegador = pw.chromium.launch(headless=self.cfg.headless,
                                           executable_path=self.cfg.executable_path or None)
            pagina = navegador.new_page(locale="es-CL")
            ultima = 0.0
            while not self._parar.is_set():
                try:
                    placa = self._fila.get(timeout=1)
                except queue.Empty:
                    continue
                espera = self.cfg.intervalo_min_s - (time.time() - ultima)
                if espera > 0:
                    time.sleep(espera)
                try:
                    status, detalhe = self._consultar(pagina, placa)
                except Exception as e:  # noqa: BLE001 - qualquer falha do navegador vira "erro"
                    log.exception("falha ao consultar %s", placa)
                    status, detalhe = "erro", str(e)[:500]
                self.banco.salvar_consulta(placa, status, detalhe)
                ultima = time.time()
                with self._lock:
                    self._enfileiradas.discard(placa)
            navegador.close()

    def _consultar(self, pagina, placa: str) -> tuple[str, str]:
        s = self.cfg.seletores
        self.banco.salvar_consulta(placa, "em_andamento")
        pagina.goto(self.cfg.url, wait_until="domcontentloaded", timeout=60_000)

        aba = pagina.locator(s["aba_placa"]).first
        if aba.count():
            aba.click()
        campo = pagina.locator(s["campo_placa"]).first
        campo.wait_for(state="visible", timeout=20_000)
        campo.fill(placa)

        enviou = False
        if pagina.locator(s["captcha"]).count():
            self.banco.salvar_consulta(placa, "aguardando_captcha",
                                       "Resolva o captcha na janela do navegador; a busca é enviada em seguida.")
            log.info("[%s] aguardando o operador resolver o captcha", placa)
        else:
            pagina.locator(s["botao_buscar"]).first.click()
            enviou = True

        resultado = pagina.locator(s["resultado"]).first
        resposta, texto = None, ""
        limite = time.time() + self.cfg.timeout_captcha_s
        while time.time() < limite:
            try:
                resposta = pagina.evaluate(_JS_RESPOSTA)
                if resposta is not None:
                    break
                # Se o site mudar o nome das variáveis, ainda dá para ler a janela de resultado.
                if enviou and resultado.count() and resultado.is_visible():
                    texto = resultado.inner_text().strip()
                    if texto:
                        break
                # Captcha resolvido pelo operador: enviamos o formulário por ele.
                token = pagina.locator("textarea[name='g-recaptcha-response'], textarea[name='h-captcha-response']")
                if not enviou and token.count() and token.first.input_value():
                    if campo.input_value() != placa:  # o operador pode ter apagado o campo
                        campo.fill(placa)
                    pagina.locator(s["botao_buscar"]).first.click()
                    enviou = True
            except Exception as e:  # noqa: BLE001 - a página está navegando entre as leituras
                log.debug("[%s] página em transição: %s", placa, e)
            pagina.wait_for_timeout(1000)
        else:
            return "erro", "tempo esgotado esperando o captcha/resultado"

        if isinstance(resposta, dict):
            try:  # deixa a janela de resultado abrir para a captura de tela
                resultado.wait_for(state="visible", timeout=5_000)
            except Exception:  # noqa: BLE001 - a captura é só evidência; o JSON já basta
                pass
        pagina.screenshot(path=str(self.pasta / f"{placa}.png"), full_page=True)
        if isinstance(resposta, dict):
            return classificar_resposta(resposta)
        return classificar(texto, self.cfg), texto[:2000]
