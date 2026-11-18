"""
04 - Lista oficial de especies amenazadas de Colombia (Res. 0126 de 2024, MADS) en CSV.

La Resolución dice (Art. 1, parágrafo 2) que sus anexos se publican en el SiB Colombia.
Ese listado está publicado como dataset:
    MADS (2024). Lista de especies silvestres amenazadas de la diversidad biológica
    continental y marino-costera de Colombia - Resolución 0126 de 2024. v1.0.
    https://doi.org/10.15472/frowz3

Este script lo busca por su DOI en la API de GBIF, descarga el archivo Darwin Core
y arma un CSV con todas las especies (flora y fauna) y su categoría. No necesita cuenta.

Correr desde la carpeta del repo:
    pip install requests pandas
    python pipeline/04_lista_mads_0126.py

Salida: data/listas/amenazadas_mads_0126.csv
        (species, nombre_cientifico, categoria, reino, clase, orden, familia)
"""
import io
import os
import re
import sys
import xml.etree.ElementTree as ET
import zipfile

import pandas as pd
import requests

DOI = "10.15472/frowz3"
SALIDA = "data/listas/amenazadas_mads_0126.csv"
API = "https://api.gbif.org/v1"


def categoria(valor):
    """Normaliza la categoría (CR, EN, VU) desde códigos o texto."""
    v = str(valor).strip().lower()
    if not v or v == "nan":
        return None
    if re.search(r"\bcr\b|cr[ií]tic", v):
        return "CR"
    if re.search(r"\ben\b|en peligro|endangered", v):
        return "EN"
    if re.search(r"\bvu\b|vulnerable", v):
        return "VU"
    return None


def buscar_archivo_dwca():
    r = requests.get(f"{API}/dataset/doi/{DOI}", timeout=60)
    r.raise_for_status()
    res = r.json().get("results", [])
    if not res:
        sys.exit(f"No encontré el dataset con DOI {DOI} en GBIF.")
    clave = res[0]["key"]
    print(f"Dataset: {res[0].get('title')}  ({clave})")
    ds = requests.get(f"{API}/dataset/{clave}", timeout=60).json()
    urls = [e["url"] for e in ds.get("endpoints", []) if e.get("type") == "DWC_ARCHIVE"]
    if not urls:
        sys.exit("El dataset no tiene un archivo Darwin Core descargable.")
    return urls[0]


def leer_dwca(contenido):
    """Devuelve (core, extensiones) como DataFrames con nombres de término Darwin Core."""
    z = zipfile.ZipFile(io.BytesIO(contenido))
    meta = ET.fromstring(z.read("meta.xml"))
    ns = {"d": "http://rs.tdwg.org/dwc/text/"}
    tablas = []
    for nodo in meta.findall("d:core", ns) + meta.findall("d:extension", ns):
        archivo = nodo.find("d:files/d:location", ns).text
        sep = nodo.get("fieldsTerminatedBy", "\\t").encode().decode("unicode_escape")
        saltar = int(nodo.get("ignoreHeaderLines", "0"))
        campos = {int(f.get("index")): f.get("term").rsplit("/", 1)[-1]
                  for f in nodo.findall("d:field", ns) if f.get("index") is not None}
        id_nodo = nodo.find("d:id", ns) if nodo.tag.endswith("core") else nodo.find("d:coreid", ns)
        idx_id = int(id_nodo.get("index")) if id_nodo is not None else 0
        df = pd.read_csv(z.open(archivo), sep=sep, header=None, skiprows=saltar, dtype=str,
                         quoting=3, on_bad_lines="skip", encoding="utf-8")
        df = df.rename(columns={**campos, idx_id: "_id"})
        tablas.append((nodo.tag.endswith("core"), archivo, df))
    core = next(t[2] for t in tablas if t[0])
    ext = [(t[1], t[2]) for t in tablas if not t[0]]
    return core, ext


def main():
    url = buscar_archivo_dwca()
    print(f"Descargando {url}")
    r = requests.get(url, timeout=300)
    r.raise_for_status()
    core, ext = leer_dwca(r.content)
    print(f"Taxones en el archivo: {len(core):,}; extensiones: {[e[0] for e in ext]}")

    # Buscar la categoría: threatStatus en alguna extensión, o en el core
    cat = pd.Series(dtype=object)
    for nombre, df in ext:
        if "threatStatus" in df.columns:
            c = df.assign(cat=df["threatStatus"].map(categoria)).dropna(subset=["cat"])
            cat = c.groupby("_id")["cat"].first()
            print(f"Categoría tomada de {nombre} (threatStatus)")
            break
    if cat.empty:
        for col in ["threatStatus", "taxonRemarks", "dynamicProperties"]:
            if col in core.columns:
                c = core.assign(cat=core[col].map(categoria)).dropna(subset=["cat"])
                if len(c):
                    cat = c.set_index("_id")["cat"]
                    print(f"Categoría tomada de la columna {col}")
                    break
    if cat.empty:
        sys.exit("No encontré la categoría de amenaza en el archivo. Revisa las columnas:\n"
                 + str(core.columns.tolist()) + "\n" + str([(n, d.columns.tolist()) for n, d in ext]))

    nombre_col = "scientificName" if "scientificName" in core.columns else core.columns[1]
    out = pd.DataFrame({
        "nombre_cientifico": core[nombre_col],
        "categoria": core["_id"].map(cat),
        "reino": core.get("kingdom"), "clase": core.get("class"),
        "orden": core.get("order"), "familia": core.get("family"),
    }).dropna(subset=["categoria"])
    # Nombre binomial para cruzar con los registros
    out.insert(0, "species", out["nombre_cientifico"].str.extract(r"^([A-Z][a-z]+ [a-z\-]+)")[0])
    out = out.dropna(subset=["species"]).drop_duplicates("species")

    os.makedirs(os.path.dirname(SALIDA), exist_ok=True)
    out.to_csv(SALIDA, index=False)
    print(f"\nEspecies: {len(out):,}")
    print(out["categoria"].value_counts().to_string())
    if "clase" in out:
        print("\nPor clase (top 15):")
        print(out["clase"].fillna("Sin dato").value_counts().head(15).to_string())
    print(f"\nGuardado en {SALIDA}")


if __name__ == "__main__":
    main()
