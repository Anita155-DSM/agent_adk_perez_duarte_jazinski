"""
genera los datos SIMULADOS de la competencia, para poder practicar con el agente.

IMPORTANTE: los competidores, los precios, las promos y las urls son INVENTADOS
(dominios .example, que no existen). sirven para poder demostrar los criterios
de aceptacion sin depender de paginas reales.

se corre desde la raiz del proyecto:
    python scripts/generar_datos_simulados.py

tiene semilla fija, o sea que siempre genera los mismos datos.
"""
import random
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parent.parent / "ragnar_agent" / "data"
random.seed(2026)

COMPETIDORES = [
    #nombre, pais, moneda, dominio inventado
    ("SuperNorte", "Argentina", "ARS", "supernorte.example"),
    ("MegaMercado", "Argentina", "ARS", "megamercado.example"),
    ("Precio Justo", "Argentina", "ARS", "preciojusto.example"),
    ("UruMarket", "Uruguay", "UYU", "urumarket.example"),
]
#cuantos pesos vale 1 unidad de cada moneda (valor inventado, de practica)
TIPO_CAMBIO = {"ARS": 1.0, "UYU": 36.5}
#productos "exclusivos" de ragnar: la competencia NO los vende (marca + producto).
#- SIN_NINGUN_SIMILAR: no hay ni otra marca con el mismo contenido,
#  ahi el agente tiene que decir "no encontrado"
#- solo_otra_marca (se sortea mas abajo): la competencia tiene solo OTRA marca del mismo producto,
#  ahi el agente tiene que decir "sin coincidencia confiable"
SIN_NINGUN_SIMILAR = {
    ("Cafe molido", "Cabrales"), ("Lavandina", "Clorox"),
    ("Mayonesa", "Fanacoa"), ("Harina de trigo", "Morixe"),
}
TAMANOS = {"g": [90, 100, 135, 200, 250, 350, 500, 750, 900, 1000, 1500, 2000, 2250, 2500],
           "ml": [90, 100, 135, 200, 250, 350, 500, 750, 900, 1000, 1500, 2000, 2250, 2500],
           "un": [1, 2, 4, 6, 12, 24]}
FECHAS = ["2026-11-07", "2026-11-08", "2026-11-09"]
PROMOCIONES = [
    "Oferta semanal",
    "Descuento con tarjeta de crédito",
    "Liquidación de stock",
    "Precio especial online",
]


#arma el pedacito de url: minusculas, sin tildes y con guiones
def slug(texto: str) -> str:
    t = str(texto).lower()
    for a, b in (("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u"), ("ñ", "n")):
        t = t.replace(a, b)
    return "".join(c if c.isalnum() else "-" for c in t).strip("-")


def main() -> None:
    cat = pd.read_csv(BASE / "catalogo_ragnar.csv")
    clave = ["producto", "marca", "contenido", "unidad_medida"]
    #precio de referencia de cada producto "identico" (la mediana), porque el
    #catalogo trae productos repetidos con precios re distintos
    ref = cat.groupby(clave).precio.median().reset_index()
    marcas_por_producto = cat.groupby("producto").marca.unique().to_dict()
    pares = sorted(set(zip(cat.producto, cat.marca)) - SIN_NINGUN_SIMILAR)
    solo_otra_marca = set(random.Random(7).sample(pares, 6))
    exclusivos = SIN_NINGUN_SIMILAR | solo_otra_marca

    filas = []
    for nombre, pais, moneda, dominio in COMPETIDORES:
        vistos = set()

        #suma una oferta de este competidor, sin repetir el mismo producto dos veces
        def agregar(prod, marca, cont, unidad, precio_ars):
            k = (prod, marca, cont, unidad)
            if k in vistos:
                return
            vistos.add(k)
            #algunos competidores publican en kg / l en vez de g / ml
            cont_pub, uni_pub = cont, unidad
            if unidad in ("g", "ml") and cont >= 500 and random.random() < 0.15:
                cont_pub = round(cont / 1000, 3)
                uni_pub = "kg" if unidad == "g" else "l"
            precio = precio_ars / TIPO_CAMBIO[moneda]
            promo, p_promo = "", ""
            #mas o menos 1 de cada 5 ofertas sale con promo
            if random.random() < 0.18:
                promo = random.choice(PROMOCIONES)
                p_promo = round(precio * random.uniform(0.75, 0.92), 2)
            filas.append({
                "competidor": nombre, "pais": pais,
                "producto": prod, "marca": marca,
                "contenido": cont_pub, "unidad": uni_pub,
                "precio": round(precio, 2), "moneda": moneda,
                "precio_promocional": p_promo, "promocion": promo,
                "fuente_url": f"https://www.{dominio}/p/{slug(prod)}-{slug(marca)}-{cont_pub}{uni_pub}",
                "fecha_consulta": random.choice(FECHAS),
                "tipo_fuente": "SIMULADA (datos de práctica)",
            })

        for r in ref.itertuples(index=False):
            prod, marca, cont, unidad, precio = r
            if (prod, marca) in exclusivos:
                continue  #la competencia no vende este producto
            #sorteamos que pasa con este producto en este competidor
            x = random.random()
            if x < 0.55:  #mismo producto, misma presentacion
                agregar(prod, marca, cont, unidad, precio * random.uniform(0.82, 1.22))
            elif x < 0.70:  #misma marca, otra presentacion
                cercanos = [c for c in TAMANOS[unidad] if c != cont and 0.4 <= c / cont <= 2.6]
                if cercanos:
                    c2 = random.choice(cercanos)
                    agregar(prod, marca, c2, unidad,
                            precio * (c2 / cont) * random.uniform(0.8, 1.25))
            elif x < 0.78:  #otra marca, misma presentacion (no es equivalente)
                otras = [m for m in marcas_por_producto[prod]
                         if m != marca and (prod, m) not in exclusivos]
                if otras:
                    agregar(prod, random.choice(otras), cont, unidad,
                            precio * random.uniform(0.8, 1.3))
            #y si no cayo en ninguno, este competidor directamente no lo vende

    out = pd.DataFrame(filas)
    out.to_csv(BASE / "ofertas_competencia.csv", index=False)
    cambios = pd.DataFrame([
        {"moneda": m, "ars_por_unidad": v, "fecha": "2026-11-09",
         "fuente": "Tipo de cambio de práctica (SIMULADO)"}
        for m, v in TIPO_CAMBIO.items()
    ])
    cambios.to_csv(BASE / "tipos_de_cambio.csv", index=False)
    print(f"{len(out)} ofertas generadas para {len(COMPETIDORES)} competidores")


if __name__ == "__main__":
    main()
