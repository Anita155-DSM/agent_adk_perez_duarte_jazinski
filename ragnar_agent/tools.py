"""
las herramientas (tools) del agente de precios de ragnar s.a.

cada funcion publica de aca es una tool que el modelo puede pedir que se ejecute.
como lo pensamos:

* las CUENTAS (diferencias, porcentajes, precio por kilo o litro, pasar de una
  moneda a otra) y el CONTROL de si dos productos son el mismo se hacen aca, en
  codigo, nunca en el modelo. asi el agente no puede inventar ni calcular mal.
* todas las tools devuelven un diccionario con la clave "status", que puede ser
  "ok", "sin_resultados" o "error" (con un "mensaje" claro). nunca le tiran una
  excepcion al modelo.
* los docstrings de las tools importan un monton: es lo que lee el modelo para
  decidir cuando usar cada una y que argumentos pasarle. por eso esos estan
  escritos mas prolijos que el resto.
"""
import math
import os
import re
import unicodedata
from datetime import datetime
from functools import lru_cache
from pathlib import Path

import pandas as pd

#las carpetas: donde estan los csv y donde se guardan los informes
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
INFORMES_DIR = BASE_DIR.parent / "informes"

UMBRAL_SIGNIFICATIVO = 10.0  #desde que % decimos que una diferencia es "significativa"
MAX_RESULTADOS = 40  #cuantos productos como mucho por consulta
MAX_CARACTERES_INFORME = 9000  #cuanto del informe le devolvemos al modelo (el archivo se guarda siempre entero)

#los ids del catalogo tienen esta forma: RAG- y 5 numeros
_ID_VALIDO = re.compile(r"^RAG-\d{5}$")

#para pasar todo a una misma unidad: unidad -> (unidad base, por cuanto se multiplica)
_UNIDADES = {
    "g": ("g", 1), "gr": ("g", 1), "kg": ("g", 1000),
    "ml": ("ml", 1), "cc": ("ml", 1), "l": ("ml", 1000), "lt": ("ml", 1000),
    "un": ("un", 1), "u": ("un", 1),
}


#----- funciones internas (el modelo no las ve) -----
def _norm(texto) -> str:
    """pasa a minusculas, saca tildes y espacios de mas, asi se pueden comparar textos"""
    t = unicodedata.normalize("NFKD", str(texto))
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", t.lower()).strip()


#los csv se leen una sola vez y quedan en memoria (eso hace lru_cache)
@lru_cache(maxsize=1)
def _catalogo() -> pd.DataFrame:
    df = pd.read_csv(DATA_DIR / "catalogo_ragnar.csv")
    #columnas extra con el texto ya normalizado, para buscar sin problemas de tildes o mayusculas
    df["n_producto"] = df["producto"].map(_norm)
    df["n_marca"] = df["marca"].map(_norm)
    df["n_categoria"] = df["categoria"].map(_norm)
    return df


@lru_cache(maxsize=1)
def _ofertas() -> pd.DataFrame:
    df = pd.read_csv(DATA_DIR / "ofertas_competencia.csv", keep_default_na=False)
    df["n_producto"] = df["producto"].map(_norm)
    df["n_marca"] = df["marca"].map(_norm)
    return df


@lru_cache(maxsize=1)
def _cambios() -> dict:
    df = pd.read_csv(DATA_DIR / "tipos_de_cambio.csv")
    return {r.moneda.upper(): r._asdict() for r in df.itertuples(index=False)}


def _a_base(contenido, unidad):
    """pasa (contenido, unidad) a (cantidad en la unidad base, unidad base). ej: 1.5 kg -> (1500, "g")"""
    u = _UNIDADES.get(_norm(unidad))
    try:
        c = float(contenido)
    except (TypeError, ValueError):
        return None
    if u is None or not math.isfinite(c) or c <= 0:
        return None
    return c * u[1], u[0]


def _unitario(precio, contenido, unidad):
    """el precio "normalizado": por kg, por litro o por unidad. devuelve (valor, etiqueta)"""
    b = _a_base(contenido, unidad)
    if b is None or precio is None:
        return None
    cantidad, base = b
    if base == "g":
        return precio / cantidad * 1000, "por kg"
    if base == "ml":
        return precio / cantidad * 1000, "por litro"
    return precio / cantidad, "por unidad"


