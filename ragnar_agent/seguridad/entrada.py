"""
guardrail de ENTRADA (before_model_callback): mira el mensaje del usuario
ANTES de que le llegue al modelo. controla dos cosas:

  1. que no sea larguisimo
  2. que no sea un intento de "prompt injection", o sea querer cambiar o
     sacarle las reglas al agente (eso se detecta en deteccion.py)

si lo bloqueamos se contesta un mensaje fijo y al modelo NI se lo llama.
los temas que no tienen nada que ver con precios no se filtran aca, eso lo
maneja una regla de las instrucciones (agent.py), porque con una lista de
palabras terminariamos bloqueando hasta un "hola" o un "gracias".

ojo: adk llama a esto en cada paso del turno (tambien despues de cada tool),
pero como el mensaje del usuario es el mismo, siempre da igual.
"""
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from . import config
from .auditoria import registrar
from .deteccion import detectar_inyeccion
from .textos import texto_de_contenido

RESPUESTA_LARGO = (f"Tu mensaje es demasiado largo (máximo {config.MAX_LARGO_MENSAJE} caracteres). "
                   "Acotalo, por ejemplo indicando el producto o la marca a comparar.")
RESPUESTA_INYECCION = ("No puedo cambiar ni revelar mis instrucciones. Puedo ayudarte a investigar "
                       "y comparar precios de productos de Ragnar con la competencia.")


def respuesta_fija(texto: str) -> LlmResponse:
    """arma una respuesta "como si fuera del modelo" pero sin llamarlo"""
    return LlmResponse(content=types.Content(role="model", parts=[types.Part(text=texto)]))


def antes_del_modelo(callback_context, llm_request):
    """si devuelve None sigue todo normal, si devuelve una respuesta esa reemplaza al modelo"""
    texto = texto_de_contenido(callback_context.user_content)
    sesion = callback_context.session.id

    if len(texto) > config.MAX_LARGO_MENSAJE:
        registrar("mensaje_bloqueado", {"sesion": sesion, "motivo": "largo", "largo": len(texto)})
        return respuesta_fija(RESPUESTA_LARGO)

    motivo = detectar_inyeccion(texto)
    if motivo:
        registrar("mensaje_bloqueado", {"sesion": sesion, "motivo": "inyeccion",
                                        "detalle": motivo, "mensaje": texto})
        return respuesta_fija(RESPUESTA_INYECCION)
    return None
