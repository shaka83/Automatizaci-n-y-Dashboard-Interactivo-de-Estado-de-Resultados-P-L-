"""ETL: lectura de extractos de los 3 ERP, homologación y carga transaccional.

Flujo:
    procesar_lote()   -> lee, normaliza, mapea y valida (no escribe en la base)
    confirmar_carga() -> inserta en fact_pnl y registra auditoría en una transacción
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

import database
import validators
from catalogos import ERPS
from validators import hallazgo

COLUMNAS_STAGED = ["periodo", "empresa", "erp", "cuenta_erp", "importe", "signo_erp",
                   "cuenta_corp", "monto", "archivo", "fila"]


@dataclass
class ResultadoLote:
    """Resultado de validar un lote. Es lo que muestra la pantalla antes de confirmar."""
    periodo: date
    staged: pd.DataFrame
    hallazgos: pd.DataFrame
    cuadratura: pd.DataFrame
    matriz: pd.DataFrame
    anomalias: pd.DataFrame
    duplicados: pd.DataFrame
    resumen_archivos: pd.DataFrame
    filas_leidas: int = 0
    extra: dict = field(default_factory=dict)

    @property
    def bloqueantes(self) -> int:
        return int((self.hallazgos["nivel"] == "ERROR").sum()) if not self.hallazgos.empty else 0

    def puede_confirmar(self, sobrescribir: bool) -> bool:
        return self.bloqueantes == 0 and (self.duplicados.empty or sobrescribir)


def leer_archivo(nombre: str, contenido: bytes, erp: str, periodo: date) -> tuple[pd.DataFrame, list[dict]]:
    """Lee un extracto de un ERP y lo lleva al formato estándar.

    Devuelve (filas válidas normalizadas, hallazgos de lectura). Las filas con período
    distinto al seleccionado, período inválido o importe no numérico se descartan y se reportan.
    """
    cfg = ERPS[erp]
    col = cfg["columnas"]
    buffer = io.BytesIO(contenido)
    tipos = {col["empresa"]: str, col["cuenta"]: str, col["periodo"]: str}

    if cfg["formato"] == "csv":
        raw = pd.read_csv(buffer, sep=cfg["sep"], decimal=cfg["decimal"], dtype=tipos, encoding="utf-8-sig")
    else:
        raw = pd.read_excel(buffer, sheet_name=cfg["hoja"], dtype=tipos)

    faltantes = [c for c in col.values() if c not in raw.columns]
    if faltantes:
        raise ValueError(f"columnas faltantes para {cfg['nombre']}: {', '.join(faltantes)}")
    if raw.empty:
        raise ValueError("el archivo no contiene filas")

    df = pd.DataFrame({
        "periodo_raw": raw[col["periodo"]].astype(str).str.strip(),
        "empresa": raw[col["empresa"]].astype(str).str.strip().str.upper(),
        "cuenta_erp": raw[col["cuenta"]].astype(str).str.strip().str.upper(),
        "importe": pd.to_numeric(raw[col["importe"]], errors="coerce"),
        "fila": range(2, len(raw) + 2),
    })
    periodo_ts = (pd.to_datetime(df["periodo_raw"], format=cfg["formato_periodo"], errors="coerce")
                  .dt.to_period("M").dt.to_timestamp())

    h: list[dict] = []
    invalido = periodo_ts.isna()
    distinto = periodo_ts.notna() & (periodo_ts != pd.Timestamp(periodo))
    no_numerico = df["importe"].isna()

    if invalido.any():
        h.append(hallazgo("ERROR", "PERIODO_INVALIDO",
                          f"{int(invalido.sum())} filas con período no interpretable", archivo=nombre, erp=erp))
    if distinto.any():
        otros = sorted(periodo_ts[distinto].dt.strftime("%Y-%m").unique())
        h.append(hallazgo("ERROR", "PERIODO_DISTINTO",
                          f"{int(distinto.sum())} filas de otro período ({', '.join(otros)}); "
                          f"se seleccionó {periodo:%Y-%m}", archivo=nombre, erp=erp))
    if no_numerico.any():
        h.append(hallazgo("ERROR", "IMPORTE_NO_NUMERICO",
                          f"{int(no_numerico.sum())} filas con importe no numérico", archivo=nombre, erp=erp))

    keep = ~(invalido | distinto | no_numerico)
    df = df[keep].assign(periodo=periodo_ts[keep], archivo=nombre, erp=erp)
    return df.reset_index(drop=True), h


def procesar_lote(periodo: date, archivos: dict[str, list[tuple[str, bytes]]]) -> ResultadoLote:
    """Valida un lote completo. `archivos` = {código_erp: [(nombre, contenido_bytes), ...]}."""
    hallazgos_lectura: list[dict] = []
    partes: list[pd.DataFrame] = []
    resumen: list[dict] = []

    for erp, lista in archivos.items():
        for nombre, contenido in lista:
            try:
                df, h = leer_archivo(nombre, contenido, erp, periodo)
            except Exception as exc:  # archivo ilegible: se reporta y el lote queda bloqueado
                hallazgos_lectura.append(hallazgo("ERROR", "ARCHIVO_ILEGIBLE",
                                                  f"No se pudo leer el archivo: {exc}", archivo=nombre, erp=erp))
                resumen.append({"archivo": nombre, "erp": erp, "filas": 0, "estado": "ERROR"})
                continue
            hallazgos_lectura.extend(h)
            partes.append(df)
            estado = "ERROR" if any(x["nivel"] == "ERROR" for x in h) else "OK"
            resumen.append({"archivo": nombre, "erp": erp, "filas": len(df), "estado": estado})

    raw = pd.concat(partes, ignore_index=True) if partes else pd.DataFrame(
        columns=["periodo", "empresa", "erp", "cuenta_erp", "importe", "archivo", "fila"])

    empresas = database.catalogo_empresas()
    cuentas = database.catalogo_cuentas()
    mapeo = database.mapeo_cuentas()

    staged = raw.merge(mapeo[["erp", "cuenta_erp", "cuenta_corp", "signo_erp"]],
                       on=["erp", "cuenta_erp"], how="left")
    staged["monto"] = staged["importe"] * staged["signo_erp"]
    staged = staged.merge(cuentas[["cuenta_corp", "rubro_nivel1", "signo_pnl", "es_control"]],
                          on="cuenta_corp", how="left")
    staged["es_control"] = staged["es_control"].fillna(False).astype(bool)

    ventana_ini = (pd.Timestamp(periodo) - pd.DateOffset(months=12)).date()
    resultado = validators.validar(
        staged=staged,
        periodo=periodo,
        empresas=empresas,
        cuentas=cuentas,
        existentes=database.existentes(periodo),
        historial=database.historial(ventana_ini, periodo),
        hallazgos_previos=hallazgos_lectura,
    )

    return ResultadoLote(
        periodo=periodo,
        staged=staged,
        hallazgos=resultado["hallazgos"],
        cuadratura=resultado["cuadratura"],
        matriz=resultado["matriz"],
        anomalias=resultado["anomalias"],
        duplicados=resultado["duplicados"],
        resumen_archivos=pd.DataFrame(resumen, columns=["archivo", "erp", "filas", "estado"]),
        filas_leidas=int(sum(len(p) for p in partes)),
    )


def confirmar_carga(res: ResultadoLote, sobrescribir: bool = False) -> dict:
    """Inserta el lote en fact_pnl y registra la auditoría en una sola transacción."""
    if res.bloqueantes:
        raise ValueError(f"El lote tiene {res.bloqueantes} errores bloqueantes")
    if not res.duplicados.empty and not sobrescribir:
        raise ValueError("Existe información previa para este período: active la sobrescritura controlada")

    st = res.staged
    fact = st[st["cuenta_corp"].notna() & ~st["es_control"] & st["monto"].notna()]
    fact_df = pd.DataFrame({
        "periodo": fact["periodo"], "empresa_codigo": fact["empresa"], "erp": fact["erp"],
        "cuenta_corp": fact["cuenta_corp"], "monto": fact["monto"].astype(float), "archivo": fact["archivo"],
    })
    dups = res.duplicados.rename(columns={"empresa": "empresa_codigo"})[["empresa_codigo", "erp"]]
    if not sobrescribir:
        dups = dups.iloc[0:0]

    periodo_ts = pd.Timestamp(res.periodo)
    registros = []
    for _, r in res.hallazgos.iterrows():
        registros.append({"tipo": "VALIDACION", "nivel": r["nivel"], "periodo": periodo_ts, "erp": r["erp"],
                          "empresa_codigo": r["empresa"], "cuenta_corp": r["cuenta_corp"], "archivo": r["archivo"],
                          "mensaje": r["mensaje"], "esperado": r["esperado"], "observado": r["observado"],
                          "variacion": None})
    for _, a in res.anomalias.iterrows():
        registros.append({"tipo": "ANOMALIA", "nivel": "ALERTA", "periodo": periodo_ts, "erp": a["erp"],
                          "empresa_codigo": a["empresa"], "cuenta_corp": a["cuenta_corp"], "archivo": a["archivo"],
                          "mensaje": a["mensaje"], "esperado": a["referencia"], "observado": a["monto"],
                          "variacion": a["variacion"]})
    registros.append({"tipo": "CARGA", "nivel": "INFO", "periodo": periodo_ts, "erp": None,
                      "empresa_codigo": None, "cuenta_corp": None, "archivo": None,
                      "mensaje": f"Carga de {len(fact_df)} filas; sobrescritos {len(dups)} pares",
                      "esperado": None, "observado": float(len(fact_df)), "variacion": None})
    log_df = pd.DataFrame(registros)
    for c in ["esperado", "observado", "variacion"]:
        log_df[c] = pd.to_numeric(log_df[c], errors="coerce")

    with database.sesion() as con:
        con.register("stg_fact", fact_df)
        con.register("stg_dups", dups)
        con.register("stg_log", log_df)
        con.execute("BEGIN TRANSACTION")
        try:
            if not dups.empty:
                con.execute("""DELETE FROM fact_pnl USING stg_dups
                               WHERE fact_pnl.periodo = ?
                                 AND fact_pnl.empresa_codigo = stg_dups.empresa_codigo
                                 AND fact_pnl.erp = stg_dups.erp""", [res.periodo])
            con.execute("""INSERT INTO fact_pnl (periodo, empresa_codigo, erp, cuenta_corp, monto, archivo, fecha_carga)
                           SELECT CAST(periodo AS DATE), empresa_codigo, erp, cuenta_corp, monto, archivo,
                                  current_timestamp FROM stg_fact""")
            con.execute("""INSERT INTO log_auditoria (fecha, tipo, nivel, periodo, erp, empresa_codigo, cuenta_corp,
                                                     archivo, mensaje, valor_esperado, valor_observado, variacion_pct)
                           SELECT current_timestamp, tipo, nivel, CAST(periodo AS DATE), erp, empresa_codigo,
                                  cuenta_corp, archivo, mensaje, esperado, observado, variacion FROM stg_log""")
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        finally:
            for nombre in ("stg_fact", "stg_dups", "stg_log"):
                con.unregister(nombre)

    return {"filas": len(fact_df), "sobrescritos": len(dups), "anomalias": len(res.anomalias)}
