#!/usr/bin/env python3
"""
finanzas.py -- Manejo Financiero Familiar
==========================================

Programa de terminal, de un solo archivo, para administrar la situacion
financiera de una familia, organizada por MES: cada mes tiene sus propios
ingresos, gastos y distribucion del disponible, para poder llevar varios
meses y compararlos. Las deudas y el ahorro son continuos (no "empiezan" ni
"terminan" con un mes: son un saldo que se actualiza), asi que no se
duplican por mes -- se ven y editan igual sin importar que mes tengas
seleccionado.

Requisitos de diseno:
    - Solo biblioteca estandar de Python (decimal, json, os, etc).
    - Todo el dinero se maneja con Decimal, nunca con float.
    - Los datos se guardan en finanzas.json, junto a este script.
    - Debe funcionar igual en PC (Windows/Linux/Mac) y en iSH (iPhone).

Ejecutar:
    python finanzas.py
    (en iSH / Linux puede ser necesario: python3 finanzas.py)

Mapa de este archivo (para ubicar las cosas facilmente):
    1. Utilidades de dinero y entrada de datos (Decimal, validaciones)
    2. Carga / guardado de datos (JSON, escritura segura)
    3. Calculos financieros centrales (ingresos, bloques, gastos, deudas)
    4. Motor de amortizacion de deudas
    5. Pantallas (impresion en terminal)
    6. Menus / edicion de datos, por seccion
    7. Programa principal (main)

Decisiones de diseno tomadas para resolver ambiguedades menores (regla 33
del pedido original: no detener la implementacion por dudas pequenas):

    - Los "gastos fijos / bills" y las "deudas" pertenecen a un bloque
      (para poder calcular "disponible por bloque"). Los "gastos familiares
      variables" (comida, gasolina, etc.) NO se asignan a un bloque
      especifico: se presupuestan a nivel mensual y se restan del
      disponible total, tal como lo muestra el ejemplo de la seccion 9
      del pedido original.
    - El "pago minimo" de una deuda se trata como un compromiso fijo
      (se resta para calcular el disponible). El "pago extra" de una
      deuda se trata como parte de la DISTRIBUCION del dinero disponible
      (junto con ahorro, familia/otros y reserva), tal como en el ejemplo
      de la seccion 9. Esto es lo unico que separa "comprometido" de
      "distribucion" en este programa.
    - El numero de bloques es configurable (por mes, por defecto 2), para
      poder agregar mas bloques en el futuro sin rehacer el codigo.
    - Ingresos, gastos, distribucion y numero de bloques viven DENTRO de
      cada mes (datos["meses"][mes_id]). Deudas y ahorro son globales
      (datos["deudas"], datos["ahorro"]): representan un saldo real y
      continuo, no una foto de un mes especifico.
"""

from __future__ import annotations

import builtins
import copy
import json
import os
import re
import shutil
import sys
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# 0. TERMINAL: CENTRADO AUTOMATICO Y COLORES (numeros en verde, texto en blanco)
# ---------------------------------------------------------------------------
#
# Todo lo que el programa imprime pasa por la funcion print() de mas abajo,
# que reemplaza a la funcion print() nativa de Python para TODO este archivo.
# Eso permite, sin tener que tocar cada llamada a print() suelta en el resto
# del codigo:
#   1. Centrar automaticamente cada linea en el ancho ACTUAL de la terminal
#      (se recalcula en cada impresion, asi que si cambias el tamano de la
#      ventana, la proxima pantalla se adapta sola).
#   2. Colorear en verde cualquier numero, monto en dinero ($1,234.56) o
#      porcentaje (24.99%), dejando el resto del texto en blanco.
#
# En Windows (cmd.exe / PowerShell antiguos) los codigos de color ANSI estan
# apagados por defecto; el truco de os.system("") de abajo los activa. Si el
# programa corre en un lugar donde la salida no es una terminal real (por
# ejemplo, redirigida a un archivo), los colores se desactivan solos.

if sys.platform == "win32":
    os.system("")

_COLOR_ACTIVO = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None

VERDE = "\033[92m"
BLANCO = "\033[97m"
RESET = "\033[0m"

# Numeros, montos y porcentajes: -$1,234.56 / $0.00 / 24.99% / 12
_PATRON_NUMERO = re.compile(r"-?\$?\d[\d,]*(?:\.\d+)?%?")

# Para medir el ancho VISIBLE de una linea hay que ignorar los codigos de
# color (si no, la terminal cuenta caracteres invisibles y el centrado sale mal).
_PATRON_ANSI = re.compile(r"\033\[[0-9;]*m")


def _ancho_terminal() -> int:
    """Ancho actual de la terminal. Si no se puede detectar, asume 80 columnas."""
    try:
        return shutil.get_terminal_size(fallback=(80, 24)).columns
    except Exception:
        return 80


def _ancho_panel() -> int:
    """
    Ancho del 'panel' de texto (lineas ===, tablas, etc).
    Se adapta a la terminal actual, pero se mantiene entre 50 y 100 columnas
    para que siga siendo legible tanto en una pantalla de celular (iSH) como
    en una terminal de escritorio muy ancha.
    """
    disponible = _ancho_terminal() - 4
    return max(50, min(disponible, 100))


def _colorear_numeros(texto: str) -> str:
    """Pinta en verde los numeros/montos/porcentajes de una linea; el resto queda en blanco."""
    if not _COLOR_ACTIVO:
        return texto

    def _reemplazar(coincidencia: "re.Match[str]") -> str:
        return f"{VERDE}{coincidencia.group(0)}{RESET}{BLANCO}"

    return f"{BLANCO}{_PATRON_NUMERO.sub(_reemplazar, texto)}{RESET}"


def print(*args: Any, sep: str = " ", end: str = "\n", file: Any = None, flush: bool = False) -> None:
    """
    Reemplaza la funcion print() nativa para TODO este archivo: centra cada
    linea en la terminal actual y colorea numeros/montos/porcentajes en
    verde. El resto de argumentos (sep, end, file, flush) funcionan igual
    que en el print() normal de Python.
    """
    texto = sep.join(str(a) for a in args)
    ancho_terminal = _ancho_terminal()

    lineas_salida = []
    for linea_txt in texto.split("\n"):
        largo_visible = len(_PATRON_ANSI.sub("", linea_txt))
        relleno = max((ancho_terminal - largo_visible) // 2, 0)
        lineas_salida.append((" " * relleno) + _colorear_numeros(linea_txt))

    builtins.print("\n".join(lineas_salida), end=end, file=file, flush=flush)

# ---------------------------------------------------------------------------
# 1. UTILIDADES DE DINERO Y ENTRADA DE DATOS
# ---------------------------------------------------------------------------

RUTA_DATOS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "finanzas.json")

CERO = Decimal("0.00")
DOS_DECIMALES = Decimal("0.01")
LIMITE_MESES_SIMULACION = 1200  # limite de seguridad: nunca simular mas de 100 anios

FRECUENCIAS_VALIDAS = ("QUINCENAL", "MENSUAL", "OTRO")
CATEGORIA_BILL = "bill"
CATEGORIA_VARIABLE = "variable"


def to_decimal(valor: Any, default: Optional[Decimal] = None) -> Optional[Decimal]:
    """Convierte texto/numero a Decimal de forma segura. Nunca lanza excepcion."""
    if valor is None:
        return default
    if isinstance(valor, Decimal):
        return valor
    try:
        texto = str(valor).strip().replace("$", "").replace(",", "")
        if texto == "":
            return default
        return Decimal(texto)
    except (InvalidOperation, ValueError):
        return default


def redondear(valor: Decimal, exp: Decimal = DOS_DECIMALES) -> Decimal:
    """Redondeo SOLO para presentacion / guardado. Los calculos intermedios no redondean."""
    return valor.quantize(exp, rounding=ROUND_HALF_UP)


def fmt_money(valor: Decimal) -> str:
    valor = redondear(valor)
    negativo = valor < 0
    texto = f"{abs(valor):,.2f}"
    return f"-${texto}" if negativo else f"${texto}"


def fmt_pct(valor: Decimal) -> str:
    return f"{redondear(valor)}%"


def leer_texto(prompt: str) -> str:
    try:
        return input(prompt)
    except (EOFError, KeyboardInterrupt):
        print("\n\nSaliendo...")
        sys.exit(0)


def leer_decimal(prompt: str, actual: Optional[Decimal] = None, permitir_vacio: bool = True) -> Decimal:
    """Pide un valor monetario. Enter conserva el valor actual (si existe). Valida entradas invalidas."""
    while True:
        texto = leer_texto(prompt).strip()
        if texto == "" and permitir_vacio and actual is not None:
            return actual
        valor = to_decimal(texto)
        if valor is None:
            print("Valor invalido. Introduce una cantidad valida (ejemplo: 150.00).")
            continue
        return valor


def leer_entero(prompt: str, actual: Optional[int] = None, minimo: Optional[int] = None,
                 maximo: Optional[int] = None) -> int:
    while True:
        texto = leer_texto(prompt).strip()
        if texto == "" and actual is not None:
            return actual
        try:
            valor = int(texto)
        except ValueError:
            print("Valor invalido. Introduce un numero entero.")
            continue
        if minimo is not None and valor < minimo:
            print(f"El valor debe ser mayor o igual a {minimo}.")
            continue
        if maximo is not None and valor > maximo:
            print(f"El valor debe ser menor o igual a {maximo}.")
            continue
        return valor


