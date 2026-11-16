"""
leer y comparar numeros, para controlar que el agente no invente valores.

la idea: las tools devuelven precios y porcentajes con decimales. guardamos todos
los numeros que devolvieron y cuando el modelo responde nos fijamos que cada
numero CON DECIMALES que escribio este entre esos. los enteros ("750 ml",
"3 productos") no los revisamos porque darian falsas alarmas.
"""
import re

#un numero: digitos con puntos o comas en el medio ("3.383,00", "2941.54", "13,05")
_PATRON_NUMERO = re.compile(r"\d+(?:[.,]\d+)*")
#el separador decimal: el ultimo punto o coma con 1 o 2 digitos atras, al final
_PATRON_DECIMAL = re.compile(r"[.,](\d{1,2})$")


def leer_numero(texto: str) -> tuple[float, int]:
    """
    pasa a float un numero escrito a la argentina o a la inglesa.
    devuelve (valor, cuantos decimales tenia escritos).
        "3.383,00" -> (3383.0, 2)    "2941.54" -> (2941.54, 2)    "3.383" -> (3383.0, 0)
    """
    decimal = _PATRON_DECIMAL.search(texto)
    if decimal:
        entero, decimales = texto[:decimal.start()], decimal.group(1)
    else:
        entero, decimales = texto, ""
    entero = entero.replace(".", "").replace(",", "")
    valor = float(f"{entero}.{decimales}") if decimales else float(entero)
    return valor, len(decimales)


def numeros_en_texto(texto: str) -> list[tuple[str, float, int]]:
    """todos los numeros de un texto: (como estaba escrito, valor, decimales)"""
    return [(m, *leer_numero(m)) for m in _PATRON_NUMERO.findall(texto)]


def numeros_de_resultado(dato) -> list[float]:
    """todos los numeros que aparecen en el resultado de una tool (tambien adentro de textos)"""
    if isinstance(dato, bool):
        return []
    if isinstance(dato, (int, float)):
        return [abs(float(dato))]
    if isinstance(dato, str):
        return [valor for _, valor, _ in numeros_en_texto(dato)]
    if isinstance(dato, dict):
        dato = list(dato.values())
    if isinstance(dato, list):
        return [n for elemento in dato for n in numeros_de_resultado(elemento)]
    return []


def tiene_respaldo(valor: float, decimales: int, validos: list[float]) -> bool:
    """True si 'valor' es alguno de los numeros validos, redondeado a los decimales que escribio"""
    tolerancia = 0.5 / 10 ** decimales + 1e-6
    return any(abs(v - valor) <= tolerancia for v in validos)


def numeros_sin_respaldo(texto: str, validos: list[float]) -> list[str]:
    """los numeros con decimales del texto que no salieron de ningun resultado de las tools"""
    return [escrito for escrito, valor, decimales in numeros_en_texto(texto)
            if decimales > 0 and not tiene_respaldo(valor, decimales, validos)]
