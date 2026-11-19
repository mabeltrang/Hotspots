"""Constantes compartidas entre la app y los scripts del pipeline."""

# Grupo de GBIF o de la plantilla -> grupo que muestra la app
GRUPOS = {
    "Aves": "Aves", "Mamíferos": "Mamíferos",
    "Reptiles": "Herpetos", "Anfibios": "Herpetos", "Herpetos": "Herpetos",
    "Insectos": "Invertebrados", "Arácnidos": "Invertebrados",
    "Otros invertebrados": "Invertebrados", "Invertebrados": "Invertebrados",
    "Plantas": "Flora", "Flora": "Flora",
}
ORDEN_GRUPOS = ["Aves", "Mamíferos", "Herpetos", "Invertebrados", "Flora"]


def aplicar_sinonimos(df, carpeta_listas):
    """Unifica nombres con data/listas/sinonimos.csv (columnas nombre, aceptado).

    Así una especie que una fuente llama distinto (p. ej. Prosopis juliflora en las resoluciones
    y Neltuma juliflora en GBIF) cuenta como una sola.
    """
    import pandas as pd
    from pathlib import Path

    ruta = Path(carpeta_listas) / "sinonimos.csv"
    if not ruta.exists():
        return df
    s = pd.read_csv(ruta)
    mapa = dict(zip(s["nombre"].str.strip(), s["aceptado"].str.strip()))
    return df.assign(species=df["species"].replace(mapa))
