# Automatización y Dashboard Interactivo de Estado de Resultados (P&L)

MVP en Python que reemplaza el consolidado manual del P&L en Excel. Integra extractos de **3 ERP** para
**16 empresas** agrupadas en **7 áreas de negocio**, valida cada lote antes de almacenarlo y publica un
dashboard ejecutivo sobre **5 años** de histórico.

| Capa | Tecnología |
|---|---|
| Interfaz y dashboard | Streamlit (`st.navigation`, `st.session_state`, layout `wide`) |
| Almacenamiento y analítica | DuckDB (`data/pnl.duckdb`, esquema en estrella) |
| ETL | Pandas |
| Visualización | Plotly (cascada, combinadas, dispersión, drill-down) |
| Validación | Reglas propias en Python (`validators.py`) |

## Puesta en marcha

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python mock_data.py --cargar      # genera 5 años de extractos sintéticos y carga la base
streamlit run app.py
```

`mock_data.py` escribe los extractos en `data/extractos/` (174 archivos, 58 meses × 3 ERP) y un lote de
prueba en `data/pruebas/202611/`. `--cargar` reinicia `data/pnl.duckdb` y carga el histórico por el mismo
pipeline que usa la interfaz.

### Probar el flujo de carga y validación

1. Abra **Carga y validación**; el período ya viene en `2026-11`.
2. Suba `data/pruebas/202611/alfa_202611.csv` en *ERP Alfa*, `beta_202611.csv` en *ERP Beta* y
   `gamma_202611.xlsx` en *ERP Gamma*, y pulse **Validar lote**.
3. El reporte debe mostrar 5 errores bloqueantes deliberados:

| # | Caso plantado | Regla | Cómo resolverlo |
|---|---|---|---|
| 1 | Cuenta ERP `600777` en Beta, sin mapeo | `CUENTA_HUERFANA` | Agregar el mapeo en **Catálogos y mapeos** y revalidar |
| 2 | Empresa `EMP-017` en Gamma, fuera del catálogo | `EMPRESA_HUERFANA` | Quitarla del extracto (no existe en el maestro) |
| 3 | Total de control de utilidad bruta de EMP-04 en Alfa, +250.000 | `CUADRATURA` | Corregir el extracto del ERP |
| 4 | Ingresos financieros de EMP-08 en negativo en Gamma | `SIGNO_INCONSISTENTE` | Corregir el signo en el extracto |
| 5 | Ventas de EMP-09 +60 % frente al mes anterior | `ANOMALIA` (alerta, no bloquea) | Se revisa en el explorador de anomalías |

Los casos 3 y 4 también provocan un descuadre de utilidad neta en EMP-08: es el efecto esperado, porque el
total de control del ERP no cambió.

El dashboard ya muestra 58 meses tras `python mock_data.py --cargar`. El lote de prueba solo se puede confirmar
cuando no tiene errores bloqueantes, así que no lo cargue hasta corregir los casos 1 a 4.

## Arquitectura

```
app.py                  Entrada: navegación entre páginas e inicialización de la base
catalogos.py            Fuente única de verdad: empresas, áreas, ERP, plan de cuentas, mapeo inicial
database.py             DuckDB: DDL, vistas, consultas analíticas, escritura de maestros
etl.py                  Lectura de los 3 formatos de ERP, homologación y carga transaccional
validators.py           Motor de validación y auditoría (funciones puras sobre DataFrames)
analisis.py             Estado de resultados: subtotales, márgenes, KPIs, variaciones A/A
graficos.py             Figuras Plotly
mock_data.py            Generador de datos sintéticos con casos de error plantados
paginas/dashboard.py    Filtros globales, KPIs, estado de resultados y gráficos ejecutivos
paginas/anomalias.py    Explorador de distorsiones con drill-down a ERP y archivo de origen
paginas/carga.py        Carga por ERP, reporte de validación, matriz de completitud, confirmación
paginas/catalogos.py    Edición de homologación de cuentas y catálogo de empresas
tests/                  Pruebas de punta a punta sobre una base temporal
```

### Modelo de datos (esquema en estrella)

- **`fact_pnl`** — saldo mensual por `periodo`, `empresa_codigo`, `erp` y `cuenta_corp`, con `archivo` de origen.
- **`dim_empresas`** — 16 empresas, `area_codigo`, `erp_origen`, `activa`.
- **`dim_areas`** — 7 áreas de negocio.
- **`dim_cuentas`** — jerarquía: `rubro_nivel1`, `subrubro_nivel2`, `orden`, `signo_pnl`, `es_control`.
- **`mapeo_cuentas`** — `(erp, cuenta_erp) → cuenta_corp` con `signo_erp`. Editable desde la interfaz.
- **`log_auditoria`** — cargas, validaciones con error o advertencia, y anomalías.
- **`v_pnl`** — vista desnormalizada que usa el dashboard.

Los montos se guardan en **magnitud natural** y el P&L aplica el signo (`signo_pnl`). Cada ERP exporta con
convenciones distintas (p. ej. ERP Beta exporta costos en negativo); el ETL lo normaliza con `signo_erp`.

### Reglas de validación

| Regla | Nivel | Bloquea |
|---|---|---|
| Cuenta ERP sin mapeo, empresa fuera del maestro | ERROR | Sí |
| Cuenta duplicada en el extracto, signo inconsistente | ERROR | Sí |
| Cuadratura: utilidad bruta y neta calculadas vs. totales de control del ERP (tolerancia 1) | ERROR | Sí |
| Período distinto al seleccionado, importe no numérico, archivo ilegible | ERROR | Sí |
| Carga existente para empresa/ERP/período | ADVERTENCIA | Sí, salvo sobrescritura explícita |
| ERP distinto al de origen, empresa sin datos en el lote, sin total de control | ADVERTENCIA | No |
| Variación > ±15 % vs. mes anterior o promedio móvil de 12 meses | ALERTA | No |

## Decisiones y supuestos

- **Lote = ERP + período.** La interfaz tiene un cargador por ERP con un período común; cada archivo puede
  contener varias empresas.
- **Validación antes de escribir.** `procesar_lote` no toca la base; `confirmar_carga` inserta y audita en
  una única transacción.
- **Cambios de homologación no reexpresan el histórico.** `fact_pnl` guarda la cuenta corporativa vigente
  al cargar. Reexpresar requeriría recargar los meses afectados.
- **Anomalías sobre el mes cargado.** La detección compara el lote con la base ya cargada; no re-evalúa meses
  anteriores.
- **Concurrencia.** DuckDB admite un solo proceso escritor. Cierre la app antes de ejecutar
  `mock_data.py --cargar` desde la terminal.
- **Datos sintéticos.** Empresas, cifras y ERP son ficticios; no representan a empresas reales.

## Pruebas

```bash
pytest -q
```

Las pruebas generan datos en una carpeta temporal y una base aislada (`PNL_DB_PATH`), y verifican:
histórico limpio sin errores ni anomalías, conciliación de la utilidad neta con el total de control de los
ERP, detección de los errores plantados y sobrescritura controlada sin duplicar filas.