def _a_ars(precio, moneda):
    """pasa un precio a pesos con el tipo de cambio cargado. si no hay tipo de cambio devuelve None"""
    fila = _cambios().get(str(moneda).upper())
    if fila is None or precio is None:
        return None
    return precio * fila["ars_por_unidad"]


#redondea, pero si viene None lo deja en None
def _r(x, n=2):
    return None if x is None else round(float(x), n)


def _fmt(v) -> str:
    """muestra la plata a la argentina: 1.234,56"""
    if v is None:
        return "-"
    return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


#muestra el contenido sin el .0 de mas (750 en vez de 750.0)
def _fmt_cont(c) -> str:
    c = float(c)
    return str(int(c)) if c == int(c) else f"{c:g}"


def _lista_ids(producto_ids):
    """revisa y acomoda una lista de ids. devuelve (ids, error): si esta todo bien error es None"""
    #a veces el modelo manda los ids en un solo texto separado por comas, lo aceptamos igual
    if isinstance(producto_ids, str):
        producto_ids = [p for p in re.split(r"[,\s;]+", producto_ids) if p]
    if not isinstance(producto_ids, (list, tuple)) or len(producto_ids) == 0:
        return None, ("Debés indicar al menos un producto_id (formato RAG-00001). "
                      "Podés obtenerlos con la herramienta consultar_catalogo.")
    ids, invalidos = [], []
    for p in producto_ids:
        pid = str(p).strip().upper()
        if not _ID_VALIDO.match(pid):
            invalidos.append(str(p))
        elif pid not in ids:
            ids.append(pid)
    if invalidos:
        return None, f"IDs con formato inválido: {invalidos}. El formato correcto es RAG-00001."
    if len(ids) > MAX_RESULTADOS:
        return None, (f"Se pidieron {len(ids)} productos y el máximo por consulta es "
                      f"{MAX_RESULTADOS}. Acotá el pedido o dividilo en lotes.")
    existentes = set(_catalogo()["producto_id"])
    faltan = [i for i in ids if i not in existentes]
    if faltan:
        return None, f"Estos productos no existen en el catálogo de Ragnar: {faltan}."
    return ids, None


def _competidores_validos(filtro):
    """interpreta el filtro de competidor, que puede ser un nombre o un pais. devuelve (lista, error)"""
    df = _ofertas()
    nombres = sorted(df["competidor"].unique())
    if filtro is None or str(filtro).strip() == "":
        return nombres, None
    f = _norm(filtro)
    por_nombre = [n for n in nombres if _norm(n) == f]
    if por_nombre:
        return por_nombre, None
    por_pais = sorted(df[df["pais"].map(_norm) == f]["competidor"].unique())
    if por_pais:
        return por_pais, None
    paises = sorted(df["pais"].unique())
    return None, (f"No existe el competidor o país '{filtro}'. "
                  f"Competidores disponibles: {nombres}. Países: {paises}.")


#----- lo mas importante: ver si dos productos son el mismo y comparar precios -----
#arma el diccionario de un producto de ragnar tal como se lo devolvemos al modelo
def _fila_producto(row) -> dict:
    unit = _unitario(row.precio, row.contenido, row.unidad_medida)
    return {
        "producto_id": row.producto_id, "producto": row.producto, "marca": row.marca,
        "categoria": row.categoria, "presentacion": row.presentacion,
        "contenido": row.contenido, "unidad": row.unidad_medida,
        "precio": _r(row.precio), "moneda": row.moneda, "pais": row.pais,
        "precio_unitario": None if unit is None else {"valor": _r(unit[0]), "unidad": unit[1]},
    }


def _advertencia_duplicado(row):
    """el catalogo trae productos identicos con precios muy distintos, si pasa avisamos"""
    cat = _catalogo()
    gemelos = cat[(cat["n_producto"] == row.n_producto) & (cat["n_marca"] == row.n_marca)
                  & (cat["contenido"] == row.contenido)
                  & (cat["unidad_medida"] == row.unidad_medida)
                  & (cat["producto_id"] != row.producto_id)]
    if gemelos.empty:
        return None
    precios = list(gemelos["precio"]) + [row.precio]
    if max(precios) / max(min(precios), 1e-9) < 1.2:
        return None
    return (f"Atención: el catálogo de Ragnar tiene {len(gemelos)} producto(s) más con las mismas "
            f"características (ids: {list(gemelos['producto_id'])}) y precios distintos "
            f"(entre {_fmt(min(precios))} y {_fmt(max(precios))}). Revisar el catálogo.")


