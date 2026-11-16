"""
las herramientas (tools) del agente de precios de ragnar s.a.

esta es "la carta": cada funcion de aca es una tool que el modelo puede pedir
que se ejecute. el trabajo pesado lo hacen los otros archivos:

    datos.py        -> leer los csv, unidades, monedas y validar ids
    comparacion.py  -> ver si es el mismo producto y hacer las cuentas
    informe.py      -> armar el texto del informe

como lo pensamos:

* las CUENTAS y el CONTROL de si dos productos son el mismo se hacen en codigo,
  nunca en el modelo. asi el agente no puede inventar ni calcular mal.
* todas las tools devuelven un diccionario con la clave "status", que puede ser
  "ok", "sin_resultados" o "error" (con un "mensaje" claro). nunca le tiran una
  excepcion al modelo.
* los docstrings de las tools importan un monton: es lo que lee el modelo para
  decidir cuando usar cada una y que argumentos pasarle. por eso estan escritos
  mas prolijos que el resto.
"""
import re
from datetime import datetime
from pathlib import Path

from .comparacion import _analizar, _fila_producto, _resumen
from .datos import (
    MAX_RESULTADOS,
    _cambios,
    _catalogo,
    _competidores_validos,
    _lista_ids,
    _norm,
    _ofertas,
)
from .informe import _markdown_informe

#donde se guardan los informes
INFORMES_DIR = Path(__file__).resolve().parent.parent.parent / "informes"

MAX_CARACTERES_INFORME = 9000  #cuanto del informe le devolvemos al modelo (el archivo se guarda siempre entero)


def consultar_catalogo(producto: str = "", marca: str = "", categoria: str = "",
                       producto_id: str = "", max_resultados: int = 10) -> dict:
    """
    Consulta el catálogo de productos de Ragnar S.A. (precios en ARS, Argentina).
    Usala SIEMPRE primero para identificar qué productos hay que investigar y
    obtener sus producto_id, que necesitan las demás herramientas.
    Se debe indicar al menos un filtro. Los filtros de texto aceptan coincidencias
    parciales y no distinguen mayúsculas ni acentos.

    Args:
        producto: str -> Nombre del producto, por ejemplo "Detergente" o "Bebida energizante".
        marca: str -> Marca, por ejemplo "Dove".
        categoria: str -> Alimentos, Bebidas, Limpieza, Hogar o Higiene personal.
        producto_id: str -> Identificador exacto, por ejemplo "RAG-00001".
        max_resultados: int -> Cantidad máxima de productos a devolver (1 a 40, por defecto 10).
    """
    try:
        cat = _catalogo()
        #nos quedamos solo con los filtros que vinieron con algo
        filtros = {"producto": producto, "marca": marca, "categoria": categoria, "producto_id": producto_id}
        usados = {k: str(v).strip() for k, v in filtros.items() if v is not None and str(v).strip()}
        if not usados:
            return {"status": "error",
                    "mensaje": "Indicá al menos un filtro: producto, marca, categoria o producto_id.",
                    "categorias_disponibles": sorted(cat["categoria"].unique()),
                    "productos_disponibles": sorted(cat["producto"].unique())}
        try:
            n = int(max_resultados)
        except (TypeError, ValueError):
            return {"status": "error", "mensaje": "max_resultados debe ser un número entero entre 1 y 40."}
        n = max(1, min(n, MAX_RESULTADOS))

        #vamos achicando la seleccion con cada filtro que se uso
        sel = cat
        if "producto_id" in usados:
            sel = sel[sel["producto_id"] == usados["producto_id"].upper()]
        if "producto" in usados:
            sel = sel[sel["n_producto"].str.contains(re.escape(_norm(usados["producto"])))]
        if "marca" in usados:
            sel = sel[sel["n_marca"].str.contains(re.escape(_norm(usados["marca"])))]
        if "categoria" in usados:
            sel = sel[sel["n_categoria"].str.contains(re.escape(_norm(usados["categoria"])))]

        if sel.empty:
            return {"status": "sin_resultados", "filtros": usados,
                    "mensaje": "No hay productos en el catálogo con esos filtros.",
                    "categorias_disponibles": sorted(cat["categoria"].unique()),
                    "productos_disponibles": sorted(cat["producto"].unique())}
        productos = [_fila_producto(r) for r in sel.head(n).itertuples(index=False)]
        return {"status": "ok", "filtros": usados, "total_encontrados": int(len(sel)),
                "mostrando": len(productos), "productos": productos,
                "nota": None if len(sel) <= n else
                f"Hay {len(sel)} productos; se muestran {n}. Acotá con más filtros o subí max_resultados."}
    #pase lo que pase devolvemos un error prolijo, nunca una excepcion
    except Exception as e:
        return {"status": "error", "mensaje": f"Error interno al consultar el catálogo: {e}"}


