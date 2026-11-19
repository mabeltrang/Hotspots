// Covariables para los modelos de distribución (pipeline/06), a ~1 km.
// Pegar en el Code Editor de Google Earth Engine, correr y darle RUN a la tarea en Tasks.
// El GeoTIFF llega a Google Drive (carpeta HOTSPOTS_SDM).
//
// Variables (pocas y poco correlacionadas, para no sobreajustar):
//   bio01  temperatura media anual (°C x 10)          WorldClim 1.4
//   bio04  estacionalidad de la temperatura           WorldClim 1.4
//   bio12  precipitación anual (mm)                    WorldClim 1.4
//   bio15  estacionalidad de la precipitación          WorldClim 1.4
//   bio17  precipitación del trimestre más seco (mm)   WorldClim 1.4
//   elevacion (m) y pendiente (°)                      SRTM 30 m
//   cobertura_arborea, arbustal, herbazal, humedal, agua
//          fracción de cada cobertura en el km²           ESA WorldCover 2021 (clases 10, 20, 30, 90, 80)
// Las coberturas son las ACTUALES: el modelo no predice hábitat de bosque en potreros. La banda
// "agua" no se usa como variable: sirve para quitar los hexágonos que son ciénaga o río.

// ================== CONFIGURACIÓN (editar) ==================
var DEPTO = 'Cesar';   // como aparece en FAO GAUL (ADM1_NAME)
var NOMBRE = 'cesar';  // nombre del archivo: covariables_<NOMBRE>.tif
var CARPETA_DRIVE = 'HOTSPOTS_SDM';
var ESCALA = 1000;     // metros
// =============================================================

var region = ee.FeatureCollection('FAO/GAUL/2015/level1')
  .filter(ee.Filter.eq('ADM0_NAME', 'Colombia'))
  .filter(ee.Filter.eq('ADM1_NAME', DEPTO))
  .geometry().buffer(10000).bounds();

var clima = ee.Image('WORLDCLIM/V1/BIO').select(['bio01', 'bio04', 'bio12', 'bio15', 'bio17']);
var dem = ee.Image('USGS/SRTMGL1_003');
var elevacion = dem.rename('elevacion');
var pendiente = ee.Terrain.slope(dem).rename('pendiente');
var worldcover = ee.ImageCollection('ESA/WorldCover/v200').first();
function fraccion(clase, nombre) {
  return worldcover.eq(clase)
    .reduceResolution({reducer: ee.Reducer.mean(), maxPixels: 65535})
    .reproject({crs: 'EPSG:4326', scale: ESCALA})
    .rename(nombre);
}
var coberturas = fraccion(10, 'cobertura_arborea').addBands(fraccion(20, 'arbustal'))
  .addBands(fraccion(30, 'herbazal')).addBands(fraccion(90, 'humedal')).addBands(fraccion(80, 'agua'));

var cov = clima.addBands(elevacion).addBands(pendiente).addBands(coberturas).toFloat().clip(region);
Map.centerObject(region, 8);
Map.addLayer(cov.select('cobertura_arborea'), {min: 0, max: 1, palette: ['f5f0e1', '1b5e20']}, 'Cobertura arbórea');
Map.addLayer(cov.select('agua'), {min: 0, max: 1, palette: ['ffffff', '0d47a1']}, 'Agua', false);
print('Bandas (deben ser 12):', cov.bandNames());

Export.image.toDrive({
  image: cov,
  description: 'covariables_' + NOMBRE,
  folder: CARPETA_DRIVE,
  fileNamePrefix: 'covariables_' + NOMBRE,
  region: region,
  scale: ESCALA,
  crs: 'EPSG:4326',
  maxPixels: 1e10
});
