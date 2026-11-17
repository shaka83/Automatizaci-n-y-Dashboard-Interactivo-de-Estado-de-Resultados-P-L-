"""Catálogos maestros y configuración de los ERP de origen.

Es la fuente única de verdad del proyecto: la usan la base de datos (semillas),
el ETL (lectura de extractos), el generador de datos sintéticos y el dashboard.
"""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = Path(os.environ.get("PNL_DB_PATH", DATA_DIR / "pnl.duckdb"))

# ---------------------------------------------------------------- parámetros de control
UMBRAL_ANOMALIA = 0.15          # ±15 % frente al mes anterior o al promedio móvil de 12 meses
MESES_MIN_HISTORIA = 3          # meses mínimos para calcular el promedio móvil
TOLERANCIA_CUADRATURA = 1.0     # diferencia absoluta tolerada entre subtotal calculado y reportado

# Horizonte histórico sintético: 5 años, último cierre disponible = oct-2026
PERIODO_INICIO = date(2022, 1, 1)
ULTIMO_CIERRE = date(2026, 10, 1)

# ---------------------------------------------------------------- áreas de negocio (7)
AREAS = {
    "COM": "Comercial",
    "MAN": "Manufactura",
    "SER": "Servicios",
    "LOG": "Logística",
    "TEC": "Tecnología",
    "CON": "Construcción",
    "CMA": "Consumo Masivo",
}

# ---------------------------------------------------------------- empresas (16)
# (código, nombre, código de área, ERP de origen)
EMPRESAS = [
    ("EMP-01", "Distribuidora Andina S.A.", "COM", "ALFA"),
    ("EMP-02", "Retail Costa Sur Ltda.", "COM", "ALFA"),
    ("EMP-03", "Comercial del Valle S.A.", "COM", "BETA"),
    ("EMP-04", "Metalúrgica Pacífico S.A.", "MAN", "ALFA"),
    ("EMP-05", "Textiles Monte Verde Ltda.", "MAN", "GAMMA"),
    ("EMP-06", "Plásticos Industriales Norte S.A.", "MAN", "BETA"),
    ("EMP-07", "Consultora Estratégica Ibérica S.A.", "SER", "ALFA"),
    ("EMP-08", "Servicios Contables Horizonte Ltda.", "SER", "GAMMA"),
    ("EMP-09", "Transportes Rápidos del Sur S.A.", "LOG", "BETA"),
    ("EMP-10", "Almacenes Logísticos Central S.A.", "LOG", "GAMMA"),
    ("EMP-11", "Soluciones Digitales Nexo S.A.", "TEC", "ALFA"),
    ("EMP-12", "Desarrollo Software Atlas Ltda.", "TEC", "BETA"),
    ("EMP-13", "Constructora Cumbre S.A.", "CON", "GAMMA"),
    ("EMP-14", "Inmobiliaria Puerto Viejo Ltda.", "CON", "ALFA"),
    ("EMP-15", "Alimentos Del Campo S.A.", "CMA", "BETA"),
    ("EMP-16", "Bebidas Montaña S.A.", "CMA", "GAMMA"),
]

# ---------------------------------------------------------------- ERP de origen
# Cada ERP tiene su propio formato de extracto. El ETL los homologa.
ERPS = {
    "ALFA": {
        "nombre": "ERP Alfa",
        "formato": "csv",
        "sep": ";",
        "decimal": ",",
        "hoja": None,
        "formato_periodo": "%Y%m",
        "columnas": {"periodo": "PERIODO", "empresa": "COD_CIA", "cuenta": "CUENTA", "importe": "IMPORTE"},
    },
    "BETA": {
        "nombre": "ERP Beta",
        "formato": "csv",
        "sep": ",",
        "decimal": ".",
        "hoja": None,
        "formato_periodo": "%Y-%m-%d",
        "columnas": {"periodo": "fecha", "empresa": "empresa", "cuenta": "cta", "importe": "monto_signado"},
    },
    "GAMMA": {
        "nombre": "ERP Gamma",
        "formato": "xlsx",
        "sep": None,
        "decimal": None,
        "hoja": "PYG",
        "formato_periodo": "%Y-%m",
        "columnas": {"periodo": "Periodo", "empresa": "Empresa", "cuenta": "Codigo", "importe": "Importe"},
    },
}

