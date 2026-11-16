"""
tests de las tools. para correrlos, desde la raiz del proyecto:
    pytest -q
"""
import math
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ragnar_agent.tools import datos
from ragnar_agent.tools import tools as t

TODOS = sorted(t._catalogo()["producto_id"])
COMPS = sorted(t._ofertas()["competidor"].unique())


@pytest.fixture(scope="module")
def analisis():
    return t._analizar(TODOS, COMPS, con_comparacion=True)


def _ofertas_con(analisis, veredicto):
    for a in analisis:
        for o in a["ofertas"]:
            if o["veredicto"] == veredicto:
                yield a, o


#consultar_catalogo 
def test_catalogo_sin_filtros_da_error_y_ayuda():
    r = t.consultar_catalogo()
    assert r["status"] == "error" and "categorias_disponibles" in r


def test_catalogo_filtra_sin_distinguir_acentos_ni_mayusculas():
    assert t.consultar_catalogo(categoria="HIGIENE PERSONAL")["status"] == "ok"
    assert t.consultar_catalogo(producto="azucar")["total_encontrados"] > 0


def test_catalogo_por_id_y_sin_resultados():
    assert t.consultar_catalogo(producto_id="rag-00001")["productos"][0]["producto_id"] == "RAG-00001"
    assert t.consultar_catalogo(producto="inexistente xyz")["status"] == "sin_resultados"


def test_catalogo_max_resultados_invalido_y_acotado():
    assert t.consultar_catalogo(categoria="Bebidas", max_resultados="abc")["status"] == "error"
    assert t.consultar_catalogo(categoria="Bebidas", max_resultados=9999)["mostrando"] == t.MAX_RESULTADOS


# validaciones
def test_ids_invalidos_y_vacios():
    assert t.buscar_precios_competencia("XXX")["status"] == "error"
    assert t.buscar_precios_competencia("RAG-99999")["status"] == "error"
    assert t.comparar_precios([])["status"] == "error"
    assert t.comparar_precios(["RAG-00001", "mal"])["status"] == "error"
    assert t.comparar_precios(TODOS[: t.MAX_RESULTADOS + 1])["status"] == "error"


def test_competidor_inexistente_da_error_con_opciones():
    r = t.buscar_precios_competencia("RAG-00001", competidor="NoExiste")
    assert r["status"] == "error" and "SuperNorte" in r["mensaje"]


def test_filtro_por_pais():
    r = t.comparar_precios(["RAG-00003"], competidor="Uruguay")
    assert r["competidores_consultados"] == ["UruMarket"]


def test_ids_como_texto_se_aceptan():
    assert t.comparar_precios("RAG-00003, RAG-00009")["status"] == "ok"


# coincidencia y cuentas 
def test_diferencias_calculadas_correctamente(analisis):
    cat = t._catalogo().set_index("producto_id")
    verificadas = 0
    for a, o in _ofertas_con(analisis, "equivalente"):
        c = o["comparacion"]
        if c is None:
            continue
        ragnar = float(cat.loc[a["producto_ragnar"]["producto_id"], "precio"])
        comp = o["precio_regular"]["valor_ars"]
        assert math.isclose(c["diferencia_absoluta_ars"], comp - ragnar, abs_tol=0.02)
        assert math.isclose(c["diferencia_porcentual"], (comp - ragnar) / ragnar * 100, abs_tol=0.02)
        assert c["base_de_comparacion"] == "precio_total" and not c["referencial"]
        verificadas += 1
    assert verificadas > 100


def test_marca_diferente_y_unidad_incompatible_nunca_se_comparan(analisis):
    n = 0
    for _, o in list(_ofertas_con(analisis, "marca_diferente")) + list(_ofertas_con(analisis, "unidad_incompatible")):
        assert o["comparacion"] is None
        n += 1
    assert _ofertas_con(analisis, "marca_diferente")
    assert n > 0


