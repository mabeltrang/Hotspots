"""
Modelos de distribución de BioModelos (Instituto Humboldt) para Hotspots.

Lo usan la app (pestaña BioModelos, consulta por predio) y pipeline/08_biomodelos.py.
No hay que editar este archivo para agregar especies: basta con poner los GeoTIFF en
data/biomodelos/entrada/ (ver data/biomodelos/LEEME.md).

Carpetas (todas dentro de data/biomodelos/):
    entrada/     GeoTIFF y ZIP originales, tal como llegan. Nunca se modifican.
    metadata/    metadatos_modelos.csv: lo que se sabe de cada modelo (especie, nivel, validación...).
                 Lo llenas tú o, más adelante, la API de BioModelos. Es lo único que se edita a mano.
    catalogo/    catalogo_modelos.csv: se regenera solo; une la parte técnica con los metadatos.
    procesados/  resultados: vistas/<ID>.png (mapa nacional) y <departamento>/biomodelos_h3.parquet.

Cada modelo se identifica por su contenido (huella SHA-256), no por el nombre del archivo: si el
mismo GeoTIFF llega dos veces, o renombrado, o dentro de un ZIP, se procesa una sola vez.
"""
import base64
import hashlib
import io
import json
import re
import unicodedata
import zipfile
from datetime import date
from functools import lru_cache
from pathlib import Path

import h3
import numpy as np
import pandas as pd

RAIZ = Path(__file__).parent
CARPETA = RAIZ / "data" / "biomodelos"
ENTRADA = CARPETA / "entrada"
ARCHIVO_METADATOS = CARPETA / "metadata" / "metadatos_modelos.csv"
ARCHIVO_CATALOGO = CARPETA / "catalogo" / "catalogo_modelos.csv"
PROCESADOS = CARPETA / "procesados"
VISTAS = PROCESADOS / "vistas"
ARCHIVO_LIMITES = RAIZ / "data" / "limites" / "municipios_colombia.geojson"

EXTENSIONES = (".tif", ".tiff", ".geotiff", ".gtiff")
RESOLUCIONES = (5, 6, 7)
VERSION_PROCESO = "1"     # subirla obliga a reprocesar todo (si cambia la forma de calcular)
ANCHO_VISTA = 700          # píxeles de ancho de la vista previa nacional
AREA_HEX_KM2 = {5: 252.9, 6: 36.13, 7: 5.161}

# Columnas que se llenan a mano o con la API. Nada de esto se adivina: vacío = sin dato.
COLS_METADATOS = [
    "id_modelo", "huella", "archivo_original", "nombre_cientifico", "familia", "nivel_modelo",
    "estado_validacion", "calificacion_expertos", "licencia", "fuente", "version_fecha", "umbral",
    "usar_en_analisis", "taxid_biomodelos", "id_modelo_biomodelos", "cita", "notas",
]
# Columnas técnicas que calcula el sistema.
COLS_TECNICAS = [
    "id_modelo", "huella", "archivo_original", "ruta_entrada", "archivos_identicos",
    "nombre_detectado", "fuente_nombre_detectado", "etiqueta_interna",
    "tipo_valores", "valores_presentes", "nodata", "tratamiento_nodata", "crs", "resolucion",
    "resolucion_km", "ancho", "alto", "bandas", "tipo_dato", "oeste", "sur", "este", "norte",
    "pixeles_presencia", "area_presencia_km2", "deptos_procesados", "umbral_aplicado",
    "estado_procesamiento", "mensaje", "fecha_incorporacion", "version_proceso",
]
# Orden del catálogo final (lo que pidió el equipo primero, lo técnico después).
COLS_CATALOGO = [
    "id_modelo", "archivo_original", "nombre_cientifico", "familia", "nivel_modelo",
    "estado_validacion", "calificacion_expertos", "licencia", "fuente", "version_fecha",
    "estado_identificacion", "estado_procesamiento", "uso_en_analisis", "modelo_principal",
    "tipo_valores", "valores_presentes", "crs", "resolucion", "resolucion_km",
    "oeste", "sur", "este", "norte",
] + [c for c in COLS_TECNICAS if c not in (
    "id_modelo", "archivo_original", "tipo_valores", "valores_presentes", "crs", "resolucion",
    "resolucion_km", "oeste", "sur", "este", "norte", "estado_procesamiento")] + [
    "umbral", "usar_en_analisis", "taxid_biomodelos", "id_modelo_biomodelos", "cita", "notas"]

