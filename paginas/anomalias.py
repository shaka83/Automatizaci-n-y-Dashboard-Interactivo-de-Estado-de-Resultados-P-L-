"""Explorador de distorsiones y anomalías con drill-down hasta el ERP y archivo de origen."""
from __future__ import annotations

import pandas as pd
import streamlit as st

import database as db
import graficos as gr
from catalogos import ERPS


def render() -> None:
    st.title("Explorador de distorsiones y anomalías")
    st.caption("Alertas generadas al cargar cada lote: variaciones superiores a ±15 % frente al mes anterior "
               "o al promedio móvil de 12 meses.")

    todas = db.anomalias()
    if todas.empty:
        st.success("No hay anomalías registradas en la base de datos.")
        _log_validacion()
        return

    with st.sidebar:
        st.header("Filtros de anomalías")
        periodos = sorted(pd.to_datetime(todas["periodo"]).dt.date.unique())
        rango = st.select_slider("Período", options=periodos, value=(periodos[0], periodos[-1]),
                                 format_func=lambda d: d.strftime("%Y-%m"), key="an_rango")
        erps = st.multiselect("ERP origen", list(ERPS), format_func=lambda k: ERPS[k]["nombre"],
                              placeholder="Todos", key="an_erps")
        empresas = st.multiselect("Empresa", sorted(todas["empresa_codigo"].unique()),
                                  placeholder="Todas", key="an_emps")
        minimo = st.slider("Variación absoluta mínima (%)", 0, 100, 15, step=5, key="an_min")

    df = todas.copy()
    df["periodo"] = pd.to_datetime(df["periodo"]).dt.date
    df = df[(df["periodo"] >= rango[0]) & (df["periodo"] <= rango[1])]
    if erps:
        df = df[df["erp"].isin(erps)]
    if empresas:
        df = df[df["empresa_codigo"].isin(empresas)]
    df = df[df["variacion_pct"].abs() * 100 >= minimo]

    if df.empty:
        st.info("Ninguna anomalía cumple los filtros seleccionados.")
        _log_validacion()
        return

    c = st.columns(4)
    c[0].metric("Anomalías", f"{len(df):,}")
    c[1].metric("Empresas afectadas", f"{df['empresa_codigo'].nunique()}")
    c[2].metric("Cuentas afectadas", f"{df['cuenta_corp'].nunique()}")
    c[3].metric("Mayor variación", f"{df['variacion_pct'].abs().max():.1%}")

    st.plotly_chart(gr.dispersion_anomalias(df.assign(comparacion=df["mensaje"].str.extract(
        r":\s(Mes anterior|Promedio móvil 12M)")[0].fillna("Otra"))), width="stretch")

    nombres_cuenta = db.catalogo_cuentas().set_index("cuenta_corp")["cuenta_nombre"]
    tabla = df.assign(
        periodo=pd.to_datetime(df["periodo"]).dt.strftime("%Y-%m"),
        cuenta=df["cuenta_corp"].map(nombres_cuenta),
    )[["periodo", "erp", "empresa_codigo", "cuenta", "valor_esperado", "valor_observado",
       "variacion_pct", "archivo", "mensaje"]]
    st.dataframe(
        tabla.style.format({"valor_esperado": "{:,.0f}", "valor_observado": "{:,.0f}",
                            "variacion_pct": "{:+.1%}"}),
        width="stretch", height=340, hide_index=True,
    )

    st.subheader("Drill-down: origen de una distorsión")
    opciones = {
        f"{r.periodo:%Y-%m} · {r.empresa_codigo} · {r.cuenta_corp} · {r.variacion_pct:+.1%}": i
        for i, r in enumerate(df.itertuples(index=False))
    }
    seleccion = st.selectbox("Anomalía a investigar", list(opciones), key="an_sel")
    fila = df.iloc[opciones[seleccion]]
    _drill_down(fila)


def _drill_down(fila: pd.Series) -> None:
    empresa = fila["empresa_codigo"]
    cuenta = fila["cuenta_corp"]
    periodo = pd.Timestamp(fila["periodo"])

    catalogo = db.catalogo_empresas()
    area = catalogo.loc[catalogo["empresa_codigo"] == empresa, "area_nombre"].iloc[0]
    nombre_cuenta = db.catalogo_cuentas().set_index("cuenta_corp").loc[cuenta, "cuenta_nombre"]

    c = st.columns(4)
    c[0].metric("Empresa", empresa)
    c[1].metric("Área", area)
    c[2].metric("ERP origen", ERPS[fila["erp"]]["nombre"])
    c[3].metric("Archivo", fila["archivo"] or "—")
    st.write(f"**Cuenta:** {cuenta} · {nombre_cuenta}  \n**Alerta:** {fila['mensaje']}")

    serie = db.serie_cuenta(empresa, cuenta, meses=24)
    if serie.empty:
        st.info("Sin serie histórica para esta cuenta.")
        return
    st.plotly_chart(gr.serie_mensual(serie, f"{empresa} · {nombre_cuenta} (últimos 24 meses)"),
                    width="stretch")
    st.caption("Serie mensual con ERP y archivo de origen de cada saldo (la fila resaltada es el mes de la alerta):")
    tabla = serie.assign(periodo=pd.to_datetime(serie["periodo"]).dt.strftime("%Y-%m"))
    resaltar = periodo.strftime("%Y-%m")
    st.dataframe(
        tabla.style.apply(lambda r: ["background-color:#FDEBD0" if r["periodo"] == resaltar else ""] * len(r),
                          axis=1).format({"monto": "{:,.0f}"}),
        width="stretch", hide_index=True, height=280)


def _log_validacion() -> None:
    with st.expander("Registro de validaciones y cargas", expanded=False):
        log = db.log_eventos(limite=200)
        if log.empty:
            st.write("Sin eventos registrados.")
        else:
            st.dataframe(log, width="stretch", hide_index=True)
