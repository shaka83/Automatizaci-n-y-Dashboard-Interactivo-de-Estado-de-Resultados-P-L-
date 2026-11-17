"""Mantenimiento de tablas maestras y de homologación desde la interfaz (sin tocar código)."""
from __future__ import annotations

import pandas as pd
import streamlit as st

import database as db
from catalogos import AREAS, ERPS


def _nota_recalculo() -> None:
    st.caption("Los cambios aplican a los lotes que se carguen a partir de ahora. Los datos ya cargados en "
               "`fact_pnl` conservan la cuenta corporativa con la que se registraron.")


def render() -> None:
    st.title("Catálogos y mapeos")
    t_map, t_emp, t_pc = st.tabs(["Homologación de cuentas", "Empresas y áreas", "Plan de cuentas corporativo"])

    with t_map:
        _mapeo()
    with t_emp:
        _empresas()
    with t_pc:
        st.caption("Jerarquía del estado de resultados (solo lectura: se define en `catalogos.py`).")
        st.dataframe(db.catalogo_cuentas(), width="stretch", hide_index=True)


def _mapeo() -> None:
    st.markdown("Traduce cada cuenta de cada ERP a una cuenta del plan corporativo. "
                "`signo_erp = -1` indica que el ERP exporta ese concepto en negativo.")
    lote = st.session_state.get("lote")
    if lote is not None and not lote.hallazgos.empty:
        huerfanas = lote.hallazgos[lote.hallazgos["regla"] == "CUENTA_HUERFANA"]
        if not huerfanas.empty:
            st.warning("Cuentas sin mapear en el último lote validado. Agréguelas abajo y vuelva a validar:")
            st.dataframe(huerfanas[["erp", "cuenta_erp", "archivo", "mensaje"]], hide_index=True, width="stretch")

    cuentas = db.catalogo_cuentas()
    editado = st.data_editor(
        db.leer_tabla("mapeo_cuentas"),
        num_rows="dynamic", width="stretch", hide_index=True, key="ed_mapeo",
        column_config={
            "erp": st.column_config.SelectboxColumn("ERP", options=list(ERPS), required=True),
            "cuenta_erp": st.column_config.TextColumn("Cuenta en el ERP", required=True),
            "cuenta_corp": st.column_config.SelectboxColumn(
                "Cuenta corporativa", options=cuentas["cuenta_corp"].tolist(), required=True),
            "signo_erp": st.column_config.SelectboxColumn("Signo ERP", options=[1, -1], required=True),
            "descripcion_erp": st.column_config.TextColumn("Descripción"),
        },
    )
    if st.button("Guardar homologación", type="primary", key="btn_mapeo"):
        errores = _validar_mapeo(editado)
        if errores:
            for e in errores:
                st.error(e)
        else:
            limpio = editado.dropna(subset=["erp", "cuenta_erp", "cuenta_corp", "signo_erp"]).assign(
                cuenta_erp=lambda d: d["cuenta_erp"].str.strip().str.upper(),
                signo_erp=lambda d: d["signo_erp"].astype(int))
            db.guardar_tabla("mapeo_cuentas", limpio)
            st.success(f"Homologación guardada: {len(limpio)} cuentas.")
    _nota_recalculo()


def _validar_mapeo(df: pd.DataFrame) -> list[str]:
    errores = []
    cuentas = set(db.catalogo_cuentas()["cuenta_corp"])
    d = df.dropna(subset=["erp", "cuenta_erp", "cuenta_corp"])
    if d.empty:
        errores.append("La homologación no puede quedar vacía.")
    dup = d.assign(k=d["erp"] + "|" + d["cuenta_erp"].astype(str).str.strip().str.upper())
    repetidos = dup[dup.duplicated("k", keep=False)]["k"].unique()
    if len(repetidos):
        errores.append(f"Cuentas ERP duplicadas: {', '.join(repetidos)}")
    desconocidas = sorted(set(d["cuenta_corp"]) - cuentas)
    if desconocidas:
        errores.append(f"Cuentas corporativas inexistentes: {', '.join(desconocidas)}")
    return errores


def _empresas() -> None:
    st.markdown("Catálogo maestro de empresas y su área de negocio. Una empresa que no esté aquí "
                "bloquea cualquier lote que la contenga.")
    editado = st.data_editor(
        db.leer_tabla("dim_empresas"),
        num_rows="dynamic", width="stretch", hide_index=True, key="ed_empresas",
        column_config={
            "empresa_codigo": st.column_config.TextColumn("Código", required=True),
            "empresa_nombre": st.column_config.TextColumn("Nombre", required=True),
            "area_codigo": st.column_config.SelectboxColumn("Área", options=list(AREAS), required=True),
            "erp_origen": st.column_config.SelectboxColumn("ERP de origen", options=list(ERPS), required=True),
            "activa": st.column_config.CheckboxColumn("Activa"),
        },
    )
    if st.button("Guardar empresas", type="primary", key="btn_empresas"):
        d = editado.dropna(subset=["empresa_codigo", "empresa_nombre", "area_codigo", "erp_origen"])
        codigos = d["empresa_codigo"].astype(str).str.strip().str.upper()
        if codigos.duplicated().any():
            st.error("Hay códigos de empresa duplicados.")
        elif not set(d["area_codigo"]) <= set(AREAS):
            st.error("Hay áreas que no existen en el catálogo.")
        else:
            limpio = d.assign(empresa_codigo=codigos, activa=d["activa"].fillna(True).astype(bool))
            db.guardar_tabla("dim_empresas", limpio)
            st.success(f"Catálogo de empresas guardado: {len(limpio)} empresas.")
    _nota_recalculo()
