"""
02 - Origen de las especies (endémica / nativa / introducida) usando la API de GBIF.

No necesita cuenta ni volver a descargar registros: toma la lista de especies de
data/procesados/<DEPTO>/registros.parquet y pregunta a GBIF, especie por especie,
qué dicen las listas de distribución sobre Colombia (GRIIS Colombia, Catálogo de
Plantas de Colombia, etc.).

Correr desde la carpeta del repo:
    pip install requests
    python pipeline/02_origen_especies.py

Tarda unos minutos. Si se interrumpe, al volver a correrlo sigue donde iba.
Salida: data/listas/origen_<DEPTO>.csv

Correcciones manuales: crea data/listas/correcciones_origen.csv con columnas
species,origen (Endémica, Nativa o Introducida). La app las aplica por encima.
"""
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import requests

# ================== CONFIGURACIÓN ==================
DEPTO = "cesar"
HILOS = 6           # consultas en paralelo (no subir mucho, GBIF limita)
# ===================================================

API = "https://api.gbif.org/v1"
ENTRADA = f"data/procesados/{DEPTO}/registros.parquet"
SALIDA = f"data/listas/origen_{DEPTO}.csv"
INTRODUCIDA = {"INTRODUCED", "NATURALISED", "NATURALIZED", "INVASIVE", "MANAGED", "ASSISTED COLONISATION"}

sesion = requests.Session()


def pedir(url, params=None, intentos=4):
    for i in range(intentos):
        try:
            r = sesion.get(url, params=params, timeout=30)
            if r.status_code == 200:
                return r.json()
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(2 ** i)
                continue
            return None
        except requests.RequestException:
            time.sleep(2 ** i)
    return None


def es_colombia(d):
    return d.get("country") == "CO" or "colombia" in str(d.get("locality", "")).lower()


def clasificar(especie, reino):
    m = pedir(f"{API}/species/match", {"name": especie, "kingdom": reino, "strict": "false"})
    if not m or "usageKey" not in m:
        return {"species": especie, "gbif_key": None, "origen": "Sin dato", "fuente_origen": "sin match"}
    clave = m.get("acceptedUsageKey") or m["usageKey"]

    dists, offset = [], 0
    while True:
        p = pedir(f"{API}/species/{clave}/distributions", {"limit": 500, "offset": offset})
        if not p:
            break
        dists += p.get("results", [])
        if p.get("endOfRecords", True):
            break
        offset += 500

    co = [d for d in dists if es_colombia(d)]
    medios = {str(d.get("establishmentMeans", "")).upper() for d in co}
    texto = " ".join(f"{d.get('status', '')} {d.get('remarks', '')} {d.get('locality', '')}" for d in co).lower()
    endemica = any(str(d.get("endemic", "")).lower() == "true" for d in co) or "endém" in texto or "endem" in texto

    if medios & INTRODUCIDA:
        origen = "Introducida"
    elif endemica:
        origen = "Endémica"
    elif "NATIVE" in medios:
        origen = "Nativa"
    else:
        origen = "Sin dato"
    fuentes = sorted({str(d.get("source", ""))[:80] for d in co if d.get("source")})
    return {"species": especie, "gbif_key": clave, "origen": origen,
            "fuente_origen": " | ".join(fuentes[:3])}


def main():
    reg = pd.read_parquet(ENTRADA, columns=["species", "grupo"])
    especies = (reg.drop_duplicates("species")
                   .assign(reino=lambda d: d["grupo"].map({"Plantas": "Plantae", "Hongos": "Fungi"}).fillna("Animalia")))

    hechas = pd.DataFrame()
    if os.path.exists(SALIDA):
        hechas = pd.read_csv(SALIDA)
        especies = especies[~especies["species"].isin(hechas["species"])]
    print(f"Especies por consultar: {len(especies):,} (ya hechas: {len(hechas):,})")

    os.makedirs(os.path.dirname(SALIDA), exist_ok=True)
    resultados = []
    with ThreadPoolExecutor(HILOS) as ex:
        futuros = {ex.submit(clasificar, r.species, r.reino): r.species for r in especies.itertuples()}
        for i, f in enumerate(as_completed(futuros), 1):
            resultados.append(f.result())
            if i % 200 == 0 or i == len(futuros):
                parcial = pd.concat([hechas, pd.DataFrame(resultados)], ignore_index=True)
                parcial.to_csv(SALIDA, index=False)
                print(f"  {i:,}/{len(futuros):,}")

    final = pd.concat([hechas, pd.DataFrame(resultados)], ignore_index=True)
    final.to_csv(SALIDA, index=False)
    print("\nResultado:")
    print(final["origen"].value_counts().to_string())
    print(f"\nGuardado en {SALIDA}")


if __name__ == "__main__":
    main()
