"""
tests del servidor de la interfaz web (servidor.py): los endpoints propios de /api,
los archivos de la interfaz y lo que la interfaz necesita de la api de adk.
no usan ollama ni el modelo.
"""
import io
import json
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import servidor
from ragnar_agent.seguridad import config
from ragnar_agent.seguridad.auditoria import registrar
from ragnar_agent.tools import comparacion, tools

#adk rechaza los pedidos que no vengan de la propia maquina (proteccion contra
#dns rebinding), por eso el cliente de prueba se presenta como 127.0.0.1
BASE = "http://127.0.0.1:8080"
SESIONES = f"/apps/{servidor.NOMBRE_APP}/users/{servidor.USUARIO}/sessions"


@pytest.fixture(scope="module")
def app():
    return servidor.crear_app(uri_sesiones="memory://")  #las sesiones de los tests no se guardan en disco


@pytest.fixture
def cliente(app):
    return TestClient(app, base_url=BASE)


@pytest.fixture
def informes(tmp_path, monkeypatch):
    """una carpeta de informes temporal, con dos informes de distinta fecha"""
    carpeta = tmp_path / "informes"
    carpeta.mkdir()
    viejo = carpeta / "informe_20260101_100000.md"
    nuevo = carpeta / "informe_20260202_100000.md"
    viejo.write_text("# Informe viejo\n\nContenido viejo.", encoding="utf-8")
    nuevo.write_text("\n# Informe nuevo\n\n| a | b |\n|---|---|\n| 1 | 2 |", encoding="utf-8")
    os.utime(viejo, (1_700_000_000, 1_700_000_000))
    os.utime(nuevo, (1_800_000_000, 1_800_000_000))
    (carpeta / "secreto.txt").write_text("no es un informe", encoding="utf-8")
    monkeypatch.setattr(tools, "INFORMES_DIR", carpeta)
    return carpeta


