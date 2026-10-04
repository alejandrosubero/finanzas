#!/usr/bin/env python3
"""
finanzas_tui.py -- Manejo Financiero Familiar (interfaz de pantallas)
======================================================================

Misma aplicacion que finanzas.py, pero con pantallas de verdad (Textual)
en vez de menus de texto plano: cada seccion (Ingresos, Gastos, Deudas,
etc.) es una pantalla independiente a la que se entra y de la que se
vuelve, con tablas, formularios y botones -- todo dentro de la terminal,
asi que sigue funcionando igual en PC y en iSH (iPhone), sin necesitar
servidor grafico.

TODA la logica de dinero y calculos (Decimal, JSON, amortizacion de
deudas, etc.) se reutiliza tal cual desde finanzas.py -- este archivo
NO duplica ningun calculo, solo reemplaza la capa de pantallas.

Requisitos:
    pip install textual

Ejecutar:
    python3 finanzas_tui.py
    (en iSH / Linux puede ser necesario: python3.11 finanzas_tui.py, etc.)

Debe estar en la MISMA carpeta que finanzas.py y usa el mismo archivo
finanzas.json (los datos son 100% compatibles entre la version de
consola y esta).

Mapa de este archivo:
    1. Utilidades de formularios reusables (Campo, FormModal, ConfirmModal,
       MessageModal)
    2. Pantalla principal (Dashboard)
    3. Pantallas por seccion (Ingresos, Bloques, Gastos, Deudas, Ahorro,
       Distribucion, Mes actual)
    4. Analizar deudas (resumen, detalle de una deuda, estrategias,
       calculadora rapida)
    5. App principal y estilos (CSS)
"""

from __future__ import annotations

import copy as _copy
from dataclasses import dataclass, field as dc_field
from decimal import Decimal
from typing import Any, Dict, List, Optional

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, DataTable, Footer, Header, Input, Label, Select, Static

from finanzas import (
    CATEGORIA_BILL,
    CATEGORIA_VARIABLE,
    CERO,
    FRECUENCIAS_VALIDAS,
    LIMITE_MESES_SIMULACION,
    bills_total,
    cargar_datos,
    comprometido_bloque,
    dinero_sin_asignar,
    disponible_bloque,
    disponible_total,
    distribucion_total,
    fmt_money,
    fmt_pct,
    gastos_variables_total,
    guardar_datos,
    ingreso_total_bloque,
    ingreso_total_mes,
    ingresos_por_persona,
    num_bloques,
    pago_extra_total,
    pago_minimo_total,
    pago_minimo_total_bloque,
    redondear,
    saldo_deuda_total,
    simular_deuda,
    to_decimal,
)

# ---------------------------------------------------------------------------
# 0. SISTEMA DE MESES (capa agregada solo en esta TUI; finanzas.py NO se toca)
# ---------------------------------------------------------------------------
#
# Ingresos, Gastos, Bloques (num_bloques) y Distribucion viven DENTRO de cada
# mes (datos["meses"][nombre]). Deudas y Ahorro son GLOBALES (un saldo
# continuo, no una foto de un mes): se ven/editan igual sin importar el mes
# de trabajo seleccionado.
#
# finanzas.py sigue esperando datos["ingresos"] / ["gastos"] / ["distribucion"]
# a nivel raiz para sus calculos; para reusarlos sin duplicar logica ni tocar
# ese archivo, vista_calculo() arma un dict "adaptador" que apunta a las
# listas/dicts REALES del mes de trabajo (mutarlas muta el mes real) mas las
# deudas/ahorro globales.

def _migrar_a_meses(datos: Dict[str, Any]) -> Optional[str]:
    """Migra un finanzas.json plano (version anterior, sin 'meses') a la
    nueva estructura. Idempotente: si ya existe 'meses' no hace nada.
    Devuelve el nombre del mes creado si hubo migracion, o None si no."""
    if isinstance(datos.get("meses"), dict):
        datos.setdefault("mes_trabajo", None)
        return None

    nombre = (datos.get("config", {}) or {}).get("mes_actual", "").strip() or "General"
    datos["meses"] = {
        nombre: {
            "ingresos": datos.get("ingresos") or [],
            "gastos": datos.get("gastos") or [],
            "distribucion": datos.get("distribucion") or
                            {"ahorro": "0.00", "familia_otros": "0.00", "reserva": "0.00"},
            "num_bloques": int((datos.get("config", {}) or {}).get("num_bloques", 2) or 2),
        }
    }
    datos["mes_trabajo"] = nombre
    # "meses" queda como unica fuente de verdad de ingresos/gastos/distribucion;
    # las claves planas se quitan para no duplicar datos (deudas/ahorro siguen aparte).
    datos.pop("ingresos", None)
    datos.pop("gastos", None)
    datos.pop("distribucion", None)
    return nombre


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


def num_bloques_mes(mes: Optional[dict]) -> int:
    if not mes:
        return 2
    try:
        n = int(mes.get("num_bloques", 2))
        return n if n > 0 else 2
    except (TypeError, ValueError):
        return 2


def crear_mes(datos: Dict[str, Any], nombre: str, copiar_de: Optional[str] = None) -> None:
    """Crea un mes nuevo y lo deja como mes de trabajo. Si copiar_de es el
    nombre de un mes existente, copia (deep copy, independiente) sus
    ingresos, gastos, bloques y distribucion tal cual.

    Excepcion: los ingresos marcados como "adicionales" (no recurrentes,
    campo ingreso["recurrente"] == False -- ej. un bono o reembolso de un
    solo mes) NO se copian al mes nuevo, porque por definicion no vuelven
    a repetirse. Todo lo demas (ingresos normales, gastos, bloques,
    distribucion) se copia igual que antes."""
    if copiar_de and copiar_de in datos.get("meses", {}):
        nuevo = _copy.deepcopy(datos["meses"][copiar_de])
        nuevo["ingresos"] = [i for i in nuevo.get("ingresos", []) if i.get("recurrente", True)]
    else:
        nuevo = {
            "ingresos": [], "gastos": [],
            "distribucion": {"ahorro": "0.00", "familia_otros": "0.00", "reserva": "0.00"},
            "num_bloques": 2,
        }
    datos["meses"][nombre] = nuevo
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


def vista_calculo(datos: Dict[str, Any]) -> Dict[str, Any]:
    """Adaptador de solo-calculo: deja reusar bills_total/disponible_total/etc.
    de finanzas.py (que esperan datos['ingresos']/['gastos']/['distribucion']
    a nivel raiz) apuntando a las listas/dicts REALES del mes de trabajo mas
    las deudas/ahorro globales, sin modificar finanzas.py."""
    mes = obtener_mes_trabajo(datos) or {}
    return {
        "config": {"num_bloques": num_bloques_mes(mes)},
        "ingresos": mes.get("ingresos", []),
        "gastos": mes.get("gastos", []),
        "distribucion": mes.get("distribucion") or
                        {"ahorro": "0.00", "familia_otros": "0.00", "reserva": "0.00"},
        "deudas": datos["deudas"],
        "ahorro": datos["ahorro"],
    }


# ---------------------------------------------------------------------------
# 1. UTILIDADES DE FORMULARIOS REUSABLES
# ---------------------------------------------------------------------------


@dataclass
class Campo:
    """Describe un campo de un formulario generico (FormModal)."""
    id: str
    etiqueta: str
    tipo: str = "texto"  # texto | dinero | entero | seleccion
    valor: Any = ""
    opciones: List[str] = dc_field(default_factory=list)
    placeholder: str = ""