# ---------------------------------------------------------------- plan de cuentas corporativo
# (cuenta_corp, nombre, rubro_nivel1, subrubro_nivel2, orden, signo_pnl, es_control)
# signo_pnl: +1 suma al resultado (ingresos), -1 resta (costos y gastos).
# Los montos se almacenan con magnitud natural (positiva) y el signo se aplica en el P&L.
PLAN_CUENTAS = [
    ("4000", "Ventas brutas", "Ingresos", "Ingresos operativos", 10, 1, False),
    ("4100", "Devoluciones y descuentos", "Ingresos", "Ingresos operativos", 20, -1, False),
    ("5000", "Costo de materiales", "Costo de Ventas", "Costo directo", 30, -1, False),
    ("5100", "Mano de obra directa", "Costo de Ventas", "Costo directo", 40, -1, False),
    ("5200", "Costos indirectos de fabricación", "Costo de Ventas", "Costo indirecto", 50, -1, False),
    ("6000", "Gastos de comercialización", "Gastos Operativos", "Gastos de ventas", 60, -1, False),
    ("6100", "Remuneraciones administrativas", "Gastos Operativos", "Gastos de administración", 70, -1, False),
    ("6200", "Servicios generales", "Gastos Operativos", "Gastos de administración", 80, -1, False),
    ("6300", "Arriendos e inmuebles", "Gastos Operativos", "Gastos de administración", 90, -1, False),
    ("7000", "Depreciación de activo fijo", "Depreciación y Amortización", "Depreciación", 100, -1, False),
    ("7100", "Amortización de intangibles", "Depreciación y Amortización", "Amortización", 110, -1, False),
    ("8000", "Ingresos financieros", "Resultado Financiero", "Financiero", 120, 1, False),
    ("8100", "Gastos financieros", "Resultado Financiero", "Financiero", 130, -1, False),
    ("9000", "Impuesto a la renta", "Impuestos", "Impuesto a la renta", 140, -1, False),
    # Totales de control que reporta cada ERP: se usan para validar la cuadratura, no se cargan al hecho.
    ("CTRL_UB", "Control: utilidad bruta reportada", "Control", "Control", 900, 1, True),
    ("CTRL_UN", "Control: utilidad neta reportada", "Control", "Control", 910, 1, True),
]

CUENTAS_CORP = [c[0] for c in PLAN_CUENTAS]

# ---------------------------------------------------------------- mapeo ERP -> plan corporativo
# Código de cuenta de cada ERP, en el mismo orden que CUENTAS_CORP.
_CODIGOS_ERP = {
    "ALFA": ["4000", "4100", "5000", "5100", "5200", "6000", "6100", "6200", "6300",
             "7000", "7100", "8000", "8100", "9000", "TOT_UB", "TOT_UN"],
    "BETA": ["100010", "100020", "200010", "200020", "200030", "300010", "300020", "300030", "300040",
             "400010", "400020", "500010", "500020", "600010", "900001", "900002"],
    "GAMMA": ["VB-01", "DV-01", "CV-01", "MO-01", "CI-01", "GV-01", "GA-01", "GS-01", "AR-01",
              "DEP-01", "AMO-01", "FI-01", "FG-01", "IR-01", "UB-CTRL", "UN-CTRL"],
}
# Signo con el que cada ERP exporta el importe respecto de la magnitud natural.
# BETA exporta los costos en negativo (efecto sobre la utilidad); ALFA y GAMMA en positivo.
_SIGNO_ERP = {
    "ALFA": [1] * 16,
    "BETA": [1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, 1, -1, -1, 1, 1],
    "GAMMA": [1] * 16,
}

MAPEO = [
    (erp, _CODIGOS_ERP[erp][i], CUENTAS_CORP[i], _SIGNO_ERP[erp][i], PLAN_CUENTAS[i][1])
    for erp in ERPS
    for i in range(len(CUENTAS_CORP))
]
