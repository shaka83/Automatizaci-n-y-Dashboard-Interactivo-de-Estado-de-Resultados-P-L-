"""Lógica del Estado de Resultados: subtotales, márgenes, KPIs y variaciones.

Trabaja sobre matrices «cuenta corporativa × grupo» con los montos ya firmados
según el P&L (ingresos +, costos y gastos -). Los subtotales se construyen aquí,
no en la base de datos, para que la lógica contable quede en un único lugar.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from database import Filtros

# Etiquetas de subtotales usadas por el dashboard (gráfico de cascada, KPIs)
ING = "Ingresos netos"
UB = "UTILIDAD BRUTA"
EBITDA = "EBITDA"
EBIT = "UTILIDAD OPERATIVA (EBIT)"
UAI = "UTILIDAD ANTES DE IMPUESTOS"
UN = "UTILIDAD NETA"

MESES = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
         "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]


def rango_periodos(anio: int, mes: int, modo: str) -> Filtros:
    """Traduce año/mes y modo (Mensual | Acumulado YTD) a un rango de períodos."""
    if modo == "Acumulado YTD":
        return Filtros(desde=date(anio, 1, 1), hasta=date(anio, mes, 1), mes_max=mes)
    return Filtros(desde=date(anio, mes, 1), hasta=date(anio, mes, 1))


def pivot(df: pd.DataFrame) -> pd.DataFrame:
    """Convierte la salida larga de la base (grupo, cuenta_corp, valor) en matriz."""
    if df.empty:
        return pd.DataFrame()
    return df.pivot_table(index="cuenta_corp", columns="grupo", values="valor",
                          aggfunc="sum", fill_value=0.0)


def construir_estado(matriz: pd.DataFrame, cuentas: pd.DataFrame) -> pd.DataFrame:
    """Estado de resultados ordenado contablemente.

    Devuelve un DataFrame indexado por etiqueta de línea, con una columna `tipo`
    (detalle | subtotal | total) y una columna por grupo.
    """
    cuentas = cuentas[~cuentas["es_control"]].set_index("cuenta_corp")
    det = matriz.reindex(cuentas.index).fillna(0.0) if not matriz.empty else pd.DataFrame(
        0.0, index=cuentas.index, columns=["Total"])

    filas: list[tuple[str, str, pd.Series]] = []

    def bloque(rubro: str, etiqueta: str) -> pd.Series:
        ids = cuentas.index[cuentas["rubro_nivel1"] == rubro]
        for cid in ids:
            filas.append((f"    {cuentas.at[cid, 'cuenta_nombre']}", "detalle", det.loc[cid]))
        total = det.loc[ids].sum()
        filas.append((etiqueta, "subtotal", total))
        return total

    ingresos = bloque("Ingresos", ING)
    costo = bloque("Costo de Ventas", "Costo de ventas")
    ub = ingresos + costo
    filas.append((UB, "total", ub))
    gastos = bloque("Gastos Operativos", "Gastos operativos")
    ebitda = ub + gastos
    filas.append((EBITDA, "total", ebitda))
    dya = bloque("Depreciación y Amortización", "Depreciación y amortización")
    ebit = ebitda + dya
    filas.append((EBIT, "total", ebit))
    rf = bloque("Resultado Financiero", "Resultado financiero")
    uai = ebit + rf
    filas.append((UAI, "total", uai))
    imp = bloque("Impuestos", "Impuesto a la renta")
    un = uai + imp
    filas.append((UN, "total", un))

    etiquetas = [f[0] for f in filas]
    tabla = pd.DataFrame([f[2] for f in filas], index=etiquetas)
    tabla.insert(0, "tipo", [f[1] for f in filas])
    return tabla


def resumen(serie: pd.Series) -> dict[str, float]:
    """KPIs y márgenes a partir de la columna de totales del estado de resultados."""
    ing = float(serie.get(ING, 0.0))
    def margen(valor: float) -> float | None:
        return None if ing == 0 else valor / ing
    return {
        "ingresos": ing,
        "ebitda": float(serie.get(EBITDA, 0.0)),
        "utilidad_neta": float(serie.get(UN, 0.0)),
        "utilidad_bruta": float(serie.get(UB, 0.0)),
        "margen_bruto": margen(float(serie.get(UB, 0.0))),
        "margen_ebitda": margen(float(serie.get(EBITDA, 0.0))),
        "margen_neto": margen(float(serie.get(UN, 0.0))),
    }


def variacion(actual: float | None, previo: float | None) -> float | None:
    if actual is None or previo is None or previo == 0 or pd.isna(previo):
        return None
    return (actual - previo) / abs(previo)


def agregar_margenes(estado: pd.DataFrame) -> pd.DataFrame:
    """Anexa filas de márgenes (%) calculadas columna a columna."""
    columnas = [c for c in estado.columns if c != "tipo"]
    ing = estado.loc[ING, columnas].astype(float)
    filas = {}
    for etiqueta, nombre in [(UB, "Margen bruto %"), (EBITDA, "Margen EBITDA %"), (UN, "Margen neto %")]:
        serie = estado.loc[etiqueta, columnas].astype(float)
        filas[nombre] = serie.where(ing != 0) / ing.where(ing != 0)
    marg = pd.DataFrame(filas).T
    marg.insert(0, "tipo", "margen")
    return pd.concat([estado, marg])


def serie_waterfall(estado_total: pd.Series) -> tuple[list[str], list[str], list[float]]:
    """Etiquetas, medidas (relative | total) y valores para el gráfico de cascada."""
    pasos = [
        ("Ingresos netos", "relative", ING),
        ("Costo de ventas", "relative", "Costo de ventas"),
        ("Utilidad bruta", "total", UB),
        ("Gastos operativos", "relative", "Gastos operativos"),
        ("EBITDA", "total", EBITDA),
        ("Depreciación y amortización", "relative", "Depreciación y amortización"),
        ("Utilidad operativa (EBIT)", "total", EBIT),
        ("Resultado financiero", "relative", "Resultado financiero"),
        ("Utilidad antes de impuestos", "total", UAI),
        ("Impuesto a la renta", "relative", "Impuesto a la renta"),
        ("Utilidad neta", "total", UN),
    ]
    etiquetas, medidas, valores = [], [], []
    for etiqueta, medida, clave in pasos:
        etiquetas.append(etiqueta)
        medidas.append(medida)
        valores.append(float(estado_total.get(clave, 0.0)))
    return etiquetas, medidas, valores


def evolucion_anual(estado_por_anio: pd.DataFrame) -> pd.DataFrame:
    """Serie anual: ingresos, EBITDA y margen EBITDA % (columnas = años)."""
    fila = estado_por_anio
    columnas = [c for c in fila.columns if c != "tipo"]
    ingresos = fila.loc[ING, columnas].astype(float)
    ebitda = fila.loc[EBITDA, columnas].astype(float)
    return pd.DataFrame({
        "anio": [int(c) for c in columnas],
        "ingresos": ingresos.values,
        "ebitda": ebitda.values,
        "margen_ebitda": (ebitda / ingresos.where(ingresos != 0)).values,
    })


def formato_moneda(valor: float | None) -> str:
    if valor is None or pd.isna(valor):
        return "—"
    return f"({abs(valor):,.0f})" if valor < 0 else f"{valor:,.0f}"


def formato_pct(valor: float | None) -> str:
    if valor is None or pd.isna(valor):
        return "—"
    return f"{valor:.1%}"


def formato_millones(valor: float) -> str:
    return f"$ {valor / 1_000_000:,.2f} M"


__all__ = [
    "ING", "UB", "EBITDA", "EBIT", "UAI", "UN", "MESES", "rango_periodos", "pivot",
    "construir_estado", "resumen", "variacion", "agregar_margenes", "serie_waterfall",
    "evolucion_anual", "formato_moneda", "formato_pct", "formato_millones",
]
