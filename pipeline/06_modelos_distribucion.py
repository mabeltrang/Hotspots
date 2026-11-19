"""
06 - Modelos de distribución de especies (SDM) por hexágono H3 de ~5 km² (resolución 7).

Para cada especie con suficientes localidades estima la idoneidad de hábitat en todo el
departamento a partir de clima, relieve y cobertura arbórea actual. La app usa el resultado
para el mapa de riqueza modelada, el mapa por especie y la consulta por predio.

Método (presencia / fondo, equivalente a MaxEnt; Fithian y Hastie 2013, Renner y Warton 2013):
    - Presencias: hexágonos con registros de la especie (GBIF, Unergy y CAR).
    - Fondo de grupo objetivo (Phillips et al. 2009): hexágonos con registros de cualquier
      especie del mismo grupo. Así el modelo compara contra donde se ha muestreado y no
      aprende el sesgo de muestreo como si fuera hábitat.
    - Modelo: regresión logística regularizada con términos lineales, cuadráticos e
      interacciones de las variables estandarizadas.
    - Validación cruzada espacial por bloques (hexágonos H3 de resolución 4, ~1.770 km²).
      Se guardan solo las especies con AUC espacial >= AUC_MIN.
    - Umbral de presencia: percentil 10 de la idoneidad en las presencias de entrenamiento.
    - Los hexágonos que son sobre todo agua (ciénagas, ríos) no se modelan: las especies
      evaluadas son terrestres.
    - Sin extrapolar: los hexágonos con alguna variable fuera del rango de los hexágonos
      muestreados del grupo no se predicen (el modelo no sabe qué pasa en ambientes que no vio).

Entradas:
    data/procesados/<depto>/registros.parquet
    data/unergy/*.xlsx y data/car/*.xlsx (opcionales)
    covariables_<depto>.tif  (de pipeline/gee_covariables.js)
Salidas (subir a data/procesados/<depto>/):
    sdm_idoneidad.parquet   h3_7, species, idoneidad (solo hexágonos sobre el umbral)
    sdm_metricas.csv        una fila por especie modelada

Uso (Colab o local):
    python pipeline/06_modelos_distribucion.py --depto cesar --covariables /ruta/covariables_cesar.tif

Opción --desagrupar: cada zona de ~250 km² pesa igual en el ajuste (menos peso a zonas con
monitoreo intensivo, sin descartar datos).

Prueba de sensibilidad (no guarda archivos, solo imprime el resumen): repetir sin los registros de
ciertas instituciones para ver si los hotspots modelados dependen de ellas.
    python pipeline/06_modelos_distribucion.py --covariables ... --excluir "Prodeco,Drummond"
"""
import argparse
import json
import sys
import warnings
from datetime import date
from pathlib import Path

import h3
import numpy as np
import pandas as pd
import rasterio
from shapely.geometry import shape
from shapely.ops import unary_union
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import PolynomialFeatures, StandardScaler

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
from comun import GRUPOS, aplicar_sinonimos  # noqa: E402
from unergy import cargar_unergy, normalizar_texto  # noqa: E402

RES = 7                # hexágonos de ~5 km²
RES_BLOQUE = 4         # bloques para la validación espacial
RES_DECLUSTER = 5      # zonas de ~250 km² que pesan igual en el ajuste (ver pesos_espaciales)
MIN_CELDAS = 20        # localidades (hexágonos) mínimas por especie
MIN_FONDO = 100        # hexágonos mínimos de fondo por grupo
N_PLIEGUES = 4
AUC_MIN = 0.70
C_REGULARIZACION = 0.5  # menor = modelo más suave
# Orden de las bandas que exporta pipeline/gee_covariables.js (versión actual y la primera)
BANDAS_GEE = {12: ["bio01", "bio04", "bio12", "bio15", "bio17", "elevacion", "pendiente", "cobertura_arborea",
                   "arbustal", "herbazal", "humedal", "agua"],
              8: ["bio01", "bio04", "bio12", "bio15", "bio17", "elevacion", "pendiente", "cobertura_arborea"]}
UMBRAL_AGUA = 0.5      # hexágonos con más de la mitad de agua (ciénagas, ríos) no se modelan


COLUMNAS = ["species", "grupo", "lat", "lon", f"h3_{RES}", "institutionCode"]


