"""API HTTP e painel web."""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from pathlib import Path

import cv2
import numpy as np
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .alpr import anotar
from .autoseguro import link_manual
from .plates import limpar, normalizar
from .worker import Monitor

ESTATICOS = Path(__file__).parent / "static"


def criar_app(monitor: Monitor, iniciar: bool = True) -> FastAPI:
    @asynccontextmanager
    async def ciclo(_app: FastAPI):
        if iniciar:
            monitor.iniciar()
        yield
        if iniciar:
            monitor.parar()

    app = FastAPI(title="Monitoramento veicular", lifespan=ciclo)
    app.mount("/static", StaticFiles(directory=ESTATICOS), name="static")

    def _camera(cam_id: str):
        cam = monitor.cameras.get(cam_id)
        if cam is None:
            raise HTTPException(404, "câmera não encontrada")
        return cam

    @app.get("/", include_in_schema=False)
    def painel():
        return FileResponse(ESTATICOS / "index.html")

    @app.get("/api/cameras")
    def cameras():
        return [{
            "id": c.cfg.id, "nome": c.cfg.nome, "local": c.cfg.local, "tipo": c.cfg.tipo,
            "alpr": c.cfg.alpr, "online": c.estado.online, "erro": c.estado.erro,
            "ultimo_quadro_em": c.estado.ultimo_quadro_em,
            "placas_no_quadro": [lt.placa.texto for lt in c.estado.leituras],
        } for c in monitor.cameras.values()]

    @app.get("/api/cameras/{cam_id}/quadro.jpg")
    def quadro(cam_id: str):
        jpeg = _camera(cam_id).estado.jpeg
        if jpeg is None:
            raise HTTPException(503, "sem imagem ainda")
        return Response(jpeg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.get("/api/cameras/{cam_id}/mjpeg")
    async def mjpeg(cam_id: str):
        cam = _camera(cam_id)

        async def gerar():
            ultimo = 0.0
            while True:
                if cam.estado.jpeg and cam.estado.ultimo_quadro_em != ultimo:
                    ultimo = cam.estado.ultimo_quadro_em
                    yield (b"--quadro\r\nContent-Type: image/jpeg\r\n\r\n" + cam.estado.jpeg + b"\r\n")
                await asyncio.sleep(0.2)

        return StreamingResponse(gerar(), media_type="multipart/x-mixed-replace; boundary=quadro")

    @app.get("/api/deteccoes")
    def deteccoes(placa: str | None = None, camera: str | None = None, desde_id: int = 0, limite: int = 100):
        return monitor.banco.listar_deteccoes(limpar(placa) if placa else None, camera, desde_id, min(limite, 500))

    @app.get("/api/deteccoes/{det_id}/{qual}.jpg")
    def imagem_deteccao(det_id: int, qual: str):
        det = monitor.banco.deteccao(det_id)
        if det is None or qual not in ("recorte", "quadro"):
            raise HTTPException(404)
        caminho = Path(det[qual] or "")
        if not caminho.is_file():
            raise HTTPException(404, "imagem não encontrada")
        return FileResponse(caminho, media_type="image/jpeg")

    @app.get("/api/placas/{placa}")
    def placa(placa: str):
        p = normalizar(placa, max_correcoes=0)
        if p is None:
            raise HTTPException(422, "placa chilena inválida")
        return {
            "placa": p.texto, "exibicao": p.exibicao, "formato": p.formato, "tipo": p.tipo,
            "consulta": monitor.banco.consulta(p.texto),
            "link_autoseguro": link_manual(p.texto, monitor.cfg.autoseguro),
            "deteccoes": monitor.banco.listar_deteccoes(p.texto, limite=50),
        }

    @app.post("/api/placas/{placa}/consultar")
    def consultar(placa: str, forcar: bool = False):
        p = normalizar(placa, max_correcoes=0)
        if p is None:
            raise HTTPException(422, "placa chilena inválida")
        status = monitor.consultor.enfileirar(p.texto, forcar=forcar)
        return {"placa": p.texto, "status": status,
                "link_autoseguro": link_manual(p.texto, monitor.cfg.autoseguro)}

    @app.get("/api/consultas")
    def consultas(status: str | None = None):
        return monitor.banco.listar_consultas(status)

    @app.get("/api/alertas")
    def alertas():
        """Placas com encargo vigente e onde/quando foram vistas pela última vez."""
        saida = []
        for c in monitor.banco.listar_consultas("encargo"):
            dets = monitor.banco.listar_deteccoes(c["placa"], limite=1)
            saida.append({**c, "ultima_deteccao": dets[0] if dets else None})
        return saida

    @app.post("/api/analisar")
    async def analisar(imagem: UploadFile = File(...), anotada: bool = False):
        """Roda o ALPR numa imagem enviada (útil para testar o modelo com fotos)."""
        if monitor.leitor is None:
            raise HTTPException(503, "ALPR desativado na configuração")
        dados = np.frombuffer(await imagem.read(), dtype=np.uint8)
        quadro = cv2.imdecode(dados, cv2.IMREAD_COLOR)
        if quadro is None:
            raise HTTPException(400, "imagem inválida")
        inicio = time.time()
        leituras = await asyncio.to_thread(monitor.leitor.ler, quadro)
        if anotada:
            ok, buf = cv2.imencode(".jpg", anotar(quadro, leituras))
            return Response(buf.tobytes(), media_type="image/jpeg")
        return {
            "tempo_ms": round((time.time() - inicio) * 1000),
            "placas": [{
                "placa": lt.placa.texto, "exibicao": lt.placa.exibicao, "formato": lt.placa.formato,
                "leitura_ocr": lt.leitura_ocr, "confianca_ocr": round(lt.confianca_ocr, 3),
                "confianca_deteccao": round(lt.confianca_deteccao, 3), "bbox": lt.bbox,
            } for lt in leituras],
        }

    return app
