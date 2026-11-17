"""Generador de datos sintéticos para probar el pipeline de punta a punta.

Genera:
  data/extractos/<erp>/<AAAAMM>.<ext>   histórico limpio 2022-01 .. 2026-10 (58 meses)
  data/pruebas/<AAAAMM>/...             lote de prueba 2026-11 con errores deliberados

Errores deliberados en el lote de prueba (para demostrar el motor de validación):
  1. BETA: cuenta ERP «600777» sin mapeo                       -> CUENTA_HUERFANA   (ERROR)
  2. GAMMA: empresa «EMP-017» fuera del catálogo maestro        -> EMPRESA_HUERFANA  (ERROR)
  3. ALFA: total de control de EMP-04 con +250.000              -> CUADRATURA        (ERROR)
  4. GAMMA: ingresos financieros de EMP-08 en negativo          -> SIGNO_INCONSISTENTE (ERROR)
  5. BETA: ventas de EMP-09 +60 % vs mes anterior              -> ANOMALIA          (ALERTA)

Uso:
  python mock_data.py            # genera archivos
  python mock_data.py --cargar   # genera y carga el histórico en DuckDB (reinicia la base)
"""
from __future__ import annotations

import argparse
import math
import shutil
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from catalogos import CUENTAS_CORP, DATA_DIR, DB_PATH, EMPRESAS, ERPS, MAPEO, PERIODO_INICIO, PLAN_CUENTAS, ULTIMO_CIERRE

SEMILLA = 42
PERIODO_PRUEBA = date(2026, 11, 1)
EXTRACTOS = DATA_DIR / "extractos"
PRUEBAS = DATA_DIR / "pruebas"

NOMBRES = {c[0]: c[1] for c in PLAN_CUENTAS}
# (erp, cuenta_corp) -> (código en el ERP, signo con el que el ERP exporta el importe)
INVERSO = {(erp, corp): (cod, signo) for erp, cod, corp, signo, _ in MAPEO}


def meses(inicio: date, fin: date) -> list[date]:
    out, y, m = [], inicio.year, inicio.month
    while date(y, m, 1) <= fin:
        out.append(date(y, m, 1))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


PERIODOS = meses(PERIODO_INICIO, ULTIMO_CIERRE)


# ---------------------------------------------------------------- modelo económico
def parametros(rng: np.random.Generator) -> dict[str, dict[str, float]]:
    """Perfil económico por empresa: escala, crecimiento y ratios de costos."""
    perfiles = {}
    for codigo, *_ in EMPRESAS:
        perfiles[codigo] = {
            "base": rng.uniform(350_000, 2_200_000),   # ventas brutas mensuales base
            "crec": rng.uniform(0.04, 0.12),           # crecimiento anual
            "devol": rng.uniform(0.010, 0.025),
            "mat": rng.uniform(0.42, 0.52), "mo": rng.uniform(0.08, 0.12), "cif": rng.uniform(0.04, 0.07),
            "com": rng.uniform(0.05, 0.08), "rem": rng.uniform(0.04, 0.07),
            "serv": rng.uniform(0.02, 0.035), "arr": rng.uniform(0.010, 0.020),
            "dep": rng.uniform(0.005, 0.010), "amort": rng.uniform(0.001, 0.003),
            "fin_ing": rng.uniform(0.001, 0.003), "fin_gas": rng.uniform(0.004, 0.008),
        }
    return perfiles


