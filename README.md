# Hotspots de biodiversidad

Mapa de registros de biodiversidad por departamento, en hexágonos H3.
Fuentes: GBIF (ya procesado para Cesar) e inventarios de Unergy (Excel).

Cita de los datos de Cesar:
GBIF.org (18 November 2026) GBIF Occurrence Download https://doi.org/10.15468/dl.wafy35

## Ver la app

```
pip install -r requirements.txt
streamlit run app.py
```

En Streamlit Cloud: Main file path = `app.py`.

## Agregar inventarios de Unergy

1. Llenar `plantillas/inventario_unergy.xlsx` (borrar las filas de ejemplo).
2. Guardar el Excel en `data/unergy/` (pueden ser varios archivos).
3. Subir el cambio a GitHub. La app los lee sola, no hay que correr ningún script.

## Actualizar GBIF o agregar otro departamento

1. Descargar de GBIF (formato Simple) y guardar el CSV o zip en `data/crudos/`
   (esa carpeta no se sube a GitHub; el CSV de Cesar pesa 256 MB).
2. Editar `DEPTO`, `RUTA_DESCARGA` y `DOI` al inicio de `pipeline/01_procesar_gbif.py`.
3. Correr `python pipeline/01_procesar_gbif.py` desde la carpeta del repo.
4. Subir `data/procesados/<departamento>/` a GitHub.

## Licencias

Cerca del 43 % de los registros GBIF de Cesar son CC BY-NC (uso no comercial).
Para análisis interno no hay problema; para productos comerciales, filtrar por licencia.

Límites municipales: DANE, Marco Geoestadístico Nacional 2018 (`data/limites/`).
