"""
01 - Procesar descarga GBIF (formato "Simple") -> archivos para la app.

Correr desde la carpeta del repo:
    python pipeline/01_procesar_gbif.py

Salida (en data/procesados/<DEPTO>/):
    registros.parquet   registros limpios, con su celda H3
    metadata.json       fecha, DOI, filtros y conteos
"""
import csv
import json
import os
import zipfile
from datetime import date

import h3
import pandas as pd

# ================== CONFIGURACIÓN (editar) ==================
RUTA_DESCARGA = "data/crudos/cesar.csv"  # CSV o zip descargado de GBIF (renombrado)
DEPTO = "cesar"
DOI = "10.15468/dl.wafy35"  # DOI de la descarga GBIF (Cesar, 18-nov-2026)
SALIDA = f"data/procesados/{DEPTO}"

INCERTIDUMBRE_MAX_M = 2000  # descarta registros con coordenadas más imprecisas
ANIO_MIN = None             # p. ej. 1970 para quitar registros muy viejos; None = todos
RESOLUCIONES_H3 = [5, 6, 7] # ~253 km², ~36 km², ~5 km² por hexágono
# ============================================================

COLUMNAS = [
    "gbifID", "kingdom", "phylum", "class", "order", "family", "genus", "species",
    "taxonRank", "occurrenceStatus", "decimalLatitude", "decimalLongitude",
    "coordinateUncertaintyInMeters", "year", "basisOfRecord", "institutionCode",
    "collectionCode", "issue",
]

PROBLEMAS_COORD = ("PRESUMED_NEGATED", "PRESUMED_SWAPPED", "ZERO_COORDINATE",
                   "COORDINATE_INVALID", "COORDINATE_OUT_OF_RANGE", "COUNTRY_COORDINATE_MISMATCH")


def leer_gbif(ruta):
    """Lee el CSV de GBIF (separado por tabulaciones, sin comillas), dentro o fuera del zip."""
    opciones = dict(sep="\t", dtype=str, quoting=csv.QUOTE_NONE, on_bad_lines="skip",
                    usecols=lambda c: c in COLUMNAS, encoding="utf-8")
    if ruta.endswith(".zip"):
        with zipfile.ZipFile(ruta) as z:
            nombre = next(n for n in z.namelist() if n.endswith((".csv", ".txt")))
            with z.open(nombre) as f:
                return pd.read_csv(f, **opciones)
    return pd.read_csv(ruta, **opciones)


def grupo(fila):
    """Grupo biológico para los filtros de la app."""
    reino, clase, filo = fila["kingdom"], fila["class"], fila["phylum"]
    if reino == "Plantae":
        return "Plantas"
    if reino == "Fungi":
        return "Hongos"
    return {
        "Aves": "Aves", "Mammalia": "Mamíferos", "Reptilia": "Reptiles",
        "Squamata": "Reptiles", "Testudines": "Reptiles", "Crocodylia": "Reptiles",
        "Amphibia": "Anfibios", "Insecta": "Insectos", "Arachnida": "Arácnidos",
        "Actinopterygii": "Peces", "Teleostei": "Peces", "Elasmobranchii": "Peces",
    }.get(clase, "Otros invertebrados" if filo in ("Arthropoda", "Mollusca", "Annelida") else "Otros")


EMPRESA = ("S.A", "SAS", "S.A.S", "LTD", "E.S.P", "INC", "SUCURSAL", "PRODECO", "DRUMMOND",
           "PAREX", "INTERCOLOMBIA", "GAIA", "ECOPETROL", "CONSULTOR", "INGENIER", "TERRASOS",
           "ENEL", "CPR")
ACADEMIA = ("UNIVERSIDAD", "UNIVERSITY", "SENA", "INSTITUTO", "INSTITUTE", "MUSEO", "MUSEUM",
            "HUMBOLDT", "HERBARIO", "COLECCI", "AGROSAVIA", "CIBIO")
ONG = ("FUNDACI", "RNOA", "OBSERVADORES", "PROAVES", "PATRIMONIO NATURAL", "ASOCIACI", "WCS", "WWF")