def listar_competidores() -> dict:
    """
    Lista los supermercados competidores sobre los que hay información de precios,
    con su país, su moneda y el tipo de fuente. Usala cuando el usuario mencione un
    competidor, un país o una región, o cuando necesites saber qué fuentes existen.
    """
    try:
        df = _ofertas()
        comps = []
        for nombre, g in df.groupby("competidor"):
            comps.append({"competidor": nombre, "pais": g["pais"].iloc[0], "moneda": g["moneda"].iloc[0],
                          "productos_publicados": int(len(g)),
                          "tipo_fuente": g["tipo_fuente"].iloc[0],
                          "ultima_fecha_consulta": g["fecha_consulta"].max()})
        return {"status": "ok", "competidores": comps,
                "tipos_de_cambio": [{"moneda": k, "ars_por_unidad": v["ars_por_unidad"], "fecha": v["fecha"],
                                     "fuente": v["fuente"]} for k, v in _cambios().items()]}
    except Exception as e:
        return {"status": "error", "mensaje": f"Error interno al listar competidores: {e}"}


def buscar_precios_competencia(producto_id: str, competidor: str = "") -> dict:
    """
    Busca el precio de UN producto de Ragnar en los supermercados competidores y
    VERIFICA si lo que se encontró es realmente el mismo artículo (producto, marca,
    contenido y unidad). Devuelve por cada competidor: veredicto de coincidencia
    (equivalente, presentacion_diferente, marca_diferente, unidad_incompatible o
    no_encontrado), precio regular, precio promocional (si hay), fuente (URL) y fecha
    de consulta. No calcula diferencias: para eso usá comparar_precios.

    Args:
        producto_id: str -> Id del producto de Ragnar, formato RAG-00001 (sale de consultar_catalogo).
        competidor: str -> Opcional. Nombre del competidor o país para filtrar. Vacío = todos.
    """
    try:
        #primero validamos el id y el competidor, si algo esta mal cortamos aca
        ids, error = _lista_ids([producto_id])
        if error:
            return {"status": "error", "mensaje": error}
        comps, error = _competidores_validos(competidor)
        if error:
            return {"status": "error", "mensaje": error}
        item = _analizar(ids, comps, con_comparacion=False)[0]
        if item["estado"] == "no_encontrado":
            return {"status": "sin_resultados", "producto_ragnar": item["producto_ragnar"],
                    "competidores_consultados": comps, "ofertas": item["ofertas"],
                    "mensaje": "No se encontró este producto en los competidores consultados. "
                               "No se debe inventar un precio."}
        return {"status": "ok", "producto_ragnar": item["producto_ragnar"], "estado": item["estado"],
                "competidores_consultados": comps, "ofertas": item["ofertas"],
                **({"advertencia_catalogo": item["advertencia_catalogo"]} if "advertencia_catalogo" in item else {})}
    except Exception as e:
        return {"status": "error", "mensaje": f"Error interno al buscar precios: {e}"}


def comparar_precios(producto_ids: list[str], competidor: str = "") -> dict:
    """
    Compara los precios de uno o varios productos de Ragnar contra la competencia y
    CALCULA las diferencias absolutas (en ARS) y porcentuales. Solo compara productos
    equivalentes; si la presentación es distinta compara por precio normalizado por
    unidad (por kg, por litro o por unidad) y lo marca como "referencial". Convierte
    monedas a ARS. Separa el precio regular del promocional. Informa además qué
    productos no se encontraron o no tienen una coincidencia confiable.
    Convención: diferencia = precio del competidor - precio de Ragnar.

    Args:
        producto_ids: list[str] -> Ids de productos de Ragnar, formato RAG-00001 (máximo 40).
        competidor: str -> Opcional. Nombre del competidor o país para filtrar. Vacío = todos.
    """
    try:
        ids, error = _lista_ids(producto_ids)
        if error:
            return {"status": "error", "mensaje": error}
        comps, error = _competidores_validos(competidor)
        if error:
            return {"status": "error", "mensaje": error}
        analisis = _analizar(ids, comps, con_comparacion=True)
        resumen = _resumen(analisis)
        #si no se encontro ninguno avisamos con sin_resultados
        if resumen["productos_por_estado"]["no_encontrado"] == len(analisis):
            return {"status": "sin_resultados", "competidores_consultados": comps, "resumen": resumen,
                    "mensaje": "Ninguno de los productos se encontró en los competidores consultados."}
        return {"status": "ok", "competidores_consultados": comps, "resumen": resumen, "productos": analisis}
    except Exception as e:
        return {"status": "error", "mensaje": f"Error interno al comparar precios: {e}"}


