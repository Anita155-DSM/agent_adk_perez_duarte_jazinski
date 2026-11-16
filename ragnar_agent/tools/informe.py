"""
arma el texto del informe en markdown, seccion por seccion.
no hace ninguna cuenta: todo lo saca de lo que ya calculo comparacion.py
"""
from datetime import datetime

from .datos import _fmt


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