def _clasificar(prod_base, prod_marca, o):
    """
    controla si una oferta de la competencia es el mismo articulo que el de ragnar.
    devuelve (veredicto, ranking, distancia), o None si no tiene nada que ver.
    el ranking sirve para quedarnos con la mejor: 0 es la equivalente, 3 la peor.
    """
    base_o = _a_base(o.contenido, o.unidad)
    misma_marca = (o.n_marca == prod_marca)
    #si las unidades no se pueden comparar (ej: gramos contra mililitros) no hay forma
    if prod_base is None or base_o is None or base_o[1] != prod_base[1]:
        return ("unidad_incompatible", 3, 0.0) if misma_marca else None
    igual_cant = math.isclose(base_o[0], prod_base[0], rel_tol=1e-6)
    #misma marca y misma cantidad: es el mismo producto
    if misma_marca and igual_cant:
        return "equivalente", 0, 0.0
    #misma marca pero otro tamaño: sirve solo como referencia (cuanto mas parecido el tamaño, mejor)
    if misma_marca:
        return "presentacion_diferente", 1, abs(math.log(base_o[0] / prod_base[0]))
    #misma cantidad pero otra marca: no se compara
    if igual_cant:
        return "marca_diferente", 2, 0.0
    return None


#la explicacion de cada veredicto, va en el resultado para que el modelo se la cuente al usuario
_MOTIVOS = {
    "equivalente": "Mismo producto, marca, contenido y unidad.",
    "presentacion_diferente": ("Misma marca y producto pero otra presentación/cantidad: NO es el mismo "
                               "artículo. Solo es comparable por precio normalizado por unidad (referencial)."),
    "marca_diferente": "Producto similar pero de OTRA marca: no es equivalente, no se compara.",
    "unidad_incompatible": "Unidades de medida incompatibles: no se puede comparar.",
    "no_encontrado": "El competidor no tiene este producto en la fuente consultada.",
}


def _armar_oferta(competidor_nombre, veredicto: str, o) -> dict:
    """arma los datos de una oferta (todavia sin las cuentas de la comparacion)"""
    if o is None:
        return {"competidor": competidor_nombre, "veredicto": veredicto, "motivo": _MOTIVOS[veredicto]}
    #pasamos todo a pesos: el precio regular y el de promo si tiene
    reg_ars = _a_ars(o.precio, o.moneda)
    promo_orig = float(o.precio_promocional) if str(o.precio_promocional) != "" else None
    promo_ars = _a_ars(promo_orig, o.moneda) if promo_orig is not None else None
    unit_reg = _unitario(reg_ars, o.contenido, o.unidad)
    unit_promo = _unitario(promo_ars, o.contenido, o.unidad) if promo_ars is not None else None
    cambio = _cambios().get(str(o.moneda).upper())
    motivo = _MOTIVOS[veredicto]
    if reg_ars is None:
        motivo += f" Falta tipo de cambio para {o.moneda}: no se puede convertir a ARS."
    return {
        "competidor": o.competidor, "pais": o.pais, "veredicto": veredicto, "motivo": motivo,
        "producto_publicado": f"{o.producto} {o.marca} {_fmt_cont(o.contenido)} {o.unidad}",
        "marca_publicada": o.marca,
        "presentacion_publicada": f"{_fmt_cont(o.contenido)} {o.unidad}",
        "precio_regular": {"valor_original": _r(o.precio), "moneda": o.moneda, "valor_ars": _r(reg_ars)},
        "precio_promocional": None if promo_orig is None else
            {"valor_original": _r(promo_orig), "moneda": o.moneda, "valor_ars": _r(promo_ars)},
        "promocion": o.promocion or None,
        "precio_unitario_regular_ars": None if unit_reg is None else _r(unit_reg[0]),
        "precio_unitario_promocional_ars": None if unit_promo is None else _r(unit_promo[0]),
        "unidad_precio": None if unit_reg is None else unit_reg[1],
        "tipo_cambio": None if (cambio is None or str(o.moneda).upper() == "ARS") else
            {"ars_por_unidad": cambio["ars_por_unidad"], "fecha": cambio["fecha"], "fuente": cambio["fuente"]},
        "fuente_url": o.fuente_url, "fecha_consulta": o.fecha_consulta, "tipo_fuente": o.tipo_fuente,
    }


