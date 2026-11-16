"""tests de los guardrails de las tools (seguridad/herramientas.py y seguridad/resultados.py)"""
import json

from conftest import herramienta
from ragnar_agent.seguridad import antes_de_tool, config, despues_de_tool


def test_llamada_normal_pasa(contexto):
    assert antes_de_tool(herramienta("consultar_catalogo"), {"marca": "Dove"}, contexto()) is None


def test_limite_de_llamadas_por_turno(contexto):
    ctx = contexto()
    for _ in range(config.MAX_LLAMADAS_TOOLS_POR_TURNO):
        assert antes_de_tool(herramienta("consultar_catalogo"), {}, ctx) is None
    bloqueo = antes_de_tool(herramienta("consultar_catalogo"), {}, ctx)
    assert bloqueo["status"] == "error" and bloqueo["bloqueado_por"] == "seguridad"


def test_el_contador_se_reinicia_en_otro_turno(contexto):
    state = {}
    for _ in range(config.MAX_LLAMADAS_TOOLS_POR_TURNO + 1):
        antes_de_tool(herramienta("consultar_catalogo"), {}, contexto(turno="t1", state=state))
    assert antes_de_tool(herramienta("consultar_catalogo"), {}, contexto(turno="t2", state=state)) is None


def test_un_solo_informe_por_turno(contexto):
    ctx = contexto()
    informe = herramienta("generar_informe")
    assert antes_de_tool(informe, {}, ctx) is None
    despues_de_tool(informe, {}, ctx, {"status": "ok"})
    assert antes_de_tool(informe, {}, ctx)["bloqueado_por"] == "seguridad"


def test_informe_fallido_no_cuenta(contexto):
    ctx = contexto()
    informe = herramienta("generar_informe")
    antes_de_tool(informe, {}, ctx)
    despues_de_tool(informe, {}, ctx, {"status": "error", "mensaje": "ids inválidos"})
    assert antes_de_tool(informe, {}, ctx) is None


def test_argumento_de_texto_demasiado_largo(contexto):
    r = antes_de_tool(herramienta("consultar_catalogo"), {"producto": "x" * 500}, contexto())
    assert r["bloqueado_por"] == "seguridad" and "producto" in r["mensaje"]


def test_la_lista_de_ids_no_tiene_limite_de_texto(contexto):
    ids = ", ".join(f"RAG-{i:05d}" for i in range(1, 41))
    assert antes_de_tool(herramienta("comparar_precios"), {"producto_ids": ids}, contexto()) is None


def test_canario_en_argumentos_se_bloquea(contexto):
    r = antes_de_tool(herramienta("web_search"), {"query": f"buscar {config.CANARIO}"}, contexto())
    assert r["bloqueado_por"] == "seguridad"


def test_guarda_numeros_y_detecta_datos_simulados(contexto):
    ctx = contexto()
    despues_de_tool(herramienta("comparar_precios"), {}, ctx,
                    {"status": "ok", "precio": 3383.0, "tipo_fuente": "SIMULADA (datos de práctica)"})
    assert 3383.0 in ctx.state[config.CLAVE_NUMEROS]
    assert ctx.state[config.CLAVE_SIMULADOS] is True


def test_web_search_se_recorta_y_marca_sin_guardar_numeros(contexto):
    ctx = contexto()
    web = {"status": "ok", "resultados": [{"title": "T", "href": "https://x", "body": "9999,99 " * 200}]}
    r = despues_de_tool(herramienta("web_search"), {"query": "x"}, ctx, web)
    assert "NO verificado" in r["aviso"]
    assert len(r["resultados"][0]["extracto"]) == config.MAX_LARGO_RESULTADO_WEB
    assert config.CLAVE_NUMEROS not in ctx.state


def test_auditoria_registra_ejecuciones_y_bloqueos(contexto, log_temporal):
    ctx = contexto()
    despues_de_tool(herramienta("consultar_catalogo"), {"marca": "Dove"}, ctx, {"status": "ok"})
    bloqueo = antes_de_tool(herramienta("consultar_catalogo"), {"producto": "x" * 500}, ctx)
    despues_de_tool(herramienta("consultar_catalogo"), {"producto": "x" * 500}, ctx, bloqueo)
    eventos = [json.loads(linea) for linea in log_temporal.read_text(encoding="utf-8").splitlines()]
    assert [e["evento"] for e in eventos] == ["tool_ejecutada", "tool_bloqueada"]
    assert eventos[1]["argumentos"]["producto"].endswith("[recortado]")
