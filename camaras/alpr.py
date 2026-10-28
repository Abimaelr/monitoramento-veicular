"""Reconhecimento automático de placas (ALPR).

Pipeline em dois estágios, ambos em ONNX (rodam em CPU, sem PyTorch):

1. Detecção da placa no quadro com YOLOv9 (``open-image-models``).
2. OCR do recorte da placa (``fast-plate-ocr``).

A leitura passa por :func:`camaras.plates.normalizar`, que corrige confusões
típicas do OCR conforme o formato chileno e descarta o que não é placa.
"""

from __future__ import annotations

import logging
import statistics
import threading
from dataclasses import dataclass

import numpy as np

from .config import AlprConfig
from .plates import PlacaNormalizada, normalizar

log = logging.getLogger(__name__)


@dataclass
class Leitura:
    placa: PlacaNormalizada
    leitura_ocr: str
    confianca_deteccao: float
    confianca_ocr: float
    bbox: tuple[int, int, int, int]  # x1, y1, x2, y2

    def recortar(self, quadro: np.ndarray, margem: int = 6) -> np.ndarray:
        x1, y1, x2, y2 = self.bbox
        h, w = quadro.shape[:2]
        return quadro[max(0, y1 - margem):min(h, y2 + margem), max(0, x1 - margem):min(w, x2 + margem)]


def confianca_caracteres(texto: str, confianca) -> float:
    """Média da confiança só dos caracteres lidos. O OCR completa a placa com ``_`` até o
    tamanho máximo, e essas posições vazias têm confiança alta que mascarava leituras ruins."""
    if not isinstance(confianca, (list, tuple, np.ndarray)):
        return float(confianca)
    valores = [c for ch, c in zip(texto, confianca) if ch != "_"]
    return float(statistics.mean(valores)) if valores else 0.0


class LeitorPlacas:
    def __init__(self, cfg: AlprConfig):
        from fast_alpr import ALPR

        self.cfg = cfg
        self._alpr = ALPR(detector_model=cfg.detector_model, ocr_model=cfg.ocr_model,
                          detector_conf_thresh=cfg.confianca_deteccao)
        # As sessões ONNX não são compartilhadas com segurança entre várias threads de câmera.
        self._lock = threading.Lock()

    def ler(self, quadro: np.ndarray) -> list[Leitura]:
        with self._lock:
            resultados = self._alpr.predict(quadro)
        leituras = []
        for r in resultados:
            if r.ocr is None:
                continue
            texto = r.ocr.text.replace("_", "")
            conf_ocr = confianca_caracteres(r.ocr.text, r.ocr.confidence)
            placa = normalizar(texto)
            if placa is None or conf_ocr < self.cfg.confianca_ocr:
                log.debug("descartado: %r (conf %.2f)", texto, conf_ocr)
                continue
            b = r.detection.bounding_box
            leituras.append(Leitura(placa, texto, float(r.detection.confidence), conf_ocr,
                                    (int(b.x1), int(b.y1), int(b.x2), int(b.y2))))
        return leituras


def anotar(quadro: np.ndarray, leituras: list[Leitura]) -> np.ndarray:
    """Desenha as caixas e as placas lidas sobre uma cópia do quadro."""
    import cv2

    img = quadro.copy()
    for lt in leituras:
        x1, y1, x2, y2 = lt.bbox
        cv2.rectangle(img, (x1, y1), (x2, y2), (0, 200, 255), 2)
        rotulo = f"{lt.placa.exibicao} {lt.confianca_ocr:.0%}"
        cv2.putText(img, rotulo, (x1, max(18, y1 - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 4)
        cv2.putText(img, rotulo, (x1, max(18, y1 - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 200, 255), 2)
    return img
