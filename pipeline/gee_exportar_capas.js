// Exportar capas de conservación recortadas a un departamento, listas para la app de hotspots.
// Pegar en el Code Editor de Google Earth Engine (code.earthengine.google.com) y correr.
// En la pestaña Tasks, darle RUN a cada exportación. Los GeoJSON quedan en Google Drive,
// carpeta HOTSPOTS_CAPAS. Bajarlos y guardarlos en data/capas/<departamento>/ del repo
// (no hace falta correr pipeline/05: ya salen con los campos nombre y categoria).
// El nombre del archivo es el nombre que muestra la app.

// ================== CONFIGURACIÓN (editar) ==================
var DEPTO = 'Cesar';  // como aparece en FAO GAUL (ADM1_NAME)
var ASSETS = 'projects/ndvi-restauracion/assets/';  // revisar que sea la ruta de tus assets
var CARPETA_DRIVE = 'HOTSPOTS_CAPAS';
var TOLERANCIA_M = 50;  // simplificación para que el archivo no pese tanto
// =============================================================

var depto = ee.FeatureCollection('FAO/GAUL/2015/level1')
  .filter(ee.Filter.eq('ADM0_NAME', 'Colombia'))
  .filter(ee.Filter.eq('ADM1_NAME', DEPTO))
  .geometry();

// Cada capa: asset, nombre del archivo, campo con el nombre del área, campo o texto de categoría
var CAPAS = [
  {asset: 'RUNAP', archivo: 'RUNAP', nombre: 'ap_nombre', categoria: 'ap_categor'},
  {asset: 'forest_reserves', archivo: 'Reservas Ley 2', nombre: 'name', categoria: 'category',
   filtro: ee.Filter.stringContains('category', 'Sustracci').not()},  // sin sustracciones
  {asset: 'REAA_excluir_simplificado', archivo: 'REAA', nombre: 'nombre_cap', categoria: 'aa'},
  {asset: 'OMEC_simplificado', archivo: 'OMEC', nombre: 'nombre_cap', categoria: 'aa'},
  {asset: 'BST_simplificado', archivo: 'Bosque seco tropical', nombre: 'Region', categoria: null}
];

CAPAS.forEach(function (c) {
  var fc = ee.FeatureCollection(ASSETS + c.asset).filterBounds(depto);
  if (c.filtro) { fc = fc.filter(c.filtro); }
  var salida = fc.map(function (f) {
    var g = f.geometry().intersection(depto, ee.ErrorMargin(10)).simplify(TOLERANCIA_M);
    var cat = c.categoria ? ee.String(ee.Algorithms.If(f.get(c.categoria), f.get(c.categoria), c.archivo))
                          : ee.String(c.archivo);
    var nom = ee.String(ee.Algorithms.If(f.get(c.nombre), f.get(c.nombre), 'Sin nombre'));
    return ee.Feature(g, {nombre: nom, categoria: cat, tipo_geom: g.type()});
  })
  // solo polígonos (el recorte puede dejar líneas o puntos en el borde)
  .filter(ee.Filter.inList('tipo_geom', ['Polygon', 'MultiPolygon']));

  print(c.archivo + ': áreas en ' + DEPTO, salida.size());
  Export.table.toDrive({
    collection: salida.select(['nombre', 'categoria']),
    description: (c.archivo + '_' + DEPTO).replace(/ /g, '_'),
    folder: CARPETA_DRIVE,
    fileNamePrefix: c.archivo,
    fileFormat: 'GeoJSON'
  });
});
