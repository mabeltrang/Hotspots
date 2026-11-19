"""
Lectura de archivos vectoriales (KMZ, KML, GeoJSON y shapefile en .zip) a geometrías
shapely en WGS84 (EPSG:4326). La usan la app (consulta por predio) y
pipeline/05_capas_conservacion.py.
"""
import io
import json
import zipfile
import xml.etree.ElementTree as ET

import numpy as np
import pyproj
import shapefile
import shapely
from shapely.geometry import LineString, Point, Polygon, shape
from shapely.ops import unary_union

EPSG_PLANO = 9377  # MAGNA-SIRGAS Origen Nacional, para áreas, distancias y buffers
FORMATOS = ["kmz", "kml", "geojson", "json", "zip"]


def a_plano(geom):
    """WGS84 -> EPSG:9377 (metros)."""
    t = pyproj.Transformer.from_crs(4326, EPSG_PLANO, always_xy=True).transform
    return shapely.transform(geom, lambda c: np.column_stack(t(c[:, 0], c[:, 1])))


def a_wgs84(geom, crs_origen=EPSG_PLANO):
    t = pyproj.Transformer.from_crs(crs_origen, 4326, always_xy=True).transform
    return shapely.transform(geom, lambda c: np.column_stack(t(c[:, 0], c[:, 1])))


def _parece_plano(geom):
    minx, miny, maxx, maxy = geom.bounds
    return max(abs(minx), abs(maxx)) > 180 or max(abs(miny), abs(maxy)) > 90


def _coords_kml(texto):
    pares = [c.split(",") for c in texto.split()]
    return [(float(p[0]), float(p[1])) for p in pares if len(p) >= 2]


def _leer_kml(kml_bytes):
    raiz = ET.fromstring(kml_bytes)
    nombre_tag = lambda e: e.tag.rsplit("}", 1)[-1]  # noqa: E731
    salida = []
    for marca in (e for e in raiz.iter() if nombre_tag(e) == "Placemark"):
        nombre = next((e.text for e in marca if nombre_tag(e) == "name"), None)
        geoms = []
        for e in marca.iter():
            tag = nombre_tag(e)
            if tag == "Polygon":
                ext, ints = None, []
                for anillo in e.iter():
                    if nombre_tag(anillo) in ("outerBoundaryIs", "innerBoundaryIs"):
                        coords = next((_coords_kml(c.text or "") for c in anillo.iter()
                                       if nombre_tag(c) == "coordinates"), [])
                        if nombre_tag(anillo) == "outerBoundaryIs":
                            ext = coords
                        else:
                            ints.append(coords)
                if ext and len(ext) >= 3:
                    geoms.append(Polygon(ext, [i for i in ints if len(i) >= 3]))
            elif tag == "LineString":
                c = next((_coords_kml(x.text or "") for x in e if nombre_tag(x) == "coordinates"), [])
                if len(c) >= 2:
                    geoms.append(LineString(c))
            elif tag == "Point":
                c = next((_coords_kml(x.text or "") for x in e if nombre_tag(x) == "coordinates"), [])
                if c:
                    geoms.append(Point(c[0]))
        if geoms:
            salida.append((unary_union(geoms), {"nombre": nombre or "Sin nombre"}))
    return salida


def _leer_geojson(datos):
    obj = json.loads(datos)
    feats = obj["features"] if obj.get("type") == "FeatureCollection" else [
        obj if obj.get("type") == "Feature" else {"type": "Feature", "geometry": obj, "properties": {}}]
    salida = [(shape(f["geometry"]), f.get("properties") or {}) for f in feats if f.get("geometry")]
    if salida and _parece_plano(unary_union([g for g, _ in salida])):
        salida = [(a_wgs84(g), p) for g, p in salida]
    return salida


def _leer_shapefile_zip(datos):
    with zipfile.ZipFile(io.BytesIO(datos)) as z:
        nombres = z.namelist()
        shp = next((n for n in nombres if n.lower().endswith(".shp")), None)
        if shp is None:
            raise ValueError("El .zip no tiene ningún .shp.")
        base = shp[:-4]
        leer = lambda ext: z.read(next(n for n in nombres if n.lower() == (base + ext).lower()))  # noqa: E731
        prj = next((n for n in nombres if n.lower() == (base + ".prj").lower()), None)
        crs = pyproj.CRS.from_wkt(z.read(prj).decode("utf-8", "ignore")) if prj else None
        lector = shapefile.Reader(shp=io.BytesIO(leer(".shp")), shx=io.BytesIO(leer(".shx")),
                                  dbf=io.BytesIO(leer(".dbf")), encoding="utf-8", encodingErrors="replace")
        campos = [f[0] for f in lector.fields[1:]]
        salida = []
        for sr in lector.iterShapeRecords():
            if sr.shape.shapeType == shapefile.NULL:
                continue
            g = shape(sr.shape.__geo_interface__)
            if crs is not None and not crs.equals(pyproj.CRS.from_epsg(4326)):
                g = a_wgs84(g, crs)
            elif crs is None and _parece_plano(g):
                g = a_wgs84(g)
            salida.append((g, dict(zip(campos, sr.record))))
        return salida


def leer_vector(datos: bytes, nombre_archivo: str) -> list[tuple]:
    """Lista de (geometría WGS84, propiedades). Shapefile: subir el .zip con .shp, .shx, .dbf y .prj."""
    ext = nombre_archivo.lower().rsplit(".", 1)[-1]
    if ext == "kmz":
        with zipfile.ZipFile(io.BytesIO(datos)) as z:
            kml = next(n for n in z.namelist() if n.lower().endswith(".kml"))
            salida = _leer_kml(z.read(kml))
    elif ext == "kml":
        salida = _leer_kml(datos)
    elif ext in ("geojson", "json"):
        salida = _leer_geojson(datos)
    elif ext == "zip":
        salida = _leer_shapefile_zip(datos)
    else:
        raise ValueError(f"Formato no soportado: .{ext}. Usa {', '.join('.' + f for f in FORMATOS)}.")
    salida = [(g if g.is_valid else shapely.make_valid(g), p) for g, p in salida if not g.is_empty]
    if not salida:
        raise ValueError("El archivo no tiene geometrías.")
    return salida
