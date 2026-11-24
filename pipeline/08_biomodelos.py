"""
08 - Modelos de BioModelos (Instituto Humboldt) -> hexágonos H3 para la app.

BioModelos publica, por especie, mapas de distribución revisados y calificados por expertos.
Este script toma los que descargaste a mano y los pasa a la misma grilla H3 de la app, para
poder sumar especies por hexágono (riqueza potencial) igual que con los modelos propios (06).

Antes de correrlo:
    1. Crear cuenta en https://biomodelos.humboldt.org.co (gratis).
    2. Buscar cada especie (empezar por las amenazadas de data/listas/), entrar a su ficha y
       descargar el modelo validado/aprobado. Preferir el de nivel 2 ("remanente": recortado a
       la cobertura natural) sobre el de nivel 1 (solo clima).
    3. Guardar los archivos en data/crudos/biomodelos/ (los .zip o los .tif, como lleguen).
       El nombre del archivo debe empezar por el nombre científico: "Ateles_hybridus.tif",
       "Ateles hybridus nivel2.zip", etc. Si el nombre no es claro, renombrarlo.
    4. Anotar en data/listas/biomodelos_fichas.csv (opcional, columnas species, nivel,
       calificacion, licencia, cita) lo que dice la ficha de cada modelo, para la pestaña Método.

Correr desde la carpeta del repo:
    pip install rasterio
    python pipeline/08_biomodelos.py

Salida (en data/procesados/<DEPTO>/):
    biomodelos.parquet        h3_7, species: hexágonos donde el modelo dice "presente"
    biomodelos_resumen.csv    una fila por archivo leído: especie, celdas presentes, avisos
"""
import json
import os
import re
import sys
import tempfile
import unicodedata
import zipfile
from pathlib import Path

import h3
import numpy as np
import pandas as pd
import rasterio
from rasterio.warp import transform
from shapely.geometry import shape
from shapely.ops import unary_union

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from comun import aplicar_sinonimos  # noqa: E402

# ================== CONFIGURACIÓN (editar) ==================
DEPTO = "cesar"
CARPETA_MODELOS = Path("data/crudos/biomodelos")
LIMITES = Path("data/limites/municipios_colombia.geojson")
SALIDA = Path(f"data/procesados/{DEPTO}")
RESOLUCION = 7         # la misma de los modelos propios (sdm_idoneidad.parquet)
UMBRAL = 0             # valor del raster por encima del cual se toma como presencia.
                       # Los modelos aprobados de BioModelos son binarios (1 = presente);
                       # si alguno llega continuo (0-1), subir este valor o revisar su ficha.
# ============================================================


def normalizar(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return s.upper().strip()


def celdas_departamento():
    """Hexágonos H3 que cubren el departamento y su centro (lat, lon)."""
    datos = json.loads(LIMITES.read_text(encoding="utf-8"))
    geoms = [shape(f["geometry"]) for f in datos["features"]
             if normalizar(f["properties"]["DPTO_CNMBR"]) == normalizar(DEPTO)]
    if not geoms:
        sys.exit(f"No encontré el departamento {DEPTO} en {LIMITES}")
    celdas = sorted(h3.geo_to_cells(unary_union(geoms).__geo_interface__, RESOLUCION))
    centros = [h3.cell_to_latlng(c) for c in celdas]
    return celdas, np.array([c[0] for c in centros]), np.array([c[1] for c in centros])


def especie_del_nombre(nombre):
    """'Ateles_hybridus_nivel2.tif' -> 'Ateles hybridus'."""
    partes = re.split(r"[\s_\-.]+", Path(nombre).stem)
    if len(partes) < 2 or not partes[0][:1].isupper() or not partes[1].islower():
        return None
    return f"{partes[0]} {partes[1]}"


def rasters():
    """Recorre la carpeta y devuelve (nombre_archivo, ruta_tif), abriendo los zip que haya."""
    tmp = Path(tempfile.mkdtemp())
    for ruta in sorted(CARPETA_MODELOS.iterdir()):
        if ruta.suffix.lower() in (".tif", ".tiff"):
            yield ruta.name, ruta
        elif ruta.suffix.lower() == ".zip":
            with zipfile.ZipFile(ruta) as z:
                tifs = [n for n in z.namelist() if n.lower().endswith((".tif", ".tiff"))]
                for n in tifs:
                    destino = tmp / f"{ruta.stem}__{Path(n).name}"
                    destino.write_bytes(z.read(n))
                    # el nombre del zip suele traer la especie; el del tif interno, a veces no
                    yield (ruta.name if len(tifs) == 1 else Path(n).name), destino


def muestrear(ruta_tif, lat, lon):
    """Valor del raster en el centro de cada hexágono (NaN fuera del raster o sin dato)."""
    with rasterio.open(ruta_tif) as r:
        xs, ys = (lon, lat) if r.crs is None or r.crs.to_epsg() == 4326 else transform("EPSG:4326", r.crs, lon, lat)
        vals = np.array([v[0] for v in r.sample(zip(xs, ys), masked=True)], dtype="float64")
        if r.nodata is not None:
            vals[vals == r.nodata] = np.nan
    return vals


def main():
    if not CARPETA_MODELOS.exists() or not any(CARPETA_MODELOS.iterdir()):
        sys.exit(f"No hay archivos en {CARPETA_MODELOS}. Descarga los modelos de BioModelos primero.")
    celdas, lat, lon = celdas_departamento()
    print(f"{len(celdas):,} hexágonos de res. {RESOLUCION} en {DEPTO.title()}")

    presencias, resumen = [], []
    for nombre, ruta in rasters():
        sp = especie_del_nombre(nombre)
        if sp is None:
            resumen.append({"archivo": nombre, "species": None, "celdas": 0,
                            "aviso": "No pude leer la especie del nombre del archivo; renómbralo."})
            continue
        try:
            vals = muestrear(ruta, lat, lon)
        except Exception as e:  # raster dañado o formato raro: se reporta y se sigue
            resumen.append({"archivo": nombre, "species": sp, "celdas": 0, "aviso": f"Error al leer: {e}"})
            continue
        validos = vals[~np.isnan(vals)]
        aviso = ""
        if len(validos) and validos.max() <= 1 and len(np.unique(validos)) > 2:
            aviso = "Parece continuo (no binario): revisa UMBRAL o descarga el modelo aprobado."
        dentro = [c for c, v in zip(celdas, vals) if not np.isnan(v) and v > UMBRAL]
        presencias += [(c, sp) for c in dentro]
        resumen.append({"archivo": nombre, "species": sp, "celdas": len(dentro),
                        "aviso": aviso or ("No cae en el departamento." if not dentro else "")})
        print(f"  {sp}: {len(dentro):,} hexágonos")

    pres = aplicar_sinonimos(pd.DataFrame(presencias, columns=[f"h3_{RESOLUCION}", "species"]), "data/listas")
    pres = pres.drop_duplicates()
    SALIDA.mkdir(parents=True, exist_ok=True)
    pres.to_parquet(SALIDA / "biomodelos.parquet", index=False)
    res = pd.DataFrame(resumen)
    res.to_csv(SALIDA / "biomodelos_resumen.csv", index=False)

    print(f"\n{pres['species'].nunique()} especies con presencia en {DEPTO.title()}, "
          f"{pres[f'h3_{RESOLUCION}'].nunique():,} hexágonos con al menos una.")
    problemas = res[res["aviso"] != ""]
    if len(problemas):
        print("\nRevisar:")
        print(problemas[["archivo", "aviso"]].to_string(index=False))
    print(f"\nGuardado en {SALIDA}")


if __name__ == "__main__":
    main()
