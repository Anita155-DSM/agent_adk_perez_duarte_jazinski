"""
la interfaz web propia del agente de precios de ragnar s.a.

levanta todo en un solo proceso:
  - la api de adk (las sesiones y /run_sse), la misma que usa `adk web`
  - unos pocos endpoints de SOLO LECTURA en /api (informes, competidores, auditoria)
  - los archivos de la carpeta interfaz/ (html, css y js, sin compilar nada)

para correrlo hay que pararse en esta carpeta (la que CONTIENE a ragnar_agent) y tirar:
    python servidor.py
y abrir http://127.0.0.1:8080
"""
import json
import os
import re
import urllib.request
from collections import deque
from datetime import datetime
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from google.adk.cli.fast_api import get_fast_api_app
from google.adk.cli.utils import envs

RAIZ = Path(__file__).resolve().parent
INTERFAZ_DIR = RAIZ / "interfaz"
NOMBRE_APP = "ragnar_agent"  #el nombre de la carpeta del agente, asi lo reconoce adk
USUARIO = "area_comercial"  #no hay login, asi que hay un solo usuario

#escuchamos solo en esta misma maquina porque adk no tiene autenticacion
HOST = "127.0.0.1"
PUERTO = int(os.getenv("PUERTO_INTERFAZ", "8080"))

MAX_EVENTOS_AUDITORIA = 500
#a los informes los nombra el codigo (informe_AAAAMMDD_HHMMSS.md). solo aceptamos un nombre
#de archivo simple, asi nadie puede pedir "../../otro_archivo"
_NOMBRE_INFORME = re.compile(r"^[A-Za-z0-9_\-]+\.md$")


def _titulo_de_informe(ruta: Path) -> str:
    """el titulo es la primera linea "# ..." del markdown"""
    try:
        with open(ruta, encoding="utf-8") as archivo:
            for linea in archivo:
                if linea.startswith("# "):
                    return linea[2:].strip()
                if linea.strip():
                    break
    except OSError:
        pass
    return ruta.stem


def _estado_ollama(modelo: str) -> dict:
    """le pregunta a ollama si esta prendido y si tiene descargado el modelo del agente"""
    base = os.getenv("OLLAMA_API_BASE", "http://localhost:11434").rstrip("/")
    if not base.startswith(("http://", "https://")):
        return {"ollama": "sin_conexion", "modelo": modelo, "modelo_disponible": False}
    try:
        with urllib.request.urlopen(f"{base}/api/tags", timeout=2) as respuesta:
            nombres = {m.get("name", "") for m in json.load(respuesta).get("models", [])}
    except (OSError, ValueError):
        return {"ollama": "sin_conexion", "modelo": modelo, "modelo_disponible": False}
    disponible = modelo in nombres or f"{modelo}:latest" in nombres
    return {"ollama": "ok", "modelo": modelo, "modelo_disponible": disponible}


def crear_app(agents_dir: Path = RAIZ, uri_sesiones: str | None = None) -> FastAPI:
    """
    arma la aplicacion: api de adk + endpoints propios + archivos de la interfaz.

    uri_sesiones: donde guarda adk las conversaciones. si no le pasamos nada usa lo
    mismo que `adk web`; los tests le pasan "memory://" para no escribir en disco.
    """
    #el .env del agente se carga ANTES de importarlo: agent.py lee MODELO_OLLAMA apenas
    #se importa, y si lo importamos primero queda fijo el modelo por defecto
    envs.load_dotenv_for_agent(NOMBRE_APP, str(agents_dir))
    from ragnar_agent import agent as agente
    from ragnar_agent import tools
    from ragnar_agent.seguridad import config

    #web=False: queremos solo la api, sin la pantalla de desarrollo de adk.
    #bind_host activa la proteccion de adk contra dns rebinding
    app = get_fast_api_app(agents_dir=str(agents_dir), web=False, bind_host=HOST,
                           session_service_uri=uri_sesiones)

    @app.get("/api/config")
    def configuracion() -> dict:
        """los datos fijos que necesita la interfaz para armarse"""
        return {
            "app": NOMBRE_APP,
            "usuario": USUARIO,
            "modelo": agente.MODELO,
            "herramientas": [h.__name__ for h in agente.herramientas],
            "max_largo_mensaje": config.MAX_LARGO_MENSAJE,
            "umbral_significativo": tools.UMBRAL_SIGNIFICATIVO,
        }

    #le dice a la interfaz si ollama esta andando y si el modelo esta descargado
    @app.get("/api/estado")
    def estado() -> dict:
        return _estado_ollama(agente.MODELO)

    @app.get("/api/competidores")
    def competidores() -> dict:
        #usamos la misma tool que el agente, asi la interfaz no repite logica
        return tools.listar_competidores()

    @app.get("/api/informes")
    def informes() -> list[dict]:
        """los informes guardados, del mas nuevo al mas viejo"""
        carpeta = tools.INFORMES_DIR
        if not carpeta.is_dir():
            return []
        archivos = sorted(carpeta.glob("*.md"), key=lambda r: r.stat().st_mtime, reverse=True)
        return [{"nombre": r.name,
                 "titulo": _titulo_de_informe(r),
                 "modificado": datetime.fromtimestamp(r.stat().st_mtime).isoformat(timespec="seconds"),
                 "bytes": r.stat().st_size} for r in archivos]

    #devuelve un informe puntual, validando antes el nombre que nos piden
    @app.get("/api/informes/{nombre}")
    def informe(nombre: str) -> dict:
        if not _NOMBRE_INFORME.fullmatch(nombre):
            raise HTTPException(status_code=400, detail="Nombre de informe inválido.")
        ruta = tools.INFORMES_DIR / nombre
        if not ruta.is_file():
            raise HTTPException(status_code=404, detail="No existe ese informe.")
        return {"nombre": nombre, "titulo": _titulo_de_informe(ruta),
                "markdown": ruta.read_text(encoding="utf-8")}

    @app.get("/api/auditoria")
    def auditoria(limite: int = Query(default=200, ge=1, le=MAX_EVENTOS_AUDITORIA)) -> list[dict]:
        """los ultimos eventos de logs/auditoria.jsonl, del mas nuevo al mas viejo"""
        ruta = config.LOG_AUDITORIA
        if not ruta.is_file():
            return []
        eventos = deque(maxlen=limite)  #se queda solo con las ultimas `limite` lineas
        with open(ruta, encoding="utf-8") as archivo:
            for linea in archivo:
                try:
                    evento = json.loads(linea)
                except ValueError:
                    continue  #si hay una linea rota la salteamos, no tiene que romper la vista
                if isinstance(evento, dict):
                    eventos.append(evento)
        return list(reversed(eventos))

    #esto va ultimo: "/" agarra todo lo que no coincidio con una ruta de arriba
    app.mount("/", StaticFiles(directory=INTERFAZ_DIR, html=True), name="interfaz")
    return app


if __name__ == "__main__":
    print(f"Interfaz de Ragnar en http://{HOST}:{PUERTO}")
    uvicorn.run(crear_app(), host=HOST, port=PUERTO)
