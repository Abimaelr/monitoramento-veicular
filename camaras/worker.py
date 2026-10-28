"""Uma thread por câmera: captura, roda o ALPR, grava as detecções e
encaminha as placas novas para o Autoseguro."""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from .alpr import LeitorPlacas, Leitura, anotar
from .autoseguro import ConsultorAutoseguro
from .config import CameraConfig, Config
from .sources import Fonte, criar_fonte
from .storage import Banco

log = logging.getLogger(__name__)


@dataclass
class EstadoCamera:
    online: bool = False
    erro: str = ""
    ultimo_quadro_em: float = 0.0
    jpeg: bytes | None = None
    leituras: list[Leitura] = field(default_factory=list)


class Camera:
    def __init__(self, cam: CameraConfig, monitor: "Monitor"):
        self.cfg = cam
        self.monitor = monitor
        self.estado = EstadoCamera()
        self._fonte: Fonte | None = None
        self._parar = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True, name=f"cam-{cam.id}")

    def iniciar(self) -> None:
        self._thread.start()

    def parar(self) -> None:
        self._parar.set()
        if self._fonte:
            self._fonte.fechar()

    def _loop(self) -> None:
        while not self._parar.is_set():
            if self._fonte is None:
                try:
                    self._fonte = criar_fonte(self.cfg)
                except Exception as e:  # noqa: BLE001
                    self.estado.online, self.estado.erro = False, str(e)
                    log.warning("[%s] %s", self.cfg.id, e)
                    self._parar.wait(30)
                    continue
            inicio = time.time()
            quadro = self._fonte.ler()
            if quadro is None:
                self.estado.online = False
                self.estado.erro = "sem imagem"
            else:
                self.processar(quadro)
            self._parar.wait(max(0.0, self.cfg.intervalo - (time.time() - inicio)))

    def processar(self, quadro: np.ndarray) -> list[Leitura]:
        leituras: list[Leitura] = []
        if self.cfg.alpr and self.monitor.leitor is not None:
            leituras = self.monitor.leitor.ler(quadro)
            for lt in leituras:
                self.monitor.registrar(self.cfg.id, quadro, lt)
        img = anotar(quadro, leituras) if leituras else quadro
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])
        self.estado.online, self.estado.erro = True, ""
        self.estado.ultimo_quadro_em = time.time()
        self.estado.leituras = leituras
        if ok:
            self.estado.jpeg = buf.tobytes()
        return leituras


class Monitor:
    def __init__(self, cfg: Config, leitor: LeitorPlacas | None = None,
                 banco: Banco | None = None, consultor: ConsultorAutoseguro | None = None):
        self.cfg = cfg
        self.banco = banco or Banco(cfg.banco)
        self.pasta = Path(cfg.capturas)
        self.pasta.mkdir(parents=True, exist_ok=True)
        if leitor is None and cfg.alpr.ativo:
            leitor = LeitorPlacas(cfg.alpr)
        self.leitor = leitor
        self.consultor = consultor or ConsultorAutoseguro(cfg.autoseguro, self.banco, self.pasta / "autoseguro")
        self.cameras = {c.id: Camera(c, self) for c in cfg.cameras if c.ativa}
        self._lock = threading.Lock()

    def iniciar(self) -> None:
        self.consultor.iniciar()
        for cam in self.cameras.values():
            cam.iniciar()

    def parar(self) -> None:
        self.consultor.parar()
        for cam in self.cameras.values():
            cam.parar()

    def registrar(self, camera_id: str, quadro: np.ndarray, lt: Leitura) -> int | None:
        """Grava a detecção, a menos que a mesma placa tenha sido vista na mesma câmera há pouco."""
        placa = lt.placa.texto
        with self._lock:
            ultima = self.banco.ultima_deteccao(placa, camera_id)
            if ultima and time.time() - ultima["criado_em"] < self.cfg.alpr.janela_duplicata_s:
                return None
            carimbo = time.strftime("%Y%m%d-%H%M%S")
            base = self.pasta / camera_id
            base.mkdir(parents=True, exist_ok=True)
            recorte = base / f"{carimbo}_{placa}_placa.jpg"
            cena = base / f"{carimbo}_{placa}.jpg"
            cv2.imwrite(str(recorte), lt.recortar(quadro))
            cv2.imwrite(str(cena), anotar(quadro, [lt]), [cv2.IMWRITE_JPEG_QUALITY, 85])
            det_id = self.banco.inserir_deteccao(
                placa=placa, leitura_ocr=lt.leitura_ocr, formato=lt.placa.formato, camera_id=camera_id,
                confianca_deteccao=lt.confianca_deteccao, confianca_ocr=lt.confianca_ocr,
                bbox=json.dumps(lt.bbox), recorte=str(recorte), quadro=str(cena))
        log.info("[%s] placa %s (OCR %.0f%%)", camera_id, lt.placa.exibicao, lt.confianca_ocr * 100)
        if self.cfg.autoseguro.automatico:
            self.consultor.enfileirar(placa)
        return det_id
