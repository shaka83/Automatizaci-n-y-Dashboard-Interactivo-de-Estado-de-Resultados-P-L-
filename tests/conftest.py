"""Aísla las pruebas: base de datos y extractos temporales, nunca los de data/."""
import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="pnl_tests_"))
# Debe fijarse antes de importar catalogos (lee la variable al cargar el módulo).
os.environ["PNL_DB_PATH"] = str(_TMP / "pnl_test.duckdb")

import mock_data  # noqa: E402
import pytest  # noqa: E402


@pytest.fixture(scope="session")
def datos_sinteticos(tmp_path_factory):
    """Genera extractos sintéticos en una carpeta temporal y carga el histórico en la base de prueba."""
    base = tmp_path_factory.mktemp("datos")
    mock_data.EXTRACTOS = base / "extractos"
    mock_data.PRUEBAS = base / "pruebas"
    mock_data.generar_historico()
    carpeta = mock_data.generar_pruebas()
    mock_data.cargar_historico()
    return {"extractos": mock_data.EXTRACTOS, "pruebas": carpeta}
