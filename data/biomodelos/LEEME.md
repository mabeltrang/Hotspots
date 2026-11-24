# Modelos de BioModelos en Hotspots

Esta carpeta es el repositorio permanente de los modelos de distribución de BioModelos
(Instituto Humboldt). La app los detecta y procesa sola: **no hay que editar código ni scripts
para agregar especies.**

```
data/biomodelos/
├── entrada/       ← AQUÍ se suben los GeoTIFF originales (o ZIP con varios). No se modifican nunca.
├── metadata/
│   └── metadatos_modelos.csv   ← lo único que se edita a mano: especie, validación, licencia…
├── catalogo/
│   └── catalogo_modelos.csv    ← se regenera solo (no editar): parte técnica + metadatos
└── procesados/    ← resultados (no editar)
    ├── vistas/MODELO_001.png               vista previa de todo el modelo
    └── cesar/biomodelos_h3.parquet         presencia por hexágono H3 en el departamento
```

## Cómo subir modelos (paso a paso en GitHub)

1. Abre el repositorio en GitHub y entra a la carpeta **`data` → `biomodelos` → `entrada`**.
   Ruta completa: `Hotspots/data/biomodelos/entrada/`
2. Botón **Add file → Upload files**.
3. Arrastra los GeoTIFF **sueltos, con su nombre original** (no hace falta renombrarlos ni
   meterlos en un ZIP). Se aceptan `.tif`, `.tiff`, `.geotiff` y `.zip` con GeoTIFF adentro.
4. Abajo, **Commit changes**.
5. En Streamlit Cloud: **Manage app → Reboot app** (o espera a que se actualice sola).
   Al abrir la pestaña *BioModelos* aparece «Se incorporaron N modelo(s) nuevo(s)».
6. Revisa la vista **Catálogo** de la pestaña: los que digan *Pendiente de identificación* se
   ven en el mapa pero no cuentan en la riqueza hasta que les pongas el nombre (ver abajo).

Los siguientes modelos se suben igual: siempre a la misma carpeta `entrada/`. Los que ya
estaban no se vuelven a procesar.

**Sueltos o en ZIP.** Mejor sueltos: GitHub los muestra, y el navegador de GitHub no deja subir
archivos de más de 25 MB (un ZIP con muchos modelos puede pasarse). El ZIP sirve si Humboldt
entrega un lote; se lee sin descomprimirlo.

## Ejemplo con los dos archivos de prueba

| Archivo original | ID asignado | Especie | Cómo se supo | Tipo | En Cesar |
|---|---|---|---|---|---|
| `AE-9.geotiff` | MODELO_001 | *Quercus humboldtii* | Descripción interna de la banda: `Quercus_humboldtii_N2` | Binario (solo presencia) | Sí, 133 hexágonos de ≈5 km² |
| `BST-477.geotiff` | MODELO_002 | *Ceroxylon quindiuense* | Descripción interna de la banda: `Ceroxylon quindiuense_veg` | Binario (solo presencia) | No (sin presencia en el departamento) |

Ambos: EPSG:4326 (WGS84), extensión -83 a -60 de longitud y -14 a 13 de latitud (Colombia
continental), píxel de 30 segundos de arco (≈0,93 km), una banda. `AE-9` es float32 con NoData = NaN;
`BST-477` es int16 con NoData = -32768. En los dos el único valor es **1**: 1 = dentro de la
distribución, NoData = fuera. No hay ceros, así que no distinguen «ausencia modelada» de «fuera del
área modelada». Los sufijos `N2` y `veg` se guardan en la columna `etiqueta_interna`; `N2`
probablemente es *nivel 2*, pero no se llena `nivel_modelo` hasta confirmarlo en la ficha.

## Cómo identificar un modelo o completar sus datos

1. En GitHub abre `data/biomodelos/metadata/metadatos_modelos.csv` y pulsa el lápiz.
2. Busca la fila por `id_modelo` (por ejemplo `MODELO_003`) y escribe lo que sepas, separado por comas.
3. **Commit changes** y reinicia la app.

Lo que dejes vacío queda como «sin dato»: nada se inventa. El nombre escrito aquí manda sobre el
que el sistema haya detectado en el archivo.