def cargar_registros(depto, excluir=()):
    df = pd.read_parquet(RAIZ / "data" / "procesados" / depto / "registros.parquet")
    df = df[COLUMNAS]
    for fuente, carpeta in {"Unergy": RAIZ / "data" / "unergy", "CAR": RAIZ / "data" / "car"}.items():
        if carpeta.exists() and any(carpeta.glob("*.xlsx")):
            inv, _ = cargar_unergy(carpeta, fuente)
            if inv is not None:
                inv = inv[inv["departamento"] == normalizar_texto(depto)]
                df = pd.concat([df, inv[COLUMNAS]], ignore_index=True)
    if excluir:
        quitar = df["institutionCode"].fillna("").str.contains("|".join(excluir), case=False)
        print(f"Excluidos {int(quitar.sum()):,} registros de: {', '.join(excluir)}")
        df = df[~quitar]
    df["grupo"] = df["grupo"].map(GRUPOS)
    df = aplicar_sinonimos(df, RAIZ / "data" / "listas")
    return df.dropna(subset=["grupo", "species", f"h3_{RES}"])


def municipios(depto):
    datos = json.loads((RAIZ / "data" / "limites" / "municipios_colombia.geojson").read_text(encoding="utf-8"))
    return {f["properties"]["MPIO_CNMBR"]: shape(f["geometry"]) for f in datos["features"]
            if normalizar_texto(f["properties"]["DPTO_CNMBR"]) == normalizar_texto(depto)}


def celdas_departamento(depto):
    contorno = unary_union(list(municipios(depto).values()))
    return set(h3.geo_to_cells(contorno.__geo_interface__, RES))


def resumen_hotspots(pred, depto):
    """Municipios con más hexágonos en el 10 % de mayor riqueza modelada."""
    import shapely
    if pred.empty:
        return
    riq = pred.groupby(f"h3_{RES}", observed=True)["species"].nunique()
    top = riq[riq >= riq.quantile(0.9)].index
    lat, lon = np.array([h3.cell_to_latlng(c) for c in top]).T
    mun = pd.Series("Fuera del límite", index=top)
    for n, g in municipios(depto).items():
        mun[shapely.contains_xy(g, lon, lat)] = n
    print("\nHotspots modelados (10 % de hexágonos con más especies) por municipio:")
    print(mun.value_counts().head(8).to_string())


def covariables_por_celda(celdas, ruta_tif):
    celdas = sorted(celdas)
    lat_lon = [h3.cell_to_latlng(c) for c in celdas]
    with rasterio.open(ruta_tif) as r:
        if all(r.descriptions):
            nombres = list(r.descriptions)
        elif r.count in BANDAS_GEE:  # el GeoTIFF de GEE no guarda los nombres de las bandas
            nombres = BANDAS_GEE[r.count]
        else:
            nombres = [f"b{i}" for i in range(1, r.count + 1)]
        valores = np.array(list(r.sample([(lon, lat) for lat, lon in lat_lon])), dtype=float)
        if r.nodata is not None:
            valores[valores == r.nodata] = np.nan
    x = pd.DataFrame(valores, index=celdas, columns=nombres).dropna()
    if "agua" in x.columns:
        n_agua = int((x["agua"] > UMBRAL_AGUA).sum())
        print(f"{n_agua} hexágonos de agua (ciénagas, ríos) fuera del modelo")
        x = x[x["agua"] <= UMBRAL_AGUA].drop(columns="agua")
    return x


def modelo():
    return make_pipeline(StandardScaler(), PolynomialFeatures(2, include_bias=False), StandardScaler(),
                         LogisticRegression(C=C_REGULARIZACION, class_weight="balanced", max_iter=3000))


def pesos_espaciales(celdas, y):
    """Peso 1/(n hexágonos de la misma clase en la misma zona de ~250 km²), normalizado.

    Una zona muy muestreada (p. ej. un área minera con monitoreo continuo) aporta muchos
    hexágonos casi iguales; con estos pesos cada zona cuenta lo mismo en el ajuste, sin
    descartar datos (desagrupamiento espacial; Kramer-Schadt et al. 2013, Boria et al. 2014).
    """
    zonas = pd.Series([h3.cell_to_parent(c, RES_DECLUSTER) for c in celdas])
    clave = zonas + "_" + pd.Series(y).astype(str)
    w = 1.0 / clave.map(clave.value_counts()).to_numpy()
    for clase in (0, 1):  # cada clase conserva su peso total
        w[y == clase] *= (y == clase).sum() / w[y == clase].sum()
    return w