# Tipos de modelo según sus valores (ver clasificar_valores)
BINARIO_PRESENCIA = "Binario (solo presencia)"
BINARIO_PA = "Binario (presencia/ausencia)"
CONTINUO_01 = "Continuo 0-1 (idoneidad o probabilidad)"
CONTINUO_OTRO = "Continuo (otra escala)"
CATEGORICO = "Categórico (varias clases)"
VACIO = "Sin píxeles con valor"
BINARIOS = (BINARIO_PRESENCIA, BINARIO_PA)
CONTINUOS = (CONTINUO_01, CONTINUO_OTRO)

PENDIENTE = "Pendiente de identificación"
NODATA_COMUNES = (-9999, -3.4028234663852886e38, -32768, -3.3999999521443642e38)
PALABRAS_NO_EPITETO = {"modelo", "model", "nivel", "level", "veg", "binario", "continuo", "final", "sp", "spp"}


# ============================================================ utilidades
def _texto(v):
    return "" if v is None or (isinstance(v, float) and np.isnan(v)) else str(v).strip()


def normalizar(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s).strip().lower()


def detectar_nombre(texto):
    """'Quercus_humboldtii_N2' -> ('Quercus humboldtii', 'N2'); 'AE-9' -> (None, '')."""
    t = re.sub(r"[_\-.]+", " ", _texto(texto)).strip()
    m = re.match(r"^([A-Z][a-z]{2,})\s+([a-z][a-z]{2,}(?:-[a-z]+)?)\b\s*(.*)$", t)
    if not m or m.group(2) in PALABRAS_NO_EPITETO:
        return None, ""
    return f"{m.group(1)} {m.group(2)}", m.group(3).strip()


def huella_bytes(datos):
    return hashlib.sha256(datos).hexdigest()


