"""Punto de entrada: Automatización y Dashboard de Estado de Resultados (P&L).

Ejecutar:  streamlit run app.py
"""
import streamlit as st

from database import inicializar
from paginas import anomalias, carga, catalogos, dashboard

st.set_page_config(page_title="P&L · Dashboard ejecutivo", page_icon="📊", layout="wide")


@st.cache_resource
def _inicializar_base() -> bool:
    inicializar()
    return True


_inicializar_base()

paginas = [
    st.Page(dashboard.render, title="Dashboard ejecutivo", icon="📊", default=True),
    st.Page(anomalias.render, title="Explorador de anomalías", icon="🔎", url_path="anomalias"),
    st.Page(carga.render, title="Carga y validación", icon="📥", url_path="carga"),
    st.Page(catalogos.render, title="Catálogos y mapeos", icon="🗂️", url_path="catalogos"),
]
st.navigation(paginas).run()
