"""Captura de quadros das câmeras.

Cada câmera vira uma ``Fonte`` com um único método, ``ler()``, que devolve o
quadro mais recente em BGR (``numpy.ndarray``) ou ``None`` se falhar.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from urllib.parse import urljoin

import cv2
import httpx
import numpy as np

from .config import CameraConfig

log = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) monitoramento-veicular/0.1"
EXTENSOES_STREAM = (".m3u8", ".mjpg", ".mjpeg", ".cgi", "rtsp://", "/video", "mjpg")


def decodificar(conteudo: bytes) -> np.ndarray | None:
    arr = np.frombuffer(conteudo, dtype=np.uint8)
    return cv2.imdecode(arr, cv2.IMREAD_COLOR) if arr.size else None


class Fonte:
    def __init__(self, cam: CameraConfig):
        self.cam = cam

    def ler(self) -> np.ndarray | None:  # pragma: no cover - interface
        raise NotImplementedError

    def fechar(self) -> None:
        pass


class FonteSnapshot(Fonte):
    """URL que devolve uma imagem nova a cada GET (o caso mais comum em portais municipais)."""

    def __init__(self, cam: CameraConfig, url: str | None = None):
        super().__init__(cam)
        self.url = url or cam.url
        self._http = httpx.Client(timeout=15, follow_redirects=True,
                                  headers={"User-Agent": USER_AGENT, **cam.headers})

    def ler(self) -> np.ndarray | None:
        # Parâmetro anti-cache: alguns servidores devolvem a mesma imagem sem ele.
        sep = "&" if "?" in self.url else "?"
        try:
            r = self._http.get(f"{self.url}{sep}_t={int(time.time() * 1000)}")
            r.raise_for_status()
        except httpx.HTTPError as e:
            log.warning("[%s] falha ao baixar snapshot: %s", self.cam.id, e)
            return None
        return decodificar(r.content)

    def fechar(self) -> None:
        self._http.close()


class FonteStream(Fonte):
    """MJPEG / HLS / RTSP via OpenCV (FFmpeg). Uma thread lê sem parar e guarda o último quadro,
    para que o processamento nunca trabalhe com quadros atrasados no buffer."""

    def __init__(self, cam: CameraConfig, url: str | None = None):
        super().__init__(cam)
        self.url = url or cam.url
        self._quadro: np.ndarray | None = None
        self._lock = threading.Lock()
        self._parar = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True, name=f"stream-{cam.id}")
        self._thread.start()

    def _loop(self) -> None:
        while not self._parar.is_set():
            cap = cv2.VideoCapture(self.url)
            if not cap.isOpened():
                log.warning("[%s] não abriu o stream; nova tentativa em 10 s", self.cam.id)
                self._parar.wait(10)
                continue
            while not self._parar.is_set():
                ok, quadro = cap.read()
                if not ok:
                    log.warning("[%s] stream caiu; reconectando", self.cam.id)
                    break
                with self._lock:
                    self._quadro = quadro
            cap.release()
            self._parar.wait(2)

    def ler(self) -> np.ndarray | None:
        with self._lock:
            return None if self._quadro is None else self._quadro.copy()

    def fechar(self) -> None:
        self._parar.set()


def descobrir_midia(html: str, base: str) -> list[str]:
    """Encontra URLs de imagem/vídeo de câmera numa página HTML (img, iframe, video, source,
    e URLs .m3u8/.mjpg soltas no JavaScript)."""
    from bs4 import BeautifulSoup

    sopa = BeautifulSoup(html, "html.parser")
    achados: list[str] = []
    for tag, attr in (("video", "src"), ("source", "src"), ("img", "src"), ("iframe", "src"),
                      ("img", "data-src"), ("embed", "src")):
        for el in sopa.find_all(tag):
            v = el.get(attr)
            if v and not v.startswith("data:"):
                achados.append(urljoin(base, v))
    for m in re.finditer(r"""["']((?:https?:)?//[^"']+?\.(?:m3u8|mjpe?g|jpg)(?:\?[^"']*)?)["']""", html):
        achados.append(urljoin(base, m.group(1)))
    vistos, saida = set(), []
    for u in achados:
        if u not in vistos:
            vistos.add(u)
            saida.append(u)
    return saida


def rotulos_midia(html: str, base: str) -> dict[str, str]:
    """Nome legível de cada mídia da página: a legenda do cartão que a contém (no portalimo.cl,
    ``<div class="cam-card"><img ...><p class="cam-caption">Puente San Pablo</p></div>``)
    ou, na falta dela, o ``alt``/``title`` da própria tag."""
    from bs4 import BeautifulSoup

    sopa = BeautifulSoup(html, "html.parser")
    nomes: dict[str, str] = {}
    for el in sopa.find_all(["img", "iframe", "video", "source", "embed"]):
        src = el.get("src") or el.get("data-src")
        if not src or src.startswith("data:"):
            continue
        nome, pai = "", el.parent
        for _ in range(3):  # sobe até achar um bloco com texto curto (a legenda)
            # Parar em blocos com outras mídias: a legenda deles não é desta câmera.
            if pai is None or len(pai.find_all(["img", "iframe", "video", "embed"])) > 1:
                break
            # Uma legenda explícita vence o texto do bloco (que pode ter selos como "EN VIVO").
            legenda = pai.find("figcaption") or pai.find(class_=re.compile(r"caption|legend|titul|title", re.I))
            texto = (legenda or pai).get_text(" ", strip=True)
            if texto:
                if len(texto) <= 80:
                    nome = texto
                break
            pai = pai.parent
        nome = nome or el.get("alt") or el.get("title") or ""
        nomes.setdefault(urljoin(base, src), " ".join(nome.split()))
    return nomes


def _parece_camera(url: str) -> bool:
    u = url.lower()
    if any(x in u for x in ("logo", "icon", "banner", "favicon", ".svg", ".gif")):
        return False
    return any(x in u for x in EXTENSOES_STREAM) or any(
        x in u for x in ("cam", "snapshot", "image.jpg", "jpg", "stream"))


def criar_fonte(cam: CameraConfig) -> Fonte:
    if cam.tipo == "stream":
        return FonteStream(cam)
    if cam.tipo == "pagina":
        r = httpx.get(cam.url, timeout=20, follow_redirects=True,
                      headers={"User-Agent": USER_AGENT, **cam.headers})
        candidatos = [u for u in descobrir_midia(r.text, str(r.url)) if _parece_camera(u)]
        if not candidatos:
            raise RuntimeError(f"nenhuma mídia de câmera encontrada em {cam.url}")
        alvo = candidatos[0]
        log.info("[%s] mídia descoberta: %s", cam.id, alvo)
        if any(x in alvo.lower() for x in EXTENSOES_STREAM):
            return FonteStream(cam, alvo)
        return FonteSnapshot(cam, alvo)
    return FonteSnapshot(cam)