def leer_csv(ruta, columnas):
    if ruta.exists() and ruta.stat().st_size:
        t = pd.read_csv(ruta, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    else:
        t = pd.DataFrame(columns=columnas)
    for c in columnas:
        if c not in t.columns:
            t[c] = ""
    return t


def escribir_csv(tabla, ruta):
    ruta.parent.mkdir(parents=True, exist_ok=True)
    tabla.to_csv(ruta, index=False, encoding="utf-8-sig")  # utf-8-sig: Excel lo abre con tildes


# ============================================================ entrada
def listar_entrada():
    """Archivos raster de entrada/, incluidos los que vienen dentro de ZIP (sin descomprimirlos)."""
    items = []
    if not ENTRADA.exists():
        return items
    for ruta in sorted(p for p in ENTRADA.rglob("*") if p.is_file()):
        rel = ruta.relative_to(ENTRADA).as_posix()
        if ruta.suffix.lower() in EXTENSIONES:
            items.append({"archivo": ruta.name, "ruta_entrada": rel, "gdal": str(ruta),
                          "leer": (lambda r=ruta: r.read_bytes())})
        elif ruta.suffix.lower() == ".zip":
            with zipfile.ZipFile(ruta) as z:
                for n in sorted(z.namelist()):
                    if n.lower().endswith(EXTENSIONES) and not n.startswith("__MACOSX"):
                        items.append({"archivo": Path(n).name, "ruta_entrada": f"{rel}!{n}",
                                      "gdal": f"/vsizip/{ruta}/{n}",
                                      "leer": (lambda r=ruta, m=n: zipfile.ZipFile(r).read(m))})
    return items


def firma_entrada():
    """Cambia cuando se agrega, quita o modifica un archivo de entrada o los metadatos (para la app)."""
    archivos = sorted(p for p in ENTRADA.rglob("*") if p.is_file()) if ENTRADA.exists() else []
    extra = [p for p in (ARCHIVO_METADATOS,) if p.exists()]
    return tuple((p.as_posix(), p.stat().st_size, p.stat().st_mtime) for p in archivos + extra)


# ============================================================ inspección
def clasificar_valores(validos, dtype):
    """Tipo de modelo según los valores de los píxeles con dato. No convierte nada a binario."""
    if validos.size == 0:
        return VACIO, ""
    unicos = np.unique(validos)
    muestra = ", ".join(f"{v:g}" for v in unicos[:8]) + (f" … ({len(unicos):,} valores)" if len(unicos) > 8 else "")
    if len(unicos) == 1:
        return BINARIO_PRESENCIA, muestra
    if len(unicos) == 2 and unicos[0] == 0:
        return BINARIO_PA, muestra
    entero = np.issubdtype(dtype, np.integer) or np.all(np.mod(unicos, 1) == 0)
    if entero and len(unicos) <= 20:
        return CATEGORICO, muestra
    if unicos.min() >= 0 and unicos.max() <= 1:
        return CONTINUO_01, f"{unicos.min():.3g} a {unicos.max():.3g}"
    return CONTINUO_OTRO, f"{unicos.min():.4g} a {unicos.max():.4g}"


def _validos(banda, nodata):
    """Máscara de píxeles con dato y cómo se decidió qué es NoData."""
    datos = banda.astype("float64")
    malo = ~np.isfinite(datos)
    if nodata is not None and not np.isnan(nodata):
        malo |= datos == nodata
        return ~malo, f"NoData declarado ({nodata:g})"
    if nodata is not None:
        return ~malo, "NoData declarado (NaN)"
    inferido = [v for v in NODATA_COMUNES if np.any(np.isclose(datos, v))]
    for v in inferido:
        malo |= np.isclose(datos, v)
    if inferido:
        return ~malo, f"Sin NoData declarado; se trató {inferido[0]:g} como NoData"
    return ~malo, "Sin NoData declarado; todos los píxeles finitos cuentan como dato"


def inspeccionar(gdal_path):
    """Características técnicas de un GeoTIFF, valores presentes y nombre de especie si lo trae."""
    import rasterio
    from rasterio.warp import transform_bounds

    with rasterio.open(gdal_path) as r:
        banda = r.read(1)
        validos, trat = _validos(banda, r.nodata)
        tipo, valores = clasificar_valores(banda[validos], banda.dtype)
        oeste, sur, este, norte = (transform_bounds(r.crs, "EPSG:4326", *r.bounds) if r.crs else r.bounds)
        res_x, res_y = r.res
        lat_media = (sur + norte) / 2
        geo = r.crs is None or r.crs.is_geographic
        res_km = res_x * 111.32 * np.cos(np.radians(lat_media)) if geo else res_x / 1000
        pres = presencia_pixeles(banda, validos, tipo, None)
        area_pix = res_km * (res_y * 110.57 if geo else res_y / 1000)
        # Posibles nombres: etiquetas internas, descripción de la banda y nombre del archivo
        candidatos = [(f"etiqueta {k}", v) for k, v in {**r.tags(), **r.tags(1)}.items()
                      if re.search(r"species|especie|scientific|taxon|nombre|name", k, re.I)]
        candidatos.append(("descripción de la banda", r.descriptions[0] if r.descriptions else ""))
        candidatos.append(("nombre del archivo", Path(gdal_path.split("!")[-1]).stem))
        nombre, fuente_nombre, etiqueta = None, "", ""
        for fuente, texto in candidatos:
            nombre, etiqueta = detectar_nombre(texto)
            if nombre:
                fuente_nombre = fuente
                break
        return {
            "nombre_detectado": nombre or "", "fuente_nombre_detectado": fuente_nombre,
            "etiqueta_interna": etiqueta, "tipo_valores": tipo, "valores_presentes": valores,
            "nodata": "" if r.nodata is None else ("NaN" if np.isnan(r.nodata) else f"{r.nodata:g}"), "tratamiento_nodata": trat,
            "crs": r.crs.to_string() if r.crs else "Sin CRS",
            "resolucion": f"{res_x:.6g} x {res_y:.6g} {'grados' if geo else 'unidades del CRS'}",
            "resolucion_km": f"{res_km:.2f}", "ancho": r.width, "alto": r.height, "bandas": r.count,
            "tipo_dato": r.dtypes[0], "oeste": round(oeste, 5), "sur": round(sur, 5),
            "este": round(este, 5), "norte": round(norte, 5),
            "pixeles_presencia": int(pres.sum()) if pres is not None else "",
            "area_presencia_km2": round(float(pres.sum()) * area_pix, 1) if pres is not None else "",
            "mensaje": "Tiene varias bandas; se usó la banda 1." if r.count > 1 else "",
        }


def presencia_pixeles(banda, validos, tipo, umbral):
    """Matriz booleana de presencia, o None si el modelo no se puede usar como presencia.

    Binario solo presencia: todo píxel con dato es presencia (fuera de la distribución = NoData).
    Binario 0/1: presencia = valor distinto de 0.
    Continuo: solo si hay un umbral en los metadatos (valor >= umbral); sin umbral no se binariza.
    """
    if tipo == BINARIO_PRESENCIA:
        return validos
    if tipo == BINARIO_PA:
        return validos & (banda != 0)
    if tipo in CONTINUOS and umbral is not None:
        return validos & (banda >= umbral)
    return None


# ============================================================ departamentos y H3
@lru_cache(maxsize=None)
def poligono_departamento(depto):
    from shapely.geometry import shape
    from shapely.ops import unary_union

    datos = json.loads(ARCHIVO_LIMITES.read_text(encoding="utf-8"))
    geoms = [shape(f["geometry"]) for f in datos["features"]
             if normalizar(f["properties"]["DPTO_CNMBR"]) == normalizar(depto)]
    return unary_union(geoms) if geoms else None


@lru_cache(maxsize=32)
def _malla(crs_wkt, transform, ancho, alto, depto):
    """Celdas H3 del centro de cada píxel dentro del departamento (se reutiliza entre modelos
    que comparten la misma grilla, como todos los de BioModelos)."""
    from rasterio.crs import CRS
    from rasterio.features import geometry_mask
    from rasterio.transform import Affine
    from rasterio.warp import transform as reproyectar
    from rasterio.warp import transform_geom
    from shapely.geometry import shape

    t = Affine(*transform)
    pol = poligono_departamento(depto)
    if pol is None:
        return None
    crs = CRS.from_wkt(crs_wkt) if crs_wkt else None
    en_4326 = crs is None or crs.to_epsg() == 4326
    geom = pol.__geo_interface__ if en_4326 else transform_geom("EPSG:4326", crs, pol.__geo_interface__)
    minx, miny, maxx, maxy = shape(geom).bounds
    # Ventana de píxeles que cubre el departamento (recortada al raster)
    cs, fs = zip(*[~t * (x, y) for x, y in ((minx, maxy), (maxx, miny), (minx, miny), (maxx, maxy))])
    col0, col1 = max(int(np.floor(min(cs))), 0), min(int(np.ceil(max(cs))), ancho)
    fila0, fila1 = max(int(np.floor(min(fs))), 0), min(int(np.ceil(max(fs))), alto)
    if col1 <= col0 or fila1 <= fila0:
        return {"fuera": True}
    tv = t * Affine.translation(col0, fila0)
    dentro = ~geometry_mask([geom], out_shape=(fila1 - fila0, col1 - col0), transform=tv)
    filas, cols = np.nonzero(dentro)
    xs, ys = tv * (cols + 0.5, filas + 0.5)
    if not en_4326:
        xs, ys = reproyectar(crs, "EPSG:4326", list(xs), list(ys))
    xs, ys = np.asarray(xs), np.asarray(ys)
    celdas = {r: np.array([h3.latlng_to_cell(la, lo, r) for la, lo in zip(ys, xs)]) for r in RESOLUCIONES}
    return {"fuera": False, "ventana": (fila0, fila1, col0, col1), "filas": filas, "cols": cols, "celdas": celdas}


def a_hexagonos(gdal_path, tipo, umbral, depto):
    """Por hexágono H3 (res 5, 6 y 7): qué fracción del hexágono cae dentro de la distribución
    y, si el modelo es continuo, el valor medio y máximo. Solo dentro del departamento."""
    import rasterio
    from rasterio.windows import Window

    with rasterio.open(gdal_path) as r:
        malla = _malla(r.crs.to_wkt() if r.crs else "", tuple(r.transform)[:6], r.width, r.height, depto)
        if malla is None or malla["fuera"]:
            return pd.DataFrame(), "El modelo no cubre el departamento."
        f0, f1, c0, c1 = malla["ventana"]
        banda = r.read(1, window=Window(c0, f0, c1 - c0, f1 - f0))
        validos, _ = _validos(banda, r.nodata)
        pres = presencia_pixeles(banda, validos, tipo, umbral)
        fi, co = malla["filas"], malla["cols"]
        tabla_px = pd.DataFrame({"valido": validos[fi, co]})
        if pres is not None:
            tabla_px["presente"] = pres[fi, co]
        if tipo in CONTINUOS:
            tabla_px["valor"] = np.where(validos[fi, co], banda[fi, co].astype("float64"), np.nan)
        salida = []
        for res in RESOLUCIONES:
            g = tabla_px.assign(h3=malla["celdas"][res]).groupby("h3")
            t = g.size().rename("pixeles").to_frame()
            if pres is not None:
                t["fraccion"] = g["presente"].mean()
            if tipo in CONTINUOS:
                t["valor_medio"] = g["valor"].mean()
                t["valor_max"] = g["valor"].max()
            t = t.reset_index().assign(res=res)
            salida.append(t)
        t = pd.concat(salida, ignore_index=True)
        con_presencia = t["fraccion"] > 0 if "fraccion" in t else pd.Series(False, index=t.index)
        con_valor = t["valor_medio"].notna() if "valor_medio" in t else pd.Series(False, index=t.index)
        t = t[con_presencia | con_valor]
        aviso = ""
        lado_km = r.res[0] * 111.32 if (r.crs is None or r.crs.is_geographic) else r.res[0] / 1000
        if lado_km > np.sqrt(AREA_HEX_KM2[7]):
            aviso = "Píxeles más grandes que el hexágono de res. 7: usar res. 5 o 6 para este modelo."
        return t, aviso


def vista_previa(gdal_path, tipo, destino):
    """PNG liviano de todo el modelo en WGS84 (para verlo aunque no cubra el departamento)."""
    import rasterio
    from PIL import Image
    from rasterio.enums import Resampling
    from rasterio.vrt import WarpedVRT

    with rasterio.open(gdal_path) as r, WarpedVRT(r, crs="EPSG:4326", resampling=Resampling.nearest) as v:
        alto = max(1, int(ANCHO_VISTA * v.height / v.width))
        banda = v.read(1, out_shape=(alto, ANCHO_VISTA), resampling=Resampling.nearest)
        validos, _ = _validos(banda, v.nodata if v.nodata is not None else r.nodata)
        rgba = np.zeros((alto, ANCHO_VISTA, 4), dtype="uint8")
        if tipo in CONTINUOS:
            x = banda.astype("float64")
            lo, hi = np.nanmin(x[validos]) if validos.any() else 0, np.nanmax(x[validos]) if validos.any() else 1
            esc = np.clip((x - lo) / max(hi - lo, 1e-9), 0, 1)
            rgba[..., 0] = (40 + 200 * esc).astype("uint8")
            rgba[..., 1] = (60 + 60 * (1 - esc)).astype("uint8")
            rgba[..., 2] = (160 - 120 * esc).astype("uint8")
            rgba[..., 3] = np.where(validos, 200, 0)
        else:
            pres = presencia_pixeles(banda, validos, tipo, None)
            pres = pres if pres is not None else validos
            rgba[pres] = [123, 76, 201, 220]                     # morado: dentro de la distribución
            if tipo == BINARIO_PA:
                rgba[validos & ~pres] = [200, 200, 200, 90]      # gris: ausencia modelada
        destino.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(rgba, "RGBA").save(destino, optimize=True)
        return list(v.bounds)


def vista_base64(id_modelo):
    ruta = VISTAS / f"{id_modelo}.png"
    return "data:image/png;base64," + base64.b64encode(ruta.read_bytes()).decode() if ruta.exists() else None


# ============================================================ catálogo
def _a_numero(v):
    try:
        return float(str(v).replace(",", "."))
    except ValueError:
        return None


def _siguiente_id(usados):
    nums = [int(m.group(1)) for u in usados if (m := re.match(r"MODELO_(\d+)$", str(u)))]
    return f"MODELO_{(max(nums) if nums else 0) + 1:03d}"


def unir_catalogo(tecnico, meta):
    """Catálogo = parte técnica + metadatos. Decide especie, estado y modelo principal por especie."""
    cat = tecnico.merge(meta.drop(columns=["id_modelo", "archivo_original"], errors="ignore"),
                        on="huella", how="left").fillna("")
    manual = cat["nombre_cientifico"].str.strip()
    cat["nombre_cientifico"] = np.where(manual != "", manual, cat["nombre_detectado"])
    cat["estado_identificacion"] = np.select(
        [manual != "", cat["nombre_detectado"] != ""],
        ["Identificada (metadatos)", "Identificada (automática: " + cat["fuente_nombre_detectado"] + ")"],
        PENDIENTE)
    usable = cat["estado_procesamiento"].str.startswith("Procesado") & (
        cat["tipo_valores"].isin(BINARIOS) | (cat["tipo_valores"].isin(CONTINUOS) & (cat["umbral_aplicado"] != "")))
    excluido = cat["usar_en_analisis"].map(normalizar).isin(["no", "n", "0", "false"])
    cat["uso_en_analisis"] = np.select(
        [cat["estado_procesamiento"] == "Error", cat["estado_identificacion"] == PENDIENTE,
         ~usable & cat["tipo_valores"].isin(CONTINUOS), ~usable, excluido],
        ["No: error al leer el archivo", "No: falta identificar la especie",
         "No: continuo sin umbral en metadatos", "No: " + cat["tipo_valores"], "No: excluido en metadatos"], "Sí")
    # Un solo modelo por especie: el elegido a mano; si no, validado > nivel > calificación > más reciente
    cat["modelo_principal"] = ""
    cand = cat[cat["uso_en_analisis"] == "Sí"].copy()
    if len(cand):
        cand["_manual"] = cand["usar_en_analisis"].map(normalizar).isin(["si", "s", "1", "true", "principal"])
        cand["_valido"] = cand["estado_validacion"].map(normalizar).str.contains("valid") & \
            ~cand["estado_validacion"].map(normalizar).str.contains("no valid|sin valid")
        cand["_nivel"] = cand["nivel_modelo"].map(lambda v: _a_numero(re.sub(r"[^\d.,]", "", str(v))) or 0)
        cand["_calif"] = cand["calificacion_expertos"].map(lambda v: _a_numero(v) or 0)
        cand = cand.sort_values(["nombre_cientifico", "_manual", "_valido", "_nivel", "_calif", "version_fecha", "id_modelo"],
                                ascending=[True, False, False, False, False, False, True])
        principales = cand.drop_duplicates("nombre_cientifico")["id_modelo"]
        cat.loc[cat["uso_en_analisis"] == "Sí", "modelo_principal"] = "No (hay otro modelo de la especie)"
        cat.loc[cat["id_modelo"].isin(principales), "modelo_principal"] = "Sí"
    return cat.reindex(columns=COLS_CATALOGO).sort_values("id_modelo").reset_index(drop=True)


def deptos_disponibles():
    carpeta = RAIZ / "data" / "procesados"
    return sorted(p.name for p in carpeta.iterdir() if (p / "registros.parquet").exists()) if carpeta.exists() else []


def actualizar(deptos=None, forzar=False, avisar=print):
    """Detecta archivos nuevos en entrada/, los procesa y regenera el catálogo.

    Devuelve un resumen. No toca los originales. Si no hay nada nuevo, solo vuelve a unir
    metadatos y catálogo (por si se editó metadatos_modelos.csv).
    """
    deptos = list(deptos or deptos_disponibles())
    tecnico = leer_csv(ARCHIVO_CATALOGO, COLS_TECNICAS)[COLS_TECNICAS]
    meta = leer_csv(ARCHIVO_METADATOS, COLS_METADATOS)
    umbral_meta = dict(zip(meta["huella"], meta["umbral"]))
    resumen = {"nuevos": 0, "reprocesados": 0, "duplicados": 0, "errores": 0, "sin_cambios": 0}
    h3_nuevos = {d: [] for d in deptos}
    ids_reprocesados = {d: set() for d in deptos}
    vistos = {}

    for item in listar_entrada():
        try:
            hue = huella_bytes(item["leer"]())
        except Exception as e:  # ZIP dañado, archivo sin permisos...
            avisar(f"No pude leer {item['ruta_entrada']}: {e}")
            resumen["errores"] += 1
            continue
        if hue in vistos:  # mismo contenido dos veces en esta carga
            i = vistos[hue]
            otros = set(filter(None, tecnico.at[i, "archivos_identicos"].split(" | "))) | {item["ruta_entrada"]}
            tecnico.at[i, "archivos_identicos"] = " | ".join(sorted(otros - {tecnico.at[i, "ruta_entrada"]}))
            resumen["duplicados"] += 1
            continue
        fila = tecnico.index[tecnico["huella"] == hue]
        umbral_txt = umbral_meta.get(hue, "").strip()
        procesar = list(deptos)
        if len(fila):
            i = fila[0]
            vistos[hue] = i
            tecnico.at[i, "ruta_entrada"] = item["ruta_entrada"]
            hechos = set(filter(None, tecnico.at[i, "deptos_procesados"].split("|")))
            cambio = (tecnico.at[i, "version_proceso"] != VERSION_PROCESO or tecnico.at[i, "umbral_aplicado"]
                      != (umbral_txt if tecnico.at[i, "tipo_valores"] in CONTINUOS else ""))
            if not (cambio or forzar):
                procesar = [d for d in deptos if d not in hechos]   # solo departamentos nuevos
                if not procesar:
                    resumen["sin_cambios"] += 1
                    continue
            else:
                hechos = set()
            resumen["reprocesados"] += 1
        else:
            hechos = set()
            i = len(tecnico)
            tecnico.loc[i] = ""
            tecnico.at[i, "id_modelo"] = _siguiente_id(tecnico["id_modelo"])
            tecnico.at[i, "huella"] = hue
            tecnico.at[i, "archivo_original"] = item["archivo"]
            tecnico.at[i, "ruta_entrada"] = item["ruta_entrada"]
            tecnico.at[i, "fecha_incorporacion"] = date.today().isoformat()
            vistos[hue] = i
            resumen["nuevos"] += 1
        id_m = tecnico.at[i, "id_modelo"]
        avisar(f"{id_m}: {item['ruta_entrada']}")
        try:
            info = inspeccionar(item["gdal"])
            for k, v in info.items():
                tecnico.at[i, k] = "" if v is None else str(v)
            umbral = _a_numero(umbral_txt) if umbral_txt else None
            tecnico.at[i, "umbral_aplicado"] = umbral_txt if (umbral is not None and info["tipo_valores"] in CONTINUOS) else ""
            vista_previa(item["gdal"], info["tipo_valores"], VISTAS / f"{id_m}.png")
            mensajes = [info["mensaje"]] if info["mensaje"] else []
            for d in procesar:
                t, aviso = a_hexagonos(item["gdal"], info["tipo_valores"], umbral, d)
                if aviso:
                    mensajes.append(f"{d.title()}: {aviso}")
                if len(t):
                    h3_nuevos[d].append(t.assign(id_modelo=id_m))
                elif not aviso and info["tipo_valores"] == CATEGORICO:
                    mensajes.append("Modelo categórico: no se calcula presencia; revisar qué significa cada clase.")
                elif not aviso:
                    mensajes.append(f"Sin presencia dentro de {d.title()}.")
            for d in procesar:
                ids_reprocesados[d].add(id_m)
            tecnico.at[i, "deptos_procesados"] = "|".join(sorted(hechos | set(procesar)))
            tecnico.at[i, "version_proceso"] = VERSION_PROCESO
            tecnico.at[i, "estado_procesamiento"] = "Procesado"
            previo_msj = tecnico.at[i, "mensaje"] if hechos else ""
            tecnico.at[i, "mensaje"] = " ".join(dict.fromkeys(filter(None, [previo_msj] + mensajes)))
        except Exception as e:  # un archivo malo no detiene la carga de los demás
            tecnico.at[i, "estado_procesamiento"] = "Error"
            tecnico.at[i, "mensaje"] = f"{type(e).__name__}: {e}".replace(str(ENTRADA) + "/", "")
            tecnico.at[i, "deptos_procesados"] = "|".join(sorted(hechos | set(procesar)))  # no se reintenta en cada carga;
            tecnico.at[i, "version_proceso"] = VERSION_PROCESO       # usar --forzar tras reemplazarlo
            resumen["errores"] += 1
            avisar(f"  Error: {e}")

    # Modelos del catálogo cuyo original ya no está en entrada/: se conservan sus resultados
    presentes = set(vistos)
    faltan = ~tecnico["huella"].isin(presentes) & tecnico["estado_procesamiento"].str.startswith("Procesado")
    tecnico.loc[faltan, "estado_procesamiento"] = "Procesado (original fuera de entrada/)"
    tecnico.loc[tecnico["huella"].isin(presentes) & (tecnico["estado_procesamiento"] == "Procesado (original fuera de entrada/)"),
                "estado_procesamiento"] = "Procesado"

    for d in deptos:
        ruta = PROCESADOS / d / "biomodelos_h3.parquet"
        previo = pd.read_parquet(ruta) if ruta.exists() else pd.DataFrame(columns=["id_modelo"])
        previo = previo[~previo["id_modelo"].isin(ids_reprocesados[d])]
        todo = pd.concat([x for x in [previo] + h3_nuevos[d] if len(x)], ignore_index=True) \
            if len(previo) or h3_nuevos[d] else pd.DataFrame(columns=["id_modelo", "h3", "res", "pixeles", "fraccion"])
        if len(todo):
            todo["res"] = todo["res"].astype(int)
        if len(todo) or ruta.exists():
            ruta.parent.mkdir(parents=True, exist_ok=True)
            todo.to_parquet(ruta, index=False)

    # Metadatos: se agrega una fila (vacía salvo id y archivo) por cada modelo nuevo; no se toca lo escrito
    nuevas = tecnico[~tecnico["huella"].isin(meta["huella"])][["id_modelo", "huella", "archivo_original"]]
    if len(nuevas):
        meta = pd.concat([meta, nuevas], ignore_index=True).fillna("")[COLS_METADATOS]
        escribir_csv(meta, ARCHIVO_METADATOS)
    catalogo = unir_catalogo(tecnico, meta)
    anterior = leer_csv(ARCHIVO_CATALOGO, COLS_CATALOGO)[COLS_CATALOGO] if ARCHIVO_CATALOGO.exists() else None
    if anterior is None or not anterior.astype(str).reset_index(drop=True).equals(catalogo.astype(str)):
        escribir_csv(catalogo, ARCHIVO_CATALOGO)
    resumen["modelos"] = len(catalogo)
    return resumen


def cargar(depto):
    """(catálogo, tabla H3 del departamento). Lee solo resultados: no necesita los originales."""
    cat = leer_csv(ARCHIVO_CATALOGO, COLS_CATALOGO)
    ruta = PROCESADOS / depto / "biomodelos_h3.parquet"
    h = pd.read_parquet(ruta) if ruta.exists() else pd.DataFrame(columns=["id_modelo", "h3", "res", "pixeles", "fraccion"])
    return cat, h


def presencias(cat, h, res, cobertura_min):
    """Especie x hexágono usando solo el modelo principal de cada especie identificada.

    Un hexágono cuenta si al menos `cobertura_min` (0-1) de su área cae dentro de la distribución.
    """
    princ = cat[cat["modelo_principal"] == "Sí"][["id_modelo", "nombre_cientifico"]]
    if h.empty or princ.empty or "fraccion" not in h.columns:
        return pd.DataFrame(columns=["h3", "species", "id_modelo", "fraccion"])
    p = h[(h["res"] == res) & h["id_modelo"].isin(princ["id_modelo"]) & (h["fraccion"].fillna(0) > 0)]
    p = p[p["fraccion"] >= cobertura_min] if cobertura_min > 0 else p
    return p.merge(princ, on="id_modelo").rename(columns={"nombre_cientifico": "species"})[
        ["h3", "species", "id_modelo", "fraccion"]]
