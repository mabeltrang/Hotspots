"""
07 - Listas completas de eBird (EBD + Sampling Event Data) -> esfuerzo y completitud de aves.

Las presencias de eBird YA vienen en la descarga de GBIF (fuente "eBird" en registros.parquet).
Este script NO agrega registros nuevos al mapa de riqueza: usa la estructura de listas de la
descarga directa de eBird, que GBIF no trae, para responder dos preguntas por hexágono:

    1. ¿Qué tan completo está el inventario de aves?  (Chao2 con listas como unidad de muestreo)
    2. ¿Con qué frecuencia aparece cada especie?      (% de listas completas que la reportan)

Descarga: https://ebird.org/data/download -> Colombia -> Cesar (CO-CES), marcar
"Include sampling event data". Llegan dos .txt (o un .zip con ambos); guardarlos en data/crudos/.

Correr desde la carpeta del repo:
    python pipeline/07_ebird_listas.py

Salida (en data/procesados/<DEPTO>/):
    ebird_celdas.parquet       una fila por hexágono y resolución: listas, horas, especies,
                               Chao2 y completitud
    ebird_frecuencia.parquet   hexágono (res. 7) x especie: listas que la reportan y frecuencia
    ebird_info.json            fecha, versión de la EBD, filtros y conteos

Uso de los datos: los términos de eBird prohíben el uso comercial sin permiso escrito de Cornell
(incluye usos que informen a una empresa). Pedir permiso a ebird@cornell.edu antes de usar
estos resultados en Unergy.
"""
import csv
import json
import os
import sys
import zipfile
from datetime import date

import h3
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from comun import aplicar_sinonimos  # noqa: E402

# ================== CONFIGURACIÓN (editar) ==================
RUTA_EBD = "data/crudos/ebd_CO-CES_relOct-2026.txt"            # observaciones (o el .zip con ambos)
RUTA_SAMPLING = "data/crudos/ebd_CO-CES_relOct-2026_sampling.txt"  # listas; ignorado si RUTA_EBD es .zip
DEPTO = "cesar"
VERSION_EBD = "relOct-2026"   # aparece en el nombre del archivo; va a la cita
SALIDA = f"data/procesados/{DEPTO}"

# Filtros de buenas prácticas (Strimas-Mackey et al. 2023, "Best Practices for Using eBird Data")
PROTOCOLOS = ("Stationary", "Traveling")
DURACION_MAX_MIN = 300    # listas de más de 5 h mezclan demasiados hábitats
DISTANCIA_MAX_KM = 5      # recorridos más largos no caben en un hexágono de res. 7
ANIO_MIN = 2000           # None = todas
RESOLUCIONES_H3 = [5, 6, 7]
MIN_LISTAS = 5            # por debajo, Chao2 no es confiable y la completitud queda vacía
TAMANO_BLOQUE = 500_000   # filas de la EBD leídas a la vez (la de un departamento puede pesar GB)
# ============================================================

COLS_SAMPLING = ["SAMPLING EVENT IDENTIFIER", "LATITUDE", "LONGITUDE", "OBSERVATION DATE",
                 "OBSERVER ID", "PROTOCOL NAME", "DURATION MINUTES", "EFFORT DISTANCE KM",
                 "NUMBER OBSERVERS", "ALL SPECIES REPORTED", "GROUP IDENTIFIER"]
COLS_EBD = ["SAMPLING EVENT IDENTIFIER", "CATEGORY", "SCIENTIFIC NAME", "EXOTIC CODE"]
OPCIONES = dict(sep="\t", dtype=str, quoting=csv.QUOTE_NONE, on_bad_lines="skip", encoding="utf-8")


def abrir(ruta, sufijo_en_zip):
    """Devuelve un archivo abierto: el .txt suelto o el que está dentro del zip."""
    if ruta.endswith(".zip"):
        z = zipfile.ZipFile(ruta)
        nombre = next(n for n in z.namelist() if n.endswith(sufijo_en_zip))
        return z.open(nombre)
    return open(ruta, "rb")