def leer_texto_simple(prompt: str, actual: Optional[str] = None) -> str:
    texto = leer_texto(prompt).strip()
    if texto == "" and actual is not None:
        return actual
    return texto


def leer_si_no(prompt: str, default_si: bool = False) -> bool:
    sufijo = " [S/n]: " if default_si else " [s/N]: "
    texto = leer_texto(prompt + sufijo).strip().lower()
    if texto == "":
        return default_si
    return texto in ("s", "si", "si.", "y", "yes")


def pausar() -> None:
    leer_texto("\nPresiona Enter para continuar...")


# ---------------------------------------------------------------------------
# 2. CARGA / GUARDADO DE DATOS (JSON, escritura segura)
# ---------------------------------------------------------------------------

def _mes_por_defecto() -> str:
    """Id de mes sugerido para instalaciones nuevas: el mes calendario actual."""
    return datetime.now().strftime("%Y-%m")


def _mes_vacio(num_bloques: int = 2) -> Dict[str, Any]:
    """Estructura vacia de UN mes: lo que cambia mes a mes (no deudas/ahorro)."""
    return {
        "num_bloques": num_bloques,
        "ingresos": [],
        "gastos": [],
        "distribucion": {
            "ahorro": "0.00",
            "familia_otros": "0.00",
            "reserva": "0.00",
        },
    }


def datos_iniciales() -> Dict[str, Any]:
    """Estructura vacia inicial. No se inventan datos financieros reales."""
    mes_id = _mes_por_defecto()
    return {
        "config": {},
        "meses": {
            mes_id: _mes_vacio(),
        },
        "mes_trabajo": mes_id,
        "deudas": [],
        "ahorro": {
            "actual": "0.00",
            "meta": "0.00",
        },
    }


def cargar_datos(ruta: str = RUTA_DATOS) -> Dict[str, Any]:
    """Carga finanzas.json. Si no existe / esta vacio / esta corrupto, crea datos vacios."""
    if not os.path.exists(ruta):
        datos = datos_iniciales()
        guardar_datos(datos, ruta)
        return datos

    try:
        with open(ruta, "r", encoding="utf-8") as f:
            contenido = f.read().strip()
        if contenido == "":
            datos = datos_iniciales()
            guardar_datos(datos, ruta)
            return datos
        datos = json.loads(contenido)
    except (json.JSONDecodeError, OSError):
        print("\nADVERTENCIA: finanzas.json esta corrupto o no se pudo leer.")
        print("Se guardara una copia en finanzas.json.corrupto.bak y se iniciara vacio.\n")
        _respaldar_archivo_corrupto(ruta)
        datos = datos_iniciales()
        guardar_datos(datos, ruta)
        return datos

    return _normalizar_datos(datos)


def _respaldar_archivo_corrupto(ruta: str) -> None:
    try:
        if os.path.exists(ruta):
            os.replace(ruta, ruta + ".corrupto.bak")
    except OSError:
        pass


def _distribucion_es_cero(dist: Any) -> bool:
    if not isinstance(dist, dict):
        return True
    return all(to_decimal(v, CERO) == CERO for v in dist.values())


def _normalizar_ingreso(ing: dict) -> None:
    ing.setdefault("persona", "Sin nombre")
    ing.setdefault("descripcion", "")
    ing.setdefault("bloque", 1)
    ing.setdefault("monto", "0.00")
    ing.setdefault("frecuencia", "MENSUAL")
    ing.setdefault("activo", True)
    ing.setdefault("recurrente", True)


def _normalizar_gasto(g: dict) -> None:
    g.setdefault("nombre", "Sin nombre")
    g.setdefault("categoria", CATEGORIA_VARIABLE)
    g.setdefault("monto", "0.00")
    g.setdefault("bloque", 1 if g.get("categoria") == CATEGORIA_BILL else None)
    g.setdefault("vencimiento", None)
    g.setdefault("activo", True)


def _normalizar_mes(mes: dict) -> None:
    """Completa las claves de UN mes (ingresos, gastos, distribucion, bloques)."""
    if not isinstance(mes.get("ingresos"), list):
        mes["ingresos"] = []
    if not isinstance(mes.get("gastos"), list):
        mes["gastos"] = []
    if not isinstance(mes.get("distribucion"), dict):
        mes["distribucion"] = {"ahorro": "0.00", "familia_otros": "0.00", "reserva": "0.00"}
    mes["distribucion"].setdefault("ahorro", "0.00")
    mes["distribucion"].setdefault("familia_otros", "0.00")
    mes["distribucion"].setdefault("reserva", "0.00")
    mes.setdefault("num_bloques", 2)
    for ing in mes["ingresos"]:
        _normalizar_ingreso(ing)
    for g in mes["gastos"]:
        _normalizar_gasto(g)


def _normalizar_datos(datos: Any) -> Dict[str, Any]:
    """
    Rellena claves faltantes para tolerar archivos JSON antiguos, parciales o
    incompletos. Entiende las DOS estructuras que pueden existir en disco:

      - Por meses (actual): datos["meses"][nombre] con ingresos/gastos/
        distribucion/num_bloques, y datos["mes_trabajo"] = mes seleccionado.
      - Plana (version anterior, sin meses): ingresos/gastos/distribucion en
        la raiz. Se deja plana aqui (solo se completan sus campos); la
        conversion a meses la hace migrar_a_meses(), que llaman las dos
        interfaces (consola y pantallas) para avisar al usuario.
    """
    if not isinstance(datos, dict):
        return datos_iniciales()

    if not isinstance(datos.get("config"), dict):
        datos["config"] = {}
    if not isinstance(datos.get("deudas"), list):
        datos["deudas"] = []
    if not isinstance(datos.get("ahorro"), dict):
        datos["ahorro"] = {"actual": "0.00", "meta": "0.00"}
    datos["ahorro"].setdefault("actual", "0.00")
    datos["ahorro"].setdefault("meta", "0.00")

    if isinstance(datos.get("meses"), dict):
        for nombre, mes in list(datos["meses"].items()):
            if not isinstance(mes, dict):
                datos["meses"][nombre] = mes = {}
            _normalizar_mes(mes)
        datos.setdefault("mes_trabajo", None)
        # Restos planos de versiones anteriores: solo se quitan si estan
        # vacios (nunca se borra un dato con contenido).
        for clave in ("ingresos", "gastos"):
            if datos.get(clave) == []:
                datos.pop(clave)
        if "distribucion" in datos and _distribucion_es_cero(datos["distribucion"]):
            datos.pop("distribucion")
        datos["config"].pop("num_bloques", None)  # ahora vive dentro de cada mes
        datos["config"].pop("mes_actual", None)   # lo reemplaza datos["mes_trabajo"]
    else:
        if not isinstance(datos.get("ingresos"), list):
            datos["ingresos"] = []
        if not isinstance(datos.get("gastos"), list):
            datos["gastos"] = []
        if not isinstance(datos.get("distribucion"), dict):
            datos["distribucion"] = {"ahorro": "0.00", "familia_otros": "0.00", "reserva": "0.00"}
        datos["distribucion"].setdefault("ahorro", "0.00")
        datos["distribucion"].setdefault("familia_otros", "0.00")
        datos["distribucion"].setdefault("reserva", "0.00")
        datos["config"].setdefault("mes_actual", _mes_por_defecto())
        datos["config"].setdefault("num_bloques", 2)
        for ing in datos["ingresos"]:
            _normalizar_ingreso(ing)
        for g in datos["gastos"]:
            _normalizar_gasto(g)

    hubo_migracion_interes = False
    for d in datos["deudas"]:
        d.setdefault("nombre", "Sin nombre")
        d.setdefault("saldo", "0.00")
        if "interes_mensual" not in d and "interes_anual" in d:
            # Migracion de versiones anteriores: el campo se guardaba como tasa
            # ANUAL. Se convierte a mensual (/12) para no perder ni distorsionar
            # los datos ya capturados por el usuario.
            valor_anual = to_decimal(d.get("interes_anual"), CERO)
            d["interes_mensual"] = str(redondear(valor_anual / Decimal(12)))
            d.pop("interes_anual", None)
            hubo_migracion_interes = True
        d.setdefault("interes_mensual", "0.00")
        d.setdefault("pago_minimo", "0.00")
        d.setdefault("pago_extra", "0.00")
        d.setdefault("bloque", 1)
        d.setdefault("vencimiento", None)
        d.setdefault("activo", True)

    if hubo_migracion_interes:
        # Se guarda de inmediato (no se espera a la proxima edicion del
        # usuario) porque es una conversion numerica real, no solo un campo
        # vacio al que se le puso un valor por defecto.
        guardar_datos(datos)
        print("\nAVISO: se detectaron deudas con tasa de interes ANUAL (version anterior).")
        print("Se convirtieron automaticamente a tasa MENSUAL (dividiendo entre 12) y se guardaron.")
        print("Revisa que la tasa mensual de cada deuda sea correcta en el menu [4] Deudas.\n")

    return datos


def guardar_datos(datos: Dict[str, Any], ruta: str = RUTA_DATOS) -> None:
    """Escritura segura: escribe a un archivo temporal y luego reemplaza el original."""
    ruta_temp = ruta + ".tmp"
    try:
        with open(ruta_temp, "w", encoding="utf-8") as f:
            json.dump(datos, f, indent=4, ensure_ascii=False)
        os.replace(ruta_temp, ruta)
    except OSError as e:
        print(f"\nERROR: No se pudo guardar finanzas.json ({e}).")
        try:
            if os.path.exists(ruta_temp):
                os.remove(ruta_temp)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# 3. CALCULOS FINANCIEROS CENTRALES
