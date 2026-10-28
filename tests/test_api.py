import cv2
import pytest
from fastapi.testclient import TestClient

from camaras.config import AlprConfig, CameraConfig, Config
from camaras.plates import normalizar
from camaras.alpr import Leitura
from camaras.storage import Banco
from camaras.web import criar_app
from camaras.worker import Monitor
from gerar_placa import cena


class LeitorFalso:
    def ler(self, quadro):
        return [Leitura(normalizar("BBCL42"), "BBCL42", 0.9, 0.99, (519, 443, 757, 520))]


@pytest.fixture
def monitor(tmp_path):
    cfg = Config(banco=":memory:", capturas=str(tmp_path / "cap"),
                 cameras=[CameraConfig(id="c1", nome="Plaza", url="http://x/cam.jpg")])
    return Monitor(cfg, leitor=LeitorFalso(), banco=Banco(":memory:"))


def test_fluxo_completo(monitor):
    cli = TestClient(criar_app(monitor, iniciar=False))
    cam = monitor.cameras["c1"]
    cam.processar(cena("BB·CL·42"))
    cam.processar(cena("BB·CL·42"))  # duplicata dentro da janela

    assert cli.get("/api/cameras").json()[0]["placas_no_quadro"] == ["BBCL42"]
    r = cli.get("/api/cameras/c1/quadro.jpg")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"

    dets = cli.get("/api/deteccoes").json()
    assert len(dets) == 1 and dets[0]["placa"] == "BBCL42"
    assert cli.get(f"/api/deteccoes/{dets[0]['id']}/recorte.jpg").status_code == 200

    p = cli.get("/api/placas/bb-cl-42").json()
    assert p["exibicao"] == "BB·CL·42" and len(p["deteccoes"]) == 1
    assert cli.post("/api/placas/BBCL42/consultar").json()["status"] == "pendente"
    assert cli.get("/api/placas/XYZ").status_code == 422

    monitor.banco.salvar_consulta("BBCL42", "encargo", "con encargo vigente")
    alertas = cli.get("/api/alertas").json()
    assert alertas[0]["placa"] == "BBCL42" and alertas[0]["ultima_deteccao"]["camera_id"] == "c1"
    assert cli.get("/").status_code == 200


@pytest.mark.modelo
def test_alpr_real_em_imagem_sintetica(tmp_path):
    """Usa os modelos ONNX de verdade (baixados na primeira execução)."""
    from camaras.alpr import LeitorPlacas

    leitor = LeitorPlacas(AlprConfig())
    for texto, esperado in [("BB·CL·42", "BBCL42"), ("AB·12·34", "AB1234"), ("BCD·12", "BCD12")]:
        leituras = leitor.ler(cena(texto))
        assert [lt.placa.texto for lt in leituras] == [esperado]

    monitor = Monitor(Config(banco=":memory:", capturas=str(tmp_path)), leitor=leitor, banco=Banco(":memory:"))
    cli = TestClient(criar_app(monitor, iniciar=False))
    ok, buf = cv2.imencode(".jpg", cena("BB·CL·42"))
    r = cli.post("/api/analisar", files={"imagem": ("c.jpg", buf.tobytes(), "image/jpeg")})
    assert r.json()["placas"][0]["placa"] == "BBCL42"
