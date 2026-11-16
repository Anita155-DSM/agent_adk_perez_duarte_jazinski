"""
los guardrails del agente, cada callback de adk tiene su archivo:

    entrada.py      -> antes_del_modelo   (before_model_callback): el mensaje del usuario
    herramientas.py -> antes_de_tool      (before_tool_callback):  la tool que quiere llamar el modelo
    resultados.py   -> despues_de_tool    (after_tool_callback):   lo que devolvio la tool
    salida.py       -> despues_del_modelo (after_model_callback):  la respuesta final

y estos son de apoyo:
    config.py     -> limites, listas para detectar y el canario
    deteccion.py  -> detecta prompt injection y el canario (a mano, sin librerias)
    numeros.py    -> lee numeros de un texto y controla que no sean inventados
    textos.py     -> saca el texto de los mensajes de adk
    auditoria.py  -> anota los eventos en logs/auditoria.jsonl
"""
from .entrada import antes_del_modelo
from .herramientas import antes_de_tool
from .resultados import despues_de_tool
from .salida import despues_del_modelo

__all__ = ["antes_del_modelo", "antes_de_tool", "despues_de_tool", "despues_del_modelo"]
