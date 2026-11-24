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

`requirements.txt` incluye `rasterio` (lectura de GeoTIFF) y `pillow` (vistas previas); en
Streamlit Cloud se instalan solos.

En Streamlit Cloud: Main file path = `app.py`.

## Agregar inventarios de Unergy

1. Llenar `plantillas/inventario_unergy.xlsx` (borrar las filas de ejemplo).
2. Guardar el Excel en `data/unergy/` (pueden ser varios archivos).
3. Subir el cambio a GitHub. La app los lee sola, no hay que correr ningún script.

## Origen de las especies (endémica / nativa / introducida)

1. Desde la carpeta del repo: `pip install requests` y `python pipeline/02_origen_especies.py`
   (consulta la API de GBIF, no necesita cuenta; tarda unos minutos).
2. Subir a GitHub `data/listas/origen_cesar.csv`.
3. Correcciones manuales en `data/listas/correcciones_origen.csv` (species, origen).

## Estado de amenaza (UICN y MADS)

- `data/listas/amenazadas_mads_flora.csv`: Res. 0126 de 2024, árboles y palmas (ya incluido).
- `python pipeline/04_lista_mads_0126.py` descarga la lista oficial completa de la Res. 0126 de 2024
  (flora y fauna) desde el SiB/GBIF (https://doi.org/10.15472/frowz3) y guarda
  `data/listas/amenazadas_mads_0126.csv`, que reemplaza a la de árboles y palmas.
- `python pipeline/03_amenaza_especies.py` (después del 02) trae la categoría UICN y el estado
  nacional que reportan las listas de GBIF; guarda `data/listas/amenaza_cesar.csv`.

## Capas de conservación (áreas protegidas, Ley 2ª, POMCA)

1. Descargar el shapefile del RUNAP (https://runap.parquesnacionales.gov.co) y, si se quiere,
   otras capas (reservas de Ley 2ª, zonificación de POMCA de la CAR). Guardarlas en `data/crudos/`
   como .zip (con .shp, .shx, .dbf y .prj), GeoJSON, KML o KMZ.
2. Editar `DEPTO` y `CAPAS` al inicio de `pipeline/05_capas_conservacion.py` (nombre del archivo y
   de los campos con el nombre y la categoría de cada área; si no los sabes, el script los imprime).
3. `pip install pyshp shapely pyproj` y `python pipeline/05_capas_conservacion.py`.
4. Subir `data/capas/<departamento>/` a GitHub.

Atajo con Google Earth Engine: si las capas ya están como assets en GEE (RUNAP, Ley 2ª, REAA,
OMEC, bosque seco), pegar `pipeline/gee_exportar_capas.js` en el Code Editor, correr las
exportaciones y guardar los GeoJSON que llegan a Drive directamente en `data/capas/<departamento>/`
(no hace falta el paso 3).

Con las capas, la app calcula el % protegido de cada hotspot y marca los vacíos de conservación
(hotspots con menos del 10 % de su área dentro de las capas).

## Métricas de riqueza

- Rarefactada (por defecto): especies esperadas en el mínimo de registros de Opciones avanzadas
  (Hurlbert 1971). Es la que corrige la diferencia de muestreo entre unidades.
- Chao1: riqueza estimada (Chao 1987). Observada: sin corrección.

## Consulta por predio

Pestaña para subir el polígono de un predio (KMZ, KML, GeoJSON o shapefile en .zip) y obtener las
especies registradas en un radio, amenazadas y endémicas, distancia al hotspot más cercano, áreas
de conservación que toca y un texto de resumen para el informe.

## Modelos de distribución de especies

1. En Google Earth Engine, correr `pipeline/gee_covariables.js` y darle RUN a la tarea: llega
   `covariables_cesar.tif` a Google Drive (carpeta HOTSPOTS_SDM).
2. En Colab, correr el script de modelos (clona el repo, lee el GeoTIFF de Drive y ejecuta
   `pipeline/06_modelos_distribucion.py`).
3. Subir a `data/procesados/cesar/` los tres archivos que descarga: `sdm_idoneidad.parquet`,
   `sdm_metricas.csv` y `sdm_info.json`.

La app muestra la pestaña *Modelos propios* (riqueza modelada y mapa por especie) y, en la
consulta por predio, las especies con hábitat idóneo que no tienen registros en el área. Son
modelos exploratorios: cuando una especie tiene modelo validado en BioModelos, manda ese.

## Listas completas de eBird (esfuerzo y completitud de aves)

Las presencias de eBird ya vienen en la descarga de GBIF. Este paso no agrega registros: usa las
listas completas de eBird para medir qué tan completo está el inventario de aves en cada hexágono
(Chao2) y con qué frecuencia se reporta cada especie.

1. Pedir permiso de uso a ebird@cornell.edu (los términos de eBird no permiten uso comercial sin él).
2. En https://ebird.org/data/download pedir Colombia → Cesar (CO-CES) con
   *Include sampling event data*. Guardar los dos .txt (o el .zip) en `data/crudos/`.
3. Editar `RUTA_EBD`, `RUTA_SAMPLING`, `DEPTO` y `VERSION_EBD` al inicio de `pipeline/07_ebird_listas.py`.
4. `python pipeline/07_ebird_listas.py` y subir `ebird_celdas.parquet`, `ebird_frecuencia.parquet`
   y `ebird_info.json` de `data/procesados/<departamento>/`.

## Modelos de BioModelos (Instituto Humboldt)

Mapas de distribución de BioModelos, revisados por expertos. **No hay que correr nada ni editar
código para agregar especies**: se suben los GeoTIFF a `data/biomodelos/entrada/` y la app los
detecta, los procesa y los suma a la pestaña *BioModelos*. Guía paso a paso, estructura de
carpetas y qué significa cada columna del catálogo: [`data/biomodelos/LEEME.md`](data/biomodelos/LEEME.md).

Resumen:

1. Subir los GeoTIFF (o un ZIP con varios) a `data/biomodelos/entrada/`, con su nombre original.
2. Reiniciar la app (Streamlit Cloud: *Manage app → Reboot*). Se procesan solo los archivos nuevos.
3. Completar lo que se sepa de cada modelo (especie, validación, licencia…) en
   `data/biomodelos/metadata/metadatos_modelos.csv`.

Opcional, en el computador o en Colab: `python pipeline/08_biomodelos.py` hace lo mismo que la app
y muestra qué modelos faltan por identificar; luego se suben a GitHub las carpetas
`data/biomodelos/catalogo/`, `metadata/` y `procesados/`.

La pestaña tiene dos ámbitos: **Colombia** (todo el país, con escala de color de verde = pocas
especies a rojo = muchas) y el **departamento** elegido. Muestra la riqueza potencial (especies cuyo
modelo cubre cada hexágono), cada modelo por separado (en el departamento y en toda su extensión), el cruce entre riqueza potencial y registros
(vacíos de información) y el catálogo. La consulta por predio lista además las especies con
distribución potencial en el área. Los modelos propios (*pipeline/06*) quedan como información
exploratoria y, por defecto, se ocultan para las especies con modelo validado en BioModelos.

## Actualizar GBIF o agregar otro departamento

1. Descargar de GBIF (formato Simple) y guardar el CSV o zip en `data/crudos/`
   (esa carpeta no se sube a GitHub; el CSV de Cesar pesa 256 MB).
2. Editar `DEPTO`, `RUTA_DESCARGA` y `DOI` al inicio de `pipeline/01_procesar_gbif.py`.
3. Correr `python pipeline/01_procesar_gbif.py` desde la carpeta del repo.
4. Subir `data/procesados/<departamento>/` a GitHub.

## Licencias y privacidad

El repositorio debe ser **privado**: tiene inventarios de Unergy con coordenadas de proyectos y,
desde ahora, modelos de BioModelos cuya licencia se debe respetar (ver columna `licencia`).

Cerca del 43 % de los registros GBIF de Cesar son CC BY-NC (uso no comercial).
Para análisis interno no hay problema; para productos comerciales, filtrar por licencia.

Límites municipales: DANE, Marco Geoestadístico Nacional 2018 (`data/limites/`).
