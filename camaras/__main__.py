"""Linha de comando.

    python -m camaras servir   [-c config.yaml]
    python -m camaras descobrir http://www.portalimo.cl/   # sugere entradas de câmera
    python -m camaras analisar foto.jpg [--saida anotada.jpg]
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
import unicodedata
from urllib.parse import urljoin, urlparse

import yaml

from .config import carregar


def cmd_servir(args) -> None:
    import uvicorn

    from .web import criar_app
    from .worker import Monitor

    cfg = carregar(args.config)
    if not cfg.cameras:
        logging.warning("nenhuma câmera em %s; o painel abre vazio", args.config)
    app = criar_app(Monitor(cfg))
    uvicorn.run(app, host=args.host or cfg.host, port=args.porta or cfg.porta)


def _slug(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", sem_acento.lower()).strip("-")


def cmd_descobrir(args) -> None:
    """Varre a página do portal (e as páginas internas que pareçam de câmera) e imprime
    um bloco ``cameras:`` pronto para colar no config.yaml."""
    import httpx
    from bs4 import BeautifulSoup

    from .sources import USER_AGENT, _parece_camera, descobrir_midia, rotulos_midia

    http = httpx.Client(timeout=20, follow_redirects=True, headers={"User-Agent": USER_AGENT})
    raiz = http.get(args.url)
    paginas = [(str(raiz.url), raiz.text, "")]
    sopa = BeautifulSoup(raiz.text, "html.parser")
    dominio = urlparse(str(raiz.url)).netloc
    for a in sopa.find_all("a", href=True):
        href = urljoin(str(raiz.url), a["href"])
        texto = a.get_text(" ", strip=True)
        if urlparse(href).netloc == dominio and re.search(r"cam|c[aá]mara|vivo|live", href + texto, re.I):
            try:
                r = http.get(href)
                paginas.append((str(r.url), r.text, texto))
            except httpx.HTTPError as e:
                logging.warning("falha em %s: %s", href, e)
        if len(paginas) > args.max_paginas:
            break

    cameras, vistos, ids = [], set(), set()
    for url, html, titulo in paginas:
        nomes = rotulos_midia(html, url)
        for midia in descobrir_midia(html, url):
            if midia in vistos or not _parece_camera(midia):
                continue
            vistos.add(midia)
            tipo = "stream" if re.search(r"\.m3u8|mjpe?g|rtsp://", midia, re.I) else "snapshot"
            n = len(cameras) + 1
            nome = nomes.get(midia) or titulo or f"Câmera {n}"
            cid = _slug(nome) or f"cam{n:02d}"
            if cid in ids:
                cid = f"{cid}-{n}"
            ids.add(cid)
            cameras.append({"id": cid, "nome": nome, "url": midia, "tipo": tipo})
    if not cameras:
        print("Nenhuma mídia de câmera encontrada. Páginas visitadas:", file=sys.stderr)
        for url, *_ in paginas:
            print("  ", url, file=sys.stderr)
        sys.exit(1)
    print(yaml.safe_dump({"cameras": cameras}, allow_unicode=True, sort_keys=False))


def cmd_analisar(args) -> None:
    import cv2

    from .alpr import LeitorPlacas, anotar

    cfg = carregar(args.config)
    leitor = LeitorPlacas(cfg.alpr)
    quadro = cv2.imread(args.imagem)
    if quadro is None:
        sys.exit(f"não consegui abrir {args.imagem}")
    leituras = leitor.ler(quadro)
    for lt in leituras:
        print(f"{lt.placa.exibicao}\t{lt.placa.formato}\tOCR={lt.leitura_ocr!r} "
              f"conf={lt.confianca_ocr:.2f} det={lt.confianca_deteccao:.2f} bbox={lt.bbox}")
    if not leituras:
        print("nenhuma placa encontrada")
    if args.saida:
        cv2.imwrite(args.saida, anotar(quadro, leituras))


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # um log por snapshot polui demais
    p = argparse.ArgumentParser(prog="camaras")
    p.add_argument("-c", "--config", default="config.yaml")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("servir", help="sobe o painel e o monitoramento")
    s.add_argument("--host")
    s.add_argument("--porta", type=int)
    s.set_defaults(func=cmd_servir)

    d = sub.add_parser("descobrir", help="lista as câmeras encontradas numa página")
    d.add_argument("url")
    d.add_argument("--max-paginas", type=int, default=60)
    d.set_defaults(func=cmd_descobrir)

    a = sub.add_parser("analisar", help="lê as placas de uma imagem")
    a.add_argument("imagem")
    a.add_argument("--saida")
    a.set_defaults(func=cmd_analisar)

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