# ---------------------------------------------------------------------------

# --- Meses --------------------------------------------------------------
#
# Ingresos, gastos, distribucion y numero de bloques viven DENTRO de cada mes
# (datos["meses"][nombre]). Deudas y ahorro son globales. El mes que se esta
# trabajando es datos["mes_trabajo"] (puede ser None si no hay ninguno).
#
# Las funciones de calculo de abajo aceptan DOS formas de "datos", para que la
# consola y las pantallas (finanzas_tui.py) compartan el mismo motor:
#   - los datos reales (con "meses"): leen el mes de trabajo.
#   - una vista plana (ingresos/gastos/distribucion y config.num_bloques en la
#     raiz, sin "meses"): la que arma vista_calculo() en finanzas_tui.py.

def _mes_datos_vacio() -> Dict[str, Any]:
    return _mes_vacio()


def _fuente_mes(datos: Dict[str, Any]) -> Dict[str, Any]:
    """Dict donde viven ingresos/gastos/distribucion del mes de trabajo."""
    if isinstance(datos.get("meses"), dict):
        clave = datos.get("mes_trabajo")
        mes = datos["meses"].get(clave) if clave else None
        return mes if mes is not None else _mes_datos_vacio()
    return datos  # vista plana


def meses_ordenados(datos: Dict[str, Any]) -> List[str]:
    return sorted(datos.get("meses", {}).keys())


def mes_trabajo_clave(datos: Dict[str, Any]) -> Optional[str]:
    clave = datos.get("mes_trabajo")
    if clave and clave in datos.get("meses", {}):
        return clave
    return None


def obtener_mes_trabajo(datos: Dict[str, Any]) -> Optional[dict]:
    clave = mes_trabajo_clave(datos)
    return datos["meses"][clave] if clave else None


def migrar_a_meses(datos: Dict[str, Any]) -> Optional[str]:
    """
    Convierte un finanzas.json plano (version anterior, sin meses) a la
    estructura por meses: ingresos, gastos, distribucion y num_bloques pasan
    a ser el primer mes. Idempotente: si ya hay meses no hace nada.
    Devuelve el nombre del mes creado si hubo migracion, o None.
    """
    if isinstance(datos.get("meses"), dict):
        datos.setdefault("mes_trabajo", None)
        return None

    config = datos.get("config") if isinstance(datos.get("config"), dict) else {}
    nombre = (config.get("mes_actual") or "").strip() or _mes_por_defecto()
    try:
        bloques = int(config.get("num_bloques", 2) or 2)
    except (TypeError, ValueError):
        bloques = 2
    mes = _mes_vacio(bloques)
    if isinstance(datos.get("ingresos"), list):
        mes["ingresos"] = datos["ingresos"]
    if isinstance(datos.get("gastos"), list):
        mes["gastos"] = datos["gastos"]
    if isinstance(datos.get("distribucion"), dict):
        mes["distribucion"] = datos["distribucion"]
    datos["meses"] = {nombre: mes}
    datos["mes_trabajo"] = nombre
    datos.pop("ingresos", None)
    datos.pop("gastos", None)
    datos.pop("distribucion", None)
    config.pop("num_bloques", None)
    return nombre


def crear_mes(datos: Dict[str, Any], nombre: str, copiar_de: Optional[str] = None) -> None:
    """
    Crea un mes nuevo y lo deja como mes de trabajo. Si copiar_de es un mes
    existente, copia (copia independiente) sus ingresos, gastos, bloques y
    distribucion. Los ingresos "adicionales" (recurrente == False, ej. un
    bono de un solo mes) NO se copian, porque no se repiten.
    """
    if copiar_de and copiar_de in datos.get("meses", {}):
        nuevo = copy.deepcopy(datos["meses"][copiar_de])
        nuevo["ingresos"] = [i for i in nuevo.get("ingresos", []) if i.get("recurrente", True)]
    else:
        nuevo = _mes_vacio()
    datos.setdefault("meses", {})[nombre] = nuevo
    datos["mes_trabajo"] = nombre


def renombrar_mes(datos: Dict[str, Any], viejo: str, nuevo: str) -> None:
    if viejo not in datos.get("meses", {}) or viejo == nuevo:
        return
    datos["meses"][nuevo] = datos["meses"].pop(viejo)
    if datos.get("mes_trabajo") == viejo:
        datos["mes_trabajo"] = nuevo


def eliminar_mes(datos: Dict[str, Any], nombre: str) -> None:
    datos.get("meses", {}).pop(nombre, None)
    if datos.get("mes_trabajo") == nombre:
        datos["mes_trabajo"] = None


def num_bloques(datos: Dict[str, Any]) -> int:
    fuente = _fuente_mes(datos)
    if "num_bloques" in fuente:
        bruto = fuente["num_bloques"]
    else:
        bruto = fuente.get("config", {}).get("num_bloques", 2)
    try:
        n = int(bruto)
        return n if n > 0 else 2
    except (TypeError, ValueError):
        return 2


def ingresos_activos(datos: Dict[str, Any]) -> List[dict]:
    return [i for i in _fuente_mes(datos)["ingresos"] if i.get("activo", True)]


def gastos_activos(datos: Dict[str, Any], categoria: Optional[str] = None) -> List[dict]:
    gastos = [g for g in _fuente_mes(datos)["gastos"] if g.get("activo", True)]
    if categoria:
        gastos = [g for g in gastos if g.get("categoria") == categoria]
    return gastos


def deudas_activas(datos: Dict[str, Any]) -> List[dict]:
    return [d for d in datos["deudas"] if d.get("activo", True)]


def _bloque_de(item: dict) -> int:
    try:
        return int(item.get("bloque") or 0)
    except (TypeError, ValueError):
        return 0


def ingreso_total_bloque(datos: Dict[str, Any], bloque: int) -> Decimal:
    total = CERO
    for i in ingresos_activos(datos):
        if _bloque_de(i) == bloque:
            total += to_decimal(i.get("monto"), CERO)
    return total


def ingreso_total_mes(datos: Dict[str, Any]) -> Decimal:
    total = CERO
    for i in ingresos_activos(datos):
        total += to_decimal(i.get("monto"), CERO)
    return total


def ingresos_por_persona(datos: Dict[str, Any]) -> Dict[str, Decimal]:
    resultado: Dict[str, Decimal] = {}
    for i in ingresos_activos(datos):
        persona = i.get("persona", "Sin nombre")
        resultado[persona] = resultado.get(persona, CERO) + to_decimal(i.get("monto"), CERO)
    return resultado


def ingreso_adicional_total(datos: Dict[str, Any]) -> Decimal:
    """Ingresos activos ADICIONALES (recurrente == False): no se copian al crear el mes siguiente."""
    total = CERO
    for i in ingresos_activos(datos):
        if not i.get("recurrente", True):
            total += to_decimal(i.get("monto"), CERO)
    return total


def ingreso_recurrente_total(datos: Dict[str, Any]) -> Decimal:
    return ingreso_total_mes(datos) - ingreso_adicional_total(datos)


def bills_total_bloque(datos: Dict[str, Any], bloque: int) -> Decimal:
    total = CERO
    for g in gastos_activos(datos, CATEGORIA_BILL):
        if _bloque_de(g) == bloque:
            total += to_decimal(g.get("monto"), CERO)
    return total


def bills_total(datos: Dict[str, Any]) -> Decimal:
    total = CERO
    for g in gastos_activos(datos, CATEGORIA_BILL):
        total += to_decimal(g.get("monto"), CERO)
    return total


def gastos_variables_total(datos: Dict[str, Any]) -> Decimal:
    total = CERO
    for g in gastos_activos(datos, CATEGORIA_VARIABLE):
        total += to_decimal(g.get("monto"), CERO)
    return total


def pago_minimo_total_bloque(datos: Dict[str, Any], bloque: int) -> Decimal:
    total = CERO
    for d in deudas_activas(datos):
        if _bloque_de(d) == bloque:
            total += to_decimal(d.get("pago_minimo"), CERO)
    return total


def pago_minimo_total(datos: Dict[str, Any]) -> Decimal:
    total = CERO
    for d in deudas_activas(datos):
        total += to_decimal(d.get("pago_minimo"), CERO)
    return total


def pago_extra_total(datos: Dict[str, Any]) -> Decimal:
    total = CERO
    for d in deudas_activas(datos):
        total += to_decimal(d.get("pago_extra"), CERO)
    return total


def saldo_deuda_total(datos: Dict[str, Any]) -> Decimal:
    total = CERO
    for d in deudas_activas(datos):
        total += to_decimal(d.get("saldo"), CERO)
    return total


def comprometido_bloque(datos: Dict[str, Any], bloque: int) -> Decimal:
    """Bills + pagos MINIMOS de deuda del bloque. El pago extra no cuenta aqui (ver distribucion)."""
    return bills_total_bloque(datos, bloque) + pago_minimo_total_bloque(datos, bloque)


def disponible_bloque(datos: Dict[str, Any], bloque: int) -> Decimal:
    return ingreso_total_bloque(datos, bloque) - comprometido_bloque(datos, bloque)


def disponible_total(datos: Dict[str, Any]) -> Decimal:
    """Ingresos - Bills - Pagos minimos de deuda - Gastos familiares variables."""
    total_bloques = CERO
    for b in range(1, num_bloques(datos) + 1):
        total_bloques += disponible_bloque(datos, b)
    return total_bloques - gastos_variables_total(datos)


