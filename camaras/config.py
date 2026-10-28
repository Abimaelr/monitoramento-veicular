"""Leitura do arquivo de configuração (YAML)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class CameraConfig:
    id: str
    nome: str
    url: str
    # snapshot: URL devolve uma imagem JPEG/PNG a cada requisição.
    # stream: MJPEG, HLS (.m3u8), RTSP ou qualquer URL que o OpenCV/FFmpeg abra.
    # pagina: página HTML que contém a imagem/stream; a URL real é descoberta nela.
    tipo: str = "snapshot"
    intervalo: float = 2.0
    alpr: bool = True
    ativa: bool = True
    local: str = ""
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class AlprConfig:
    ativo: bool = True
    detector_model: str = "yolo-v9-t-384-license-plate-end2end"
    ocr_model: str = "cct-s-v2-global-model"
    confianca_deteccao: float = 0.4
    confianca_ocr: float = 0.6
    # Mesma placa na mesma câmera dentro deste intervalo conta como uma detecção.
    janela_duplicata_s: float = 120.0


@dataclass
class AutoseguroConfig:
    ativo: bool = True
    # "assistido": abre um navegador visível e espera o operador resolver o captcha.
    # "manual": a plataforma só monta o link e o operador consulta no site.
    modo: str = "assistido"
    url: str = "https://www.autoseguro.gob.cl/"
    automatico: bool = False  # consultar toda placa nova detectada
    headless: bool = False
    executable_path: str | None = None
    intervalo_min_s: float = 15.0
    cache_horas: float = 24.0
    timeout_captcha_s: float = 180.0
    seletores: dict[str, str] = field(default_factory=lambda: {
        "aba_placa": "text=/placa patente/i",
        "campo_placa": "input[name*='patente' i], input[id*='patente' i], input[placeholder*='patente' i]",
        "botao_buscar": "button:has-text('Buscar'), button:has-text('Consultar'), input[type=submit]",
        "resultado": "#resultado, .resultado, [class*='result' i]",
        "captcha": "iframe[src*='recaptcha'], iframe[src*='hcaptcha'], .g-recaptcha, .h-captcha",
    })
    palavras_encargo: list[str] = field(default_factory=lambda: [
        "encargo vigente", "presenta encargo", "con encargo", "robado"])
    palavras_sem_encargo: list[str] = field(default_factory=lambda: [
        "no presenta encargo", "sin encargo", "no registra encargo", "no se encontr"])


@dataclass
class Config:
    host: str = "0.0.0.0"
    porta: int = 8000
    banco: str = "data/camaras.db"
    capturas: str = "data/capturas"
    alpr: AlprConfig = field(default_factory=AlprConfig)
    autoseguro: AutoseguroConfig = field(default_factory=AutoseguroConfig)
    cameras: list[CameraConfig] = field(default_factory=list)


def _sub(cls, dados: dict[str, Any] | None):
    dados = dict(dados or {})
    if cls is AutoseguroConfig and "seletores" in dados:
        base = AutoseguroConfig().seletores
        base.update(dados["seletores"] or {})
        dados["seletores"] = base
    return cls(**dados)


def carregar(caminho: str | Path) -> Config:
    caminho = Path(caminho)
    dados = yaml.safe_load(caminho.read_text(encoding="utf-8")) if caminho.exists() else {}
    dados = dados or {}
    return Config(
        host=dados.get("host", "0.0.0.0"),
        porta=int(dados.get("porta", 8000)),
        banco=dados.get("banco", "data/camaras.db"),
        capturas=dados.get("capturas", "data/capturas"),
        alpr=_sub(AlprConfig, dados.get("alpr")),
        autoseguro=_sub(AutoseguroConfig, dados.get("autoseguro")),
        cameras=[CameraConfig(**c) for c in dados.get("cameras") or []],
    )
