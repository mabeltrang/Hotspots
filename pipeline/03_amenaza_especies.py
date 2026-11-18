"""
03 - Estado de amenaza de las especies (UICN y nacional) usando la API de GBIF.

- UICN: GBIF publica la categoría de la Lista Roja de la UICN para cada especie
  (endpoint /species/{key}/iucnRedListCategory). No necesita cuenta ni token.
- Nacional (MADS): se toma el threatStatus que traen las listas de distribución para
  Colombia en GBIF. Además la app usa data/listas/amenazadas_mads_flora.csv
  (Res. 0126 de 2024, árboles y palmas), que tiene prioridad.

Usa las claves GBIF que dejó el script 02 en data/listas/origen_<DEPTO>.csv,
así que primero hay que haber corrido el 02.

Correr desde la carpeta del repo:
    pip install requests pandas
    python pipeline/03_amenaza_especies.py

Salida: data/listas/amenaza_<DEPTO>.csv  (species, uicn, mads_gbif)
"""
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import requests

DEPTO = "cesar"
HILOS = 6

API = "https://api.gbif.org/v1"
ENTRADA = f"data/listas/origen_{DEPTO}.csv"
SALIDA = f"data/listas/amenaza_{DEPTO}.csv"

CODIGOS = {
    "EXTINCT": "EX", "EXTINCT_IN_THE_WILD": "EW", "CRITICALLY_ENDANGERED": "CR",
    "ENDANGERED": "EN", "VULNERABLE": "VU", "NEAR_THREATENED": "NT",
    "LEAST_CONCERN": "LC", "DATA_DEFICIENT": "DD", "NOT_EVALUATED": "NE",
}
ORDEN = ["EX", "EW", "CR", "EN", "VU", "NT", "LC", "DD", "NE"]

sesion = requests.Session()


def pedir(url, params=None, intentos=4):
    for i in range(intentos):
        try:
            r = sesion.get(url, params=params, timeout=30)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 204:
                return {}
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(2 ** i)
                continue
            return None
        except (requests.RequestException, ValueError):
            time.sleep(2 ** i)
    return None


def a_codigo(valor):
    if not valor:
        return None
    v = str(valor).upper().replace(" ", "_")
    return v if v in ORDEN else CODIGOS.get(v)


def consultar(especie, clave):
    fila = {"species": especie, "uicn": None, "mads_gbif": None}
    if pd.isna(clave):
        return fila
    clave = int(clave)

    r = pedir(f"{API}/species/{clave}/iucnRedListCategory")
    if r:
        fila["uicn"] = a_codigo(r.get("code")) or a_codigo(r.get("category"))

    dists, offset = [], 0
    while True:
        p = pedir(f"{API}/species/{clave}/distributions", {"limit": 500, "offset": offset})
        if not p:
            break
        dists += p.get("results", [])
        if p.get("endOfRecords", True):
            break
        offset += 500
    codigos = [a_codigo(d.get("threatStatus")) for d in dists
               if (d.get("country") == "CO" or "colombia" in str(d.get("locality", "")).lower())
               and d.get("threatStatus")]
    codigos = [c for c in codigos if c]
    if codigos:
        fila["mads_gbif"] = min(codigos, key=ORDEN.index)  # la categoría más grave
    return fila


def main():
    especies = pd.read_csv(ENTRADA)[["species", "gbif_key"]].drop_duplicates("species")
    hechas = pd.DataFrame()
    if os.path.exists(SALIDA):
        hechas = pd.read_csv(SALIDA)
        especies = especies[~especies["species"].isin(hechas["species"])]
    print(f"Especies por consultar: {len(especies):,} (ya hechas: {len(hechas):,})")

    resultados = []
    with ThreadPoolExecutor(HILOS) as ex:
        futuros = [ex.submit(consultar, r.species, r.gbif_key) for r in especies.itertuples()]
        for i, f in enumerate(as_completed(futuros), 1):
            resultados.append(f.result())
            if i % 200 == 0 or i == len(futuros):
                pd.concat([hechas, pd.DataFrame(resultados)], ignore_index=True).to_csv(SALIDA, index=False)
                print(f"  {i:,}/{len(futuros):,}")

    final = pd.concat([hechas, pd.DataFrame(resultados)], ignore_index=True)
    final.to_csv(SALIDA, index=False)
    print("\nUICN:")
    print(final["uicn"].fillna("Sin dato").value_counts().to_string())
    print("\nNacional (según GBIF):")
    print(final["mads_gbif"].fillna("Sin dato").value_counts().to_string())
    print(f"\nGuardado en {SALIDA}")


if __name__ == "__main__":
    main()