def _ofertas_de_producto(prod_row, competidores: list) -> list:
    """devuelve una oferta por cada competidor consultado: la que mejor coincide"""
    prod_base = _a_base(prod_row.contenido, prod_row.unidad_medida)
    candidatas = _ofertas()
    #primero nos quedamos con las ofertas que tienen el mismo nombre de producto
    candidatas = candidatas[(candidatas["n_producto"] == prod_row.n_producto)
                            & (candidatas["competidor"].isin(competidores))]
    resultado = []
    for comp in competidores:
        mejor = None
        for o in candidatas[candidatas["competidor"] == comp].itertuples(index=False):
            c = _clasificar(prod_base, prod_row.n_marca, o)
            if c is None:
                continue
            #nos quedamos con la de mejor ranking, y si empatan con la de tamaño mas parecido
            clave = (c[1], c[2])
            if mejor is None or clave < mejor[0]:
                mejor = (clave, c[0], o)
        if mejor is None:
            resultado.append(_armar_oferta(comp, "no_encontrado", None))
        else:
            resultado.append(_armar_oferta(None, mejor[1], mejor[2]))
    return resultado


#la convencion que usamos siempre: diferencia = competidor - ragnar (positiva = ragnar mas barato)
def _comparar_valores(ragnar, competidor):
    dif = competidor - ragnar
    pct = dif / ragnar * 100 if ragnar else None
    return dif, pct


def _comparacion(prod_row, oferta: dict):
    """hace las cuentas de la diferencia. solo si la oferta es equivalente o de otra presentacion"""
    ver = oferta["veredicto"]
    if ver not in ("equivalente", "presentacion_diferente"):
        return None
    #mismo producto: comparamos el precio total
    if ver == "equivalente":
        base = "precio_total"
        r = float(prod_row.precio)
        c = oferta["precio_regular"]["valor_ars"]
        cp = None if oferta["precio_promocional"] is None else oferta["precio_promocional"]["valor_ars"]
    #otro tamaño: comparamos el precio por kg / litro / unidad
    else:
        base = "precio_unitario"
        u = _unitario(prod_row.precio, prod_row.contenido, prod_row.unidad_medida)
        r = None if u is None else u[0]
        c = oferta["precio_unitario_regular_ars"]
        cp = oferta["precio_unitario_promocional_ars"]
    if r is None or c is None:
        return None
    dif, pct = _comparar_valores(r, c)
    #si la diferencia es casi cero los damos por iguales
    if abs(pct) < 0.005:
        posicion = "iguales"
    elif dif > 0:
        posicion = "ragnar_mas_barato"
    else:
        posicion = "ragnar_mas_caro"
    comp = {
        "base_de_comparacion": base,
        "referencial": ver == "presentacion_diferente",
        "precio_ragnar": _r(r), "precio_competidor_regular": _r(c),
        "diferencia_absoluta_ars": _r(dif), "diferencia_porcentual": _r(pct),
        "posicion": posicion,
        "diferencia_significativa": abs(pct) >= UMBRAL_SIGNIFICATIVO,
    }
    #si hay promo, calculamos tambien la diferencia contra el precio promocional
    if cp is not None:
        dif_p, pct_p = _comparar_valores(r, cp)
        comp["precio_competidor_promocional"] = _r(cp)
        comp["diferencia_absoluta_promocional_ars"] = _r(dif_p)
        comp["diferencia_porcentual_promocional"] = _r(pct_p)
    return comp


