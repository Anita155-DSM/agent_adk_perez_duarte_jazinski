"""
guardrail de SALIDA (after_model_callback): mira la respuesta final del modelo
ANTES de que la vea el usuario.

  1. fuga de instrucciones: si aparece el canario, el modelo esta contando sus
     instrucciones. es lo unico que se BLOQUEA: se reemplaza toda la respuesta
  2. numeros sin respaldo: cada numero con decimales (precios, porcentajes) tiene
     que haber salido de alguna tool o del mensaje del usuario
  3. aviso de datos simulados: si se usaron datos SIMULADOS y la respuesta no lo
     dice, se lo agregamos

en el 2 y el 3 la respuesta NO se bloquea (el control de numeros es aproximado
y puede dar falsas alarmas): se le agrega una nota visible al final y se anota.
"""
from google.genai import types

from . import config
from .auditoria import registrar
from .deteccion import contiene_canario
from .numeros import numeros_de_resultado, numeros_en_texto, numeros_sin_respaldo
from .textos import normalizar, partes_de_texto, texto_de_contenido

MAX_NUMEROS_EN_NOTA = 5
AVISO_SIMULADOS = "ℹ️ Los precios de la competencia son datos SIMULADOS de práctica."
RESPUESTA_FUGA = ("No puedo compartir mis instrucciones internas. Puedo ayudarte a investigar "
                  "y comparar precios de productos de Ragnar con la competencia.")


def _nota_numeros(sospechosos: list[str]) -> str:
    lista = ", ".join(sospechosos[:MAX_NUMEROS_EN_NOTA])
    return (f"⚠️ Verificá: algunos valores no coinciden con los datos consultados ({lista}). "
            "Pedí que se recalculen con las herramientas antes de usarlos.")


def _es_respuesta_final(llm_response) -> bool:
    """solo revisamos el texto final, no los pedacitos del streaming ni los pedidos de tools"""
    contenido = llm_response.content
    if llm_response.partial or contenido is None or not contenido.parts:
        return False
    return not any(p.function_call for p in contenido.parts)


def despues_del_modelo(callback_context, llm_response):
    """si devuelve None la respuesta queda como esta, si no devuelve la respuesta modificada"""
    if not _es_respuesta_final(llm_response):
        return None
    texto = texto_de_contenido(llm_response.content)
    if not texto.strip():
        return None

    if contiene_canario(texto):
        registrar("respuesta_bloqueada", {"sesion": callback_context.session.id,
                                          "motivo": "fuga_de_instrucciones"})
        llm_response.content.parts = [types.Part(text=RESPUESTA_FUGA)]
        return llm_response

    state = callback_context.state
    validos = state.get(config.CLAVE_NUMEROS, []) + numeros_de_resultado(
        texto_de_contenido(callback_context.user_content))
    notas = []

    sospechosos = numeros_sin_respaldo(texto, validos)
    if sospechosos:
        notas.append(_nota_numeros(sospechosos))
        registrar("respuesta_corregida", {"sesion": callback_context.session.id,
                                          "motivo": "numeros_sin_respaldo", "numeros": sospechosos})

    menciona_valores = any(decimales > 0 for _, _, decimales in numeros_en_texto(texto))
    if state.get(config.CLAVE_SIMULADOS) and menciona_valores and "simulad" not in normalizar(texto):
        notas.append(AVISO_SIMULADOS)

    if not notas:
        return None
    ultima_parte = partes_de_texto(llm_response.content)[-1]
    ultima_parte.text += "\n\n" + "\n\n".join(notas)
    return llm_response