def leer_listas():
    """Lee el Sampling Event Data y deja solo listas completas comparables."""
    ruta, sufijo = (RUTA_EBD, "_sampling.txt") if RUTA_EBD.endswith(".zip") else (RUTA_SAMPLING, ".txt")
    with abrir(ruta, sufijo) as f:
        s = pd.read_csv(f, usecols=lambda c: c in COLS_SAMPLING, **OPCIONES)
    conteo = {"listas_descargadas": len(s)}

    for c in ["LATITUDE", "LONGITUDE", "DURATION MINUTES", "EFFORT DISTANCE KM", "NUMBER OBSERVERS"]:
        s[c] = pd.to_numeric(s[c], errors="coerce")
    s["anio"] = pd.to_datetime(s["OBSERVATION DATE"], errors="coerce").dt.year
    s["mes"] = pd.to_datetime(s["OBSERVATION DATE"], errors="coerce").dt.month

    s = s[s["ALL SPECIES REPORTED"] == "1"]
    conteo["listas_completas"] = len(s)

    s["EFFORT DISTANCE KM"] = s["EFFORT DISTANCE KM"].fillna(0)  # estacionarias no tienen distancia
    s = s[s["PROTOCOL NAME"].isin(PROTOCOLOS)
          & (s["DURATION MINUTES"] <= DURACION_MAX_MIN)
          & (s["EFFORT DISTANCE KM"] <= DISTANCIA_MAX_KM)]
    if ANIO_MIN:
        s = s[s["anio"] >= ANIO_MIN]
    conteo["con_esfuerzo_comparable"] = len(s)

    # Listas compartidas: varios observadores suben copias de la misma salida. Se deja una por grupo.
    s["grupo_id"] = s["GROUP IDENTIFIER"].fillna(s["SAMPLING EVENT IDENTIFIER"])
    s = s.drop_duplicates("grupo_id")
    conteo["listas_finales"] = len(s)

    for r in RESOLUCIONES_H3:
        s[f"h3_{r}"] = [h3.latlng_to_cell(la, lo, r) for la, lo in zip(s["LATITUDE"], s["LONGITUDE"])]
    return s.rename(columns={"SAMPLING EVENT IDENTIFIER": "lista", "DURATION MINUTES": "minutos",
                             "EFFORT DISTANCE KM": "km", "OBSERVER ID": "observador"}), conteo


def leer_detecciones(listas_validas):
    """Lee la EBD por bloques y guarda solo (lista, especie) de las listas que quedaron."""
    partes = []
    with abrir(RUTA_EBD, ".txt") as f:
        lector = pd.read_csv(f, usecols=lambda c: c in COLS_EBD, chunksize=TAMANO_BLOQUE, **OPCIONES)
        for i, b in enumerate(lector, 1):
            b = b[b["SAMPLING EVENT IDENTIFIER"].isin(listas_validas)]
            # Solo especies: los slash ("Turdus sp.") e híbridos no son especies distintas.
            # Las subespecies ya vienen con el nombre de la especie en SCIENTIFIC NAME.
            b = b[b["CATEGORY"].isin(["species", "issf", "form", "intergrade"])]
            b = b[b["EXOTIC CODE"].fillna("") != "X"]  # escapadas de cautiverio
            partes.append(b[["SAMPLING EVENT IDENTIFIER", "SCIENTIFIC NAME"]])
            print(f"  bloque {i}: {sum(len(p) for p in partes):,} detecciones")
    d = pd.concat(partes).rename(columns={"SAMPLING EVENT IDENTIFIER": "lista", "SCIENTIFIC NAME": "species"})
    # eBird usa la taxonomía Clements; se unifica con los nombres de GBIF usando sinonimos.csv
    d = aplicar_sinonimos(d, "data/listas")
    return d.drop_duplicates()


