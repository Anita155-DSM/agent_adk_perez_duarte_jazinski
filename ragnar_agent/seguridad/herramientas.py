"""
guardrail de ANTES de ejecutar una tool (before_tool_callback).

aca es donde el modelo "toca" el sistema, asi que controlamos:
  1. que no llame tools sin parar en un mismo turno (bucles)
  2. que no haga mas de un informe por turno (son archivos en disco)
  3. que los argumentos de texto no sean larguisimos
  4. que no se le escapen las instrucciones por los argumentos (el canario),
     por ejemplo adentro de una busqueda web

las validaciones propias de cada tool (el formato RAG-00001, el maximo de 40
productos, competidor que no existe, etc.) ya las hace tools.py, aca NO las repetimos.

si bloqueamos, devolvemos un diccionario con la misma forma que usan las tools
({"status": "error", "mensaje": ...}). adk no ejecuta la tool y le pasa eso al
modelo, que se lo explica al usuario como cualquier otro error.
"""
from . import config
from .deteccion import contiene_canario


def bloqueo(mensaje: str) -> dict:
    """la respuesta de bloqueo. 'bloqueado_por' sirve para distinguirla en la auditoria"""
    return {"status": "error", "mensaje": mensaje, "bloqueado_por": "seguridad"}


def _reiniciar_si_es_otro_turno(state, id_turno: str) -> None:
    """los contadores son por turno: vuelven a cero cuando el usuario manda otro mensaje"""
    if state.get(config.CLAVE_TURNO) != id_turno:
        state[config.CLAVE_TURNO] = id_turno
        state[config.CLAVE_LLAMADAS] = 0
        state[config.CLAVE_INFORMES] = 0


def _argumento_demasiado_largo(args: dict):
    """devuelve el nombre del primer argumento de texto que se pasa del limite, o None"""
    for nombre, valor in args.items():
        if nombre == "producto_ids":
            continue  #la lista de ids la controla la propia tool (maximo 40 productos)
        if isinstance(valor, str) and len(valor) > config.MAX_LARGO_ARGUMENTO_TEXTO:
            return nombre
    return None


def antes_de_tool(tool, args, tool_context):
    """si devuelve None la tool se ejecuta normal, si devuelve un dict queda bloqueada"""
    state = tool_context.state
    _reiniciar_si_es_otro_turno(state, tool_context.invocation_id)
    state[config.CLAVE_LLAMADAS] = state[config.CLAVE_LLAMADAS] + 1

    if state[config.CLAVE_LLAMADAS] > config.MAX_LLAMADAS_TOOLS_POR_TURNO:
        return bloqueo(f"Se alcanzó el límite de {config.MAX_LLAMADAS_TOOLS_POR_TURNO} consultas "
                       "por pedido. Respondé con la información que ya tenés o pedile al usuario "
                       "que divida el pedido.")

    if tool.name == "generar_informe" and state[config.CLAVE_INFORMES] >= config.MAX_INFORMES_POR_TURNO:
        return bloqueo("Ya se generó un informe en este pedido. No generes otro: "
                       "usá el informe ya generado.")

    nombre = _argumento_demasiado_largo(args)
    if nombre:
        return bloqueo(f"El argumento '{nombre}' es demasiado largo (máximo "
                       f"{config.MAX_LARGO_ARGUMENTO_TEXTO} caracteres). Usá un filtro más corto.")

    if contiene_canario(str(args)):
        return bloqueo("Los argumentos contienen información interna confidencial.")
    return None
