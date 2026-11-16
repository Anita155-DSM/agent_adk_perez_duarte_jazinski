"""
la parte mas importante: ver si un producto de la competencia es el MISMO que el de
ragnar y hacer las cuentas de la diferencia de precio.

el recorrido es este:
    _ofertas_de_producto  -> busca la mejor oferta de cada competidor
    _clasificar           -> decide si es el mismo producto (el "veredicto")
    _comparacion          -> calcula la diferencia en pesos y en %
    _analizar             -> hace todo lo anterior para cada producto pedido
    _resumen              -> cuenta cuantos mas caros, mas baratos, no encontrados, etc.
"""
import math

from .datos import _a_ars, _a_base, _cambios, _catalogo, _fmt, _fmt_cont, _ofertas, _r, _unitario

UMBRAL_SIGNIFICATIVO = 10.0  #desde que % decimos que una diferencia es "significativa"


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