def distribucion_total(datos: Dict[str, Any]) -> Decimal:
    dist = _fuente_mes(datos)["distribucion"]
    return (
        to_decimal(dist.get("ahorro"), CERO)
        + pago_extra_total(datos)
        + to_decimal(dist.get("familia_otros"), CERO)
        + to_decimal(dist.get("reserva"), CERO)
    )


def dinero_sin_asignar(datos: Dict[str, Any]) -> Decimal:
    return disponible_total(datos) - distribucion_total(datos)


# ---------------------------------------------------------------------------
# 4. MOTOR DE AMORTIZACION DE DEUDAS
# ---------------------------------------------------------------------------

def simular_deuda(saldo: Decimal, tasa_mensual_pct: Decimal, pago_mensual: Decimal,
                   limite_meses: int = LIMITE_MESES_SIMULACION) -> Dict[str, Any]:
    """
    Simula mes a mes el pago de una deuda.

        interes_mes  = saldo * (tasa_mensual_pct / 100)
        capital      = pago - interes_mes
        nuevo_saldo  = saldo - capital

    Args:
        saldo: saldo actual (Decimal, se asume >= 0)
        tasa_mensual_pct: tasa de interes MENSUAL en porcentaje, tal como la
            cobran tarjetas y prestamos (2.5 significa 2.5% mensual)
        pago_mensual: pago total mensual (minimo + extra)

    Returns dict con: meses, interes_total, total_pagado, se_liquido,
                       advertencia_no_amortiza, advertencia_limite
    """
    resultado = {
        "meses": 0,
        "interes_total": CERO,
        "total_pagado": CERO,
        "se_liquido": False,
        "advertencia_no_amortiza": False,
        "advertencia_limite": False,
    }

    if saldo <= CERO:
        resultado["se_liquido"] = True
        return resultado

    if pago_mensual <= CERO:
        resultado["advertencia_no_amortiza"] = True
        return resultado

    tasa_mensual = tasa_mensual_pct / Decimal(100)

    interes_primer_mes = saldo * tasa_mensual
    if pago_mensual <= interes_primer_mes:
        # El pago no alcanza siquiera a cubrir el interes: la deuda no baja.
        resultado["advertencia_no_amortiza"] = True
        resultado["interes_total"] = interes_primer_mes
        return resultado

    saldo_restante = saldo
    meses = 0
    interes_acumulado = CERO
    total_pagado = CERO

    while saldo_restante > CERO and meses < limite_meses:
        interes_mes = saldo_restante * tasa_mensual
        capital = pago_mensual - interes_mes
        pago_real = pago_mensual

        if capital >= saldo_restante:
            capital = saldo_restante
            pago_real = capital + interes_mes

        saldo_restante -= capital
        interes_acumulado += interes_mes
        total_pagado += pago_real
        meses += 1

        if saldo_restante <= CERO:
            saldo_restante = CERO
            resultado["se_liquido"] = True
            break

    if not resultado["se_liquido"]:
        resultado["advertencia_limite"] = True

    resultado["meses"] = meses
    resultado["interes_total"] = interes_acumulado
    resultado["total_pagado"] = total_pagado
    return resultado


# ---------------------------------------------------------------------------
# 5. PANTALLAS
# ---------------------------------------------------------------------------

def linea(caracter: str = "-") -> None:
    print(caracter * _ancho_panel())


def titulo(texto: str) -> None:
    linea("=")
    print(texto.center(_ancho_panel()))
    linea("=")


def fila(etiqueta: str, valor: str, ancho_etiqueta: int = 32) -> None:
    """
    Una linea "etiqueta ....... valor" con el valor alineado a la derecha.
    Si la etiqueta es mas larga que el ancho reservado (ej. un gasto con
    nombre largo y su vencimiento), el campo de la etiqueta se ensancha (o
    se recorta con "..." si no cabe) para que el monto siga cayendo en la
    misma columna que el resto de las filas.
    """
    ancho = _ancho_panel()
    maximo = max(ancho - len(valor) - 2, 10)
    if len(etiqueta) > maximo:
        etiqueta = etiqueta[:maximo - 3] + "..."
    ancho_etiqueta = max(ancho_etiqueta, len(etiqueta) + 1)
    print(f"{etiqueta:<{ancho_etiqueta}}{valor:>{max(ancho - ancho_etiqueta, 8)}}")


def imprimir_bloque(lineas: List[str]) -> None:
    """
    Imprime varias lineas como un solo bloque, con el MISMO margen izquierdo
    para todas.

    print() centra cada linea segun su propio ancho visible. Si se imprimen
    lineas de distinto largo una por una (una lista numerada, un menu en
    columnas), cada una recibe un margen distinto y el bloque se ve
    "chueco" / desalineado, aunque el contenido de cada linea este bien
    formado. Aqui se calcula el ancho de la linea mas larga del bloque y se
    rellenan todas a ese mismo ancho antes de imprimir, asi el centrado sale
    identico para todas y el bloque completo queda alineado a la izquierda
    entre si (y centrado como una sola unidad en la terminal).
    """
    if not lineas:
        return
    ancho_max = max(len(l) for l in lineas)
    for l in lineas:
        print(l.ljust(ancho_max))


def mostrar_resumen(datos: Dict[str, Any]) -> None:
    print()
    titulo("FINANZAS FAMILIARES")
    mes = mes_trabajo_clave(datos) or "(sin mes de trabajo: usa [8] Meses)"
    print(f"Mes actual: {mes}")
    print()

    print("INGRESOS")
    linea()
    por_persona = ingresos_por_persona(datos)
    if not por_persona:
        print("  (sin ingresos registrados)")
    for persona, monto in por_persona.items():
        fila(persona, fmt_money(monto))
    linea()
    fila("  Recurrentes", fmt_money(ingreso_recurrente_total(datos)))
    fila("  Adicionales (no se copian)", fmt_money(ingreso_adicional_total(datos)))
    fila("TOTAL", fmt_money(ingreso_total_mes(datos)))
    print()

    for b in range(1, num_bloques(datos) + 1):
        print(f"BLOQUE {b}")
        linea()
        fila("Ingresos", fmt_money(ingreso_total_bloque(datos, b)))
        fila("Comprometido (bills + minimos)", fmt_money(comprometido_bloque(datos, b)))
        fila("Disponible", fmt_money(disponible_bloque(datos, b)))
        print()

    print("GASTOS FAMILIARES (presupuesto variable)")
    linea()
    fila("Total presupuestado", fmt_money(gastos_variables_total(datos)))
    print()

    print("TOTAL DISPONIBLE DEL MES")
    linea()
    fila("Disponible", fmt_money(disponible_total(datos)))
    print()

    print("DEUDAS")
    linea()
    fila("Saldo total", fmt_money(saldo_deuda_total(datos)))
    fila("Pagos minimos", fmt_money(pago_minimo_total(datos)))
    fila("Pagos extra", fmt_money(pago_extra_total(datos)))
    print()

    ahorro = datos["ahorro"]
    actual = to_decimal(ahorro.get("actual"), CERO)
    meta = to_decimal(ahorro.get("meta"), CERO)
    faltante = meta - actual
    if faltante < CERO:
        faltante = CERO
    print("AHORRO")
    linea()
    fila("Actual", fmt_money(actual))
    fila("Meta", fmt_money(meta))
    fila("Faltante", fmt_money(faltante))
    print()

    dist = _fuente_mes(datos)["distribucion"]
    print("DISTRIBUCION DEL DISPONIBLE")
    linea()
    fila("Ahorro asignado", fmt_money(to_decimal(dist.get("ahorro"), CERO)))
    fila("Pago extra a deudas", fmt_money(pago_extra_total(datos)))
    fila("Familia / otros", fmt_money(to_decimal(dist.get("familia_otros"), CERO)))
    fila("Reserva", fmt_money(to_decimal(dist.get("reserva"), CERO)))
    linea()
    fila("TOTAL ASIGNADO", fmt_money(distribucion_total(datos)))
    print()

    sin_asignar = dinero_sin_asignar(datos)
    print("DINERO SIN ASIGNAR")
    linea()
    fila("", fmt_money(sin_asignar))
    if sin_asignar < CERO:
        print()
        print("ERROR: La cantidad asignada supera el dinero disponible.")
    print()

    linea("=")
    imprimir_bloque([
        "[1] Ingresos      [2] Bloques        [3] Gastos",
        "[4] Deudas        [5] Ahorro         [6] Distribucion",
        "[7] Analizar deudas                  [8] Meses",
        "[9] Detalle de gastos                [0] Salir",
    ])
    linea("=")


# ---------------------------------------------------------------------------
# 6. MENUS / EDICION DE DATOS
# ---------------------------------------------------------------------------

def _requiere_mes(datos: Dict[str, Any]) -> bool:
    """Ingresos, bloques, gastos y distribucion viven dentro de un mes."""
    if mes_trabajo_clave(datos) is not None:
        return True
    print("\nNo hay ningun mes de trabajo seleccionado.")
    print("Ve a [8] Meses para crear o seleccionar uno.")
    pausar()
    return False


def _seleccionar_indice(items: List[dict], etiqueta_campo: str, prompt: str) -> Optional[int]:
    if not items:
        print(f"No hay {etiqueta_campo} registrados.")
        return None
    idx = leer_entero(prompt, minimo=1, maximo=len(items))
    return idx - 1


