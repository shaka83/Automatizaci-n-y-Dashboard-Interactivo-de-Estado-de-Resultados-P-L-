"""Figuras Plotly del dashboard. Sólo construyen figuras; no consultan datos."""
from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots

AZUL = "#1F4E79"
AZUL_CLARO = "#5B9BD5"
VERDE = "#2E7D32"
ROJO = "#C0392B"
GRIS = "#7F8C8D"
PALETA = ["#1F4E79", "#5B9BD5", "#2E7D32", "#F4A300", "#8E44AD", "#16A085", "#C0392B"]


def _layout(fig: go.Figure, alto: int = 420) -> go.Figure:
    fig.update_layout(height=alto, margin=dict(l=10, r=10, t=50, b=10),
                      legend=dict(orientation="h", y=-0.15), template="plotly_white")
    return fig


def cascada(etiquetas: list[str], medidas: list[str], valores: list[float]) -> go.Figure:
    fig = go.Figure(go.Waterfall(
        orientation="v", measure=medidas, x=etiquetas, y=valores,
        text=[f"{v / 1e6:,.2f} M" for v in valores], textposition="outside",
        connector={"line": {"color": GRIS, "width": 1}},
        increasing={"marker": {"color": VERDE}}, decreasing={"marker": {"color": ROJO}},
        totals={"marker": {"color": AZUL}},
    ))
    fig.update_layout(title="Cascada del estado de resultados (Ingresos → Utilidad neta)", showlegend=False)
    fig.update_xaxes(tickangle=-30)
    return _layout(fig, 480)


def evolucion(df: pd.DataFrame, titulo: str) -> go.Figure:
    """Barras de ingresos por año con línea de margen EBITDA % en eje secundario."""
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    anios = df["anio"].astype(str)
    fig.add_bar(x=anios, y=df["ingresos"], name="Ingresos netos", marker_color=AZUL,
                text=[f"{v / 1e6:,.1f} M" for v in df["ingresos"]], textposition="outside")
    fig.add_scatter(x=anios, y=df["margen_ebitda"], name="Margen EBITDA %", mode="lines+markers",
                    line=dict(color=AZUL_CLARO, width=3), secondary_y=True)
    fig.update_yaxes(title_text="Ingresos", secondary_y=False, tickformat=",.0s")
    fig.update_yaxes(title_text="Margen EBITDA", secondary_y=True, tickformat=".1%")
    fig.update_yaxes(range=[0, float(df["margen_ebitda"].max()) * 1.3], secondary_y=True)
    fig.update_layout(title=titulo, barmode="group")
    return _layout(fig)


def areas_margen(df: pd.DataFrame) -> go.Figure:
    """Margen EBITDA % por área (barras horizontales ordenadas)."""
    d = df.sort_values("Margen EBITDA %")
    fig = px.bar(d, x="Margen EBITDA %", y=d.index, orientation="h", text_auto=".1%",
                 color="Margen EBITDA %", color_continuous_scale=["#F5B7B1", "#FDEBD0", "#A9DFBF"])
    fig.update_layout(title="Rentabilidad EBITDA por área", coloraxis_showscale=False, yaxis_title=None)
    fig.update_xaxes(tickformat=".0%")
    return _layout(fig)


def areas_participacion(df: pd.DataFrame) -> go.Figure:
    fig = px.pie(df, names=df.index, values="Ingresos netos", hole=0.5, color_discrete_sequence=PALETA)
    fig.update_traces(textinfo="percent+label", textposition="inside")
    fig.update_layout(title="Participación en ingresos netos")
    return _layout(fig)


def serie_mensual(df: pd.DataFrame, titulo: str) -> go.Figure:
    fig = px.line(df, x="periodo", y="monto", color="erp", markers=True,
                  hover_data={"archivo": True, "monto": ":,.0f"}, title=titulo)
    fig.update_layout(yaxis_title="Monto (magnitud natural)")
    return _layout(fig)


def dispersion_anomalias(df: pd.DataFrame) -> go.Figure:
    d = df.assign(variacion_pct=df["variacion_pct"] * 100)
    fig = px.scatter(d, x="periodo", y="variacion_pct", color="erp", symbol="comparacion",
                     hover_data={"empresa_codigo": True, "cuenta_corp": True, "valor_observado": ":,.0f"},
                     color_discrete_sequence=PALETA, title="Variaciones detectadas frente al umbral de ±15 %")
    fig.add_hline(y=15, line_dash="dash", line_color=ROJO)
    fig.add_hline(y=-15, line_dash="dash", line_color=ROJO)
    fig.update_yaxes(title_text="Variación (%)")
    return _layout(fig)