def generar_informe(producto_ids: list[str], competidor: str = "",
                    titulo: str = "Informe comparativo de precios") -> dict:
    """
    Genera un informe comparativo en formato Markdown (y lo guarda como archivo en la
    carpeta informes/). Incluye productos analizados, precios, diferencias porcentuales,
    promociones, competidores consultados, fuentes con fecha de consulta, productos no
    encontrados o sin coincidencia confiable, y conclusiones. Usala cuando el usuario
    pida un informe o un reporte.

    Args:
        producto_ids: list[str] -> Ids de productos de Ragnar, formato RAG-00001 (máximo 40).
        competidor: str -> Opcional. Nombre del competidor o país para filtrar. Vacío = todos.
        titulo: str -> Título del informe.
    """
    try:
        ids, error = _lista_ids(producto_ids)
        if error:
            return {"status": "error", "mensaje": error}
        comps, error = _competidores_validos(competidor)
        if error:
            return {"status": "error", "mensaje": error}
        analisis = _analizar(ids, comps, con_comparacion=True)
        resumen = _resumen(analisis)
        md = _markdown_informe(str(titulo).strip() or "Informe comparativo de precios", analisis, resumen, comps)

        #guardamos el archivo, y si no se puede (permisos, disco) devolvemos el informe igual
        ruta, aviso = None, None
        try:
            INFORMES_DIR.mkdir(parents=True, exist_ok=True)
            ruta = INFORMES_DIR / f"informe_{datetime.now():%Y%m%d_%H%M%S}.md"
            ruta.write_text(md, encoding="utf-8")
        except OSError as e:
            aviso = f"No se pudo guardar el archivo ({e}); el informe se devuelve igualmente."
        out = {"status": "ok", "ruta_archivo": None if ruta is None else str(ruta), "resumen": resumen}
        if aviso:
            out["aviso"] = aviso
        #al modelo le mandamos el informe recortado si es muy largo, para no llenarle el contexto
        if len(md) > MAX_CARACTERES_INFORME:
            out["informe_markdown"] = md[:MAX_CARACTERES_INFORME] + "\n\n[...informe recortado; ver archivo completo...]"
            out["informe_recortado"] = True
        else:
            out["informe_markdown"] = md
            out["informe_recortado"] = False
        return out
    except Exception as e:
        return {"status": "error", "mensaje": f"Error interno al generar el informe: {e}"}


def web_search(query: str, max_results: int = 5) -> dict:
    """
    OPCIONAL (solo disponible si ENABLE_WEB_SEARCH=true). Busca información en internet
    con DuckDuckGo. Los resultados son texto de páginas web, NO precios verificados:
    no usar para inventar ni completar precios. Sirve solo como contexto.

    Args:
        query: str -> Consulta de búsqueda.
        max_results: int -> Cantidad máxima de resultados (1 a 10).
    """
    try:
        if not str(query).strip():
            return {"status": "error", "mensaje": "La consulta no puede estar vacía."}
        from ddgs import DDGS  #se importa recien aca porque es opcional, puede no estar instalada
        n = max(1, min(int(max_results), 10))
        resultados = DDGS().text(query=str(query), max_results=n)
        if not resultados:
            return {"status": "sin_resultados", "mensaje": "La búsqueda no devolvió resultados."}
        return {"status": "ok", "fecha_consulta": f"{datetime.now():%Y-%m-%d}", "resultados": resultados}
    except Exception as e:
        return {"status": "error", "mensaje": f"Error en la búsqueda web: {e}"}
