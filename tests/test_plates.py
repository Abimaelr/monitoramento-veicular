import pytest

from camaras.plates import normalizar, valida


@pytest.mark.parametrize("entrada,esperado,formato", [
    ("BBCL42", "BBCL42", "novo"),
    ("bb·cl·42", "BBCL42", "novo"),
    ("BB-CL-42", "BBCL42", "novo"),
    ("AB1234", "AB1234", "antigo"),
    ("BCD12", "BCD12", "moto_nova"),
    ("AB123", "AB123", "moto_antiga"),
])
def test_formatos_validos(entrada, esperado, formato):
    p = normalizar(entrada)
    assert p.texto == esperado
    assert p.formato == formato
    assert p.correcoes == 0


@pytest.mark.parametrize("ocr,esperado", [
    ("8BCL42", "BBCL42"),   # 8 -> B na posição de letra
    ("BBCL4Z", "BBCL42"),   # Z -> 2 na posição de número
    ("ABI234", "AB1234"),   # I -> 1
    ("ABl234", "AB1234"),   # l minúsculo -> L -> 1
    ("GH5T0S", "GHST05"),
])
def test_corrige_confusoes_do_ocr(ocr, esperado):
    assert normalizar(ocr).texto == esperado


@pytest.mark.parametrize("lixo", ["", "ABC", "1234567", "AEIOU1", "5AU5341", "BBMN42"])
def test_rejeita_o_que_nao_e_placa_chilena(lixo):
    assert normalizar(lixo) is None


def test_novo_formato_nao_aceita_vogais_nem_m_n_q():
    assert not valida("BACL42")
    assert not valida("BMCL42")
    assert valida("BBCL42")


def test_exibicao():
    assert normalizar("BBCL42").exibicao == "BB·CL·42"
    assert normalizar("AB1234").exibicao == "AB·12·34"
    assert normalizar("BCD12").exibicao == "BCD·12"
    assert normalizar("AB123").exibicao == "AB·123"


def test_confianca_ignora_preenchimento():
    from camaras.alpr import confianca_caracteres

    conf = [0.8, 0.4, 0.5, 0.6, 0.45, 0.97, 0.97, 0.94, 0.99, 1.0]
    assert confianca_caracteres("16091_____", conf) == pytest.approx(0.55)
    assert confianca_caracteres("X", 0.7) == 0.7
