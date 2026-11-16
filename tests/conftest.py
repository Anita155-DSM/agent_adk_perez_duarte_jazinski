"""cosas que comparten todos los tests"""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from google.genai import types

#agregamos la raiz del proyecto al path para poder importar ragnar_agent
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ragnar_agent.seguridad import config


@pytest.fixture(autouse=True)
def log_temporal(tmp_path, monkeypatch):
    """todos los tests escriben la auditoria en una carpeta temporal, asi no ensucian logs/"""
    ruta = tmp_path / "auditoria.jsonl"
    monkeypatch.setattr(config, "LOG_AUDITORIA", ruta)
    return ruta


@pytest.fixture
def contexto():
    """
    arma contextos falsos con lo unico que usan los guardrails:
    state, invocation_id, session.id y user_content.
    """
    def crear(texto: str = "", turno: str = "turno-1", state: dict | None = None):
        mensaje = types.Content(role="user", parts=[types.Part(text=texto)])
        return SimpleNamespace(state={} if state is None else state, invocation_id=turno,
                               session=SimpleNamespace(id="sesion-test"), user_content=mensaje)
    return crear


def herramienta(nombre: str):
    """una tool falsa: los guardrails solo le miran el nombre"""
    return SimpleNamespace(name=nombre)
