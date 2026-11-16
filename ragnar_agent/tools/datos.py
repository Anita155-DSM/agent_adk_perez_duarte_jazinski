"""
los datos y las cuentitas basicas que usan todas las tools:

* leer los csv de la carpeta data/ (catalogo, ofertas de la competencia y tipos de cambio)
* pasar las unidades a una sola (todo a gramos, mililitros o unidades)
* sacar el precio por kg / litro / unidad y pasar otras monedas a pesos
* revisar los ids de producto y el filtro de competidor que manda el modelo
"""
import math
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

import pandas as pd

#donde estan los csv
DATA_DIR = Path(__file__).resolve().parent.parent / "data"

MAX_RESULTADOS = 40  #cuantos productos como mucho por consulta

#los ids del catalogo tienen esta forma: RAG- y 5 numeros
_ID_VALIDO = re.compile(r"^RAG-\d{5}$")

#para pasar todo a una misma unidad: unidad -> (unidad base, por cuanto se multiplica)
_UNIDADES = {
    "g": ("g", 1), "gr": ("g", 1), "kg": ("g", 1000),
    "ml": ("ml", 1), "cc": ("ml", 1), "l": ("ml", 1000), "lt": ("ml", 1000),
    "un": ("un", 1), "u": ("un", 1),
}


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

