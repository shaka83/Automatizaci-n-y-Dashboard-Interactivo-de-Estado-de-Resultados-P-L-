"""Carga de extractos de los 3 ERP, validación previa al almacenamiento y confirmación controlada."""
from __future__ import annotations

from datetime import date

import streamlit as st

import database as db
import etl
from catalogos import ERPS

COLOR_NIVEL = {"ERROR": "background-color:#F9D6D2", "ADVERTENCIA": "background-color:#FCEBC4",
               "ALERTA": "background-color:#D6EAF8", "INFO": ""}
COLOR_ESTADO = {"Cargada en BD": "background-color:#FCEBC4", "En lote": "background-color:#D5F5E3",
                "Pendiente": "background-color:#EAECEE", "OK": "background-color:#D5F5E3",
                "ERROR": "background-color:#F9D6D2"}


def _periodo_por_defecto() -> date:
    ultimo = db.ultimo_periodo()
    if ultimo is None:
        return date(2026, 10, 1)
    return date(ultimo.year + (ultimo.month == 12), ultimo.month % 12 + 1, 1)


def render() -> None:
    st.title("Carga y validación de extractos")
    st.caption("Suba los extractos de cada ERP para un mismo período. Nada se almacena hasta que el lote "
               "supere la validación y usted confirme la carga.")

    por_defecto = _periodo_por_defecto()
    c1, c2, _ = st.columns([1, 1, 3])
    anio = c1.selectbox("Año del período", list(range(2022, 2031)), index=list(range(2022, 2031)).index(
        por_defecto.year), key="cg_anio")
    mes = c2.selectbox("Mes del período", list(range(1, 13)), index=por_defecto.month - 1,
                       format_func=lambda m: f"{m:02d}", key="cg_mes")
    periodo = date(anio, mes, 1)

    st.subheader("1 · Extractos por ERP")
    cols = st.columns(len(ERPS))
    archivos: dict[str, list[tuple[str, bytes]]] = {}
    for col, (erp, cfg) in zip(cols, ERPS.items()):
        with col:
            st.markdown(f"**{cfg['nombre']}** · {cfg['formato'].upper()}")
            subidos = st.file_uploader(f"Extractos de {cfg['nombre']}", type=[cfg["formato"]],
                                       accept_multiple_files=True, key=f"up_{erp}",
                                       label_visibility="collapsed")
            archivos[erp] = [(f.name, f.getvalue()) for f in subidos or []]

    sobrescribir = st.checkbox(
        "Permitir sobrescribir la información ya cargada (empresa/ERP/período) — control explícito",
        key="cg_sobre")

    if st.button("Validar lote", type="primary", disabled=not any(archivos.values())):
        with st.spinner("Leyendo, homologando y validando el lote…"):
            st.session_state["lote"] = etl.procesar_lote(periodo, archivos)

    res: etl.ResultadoLote | None = st.session_state.get("lote")
    if res is None:
        st.info("Seleccione el período, suba los extractos y pulse **Validar lote**. "
                "Para generar datos de prueba: `python mock_data.py --cargar`.")
        return
    if res.periodo != periodo:
        st.warning(f"El último lote validado es de {res.periodo:%Y-%m}. Vuelva a validar el período seleccionado.")
        return

    _reporte(res)
    _confirmacion(res, sobrescribir)


def _reporte(res: etl.ResultadoLote) -> None:
    st.subheader("2 · Reporte de validación")
    h = res.hallazgos
    n_err = int((h["nivel"] == "ERROR").sum()) if not h.empty else 0
    n_adv = int((h["nivel"] == "ADVERTENCIA").sum()) if not h.empty else 0

    c = st.columns(5)
    c[0].metric("Archivos", len(res.resumen_archivos))
    c[1].metric("Filas leídas", f"{res.filas_leidas:,}")
    c[2].metric("Errores bloqueantes", n_err)
    c[3].metric("Advertencias", n_adv)
    c[4].metric("Anomalías", len(res.anomalias))

    if n_err:
        st.error(f"Lote bloqueado: {n_err} error(es) deben corregirse antes de confirmar. Las cuentas y empresas "
                 "huérfanas se resuelven en **Catálogos y mapeos**; el resto, corrigiendo el extracto.")
    else:
        st.success("Sin errores bloqueantes.")

    t_hall, t_cuad, t_matriz, t_anom = st.tabs(["Hallazgos", "Cuadratura", "Completitud del período", "Anomalías"])

    with t_hall:
        st.dataframe(
            res.resumen_archivos.style.map(lambda v: COLOR_ESTADO.get(v, ""), subset=["estado"]),
            width="stretch", hide_index=True)
        if h.empty:
            st.write("Sin hallazgos.")
        else:
            st.dataframe(
                h.style.map(lambda v: COLOR_NIVEL.get(v, ""), subset=["nivel"]),
                width="stretch", hide_index=True, height=min(60 + 35 * len(h), 480))

    with t_cuad:
        cuad = res.cuadratura
        if cuad.empty:
            st.info("No hay información para validar cuadratura.")
        else:
            st.caption("Subtotales calculados con el plan corporativo frente a los totales de control reportados "
                       "por cada ERP. Tolerancia: 1 unidad monetaria.")
            st.dataframe(
                cuad.style.format({c: "{:,.0f}" for c in cuad.columns if c not in ("empresa", "erp")})
                .map(lambda v: "color:#C0392B" if isinstance(v, (int, float)) and v < 0 else "",
                     subset=["dif_ub", "dif_un"]),
                width="stretch", hide_index=True)

    with t_matriz:
        matriz = res.matriz
        st.caption("Estado de las 16 empresas para el período: cargadas en la base, presentes en este lote o pendientes.")
        cuenta = matriz["estado"].value_counts()
        st.write(" · ".join(f"**{k}**: {v}" for k, v in cuenta.items()))
        st.dataframe(matriz.style.map(lambda v: COLOR_ESTADO.get(v, ""), subset=["estado"]),
                     width="stretch", hide_index=True, height=min(60 + 35 * len(matriz), 620))

    with t_anom:
        if res.anomalias.empty:
            st.info("Sin anomalías frente al mes anterior ni al promedio móvil de 12 meses.")
        else:
            st.dataframe(
                res.anomalias.assign(variacion=res.anomalias["variacion"].map("{:+.1%}".format))
                [["empresa", "erp", "cuenta_corp", "comparacion", "referencia", "monto", "variacion"]],
                width="stretch", hide_index=True)


def _confirmacion(res: etl.ResultadoLote, sobrescribir: bool) -> None:
    st.subheader("3 · Confirmación")
    if not res.duplicados.empty and not sobrescribir:
        st.warning(f"Ya existe información para {len(res.duplicados)} empresa(s) en este período. Active la "
                   "sobrescritura controlada para reemplazarla.")
        st.dataframe(res.duplicados, hide_index=True)
    puede = res.puede_confirmar(sobrescribir)
    if st.button("Confirmar carga en DuckDB", type="primary", disabled=not puede):
        info = etl.confirmar_carga(res, sobrescribir=sobrescribir)
        st.session_state.pop("lote", None)
        st.success(f"Carga confirmada: {info['filas']:,} filas en fact_pnl · {info['sobrescritos']} "
                   f"empresa/ERP sobrescritos · {info['anomalias']} anomalías registradas.")
        st.balloons()
    elif not puede:
        st.caption("El botón se habilita cuando no existan errores bloqueantes y, si hay cargas previas, "
                   "se haya activado la sobrescritura.")