def _activar_desactivar(datos: Dict[str, Any], items: List[dict], etiqueta_campo: str) -> None:
    idx = _seleccionar_indice(items, etiqueta_campo, f"Cual {etiqueta_campo} activar/desactivar? (1-{len(items)}): ")
    if idx is None:
        return
    items[idx]["activo"] = not items[idx].get("activo", True)
    guardar_datos(datos)
    estado = "activado" if items[idx]["activo"] else "desactivado"
    print(f"Elemento {estado} y guardado.")


def _eliminar(datos: Dict[str, Any], items: List[dict], etiqueta_campo: str) -> None:
    idx = _seleccionar_indice(items, etiqueta_campo, f"Cual {etiqueta_campo} eliminar? (1-{len(items)}): ")
    if idx is None:
        return
    if leer_si_no("Seguro que deseas eliminarlo permanentemente?"):
        items.pop(idx)
        guardar_datos(datos)
        print("Elemento eliminado y guardado.")
    else:
        print("Cancelado.")


# --- INGRESOS ---------------------------------------------------------------

def menu_ingresos(datos: Dict[str, Any]) -> None:
    if not _requiere_mes(datos):
        return
    while True:
        print()
        titulo("INGRESOS")
        items = _fuente_mes(datos)["ingresos"]
        if not items:
            print("  (sin ingresos registrados)")
        lineas = []
        for idx, i in enumerate(items, start=1):
            estado = "" if i.get("activo", True) else "  [INACTIVO]"
            desc = f" ({i['descripcion']})" if i.get("descripcion") else ""
            monto = to_decimal(i.get("monto"), CERO)
            adicional = "" if i.get("recurrente", True) else "  [ADICIONAL]"
            lineas.append(f"{idx}. {i['persona']}{desc} - Bloque {i['bloque']}: "
                          f"{fmt_money(monto)} [{i.get('frecuencia', 'MENSUAL')}]{adicional}{estado}")
        imprimir_bloque(lineas)
        linea()
        fila("Recurrentes", fmt_money(ingreso_recurrente_total(datos)))
        fila("Adicionales (no se copian)", fmt_money(ingreso_adicional_total(datos)))
        fila("TOTAL", fmt_money(ingreso_total_mes(datos)))
        print()
        print("[A] Agregar  [E] Editar  [D] Activar/Desactivar  [X] Eliminar  [0] Volver")
        opcion = leer_texto("> ").strip().lower()

        if opcion == "0":
            return
        elif opcion == "a":
            _agregar_ingreso(datos)
        elif opcion == "e":
            _editar_ingreso(datos)
        elif opcion == "d":
            _activar_desactivar(datos, items, "ingreso")
        elif opcion == "x":
            _eliminar(datos, items, "ingreso")
        else:
            print("Opcion invalida.")


def _leer_tipo_ingreso(recurrente_actual: bool) -> bool:
    """Pregunta si el ingreso es recurrente (se copia al mes siguiente) o adicional (no se copia)."""
    imprimir_bloque([
        "Tipo de ingreso:",
        "  [1] Recurrente (se copia al crear el siguiente mes)",
        "  [2] Adicional (NO se copia: bono, reembolso, un solo mes)",
    ])
    por_defecto = 1 if recurrente_actual else 2
    return leer_entero(f"Tipo [{por_defecto}]: ", por_defecto, 1, 2) == 1


def _agregar_ingreso(datos: Dict[str, Any]) -> None:
    print("\nNUEVO INGRESO")
    persona = leer_texto_simple("Persona (ej. Yo, Esposa): ")
    if not persona:
        print("Se cancelo: se necesita un nombre de persona.")
        return
    descripcion = leer_texto_simple("Descripcion (opcional): ")
    bloque = leer_entero(f"Bloque (1-{num_bloques(datos)}): ", minimo=1, maximo=num_bloques(datos))
    monto = leer_decimal("Monto: $", permitir_vacio=False)
    frecuencia = leer_texto_simple("Frecuencia (QUINCENAL/MENSUAL/OTRO) [MENSUAL]: ", "MENSUAL").upper()
    if frecuencia not in FRECUENCIAS_VALIDAS:
        frecuencia = "OTRO"
    recurrente = _leer_tipo_ingreso(True)
    _fuente_mes(datos)["ingresos"].append({
        "persona": persona,
        "descripcion": descripcion,
        "bloque": bloque,
        "monto": str(redondear(monto)),
        "frecuencia": frecuencia,
        "activo": True,
        "recurrente": recurrente,
    })
    guardar_datos(datos)
    print("Ingreso agregado y guardado.")


def _editar_ingreso(datos: Dict[str, Any]) -> None:
    items = _fuente_mes(datos)["ingresos"]
    idx = _seleccionar_indice(items, "ingreso", f"Cual ingreso editar? (1-{len(items)}): ")
    if idx is None:
        return
    i = items[idx]
    print(f"\nEditando: {i['persona']} - Bloque {i['bloque']}")
    i["persona"] = leer_texto_simple(f"Persona [{i['persona']}]: ", i["persona"])
    i["descripcion"] = leer_texto_simple(f"Descripcion [{i.get('descripcion', '')}]: ", i.get("descripcion", ""))
    i["bloque"] = leer_entero(f"Bloque [{i['bloque']}]: ", i["bloque"], 1, num_bloques(datos))
    actual = to_decimal(i["monto"], CERO)
    print(f"Monto actual: {fmt_money(actual)}")
    nuevo = leer_decimal("Nuevo monto (Enter para conservar): $", actual)
    i["monto"] = str(redondear(nuevo))
    i["recurrente"] = _leer_tipo_ingreso(i.get("recurrente", True))
    guardar_datos(datos)
    print("Ingreso actualizado y guardado.")


# --- BLOQUES (solo consulta: la composicion viene de ingresos/gastos/deudas) --

def menu_bloques(datos: Dict[str, Any]) -> None:
    if not _requiere_mes(datos):
        return
    while True:
        print()
        titulo("BLOQUES DE PAGO")
        for b in range(1, num_bloques(datos) + 1):
            print(f"\nBLOQUE {b}")
            linea()
            fila("Ingresos", fmt_money(ingreso_total_bloque(datos, b)))
            fila("  Bills", fmt_money(bills_total_bloque(datos, b)), 32)
            fila("  Pagos minimos de deuda", fmt_money(pago_minimo_total_bloque(datos, b)), 32)
            fila("Comprometido", fmt_money(comprometido_bloque(datos, b)))
            fila("Disponible", fmt_money(disponible_bloque(datos, b)))
        print()
        print("[N] Cambiar numero de bloques   [0] Volver")
        opcion = leer_texto("> ").strip().lower()
        if opcion == "0":
            return
        elif opcion == "n":
            actual = num_bloques(datos)
            nuevo = leer_entero(f"Numero de bloques actual: {actual}. Nuevo valor: ", actual, minimo=1, maximo=12)
            _fuente_mes(datos)["num_bloques"] = nuevo
            guardar_datos(datos)
            print("Numero de bloques actualizado y guardado.")
        else:
            print("Opcion invalida.")


# --- GASTOS ------------------------------------------------------------------

def menu_gastos(datos: Dict[str, Any]) -> None:
    if not _requiere_mes(datos):
        return
    while True:
        print()
        titulo("GASTOS")
        bills = gastos_activos(datos, CATEGORIA_BILL) + [g for g in _fuente_mes(datos)["gastos"]
                                                          if g.get("categoria") == CATEGORIA_BILL and not g.get("activo", True)]
        variables = [g for g in _fuente_mes(datos)["gastos"] if g.get("categoria") == CATEGORIA_VARIABLE]
        todos = _fuente_mes(datos)["gastos"]

        print("\nGASTOS FIJOS / BILLS")
        linea()
        lineas_bills = []
        for idx, g in enumerate(todos, start=1):
            if g.get("categoria") != CATEGORIA_BILL:
                continue
            estado = "" if g.get("activo", True) else "  [INACTIVO]"
            venc = f", vence dia {g['vencimiento']}" if g.get("vencimiento") else ""
            lineas_bills.append(f"{idx}. {g['nombre']} - Bloque {g['bloque']}: "
                                 f"{fmt_money(to_decimal(g['monto'], CERO))}{venc}{estado}")
        if not lineas_bills:
            print("  (sin bills registrados)")
        imprimir_bloque(lineas_bills)

        print("\nGASTOS FAMILIARES (presupuesto variable)")
        linea()
        lineas_var = []
        for idx, g in enumerate(todos, start=1):
            if g.get("categoria") != CATEGORIA_VARIABLE:
                continue
            estado = "" if g.get("activo", True) else "  [INACTIVO]"
            lineas_var.append(f"{idx}. {g['nombre']}: {fmt_money(to_decimal(g['monto'], CERO))}{estado}")
        if not lineas_var:
            print("  (sin gastos variables registrados)")
        imprimir_bloque(lineas_var)

        print()
        imprimir_bloque([
            "[A] Agregar bill   [V] Agregar gasto variable   [E] Editar",
            "[D] Activar/Desactivar   [X] Eliminar   [T] Detalle   [0] Volver",
        ])
        opcion = leer_texto("> ").strip().lower()

        if opcion == "0":
            return
        elif opcion == "a":
            _agregar_gasto(datos, CATEGORIA_BILL)
        elif opcion == "v":
            _agregar_gasto(datos, CATEGORIA_VARIABLE)
        elif opcion == "e":
            _editar_gasto(datos)
        elif opcion == "d":
            _activar_desactivar(datos, todos, "gasto")
        elif opcion == "x":
            _eliminar(datos, todos, "gasto")
        elif opcion == "t":
            menu_detalle_gastos(datos)
        else:
            print("Opcion invalida.")


