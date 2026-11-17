"""Motor de validación y auditoría automática de un lote mensual.

Todas las reglas son funciones puras sobre DataFrames: no escriben en la base.
Un hallazgo con nivel ERROR es bloqueante; ADVERTENCIA y ALERTA no bloquean.

Reglas:
  CUENTA_HUERFANA      cuenta ERP sin mapeo al plan corporativo            (ERROR)
  EMPRESA_HUERFANA     empresa fuera del catálogo maestro                  (ERROR)
  ERP_INCONSISTENTE    empresa informada por un ERP distinto al de origen  (ADVERTENCIA)
  CUENTA_DUPLICADA     misma cuenta de la misma empresa dos veces          (ERROR)
  SIGNO_INCONSISTENTE  cuenta de magnitud natural con importe negativo     (ERROR)
  EMPRESA_SIN_DATOS    empresa activa sin información en el lote           (ADVERTENCIA)
  CUADRATURA           subtotal calculado distinto del reportado por ERP   (ERROR)
  SIN_CONTROL          no hay total de control para validar cuadratura     (ADVERTENCIA)
  CARGA_EXISTENTE      ya existe información empresa/ERP/período           (ADVERTENCIA)
  ANOMALIA             variación > ±15 % vs mes anterior o promedio 12M    (ALERTA)
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from catalogos import MESES_MIN_HISTORIA, TOLERANCIA_CUADRATURA, UMBRAL_ANOMALIA

COLUMNAS_HALLAZGO = ["nivel", "regla", "mensaje", "archivo", "erp", "empresa",
                     "cuenta_erp", "cuenta_corp", "esperado", "observado"]


def hallazgo(nivel: str, regla: str, mensaje: str, **kw) -> dict:
    fila = {c: None for c in COLUMNAS_HALLAZGO}
    fila.update(nivel=nivel, regla=regla, mensaje=mensaje)
    fila.update(kw)
    return fila


def validar(
    staged: pd.DataFrame,
    periodo: date,
    empresas: pd.DataFrame,
    cuentas: pd.DataFrame,
    existentes: pd.DataFrame,
    historial: pd.DataFrame,
    hallazgos_previos: list[dict] | None = None,
) -> dict:
    """Ejecuta todas las reglas sobre el lote normalizado y mapeado (`staged`)."""
    h: list[dict] = list(hallazgos_previos or [])
    codigos = set(empresas["empresa_codigo"])
    erp_catalogo = empresas.set_index("empresa_codigo")["erp_origen"]

    # 1. Cuentas huérfanas (sin homologar)
    for (erp, cuenta, archivo), g in staged[staged["cuenta_corp"].isna()].groupby(["erp", "cuenta_erp", "archivo"]):
        h.append(hallazgo("ERROR", "CUENTA_HUERFANA",
                          f"Cuenta ERP «{cuenta}» sin mapeo al plan corporativo ({len(g)} fila(s))",
                          archivo=archivo, erp=erp, cuenta_erp=cuenta))

    # 2. Empresas huérfanas (fuera del catálogo maestro)
    for (empresa, archivo), g in staged[~staged["empresa"].isin(codigos)].groupby(["empresa", "archivo"]):
        h.append(hallazgo("ERROR", "EMPRESA_HUERFANA",
                          f"Empresa «{empresa}» no registrada en el catálogo maestro ({len(g)} fila(s))",
                          archivo=archivo, empresa=empresa))

    # 3. ERP de origen distinto al catálogo
    conocidas = staged[staged["empresa"].isin(codigos)]
    if not conocidas.empty:
        esperado_erp = conocidas["empresa"].map(erp_catalogo)
        for (empresa, erp), _ in conocidas[conocidas["erp"] != esperado_erp].groupby(["empresa", "erp"]):
            h.append(hallazgo("ADVERTENCIA", "ERP_INCONSISTENTE",
                              f"{empresa} llega desde {erp}, pero su ERP de origen es {erp_catalogo[empresa]}",
                              erp=erp, empresa=empresa,
                              esperado=erp_catalogo[empresa]))

    # 4. Cuentas duplicadas dentro del mismo extracto
    dup = staged[staged.duplicated(["erp", "empresa", "cuenta_erp"], keep=False)]
    for (erp, empresa, cuenta), g in dup.groupby(["erp", "empresa", "cuenta_erp"]):
        h.append(hallazgo("ERROR", "CUENTA_DUPLICADA",
                          f"Cuenta «{cuenta}» repetida {len(g)} vez/veces para {empresa}",
                          erp=erp, empresa=empresa, cuenta_erp=cuenta, archivo=g["archivo"].iloc[0]))

    # 5. Signos: en cuentas de magnitud natural el importe normalizado no puede ser negativo
    es_control = staged["es_control"].fillna(False).astype(bool)
    detalle = staged[staged["cuenta_corp"].notna() & ~es_control]
    for (empresa, erp, cuenta_corp), g in detalle[detalle["monto"] < 0].groupby(["empresa", "erp", "cuenta_corp"]):
        h.append(hallazgo("ERROR", "SIGNO_INCONSISTENTE",
                          f"{empresa}: cuenta {cuenta_corp} con importe negativo ({g['monto'].sum():,.0f})",
                          erp=erp, empresa=empresa, cuenta_corp=cuenta_corp,
                          archivo=g["archivo"].iloc[0], observado=float(g["monto"].sum())))

    # 6. Completitud del lote: empresas activas sin información
    presentes = set(staged["empresa"])
    for _, e in empresas[empresas["activa"].astype(bool)].iterrows():
        if e["empresa_codigo"] not in presentes:
            h.append(hallazgo("ADVERTENCIA", "EMPRESA_SIN_DATOS",
                              f"{e['empresa_codigo']} ({e['empresa_nombre']}) no tiene información en el lote",
                              empresa=e["empresa_codigo"], erp=e["erp_origen"]))

    # 7. Cuadratura de subtotales frente a totales de control
    cuadratura = _cuadratura(staged)
    for _, c in cuadratura.iterrows():
        for etiqueta, calc, rep, dif in [
            ("utilidad bruta", c["ub_calculada"], c["ub_reportada"], c["dif_ub"]),
            ("utilidad neta", c["un_calculada"], c["un_reportada"], c["dif_un"]),
        ]:
            if pd.isna(rep):
                h.append(hallazgo("ADVERTENCIA", "SIN_CONTROL",
                                  f"{c['empresa']} ({c['erp']}): sin total de control de {etiqueta}",
                                  erp=c["erp"], empresa=c["empresa"]))
            elif abs(dif) > TOLERANCIA_CUADRATURA:
                h.append(hallazgo("ERROR", "CUADRATURA",
                                  f"{c['empresa']} ({c['erp']}): descuadre en {etiqueta}: "
                                  f"calculado {calc:,.0f} vs reportado {rep:,.0f} (dif. {dif:,.0f})",
                                  erp=c["erp"], empresa=c["empresa"],
                                  esperado=float(rep), observado=float(calc)))

    # 8. Carga existente en la base (duplicado de período/empresa/ERP)
    pares_lote = staged[["empresa", "erp"]].drop_duplicates()
    duplicados = pares_lote.merge(
        existentes.rename(columns={"empresa_codigo": "empresa"}), on=["empresa", "erp"], how="inner")
    for _, d in duplicados.iterrows():
        h.append(hallazgo("ADVERTENCIA", "CARGA_EXISTENTE",
                          f"Ya existe información de {d['empresa']} desde {d['erp']} en {periodo:%Y-%m}",
                          empresa=d["empresa"], erp=d["erp"]))

    # 9. Anomalías frente a mes anterior y promedio móvil de 12 meses
    anomalias = _anomalias(staged, periodo, historial)
    for _, a in anomalias.iterrows():
        h.append(hallazgo("ALERTA", "ANOMALIA", a["mensaje"], erp=a["erp"], empresa=a["empresa"],
                          cuenta_corp=a["cuenta_corp"], archivo=a["archivo"],
                          esperado=float(a["referencia"]), observado=float(a["monto"])))

    orden = {"ERROR": 0, "ADVERTENCIA": 1, "ALERTA": 2}
    hallazgos = pd.DataFrame(h, columns=COLUMNAS_HALLAZGO)
    if not hallazgos.empty:
        hallazgos = hallazgos.assign(_o=hallazgos["nivel"].map(orden)).sort_values("_o").drop(columns="_o")

    return {
        "hallazgos": hallazgos.reset_index(drop=True),
        "cuadratura": cuadratura,
        "matriz": _matriz(empresas, staged, existentes),
        "anomalias": anomalias,
        "duplicados": duplicados.reset_index(drop=True),
    }


def _cuadratura(staged: pd.DataFrame) -> pd.DataFrame:
    """Compara utilidad bruta y neta calculadas con los totales de control del ERP."""
    columnas = ["empresa", "erp", "ub_calculada", "ub_reportada", "dif_ub",
                "un_calculada", "un_reportada", "dif_un"]
    base = staged[staged["cuenta_corp"].notna()].copy()
    if base.empty:
        return pd.DataFrame(columns=columnas)
    base["es_control"] = base["es_control"].fillna(False).astype(bool)
    base["valor"] = base["monto"] * base["signo_pnl"]

    det = base[~base["es_control"]]
    ub = det[det["rubro_nivel1"].isin(["Ingresos", "Costo de Ventas"])].groupby(["empresa", "erp"])["valor"].sum()
    un = det.groupby(["empresa", "erp"])["valor"].sum()

    ctrl = (base[base["es_control"]]
            .groupby(["empresa", "erp", "cuenta_corp"])["monto"].sum()
            .unstack("cuenta_corp")
            .reindex(columns=["CTRL_UB", "CTRL_UN"]))

    cuad = pd.DataFrame({
        "ub_calculada": ub, "ub_reportada": ctrl["CTRL_UB"],
        "un_calculada": un, "un_reportada": ctrl["CTRL_UN"],
    }).reset_index()
    cuad["dif_ub"] = cuad["ub_calculada"] - cuad["ub_reportada"]
    cuad["dif_un"] = cuad["un_calculada"] - cuad["un_reportada"]
    return cuad[columnas]


def _matriz(empresas: pd.DataFrame, staged: pd.DataFrame, existentes: pd.DataFrame) -> pd.DataFrame:
    """Estado de cada una de las empresas para el período: cargada, en lote o pendiente."""
    en_bd = set(existentes["empresa_codigo"]) if not existentes.empty else set()
    en_lote = set(staged["empresa"])
    m = empresas[["empresa_codigo", "empresa_nombre", "area_nombre", "erp_origen"]].copy()
    m["estado"] = [
        "Cargada en BD" if c in en_bd else ("En lote" if c in en_lote else "Pendiente")
        for c in m["empresa_codigo"]
    ]
    return m.reset_index(drop=True)


def _anomalias(staged: pd.DataFrame, periodo: date, historial: pd.DataFrame) -> pd.DataFrame:
    """Compara cada empresa-cuenta del lote con el mes anterior y con el promedio móvil de 12 meses."""
    columnas = ["periodo", "empresa", "erp", "cuenta_corp", "archivo", "comparacion",
                "monto", "referencia", "variacion", "mensaje"]
    if historial.empty or staged.empty:
        return pd.DataFrame(columns=columnas)

    es_control = staged["es_control"].fillna(False).astype(bool)
    actual = (staged[staged["cuenta_corp"].notna() & ~es_control]
              .groupby(["empresa", "erp", "cuenta_corp"], as_index=False)
              .agg(monto=("monto", "sum"), archivo=("archivo", "first")))

    hist = historial.rename(columns={"empresa_codigo": "empresa"}).copy()
    hist["periodo"] = pd.to_datetime(hist["periodo"])
    p = pd.Timestamp(periodo)

    mes_ant = (hist[hist["periodo"] == p - pd.DateOffset(months=1)]
               [["empresa", "cuenta_corp", "monto"]].rename(columns={"monto": "ref_mes"}))
    prom = hist.groupby(["empresa", "cuenta_corp"])["monto"].agg(["mean", "count"]).reset_index()
    prom = (prom[prom["count"] >= MESES_MIN_HISTORIA]
            .rename(columns={"mean": "ref_12m"})[["empresa", "cuenta_corp", "ref_12m"]])

    m = actual.merge(mes_ant, on=["empresa", "cuenta_corp"], how="left").merge(
        prom, on=["empresa", "cuenta_corp"], how="left")

    filas = []
    for comparacion, col in [("Mes anterior", "ref_mes"), ("Promedio móvil 12M", "ref_12m")]:
        s = m[m[col].notna() & (m[col] != 0)].copy()
        s["referencia"] = s[col]
        s["variacion"] = (s["monto"] - s["referencia"]) / s["referencia"].abs()
        s = s[s["variacion"].abs() > UMBRAL_ANOMALIA]
        s["comparacion"] = comparacion
        filas.append(s)

    res = pd.concat(filas, ignore_index=True) if filas else pd.DataFrame()
    if res.empty:
        return pd.DataFrame(columns=columnas)
    res["periodo"] = pd.Timestamp(periodo)
    res["mensaje"] = [
        f"{r.empresa} · {r.cuenta_corp}: {r.comparacion} {r.variacion:+.1%} "
        f"(referencia {r.referencia:,.0f}, observado {r.monto:,.0f})"
        for r in res.itertuples()
    ]
    return res[columnas]