class FormModal(ModalScreen[Optional[Dict[str, str]]]):
    """
    Formulario modal generico: recibe una lista de Campo y, al confirmar,
    devuelve un dict {id: valor_texto}; si se cancela, devuelve None.

    Valida aqui mismo que los campos "dinero" y "entero" tengan numeros
    validos (equivalente a lo que hacian leer_decimal / leer_entero en la
    version de consola); la conversion final a Decimal/int y las reglas de
    negocio (ej. bloque dentro de rango) las aplica quien la use.
    """

    BINDINGS = [Binding("escape", "cancelar", "Cancelar")]

    DEFAULT_CSS = """
    FormModal { align: center middle; }
    FormModal > Vertical {
        width: 64; max-width: 94%; height: auto; max-height: 90%;
        border: round $accent; background: $surface; padding: 1 2;
    }
    FormModal Label.titulo { text-style: bold; color: $accent; margin-bottom: 1; }
    FormModal Label.etiqueta { margin-top: 1; }
    FormModal #campos { height: auto; max-height: 18; }
    FormModal #error { color: $error; margin-top: 1; }
    FormModal #botones { margin-top: 1; height: auto; align: right middle; }
    FormModal #botones Button { margin-left: 1; }
    """

    def __init__(self, titulo: str, campos: List[Campo], texto_guardar: str = "Guardar"):
        super().__init__()
        self.titulo = titulo
        self.campos = campos
        self.texto_guardar = texto_guardar

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(self.titulo, classes="titulo")
            with VerticalScroll(id="campos"):
                for campo in self.campos:
                    yield Label(campo.etiqueta, classes="etiqueta")
                    if campo.tipo == "seleccion":
                        yield Select(
                            [(op, op) for op in campo.opciones],
                            value=campo.valor if campo.valor in campo.opciones else campo.opciones[0],
                            allow_blank=False,
                            id=f"campo_{campo.id}",
                        )
                    else:
                        yield Input(value=str(campo.valor), placeholder=campo.placeholder, id=f"campo_{campo.id}")
            yield Static("", id="error")
            with Horizontal(id="botones"):
                yield Button("Cancelar", id="cancelar")
                yield Button(self.texto_guardar, id="guardar", variant="success")

    def action_cancelar(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancelar":
            self.dismiss(None)
            return

        valores: Dict[str, str] = {}
        errores: List[str] = []
        for campo in self.campos:
            widget = self.query_one(f"#campo_{campo.id}")
            valor = "" if widget.value is None else str(widget.value)
            if campo.tipo == "dinero" and to_decimal(valor) is None:
                errores.append(f"- {campo.etiqueta}: monto invalido")
            elif campo.tipo == "entero":
                try:
                    int(valor)
                except ValueError:
                    errores.append(f"- {campo.etiqueta}: debe ser un numero entero")
            valores[campo.id] = valor

        if errores:
            self.query_one("#error", Static).update("\n".join(errores))
            return
        self.dismiss(valores)


class ConfirmModal(ModalScreen[bool]):
    """Dialogo de confirmacion Si/No (reemplaza a leer_si_no)."""

    BINDINGS = [Binding("escape", "no", "No")]

    DEFAULT_CSS = """
    ConfirmModal { align: center middle; }
    ConfirmModal > Vertical {
        width: 60; max-width: 90%; height: auto;
        border: round $warning; background: $surface; padding: 1 2;
    }
    ConfirmModal #botones { margin-top: 1; height: auto; align: right middle; }
    ConfirmModal #botones Button { margin-left: 1; }
    """

    def __init__(self, mensaje: str):
        super().__init__()
        self.mensaje = mensaje

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(self.mensaje)
            with Horizontal(id="botones"):
                yield Button("No", id="no")
                yield Button("Si", id="si", variant="warning")

    def action_no(self) -> None:
        self.dismiss(False)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "si")