| Columna | Qué poner |
|---|---|
| `nombre_cientifico` | *Género especie* (p. ej. `Quercus humboldtii`). Obligatorio para que cuente en la riqueza. |
| `familia` | Familia botánica o zoológica. |
| `nivel_modelo` | Nivel de BioModelos: `1` (climático) o `2` (remanente, recortado a coberturas). |
| `estado_validacion` | `Validado` si la ficha lo dice; otro texto si no (p. ej. `Estadístico`, `Publicado`). |
| `calificacion_expertos` | Número de la ficha (1 a 5). |
| `licencia`, `fuente`, `version_fecha`, `cita` | Como aparecen en la ficha o en lo que envíe Humboldt. |
| `umbral` | Solo modelos **continuos**: valor desde el cual se considera presencia (lo indica la ficha). Sin umbral, el modelo continuo se puede ver pero no se suma. |
| `usar_en_analisis` | `si`: este es el modelo principal de su especie. `no`: excluirlo. Vacío: lo decide el sistema. |
| `taxid_biomodelos`, `id_modelo_biomodelos` | Identificadores de BioModelos (para cuando haya acceso a la API). |
| `notas` | Libre. |

No borres ni cambies `id_modelo`, `huella` ni `archivo_original`: así se reconoce cada archivo.

## Cómo trata el sistema cada modelo

- **Identidad por contenido.** Cada archivo se reconoce por su huella (SHA-256). Si el mismo modelo
  llega dos veces, renombrado o dentro de un ZIP, se procesa una sola vez y la copia se anota en
  `archivos_identicos`.
- **Nombre de la especie.** Se busca, en orden: metadatos (lo que escribas tú), etiquetas internas
  del GeoTIFF, descripción de la banda y nombre del archivo. Si no aparece, el modelo queda como
  `MODELO_###` *pendiente de identificación*: se ve en el mapa y en el catálogo, pero no suma.
- **Tipo de valores** (no se convierte nada a binario sin justificación):
  - *Binario (solo presencia)*: un único valor; todo píxel con dato es presencia (caso de BioModelos).
  - *Binario (presencia/ausencia)*: valores 0 y 1.
  - *Continuo 0-1* (idoneidad o probabilidad) o *continuo (otra escala)*: se muestra el valor; solo
    suma a la riqueza si `umbral` está lleno.
  - *Categórico*: varias clases enteras; se ve, pero no suma hasta revisar qué significa cada clase.
- **NoData.** Se usa el declarado en el archivo; si no hay, se trata como NoData -9999, -32768 o
  -3.4e38 cuando aparecen, y se registra en `tratamiento_nodata`.
- **Otros sistemas de coordenadas y resoluciones.** Se aceptan: cada píxel se ubica por su centro
  en WGS84 y se asigna a su hexágono. Si el píxel es más grande que el hexágono de res. 7, el
  catálogo lo advierte (usar res. 5 o 6).
- **Recorte al departamento.** Solo se procesan los píxeles cuyo centro cae dentro del departamento
  (límites DANE). La vista previa conserva la extensión completa del modelo.
- **Varios modelos de la misma especie.** Cuenta uno solo, el *principal*: el marcado con
  `usar_en_analisis = si`; si no hay, el validado, luego el de mayor nivel, mayor calificación y
  versión más reciente.
- **Archivos dañados.** Quedan en el catálogo con estado *Error* y no detienen a los demás. Tras
  reemplazarlos, correr `python pipeline/08_biomodelos.py --forzar` o subir el archivo corregido
  (si cambia el contenido, entra como modelo nuevo).

## ¿Guardar los GeoTIFF en GitHub o afuera?

Los modelos de BioModelos son livianos porque vienen comprimidos (LZW): los de prueba pesan 1,2 MB
y 0,6 MB. Diez modelos ocupan unos 10 MB; trescientos, del orden de 300 MB.

- **Hasta unos cientos de modelos (≈500 MB en total): directamente en el repositorio**, sin Git LFS.
  Es lo más simple, funciona con la subida por navegador y Streamlit Cloud los lee sin
  configuración extra. Ningún archivo individual debe pasar de 25 MB (límite del navegador de GitHub).
- **Si el total crece mucho más**: dejar los originales en Google Drive y subir a GitHub solo
  `metadata/`, `catalogo/` y `procesados/` (lo que la app necesita para mostrar los mapas). Para
  procesar los nuevos, correr `python pipeline/08_biomodelos.py` en Colab con la carpeta `entrada/`
  apuntando a Drive. La app sigue mostrando los modelos aunque el original ya no esté en `entrada/`
  (estado *Procesado (original fuera de entrada/)*).
- Git LFS no se recomienda aquí: complica la subida por navegador y tiene cuota de transferencia.

**Privacidad y licencias.** El repositorio debe ser privado: contiene inventarios de Unergy y estos
modelos tienen licencia propia (columna `licencia`), que puede no permitir redistribuirlos.

## Cuando llegue la API de BioModelos

Los campos de la API (taxID, ID del modelo, nivel, estado, licencia, cita) caben en las columnas de
`metadatos_modelos.csv`. Un script que los llene solo necesita escribir en ese archivo; el resto
del sistema no cambia.
