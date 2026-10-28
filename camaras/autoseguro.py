"""Consulta de placas no Autoseguro (https://www.autoseguro.gob.cl/).

O Autoseguro informa se um veículo tem encargo por roubo vigente. O site é
pensado para consulta humana, então a integração é *assistida*:

* Uma única thread controla um navegador Chromium **visível** (Playwright).
* Para cada placa da fila ela abre o site, escolhe "Por placa patente" e
  preenche a placa.
* Se houver captcha, a consulta fica em ``aguardando_captcha`` até o operador
  resolvê-lo nessa janela. A plataforma não tenta burlar o captcha.
* Há intervalo mínimo entre consultas e cache por placa, para não
  sobrecarregar o serviço.

Os seletores e as palavras que classificam o resultado ficam no
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

        resultado = pagina.locator(s["resultado"]).first
        texto_antes = resultado.inner_text() if resultado.count() else ""

        clicou = False
        if pagina.locator(s["captcha"]).count():
            self.banco.salvar_consulta(placa, "aguardando_captcha",
                                       "Resolva o captcha e clique em buscar na janela do navegador.")
            log.info("[%s] aguardando o operador resolver o captcha", placa)
        else:
            pagina.locator(s["botao_buscar"]).first.click()
            clicou = True

        limite = time.time() + self.cfg.timeout_captcha_s
        while time.time() < limite:
            if resultado.count():
                texto = resultado.inner_text().strip()
                if texto and texto != texto_antes:
                    break
            # Captcha resolvido mas o operador não clicou em buscar: clicamos por ele.
            token = pagina.locator("textarea[name='g-recaptcha-response'], textarea[name='h-captcha-response']")
            if not clicou and token.count() and token.first.input_value():
                pagina.locator(s["botao_buscar"]).first.click()
                clicou = True
            pagina.wait_for_timeout(1000)
        else:
            return "erro", "tempo esgotado esperando o resultado"

        texto = resultado.inner_text().strip()
        pagina.screenshot(path=str(self.pasta / f"{placa}.png"), full_page=True)
        return classificar(texto, self.cfg), texto[:2000]