def chao2(incidencias, m):
    """Riqueza estimada por incidencia (Chao 1987; Colwell 2013, EstimateS).

    incidencias: número de listas en que aparece cada especie. m: número de listas.
    """
    s = len(incidencias)
    q1 = int((incidencias == 1).sum())
    q2 = int((incidencias == 2).sum())
    if m < 2:
        return float(s)
    factor = (m - 1) / m
    if q2 > 0:
        return s + factor * q1 ** 2 / (2 * q2)
    return s + factor * q1 * (q1 - 1) / 2  # versión con corrección de sesgo


def por_celda(listas, det, r):
    col = f"h3_{r}"
    det_c = det.merge(listas[["lista", col]], on="lista")
    esfuerzo = listas.groupby(col).agg(listas=("lista", "size"), horas=("minutos", lambda x: x.sum() / 60),
                                       observadores=("observador", "nunique"), ultimo_anio=("anio", "max"),
                                       meses=("mes", "nunique"))
    filas = []
    for celda, g in det_c.groupby(col):
        inc = g.groupby("species").size()
        m = int(esfuerzo.at[celda, "listas"])
        est = chao2(inc, m)
        ok = m >= MIN_LISTAS
        filas.append({col: celda, "especies": len(inc), "chao2": round(est, 1) if ok else None,
                      "completitud": round(len(inc) / est, 3) if ok and est else None})
    t = esfuerzo.join(pd.DataFrame(filas).set_index(col), how="left").reset_index()
    t = t.rename(columns={col: "celda"})
    t["especies"] = t["especies"].fillna(0).astype(int)  # listas completas sin ninguna especie
    t["horas"] = t["horas"].round(1)
    t.insert(1, "resolucion", r)
    return t


def frecuencia(listas, det):
    """% de listas completas de cada hexágono (res. 7) que reportan cada especie."""
    det_c = det.merge(listas[["lista", "h3_7"]], on="lista")
    n = listas.groupby("h3_7").size().rename("listas_celda")
    f = det_c.groupby(["h3_7", "species"]).size().rename("listas_con").reset_index().join(n, on="h3_7")
    f["frecuencia"] = (f["listas_con"] / f["listas_celda"]).round(3)
    return f


def main():
    print("Leyendo listas (sampling)...")
    listas, conteo = leer_listas()
    print("Leyendo observaciones (EBD)...")
    det = leer_detecciones(set(listas["lista"]))
    conteo["detecciones"] = len(det)

    celdas = pd.concat([por_celda(listas, det, r) for r in RESOLUCIONES_H3], ignore_index=True)
    frec = frecuencia(listas, det)

    os.makedirs(SALIDA, exist_ok=True)
    celdas.to_parquet(f"{SALIDA}/ebird_celdas.parquet", index=False)
    frec.to_parquet(f"{SALIDA}/ebird_frecuencia.parquet", index=False)

    info = {
        "departamento": DEPTO,
        "fecha_procesamiento": date.today().isoformat(),
        "version_ebd": VERSION_EBD,
        "cita": f"eBird Basic Dataset. Version: EBD_{VERSION_EBD}. Cornell Lab of Ornithology, Ithaca, New York.",
        "filtros": {"protocolos": PROTOCOLOS, "duracion_max_min": DURACION_MAX_MIN,
                    "distancia_max_km": DISTANCIA_MAX_KM, "anio_min": ANIO_MIN,
                    "solo_listas_completas": True, "listas_compartidas": "una por GROUP IDENTIFIER",
                    "min_listas_para_completitud": MIN_LISTAS},
        "conteos": conteo,
        "n_especies": int(det["species"].nunique()),
    }
    with open(f"{SALIDA}/ebird_info.json", "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=2)

    print("\nResumen")
    for k, v in conteo.items():
        print(f"  {k}: {v:,}")
    print(f"  especies: {info['n_especies']:,}")
    r7 = celdas[celdas["resolucion"] == 7]
    con = r7["completitud"].dropna()
    print(f"\nHexágonos res. 7 con listas: {len(r7):,}; con {MIN_LISTAS}+ listas: {len(con):,}")
    if len(con):
        print(f"  completitud mediana: {con.median():.0%}; con 80 % o más: {(con >= 0.8).sum():,}")
    print(f"\nGuardado en {SALIDA}")


if __name__ == "__main__":
    main()