def test_presentacion_diferente_es_referencial_y_por_unidad(analisis):
    cat = t._catalogo().set_index("producto_id")
    n = 0
    for a, o in _ofertas_con(analisis, "presentacion_diferente"):
        c = o["comparacion"]
        assert c["referencial"] and c["base_de_comparacion"] == "precio_unitario"
        row = cat.loc[a["producto_ragnar"]["producto_id"]]
        esperado, _ = datos._unitario(float(row.precio), row.contenido, row.unidad_medida)
        assert math.isclose(c["precio_ragnar"], esperado, abs_tol=0.02)
        n += 1
    assert n > 0


def test_ofertas_en_kg_o_litros_se_reconocen_como_equivalentes(analisis):
    ofertas = t._ofertas()
    publicadas_en_kg_l = ofertas[ofertas["unidad"].isin(["kg", "l"])]
    assert len(publicadas_en_kg_l) > 0
    hay = [o for _, o in _ofertas_con(analisis, "equivalente") if o["presentacion_publicada"].endswith((" kg", " l"))]
    assert len(hay) > 0


def test_conversion_de_moneda(analisis):
    n = 0
    for _, o in _ofertas_con(analisis, "equivalente"):
        if o["competidor"] == "UruMarket":
            assert o["precio_regular"]["moneda"] == "UYU"
            assert math.isclose(o["precio_regular"]["valor_ars"], o["precio_regular"]["valor_original"] * 36.5, rel_tol=1e-3)
            assert o["tipo_cambio"]["ars_por_unidad"] == 36.5 and o["tipo_cambio"]["fecha"]
            n += 1
    assert n > 0


def test_promociones_se_separan_del_precio_regular(analisis):
    n = 0
    for _, o in _ofertas_con(analisis, "equivalente"):
        if o["precio_promocional"] is not None:
            c = o["comparacion"]
            assert o["promocion"]
            assert o["precio_promocional"]["valor_ars"] < o["precio_regular"]["valor_ars"]
            assert "diferencia_porcentual_promocional" in c
            n += 1
    assert n > 0


def test_toda_oferta_tiene_fuente_y_fecha(analisis):
    for a in analisis:
        for o in a["ofertas"]:
            if o["veredicto"] != "no_encontrado":
                assert o["fuente_url"].startswith("https://") and o["fecha_consulta"]


def test_producto_no_encontrado_no_inventa_precio(analisis):
    sin = [a for a in analisis if a["estado"] == "no_encontrado"]
    assert sin, "los datos simulados deberían incluir productos sin coincidencias"
    pid = sin[0]["producto_ragnar"]["producto_id"]
    r = t.buscar_precios_competencia(pid)
    assert r["status"] == "sin_resultados"
    assert all("precio_regular" not in o for o in r["ofertas"])
    rc = t.comparar_precios([pid])
    assert rc["status"] == "sin_resultados" and pid in rc["resumen"]["ids_no_encontrados"]


def test_advertencia_de_catalogo_duplicado(analisis):
    assert any("advertencia_catalogo" in a for a in analisis)


#informe 
def test_informe_se_genera_y_guarda(tmp_path, monkeypatch):
    monkeypatch.setattr(t, "INFORMES_DIR", tmp_path)
    ids = [p["producto_id"] for p in t.consultar_catalogo(producto="Desodorante", max_resultados=6)["productos"]]
    r = t.generar_informe(ids, titulo="Prueba")
    assert r["status"] == "ok" and Path(r["ruta_archivo"]).exists()
    md = Path(r["ruta_archivo"]).read_text(encoding="utf-8")
    for esperado in ("# Prueba", "Fuente", "Fecha", "SIMULADAS", "Conclusiones", "Promociones"):
        assert esperado in md
    assert "https://www." in md


def test_informe_con_ids_invalidos():
    assert t.generar_informe(["nada"])["status"] == "error"


def test_web_search_valida_consulta_vacia():
    assert t.web_search("   ")["status"] == "error"
