#las 6 tools del agente, asi agent.py las sigue importando con "from .tools import ..."
from .tools import (
    buscar_precios_competencia,
    comparar_precios,
    consultar_catalogo,
    generar_informe,
    listar_competidores,
    web_search,
)

__all__ = [
    "buscar_precios_competencia",
    "comparar_precios",
    "consultar_catalogo",
    "generar_informe",
    "listar_competidores",
    "web_search",
]
