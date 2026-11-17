"""Capa de almacenamiento y consultas analíticas sobre DuckDB.

Esquema en estrella:
    fact_pnl        -> hechos: saldo mensual por empresa, ERP y cuenta corporativa
    dim_empresas    -> 16 empresas y su área de negocio (7 áreas)
    dim_areas       -> catálogo de áreas
    dim_cuentas     -> jerarquía del estado de resultados
    mapeo_cuentas   -> homologación cuenta ERP -> cuenta corporativa (editable desde la app)
    log_auditoria   -> cargas, errores, anomalías
    v_pnl           -> vista desnormalizada usada por el dashboard
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import date

import duckdb
import pandas as pd

from catalogos import AREAS, DB_PATH, EMPRESAS, MAPEO, PLAN_CUENTAS

DDL = [
    "CREATE SEQUENCE IF NOT EXISTS seq_log START 1",
    """CREATE TABLE IF NOT EXISTS dim_areas (
           area_codigo VARCHAR PRIMARY KEY, area_nombre VARCHAR NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS dim_empresas (
           empresa_codigo VARCHAR PRIMARY KEY, empresa_nombre VARCHAR NOT NULL,
           area_codigo VARCHAR NOT NULL, erp_origen VARCHAR NOT NULL, activa BOOLEAN DEFAULT TRUE)""",
    """CREATE TABLE IF NOT EXISTS dim_cuentas (
           cuenta_corp VARCHAR PRIMARY KEY, cuenta_nombre VARCHAR, rubro_nivel1 VARCHAR,
           subrubro_nivel2 VARCHAR, orden INTEGER, signo_pnl INTEGER, es_control BOOLEAN)""",
    """CREATE TABLE IF NOT EXISTS mapeo_cuentas (
           erp VARCHAR, cuenta_erp VARCHAR, cuenta_corp VARCHAR, signo_erp INTEGER,
           descripcion_erp VARCHAR, PRIMARY KEY (erp, cuenta_erp))""",
    """CREATE TABLE IF NOT EXISTS fact_pnl (
           periodo DATE, empresa_codigo VARCHAR, erp VARCHAR, cuenta_corp VARCHAR,
           monto DOUBLE, archivo VARCHAR, fecha_carga TIMESTAMP)""",
    """CREATE TABLE IF NOT EXISTS log_auditoria (
           id BIGINT DEFAULT nextval('seq_log'), fecha TIMESTAMP, tipo VARCHAR, nivel VARCHAR,
           periodo DATE, erp VARCHAR, empresa_codigo VARCHAR, cuenta_corp VARCHAR, archivo VARCHAR,
           mensaje VARCHAR, valor_esperado DOUBLE, valor_observado DOUBLE, variacion_pct DOUBLE)""",
    """CREATE OR REPLACE VIEW v_pnl AS
           SELECT f.periodo, year(f.periodo) AS anio, month(f.periodo) AS mes,
                  f.empresa_codigo, e.empresa_nombre, e.area_codigo, a.area_nombre, f.erp,
                  f.cuenta_corp, c.cuenta_nombre, c.rubro_nivel1, c.subrubro_nivel2, c.orden,
                  f.monto, f.monto * c.signo_pnl AS monto_signed, f.archivo
           FROM fact_pnl f
           JOIN dim_empresas e ON e.empresa_codigo = f.empresa_codigo
           JOIN dim_areas a ON a.area_codigo = e.area_codigo
           JOIN dim_cuentas c ON c.cuenta_corp = f.cuenta_corp""",
]

COLUMNAS = {
    "dim_areas": ["area_codigo", "area_nombre"],
    "dim_empresas": ["empresa_codigo", "empresa_nombre", "area_codigo", "erp_origen", "activa"],
    "mapeo_cuentas": ["erp", "cuenta_erp", "cuenta_corp", "signo_erp", "descripcion_erp"],
}


@dataclass(frozen=True)
class Filtros:
    """Filtros globales del dashboard. Listas vacías significan «todas»."""
    desde: date
    hasta: date
    areas: tuple = ()
    empresas: tuple = ()
    erps: tuple = ()
    mes_max: int | None = None  # limita el año a los meses 1..mes_max (comparables en YTD)

    def desplazar_anios(self, n: int) -> "Filtros":
        return replace(self, desde=self.desde.replace(year=self.desde.year + n),
                       hasta=self.hasta.replace(year=self.hasta.year + n))

    def horizonte(self) -> "Filtros":
        """Mismos filtros de dimensión pero sobre todo el histórico."""
        return replace(self, desde=date(1900, 1, 1), hasta=date(2999, 12, 1))


# ---------------------------------------------------------------- conexión
@contextmanager
def sesion(path=DB_PATH):
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    try:
        yield con
    finally:
        con.close()


def inicializar(path=DB_PATH) -> None:
    """Crea esquema, vistas y datos maestros iniciales (sólo si las tablas están vacías)."""
    with sesion(path) as con:
        for sentencia in DDL:
            con.execute(sentencia)

        if con.execute("SELECT count(*) FROM dim_areas").fetchone()[0] == 0:
            con.executemany("INSERT INTO dim_areas VALUES (?, ?)", list(AREAS.items()))
        if con.execute("SELECT count(*) FROM dim_empresas").fetchone()[0] == 0:
            con.executemany(
                "INSERT INTO dim_empresas VALUES (?, ?, ?, ?, TRUE)",
                [(c, n, a, e) for c, n, a, e in EMPRESAS],
            )
        if con.execute("SELECT count(*) FROM dim_cuentas").fetchone()[0] == 0:
            con.executemany("INSERT INTO dim_cuentas VALUES (?, ?, ?, ?, ?, ?, ?)", PLAN_CUENTAS)
        if con.execute("SELECT count(*) FROM mapeo_cuentas").fetchone()[0] == 0:
            con.executemany("INSERT INTO mapeo_cuentas VALUES (?, ?, ?, ?, ?)", MAPEO)


# ---------------------------------------------------------------- lectura de catálogos
def _df(sql: str, params: list | None = None, path=DB_PATH) -> pd.DataFrame:
    with sesion(path) as con:
        return con.execute(sql, params or []).df()


def catalogo_empresas() -> pd.DataFrame:
    return _df("""SELECT e.empresa_codigo, e.empresa_nombre, e.area_codigo, a.area_nombre,
                         e.erp_origen, e.activa
                  FROM dim_empresas e JOIN dim_areas a USING (area_codigo)
                  ORDER BY e.empresa_codigo""")


def catalogo_areas() -> pd.DataFrame:
    return _df("SELECT * FROM dim_areas ORDER BY area_codigo")


def leer_tabla(nombre: str) -> pd.DataFrame:
    """Tabla maestra o de mapeo tal como está almacenada (para el editor de catálogos)."""
    return _df(f"SELECT {', '.join(COLUMNAS[nombre])} FROM {nombre} ORDER BY 1")


def catalogo_cuentas() -> pd.DataFrame:
    return _df("SELECT * FROM dim_cuentas ORDER BY orden")


def mapeo_cuentas() -> pd.DataFrame:
    return _df("SELECT * FROM mapeo_cuentas ORDER BY erp, cuenta_erp")


def anios_disponibles() -> list[int]:
    df = _df("SELECT DISTINCT year(periodo) AS anio FROM fact_pnl ORDER BY anio")
    return [int(a) for a in df["anio"]]


def ultimo_periodo() -> date | None:
    df = _df("SELECT max(periodo) AS p FROM fact_pnl")
    valor = df["p"].iloc[0]
    return None if pd.isna(valor) else pd.Timestamp(valor).date()


def existentes(periodo: date) -> pd.DataFrame:
    """Pares (empresa, ERP) que ya tienen información cargada en el período."""
    return _df("SELECT DISTINCT empresa_codigo, erp FROM fact_pnl WHERE periodo = ?", [periodo])


def historial(desde: date, hasta_excl: date) -> pd.DataFrame:
    """Saldo mensual por empresa y cuenta en un rango (usado por la detección de anomalías)."""
    return _df(
        """SELECT empresa_codigo, cuenta_corp, periodo, SUM(monto) AS monto
           FROM fact_pnl WHERE periodo >= ? AND periodo < ?
           GROUP BY ALL""",
        [desde, hasta_excl],
    )


# ---------------------------------------------------------------- consultas del dashboard
def _in(columna: str, valores: tuple, params: list) -> str:
    if not valores:
        return "TRUE"
    params.extend(valores)
    return f"{columna} IN ({', '.join('?' * len(valores))})"


def _where(f: Filtros) -> tuple[str, list]:
    params: list = [f.desde, f.hasta]
    cond = ["periodo BETWEEN ? AND ?", _in("area_codigo", f.areas, params),
            _in("empresa_codigo", f.empresas, params), _in("erp", f.erps, params)]
    if f.mes_max is not None:
        cond.append("mes <= ?")
        params.append(f.mes_max)
    return " AND ".join(cond), params


DIMENSIONES = {
    "total": "'Total'",
    "area": "area_nombre",
    "empresa": "empresa_nombre",
    "anio": "anio",
}


def pnl_agregado(f: Filtros, dimension: str = "total") -> pd.DataFrame:
    """Suma con signo P&L por cuenta corporativa y dimensión. Devuelve grupo, cuenta_corp, valor."""
    where, params = _where(f)
    sql = f"""SELECT {DIMENSIONES[dimension]} AS grupo, cuenta_corp,
                     SUM(monto_signed) AS valor
              FROM v_pnl WHERE {where}
              GROUP BY ALL"""
    return _df(sql, params)


def serie_cuenta(empresa: str, cuenta_corp: str, meses: int = 24) -> pd.DataFrame:
    """Evolución mensual de una cuenta de una empresa, con ERP y archivo de origen."""
    return _df(
        f"""SELECT periodo, erp, archivo, monto
            FROM fact_pnl
            WHERE empresa_codigo = ? AND cuenta_corp = ?
              AND periodo >= (SELECT max(periodo) FROM fact_pnl) - INTERVAL {int(meses)} MONTH
            ORDER BY periodo""",
        [empresa, cuenta_corp],
    )


def anomalias(desde: date | None = None, hasta: date | None = None) -> pd.DataFrame:
    return _df(
        """SELECT id, periodo, erp, empresa_codigo, cuenta_corp, archivo, mensaje,
                  valor_esperado, valor_observado, variacion_pct
           FROM log_auditoria
           WHERE tipo = 'ANOMALIA'
             AND (? IS NULL OR periodo >= ?) AND (? IS NULL OR periodo <= ?)
           ORDER BY periodo DESC, abs(variacion_pct) DESC""",
        [desde, desde, hasta, hasta],
    )


def log_eventos(tipo: str | None = None, limite: int = 500) -> pd.DataFrame:
    return _df(
        """SELECT fecha, tipo, nivel, periodo, erp, empresa_codigo, cuenta_corp, archivo, mensaje
           FROM log_auditoria WHERE (? IS NULL OR tipo = ?)
           ORDER BY fecha DESC LIMIT ?""",
        [tipo, tipo, limite],
    )


# ---------------------------------------------------------------- escritura
def guardar_tabla(nombre: str, df: pd.DataFrame) -> None:
    """Reemplaza el contenido de una tabla de mapeo o maestro dentro de una transacción."""
    columnas = COLUMNAS[nombre]
    with sesion() as con:
        con.register("tmp_tabla", df[columnas])
        con.execute("BEGIN TRANSACTION")
        try:
            con.execute(f"DELETE FROM {nombre}")
            con.execute(f"INSERT INTO {nombre} ({', '.join(columnas)}) SELECT {', '.join(columnas)} FROM tmp_tabla")
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        finally:
            con.unregister("tmp_tabla")