#recorre los productos pedidos, busca sus ofertas y le pone un estado a cada producto
def _analizar(ids: list, competidores: list, con_comparacion: bool) -> list:
    cat = _catalogo().set_index("producto_id", drop=False)
    analisis = []
    for pid in ids:
        row = next(cat.loc[[pid]].itertuples(index=False))
        ofertas = _ofertas_de_producto(row, competidores)
        if con_comparacion:
            for of in ofertas:
                of["comparacion"] = _comparacion(row, of)
        vereds = [o["veredicto"] for o in ofertas]
        #el estado del producto sale de lo mejor que se encontro entre todos los competidores
        if "equivalente" in vereds and any(
                o["veredicto"] == "equivalente" and (not con_comparacion or o.get("comparacion")) for o in ofertas):
            estado = "con_coincidencia"
        elif "presentacion_diferente" in vereds:
            estado = "solo_comparacion_referencial"
        elif any(v in ("marca_diferente", "unidad_incompatible", "equivalente") for v in vereds):
            estado = "sin_coincidencia_confiable"
        else:
            estado = "no_encontrado"
        item = {"producto_ragnar": _fila_producto(row), "estado": estado, "ofertas": ofertas}
        adv = _advertencia_duplicado(row)
        if adv:
            item["advertencia_catalogo"] = adv
        analisis.append(item)
    return analisis


#arma los totales: cuantos mas caros, mas baratos, significativos, no encontrados, etc.
def _resumen(analisis: list) -> dict:
    estados = {"con_coincidencia": 0, "solo_comparacion_referencial": 0,
               "sin_coincidencia_confiable": 0, "no_encontrado": 0}
    posiciones = {"ragnar_mas_caro": 0, "ragnar_mas_barato": 0, "iguales": 0}
    significativas, por_comp = [], {}
    for a in analisis:
        estados[a["estado"]] += 1
        p = a["producto_ragnar"]
        for o in a["ofertas"]:
            c = o.get("comparacion")
            #las comparaciones referenciales no cuentan para el resumen, solo las de productos equivalentes
            if not c or c["referencial"]:
                continue
            posiciones[c["posicion"]] += 1
            por_comp.setdefault(o["competidor"], []).append(c["diferencia_porcentual"])
            if c["diferencia_significativa"]:
                significativas.append({
                    "producto_id": p["producto_id"], "producto": f'{p["producto"]} {p["marca"]} {p["presentacion"]}',
                    "competidor": o["competidor"], "posicion": c["posicion"],
                    "diferencia_porcentual": c["diferencia_porcentual"]})
    return {
        "productos_analizados": len(analisis),
        "productos_por_estado": estados,
        "comparaciones_equivalentes": sum(posiciones.values()),
        "posicion_de_ragnar": posiciones,
        "umbral_diferencia_significativa_pct": UMBRAL_SIGNIFICATIVO,
        "diferencias_significativas": significativas,
        "diferencia_promedio_por_competidor_pct": {k: _r(sum(v) / len(v)) for k, v in por_comp.items()},
        "ids_no_encontrados": [a["producto_ragnar"]["producto_id"] for a in analisis if a["estado"] == "no_encontrado"],
        "ids_sin_coincidencia_confiable": [a["producto_ragnar"]["producto_id"] for a in analisis
                                           if a["estado"] == "sin_coincidencia_confiable"],
        "convencion": "diferencia = precio del competidor - precio de Ragnar; positivo = Ragnar es más barato.",
    }


#----- las TOOLS (estas son las que ve el modelo) -----
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


#armado del informe en markdown
def _dec(v, n=1) -> str:
    """numero con coma decimal, a la argentina"""
    return f"{v:.{n}f}".replace(".", ",")


#porcentaje con el signo adelante: +7,46%
def _signo(v):
    return "-" if v is None else f"{v:+.2f}%".replace(".", ",")