def auc_espacial(x, y, bloques, w):
    if len(set(bloques[y == 1])) < 2:
        return np.nan
    w = np.ones(len(y)) if w is None else w
    aucs = []
    for ent, pru in GroupKFold(n_splits=min(N_PLIEGUES, len(set(bloques)))).split(x, y, bloques):
        if y[pru].min() == y[pru].max() or y[ent].sum() < 5:
            continue
        m = modelo().fit(x[ent], y[ent], logisticregression__sample_weight=w[ent])
        aucs.append(roc_auc_score(y[pru], m.predict_proba(x[pru])[:, 1], sample_weight=w[pru]))
    return float(np.mean(aucs)) if aucs else np.nan


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--depto", default="cesar")
    ap.add_argument("--covariables", required=True)
    ap.add_argument("--excluir", default="", help="instituciones a excluir, separadas por coma (prueba de sensibilidad)")
    ap.add_argument("--desagrupar", action="store_true",
                    help="cada zona de ~250 km² pesa igual en el ajuste (reduce el peso de zonas muy muestreadas)")
    args = ap.parse_args()
    desagrupar = args.desagrupar
    excluir = tuple(x.strip() for x in args.excluir.split(",") if x.strip())
    depto = normalizar_texto(args.depto)
    salida = RAIZ / "data" / "procesados" / depto

    print("Desagrupamiento espacial:", "sí" if desagrupar else "no")
    reg = cargar_registros(depto, excluir)
    celdas = celdas_departamento(depto)
    reg = reg[reg[f"h3_{RES}"].isin(celdas)]
    cov = covariables_por_celda(celdas, args.covariables)
    print(f"{len(cov):,} hexágonos con covariables ({', '.join(cov.columns)}); {len(reg):,} registros")

    x_todo = cov.to_numpy()
    filas_pred, metricas = [], []
    for grupo, sub in reg.groupby("grupo"):
        fondo = sorted(set(sub[f"h3_{RES}"]) & set(cov.index))
        if len(fondo) < MIN_FONDO:
            print(f"[{grupo}] solo {len(fondo)} hexágonos de fondo: no se modela")
            continue
        x_fondo = cov.loc[fondo]
        dentro_rango = ((cov >= x_fondo.min()) & (cov <= x_fondo.max())).all(axis=1).to_numpy()
        por_especie = sub.groupby("species")[f"h3_{RES}"].agg(lambda c: set(c) & set(cov.index))
        candidatas = por_especie[por_especie.map(len) >= MIN_CELDAS]
        print(f"[{grupo}] {len(candidatas)} especies con >= {MIN_CELDAS} hexágonos; fondo {len(fondo)}")
        for especie, pres in candidatas.items():
            celdas_m = sorted(pres) + [c for c in fondo if c not in pres]
            y = np.array([1] * len(pres) + [0] * (len(celdas_m) - len(pres)))
            x = cov.loc[celdas_m].to_numpy()
            bloques = np.array([h3.cell_to_parent(c, RES_BLOQUE) for c in celdas_m])
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                w = pesos_espaciales(celdas_m, y) if desagrupar else np.ones(len(y))
                auc = auc_espacial(x, y, bloques, w)
                m = modelo().fit(x, y, logisticregression__sample_weight=w)
                idon_pres = m.predict_proba(cov.loc[sorted(pres)].to_numpy())[:, 1]
                umbral = float(np.percentile(idon_pres, 10))
                idon = m.predict_proba(x_todo)[:, 1]
            sobre = (idon >= umbral) & dentro_rango
            valido = bool(auc >= AUC_MIN) if not np.isnan(auc) else False
            metricas.append({"species": especie, "grupo": grupo, "n_celdas": len(pres),
                             "auc_espacial": round(auc, 3) if not np.isnan(auc) else None,
                             "umbral": round(umbral, 4), "celdas_idoneas": int(sobre.sum()), "valido": valido})
            if valido:
                filas_pred.append(pd.DataFrame({f"h3_{RES}": cov.index[sobre], "species": especie,
                                                "idoneidad": np.round(idon[sobre], 3).astype("float32")}))

    met = pd.DataFrame(metricas)
    pred = pd.concat(filas_pred, ignore_index=True) if filas_pred else pd.DataFrame(
        columns=[f"h3_{RES}", "species", "idoneidad"])
    print(f"\n{len(met)} especies modeladas, {int(met['valido'].sum()) if len(met) else 0} con AUC espacial >= {AUC_MIN}")
    resumen_hotspots(pred, depto)
    if excluir:
        print("\nPrueba de sensibilidad: no se guardaron archivos.")
        return
    pred["species"] = pred["species"].astype("category")
    pred.to_parquet(salida / "sdm_idoneidad.parquet", index=False)
    met.to_csv(salida / "sdm_metricas.csv", index=False)
    (salida / "sdm_info.json").write_text(json.dumps({
        "fecha": date.today().isoformat(), "resolucion_h3": RES, "covariables": list(cov.columns),
        "desagrupado": desagrupar,
        "min_celdas": MIN_CELDAS, "auc_min": AUC_MIN, "especies_modeladas": len(met),
        "especies_validas": int(met["valido"].sum()) if len(met) else 0}, ensure_ascii=False, indent=1))
    print(f"Archivos en {salida.relative_to(RAIZ)}: sdm_idoneidad.parquet, sdm_metricas.csv, sdm_info.json")


if __name__ == "__main__":
    main()
