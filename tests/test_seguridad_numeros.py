"""tests de la lectura y el control de numeros (seguridad/numeros.py)"""
from ragnar_agent.seguridad.numeros import leer_numero, numeros_de_resultado, numeros_sin_respaldo


def test_lee_formato_argentino_e_ingles():
    assert leer_numero("3.383,00") == (3383.0, 2)
    assert leer_numero("2941.54") == (2941.54, 2)
    assert leer_numero("13,05") == (13.05, 2)
    assert leer_numero("36,5") == (36.5, 1)
    assert leer_numero("3.383") == (3383.0, 0)  #aca el punto es separador de miles, no hay decimales
    assert leer_numero("1.234.567,89") == (1234567.89, 2)


def test_extrae_numeros_de_un_resultado_anidado():
    resultado = {"status": "ok", "precio": 3383.0, "dif": -13.05, "activo": True,
                 "ofertas": [{"valor": 2941.54, "texto": "1 UYU = 36,5 ARS"}]}
    assert {3383.0, 13.05, 2941.54, 36.5} <= set(numeros_de_resultado(resultado))
    assert numeros_de_resultado(True) == []  #los booleanos no cuentan como numeros


def test_numeros_con_respaldo_no_se_marcan():
    validos = [3383.0, 2941.54, 13.05]
    texto = "Ragnar cobra $3.383,00 y UruMarket 2.941,54 ARS: Ragnar es 13,05% más caro (≈13,1%)."
    assert numeros_sin_respaldo(texto, validos) == []


def test_numero_inventado_se_marca():
    assert numeros_sin_respaldo("El precio es 1.999,99 ARS", [3383.0]) == ["1.999,99"]


def test_enteros_y_fechas_no_se_revisan():
    assert numeros_sin_respaldo("750 ml, 3 productos, consultado el 2026-11-09 (RAG-00003)", []) == []