def valores_mes(periodo: date, p: dict, rng: np.random.Generator, spike: float = 1.0) -> dict[str, int]:
    """Montos en magnitud natural para todas las cuentas corporativas de una empresa y mes.

    Los totales de control (CTRL_UB, CTRL_UN) se calculan con la misma lógica contable
    que valida el sistema, así la cuadratura cierra en el histórico limpio.
    """
    t = (periodo.year - PERIODO_INICIO.year) * 12 + (periodo.month - PERIODO_INICIO.month)
    estacional = 1 + 0.04 * math.sin(2 * math.pi * (periodo.month - 1) / 12)
    ventas_base = p["base"] * (1 + p["crec"]) ** (t / 12) * estacional * rng.normal(1, 0.010)
    devol = ventas_base * p["devol"] * rng.normal(1, 0.015)
    neto = ventas_base - devol

    def ruido(x: float) -> int:
        return round(x * rng.normal(1, 0.005))

    v: dict[str, int] = {
        "4000": round(ventas_base * spike),          # único concepto afectado por el pico de prueba
        "4100": round(devol),
        "5000": ruido(neto * p["mat"]),
        "5100": ruido(neto * p["mo"]),
        "5200": ruido(neto * p["cif"]),
        "6000": ruido(neto * p["com"]),
        "6100": ruido(neto * p["rem"]),
        "6200": ruido(neto * p["serv"]),
        "6300": ruido(neto * p["arr"]),
        "7000": round(p["base"] * p["dep"]),
        "7100": round(p["base"] * p["amort"]),
        "8000": ruido(ventas_base * p["fin_ing"]),
        "8100": ruido(ventas_base * p["fin_gas"]),
    }
    ub = (v["4000"] - v["4100"]) - (v["5000"] + v["5100"] + v["5200"])
    go = v["6000"] + v["6100"] + v["6200"] + v["6300"]
    da = v["7000"] + v["7100"]
    uai = ub - go - da + (v["8000"] - v["8100"])
    v["9000"] = round(max(uai, 0) * 0.27)
    v["CTRL_UB"] = ub
    v["CTRL_UN"] = uai - v["9000"]
    return v


# ---------------------------------------------------------------- escritura de extractos
def _escribir(erp: str, periodo: date, filas: list[tuple[str, str, float]], ruta: Path,
              extra: list[dict] | None = None) -> None:
    """Escribe un extracto en el formato propio del ERP (sólo la estructura cambia)."""
    registros = []
    for empresa, corp, monto in filas:
        codigo, signo = INVERSO[(erp, corp)]
        registros.append({"empresa": empresa, "cod": codigo, "corp": corp,
                          "importe": monto * signo, "nombre": NOMBRES[corp]})
    df = pd.DataFrame(registros)
    ruta.parent.mkdir(parents=True, exist_ok=True)

    if erp == "ALFA":
        out = pd.DataFrame({"PERIODO": periodo.strftime("%Y%m"), "COD_CIA": df["empresa"],
                            "CUENTA": df["cod"], "DESCRIPCION": df["nombre"], "IMPORTE": df["importe"]})
        for fila in extra or []:
            out = pd.concat([out, pd.DataFrame([fila])], ignore_index=True)
        out.to_csv(ruta, sep=";", decimal=",", index=False, float_format="%.2f", encoding="utf-8-sig")
    elif erp == "BETA":
        out = pd.DataFrame({"fecha": periodo.strftime("%Y-%m-01"), "empresa": df["empresa"],
                            "cta": df["cod"], "monto_signado": df["importe"], "glosa": df["nombre"]})
        for fila in extra or []:
            out = pd.concat([out, pd.DataFrame([fila])], ignore_index=True)
        out.to_csv(ruta, sep=",", index=False)
    else:  # GAMMA
        out = pd.DataFrame({"Periodo": periodo.strftime("%Y-%m"), "Empresa": df["empresa"],
                            "Codigo": df["cod"], "Importe": df["importe"]})
        for fila in extra or []:
            out = pd.concat([out, pd.DataFrame([fila])], ignore_index=True)
        out.to_excel(ruta, sheet_name=ERPS[erp]["hoja"], index=False)


def _filas_por_erp(valores: dict[str, dict[str, int]]) -> dict[str, list[tuple[str, str, float]]]:
    por_erp: dict[str, list] = {erp: [] for erp in ERPS}
    for codigo, nombre, _area, erp in EMPRESAS:
        for corp in CUENTAS_CORP:
            por_erp[erp].append((codigo, corp, valores[codigo][corp]))
    return por_erp


def _ruta(erp: str, nombre_base: Path) -> Path:
    ext = "csv" if ERPS[erp]["formato"] == "csv" else "xlsx"
    return nombre_base.with_suffix(f".{ext}")


def generar_historico() -> int:
    """Histórico limpio: un archivo por ERP y mes."""
    perfiles = parametros(np.random.default_rng(SEMILLA))
    rng = np.random.default_rng(SEMILLA + 1)
    total = 0
    for periodo in PERIODOS:
        valores = {codigo: valores_mes(periodo, perfiles[codigo], rng) for codigo, *_ in EMPRESAS}
        for erp, filas in _filas_por_erp(valores).items():
            ruta = _ruta(erp, EXTRACTOS / erp.lower() / f"{periodo:%Y%m}")
            _escribir(erp, periodo, filas, ruta)
            total += 1
    return total


