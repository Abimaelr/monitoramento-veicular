from camaras.autoseguro import ConsultorAutoseguro, classificar, classificar_resposta
from camaras.config import AutoseguroConfig
from camaras.storage import Banco


def test_classificar():
    cfg = AutoseguroConfig()
    assert classificar("El vehículo NO PRESENTA ENCARGO por robo", cfg) == "sem_encargo"
    assert classificar("Vehículo con encargo vigente", cfg) == "encargo"
    assert classificar("Servicio no disponible", cfg) == "indeterminado"


def test_fila_respeita_cache(tmp_path):
    banco = Banco(":memory:")
    c = ConsultorAutoseguro(AutoseguroConfig(), banco, tmp_path)  # sem iniciar a thread
    assert c.enfileirar("BBCL42") == "pendente"
    assert c.enfileirar("BBCL42") == "pendente"  # já está na fila
    assert c._fila.qsize() == 1

    c._enfileiradas.clear()
    banco.salvar_consulta("BBCL42", "sem_encargo")
    assert c.enfileirar("BBCL42") == "sem_encargo"  # cache ainda válido
    assert c.enfileirar("BBCL42", forcar=True) == "pendente"


def test_modo_manual_nao_enfileira(tmp_path):
    c = ConsultorAutoseguro(AutoseguroConfig(modo="manual"), Banco(":memory:"), tmp_path)
    assert c.enfileirar("BBCL42") == "manual"
    assert c._fila.empty()


def _resp(especies, cod_tipo=1, eun=1, cod=0):
    return {"Cod": cod, "Desc": "", "Result": {"CodTipoEncargo": cod_tipo, "EUN": eun, "Especies": especies}}


def _esp(tipo, recuperada=False, desc="X"):
    return {"TipoEspecie": tipo, "Descripcion": desc,
            "FechaSolucionEspecie": "2026-01-01T00:00:00" if recuperada else None}


def test_classificar_resposta_segue_o_site():
    # Sem encargo: CodTipoEncargo != 1, ou nenhuma peça, ou EUN == 0.
    assert classificar_resposta(_resp([], cod_tipo=0))[0] == "sem_encargo"
    assert classificar_resposta(_resp([]))[0] == "sem_encargo"
    assert classificar_resposta(_resp([_esp(3)], eun=0))[0] == "sem_encargo"
    # Veículo (VIN) com encargo pendente.
    status, det = classificar_resposta(_resp([_esp(1), _esp(2), _esp(3)]))
    assert status == "encargo" and det.startswith("Encargo policial vigente")
    # Veículo recuperado, mas a placa traseira não: encargo parcial.
    status, det = classificar_resposta(_resp([_esp(1, True), _esp(2), _esp(3, True), _esp(4, True)]))
    assert status == "encargo" and "placa traseira" in det
    # Tudo recuperado.
    assert classificar_resposta(_resp([_esp(t, True) for t in (1, 2, 3, 4)]))[0] == "sem_encargo"


def test_classificar_resposta_erros():
    assert classificar_resposta({"Cod": 20})[0] == "erro"
    status, det = classificar_resposta({"Cod": 1, "Desc": "Captcha inválido"})
    assert status == "erro" and "Captcha inválido" in det


def test_classificar_texto_do_site():
    cfg = AutoseguroConfig()
    assert classificar("La Placa Patente consultada NO mantiene encargo policial vigente.", cfg) == "sem_encargo"
    assert classificar("Encargo policial vigente", cfg) == "encargo"