def fuente(fila):
    inst, col = str(fila["institutionCode"]), str(fila["collectionCode"])
    up = inst.upper()
    if up == "INATURALIST":
        return "iNaturalist"
    if col.upper() == "EBIRD":
        return "eBird"
    if fila["basisOfRecord"] in ("PRESERVED_SPECIMEN", "MATERIAL_CITATION"):
        return "Colecciones"
    if any(k in up for k in EMPRESA):
        return "Estudios ambientales"
    if any(k in up for k in ACADEMIA):
        return "Universidades e institutos"
    if any(k in up for k in ONG):
        return "ONG y redes de observadores"
    return "Otras fuentes"


def main():
    df = leer_gbif(RUTA_DESCARGA)
    conteo = {"registros_descargados": len(df)}
    print(f"Registros leídos: {len(df):,}")

    # Tipos
    for c in ["decimalLatitude", "decimalLongitude", "coordinateUncertaintyInMeters", "year"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    # 1. Coordenadas válidas
    df = df.dropna(subset=["decimalLatitude", "decimalLongitude"])
    df = df[df["decimalLatitude"].between(-5, 14) & df["decimalLongitude"].between(-80, -66)]
    df = df[~df["issue"].fillna("").str.contains("|".join(PROBLEMAS_COORD))]
    conteo["con_coordenadas_validas"] = len(df)

    # 2. Precisión
    inc = df["coordinateUncertaintyInMeters"]
    df = df[inc.isna() | (inc <= INCERTIDUMBRE_MAX_M)]
    conteo["precision_suficiente"] = len(df)

    # 3. Presencias identificadas a especie
    df = df[df["occurrenceStatus"].fillna("PRESENT").str.upper() != "ABSENT"]
    sin_especie = df["species"].isna().sum()
    df = df.dropna(subset=["species"])
    conteo["descartados_sin_especie"] = int(sin_especie)

    # 4. Año
    if ANIO_MIN:
        df = df[df["year"] >= ANIO_MIN]

    # 5. Duplicados (misma especie, mismo punto, mismo año)
    df["_lat"] = df["decimalLatitude"].round(4)
    df["_lon"] = df["decimalLongitude"].round(4)
    df = df.drop_duplicates(subset=["species", "_lat", "_lon", "year"]).drop(columns=["_lat", "_lon"])
    conteo["registros_finales"] = len(df)

    # 6. Atributos
    df["grupo"] = df.apply(grupo, axis=1)
    df["fuente"] = df.apply(fuente, axis=1)

    # 7. Celdas H3
    for r in RESOLUCIONES_H3:
        df[f"h3_{r}"] = [h3.latlng_to_cell(la, lo, r)
                         for la, lo in zip(df["decimalLatitude"], df["decimalLongitude"])]

    salida = df.rename(columns={"decimalLatitude": "lat", "decimalLongitude": "lon"})[
        ["gbifID", "species", "family", "order", "class", "grupo", "year", "lat", "lon",
         "fuente", "institutionCode", "basisOfRecord"] + [f"h3_{r}" for r in RESOLUCIONES_H3]
    ]
    salida["year"] = salida["year"].astype("Int64")

    os.makedirs(SALIDA, exist_ok=True)
    salida.to_parquet(f"{SALIDA}/registros.parquet", index=False)

    meta = {
        "departamento": DEPTO,
        "doi_gbif": DOI,
        "fecha_procesamiento": date.today().isoformat(),
        "filtros": {"incertidumbre_max_m": INCERTIDUMBRE_MAX_M, "anio_min": ANIO_MIN,
                    "solo_especie": True, "sin_duplicados": "especie + punto + año"},
        "resoluciones_h3": RESOLUCIONES_H3,
        "conteos": conteo,
        "n_especies": int(salida["species"].nunique()),
    }
    with open(f"{SALIDA}/metadata.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    print("\nResumen")
    for k, v in conteo.items():
        print(f"  {k}: {v:,}")
    print(f"  especies: {meta['n_especies']:,}")
    print("\nRegistros por grupo:")
    print(salida["grupo"].value_counts().to_string())
    print("\nRegistros por fuente:")
    print(salida["fuente"].value_counts().to_string())
    print(f"\nGuardado en {SALIDA}")


if __name__ == "__main__":
    main()
