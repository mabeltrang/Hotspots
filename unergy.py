"""
Lectura de inventarios de Unergy (Excel) para sumarlos a los registros de GBIF.

Los archivos van en data/unergy/ (cualquier .xlsx). El formato está en
plantillas/inventario_unergy.xlsx. Columnas:

    obligatorias: proyecto, departamento, nombre_cientifico, latitud, longitud
    opcionales:   municipio, fecha, familia, grupo, este, norte

Si no hay latitud/longitud pero sí este/norte, se asumen coordenadas planas
Magna-Sirgas origen nacional (EPSG:9377).

Cada árbol es una fila, pero para los indicadores se deja una sola presencia por
especie, proyecto y celda de ~5 km², para no inflar el esfuerzo de muestreo.
"""
import re
import unicodedata
from pathlib import Path

import h3
import pandas as pd

OBLIGATORIAS = ["proyecto", "departamento", "nombre_cientifico"]
# Nombres alternativos de columnas que se aceptan (ya normalizados: minúsculas, sin tildes)
ALIAS = {
    "coordenada_x": "longitud", "x": "longitud", "lon": "longitud", "long": "longitud",
    "coordenada_y": "latitud", "y": "latitud", "lat": "latitud",
    "especie": "nombre_cientifico", "nombre_cientifico_": "nombre_cientifico",
}
RESOLUCIONES_H3 = [5, 6, 7]


def normalizar_texto(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s).strip().lower()


def normalizar_especie(nombre):
    """Devuelve 'Genero especie' o None si no está identificado a especie."""
    if pd.isna(nombre):
        return None
    partes = re.sub(r"\s+", " ", str(nombre)).strip().split(" ")
    if len(partes) < 2 or partes[1].lower().rstrip(".") in ("sp", "spp", "cf", "aff", "indet"):
        return None
    return f"{partes[0].capitalize()} {partes[1].lower()}"


def _planas_a_geograficas(este, norte):
    from pyproj import Transformer
    t = Transformer.from_crs("EPSG:9377", "EPSG:4326", always_xy=True)
    lon, lat = t.transform(este.to_numpy(float), norte.to_numpy(float))
    return lat, lon


def cargar_unergy(carpeta):
    """Lee todos los Excel de la carpeta. Devuelve (registros, avisos)."""
    carpeta = Path(carpeta)
    archivos = sorted(p for p in carpeta.glob("*.xlsx") if not p.name.startswith("~$"))
    avisos = []
    if not archivos:
        return None, avisos

    tablas = []
    for arch in archivos:
        try:
            t = pd.read_excel(arch)
        except Exception as e:  # archivo dañado o abierto
            avisos.append(f"{arch.name}: no se pudo leer ({e}).")
            continue
        t.columns = [normalizar_texto(c).replace(" ", "_") for c in t.columns]
        t = t.rename(columns={c: ALIAS[c] for c in t.columns if c in ALIAS and ALIAS[c] not in t.columns})
        # Si "latitud/longitud" en realidad son coordenadas planas (valores grandes), pasarlas a este/norte
        if {"latitud", "longitud"} <= set(t.columns) and "este" not in t.columns:
            lon_num = pd.to_numeric(t["longitud"], errors="coerce")
            if lon_num.abs().median() > 180:
                t = t.rename(columns={"longitud": "este", "latitud": "norte"})
        faltan = [c for c in OBLIGATORIAS if c not in t.columns]
        tiene_geo = {"latitud", "longitud"} <= set(t.columns)
        tiene_planas = {"este", "norte"} <= set(t.columns)
        if faltan or not (tiene_geo or tiene_planas):
            avisos.append(f"{arch.name}: faltan columnas "
                          f"{', '.join(faltan) or 'latitud/longitud (o este/norte)'}.")
            continue
        t["archivo"] = arch.name
        tablas.append(t)

    if not tablas:
        return None, avisos

    df = pd.concat(tablas, ignore_index=True)
    n0 = len(df)

    # Coordenadas
    for c in ("latitud", "longitud", "este", "norte"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    if "latitud" not in df.columns:
        df["latitud"] = pd.NA
        df["longitud"] = pd.NA
    if {"este", "norte"} <= set(df.columns):
        faltan_geo = df["latitud"].isna() & df["este"].notna() & df["norte"].notna()
        if faltan_geo.any():
            lat, lon = _planas_a_geograficas(df.loc[faltan_geo, "este"], df.loc[faltan_geo, "norte"])
            df.loc[faltan_geo, "latitud"] = lat
            df.loc[faltan_geo, "longitud"] = lon
    df["latitud"] = pd.to_numeric(df["latitud"], errors="coerce")
    df["longitud"] = pd.to_numeric(df["longitud"], errors="coerce")
    ok_coord = df["latitud"].between(-5, 14) & df["longitud"].between(-80, -66)
    if (~ok_coord).sum():
        avisos.append(f"{(~ok_coord).sum()} filas sin coordenadas válidas (descartadas).")
    df = df[ok_coord]

    # Especie
    df["species"] = df["nombre_cientifico"].map(normalizar_especie)
    sin_sp = df["species"].isna().sum()
    if sin_sp:
        avisos.append(f"{sin_sp} filas no identificadas a especie (no cuentan para riqueza).")
    df = df.dropna(subset=["species"])

    # Año
    df["year"] = (pd.to_datetime(df["fecha"], errors="coerce", dayfirst=True).dt.year
                  if "fecha" in df.columns else pd.NA)

    salida = pd.DataFrame({
        "gbifID": pd.NA,
        "species": df["species"],
        "family": df["familia"] if "familia" in df.columns else pd.NA,
        "order": pd.NA,
        "class": pd.NA,
        "grupo": df["grupo"].fillna("Plantas") if "grupo" in df.columns else "Plantas",
        "year": pd.array(df["year"], dtype="Int64"),
        "lat": df["latitud"].astype(float),
        "lon": df["longitud"].astype(float),
        "fuente": "Unergy",
        "institutionCode": df["proyecto"].astype(str),
        "basisOfRecord": "INVENTARIO_UNERGY",
        "departamento": df["departamento"].map(normalizar_texto),
    })
    for r in RESOLUCIONES_H3:
        salida[f"h3_{r}"] = [h3.latlng_to_cell(la, lo, r) for la, lo in zip(salida["lat"], salida["lon"])]

    # Una presencia por especie, proyecto y celda fina
    salida = salida.drop_duplicates(subset=["species", "institutionCode", "h3_7"])
    avisos.insert(0, f"{len(archivos)} archivo(s), {n0} filas leídas, "
                     f"{len(salida)} presencias únicas de especie usadas.")
    return salida, avisos