def _agregar_gasto(datos: Dict[str, Any], categoria: str) -> None:
    print(f"\nNUEVO GASTO ({'bill fijo' if categoria == CATEGORIA_BILL else 'familiar variable'})")
    nombre = leer_texto_simple("Nombre: ")
    if not nombre:
        print("Se cancelo: se necesita un nombre.")
        return
    monto = leer_decimal("Monto: $", permitir_vacio=False)
    bloque = None
    vencimiento = None
    if categoria == CATEGORIA_BILL:
        bloque = leer_entero(f"Bloque (1-{num_bloques(datos)}): ", minimo=1, maximo=num_bloques(datos))
        texto_venc = leer_texto_simple("Dia de vencimiento (1-31, opcional): ")
        if texto_venc.isdigit():
            vencimiento = int(texto_venc)
    _fuente_mes(datos)["gastos"].append({
        "nombre": nombre,
        "categoria": categoria,
        "monto": str(redondear(monto)),
        "bloque": bloque,
        "vencimiento": vencimiento,
        "activo": True,
    })
    guardar_datos(datos)
    print("Gasto agregado y guardado.")


def _editar_gasto(datos: Dict[str, Any]) -> None:
    items = _fuente_mes(datos)["gastos"]
    idx = _seleccionar_indice(items, "gasto", f"Cual gasto editar? (1-{len(items)}): ")
    if idx is None:
        return
    g = items[idx]
    print(f"\nEditando: {g['nombre']} ({g['categoria']})")
    g["nombre"] = leer_texto_simple(f"Nombre [{g['nombre']}]: ", g["nombre"])
    actual = to_decimal(g["monto"], CERO)
    print(f"Monto actual: {fmt_money(actual)}")
    nuevo = leer_decimal("Nuevo monto (Enter para conservar): $", actual)
    g["monto"] = str(redondear(nuevo))
    if g.get("categoria") == CATEGORIA_BILL:
        g["bloque"] = leer_entero(f"Bloque [{g.get('bloque')}]: ", g.get("bloque", 1), 1, num_bloques(datos))
        venc_actual = g.get("vencimiento")
        texto_venc = leer_texto_simple(f"Dia de vencimiento [{venc_actual if venc_actual else 'sin definir'}]: ",
                                        str(venc_actual) if venc_actual else "")
        if texto_venc.isdigit():
            g["vencimiento"] = int(texto_venc)
    guardar_datos(datos)
    print("Gasto actualizado y guardado.")


# --- DETALLE DE GASTOS (solo lectura) -----------------------------------------

def _imprimir_gastos_por_bloques(datos: Dict[str, Any]) -> None:
    gastos = _fuente_mes(datos)["gastos"]
    total_general = CERO

    for b in range(1, num_bloques(datos) + 1):
        print(f"\nBLOQUE {b}")
        linea()
        bills_bloque = [g for g in gastos if g.get("categoria") == CATEGORIA_BILL and _bloque_de(g) == b]
        if not bills_bloque:
            print("  (sin bills en este bloque)")
        subtotal = CERO
        for g in bills_bloque:
            estado = "" if g.get("activo", True) else "  [INACTIVO]"
            venc = f", vence dia {g['vencimiento']}" if g.get("vencimiento") else ""
            monto = to_decimal(g.get("monto"), CERO)
            fila(f"{g.get('nombre', '')}{venc}{estado}", fmt_money(monto))
            if g.get("activo", True):
                subtotal += monto
        linea()
        fila(f"Subtotal bloque {b}", fmt_money(subtotal))
        total_general += subtotal

    print("\nGASTOS FAMILIARES (variables, no van por bloque)")
    linea()
    variables = [g for g in gastos if g.get("categoria") == CATEGORIA_VARIABLE]
    if not variables:
        print("  (sin gastos variables registrados)")
    subtotal_var = CERO
    for g in variables:
        estado = "" if g.get("activo", True) else "  [INACTIVO]"
        monto = to_decimal(g.get("monto"), CERO)
        fila(f"{g.get('nombre', '')}{estado}", fmt_money(monto))
        if g.get("activo", True):
            subtotal_var += monto
    linea()
    fila("Subtotal gastos variables", fmt_money(subtotal_var))
    total_general += subtotal_var

    print()
    linea("=")
    fila("TOTAL GENERAL DE GASTOS (activos)", fmt_money(total_general))


def _imprimir_gastos_lista(datos: Dict[str, Any]) -> None:
    gastos = _fuente_mes(datos)["gastos"]
    print("\nLISTA COMPLETA DE GASTOS")
    linea()
    if not gastos:
        print("  (sin gastos registrados)")
    total = CERO
    for g in gastos:
        estado = "" if g.get("activo", True) else "  [INACTIVO]"
        if g.get("categoria") == CATEGORIA_BILL:
            extra = f"Bill, Bloque {g.get('bloque')}" if g.get("bloque") else "Bill"
            extra += f", dia {g['vencimiento']}" if g.get("vencimiento") else ""
        else:
            extra = "Variable"
        monto = to_decimal(g.get("monto"), CERO)
        fila(f"{g.get('nombre', '')} [{extra}]{estado}", fmt_money(monto))
        if g.get("activo", True):
            total += monto
    print()
    linea("=")
    fila("TOTAL (activos)", fmt_money(total))


def menu_detalle_gastos(datos: Dict[str, Any]) -> None:
    """
    Detalle de gastos del mes de trabajo, con dos vistas que se alternan:
    por bloques (bills agrupados por bloque con subtotal, y luego los
    variables) o lista completa (todo de corrido). No modifica nada.
    """
    if not _requiere_mes(datos):
        return
    modo = "bloques"
    while True:
        print()
        titulo("DETALLE DE GASTOS")
        if modo == "bloques":
            print("Vista: POR BLOQUES")
            _imprimir_gastos_por_bloques(datos)
        else:
            print("Vista: LISTA COMPLETA")
            _imprimir_gastos_lista(datos)
        print()
        cambiar = "[V] Ver lista completa" if modo == "bloques" else "[V] Ver por bloques"
        print(f"{cambiar}   [0] Volver")
        opcion = leer_texto("> ").strip().lower()
        if opcion == "0":
            return
        elif opcion == "v":
            modo = "lista" if modo == "bloques" else "bloques"
        else:
            print("Opcion invalida.")


# --- DEUDAS --------------------------------------------------------------

def menu_deudas(datos: Dict[str, Any]) -> None:
    while True:
        print()
        titulo("DEUDAS")
        items = datos["deudas"]
        if not items:
            print("  (sin deudas registradas)")
        lineas = []
        for idx, d in enumerate(items, start=1):
            estado = "" if d.get("activo", True) else "  [INACTIVA]"
            saldo = to_decimal(d.get("saldo"), CERO)
            lineas.append(f"{idx}. {d['nombre']} - Saldo {fmt_money(saldo)} - "
                          f"Interes {fmt_pct(to_decimal(d.get('interes_mensual'), CERO))} - "
                          f"Min {fmt_money(to_decimal(d.get('pago_minimo'), CERO))} - "
                          f"Extra {fmt_money(to_decimal(d.get('pago_extra'), CERO))}{estado}")
        imprimir_bloque(lineas)
        print()
        fila("SALDO TOTAL", fmt_money(saldo_deuda_total(datos)))
        fila("PAGOS MINIMOS TOTALES", fmt_money(pago_minimo_total(datos)))
        fila("PAGOS EXTRA TOTALES", fmt_money(pago_extra_total(datos)))
        fila("PAGO TOTAL PLANIFICADO", fmt_money(pago_minimo_total(datos) + pago_extra_total(datos)))
        print()
        print("[A] Agregar deuda   [E] Editar   [D] Activar/Desactivar   [X] Eliminar   [0] Volver")
        opcion = leer_texto("> ").strip().lower()

        if opcion == "0":
            return
        elif opcion == "a":
            _agregar_deuda(datos)
        elif opcion == "e":
            _editar_deuda(datos)
        elif opcion == "d":
            _activar_desactivar(datos, items, "deuda")
        elif opcion == "x":
            _eliminar(datos, items, "deuda")
        else:
            print("Opcion invalida.")


def _agregar_deuda(datos: Dict[str, Any]) -> None:
    print("\nNUEVA DEUDA")
    nombre = leer_texto_simple("Nombre (ej. Visa): ")
    if not nombre:
        print("Se cancelo: se necesita un nombre.")
        return
    saldo = leer_decimal("Saldo actual: $", permitir_vacio=False)
    interes = leer_decimal("Tasa de interes mensual (ej. 2.5 para 2.5% mensual): ", permitir_vacio=False)
    pago_minimo = leer_decimal("Pago minimo: $", permitir_vacio=False)
    pago_extra = leer_decimal("Pago extra (0 si no aplica): $", CERO)
    bloque = leer_entero(f"Bloque en que se paga (1-{num_bloques(datos)}): ", 1, 1, num_bloques(datos))
    texto_venc = leer_texto_simple("Dia de vencimiento (1-31, opcional): ")
    vencimiento = int(texto_venc) if texto_venc.isdigit() else None
    datos["deudas"].append({
        "nombre": nombre,
        "saldo": str(redondear(saldo)),
        "interes_mensual": str(redondear(interes)),
        "pago_minimo": str(redondear(pago_minimo)),
        "pago_extra": str(redondear(pago_extra)),
        "bloque": bloque,
        "vencimiento": vencimiento,
        "activo": True,
    })
    guardar_datos(datos)
    print("Deuda agregada y guardada.")


