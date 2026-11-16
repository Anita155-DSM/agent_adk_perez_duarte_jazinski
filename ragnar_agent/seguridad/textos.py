"""cositas de texto que usan todos los guardrails"""
from ..tools.datos import _norm as normalizar  #pasa a minusculas, saca tildes y espacios de mas

__all__ = ["normalizar", "texto_de_contenido", "partes_de_texto"]


def partes_de_texto(contenido) -> list:
    """se queda con las partes del mensaje que son texto visible (los "pensamientos" del modelo no)"""
    if contenido is None or not contenido.parts:
        return []
    return [p for p in contenido.parts if p.text and not p.thought]


def texto_de_contenido(contenido) -> str:
    """junta todo el texto visible de un mensaje de adk (types.Content)"""
    return "\n".join(p.text for p in partes_de_texto(contenido))
