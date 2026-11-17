"""Pruebas de punta a punta: ETL, validaciones, cuadratura y carga controlada."""
from datetime import date

import pytest

import analisis as an
import database as db
import etl
from catalogos import ERPS

PERIODO_PRUEBA = date(2026, 11, 1)
PERIODO_CONTROL = date(2026, 10, 1)
ERRORES_ESPERADOS = {"CUENTA_HUERFANA", "EMPRESA_HUERFANA", "SIGNO_INCONSISTENTE", "CUADRATURA"}


def _archivos(carpeta, patron="*"):
    """Lee los extractos de una carpeta agrupados por ERP (mismo formato que la interfaz)."""
    return {erp: [(p.name, p.read_bytes()) for p in sorted(carpeta.glob(f"{erp.lower()}{patron}"))]
            for erp in ERPS}


def _extractos_del_mes(datos, periodo):
    return {erp: [(p.name, p.read_bytes())
                  for p in sorted((datos["extractos"] / erp.lower()).glob(f"{periodo:%Y%m}.*"))]
            for erp in ERPS}


def test_histórico_limpio_sin_errores_ni_anomalias(datos_sinteticos):
    """58 meses cargados por el pipeline: 16 empresas, 7 áreas, 3 ERP, sin bloqueos ni alertas."""
    assert db.anios_disponibles() == [2022, 2023, 2024, 2025, 2026]
    empresas = db.catalogo_empresas()
    assert len(empresas) == 16
    assert empresas["area_nombre"].nunique() == 7
    assert set(empresas["erp_origen"]) == set(ERPS)
    assert db.anomalias().empty


def test_utilidad_neta_del_dashboard_coincide_con_control_de_los_erp(datos_sinteticos):
    """La utilidad neta del estado de resultados debe igualar el total de control reportado por los ERP."""
    res = etl.procesar_lote(PERIODO_CONTROL, _extractos_del_mes(datos_sinteticos, PERIODO_CONTROL))
    assert res.bloqueantes == 0
    control = res.staged.loc[res.staged["cuenta_corp"] == "CTRL_UN", "monto"].sum()

    filtro = an.rango_periodos(2026, 10, "Mensual")
    estado = an.construir_estado(an.pivot(db.pnl_agregado(filtro, "total")), db.catalogo_cuentas())
    assert estado.loc[an.UN, "Total"] == pytest.approx(control, abs=1.0)


def test_lote_de_prueba_detecta_los_errores_plantados(datos_sinteticos):
    res = etl.procesar_lote(PERIODO_PRUEBA, _archivos(datos_sinteticos["pruebas"]))
    reglas = set(res.hallazgos.loc[res.hallazgos["nivel"] == "ERROR", "regla"])
    assert reglas == ERRORES_ESPERADOS
    assert not res.puede_confirmar(sobrescribir=True)

    anomalias = res.hallazgos[res.hallazgos["regla"] == "ANOMALIA"]
    assert any("EMP-09" in m and "· 4000" in m for m in anomalias["mensaje"])
    # Las 16 empresas del catálogo llegan en el lote; EMP-017 queda fuera de la matriz (no existe en el catálogo).
    assert (res.matriz["estado"] == "En lote").sum() == 16
    assert "EMP-017" not in set(res.matriz["empresa_codigo"])

    with pytest.raises(ValueError):
        etl.confirmar_carga(res, sobrescribir=True)


def test_sobrescritura_controlada_reemplaza_sin_duplicar(datos_sinteticos):
    res = etl.procesar_lote(PERIODO_CONTROL, _extractos_del_mes(datos_sinteticos, PERIODO_CONTROL))
    assert not res.duplicados.empty
    assert not res.puede_confirmar(sobrescribir=False)

    def filas():
        with db.sesion() as con:
            return con.execute("SELECT count(*) FROM fact_pnl WHERE periodo = ?", [PERIODO_CONTROL]).fetchone()[0]

    antes = filas()
    info = etl.confirmar_carga(res, sobrescribir=True)
    assert info["sobrescritos"] == len(res.duplicados)
    assert filas() == antes