#interfaz y configuracion 
def test_sirve_la_interfaz(cliente):
    r = cliente.get("/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert "Ragnar" in r.text
    for archivo in ("estilos.css", "app.js", "markdown.js", "vidrio.js", "fuentes/krona-one.woff2"):
        assert cliente.get(f"/{archivo}").status_code == 200, archivo


def test_config_refleja_el_agente_y_los_guardrails(cliente):
    datos = cliente.get("/api/config").json()
    assert datos["app"] == "ragnar_agent" and datos["usuario"] == servidor.USUARIO
    assert datos["max_largo_mensaje"] == config.MAX_LARGO_MENSAJE
    assert datos["umbral_significativo"] == comparacion.UMBRAL_SIGNIFICATIVO
    assert {"consultar_catalogo", "comparar_precios", "generar_informe"} <= set(datos["herramientas"])
    assert datos["modelo"]


def test_competidores_usa_la_herramienta_del_agente(cliente):
    datos = cliente.get("/api/competidores").json()
    assert datos == tools.listar_competidores()
    assert datos["status"] == "ok" and len(datos["competidores"]) == 4


#informes
def test_lista_informes_del_mas_nuevo_al_mas_viejo(cliente, informes):
    lista = cliente.get("/api/informes").json()
    assert [i["nombre"] for i in lista] == ["informe_20260202_100000.md", "informe_20260101_100000.md"]
    assert [i["titulo"] for i in lista] == ["Informe nuevo", "Informe viejo"]  #secreto.txt no tiene que aparecer en la lista


def test_lee_un_informe(cliente, informes):
    r = cliente.get("/api/informes/informe_20260202_100000.md")
    assert r.status_code == 200
    assert r.json()["titulo"] == "Informe nuevo" and "| 1 | 2 |" in r.json()["markdown"]


def test_sin_carpeta_de_informes_devuelve_lista_vacia(cliente, tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "INFORMES_DIR", tmp_path / "no_existe")
    assert cliente.get("/api/informes").json() == []


@pytest.mark.parametrize("nombre", ["secreto.txt", "..md", "informe con espacios.md", "informe.md.txt"])
def test_rechaza_nombres_de_informe_invalidos(cliente, informes, nombre):
    assert cliente.get(f"/api/informes/{nombre}").status_code == 400


@pytest.mark.parametrize("ruta", ["..%2F..%2FREADME.md", "%2e%2e%2fsecreto.txt", "..%5C..%5CREADME.md"])
def test_no_se_puede_salir_de_la_carpeta_de_informes(cliente, informes, ruta):
    r = cliente.get(f"/api/informes/{ruta}")
    assert r.status_code in (400, 404)
    assert "no es un informe" not in r.text and "Agente inteligente" not in r.text


def test_informe_inexistente(cliente, informes):
    assert cliente.get("/api/informes/informe_que_no_esta.md").status_code == 404


#auditoria
def test_auditoria_devuelve_los_ultimos_eventos_primero(cliente, log_temporal):
    for i in range(1, 4):
        registrar("tool_ejecutada", {"sesion": "s", "tool": f"tool_{i}"})
    with open(log_temporal, "a", encoding="utf-8") as archivo:
        archivo.write("esto no es JSON\n")  #una linea rota no tiene que romper la vista
    registrar("mensaje_bloqueado", {"sesion": "s", "motivo": "largo", "largo": 900})

    eventos = cliente.get("/api/auditoria?limite=2").json()
    assert [e["evento"] for e in eventos] == ["mensaje_bloqueado", "tool_ejecutada"]
    assert eventos[1]["tool"] == "tool_3"
    assert len(cliente.get("/api/auditoria").json()) == 4


def test_auditoria_sin_registro_y_limite_invalido(cliente):
    assert cliente.get("/api/auditoria").json() == []  #el log temporal todavia no existe
    assert cliente.get("/api/auditoria?limite=0").status_code == 422
    assert cliente.get("/api/auditoria?limite=99999").status_code == 422


#estado de ollama 
def _ollama_falso(monkeypatch, modelos):
    cuerpo = json.dumps({"models": [{"name": m} for m in modelos]}).encode()
    monkeypatch.setattr(servidor.urllib.request, "urlopen", lambda url, timeout: io.BytesIO(cuerpo))


def test_estado_con_el_modelo_descargado(monkeypatch):
    _ollama_falso(monkeypatch, ["otro:latest", "gemma4:31b-cloud"])
    assert servidor._estado_ollama("gemma4:31b-cloud") == {
        "ollama": "ok", "modelo": "gemma4:31b-cloud", "modelo_disponible": True}
    assert servidor._estado_ollama("otro")["modelo_disponible"] is True  #"otro" es lo mismo que "otro:latest"


def test_estado_sin_el_modelo(monkeypatch):
    _ollama_falso(monkeypatch, [])
    estado = servidor._estado_ollama("gemma4:31b-cloud")
    assert estado["ollama"] == "ok" and estado["modelo_disponible"] is False


def test_estado_sin_ollama(cliente, monkeypatch):
    def sin_conexion(url, timeout):
        raise OSError("conexión rechazada")
    monkeypatch.setattr(servidor.urllib.request, "urlopen", sin_conexion)
    estado = cliente.get("/api/estado").json()
    assert estado["ollama"] == "sin_conexion" and estado["modelo_disponible"] is False


def test_estado_no_consulta_urls_que_no_son_http(monkeypatch):
    monkeypatch.setenv("OLLAMA_API_BASE", "file:///C:/Windows")
    monkeypatch.setattr(servidor.urllib.request, "urlopen",
                        lambda url, timeout: pytest.fail("no debería abrir esa URL"))
    assert servidor._estado_ollama("m")["ollama"] == "sin_conexion"


# lo que la interfaz necesita de la api de adk
def test_la_sesion_guarda_el_titulo_y_se_puede_borrar(cliente):
    sesion = cliente.post(SESIONES, json={"state": {"titulo": "Compará los Dove"}}).json()
    listadas = {s["id"]: s for s in cliente.get(SESIONES).json()}
    assert listadas[sesion["id"]]["state"]["titulo"] == "Compará los Dove"

    assert cliente.get(f"{SESIONES}/{sesion['id']}").json()["events"] == []
    assert cliente.delete(f"{SESIONES}/{sesion['id']}").status_code == 200
    assert cliente.get(f"{SESIONES}/{sesion['id']}").status_code == 404


def test_run_sse_con_sesion_inexistente_devuelve_404(cliente):
    r = cliente.post("/run_sse", json={
        "app_name": servidor.NOMBRE_APP, "user_id": servidor.USUARIO, "session_id": "no-existe",
        "streaming": False, "new_message": {"role": "user", "parts": [{"text": "hola"}]}})
    assert r.status_code == 404


def test_rechaza_pedidos_de_otros_sitios(cliente):
    assert cliente.get("/api/config", headers={"Origin": "http://sitio-ajeno.example"}).status_code == 403
    assert cliente.get("/api/config", headers={"Host": "sitio-ajeno.example"}).status_code == 403
