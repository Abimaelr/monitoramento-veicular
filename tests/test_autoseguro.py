from camaras.autoseguro import ConsultorAutoseguro, classificar
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
