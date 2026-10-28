"""Persistência em SQLite: detecções de placas e consultas ao Autoseguro."""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

ESQUEMA = """
CREATE TABLE IF NOT EXISTS deteccoes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    placa TEXT NOT NULL,
    leitura_ocr TEXT NOT NULL,
    formato TEXT,
    camera_id TEXT NOT NULL,
    confianca_deteccao REAL,
    confianca_ocr REAL,
    bbox TEXT,
    recorte TEXT,
    quadro TEXT,
    criado_em REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_det_placa ON deteccoes(placa);
CREATE INDEX IF NOT EXISTS idx_det_criado ON deteccoes(criado_em);

CREATE TABLE IF NOT EXISTS consultas (
    placa TEXT PRIMARY KEY,
    -- pendente | em_andamento | aguardando_captcha | encargo | sem_encargo | indeterminado | erro
    status TEXT NOT NULL,
    detalhe TEXT,
    atualizado_em REAL NOT NULL
);
"""

STATUS_FINAIS = {"encargo", "sem_encargo", "indeterminado"}


class Banco:
    def __init__(self, caminho: str | Path):
        if str(caminho) != ":memory:":
            Path(caminho).parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(caminho), check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._con.executescript(ESQUEMA)

    def _exec(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._con.execute(sql, params)
            self._con.commit()
            return cur

    def _lista(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._con.execute(sql, params).fetchall()]

    # Detecções -----------------------------------------------------------
    def inserir_deteccao(self, **campos: Any) -> int:
        campos.setdefault("criado_em", time.time())
        cols = ", ".join(campos)
        marcas = ", ".join("?" for _ in campos)
        cur = self._exec(f"INSERT INTO deteccoes ({cols}) VALUES ({marcas})", tuple(campos.values()))
        return int(cur.lastrowid)

    def ultima_deteccao(self, placa: str, camera_id: str) -> dict[str, Any] | None:
        r = self._lista(
            "SELECT * FROM deteccoes WHERE placa=? AND camera_id=? ORDER BY criado_em DESC LIMIT 1",
            (placa, camera_id))
        return r[0] if r else None

    def deteccao(self, det_id: int) -> dict[str, Any] | None:
        r = self._lista("SELECT * FROM deteccoes WHERE id=?", (det_id,))
        return r[0] if r else None

    def listar_deteccoes(self, placa: str | None = None, camera_id: str | None = None,
                         desde_id: int = 0, limite: int = 100) -> list[dict[str, Any]]:
        sql = ("SELECT d.*, c.status AS consulta_status FROM deteccoes d "
               "LEFT JOIN consultas c ON c.placa = d.placa WHERE d.id > ?")
        params: list[Any] = [desde_id]
        if placa:
            sql += " AND d.placa LIKE ?"
            params.append(f"%{placa}%")
        if camera_id:
            sql += " AND d.camera_id = ?"
            params.append(camera_id)
        sql += " ORDER BY d.id DESC LIMIT ?"
        params.append(limite)
        return self._lista(sql, tuple(params))

    # Consultas -----------------------------------------------------------
    def consulta(self, placa: str) -> dict[str, Any] | None:
        r = self._lista("SELECT * FROM consultas WHERE placa=?", (placa,))
        return r[0] if r else None

    def salvar_consulta(self, placa: str, status: str, detalhe: str = "") -> None:
        self._exec(
            "INSERT INTO consultas (placa, status, detalhe, atualizado_em) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(placa) DO UPDATE SET status=excluded.status, detalhe=excluded.detalhe, "
            "atualizado_em=excluded.atualizado_em",
            (placa, status, detalhe, time.time()))

    def listar_consultas(self, status: str | None = None, limite: int = 100) -> list[dict[str, Any]]:
        if status:
            return self._lista("SELECT * FROM consultas WHERE status=? ORDER BY atualizado_em DESC LIMIT ?",
                               (status, limite))
        return self._lista("SELECT * FROM consultas ORDER BY atualizado_em DESC LIMIT ?", (limite,))
