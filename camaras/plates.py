"""Normalização e validação de placas patente chilenas.

Formatos suportados:

* ``LLLLNN`` – veículos a partir de 2007 (ex.: ``BBCL42``). As letras usam só
  consoantes, sem M, N, Ñ e Q.
* ``LLNNNN`` – veículos anteriores a 2007 (ex.: ``AB1234``).
* ``LLLNN``  – motos novas (ex.: ``BCD12``).
* ``LLNNN``  – motos antigas (ex.: ``AB123``).

O OCR confunde letras e números parecidos (O/0, I/1, B/8...). Como cada
formato define se uma posição é letra ou número, corrigimos a leitura posição a
posição e escolhemos o formato que exige menos trocas.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

LETRAS_NOVAS = set("BCDFGHJKLPRSTVWXYZ")
LETRAS_ANTIGAS = set("ABCDEFGHIJKLMNOPRSTUVWXYZ")
DIGITOS = set("0123456789")

# Leitura do OCR -> caractere provável quando a posição exige número.
PARA_DIGITO = {"O": "0", "D": "0", "Q": "0", "U": "0", "I": "1", "L": "1", "T": "1",
               "Z": "2", "A": "4", "S": "5", "G": "6", "B": "8"}
# Leitura do OCR -> caractere provável quando a posição exige letra.
PARA_LETRA = {"0": "D", "1": "L", "2": "Z", "4": "A", "5": "S", "6": "G", "7": "T", "8": "B"}


@dataclass(frozen=True)
class Formato:
    nome: str
    padrao: str  # "L" = letra, "N" = número
    letras: frozenset[str]
    tipo: str


FORMATOS = (
    Formato("novo", "LLLLNN", frozenset(LETRAS_NOVAS), "veiculo"),
    Formato("antigo", "LLNNNN", frozenset(LETRAS_ANTIGAS), "veiculo"),
    Formato("moto_nova", "LLLNN", frozenset(LETRAS_NOVAS), "moto"),
    Formato("moto_antiga", "LLNNN", frozenset(LETRAS_ANTIGAS), "moto"),
)


@dataclass(frozen=True)
class PlacaNormalizada:
    texto: str      # ex.: "BBCL42"
    formato: str    # nome do formato
    tipo: str       # "veiculo" ou "moto"
    correcoes: int  # quantos caracteres foram trocados

    @property
    def exibicao(self) -> str:
        """Placa no estilo impresso, ex.: ``BB·CL·42`` ou ``AB·12·34``."""
        t = self.texto
        if len(t) == 6:
            return f"{t[0:2]}·{t[2:4]}·{t[4:6]}"
        return f"{t[0:2]}·{t[2:]}" if self.formato == "moto_antiga" else f"{t[0:3]}·{t[3:]}"


def limpar(texto: str) -> str:
    """Remove separadores, espaços e o ``_`` de preenchimento do OCR."""
    return re.sub(r"[^A-Z0-9]", "", (texto or "").upper().replace("Ñ", "N"))


def _ajustar(texto: str, formato: Formato) -> tuple[str, int] | None:
    if len(texto) != len(formato.padrao):
        return None
    saida, trocas = [], 0
    for ch, tipo in zip(texto, formato.padrao):
        if tipo == "N":
            if ch not in DIGITOS:
                ch, trocas = PARA_DIGITO.get(ch), trocas + 1
        elif ch not in formato.letras:
            ch, trocas = PARA_LETRA.get(ch), trocas + 1
            if ch is not None and ch not in formato.letras:
                ch = None
        if ch is None:
            return None
        saida.append(ch)
    return "".join(saida), trocas


def normalizar(texto: str, max_correcoes: int = 2) -> PlacaNormalizada | None:
    """Devolve a placa chilena mais provável para a leitura do OCR, ou ``None``.

    >>> normalizar("BB-CL-42").texto
    'BBCL42'
    >>> normalizar("8BCL4Z").texto
    'BBCL42'
    """
    limpo = limpar(texto)
    melhores: list[PlacaNormalizada] = []
    for formato in FORMATOS:
        r = _ajustar(limpo, formato)
        if r and r[1] <= max_correcoes:
            melhores.append(PlacaNormalizada(r[0], formato.nome, formato.tipo, r[1]))
    if not melhores:
        return None
    # Em empate, a ordem de FORMATOS dá preferência ao formato atual.
    return min(melhores, key=lambda p: p.correcoes)


def valida(texto: str) -> bool:
    """``True`` se o texto já é uma placa chilena válida, sem correções."""
    p = normalizar(texto, max_correcoes=0)
    return p is not None
