"""Dashboard ejecutivo: filtros globales, KPIs, estado de resultados y gráficos."""
from __future__ import annotations

from dataclasses import replace

import pandas as pd
import streamlit as st

import analisis as an
import database as db
import graficos as gr
from catalogos import AREAS, ERPS


def _var_txt(actual: float | None, previo: float | None) -> str | None:
    v = an.variacion(actual, previo)
    return None if v is None else f"{v:+.1%} A/A"


def _pp_txt(actual: float | None, previo: float | None) -> str | None:
    if actual is None or previo is None:
        return None
    return f"{(actual - previo) * 100:+.1f} pp A/A"


def _filtros_sidebar() -> tuple[int, int, str, db.Filtros]:
    anios = db.anios_disponibles()
    ultimo = db.ultimo_periodo()
    empresas = db.catalogo_empresas()
    nombres = dict(zip(empresas["empresa_codigo"], empresas["empresa_nombre"]))

    with st.sidebar:
        st.header("Filtros globales")
        anio = st.selectbox("Año", anios, index=len(anios) - 1, key="flt_anio")
        modo = st.radio("Vista temporal", ["Mensual", "Acumulado YTD"], horizontal=True, key="flt_modo")
        mes = st.selectbox("Mes", list(range(1, 13)), index=ultimo.month - 1 if ultimo else 0,
                           format_func=lambda m: an.MESES[m - 1], key="flt_mes")
        areas = st.multiselect("Área de negocio", list(AREAS), format_func=AREAS.get,
                               placeholder="Todas las áreas", key="flt_areas")
        emps = st.multiselect("Empresa", list(nombres), format_func=lambda c: f"{c} · {nombres[c]}",
                              placeholder="Las 16 empresas", key="flt_emps")
        erps = st.multiselect("ERP origen", list(ERPS), format_func=lambda k: ERPS[k]["nombre"],
                              placeholder="Todos los ERP", key="flt_erps")
        st.caption("Sin selección = todas. Las variaciones A/A comparan con el mismo rango del año anterior.")

    base = an.rango_periodos(anio, mes, modo)
    filtros = replace(base, areas=tuple(areas), empresas=tuple(emps), erps=tuple(erps))
    return anio, mes, modo, filtros


def _estado(f: db.Filtros, cuentas: pd.DataFrame, dimension: str = "total") -> pd.DataFrame | None:
    matriz = an.pivot(db.pnl_agregado(f, dimension))
    return None if matriz.empty else an.construir_estado(matriz, cuentas)


def _estilo(tabla: pd.DataFrame, numerico: pd.DataFrame, tipos: pd.Series) -> object:
    """Negritas para subtotales y rojo para valores negativos."""
    def css(_: pd.DataFrame) -> pd.DataFrame:
        out = pd.DataFrame("", index=tabla.index, columns=tabla.columns)
        for etiqueta, tipo in tipos.items():
            if tipo != "detalle":
                out.loc[etiqueta] = "font-weight:600;background-color:#EEF3F8"
        for etiqueta in numerico.index:
            for col in numerico.columns:
                valor = numerico.at[etiqueta, col]
                if pd.notna(valor) and valor < 0:
                    previo = out.at[etiqueta, col]
                    out.at[etiqueta, col] = (previo + ";" if previo else "") + "color:#C0392B"
        return out
    return tabla.style.apply(css, axis=None)


def _tabla_estado(f: db.Filtros, previo: pd.DataFrame | None, actual: pd.DataFrame, cuentas: pd.DataFrame) -> None:
    dimension = st.radio("Desglosar por", ["Total", "Área de negocio", "Empresa"], horizontal=True,
                         key="dim_estado")
    clave = {"Total": "total", "Área de negocio": "area", "Empresa": "empresa"}[dimension]
    estado = actual if clave == "total" else _estado(f, cuentas, clave)
    if estado is None:
        st.info("Sin datos para desglosar.")
        return

    estado = estado.copy()
    if clave != "total":
        estado["Total"] = actual["Total"]

    grupos = [c for c in estado.columns if c not in ("tipo", "Total")]
    estado = an.agregar_margenes(estado)
    columnas_valor = grupos + ["Total"]

    var = None
    if previo is not None:
        prev_total = previo["Total"].reindex(estado.index)
        var = pd.Series([an.variacion(a, p) if t != "margen" else None
                         for a, p, t in zip(estado["Total"], prev_total, estado["tipo"])], index=estado.index)

    numerico = estado[columnas_valor].astype(float)
    if var is not None:
        numerico["Var. A/A"] = var.astype(float)

    display = pd.DataFrame(index=estado.index)
    for col in columnas_valor:
        display[col] = [an.formato_pct(v) if t == "margen" else an.formato_moneda(v)
                        for v, t in zip(numerico[col], estado["tipo"])]
    if var is not None:
        display["Var. A/A"] = [an.formato_pct(v) for v in var]

    st.caption("Montos con signo P&L: ingresos positivos, costos y gastos entre paréntesis.")
    estilo = _estilo(display, numerico, estado["tipo"])
    st.dataframe(estilo, width="stretch", height=min(60 + 35 * len(display), 760))