def _editar_deuda(datos: Dict[str, Any]) -> None:
    items = datos["deudas"]
    idx = _seleccionar_indice(items, "deuda", f"Cual deuda editar? (1-{len(items)}): ")
    if idx is None:
        return
    d = items[idx]
    print(f"\nEditando: {d['nombre']}")
    imprimir_bloque([
        "1. Saldo actual",
        "2. Tasa de interes mensual",
        "3. Pago minimo",
        "4. Pago extra",
        "5. Bloque",
        "6. Dia de vencimiento",
        "7. Nombre",
        "0. Volver",
    ])
    campo = leer_texto("Que deseas editar? > ").strip()

    if campo == "1":
        actual = to_decimal(d["saldo"], CERO)
        print(f"Saldo actual: {fmt_money(actual)}")
        d["saldo"] = str(redondear(leer_decimal("Nuevo saldo: $", actual)))
    elif campo == "2":
        actual = to_decimal(d["interes_mensual"], CERO)
        print(f"Interes mensual actual: {fmt_pct(actual)}")
        d["interes_mensual"] = str(redondear(leer_decimal("Nueva tasa mensual (%): ", actual)))
    elif campo == "3":
        actual = to_decimal(d["pago_minimo"], CERO)
        print(f"Pago minimo actual: {fmt_money(actual)}")
        d["pago_minimo"] = str(redondear(leer_decimal("Nuevo pago minimo: $", actual)))
    elif campo == "4":
        actual = to_decimal(d["pago_extra"], CERO)
        print(f"Pago extra actual: {fmt_money(actual)}")
        d["pago_extra"] = str(redondear(leer_decimal("Nuevo pago extra: $", actual)))
    elif campo == "5":
        d["bloque"] = leer_entero(f"Bloque [{d.get('bloque')}]: ", d.get("bloque", 1), 1, num_bloques(datos))
    elif campo == "6":
        texto_venc = leer_texto_simple(f"Dia de vencimiento [{d.get('vencimiento')}]: ")
        if texto_venc.isdigit():
            d["vencimiento"] = int(texto_venc)
    elif campo == "7":
        d["nombre"] = leer_texto_simple(f"Nombre [{d['nombre']}]: ", d["nombre"])
    elif campo == "0":
        return
    else:
        print("Opcion invalida.")
        return

    guardar_datos(datos)
    print("Deuda actualizada y guardada.")


# --- AHORRO ----------------------------------------------------------------

def menu_ahorro(datos: Dict[str, Any]) -> None:
    while True:
        ahorro = datos["ahorro"]
        actual = to_decimal(ahorro.get("actual"), CERO)
        meta = to_decimal(ahorro.get("meta"), CERO)
        faltante = meta - actual
        if faltante < CERO:
            faltante = CERO
        if meta > CERO:
            porcentaje = (actual / meta) * Decimal(100)
        else:
            porcentaje = CERO

        print()
        titulo("AHORRO")
        fila("Ahorro actual", fmt_money(actual))
        fila("Meta", fmt_money(meta))
        fila("Faltante", fmt_money(faltante))
        fila("Porcentaje alcanzado", fmt_pct(porcentaje))
        print()
        print("[1] Editar ahorro actual   [2] Editar meta   [0] Volver")
        opcion = leer_texto("> ").strip()

        if opcion == "0":
            return
        elif opcion == "1":
            ahorro["actual"] = str(redondear(leer_decimal(f"Ahorro actual [{fmt_money(actual)}]: $", actual)))
            guardar_datos(datos)
            print("Ahorro actualizado y guardado.")
        elif opcion == "2":
            ahorro["meta"] = str(redondear(leer_decimal(f"Meta [{fmt_money(meta)}]: $", meta)))
            guardar_datos(datos)
            print("Meta actualizada y guardada.")
        else:
            print("Opcion invalida.")


# --- DISTRIBUCION ------------------------------------------------------------

def menu_distribucion(datos: Dict[str, Any]) -> None:
    if not _requiere_mes(datos):
        return
    while True:
        dist = _fuente_mes(datos)["distribucion"]
        disponible = disponible_total(datos)

        print()
        titulo("DISTRIBUCION DEL DISPONIBLE")
        fila("Disponible del mes", fmt_money(disponible))
        print()
        fila("1. Ahorro asignado", fmt_money(to_decimal(dist.get("ahorro"), CERO)))
        fila("   Pago extra a deudas", fmt_money(pago_extra_total(datos)))
        print("      (se administra en el menu [4] Deudas, no aqui)")
        fila("2. Familia / otros", fmt_money(to_decimal(dist.get("familia_otros"), CERO)))
        fila("3. Reserva", fmt_money(to_decimal(dist.get("reserva"), CERO)))
        linea()
        total_asignado = distribucion_total(datos)
        fila("TOTAL ASIGNADO", fmt_money(total_asignado))
        fila("SIN ASIGNAR", fmt_money(disponible - total_asignado))
        if disponible - total_asignado < CERO:
            print("\nERROR: La cantidad asignada supera el dinero disponible.")
        print()
        print("[1] Editar ahorro asignado   [2] Editar familia/otros   [3] Editar reserva   [0] Volver")
        opcion = leer_texto("> ").strip()

        if opcion == "0":
            return
        elif opcion in ("1", "2", "3"):
            campo = {"1": "ahorro", "2": "familia_otros", "3": "reserva"}[opcion]
            actual = to_decimal(dist.get(campo), CERO)
            nuevo = redondear(leer_decimal(f"Valor actual: {fmt_money(actual)}\nNuevo valor: $", actual))
            # comprobacion: total asignado no debe superar el disponible
            otros = total_asignado - actual
            if otros + nuevo > disponible:
                print(f"\nADVERTENCIA: Esto dejaria el total asignado en {fmt_money(otros + nuevo)}, "
                      f"que supera el disponible ({fmt_money(disponible)}).")
                if not leer_si_no("Deseas guardarlo de todas formas?"):
                    print("Cancelado, no se guardaron cambios.")
                    continue
            dist[campo] = str(nuevo)
            guardar_datos(datos)
            print("Distribucion actualizada y guardada.")
        else:
            print("Opcion invalida.")


# --- ANALIZAR DEUDAS ---------------------------------------------------------

def _imprimir_analisis(etiqueta: str, saldo: Decimal, interes_mensual: Decimal, pago: Decimal,
                        resultado: Dict[str, Any]) -> None:
    print(f"\n{etiqueta}")
    linea()
    fila("Saldo", fmt_money(saldo))
    fila("Interes mensual", fmt_pct(interes_mensual))
    fila("Pago mensual", fmt_money(pago))
    if resultado["se_liquido"]:
        fila("Meses estimados", str(resultado["meses"]))
        fila("Interes estimado", fmt_money(resultado["interes_total"]))
        fila("Total pagado", fmt_money(resultado["total_pagado"]))
    elif resultado["advertencia_no_amortiza"]:
        print("\nADVERTENCIA:")
        print("Con este pago la deuda no disminuye de forma suficiente.")
        print("El pago mensual es igual o inferior al interes calculado.")
    elif resultado["advertencia_limite"]:
        fila("Meses simulados", f">= {LIMITE_MESES_SIMULACION}")
        fila("Interes acumulado (parcial)", fmt_money(resultado["interes_total"]))
        print("\nADVERTENCIA: la deuda no se liquida dentro del limite de simulacion")
        print(f"({LIMITE_MESES_SIMULACION} meses). Revisa el pago o la tasa de interes.")


def _calculadora_rapida_deuda() -> None:
    """
    Calcula cuanto se tarda en pagar una deuda que NO esta registrada en el
    sistema (por ejemplo, para probar un numero antes de decidir si la das de
    alta). No guarda nada. Reutiliza el mismo motor (simular_deuda) que usa
    el analisis de las deudas ya registradas, para que el resultado sea
    siempre consistente con el resto del programa.
    """
    print()
    titulo("CALCULADORA RAPIDA (deuda no registrada)")
    print("Para probar un numero sin tener que registrar la deuda. No se guarda nada.")

    saldo = leer_decimal("Saldo de la deuda: $", permitir_vacio=False)
    if saldo <= CERO:
        print("El saldo debe ser mayor que cero.")
        return

    interes = leer_decimal("Tasa de interes mensual (ej. 2.5 para 2.5% mensual): ", permitir_vacio=False)
    if interes < CERO:
        print("La tasa de interes no puede ser negativa.")
        return

    pago = leer_decimal("Pago mensual: $", permitir_vacio=False)
    if pago <= CERO:
        print("El pago debe ser mayor que cero.")
        return

    resultado = simular_deuda(saldo, interes, pago)
    _imprimir_analisis("RESULTADO", saldo, interes, pago, resultado)
    pausar()