def generar_pruebas() -> Path:
    """Lote de prueba con errores deliberados (ver encabezado del módulo)."""
    perfiles = parametros(np.random.default_rng(SEMILLA))
    rng = np.random.default_rng(SEMILLA + 2)
    valores = {}
    for codigo, *_ in EMPRESAS:
        spike = 1.6 if codigo == "EMP-09" else 1.0          # caso 5: pico de ventas
        valores[codigo] = valores_mes(PERIODO_PRUEBA, perfiles[codigo], rng, spike=spike)
    por_erp = _filas_por_erp(valores)
    carpeta = PRUEBAS / f"{PERIODO_PRUEBA:%Y%m}"

    # 3. Descuadre: total de control de utilidad bruta de EMP-04 incorrecto en ALFA
    por_erp["ALFA"] = [(e, c, m + 250_000 if (e, c) == ("EMP-04", "CTRL_UB") else m)
                       for e, c, m in por_erp["ALFA"]]
    # 4. Signo inconsistente: ingresos financieros de EMP-08 en negativo en GAMMA
    por_erp["GAMMA"] = [(e, c, -35_000 if (e, c) == ("EMP-08", "8000") else m)
                        for e, c, m in por_erp["GAMMA"]]

    # 1. Cuenta sin mapeo en BETA (fila añadida directamente en el formato del ERP)
    extra_beta = [{"fecha": PERIODO_PRUEBA.strftime("%Y-%m-01"), "empresa": "EMP-03", "cta": "600777",
                   "monto_signado": -12_500, "glosa": "Cuenta no homologada"}]
    # 2. Empresa fuera del catálogo en GAMMA
    extra_gamma = [{"Periodo": PERIODO_PRUEBA.strftime("%Y-%m"), "Empresa": "EMP-017",
                    "Codigo": "VB-01", "Importe": 800_000}]

    extras = {"ALFA": None, "BETA": extra_beta, "GAMMA": extra_gamma}
    for erp, filas in por_erp.items():
        ruta = _ruta(erp, carpeta / f"{erp.lower()}_{PERIODO_PRUEBA:%Y%m}")
        _escribir(erp, PERIODO_PRUEBA, filas, ruta, extra=extras[erp])
    return carpeta


# ---------------------------------------------------------------- carga del histórico
def cargar_historico() -> None:
    """Reinicia la base y carga todos los meses del histórico por el pipeline real."""
    from database import inicializar
    from etl import confirmar_carga, procesar_lote

    for sufijo in ("", ".wal"):
        ruta = Path(str(DB_PATH) + sufijo)
        if ruta.exists():
            ruta.unlink()
    inicializar()

    for periodo in PERIODOS:
        archivos = {}
        for erp in ERPS:
            carpeta = EXTRACTOS / erp.lower()
            encontrados = sorted(carpeta.glob(f"{periodo:%Y%m}.*"))
            archivos[erp] = [(p.name, p.read_bytes()) for p in encontrados]
        res = procesar_lote(periodo, archivos)
        if res.bloqueantes:
            raise SystemExit(f"{periodo:%Y-%m}: {res.bloqueantes} errores bloqueantes\n{res.hallazgos}")
        info = confirmar_carga(res)
        print(f"  {periodo:%Y-%m}: {info['filas']} filas, {info['anomalias']} anomalías registradas")


def main() -> None:
    parser = argparse.ArgumentParser(description="Genera datos sintéticos del P&L")
    parser.add_argument("--cargar", action="store_true",
                        help="carga el histórico en DuckDB (reinicia la base de datos)")
    args = parser.parse_args()

    if EXTRACTOS.exists():
        shutil.rmtree(EXTRACTOS)
    if PRUEBAS.exists():
        shutil.rmtree(PRUEBAS)

    n = generar_historico()
    carpeta = generar_pruebas()
    print(f"Extractos históricos: {n} archivos en {EXTRACTOS}")
    print(f"Lote de prueba {PERIODO_PRUEBA:%Y-%m}: {carpeta}")

    if args.cargar:
        print("Cargando histórico en DuckDB...")
        cargar_historico()
        print(f"Base de datos lista: {DB_PATH}")


if __name__ == "__main__":
    main()