def _tab_graficos(f: db.Filtros, actual: pd.DataFrame, cuentas: pd.DataFrame, mes: int) -> None:
    c1, c2 = st.columns(2)
    with c1:
        etiquetas, medidas, valores = an.serie_waterfall(actual["Total"])
        st.plotly_chart(gr.cascada(etiquetas, medidas, valores), width="stretch")

    with c2:
        hist = replace(f.horizonte(), mes_max=mes)
        anual = _estado(hist, cuentas, "anio")
        if anual is not None:
            serie = an.evolucion_anual(anual)
            st.plotly_chart(gr.evolucion(serie, f"Evolución anual de ingresos (enero a {an.MESES[mes - 1]})"),
                            width="stretch")

    por_area = _estado(f, cuentas, "area")
    if por_area is None:
        return
    ing = por_area.loc[an.ING].drop("tipo").astype(float)
    ebitda = por_area.loc[an.EBITDA].drop("tipo").astype(float)
    neta = por_area.loc[an.UN].drop("tipo").astype(float)
    tabla = pd.DataFrame({
        "Ingresos netos": ing,
        "EBITDA": ebitda,
        "Utilidad neta": neta,
        "Margen EBITDA %": ebitda / ing.where(ing != 0),
        "Margen neto %": neta / ing.where(ing != 0),
        "Participación ingresos %": ing / ing.sum(),
    })
    st.subheader("Rentabilidad y participación por área de negocio")
    c3, c4 = st.columns([3, 2])
    with c3:
        st.plotly_chart(gr.areas_margen(tabla[["Margen EBITDA %"]]), width="stretch")
    with c4:
        st.plotly_chart(gr.areas_participacion(tabla[["Ingresos netos"]]), width="stretch")
    st.dataframe(
        tabla.style.format({"Ingresos netos": "{:,.0f}", "EBITDA": "{:,.0f}", "Utilidad neta": "{:,.0f}",
                            "Margen EBITDA %": "{:.1%}", "Margen neto %": "{:.1%}",
                            "Participación ingresos %": "{:.1%}"}),
        width="stretch")


def render() -> None:
    st.title("Estado de Resultados · Dashboard ejecutivo")
    if not db.anios_disponibles():
        st.info("Aún no hay datos. Ejecute `python mock_data.py --cargar` o cargue extractos en "
                "**Carga y validación**.")
        return

    anio, mes, modo, f = _filtros_sidebar()
    cuentas = db.catalogo_cuentas()

    actual = _estado(f, cuentas)
    previo = _estado(f.desplazar_anios(-1), cuentas)
    if actual is None:
        st.warning("No hay información para los filtros seleccionados.")
        return

    ka = an.resumen(actual["Total"])
    kp = an.resumen(previo["Total"]) if previo is not None else {}
    periodo = f"{an.MESES[mes - 1]} {anio}" if modo == "Mensual" else f"Enero a {an.MESES[mes - 1]} {anio}"
    st.caption(f"Período: **{periodo}** · vista {modo.lower()} · variaciones frente al mismo rango de {anio - 1}")

    c = st.columns(6)
    c[0].metric("Ingresos netos", an.formato_millones(ka["ingresos"]),
                _var_txt(ka["ingresos"], kp.get("ingresos")))
    c[1].metric("EBITDA", an.formato_millones(ka["ebitda"]), _var_txt(ka["ebitda"], kp.get("ebitda")))
    c[2].metric("Utilidad neta", an.formato_millones(ka["utilidad_neta"]),
                _var_txt(ka["utilidad_neta"], kp.get("utilidad_neta")))
    c[3].metric("Margen bruto", an.formato_pct(ka["margen_bruto"]),
                _pp_txt(ka["margen_bruto"], kp.get("margen_bruto")))
    c[4].metric("Margen EBITDA", an.formato_pct(ka["margen_ebitda"]),
                _pp_txt(ka["margen_ebitda"], kp.get("margen_ebitda")))
    c[5].metric("Margen neto", an.formato_pct(ka["margen_neto"]),
                _pp_txt(ka["margen_neto"], kp.get("margen_neto")))

    tab_estado, tab_graficos = st.tabs(["Estado de resultados", "Gráficos ejecutivos"])
    with tab_estado:
        _tabla_estado(f, previo, actual, cuentas)
    with tab_graficos:
        _tab_graficos(f, actual, cuentas, mes)