def menu_analizar_deudas(datos: Dict[str, Any]) -> None:
    while True:
        print()
        titulo("ANALIZAR DEUDAS")
        items = datos["deudas"]
        if not items:
            print("No hay deudas registradas.")
            print()
            print("[C] Calculadora rapida (deuda no registrada)   [0] Volver")
            opcion = leer_texto("> ").strip().lower()
            if opcion == "c":
                _calculadora_rapida_deuda()
            elif opcion == "0":
                return
            else:
                print("Opcion invalida.")
            continue

        print("\nRESUMEN DE DEUDAS")
        linea()
        lineas = []
        for idx, d in enumerate(items, start=1):
            estado = "" if d.get("activo", True) else "  [INACTIVA]"
            lineas.append(f"{idx}. {d['nombre']:<20}{fmt_money(to_decimal(d['saldo'], CERO)):>15}{estado}")
        imprimir_bloque(lineas)
        linea()
        fila("TOTAL", fmt_money(saldo_deuda_total(datos)))
        fila("Pagos minimos totales", fmt_money(pago_minimo_total(datos)))
        fila("Pagos extra totales", fmt_money(pago_extra_total(datos)))
        fila("Pago total planificado", fmt_money(pago_minimo_total(datos) + pago_extra_total(datos)))

        print()
        print("[1-N] Analizar una deuda especifica   [E] Estrategias (avalancha / bola de nieve)")
        print("[C] Calculadora rapida (deuda no registrada)   [0] Volver")
        opcion = leer_texto("> ").strip().lower()

        if opcion == "0":
            return
        elif opcion == "e":
            _mostrar_estrategias(datos)
        elif opcion == "c":
            _calculadora_rapida_deuda()
        elif opcion.isdigit() and 1 <= int(opcion) <= len(items):
            _analizar_una_deuda(datos, items[int(opcion) - 1])
        else:
            print("Opcion invalida.")


def _analizar_una_deuda(datos: Dict[str, Any], d: dict) -> None:
    while True:
        saldo = to_decimal(d["saldo"], CERO)
        interes = to_decimal(d["interes_mensual"], CERO)
        pago_minimo = to_decimal(d["pago_minimo"], CERO)
        pago_extra = to_decimal(d["pago_extra"], CERO)
        pago_total = pago_minimo + pago_extra

        print()
        titulo(d["nombre"].upper())
        fila("Saldo actual", fmt_money(saldo))
        fila("Interes mensual", fmt_pct(interes))
        fila("Pago minimo", fmt_money(pago_minimo))
        fila("Pago extra", fmt_money(pago_extra))
        fila("Pago total", fmt_money(pago_total))

        resultado_actual = simular_deuda(saldo, interes, pago_total)
        _imprimir_analisis("ANALISIS ACTUAL", saldo, interes, pago_total, resultado_actual)

        print()
        print("[S] Simular otro pago total   [0] Volver")
        opcion = leer_texto("> ").strip().lower()

        if opcion == "0":
            return
        elif opcion == "s":
            nuevo_pago = leer_decimal(f"Pago actual: {fmt_money(pago_total)}\nSimular pago de: $", permitir_vacio=False)
            resultado_sim = simular_deuda(saldo, interes, nuevo_pago)
            print()
            titulo("COMPARACION")
            _imprimir_analisis("SITUACION ACTUAL", saldo, interes, pago_total, resultado_actual)
            _imprimir_analisis("SIMULACION", saldo, interes, nuevo_pago, resultado_sim)

            if resultado_actual["se_liquido"] and resultado_sim["se_liquido"]:
                print()
                diferencia_meses = resultado_actual["meses"] - resultado_sim["meses"]
                interes_ahorrado = resultado_actual["interes_total"] - resultado_sim["interes_total"]
                fila("Diferencia de meses", str(diferencia_meses))
                fila("Interes ahorrado", fmt_money(interes_ahorrado))

            print()
            print("Esta simulacion NO modifica los datos guardados automaticamente.")
            nuevo_extra = nuevo_pago - pago_minimo
            if leer_si_no(f"Guardar este nuevo pago extra ({fmt_money(nuevo_extra)}) en la deuda?"):
                if nuevo_extra < CERO:
                    print("El nuevo pago es menor que el pago minimo: no se puede guardar como pago extra.")
                else:
                    d["pago_extra"] = str(redondear(nuevo_extra))
                    guardar_datos(datos)
                    print("Pago extra actualizado y guardado.")
            else:
                print("No se guardaron cambios.")
        else:
            print("Opcion invalida.")


def _mostrar_estrategias(datos: Dict[str, Any]) -> None:
    items = [d for d in datos["deudas"] if d.get("activo", True)]
    if not items:
        print("No hay deudas activas para analizar.")
        return

    print()
    titulo("ESTRATEGIAS DE DEUDA")
    print("Estas estrategias solo muestran el ORDEN sugerido para aplicar el pago extra")
    print("disponible. No modifican los datos y ninguna se presenta como 'la mejor':")
    print("el usuario decide.")

    print("\nESTRATEGIA AVALANCHA (mayor tasa de interes primero)")
    linea()
    for d in sorted(items, key=lambda x: to_decimal(x["interes_mensual"], CERO), reverse=True):
        fila(d["nombre"], f"{fmt_pct(to_decimal(d['interes_mensual'], CERO))} - {fmt_money(to_decimal(d['saldo'], CERO))}")

    print("\nESTRATEGIA BOLA DE NIEVE (menor saldo primero)")
    linea()
    for d in sorted(items, key=lambda x: to_decimal(x["saldo"], CERO)):
        fila(d["nombre"], f"{fmt_money(to_decimal(d['saldo'], CERO))} - {fmt_pct(to_decimal(d['interes_mensual'], CERO))}")

    pausar()


# --- MES ACTUAL --------------------------------------------------------------

def menu_meses(datos: Dict[str, Any]) -> None:
    """Crear / seleccionar / renombrar / eliminar meses. Deudas y ahorro no cambian por mes."""
    while True:
        print()
        titulo("MESES")
        nombres = meses_ordenados(datos)
        trabajo = mes_trabajo_clave(datos)
        if not nombres:
            print("  (no hay meses: crea uno con [C])")
        lineas = []
        for idx, nombre in enumerate(nombres, start=1):
            marca = "   <- mes de trabajo" if nombre == trabajo else ""
            lineas.append(f"{idx}. {nombre}{marca}")
        imprimir_bloque(lineas)
        print()
        print("[S] Seleccionar   [C] Crear nuevo   [R] Renombrar   [X] Eliminar   [0] Volver")
        opcion = leer_texto("> ").strip().lower()

        if opcion == "0":
            return
        elif opcion == "c":
            _crear_mes_consola(datos)
        elif opcion in ("s", "r", "x"):
            if not nombres:
                print("No hay meses registrados.")
                continue
            idx = leer_entero(f"Cual mes? (1-{len(nombres)}): ", minimo=1, maximo=len(nombres)) - 1
            nombre = nombres[idx]
            if opcion == "s":
                datos["mes_trabajo"] = nombre
                guardar_datos(datos)
                print(f"Mes de trabajo: {nombre}")
            elif opcion == "r":
                nuevo = leer_texto_simple(f"Nuevo nombre para '{nombre}': ")
                if not nuevo:
                    print("Cancelado.")
                elif nuevo != nombre and nuevo in datos["meses"]:
                    print(f"Ya existe un mes llamado '{nuevo}'.")
                else:
                    renombrar_mes(datos, nombre, nuevo)
                    guardar_datos(datos)
                    print("Mes renombrado y guardado.")
            else:
                print(f"Se borraran los ingresos, gastos, bloques y distribucion de '{nombre}'.")
                print("(las deudas y el ahorro NO se ven afectados)")
                if leer_si_no("Seguro que deseas eliminarlo permanentemente?"):
                    eliminar_mes(datos, nombre)
                    guardar_datos(datos)
                    print("Mes eliminado y guardado.")
                else:
                    print("Cancelado.")
        else:
            print("Opcion invalida.")


def _crear_mes_consola(datos: Dict[str, Any]) -> None:
    nombre = leer_texto_simple("Nombre del mes nuevo (ej. 2026-10): ")
    if not nombre:
        print("Cancelado: se necesita un nombre.")
        return
    if nombre in datos["meses"]:
        print(f"Ya existe un mes llamado '{nombre}'.")
        return
    origen = None
    existentes = meses_ordenados(datos)
    if existentes:
        base = mes_trabajo_clave(datos) or existentes[-1]
        if leer_si_no(f"Copiar ingresos, gastos, bloques y distribucion de '{base}'?", default_si=True):
            origen = base
            print("(los ingresos ADICIONALES no se copian: son de un solo mes)")
    crear_mes(datos, nombre, origen)
    guardar_datos(datos)
    print(f"Mes '{nombre}' creado y seleccionado como mes de trabajo.")


# ---------------------------------------------------------------------------
# 7. PROGRAMA PRINCIPAL
# ---------------------------------------------------------------------------

def main() -> None:
    datos = cargar_datos()
    mes_migrado = migrar_a_meses(datos)
    if mes_migrado:
        guardar_datos(datos)
        print(f"\nAVISO: tus datos anteriores (ingresos, gastos, bloques y distribucion) se")
        print(f"guardaron como tu primer mes: '{mes_migrado}'. Deudas y ahorro siguen siendo globales.")
        print("Desde [8] Meses puedes crear meses nuevos, con opcion de copiar estos datos.\n")

    while True:
        mostrar_resumen(datos)
        opcion = leer_texto("> ").strip().lower()

        if opcion == "1":
            menu_ingresos(datos)
        elif opcion == "2":
            menu_bloques(datos)
        elif opcion == "3":
            menu_gastos(datos)
        elif opcion == "4":
            menu_deudas(datos)
        elif opcion == "5":
            menu_ahorro(datos)
        elif opcion == "6":
            menu_distribucion(datos)
        elif opcion == "7":
            menu_analizar_deudas(datos)
        elif opcion == "8":
            menu_meses(datos)
        elif opcion == "9":
            menu_detalle_gastos(datos)
        elif opcion == "0":
            print("\nHasta luego.")
            return
        else:
            print("Opcion invalida.")


if __name__ == "__main__":
    main()
