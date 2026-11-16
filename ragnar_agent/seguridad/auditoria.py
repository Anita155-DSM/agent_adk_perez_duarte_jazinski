"""
la auditoria: cada cosa que pasa con la seguridad se anota como una linea json
en logs/auditoria.jsonl, asi despues se puede leer facil.

si no se puede escribir el archivo no pasa nada, seguimos igual: la auditoria
nunca tiene que romper al agente.
"""
import json
from datetime import datetime

from . import config


def _recortar(valor):
    """corta los textos largos, tambien si vienen adentro de listas o diccionarios"""
    if isinstance(valor, str) and len(valor) > config.MAX_LARGO_DATO_LOG:
        return valor[:config.MAX_LARGO_DATO_LOG] + "...[recortado]"
    if isinstance(valor, dict):
        return {k: _recortar(v) for k, v in valor.items()}
    if isinstance(valor, list):
        return [_recortar(v) for v in valor]
    return valor


def registrar(evento: str, datos: dict) -> None:
    """agrega un evento al final del archivo de auditoria"""
    linea = {"fecha": datetime.now().isoformat(timespec="seconds"), "evento": evento, **_recortar(datos)}
    try:
        config.LOG_AUDITORIA.parent.mkdir(parents=True, exist_ok=True)
        with open(config.LOG_AUDITORIA, "a", encoding="utf-8") as archivo:
            archivo.write(json.dumps(linea, ensure_ascii=False, default=str) + "\n")
    except OSError:
        pass  #preferimos quedarnos sin registro antes que sin agente