#arma el texto completo del informe, seccion por seccion. L es la lista de lineas
def _markdown_informe(titulo: str, analisis: list, resumen: dict, comps: list) -> str:
    ahora = f"{datetime.now():%Y-%m-%d %H:%M}"
    simulada = any("SIMULADA" in str(o.get("tipo_fuente", "")).upper()
                   for a in analisis for o in a["ofertas"])
    L = [f"# {titulo}", "",
         f"**Generado:** {ahora} · **Productos analizados:** {resumen['productos_analizados']} · "
         f"**Competidores consultados:** {', '.join(comps)}", ""]
    if simulada:
        L += ["> ⚠️ **Aviso:** los precios de la competencia de este informe provienen de fuentes "
              "**SIMULADAS** (datos de práctica). Las URLs son ficticias y no deben usarse para tomar "
              "decisiones comerciales reales.", ""]

    pe, pc = resumen["productos_por_estado"], resumen["posicion_de_ragnar"]
    L += ["## 1. Resumen", "",
          f"- Productos con coincidencia equivalente: **{pe['con_coincidencia']}**",
          f"- Productos solo con comparación referencial (otra presentación): **{pe['solo_comparacion_referencial']}**",
          f"- Productos sin coincidencia confiable: **{pe['sin_coincidencia_confiable']}**",
          f"- Productos no encontrados: **{pe['no_encontrado']}**",
          f"- Comparaciones con productos equivalentes: **{resumen['comparaciones_equivalentes']}** "
          f"(Ragnar más caro en {pc['ragnar_mas_caro']}, más barato en {pc['ragnar_mas_barato']}, "
          f"igual en {pc['iguales']})", ""]

    #separamos las comparaciones en tres grupos: equivalentes, referenciales y con promo
    equiv, refer, promos = [], [], []
    for a in analisis:
        p = a["producto_ragnar"]
        nombre = f'{p["producto"]} {p["marca"]} {p["presentacion"]} ({p["producto_id"]})'
        for o in a["ofertas"]:
            c = o.get("comparacion")
            if not c:
                continue
            fila = (nombre, o, c)
            (refer if c["referencial"] else equiv).append(fila)
            if o["precio_promocional"] is not None:
                promos.append(fila)

    L += ["## 2. Comparación con productos equivalentes", ""]
    if equiv:
        L += ["| Producto | Competidor | Precio Ragnar (ARS) | Precio competidor (ARS) | Dif. (ARS) | Dif. % | "
              "Fuente | Fecha |", "|---|---|---:|---:|---:|---:|---|---|"]
        for nombre, o, c in equiv:
            marca = " **(significativa)**" if c["diferencia_significativa"] else ""
            L.append(f"| {nombre} | {o['competidor']} | {_fmt(c['precio_ragnar'])} | "
                     f"{_fmt(c['precio_competidor_regular'])} | {_fmt(c['diferencia_absoluta_ars'])} | "
                     f"{_signo(c['diferencia_porcentual'])}{marca} | {o['fuente_url']} | {o['fecha_consulta']} |")
    else:
        L.append("No hubo comparaciones con productos equivalentes.")

    L += ["", "## 3. Comparación referencial (presentación distinta, precio normalizado por unidad)", ""]
    if refer:
        L += ["No son el mismo artículo: se comparan solo por precio por unidad de medida (ARS por kg, litro o unidad).", "",
              "| Producto Ragnar | Competidor | Presentación competidor | Unidad de precio | Ragnar (ARS) | "
              "Competidor (ARS) | Dif. % | Fuente | Fecha |", "|---|---|---|---|---:|---:|---:|---|---|"]
        for nombre, o, c in refer:
            L.append(f"| {nombre} | {o['competidor']} | {o['presentacion_publicada']} | {o['unidad_precio']} | "
                     f"{_fmt(c['precio_ragnar'])} | {_fmt(c['precio_competidor_regular'])} | "
                     f"{_signo(c['diferencia_porcentual'])} | {o['fuente_url']} | {o['fecha_consulta']} |")
    else:
        L.append("No hubo comparaciones referenciales.")

    L += ["", "## 4. Promociones detectadas", ""]
    if promos:
        L += ["Se muestran los precios publicados (regular y promocional). La diferencia compara el precio "
              "promocional con el de Ragnar, sobre la base indicada.", "",
              "| Producto | Competidor | Presentación | Precio regular (ARS) | Precio promocional (ARS) | "
              "Promoción | Base de comparación | Dif. % promo vs Ragnar |", "|---|---|---|---:|---:|---|---|---:|"]
        for nombre, o, c in promos:
            base = "precio por unidad (referencial)" if c["referencial"] else "precio total"
            L.append(f"| {nombre} | {o['competidor']} | {o['presentacion_publicada']} | "
                     f"{_fmt(o['precio_regular']['valor_ars'])} | {_fmt(o['precio_promocional']['valor_ars'])} | "
                     f"{o['promocion']} | {base} | {_signo(c.get('diferencia_porcentual_promocional'))} |")
    else:
        L.append("No se detectaron promociones.")

    L += ["", "## 5. Productos sin coincidencia confiable", ""]
    sin = [a for a in analisis if a["estado"] == "sin_coincidencia_confiable"]
    if sin:
        for a in sin:
            p = a["producto_ragnar"]
            partes = []
            for o in a["ofertas"]:
                if o["veredicto"] == "marca_diferente":
                    partes.append(f"{o['competidor']} solo ofrece otra marca ({o['producto_publicado']})")
                elif o["veredicto"] == "unidad_incompatible":
                    partes.append(f"{o['competidor']}: unidades incompatibles ({o['producto_publicado']})")
            L.append(f"- **{p['producto']} {p['marca']} {p['presentacion']}** ({p['producto_id']}) — "
                     + "; ".join(partes) + ". No se compara.")
    else:
        L.append("Ninguno.")

    L += ["", "## 6. Productos no encontrados en la competencia", ""]
    nf = [a for a in analisis if a["estado"] == "no_encontrado"]
    if nf:
        for a in nf:
            p = a["producto_ragnar"]
            L.append(f"- {p['producto']} {p['marca']} {p['presentacion']} ({p['producto_id']})")
    else:
        L.append("Ninguno.")

    #juntamos las advertencias del catalogo y los tipos de cambio que se usaron, van en las notas
    advert = [(a["producto_ragnar"]["producto_id"], a["advertencia_catalogo"])
              for a in analisis if "advertencia_catalogo" in a]
    cambios_usados = {}
    for a in analisis:
        for o in a["ofertas"]:
            if o.get("tipo_cambio"):
                cambios_usados[o["precio_regular"]["moneda"]] = o["tipo_cambio"]
    L += ["", "## 7. Notas metodológicas", "",
          "- Diferencia = precio del competidor − precio de Ragnar. Positiva: Ragnar es más barato.",
          f"- Se marca como significativa una diferencia de {resumen['umbral_diferencia_significativa_pct']:.0f}% o más.",
          "- Solo se consideran equivalentes los productos con igual nombre, marca, contenido y unidad.",
          "- Las diferencias de presentación se comparan únicamente por precio por kg / litro / unidad."]
    for mon, tc in cambios_usados.items():
        L.append(f"- Conversión {mon} → ARS: 1 {mon} = {tc['ars_por_unidad']} ARS "
                 f"(fecha {tc['fecha']}; fuente: {tc['fuente']}).")
    for pid, adv in advert:
        L.append(f"- {pid}: {adv}")

    L += ["", "## 8. Conclusiones", ""]
    n = resumen["comparaciones_equivalentes"]
    if n:
        L.append(f"- Ragnar resulta **más caro** en {pc['ragnar_mas_caro']} de {n} comparaciones "
                 f"({pc['ragnar_mas_caro'] / n * 100:.0f}%) y **más barato** en {pc['ragnar_mas_barato']} "
                 f"({pc['ragnar_mas_barato'] / n * 100:.0f}%).")
        for comp, prom in resumen["diferencia_promedio_por_competidor_pct"].items():
            sentido = "más caro que Ragnar" if prom > 0 else "más barato que Ragnar"
            L.append(f"- En promedio, {comp} está {_dec(abs(prom))}% {sentido} (en productos equivalentes).")
        sig = resumen["diferencias_significativas"]
        L.append(f"- Hay **{len(sig)}** comparaciones con diferencia significativa (≥ "
                 f"{resumen['umbral_diferencia_significativa_pct']:.0f}%).")
        #las 3 diferencias mas grandes donde ragnar sale mas caro
        masc = sorted((s for s in sig if s["posicion"] == "ragnar_mas_caro"),
                      key=lambda s: s["diferencia_porcentual"])[:3]
        if masc:
            L.append("- Mayores brechas donde Ragnar es más caro: " + "; ".join(
                f"{s['producto']} vs {s['competidor']} (Ragnar {_dec(abs(s['diferencia_porcentual']))}% más caro)"
                for s in masc) + ".")
    else:
        L.append("- No hubo comparaciones con productos equivalentes, por lo que no se pueden extraer "
                 "conclusiones de posicionamiento.")
    if pe["no_encontrado"] or pe["sin_coincidencia_confiable"]:
        L.append(f"- {pe['no_encontrado'] + pe['sin_coincidencia_confiable']} producto(s) no pudieron compararse "
                 "de forma confiable; revisar las secciones 5 y 6.")
    return "\n".join(L) + "\n"
