"""Gera uma cena sintética com uma placa chilena (só para testes)."""
from __future__ import annotations

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

FONTE = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def placa_img(texto: str, larg: int = 360, alt: int = 120) -> np.ndarray:
    img = Image.new("RGB", (larg, alt), "white")
    d = ImageDraw.Draw(img)
    d.rectangle([2, 2, larg - 3, alt - 3], outline="black", width=5)
    d.text((larg // 2, alt // 12), "CHILE", fill="black", anchor="mt", font=ImageFont.truetype(FONTE, max(10, alt // 7)))
    tam = alt
    f = ImageFont.truetype(FONTE, tam)
    while d.textlength(texto, font=f) > larg * 0.88 or tam > alt * 0.6:
        tam -= 2
        f = ImageFont.truetype(FONTE, tam)
    d.text((larg // 2, alt // 2 + alt // 10), texto, fill="black", anchor="mm", font=f)
    return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)


def cena(texto: str, w: int = 1280, h: int = 720) -> np.ndarray:
    rng = np.random.default_rng(0)
    fundo = np.full((h, w, 3), 70, np.uint8)
    fundo = cv2.add(fundo, rng.integers(0, 25, (h, w, 3), dtype=np.uint8))
    # "carro"
    cv2.rectangle(fundo, (380, 220), (900, 560), (40, 40, 160), -1)
    cv2.rectangle(fundo, (430, 240), (850, 360), (30, 30, 30), -1)
    cv2.circle(fundo, (450, 560), 50, (20, 20, 20), -1)
    cv2.circle(fundo, (830, 560), 50, (20, 20, 20), -1)
    p = placa_img(texto, 240, 80)
    y, x = 440, 520
    fundo[y:y + p.shape[0], x:x + p.shape[1]] = p
    return cv2.GaussianBlur(fundo, (3, 3), 0)


if __name__ == "__main__":
    import sys
    cv2.imwrite(sys.argv[2], cena(sys.argv[1]))