class MessageModal(ModalScreen[None]):
    """Ventana de solo lectura para mostrar resultados (analisis, estrategias)."""

    BINDINGS = [Binding("escape", "cerrar", "Cerrar")]

    DEFAULT_CSS = """
    MessageModal { align: center middle; }
    MessageModal > VerticalScroll {
        width: 76; max-width: 94%; height: auto; max-height: 88%;
        border: round $accent; background: $surface; padding: 1 2;
    }
    MessageModal Label.titulo { text-style: bold; color: $accent; margin-bottom: 1; }
    MessageModal Button { margin-top: 1; }
    """

    def __init__(self, titulo: str, cuerpo: str):
        super().__init__()
        self.titulo = titulo
        self.cuerpo = cuerpo

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Label(self.titulo, classes="titulo")
            yield Static(self.cuerpo, id="cuerpo")
            yield Button("Cerrar", id="cerrar", variant="primary")

    def action_cerrar(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(None)


def fila_texto(etiqueta: str, valor: str, ancho: int = 34) -> str:
    """Una linea 'etiqueta ......... valor' para pantallas de solo lectura."""
    return f"{etiqueta:<{ancho}}{valor:>18}"


# ---------------------------------------------------------------------------
# 2. PANTALLA PRINCIPAL (DASHBOARD)
# ---------------------------------------------------------------------------


class DashboardScreen(Screen):
    BINDINGS = [Binding("q", "salir", "Salir")]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with VerticalScroll(id="resumen"):
            yield Static(id="resumen-texto")
        with Vertical(id="menu-grid"):
            with Horizontal(classes="fila-menu"):
                yield Button("1. Ingresos", id="m_ingresos")
                yield Button("2. Bloques", id="m_bloques")
                yield Button("3. Gastos", id="m_gastos")
            with Horizontal(classes="fila-menu"):
                yield Button("4. Deudas", id="m_deudas")
                yield Button("5. Ahorro", id="m_ahorro")
                yield Button("6. Distribucion", id="m_distribucion")
            with Horizontal(classes="fila-menu"):
                yield Button("7. Analizar deudas", id="m_analizar")
                yield Button("8. Meses", id="m_mes")
                yield Button("9. Reporte", id="m_reporte")
        yield Footer()

    def on_mount(self) -> None:
        self._refrescar()

    def on_screen_resume(self) -> None:
        self._refrescar()

    def _refrescar(self) -> None:
        datos = self.app.datos  # type: ignore[attr-defined]
        mes_nombre = mes_trabajo_clave(datos)
        if mes_nombre is None:
            texto = (
                "[b]Mes de trabajo:[/b] (ninguno seleccionado)\n\n"
                "Ve a [8] Meses para crear o seleccionar un mes."
            )
        else:
            vista = vista_calculo(datos)
            disponible = disponible_total(vista)
            deuda = saldo_deuda_total(datos)
            ahorro_actual = to_decimal(datos["ahorro"].get("actual"), CERO)
            sin_asignar = dinero_sin_asignar(vista)
            color_disp = "red" if disponible < CERO else "green"
            color_sin_asignar = "red" if sin_asignar < CERO else "white"
            texto = (
                f"Mes de trabajo: [b]{mes_nombre}[/b]\n\n"
                f"Disponible del mes:  [{color_disp} b]{fmt_money(disponible)}[/{color_disp} b]\n"
                f"Deuda total:         [red]{fmt_money(deuda)}[/red]\n"
                f"Ahorro actual:       [green]{fmt_money(ahorro_actual)}[/green]\n"
                f"Dinero sin asignar:  [{color_sin_asignar}]{fmt_money(sin_asignar)}[/{color_sin_asignar}]"
            )
        self.query_one("#resumen-texto", Static).update(texto)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        destinos = {
            "m_ingresos": IngresosScreen,
            "m_bloques": BloquesScreen,
            "m_gastos": GastosScreen,
            "m_deudas": DeudasScreen,
            "m_ahorro": AhorroScreen,
            "m_distribucion": DistribucionScreen,
            "m_analizar": AnalizarDeudasScreen,
            "m_mes": MesesScreen,
            "m_reporte": ReporteScreen,
        }
        pantalla = destinos.get(event.button.id or "")
        if pantalla is not None:
            self.app.push_screen(pantalla())

    def action_salir(self) -> None:
        self.app.exit()


# ---------------------------------------------------------------------------
# 3. PANTALLAS POR SECCION
# ---------------------------------------------------------------------------


class SeccionScreen(Screen):
    """Base comun: Escape vuelve a la pantalla anterior."""
    BINDINGS = [Binding("escape", "volver", "Volver")]

    def action_volver(self) -> None:
        self.app.pop_screen()


class SeccionMesScreen(SeccionScreen):
    """
    Base para pantallas cuyo contenido vive DENTRO del mes de trabajo
    (Ingresos, Bloques, Gastos, Distribucion, Reporte). Si no hay ningun mes
    de trabajo seleccionado, avisa y vuelve a la pantalla anterior en vez de
    mostrar una pantalla vacia o con datos incorrectos.
    """

    def on_mount(self) -> None:
        if self._verificar_mes():
            self._al_montar()

    def on_screen_resume(self) -> None:
        if self._verificar_mes():
            self._refrescar()

    def _mes(self) -> dict:
        return obtener_mes_trabajo(self.app.datos)  # type: ignore[attr-defined]

    def _verificar_mes(self) -> bool:
        if obtener_mes_trabajo(self.app.datos) is not None:  # type: ignore[attr-defined]
            return True

        def cerrar(_: None) -> None:
            self.app.pop_screen()

        self.app.push_screen(
            MessageModal("SIN MES DE TRABAJO",
                         "No hay ningun mes de trabajo seleccionado.\n\n"
                         "Ve a [8] Meses para crear o seleccionar uno."),
            cerrar,
        )
        return False

    def _al_montar(self) -> None:
        self._refrescar()

    def _refrescar(self) -> None:
        raise NotImplementedError


# --- INGRESOS ----------------------------------------------------------------

# Opciones del campo "Tipo" de un ingreso, en el formulario. La primera
# ("RECURRENTE") corresponde a ingreso["recurrente"] = True; la segunda
# ("ADICIONAL...") corresponde a False. Ver crear_mes(): un ingreso
# ADICIONAL no se copia cuando se crea el siguiente mes.
OPCION_RECURRENTE = "RECURRENTE (se copia cada mes)"
OPCION_ADICIONAL = "ADICIONAL (no se copia al crear el siguiente mes)"
OPCIONES_TIPO_INGRESO = [OPCION_RECURRENTE, OPCION_ADICIONAL]


def _tipo_ingreso_a_texto(recurrente: bool) -> str:
    return OPCION_RECURRENTE if recurrente else OPCION_ADICIONAL


def _tipo_ingreso_a_bool(texto: str) -> bool:
    return texto != OPCION_ADICIONAL


class IngresosScreen(SeccionMesScreen):
    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("INGRESOS", classes="titulo-pantalla")
        yield DataTable(id="tabla", cursor_type="row", zebra_stripes=True)
        with Horizontal(classes="acciones"):
            yield Button("Agregar", id="agregar", variant="success")
            yield Button("Editar", id="editar")
            yield Button("Activar/Desactivar", id="toggle")
            yield Button("Eliminar", id="eliminar", variant="error")
            yield Button("Volver", id="volver")
        yield Footer()

    def _al_montar(self) -> None:
        tabla = self.query_one(DataTable)
        tabla.add_columns("Persona", "Descripcion", "Bloque", "Monto", "Frecuencia", "Tipo", "Estado")
        self._refrescar()

    def _refrescar(self) -> None:
        tabla = self.query_one(DataTable)
        tabla.clear()
        for idx, i in enumerate(self._mes()["ingresos"]):
            estado = "Activo" if i.get("activo", True) else "Inactivo"
            tipo = "Recurrente" if i.get("recurrente", True) else "Adicional"
            tabla.add_row(
                i.get("persona", ""),
                i.get("descripcion", "") or "-",
                str(i.get("bloque", 1)),
                fmt_money(to_decimal(i.get("monto"), CERO)),
                i.get("frecuencia", "MENSUAL"),
                tipo,
                estado,
                key=str(idx),
            )

    def _indice_seleccionado(self) -> Optional[int]:
        tabla = self.query_one(DataTable)
        if tabla.row_count == 0:
            return None
        row_key, _ = tabla.coordinate_to_cell_key(tabla.cursor_coordinate)
        return int(row_key.value) if row_key.value is not None else None

    def on_button_pressed(self, event: Button.Pressed) -> None:
        accion = event.button.id
        if accion == "volver":
            self.app.pop_screen()
        elif accion == "agregar":
            self._agregar()
        elif accion == "editar":
            self._editar()
        elif accion == "toggle":
            self._toggle()
        elif accion == "eliminar":
            self._eliminar()

    def _agregar(self) -> None:
        mes = self._mes()
        campos = [
            Campo("persona", "Persona (ej. Yo, Esposa)"),
            Campo("descripcion", "Descripcion (opcional)"),
            Campo("bloque", f"Bloque (1-{num_bloques_mes(mes)})", tipo="entero", valor=1),
            Campo("monto", "Monto ($)", tipo="dinero", valor="0.00"),
            Campo("frecuencia", "Frecuencia", tipo="seleccion", valor="MENSUAL",
                  opciones=list(FRECUENCIAS_VALIDAS)),
            Campo("recurrente", "Tipo de ingreso", tipo="seleccion", valor=OPCION_RECURRENTE,
                  opciones=OPCIONES_TIPO_INGRESO),
        ]

        def al_cerrar(valores: Optional[Dict[str, str]]) -> None:
            if not valores or not valores["persona"].strip():
                return
            mes["ingresos"].append({
                "persona": valores["persona"].strip(),
                "descripcion": valores["descripcion"].strip(),
                "bloque": int(valores["bloque"]),
                "monto": str(redondear(to_decimal(valores["monto"], CERO))),
                "frecuencia": valores["frecuencia"] if valores["frecuencia"] in FRECUENCIAS_VALIDAS else "OTRO",
                "activo": True,
                "recurrente": _tipo_ingreso_a_bool(valores["recurrente"]),
            })
            guardar_datos(self.app.datos)  # type: ignore[attr-defined]
            self._refrescar()

        self.app.push_screen(FormModal("NUEVO INGRESO", campos), al_cerrar)

    def _editar(self) -> None:
        idx = self._indice_seleccionado()
        if idx is None:
            return
        mes = self._mes()
        i = mes["ingresos"][idx]
        campos = [
            Campo("persona", "Persona", valor=i.get("persona", "")),
            Campo("descripcion", "Descripcion", valor=i.get("descripcion", "")),
            Campo("bloque", f"Bloque (1-{num_bloques_mes(mes)})", tipo="entero", valor=i.get("bloque", 1)),
            Campo("monto", "Monto ($)", tipo="dinero", valor=i.get("monto", "0.00")),
            Campo("frecuencia", "Frecuencia", tipo="seleccion", valor=i.get("frecuencia", "MENSUAL"),
                  opciones=list(FRECUENCIAS_VALIDAS)),
            Campo("recurrente", "Tipo de ingreso", tipo="seleccion",
                  valor=_tipo_ingreso_a_texto(i.get("recurrente", True)),
                  opciones=OPCIONES_TIPO_INGRESO),
        ]

        def al_cerrar(valores: Optional[Dict[str, str]]) -> None:
            if not valores:
                return
            if valores["persona"].strip():
                i["persona"] = valores["persona"].strip()
            i["descripcion"] = valores["descripcion"].strip()
            i["bloque"] = int(valores["bloque"])
            i["monto"] = str(redondear(to_decimal(valores["monto"], CERO)))
            i["frecuencia"] = valores["frecuencia"] if valores["frecuencia"] in FRECUENCIAS_VALIDAS else "OTRO"
            i["recurrente"] = _tipo_ingreso_a_bool(valores["recurrente"])
            guardar_datos(self.app.datos)  # type: ignore[attr-defined]
            self._refrescar()

        self.app.push_screen(FormModal(f"EDITANDO: {i.get('persona', '')}", campos), al_cerrar)

    def _toggle(self) -> None:
        idx = self._indice_seleccionado()
        if idx is None:
            return
        i = self._mes()["ingresos"][idx]
        i["activo"] = not i.get("activo", True)
        guardar_datos(self.app.datos)  # type: ignore[attr-defined]
        self._refrescar()

    def _eliminar(self) -> None:
        idx = self._indice_seleccionado()
        if idx is None:
            return
        mes = self._mes()

        def al_confirmar(si: bool) -> None:
            if si:
                mes["ingresos"].pop(idx)
                guardar_datos(self.app.datos)  # type: ignore[attr-defined]
                self._refrescar()

        self.app.push_screen(ConfirmModal("Seguro que deseas eliminar este ingreso permanentemente?"), al_confirmar)


# --- BLOQUES -------------------------------------------------------------


class BloquesScreen(SeccionMesScreen):
    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("BLOQUES DE PAGO", classes="titulo-pantalla")
        with VerticalScroll(id="bloques-caja"):
            yield Static(id="bloques-texto")
        with Horizontal(classes="acciones"):
            yield Button("Cambiar numero de bloques", id="cambiar")
            yield Button("Volver", id="volver")
        yield Footer()

    def _refrescar(self) -> None:
        mes = self._mes()
        vista = vista_calculo(self.app.datos)  # type: ignore[attr-defined]
        partes = []
        for b in range(1, num_bloques_mes(mes) + 1):
            partes.append(
                f"[b]BLOQUE {b}[/b]\n"
                f"{fila_texto('Ingresos', fmt_money(ingreso_total_bloque(vista, b)))}\n"
                f"{fila_texto('  Pagos minimos de deuda', fmt_money(pago_minimo_total_bloque(vista, b)))}\n"
                f"{fila_texto('Comprometido', fmt_money(comprometido_bloque(vista, b)))}\n"
                f"{fila_texto('Disponible', fmt_money(disponible_bloque(vista, b)))}\n"
            )
        self.query_one("#bloques-texto", Static).update("\n".join(partes))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "volver":
            self.app.pop_screen()
        elif event.button.id == "cambiar":
            self._cambiar()

    def _cambiar(self) -> None:
        mes = self._mes()
        campos = [Campo("num", f"Numero de bloques (actual: {num_bloques_mes(mes)})",
                         tipo="entero", valor=num_bloques_mes(mes))]

        def al_cerrar(valores: Optional[Dict[str, str]]) -> None:
            if not valores:
                return
            nuevo = max(1, min(12, int(valores["num"])))
            mes["num_bloques"] = nuevo
            guardar_datos(self.app.datos)  # type: ignore[attr-defined]
            self._refrescar()

        self.app.push_screen(FormModal("CAMBIAR NUMERO DE BLOQUES", campos), al_cerrar)


# --- GASTOS ----------------------------------------------------------------


class GastosScreen(SeccionMesScreen):
    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("GASTOS FIJOS / BILLS", classes="titulo-pantalla")
        yield DataTable(id="tabla_bills", cursor_type="row", zebra_stripes=True)
        with Horizontal(classes="acciones"):
            yield Button("Agregar bill", id="agregar_bill", variant="success")
            yield Button("Editar", id="editar_bill")
            yield Button("Activar/Desactivar", id="toggle_bill")
            yield Button("Eliminar", id="eliminar_bill", variant="error")
        yield Static("GASTOS FAMILIARES (presupuesto variable)", classes="titulo-pantalla")
        yield DataTable(id="tabla_var", cursor_type="row", zebra_stripes=True)
        with Horizontal(classes="acciones"):
            yield Button("Agregar variable", id="agregar_var", variant="success")
            yield Button("Editar", id="editar_var")
            yield Button("Activar/Desactivar", id="toggle_var")
            yield Button("Eliminar", id="eliminar_var", variant="error")
        with Horizontal(classes="acciones"):
            yield Button("Detalle de gastos", id="detalle_gastos", variant="primary")
            yield Button("Volver", id="volver")
        yield Footer()

    def _al_montar(self) -> None:
        self.query_one("#tabla_bills", DataTable).add_columns("Nombre", "Bloque", "Monto", "Vencimiento", "Estado")
        self.query_one("#tabla_var", DataTable).add_columns("Nombre", "Monto", "Estado")
        self._refrescar()

    def _refrescar(self) -> None:
        mes = self._mes()
        tabla_bills = self.query_one("#tabla_bills", DataTable)
        tabla_var = self.query_one("#tabla_var", DataTable)
        tabla_bills.clear()
        tabla_var.clear()
        for idx, g in enumerate(mes["gastos"]):
            estado = "Activo" if g.get("activo", True) else "Inactivo"
            if g.get("categoria") == CATEGORIA_BILL:
                venc = f"dia {g['vencimiento']}" if g.get("vencimiento") else "-"
                tabla_bills.add_row(
                    g.get("nombre", ""), str(g.get("bloque", 1)),
                    fmt_money(to_decimal(g.get("monto"), CERO)), venc, estado, key=str(idx),
                )
            elif g.get("categoria") == CATEGORIA_VARIABLE:
                tabla_var.add_row(
                    g.get("nombre", ""), fmt_money(to_decimal(g.get("monto"), CERO)), estado, key=str(idx),
                )

    def _indice(self, tabla_id: str) -> Optional[int]:
        tabla = self.query_one(f"#{tabla_id}", DataTable)
        if tabla.row_count == 0:
            return None
        row_key, _ = tabla.coordinate_to_cell_key(tabla.cursor_coordinate)
        return int(row_key.value) if row_key.value is not None else None

    def on_button_pressed(self, event: Button.Pressed) -> None:
        accion = event.button.id
        if accion == "volver":
            self.app.pop_screen()
        elif accion == "agregar_bill":
            self._agregar(CATEGORIA_BILL)
        elif accion == "agregar_var":
            self._agregar(CATEGORIA_VARIABLE)
        elif accion == "editar_bill":
            self._editar("tabla_bills")
        elif accion == "editar_var":
            self._editar("tabla_var")
        elif accion == "toggle_bill":
            self._toggle("tabla_bills")
        elif accion == "toggle_var":
            self._toggle("tabla_var")
        elif accion == "eliminar_bill":
            self._eliminar("tabla_bills")
        elif accion == "eliminar_var":
            self._eliminar("tabla_var")
        elif accion == "detalle_gastos":
            self.app.push_screen(DetalleGastosScreen())

    def _agregar(self, categoria: str) -> None:
        mes = self._mes()
        campos = [Campo("nombre", "Nombre"), Campo("monto", "Monto ($)", tipo="dinero", valor="0.00")]
        if categoria == CATEGORIA_BILL:
            campos.append(Campo("bloque", f"Bloque (1-{num_bloques_mes(mes)})", tipo="entero", valor=1))
            campos.append(Campo("vencimiento", "Dia de vencimiento (1-31, opcional)"))

        etiqueta = "NUEVO BILL FIJO" if categoria == CATEGORIA_BILL else "NUEVO GASTO VARIABLE"

        def al_cerrar(valores: Optional[Dict[str, str]]) -> None:
            if not valores or not valores["nombre"].strip():
                return
            bloque = int(valores["bloque"]) if categoria == CATEGORIA_BILL else None
            texto_venc = valores.get("vencimiento", "").strip() if categoria == CATEGORIA_BILL else ""
            vencimiento = int(texto_venc) if texto_venc.isdigit() else None
            mes["gastos"].append({
                "nombre": valores["nombre"].strip(),
                "categoria": categoria,
                "monto": str(redondear(to_decimal(valores["monto"], CERO))),
                "bloque": bloque,
                "vencimiento": vencimiento,
                "activo": True,
            })
            guardar_datos(self.app.datos)  # type: ignore[attr-defined]
            self._refrescar()

        self.app.push_screen(FormModal(etiqueta, campos), al_cerrar)

    def _editar(self, tabla_id: str) -> None:
        idx = self._indice(tabla_id)
        if idx is None:
            return
        mes = self._mes()
        g = mes["gastos"][idx]
        campos = [Campo("nombre", "Nombre", valor=g.get("nombre", "")),
                  Campo("monto", "Monto ($)", tipo="dinero", valor=g.get("monto", "0.00"))]
        if g.get("categoria") == CATEGORIA_BILL:
            campos.append(Campo("bloque", f"Bloque (1-{num_bloques_mes(mes)})", tipo="entero",
                                 valor=g.get("bloque", 1)))
            campos.append(Campo("vencimiento", "Dia de vencimiento (1-31, opcional)",
                                 valor=g.get("vencimiento") or ""))

        def al_cerrar(valores: Optional[Dict[str, str]]) -> None:
            if not valores:
                return
            if valores["nombre"].strip():
                g["nombre"] = valores["nombre"].strip()
            g["monto"] = str(redondear(to_decimal(valores["monto"], CERO)))
            if g.get("categoria") == CATEGORIA_BILL:
                g["bloque"] = int(valores["bloque"])
                texto_venc = valores.get("vencimiento", "").strip()
                g["vencimiento"] = int(texto_venc) if texto_venc.isdigit() else None
            guardar_datos(self.app.datos)  # type: ignore[attr-defined]
            self._refrescar()

        self.app.push_screen(FormModal(f"EDITANDO: {g.get('nombre', '')}", campos), al_cerrar)

    def _toggle(self, tabla_id: str) -> None:
        idx = self._indice(tabla_id)
        if idx is None:
            return
        g = self._mes()["gastos"][idx]
        g["activo"] = not g.get("activo", True)
        guardar_datos(self.app.datos)  # type: ignore[attr-defined]
        self._refrescar()

    def _eliminar(self, tabla_id: str) -> None:
        idx = self._indice(tabla_id)
        if idx is None:
            return
        mes = self._mes()

        def al_confirmar(si: bool) -> None:
            if si:
                mes["gastos"].pop(idx)
                guardar_datos(self.app.datos)  # type: ignore[attr-defined]
                self._refrescar()

        self.app.push_screen(ConfirmModal("Seguro que deseas eliminar este gasto permanentemente?"), al_confirmar)


# --- DETALLE / REPORTE DE GASTOS ----------------------------------------------


class DetalleGastosScreen(SeccionMesScreen):
    """
    Detalle de gastos del mes de trabajo, facil de revisar de un vistazo,
    con dos vistas intercambiables (el mismo boton alterna entre ellas):

      - "Por bloques": los bills agrupados por su bloque de pago (con
        subtotal de cada bloque) y despues los gastos variables, tal como
        se organiza el resto de la app.
      - "Lista completa": todos los gastos (bills + variables), uno debajo
        del otro, sin agrupar -- util para ver todo de corrido.

    Es de solo lectura: no agrega, edita ni elimina nada.
    """

    def __init__(self) -> None:
        super().__init__()
        self.modo: str = "bloques"  # "bloques" | "lista"

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("DETALLE DE GASTOS", classes="titulo-pantalla")
        with VerticalScroll(id="reporte-caja"):
            yield Static(id="detalle-texto")
        with Horizontal(classes="acciones"):
            yield Button("Ver lista completa", id="cambiar_vista")
            yield Button("Volver", id="volver")
        yield Footer()

    def _refrescar(self) -> None:
        boton = self.query_one("#cambiar_vista", Button)
        boton.label = "Ver lista completa" if self.modo == "bloques" else "Ver por bloques"
        if self.modo == "bloques":
            texto = self._texto_por_bloques()
        else:
            texto = self._texto_lista()
        self.query_one("#detalle-texto", Static).update(texto)

    def _texto_por_bloques(self) -> str:
        mes = self._mes()
        gastos = mes["gastos"]
        secciones: List[str] = ["[b]VISTA: POR BLOQUES[/b]"]
        total_general = CERO

        for b in range(1, num_bloques_mes(mes) + 1):
            lineas = [f"[b]BLOQUE {b}[/b]"]
            bills_bloque = [g for g in gastos if g.get("categoria") == CATEGORIA_BILL
                             and int(g.get("bloque") or 0) == b]
            if not bills_bloque:
                lineas.append("  (sin bills en este bloque)")
            subtotal = CERO
            for g in bills_bloque:
                estado = "" if g.get("activo", True) else "  [INACTIVO]"
                venc = f", vence dia {g['vencimiento']}" if g.get("vencimiento") else ""
                monto = to_decimal(g.get("monto"), CERO)
                lineas.append(fila_texto(f"{g.get('nombre', '')}{venc}{estado}", fmt_money(monto)))
                if g.get("activo", True):
                    subtotal += monto
            lineas.append(fila_texto(f"Subtotal bloque {b}", fmt_money(subtotal)))
            total_general += subtotal
            secciones.append("\n".join(lineas))

        lineas_var = ["[b]GASTOS FAMILIARES (variables, no van por bloque)[/b]"]
        variables = [g for g in gastos if g.get("categoria") == CATEGORIA_VARIABLE]
        if not variables:
            lineas_var.append("  (sin gastos variables registrados)")
        subtotal_var = CERO
        for g in variables:
            estado = "" if g.get("activo", True) else "  [INACTIVO]"
            monto = to_decimal(g.get("monto"), CERO)
            lineas_var.append(fila_texto(f"{g.get('nombre', '')}{estado}", fmt_money(monto)))
            if g.get("activo", True):
                subtotal_var += monto
        lineas_var.append(fila_texto("Subtotal gastos variables", fmt_money(subtotal_var)))
        total_general += subtotal_var
        secciones.append("\n".join(lineas_var))

        secciones.append(f"[b]{fila_texto('TOTAL GENERAL DE GASTOS (activos)', fmt_money(total_general))}[/b]")
        return "\n\n".join(secciones)

    def _texto_lista(self) -> str:
        mes = self._mes()
        gastos = mes["gastos"]
        lineas = ["[b]VISTA: LISTA COMPLETA[/b]"]
        if not gastos:
            lineas.append("  (sin gastos registrados)")
        total = CERO
        for g in gastos:
            estado = "Inactivo" if not g.get("activo", True) else "Activo"
            cat = "Bill" if g.get("categoria") == CATEGORIA_BILL else "Variable"
            detalle_extra = ""
            if g.get("categoria") == CATEGORIA_BILL:
                bloque = g.get("bloque")
                venc = g.get("vencimiento")
                detalle_extra = f", Bloque {bloque}" if bloque else ""
                detalle_extra += f", vence dia {venc}" if venc else ""
            monto = to_decimal(g.get("monto"), CERO)
            lineas.append(f"[b]{g.get('nombre', '')}[/b]  [{cat}{detalle_extra}] ({estado})")
            lineas.append(fila_texto("  Monto", fmt_money(monto)))
            if g.get("activo", True):
                total += monto
        lineas.append(f"[b]{fila_texto('TOTAL (activos)', fmt_money(total))}[/b]")
        return "\n".join(lineas)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "volver":
            self.app.pop_screen()
        elif event.button.id == "cambiar_vista":
            self.modo = "lista" if self.modo == "bloques" else "bloques"
            self._refrescar()


# --- DEUDAS ------------------------------------------------------------------


class DeudasScreen(SeccionScreen):
    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("DEUDAS", classes="titulo-pantalla")
        yield DataTable(id="tabla", cursor_type="row", zebra_stripes=True)
        with Horizontal(classes="acciones"):
            yield Button("Agregar", id="agregar", variant="success")
            yield Button("Editar", id="editar")
            yield Button("Activar/Desactivar", id="toggle")
            yield Button("Eliminar", id="eliminar", variant="error")
            yield Button("Volver", id="volver")
        yield Footer()

    def on_mount(self) -> None:
        tabla = self.query_one(DataTable)
        tabla.add_columns("Nombre", "Saldo", "Interes mensual", "Pago minimo", "Pago extra", "Estado")
        self._refrescar()

    def on_screen_resume(self) -> None:
        self._refrescar()

    def _refrescar(self) -> None:
        tabla = self.query_one(DataTable)
        tabla.clear()
        for idx, d in enumerate(self.app.datos["deudas"]):  # type: ignore[attr-defined]
            estado = "Activa" if d.get("activo", True) else "Inactiva"
            tabla.add_row(
                d.get("nombre", ""),
                fmt_money(to_decimal(d.get("saldo"), CERO)),
                fmt_pct(to_decimal(d.get("interes_mensual"), CERO)),
                fmt_money(to_decimal(d.get("pago_minimo"), CERO)),
                fmt_money(to_decimal(d.get("pago_extra"), CERO)),
                estado,
                key=str(idx),
            )

    def _indice_seleccionado(self) -> Optional[int]:
        tabla = self.query_one(DataTable)
        if tabla.row_count == 0:
            return None
        row_key, _ = tabla.coordinate_to_cell_key(tabla.cursor_coordinate)
        return int(row_key.value) if row_key.value is not None else None

    def on_button_pressed(self, event: Button.Pressed) -> None:
        accion = event.button.id
        if accion == "volver":
            self.app.pop_screen()
        elif accion == "agregar":
            self._agregar()
        elif accion == "editar":
            self._editar()
        elif accion == "toggle":
            self._toggle()
        elif accion == "eliminar":
            self._eliminar()

    def _agregar(self) -> None:
        datos = self.app.datos  # type: ignore[attr-defined]
        campos = [
            Campo("nombre", "Nombre (ej. Visa)"),
            Campo("saldo", "Saldo actual ($)", tipo="dinero", valor="0.00"),
            Campo("interes", "Tasa de interes mensual (ej. 2.5 para 2.5%)", tipo="dinero", valor="0.00"),
            Campo("pago_minimo", "Pago minimo ($)", tipo="dinero", valor="0.00"),
            Campo("pago_extra", "Pago extra ($)", tipo="dinero", valor="0.00"),
            Campo("bloque", f"Bloque (1-{num_bloques_mes(obtener_mes_trabajo(datos))})", tipo="entero", valor=1),
            Campo("vencimiento", "Dia de vencimiento (1-31, opcional)"),
        ]

        def al_cerrar(valores: Optional[Dict[str, str]]) -> None:
            if not valores or not valores["nombre"].strip():
                return
            texto_venc = valores.get("vencimiento", "").strip()
            datos["deudas"].append({
                "nombre": valores["nombre"].strip(),
                "saldo": str(redondear(to_decimal(valores["saldo"], CERO))),
                "interes_mensual": str(redondear(to_decimal(valores["interes"], CERO))),
                "pago_minimo": str(redondear(to_decimal(valores["pago_minimo"], CERO))),
                "pago_extra": str(redondear(to_decimal(valores["pago_extra"], CERO))),
                "bloque": int(valores["bloque"]),
                "vencimiento": int(texto_venc) if texto_venc.isdigit() else None,
                "activo": True,
            })
            guardar_datos(datos)
            self._refrescar()

        self.app.push_screen(FormModal("NUEVA DEUDA", campos), al_cerrar)

    def _editar(self) -> None:
        idx = self._indice_seleccionado()
        if idx is None:
            return
        datos = self.app.datos  # type: ignore[attr-defined]
        d = datos["deudas"][idx]
        campos = [
            Campo("nombre", "Nombre", valor=d.get("nombre", "")),
            Campo("saldo", "Saldo actual ($)", tipo="dinero", valor=d.get("saldo", "0.00")),
            Campo("interes", "Tasa de interes mensual (%)", tipo="dinero", valor=d.get("interes_mensual", "0.00")),
            Campo("pago_minimo", "Pago minimo ($)", tipo="dinero", valor=d.get("pago_minimo", "0.00")),
            Campo("pago_extra", "Pago extra ($)", tipo="dinero", valor=d.get("pago_extra", "0.00")),
            Campo("bloque", f"Bloque (1-{num_bloques_mes(obtener_mes_trabajo(datos))})", tipo="entero",
                  valor=d.get("bloque", 1)),
            Campo("vencimiento", "Dia de vencimiento (1-31, opcional)", valor=d.get("vencimiento") or ""),
        ]

        def al_cerrar(valores: Optional[Dict[str, str]]) -> None:
            if not valores:
                return
            if valores["nombre"].strip():
                d["nombre"] = valores["nombre"].strip()
            d["saldo"] = str(redondear(to_decimal(valores["saldo"], CERO)))
            d["interes_mensual"] = str(redondear(to_decimal(valores["interes"], CERO)))
            d["pago_minimo"] = str(redondear(to_decimal(valores["pago_minimo"], CERO)))
            d["pago_extra"] = str(redondear(to_decimal(valores["pago_extra"], CERO)))
            d["bloque"] = int(valores["bloque"])
            texto_venc = valores.get("vencimiento", "").strip()
            d["vencimiento"] = int(texto_venc) if texto_venc.isdigit() else None
            guardar_datos(datos)
            self._refrescar()

        self.app.push_screen(FormModal(f"EDITANDO: {d.get('nombre', '')}", campos), al_cerrar)

    def _toggle(self) -> None:
        idx = self._indice_seleccionado()
        if idx is None:
            return
        datos = self.app.datos  # type: ignore[attr-defined]
        d = datos["deudas"][idx]
        d["activo"] = not d.get("activo", True)
        guardar_datos(datos)
        self._refrescar()

    def _eliminar(self) -> None:
        idx = self._indice_seleccionado()
        if idx is None:
            return
        datos = self.app.datos  # type: ignore[attr-defined]

        def al_confirmar(si: bool) -> None:
            if si:
                datos["deudas"].pop(idx)
                guardar_datos(datos)
                self._refrescar()

        self.app.push_screen(ConfirmModal("Seguro que deseas eliminar esta deuda permanentemente?"), al_confirmar)


# --- AHORRO --------------------------------------------------------------


class AhorroScreen(SeccionScreen):
    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("AHORRO", classes="titulo-pantalla")
        with VerticalScroll(id="ahorro-caja"):
            yield Static(id="ahorro-texto")
        with Horizontal(classes="acciones"):
            yield Button("Editar", id="editar")
            yield Button("Volver", id="volver")
        yield Footer()

    def on_mount(self) -> None:
        self._refrescar()

    def on_screen_resume(self) -> None:
        self._refrescar()

    def _refrescar(self) -> None:
        ahorro = self.app.datos["ahorro"]  # type: ignore[attr-defined]
        actual = to_decimal(ahorro.get("actual"), CERO)
        meta = to_decimal(ahorro.get("meta"), CERO)
        faltante = max(CERO, meta - actual)
        texto = (
            f"{fila_texto('Actual', fmt_money(actual))}\n"
            f"{fila_texto('Meta', fmt_money(meta))}\n"
            f"{fila_texto('Faltante', fmt_money(faltante))}"
        )
        self.query_one("#ahorro-texto", Static).update(texto)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "volver":
            self.app.pop_screen()
        elif event.button.id == "editar":
            self._editar()

    def _editar(self) -> None:
        datos = self.app.datos  # type: ignore[attr-defined]
        ahorro = datos["ahorro"]
        campos = [
            Campo("actual", "Ahorro actual ($)", tipo="dinero", valor=ahorro.get("actual", "0.00")),
            Campo("meta", "Meta de ahorro ($)", tipo="dinero", valor=ahorro.get("meta", "0.00")),
        ]

        def al_cerrar(valores: Optional[Dict[str, str]]) -> None:
            if not valores:
                return
            ahorro["actual"] = str(redondear(to_decimal(valores["actual"], CERO)))
            ahorro["meta"] = str(redondear(to_decimal(valores["meta"], CERO)))
            guardar_datos(datos)
            self._refrescar()

        self.app.push_screen(FormModal("EDITAR AHORRO", campos), al_cerrar)


# --- DISTRIBUCION ----------------------------------------------------------


class DistribucionScreen(SeccionMesScreen):
    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("DISTRIBUCION DEL DISPONIBLE", classes="titulo-pantalla")
        with VerticalScroll(id="dist-caja"):
            yield Static(id="dist-texto")
        with Horizontal(classes="acciones"):
            yield Button("Editar ahorro asignado", id="editar_ahorro")
            yield Button("Editar familia/otros", id="editar_familia")
            yield Button("Editar reserva", id="editar_reserva")
        with Horizontal(classes="acciones"):
            yield Button("Volver", id="volver")
        yield Footer()

    def _refrescar(self) -> None:
        datos = self.app.datos  # type: ignore[attr-defined]
        dist = self._mes()["distribucion"]
        vista = vista_calculo(datos)
        disponible = disponible_total(vista)
        total_asignado = distribucion_total(vista)
        sin_asignar = disponible - total_asignado
        color = "red" if sin_asignar < CERO else "white"
        texto = (
            f"{fila_texto('Disponible del mes', fmt_money(disponible))}\n\n"
            f"{fila_texto('Ahorro asignado', fmt_money(to_decimal(dist.get('ahorro'), CERO)))}\n"
            f"{fila_texto('Pago extra a deudas', fmt_money(pago_extra_total(datos)))}\n"
            f"{fila_texto('Familia / otros', fmt_money(to_decimal(dist.get('familia_otros'), CERO)))}\n"
            f"{fila_texto('Reserva', fmt_money(to_decimal(dist.get('reserva'), CERO)))}\n\n"
            f"{fila_texto('TOTAL ASIGNADO', fmt_money(total_asignado))}\n"
            f"[{color}]{fila_texto('SIN ASIGNAR', fmt_money(sin_asignar))}[/{color}]\n\n"
            "(el pago extra a deudas se administra en la pantalla Deudas, no aqui)"
        )
        self.query_one("#dist-texto", Static).update(texto)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "volver":
            self.app.pop_screen()
        elif event.button.id == "editar_ahorro":
            self._editar_campo("ahorro", "Ahorro asignado")
        elif event.button.id == "editar_familia":
            self._editar_campo("familia_otros", "Familia / otros")
        elif event.button.id == "editar_reserva":
            self._editar_campo("reserva", "Reserva")

    def _editar_campo(self, campo_id: str, etiqueta: str) -> None:
        datos = self.app.datos  # type: ignore[attr-defined]
        dist = self._mes()["distribucion"]
        actual = to_decimal(dist.get(campo_id), CERO)
        campos = [Campo("valor", f"{etiqueta} ($)", tipo="dinero", valor=str(actual))]

        def al_cerrar(valores: Optional[Dict[str, str]]) -> None:
            if not valores:
                return
            nuevo = redondear(to_decimal(valores["valor"], CERO))
            vista = vista_calculo(datos)
            disponible = disponible_total(vista)
            otros = distribucion_total(vista) - actual

            def guardar_valor() -> None:
                dist[campo_id] = str(nuevo)
                guardar_datos(datos)
                self._refrescar()

            if otros + nuevo > disponible:
                mensaje = (
                    f"Esto dejaria el total asignado en {fmt_money(otros + nuevo)}, "
                    f"que supera el disponible ({fmt_money(disponible)}).\n\n"
                    "Deseas guardarlo de todas formas?"
                )

                def al_confirmar(si: bool) -> None:
                    if si:
                        guardar_valor()

                self.app.push_screen(ConfirmModal(mensaje), al_confirmar)
            else:
                guardar_valor()

        self.app.push_screen(FormModal(f"EDITAR: {etiqueta}", campos), al_cerrar)


# --- MESES ---------------------------------------------------------------


class MesesScreen(SeccionScreen):
    """
    Crear/seleccionar/renombrar/eliminar meses. Ingresos, Gastos, Bloques y
    Distribucion viven dentro de cada mes; Deudas y Ahorro son globales y no
    se ven afectados por nada de esta pantalla.
    """

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("MESES", classes="titulo-pantalla")
        yield DataTable(id="tabla", cursor_type="row", zebra_stripes=True)
        with Horizontal(classes="acciones"):
            yield Button("Seleccionar", id="seleccionar", variant="success")
            yield Button("Crear nuevo", id="crear")
        with Horizontal(classes="acciones"):
            yield Button("Renombrar", id="renombrar")
            yield Button("Eliminar", id="eliminar", variant="error")
            yield Button("Volver", id="volver")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one(DataTable).add_columns("Mes", "Estado")
        self._refrescar()

    def on_screen_resume(self) -> None:
        self._refrescar()

    def _refrescar(self) -> None:
        datos = self.app.datos  # type: ignore[attr-defined]
        tabla = self.query_one(DataTable)
        tabla.clear()
        trabajo = mes_trabajo_clave(datos)
        for nombre in meses_ordenados(datos):
            estado = "Mes de trabajo" if nombre == trabajo else "-"
            tabla.add_row(nombre, estado, key=nombre)

    def _seleccionado(self) -> Optional[str]:
        tabla = self.query_one(DataTable)
        if tabla.row_count == 0:
            return None
        row_key, _ = tabla.coordinate_to_cell_key(tabla.cursor_coordinate)
        return row_key.value

    def on_button_pressed(self, event: Button.Pressed) -> None:
        accion = event.button.id
        if accion == "volver":
            self.app.pop_screen()
        elif accion == "seleccionar":
            self._seleccionar()
        elif accion == "crear":
            self._crear()
        elif accion == "renombrar":
            self._renombrar()
        elif accion == "eliminar":
            self._eliminar()

    def _seleccionar(self) -> None:
        nombre = self._seleccionado()
        if nombre is None:
            return
        datos = self.app.datos  # type: ignore[attr-defined]
        datos["mes_trabajo"] = nombre
        guardar_datos(datos)
        self._refrescar()

    def _crear(self) -> None:
        datos = self.app.datos  # type: ignore[attr-defined]
        opciones = ["(vacio)"] + meses_ordenados(datos)
        valor_default = mes_trabajo_clave(datos) or "(vacio)"
        campos = [
            Campo("nombre", "Nombre del mes nuevo (ej. 2026-10)"),
            Campo("copiar_de", "Copiar Ingresos/Gastos/Bloques/Distribucion de:",
                  tipo="seleccion", valor=valor_default, opciones=opciones),
        ]

        def al_cerrar(valores: Optional[Dict[str, str]]) -> None:
            if not valores or not valores["nombre"].strip():
                return
            nombre = valores["nombre"].strip()
            if nombre in datos.get("meses", {}):
                self.app.push_screen(MessageModal("AVISO", f"Ya existe un mes llamado '{nombre}'."))
                return
            copiar_de = valores["copiar_de"]
            copiar_de = copiar_de if copiar_de != "(vacio)" else None
            crear_mes(datos, nombre, copiar_de)
            guardar_datos(datos)
            self._refrescar()

        self.app.push_screen(FormModal("CREAR MES NUEVO", campos), al_cerrar)

    def _renombrar(self) -> None:
        viejo = self._seleccionado()
        if viejo is None:
            return
        datos = self.app.datos  # type: ignore[attr-defined]
        campos = [Campo("nombre", "Nuevo nombre", valor=viejo)]

        def al_cerrar(valores: Optional[Dict[str, str]]) -> None:
            if not valores or not valores["nombre"].strip():
                return
            nuevo = valores["nombre"].strip()
            if nuevo != viejo and nuevo in datos.get("meses", {}):
                self.app.push_screen(MessageModal("AVISO", f"Ya existe un mes llamado '{nuevo}'."))
                return
            renombrar_mes(datos, viejo, nuevo)
            guardar_datos(datos)
            self._refrescar()

        self.app.push_screen(FormModal(f"RENOMBRAR: {viejo}", campos), al_cerrar)

    def _eliminar(self) -> None:
        nombre = self._seleccionado()
        if nombre is None:
            return
        datos = self.app.datos  # type: ignore[attr-defined]

        def al_confirmar(si: bool) -> None:
            if si:
                eliminar_mes(datos, nombre)
                guardar_datos(datos)
                self._refrescar()

        self.app.push_screen(
            ConfirmModal(f"Seguro que deseas eliminar el mes '{nombre}' permanentemente?\n"
                         "(esto borra sus ingresos, gastos, bloques y distribucion; "
                         "las deudas y el ahorro NO se ven afectados)"),
            al_confirmar,
        )


# --- REPORTE -----------------------------------------------------------------


class ReporteScreen(SeccionMesScreen):
    """
    Reporte completo: el mismo contenido que el resumen de la version de
    consola (mostrar_resumen), pero en una pantalla dedicada y con scroll,
    para verlo todo junto sin tener que entrar seccion por seccion.
    """

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("REPORTE COMPLETO", classes="titulo-pantalla")
        with VerticalScroll(id="reporte-caja"):
            yield Static(id="reporte-texto")
        with Horizontal(classes="acciones"):
            yield Button("Volver", id="volver")
        yield Footer()

    def _refrescar(self) -> None:
        datos = self.app.datos  # type: ignore[attr-defined]
        mes_nombre = mes_trabajo_clave(datos) or "(sin definir)"
        mes = self._mes()
        vista = vista_calculo(datos)
        secciones: List[str] = [f"Mes de trabajo: [b]{mes_nombre}[/b]"]

        por_persona = ingresos_por_persona(vista)
        lineas = ["[b]INGRESOS[/b]"]
        if not por_persona:
            lineas.append("  (sin ingresos registrados)")
        for persona, monto in por_persona.items():
            lineas.append(fila_texto(persona, fmt_money(monto)))
        lineas.append(fila_texto("TOTAL", fmt_money(ingreso_total_mes(vista))))
        secciones.append("\n".join(lineas))

        for b in range(1, num_bloques_mes(mes) + 1):
            lineas = [f"[b]BLOQUE {b}[/b]"]
            lineas.append(fila_texto("Ingresos", fmt_money(ingreso_total_bloque(vista, b))))
            lineas.append(fila_texto("Comprometido (bills + minimos)", fmt_money(comprometido_bloque(vista, b))))
            disp_bloque = disponible_bloque(vista, b)
            color_b = "red" if disp_bloque < CERO else "green"
            lineas.append(f"[{color_b}]{fila_texto('Disponible', fmt_money(disp_bloque))}[/{color_b}]")
            secciones.append("\n".join(lineas))

        secciones.append(
            "[b]GASTOS FAMILIARES (presupuesto variable)[/b]\n"
            + fila_texto("Total presupuestado", fmt_money(gastos_variables_total(vista)))
        )

        disponible = disponible_total(vista)
        color_disp = "red" if disponible < CERO else "green"
        secciones.append(
            "[b]TOTAL DISPONIBLE DEL MES[/b]\n"
            + f"[{color_disp} b]{fila_texto('Disponible', fmt_money(disponible))}[/{color_disp} b]"
        )

        secciones.append(
            "[b]DEUDAS[/b]\n"
            + fila_texto("Saldo total", fmt_money(saldo_deuda_total(datos))) + "\n"
            + fila_texto("Pagos minimos", fmt_money(pago_minimo_total(datos))) + "\n"
            + fila_texto("Pagos extra", fmt_money(pago_extra_total(datos)))
        )

        ahorro = datos["ahorro"]
        actual = to_decimal(ahorro.get("actual"), CERO)
        meta = to_decimal(ahorro.get("meta"), CERO)
        faltante = max(CERO, meta - actual)
        secciones.append(
            "[b]AHORRO[/b]\n"
            + fila_texto("Actual", fmt_money(actual)) + "\n"
            + fila_texto("Meta", fmt_money(meta)) + "\n"
            + fila_texto("Faltante", fmt_money(faltante))
        )

        dist = mes["distribucion"]
        secciones.append(
            "[b]DISTRIBUCION DEL DISPONIBLE[/b]\n"
            + fila_texto("Ahorro asignado", fmt_money(to_decimal(dist.get("ahorro"), CERO))) + "\n"
            + fila_texto("Pago extra a deudas", fmt_money(pago_extra_total(datos))) + "\n"
            + fila_texto("Familia / otros", fmt_money(to_decimal(dist.get("familia_otros"), CERO))) + "\n"
            + fila_texto("Reserva", fmt_money(to_decimal(dist.get("reserva"), CERO))) + "\n"
            + fila_texto("TOTAL ASIGNADO", fmt_money(distribucion_total(vista)))
        )

        sin_asignar = dinero_sin_asignar(vista)
        color_sa = "red" if sin_asignar < CERO else "white"
        bloque_sa = f"[b]DINERO SIN ASIGNAR[/b]\n[{color_sa}]{fila_texto('', fmt_money(sin_asignar))}[/{color_sa}]"
        if sin_asignar < CERO:
            bloque_sa += "\n\n[red]ERROR: La cantidad asignada supera el dinero disponible.[/red]"
        secciones.append(bloque_sa)

        self.query_one("#reporte-texto", Static).update("\n\n".join(secciones))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "volver":
            self.app.pop_screen()


# ---------------------------------------------------------------------------
# 4. ANALIZAR DEUDAS
# ---------------------------------------------------------------------------


def _texto_analisis(etiqueta: str, saldo: Decimal, interes_mensual: Decimal, pago: Decimal,
                     resultado: Dict[str, Any]) -> str:
    partes = [
        f"[b]{etiqueta}[/b]",
        fila_texto("Saldo", fmt_money(saldo)),
        fila_texto("Interes mensual", fmt_pct(interes_mensual)),
        fila_texto("Pago mensual", fmt_money(pago)),
    ]
    if resultado["se_liquido"]:
        partes.append(fila_texto("Meses estimados", str(resultado["meses"])))
        partes.append(fila_texto("Interes estimado", fmt_money(resultado["interes_total"])))
        partes.append(fila_texto("Total pagado", fmt_money(resultado["total_pagado"])))
    elif resultado["advertencia_no_amortiza"]:
        partes.append("\n[red]ADVERTENCIA:[/red] con este pago la deuda no disminuye de forma suficiente")
        partes.append("(el pago mensual es igual o inferior al interes calculado).")
    elif resultado["advertencia_limite"]:
        partes.append(fila_texto("Meses simulados", f">= {LIMITE_MESES_SIMULACION}"))
        partes.append(fila_texto("Interes acumulado (parcial)", fmt_money(resultado["interes_total"])))
        partes.append(f"\n[red]ADVERTENCIA:[/red] no se liquida dentro del limite de simulacion "
                       f"({LIMITE_MESES_SIMULACION} meses).")
    return "\n".join(partes)


class AnalizarDeudasScreen(SeccionScreen):
    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("ANALIZAR DEUDAS", classes="titulo-pantalla")
        yield DataTable(id="tabla", cursor_type="row", zebra_stripes=True)
        with Horizontal(classes="acciones"):
            yield Button("Analizar seleccionada", id="analizar", variant="success")
            yield Button("Estrategias", id="estrategias")
            yield Button("Calculadora rapida", id="calculadora")
            yield Button("Volver", id="volver")
        yield Footer()

    def on_mount(self) -> None:
        tabla = self.query_one(DataTable)
        tabla.add_columns("Nombre", "Saldo", "Interes mensual", "Estado")
        self._refrescar()

    def on_screen_resume(self) -> None:
        self._refrescar()

    def _refrescar(self) -> None:
        datos = self.app.datos  # type: ignore[attr-defined]
        tabla = self.query_one(DataTable)
        tabla.clear()
        for idx, d in enumerate(datos["deudas"]):
            estado = "Activa" if d.get("activo", True) else "Inactiva"
            tabla.add_row(
                d.get("nombre", ""),
                fmt_money(to_decimal(d.get("saldo"), CERO)),
                fmt_pct(to_decimal(d.get("interes_mensual"), CERO)),
                estado,
                key=str(idx),
            )

    def _indice_seleccionado(self) -> Optional[int]:
        tabla = self.query_one(DataTable)
        if tabla.row_count == 0:
            return None
        row_key, _ = tabla.coordinate_to_cell_key(tabla.cursor_coordinate)
        return int(row_key.value) if row_key.value is not None else None

    def on_button_pressed(self, event: Button.Pressed) -> None:
        accion = event.button.id
        if accion == "volver":
            self.app.pop_screen()
        elif accion == "analizar":
            idx = self._indice_seleccionado()
            if idx is not None:
                self.app.push_screen(DeudaDetalleScreen(idx))
        elif accion == "estrategias":
            self._mostrar_estrategias()
        elif accion == "calculadora":
            self.app.push_screen(CalculadoraRapidaModal())

    def _mostrar_estrategias(self) -> None:
        datos = self.app.datos  # type: ignore[attr-defined]
        items = [d for d in datos["deudas"] if d.get("activo", True)]
        if not items:
            self.app.push_screen(MessageModal("ESTRATEGIAS DE DEUDA", "No hay deudas activas para analizar."))
            return

        partes = ["Orden sugerido para aplicar el pago extra disponible. No modifica los datos.\n",
                  "[b]AVALANCHA (mayor tasa de interes primero)[/b]"]
        for d in sorted(items, key=lambda x: to_decimal(x["interes_mensual"], CERO), reverse=True):
            partes.append(f"  {d['nombre']}: {fmt_pct(to_decimal(d['interes_mensual'], CERO))} - "
                          f"{fmt_money(to_decimal(d['saldo'], CERO))}")
        partes.append("\n[b]BOLA DE NIEVE (menor saldo primero)[/b]")
        for d in sorted(items, key=lambda x: to_decimal(x["saldo"], CERO)):
            partes.append(f"  {d['nombre']}: {fmt_money(to_decimal(d['saldo'], CERO))} - "
                          f"{fmt_pct(to_decimal(d['interes_mensual'], CERO))}")

        self.app.push_screen(MessageModal("ESTRATEGIAS DE DEUDA", "\n".join(partes)))


class DeudaDetalleScreen(SeccionScreen):
    def __init__(self, idx: int):
        super().__init__()
        self.idx = idx

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(id="titulo-deuda", classes="titulo-pantalla")
        with VerticalScroll(id="detalle-caja"):
            yield Static(id="detalle-texto")
        with Horizontal(classes="acciones"):
            yield Button("Simular otro pago", id="simular", variant="success")
            yield Button("Volver", id="volver")
        yield Footer()

    def on_mount(self) -> None:
        self._refrescar()

    def _deuda(self) -> dict:
        return self.app.datos["deudas"][self.idx]  # type: ignore[attr-defined]

    def _refrescar(self) -> None:
        d = self._deuda()
        self.query_one("#titulo-deuda", Static).update(d.get("nombre", "").upper())
        saldo = to_decimal(d["saldo"], CERO)
        interes = to_decimal(d["interes_mensual"], CERO)
        pago_total = to_decimal(d["pago_minimo"], CERO) + to_decimal(d["pago_extra"], CERO)
        resultado = simular_deuda(saldo, interes, pago_total)
        self.query_one("#detalle-texto", Static).update(_texto_analisis("ANALISIS ACTUAL", saldo, interes,
                                                                          pago_total, resultado))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "volver":
            self.app.pop_screen()
        elif event.button.id == "simular":
            self._simular()

    def _simular(self) -> None:
        d = self._deuda()
        saldo = to_decimal(d["saldo"], CERO)
        interes = to_decimal(d["interes_mensual"], CERO)
        pago_minimo = to_decimal(d["pago_minimo"], CERO)
        pago_total_actual = pago_minimo + to_decimal(d["pago_extra"], CERO)
        campos = [Campo("pago", "Simular pago total mensual ($)", tipo="dinero", valor=str(pago_total_actual))]

        def al_cerrar(valores: Optional[Dict[str, str]]) -> None:
            if not valores:
                return
            nuevo_pago = to_decimal(valores["pago"], CERO)
            resultado_actual = simular_deuda(saldo, interes, pago_total_actual)
            resultado_sim = simular_deuda(saldo, interes, nuevo_pago)

            cuerpo = (
                _texto_analisis("SITUACION ACTUAL", saldo, interes, pago_total_actual, resultado_actual)
                + "\n\n"
                + _texto_analisis("SIMULACION", saldo, interes, nuevo_pago, resultado_sim)
            )
            if resultado_actual["se_liquido"] and resultado_sim["se_liquido"]:
                diferencia_meses = resultado_actual["meses"] - resultado_sim["meses"]
                interes_ahorrado = resultado_actual["interes_total"] - resultado_sim["interes_total"]
                cuerpo += (
                    f"\n\n{fila_texto('Diferencia de meses', str(diferencia_meses))}\n"
                    f"{fila_texto('Interes ahorrado', fmt_money(interes_ahorrado))}"
                )

            def despues_de_ver(_: None) -> None:
                nuevo_extra = nuevo_pago - pago_minimo
                if nuevo_extra < CERO:
                    self.app.push_screen(MessageModal(
                        "AVISO", "El nuevo pago es menor que el pago minimo: no se puede guardar como pago extra."))
                    return

                def al_confirmar_guardar(si: bool) -> None:
                    if si:
                        d["pago_extra"] = str(redondear(nuevo_extra))
                        guardar_datos(self.app.datos)  # type: ignore[attr-defined]
                        self._refrescar()

                self.app.push_screen(
                    ConfirmModal(f"Guardar este nuevo pago extra ({fmt_money(nuevo_extra)}) en la deuda?\n"
                                 "(esta simulacion NO modifica los datos guardados automaticamente)"),
                    al_confirmar_guardar,
                )

            self.app.push_screen(MessageModal("COMPARACION", cuerpo), despues_de_ver)

        self.app.push_screen(FormModal("SIMULAR OTRO PAGO", campos), al_cerrar)


class CalculadoraRapidaModal(ModalScreen[None]):
    """
    Calcula cuanto se tarda en pagar una deuda que NO esta registrada en el
    sistema. No guarda nada. Reutiliza simular_deuda, el mismo motor que
    usa el analisis de las deudas ya registradas.
    """

    BINDINGS = [Binding("escape", "cerrar", "Cerrar")]

    DEFAULT_CSS = """
    CalculadoraRapidaModal { align: center middle; }
    CalculadoraRapidaModal > Vertical {
        width: 68; max-width: 94%; height: auto; max-height: 90%;
        border: round $accent; background: $surface; padding: 1 2;
    }
    CalculadoraRapidaModal Label.titulo { text-style: bold; color: $accent; margin-bottom: 1; }
    CalculadoraRapidaModal Label.etiqueta { margin-top: 1; }
    CalculadoraRapidaModal #resultado { margin-top: 1; }
    CalculadoraRapidaModal #botones { margin-top: 1; height: auto; align: right middle; }
    CalculadoraRapidaModal #botones Button { margin-left: 1; }
    """

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("CALCULADORA RAPIDA (deuda no registrada)", classes="titulo")
            yield Label("Saldo de la deuda ($)", classes="etiqueta")
            yield Input(value="0.00", id="saldo")
            yield Label("Tasa de interes mensual (ej. 2.5 para 2.5%)", classes="etiqueta")
            yield Input(value="0.00", id="interes")
            yield Label("Pago mensual ($)", classes="etiqueta")
            yield Input(value="0.00", id="pago")
            yield Static("", id="resultado")
            with Horizontal(id="botones"):
                yield Button("Cerrar", id="cerrar")
                yield Button("Calcular", id="calcular", variant="success")

    def action_cerrar(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cerrar":
            self.dismiss(None)
            return
        saldo = to_decimal(self.query_one("#saldo", Input).value)
        interes = to_decimal(self.query_one("#interes", Input).value)
        pago = to_decimal(self.query_one("#pago", Input).value)
        resultado_widget = self.query_one("#resultado", Static)

        if saldo is None or interes is None or pago is None:
            resultado_widget.update("[red]Revisa que los tres valores sean numeros validos.[/red]")
            return
        if saldo <= CERO:
            resultado_widget.update("[red]El saldo debe ser mayor que cero.[/red]")
            return
        if interes < CERO:
            resultado_widget.update("[red]La tasa de interes no puede ser negativa.[/red]")
            return
        if pago <= CERO:
            resultado_widget.update("[red]El pago debe ser mayor que cero.[/red]")
            return

        resultado = simular_deuda(saldo, interes, pago)
        resultado_widget.update(_texto_analisis("RESULTADO", saldo, interes, pago, resultado))


# ---------------------------------------------------------------------------
# 5. APP PRINCIPAL Y ESTILOS
# ---------------------------------------------------------------------------


class FinanzasApp(App):
    TITLE = "Finanzas Familiares"
    CSS = """
    Screen { background: $background; }

    .titulo-pantalla {
        text-style: bold;
        color: $accent;
        background: $panel;
        padding: 0 1;
        margin-bottom: 1;
        width: 100%;
    }

    .acciones { height: auto; margin-bottom: 1; }
    .acciones Button { margin-right: 1; }

    #resumen {
        border: round $accent;
        padding: 1 2;
        margin-bottom: 1;
        height: auto;
        max-height: 10;
    }

    #reporte-caja {
        border: round $accent;
        padding: 1 2;
        margin-bottom: 1;
        height: 1fr;
    }

    #menu-grid { align: center middle; height: 1fr; }
    .fila-menu { align: center middle; height: auto; margin-bottom: 1; }
    .fila-menu Button { width: 26; margin: 0 1; }

    DataTable { height: 1fr; margin-bottom: 1; }
    """

    def on_mount(self) -> None:
        self.datos: Dict[str, Any] = cargar_datos()
        mes_migrado = _migrar_a_meses(self.datos)
        if mes_migrado:
            guardar_datos(self.datos)
        self.push_screen(DashboardScreen())
        if mes_migrado:
            self.push_screen(MessageModal(
                "MIGRACION AUTOMATICA",
                f"Tus datos anteriores (ingresos, gastos, bloques y distribucion) se guardaron "
                f"como tu primer mes: '{mes_migrado}'.\n\n"
                "Deudas y ahorro siguen siendo globales: no cambian segun el mes.\n\n"
                "Desde [8] Meses puedes crear meses nuevos, con opcion de copiar estos datos como base."
            ))

    def guardar(self) -> None:
        guardar_datos(self.datos)


def main() -> None:
    FinanzasApp().run()


if __name__ == "__main__":
    main()
