"""
Hotspots de biodiversidad por departamento.

Correr:  streamlit run app.py
Datos:   data/procesados/<departamento>/registros.parquet  (pipeline/01_procesar_gbif.py)
         data/unergy/*.xlsx                                 (inventarios Unergy, opcional)
         data/car/*.xlsx                                    (árboles de resoluciones de la CAR, opcional)
         data/limites/municipios_colombia.geojson           (DANE, MGN 2018)
         data/capas/<departamento>/*.geojson                (áreas protegidas, opcional; pipeline/05)
"""
import io
import json
import math
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

import numpy as np
import pandas as pd
import h3
import pydeck as pdk
import shapely
import streamlit as st
from shapely.geometry import Polygon, mapping, shape
from shapely.geometry.polygon import orient
from shapely.ops import unary_union

from comun import GRUPOS, ORDEN_GRUPOS, aplicar_sinonimos
from geodatos import FORMATOS, a_plano, a_wgs84, leer_vector
from unergy import cargar_unergy, normalizar_texto

st.set_page_config(page_title="Hotspots de biodiversidad", page_icon="🌿", layout="wide")

RAIZ = Path(__file__).parent
CARPETA_DATOS = RAIZ / "data" / "procesados"
CARPETA_UNERGY = RAIZ / "data" / "unergy"
CARPETA_CAR = RAIZ / "data" / "car"
CARPETAS_INVENTARIO = {"Unergy": CARPETA_UNERGY, "CAR": CARPETA_CAR}
ARCHIVO_LIMITES = RAIZ / "data" / "limites" / "municipios_colombia.geojson"
CARPETA_LISTAS = RAIZ / "data" / "listas"
CARPETA_CAPAS = RAIZ / "data" / "capas"
ORIGENES = ["Endémica", "Nativa", "Introducida", "Sin dato"]
AMENAZA = {"CR", "EN", "VU"}

TAMANO_CELDA = {5: "≈253 km²", 6: "≈36 km²", 7: "≈5 km²", 8: "≈0,7 km²"}
ICONOS_GRUPO = {"Aves": "🐦", "Mamíferos": "🦊", "Herpetos": "🦎", "Invertebrados": "🦋", "Flora": "🌿"}
ICONOS_ORIGEN = {"Endémica": "⭐", "Nativa": "🌱", "Introducida": "🚩", "Sin dato": "❔"}
CATEGORIAS = {"CR": "🔴 CR · En peligro crítico", "EN": "🟠 EN · En peligro", "VU": "🟡 VU · Vulnerable",
              "NT": "🟢 NT · Casi amenazada", "LC": "🔵 LC · Preocupación menor",
              "DD": "⚪ DD · Datos insuficientes", "NE": "⚫ NE · No evaluada"}
FUENTES = {"GBIF": "🌐 GBIF", "Unergy": "🌳 Unergy", "CAR": "🏛️ Resoluciones CAR"}
METRICAS = {
    "Rarefactada": "Especies esperadas si todas las unidades tuvieran el mismo número de registros "
                   "(el mínimo de registros de Opciones avanzadas). Corrige la diferencia de muestreo entre unidades.",
    "Chao1": "Riqueza estimada: suma las especies que probablemente están pero no se han registrado, "
             "a partir de las especies con uno y dos registros.",
    "Observada": "Número de especies registradas, sin corrección. Favorece las zonas más muestreadas.",
}
UMBRAL_VACIO = 0.10  # hotspot con menos de este % de área en capas de conservación = vacío

# ---- Colores (editar aquí) -----------------------------------------------
# Degradado de menos a más especies. Pon los códigos hex que quieras, de claro a oscuro.
# Colores corporativos Unergy (los mismos de la app de compensación)
PURPLE, PURPLE_DARK, PURPLE_MID, PURPLE_LIGHT = "#7B4CC9", "#2D1B4E", "#9B6FE8", "#D9CEEF"
# Mapa: verde (menos especies) -> amarillo -> rojo (más especies)
PALETA_HEX = ["#1A9850", "#66BD63", "#A6D96A", "#FEE08B", "#FDAE61", "#F46D43", "#D73027"]
COLOR_HOTSPOT_HEX = "#2D1B4E"   # borde de los hotspots (morado Unergy)
COLOR_CAPAS_HEX = "#8D6E63"     # áreas protegidas y otras capas de conservación (café, para no confundir con el azul)
COLOR_POCOS_HEX = "#74ADD1"     # registros insuficientes: azul, antes del verde de la escala
COLOR_SIN_DATOS_HEX = "#C8C8C8"  # fondo del departamento: sin registros
# --------------------------------------------------------------------------


def hex_a_rgb(h):
    h = h.lstrip("#")
    return [int(h[i:i + 2], 16) for i in (0, 2, 4)]


PALETA = np.array([hex_a_rgb(h) for h in PALETA_HEX])
COLOR_HOTSPOT = hex_a_rgb(COLOR_HOTSPOT_HEX)
COLOR_CAPAS = hex_a_rgb(COLOR_CAPAS_HEX)

st.markdown(f"""
<style>
  .stApp h1, .stApp h2, .stApp h3 {{ color: #1A1A2E; }}
  .unergy-header {{ background: {PURPLE_DARK}; padding: 18px 28px 14px; border-radius: 12px;
    margin-bottom: 18px; display: flex; align-items: center; gap: 16px; }}
  .unergy-header h1 {{ color: #FFFFFF !important; font-size: 1.55rem; font-weight: 700; margin: 0; padding: 0; }}
  .unergy-header p {{ color: #C5B3F0 !important; font-size: 0.82rem; margin: 2px 0 0; }}
  .unergy-header a {{ color: #C5B3F0 !important; }}
  .unergy-logo {{ font-size: 2.2rem; line-height: 1; }}
  .stTabs [data-baseweb="tab-list"] {{ gap: 4px; background: {PURPLE_LIGHT}; border-radius: 10px;
    padding: 4px; border: 1px solid #B8A9E0; }}
  .stTabs [data-baseweb="tab-highlight"], .stTabs [data-baseweb="tab-border"] {{ display: none !important; }}
  .stTabs [data-baseweb="tab"] {{ border-radius: 8px !important; color: {PURPLE_DARK} !important;
    font-weight: 700 !important; padding: 7px 18px !important; background: transparent !important; }}
  .stTabs [data-baseweb="tab"]:hover {{ background: rgba(123,76,201,0.12) !important; }}
  .stTabs [aria-selected="true"] {{ background: {PURPLE} !important; }}
  .stTabs [aria-selected="true"] p {{ color: #FFFFFF !important; }}
  .metric-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 14px; margin: 6px 0 14px; }}
  .metric-card {{ background: #FFFFFF; border: 2px solid {PURPLE_LIGHT}; border-left: 5px solid {PURPLE};
    border-radius: 10px; padding: 12px 18px; box-shadow: 0 1px 4px rgba(123,76,201,0.08); }}
  .metric-card .label {{ font-size: 0.8rem; color: #5A5A72; font-weight: 700; margin-bottom: 4px; }}
  .metric-card .value {{ font-size: 1.35rem; font-weight: 800; color: {PURPLE_DARK}; }}
  @media (max-width: 760px) {{ .metric-grid {{ grid-template-columns: 1fr; }} }}
  [data-testid="stExpander"] {{ border: 1.5px solid #B8A9E0 !important; border-radius: 8px !important; }}
  [data-testid="stDataFrame"] {{ border: 1.5px solid #B8A9E0; border-radius: 8px; overflow: hidden; }}
  [data-testid="stSidebar"] {{ background: {PURPLE_DARK} !important; border-right: 3px solid {PURPLE}; }}
  [data-testid="stSidebar"] p, [data-testid="stSidebar"] span, [data-testid="stSidebar"] label,
  [data-testid="stSidebar"] h2, [data-testid="stSidebar"] h3, [data-testid="stSidebar"] small,
  [data-testid="stSidebar"] [data-testid="stCaptionContainer"] p {{ color: #FFFFFF !important; }}
  [data-testid="stSidebar"] h3 {{ color: #C5B3F0 !important; }}
  [data-testid="stSidebar"] [data-baseweb="select"] *,
  [data-testid="stSidebar"] [data-baseweb="input"] *,
  [data-testid="stSidebar"] input {{ color: #1A1A2E !important; }}
  [data-testid="stSidebar"] [data-baseweb="tag"] {{ background: {PURPLE} !important; }}
  [data-testid="stSidebar"] [data-baseweb="tag"] * {{ color: #FFFFFF !important; }}
  [data-testid="stSidebar"] [data-testid="stExpander"] {{ background: rgba(255,255,255,0.06) !important; }}
  [data-testid="stSidebar"] [data-testid="stExpander"] summary {{ background: transparent !important; }}
  [data-testid="stSidebar"] button[data-variant="pills"] {{ background: {PURPLE_DARK} !important;
    border: 1.5px solid rgba(255,255,255,0.30) !important; opacity: 1 !important; box-shadow: none !important; }}
  [data-testid="stSidebar"] button[data-variant="pills"] * {{ color: #E6DDF8 !important; font-weight: 400; }}
  [data-testid="stSidebar"] button[data-variant="pills"]:hover {{ border-color: {PURPLE_MID} !important; }}
  [data-testid="stSidebar"] button[data-variant="pills"][aria-pressed="true"] {{ background: {PURPLE} !important;
    border: 1.5px solid {PURPLE_MID} !important; }}
  [data-testid="stSidebar"] button[data-variant="pills"][aria-pressed="true"] * {{ color: #FFFFFF !important; font-weight: 700; }}
  [data-testid="stSidebar"] button[kind="tertiary"] p {{ color: #C5B3F0 !important; font-size: 0.78rem;
    text-decoration: underline; }}
  [data-testid="stSidebar"] hr {{ border-color: rgba(255,255,255,0.2) !important; }}
  [data-testid="stSidebar"] [data-testid="stFileUploaderDropzone"] {{ background: rgba(255,255,255,0.08) !important; }}
</style>
""", unsafe_allow_html=True)

GRIS = hex_a_rgb(COLOR_POCOS_HEX) + [190]  # se ve, pero no compite con la escala de riqueza
FONDO_SIN_DATOS = hex_a_rgb(COLOR_SIN_DATOS_HEX) + [70]
ALFA = 185


# ================================================================ carga
@st.cache_data
def departamentos():
    if not CARPETA_DATOS.exists():
        return []
    return sorted(p.name for p in CARPETA_DATOS.iterdir() if (p / "registros.parquet").exists())


@st.cache_data
def cargar_gbif(depto):
    df = pd.read_parquet(CARPETA_DATOS / depto / "registros.parquet")
    meta_path = CARPETA_DATOS / depto / "metadata.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    return df, meta


def firma_unergy():
    """Archivos de inventario (Unergy y CAR) con su fecha de modificación, para recalcular si cambian."""
    return tuple((fuente, p.name, p.stat().st_mtime) for fuente, carpeta in CARPETAS_INVENTARIO.items()
                 if carpeta.exists() for p in sorted(carpeta.glob("*.xlsx")))


@st.cache_data
def cargar_inventarios(firma):
    tablas, avisos = [], []
    for fuente in sorted({f for f, _, _ in firma}):
        t, a = cargar_unergy(CARPETAS_INVENTARIO[fuente], fuente)
        avisos += a
        if t is not None:
            tablas.append(t)
    return (pd.concat(tablas, ignore_index=True) if tablas else None), avisos


@st.cache_resource
def cargar_municipios(depto):
    """Polígonos de los municipios del departamento y su contorno."""
    datos = json.loads(ARCHIVO_LIMITES.read_text(encoding="utf-8"))
    feats = [f for f in datos["features"]
             if normalizar_texto(f["properties"]["DPTO_CNMBR"]) == normalizar_texto(depto)]
    nombres = [f["properties"]["MPIO_CNMBR"].title().replace(" De ", " de ").replace(" Del ", " del ") for f in feats]
    geoms = [shape(f["geometry"]) for f in feats]
    return nombres, geoms, unary_union(geoms)


def sin_tildes(s):
    """El TextLayer de pydeck solo dibuja caracteres ASCII."""
    import unicodedata
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


def anillos(geom):
    """Lista de anillos exteriores [[lon, lat], ...] de un (Multi)Polygon."""
    partes = geom.geoms if geom.geom_type == "MultiPolygon" else [geom]
    return [[list(c) for c in p.exterior.coords] for p in partes]


def asignar_municipio(lon, lat, nombres, geoms):
    salida = np.full(len(lon), None, dtype=object)
    for n, g in zip(nombres, geoms):
        dentro = shapely.contains_xy(g, lon, lat)
        salida[dentro & (salida == None)] = n  # noqa: E711
    return salida


def firma_listas():
    if not CARPETA_LISTAS.exists():
        return ()
    return tuple((p.name, p.stat().st_mtime) for p in sorted(CARPETA_LISTAS.glob("*.csv")))


def firma_capas(depto):
    carpeta = CARPETA_CAPAS / depto
    if not carpeta.exists():
        return ()
    return tuple((p.name, p.stat().st_mtime) for p in sorted(carpeta.glob("*.geojson")))


@st.cache_resource
def cargar_capas(depto, firma):
    """{nombre de capa: [(geometría, propiedades), ...]} desde data/capas/<depto>/."""
    capas = {}
    for nombre, _ in firma:
        datos = json.loads((CARPETA_CAPAS / depto / nombre).read_text(encoding="utf-8"))
        capas[Path(nombre).stem] = [(shape(f["geometry"]), f.get("properties") or {})
                                    for f in datos["features"] if f.get("geometry")]
    return capas


@st.cache_resource
def union_capas(depto, seleccion, firma):
    """Unión de las capas elegidas (None si no hay ninguna)."""
    if not seleccion:
        return None
    capas = cargar_capas(depto, firma)
    union = unary_union([g for n in seleccion for g, _ in capas.get(n, [])])
    return None if union.is_empty else union


def firma_sdm(depto):
    carpeta = RAIZ / "data" / "procesados" / depto
    return tuple((n, (carpeta / n).stat().st_mtime) for n in ("sdm_idoneidad.parquet", "sdm_metricas.csv")
                 if (carpeta / n).exists())


@st.cache_data
def cargar_sdm(depto, firma):
    """Idoneidad por hexágono (res 7) y métricas por especie de pipeline/06. (None, None, {}) si no hay."""
    if len(firma) < 2:
        return None, None, {}
    carpeta = RAIZ / "data" / "procesados" / depto
    info_p = carpeta / "sdm_info.json"
    info = json.loads(info_p.read_text(encoding="utf-8")) if info_p.exists() else {}
    pred = pd.read_parquet(carpeta / "sdm_idoneidad.parquet")
    pred["species"] = pred["species"].astype(str)
    return pred, pd.read_csv(carpeta / "sdm_metricas.csv"), info


@st.cache_data
def municipio_de_celdas(depto, celdas):
    """Municipio que contiene el centro de cada hexágono."""
    nombres, geoms, _ = cargar_municipios(depto)
    lat, lon = np.array([h3.cell_to_latlng(c) for c in celdas]).T if celdas else (np.array([]), np.array([]))
    salida = np.full(len(celdas), "Fuera del límite", dtype=object)
    for n, g in zip(nombres, geoms):
        salida[shapely.contains_xy(g, lon, lat)] = n
    return dict(zip(celdas, salida))


@st.cache_data
def preparar(depto, firma, firma_l):
    """firma y firma_l cambian cuando cambian los Excel o las listas, para recalcular."""
    df, meta = cargar_gbif(depto)
    unergy_todo, avisos = cargar_inventarios(firma)
    unergy = None
    if unergy_todo is not None:
        unergy = unergy_todo[unergy_todo["departamento"] == normalizar_texto(depto)]
        unergy = unergy.drop(columns="departamento") if len(unergy) else None
    df = df.assign(fuente="GBIF")
    if unergy is not None:
        df = pd.concat([df, unergy], ignore_index=True)
    df = aplicar_sinonimos(df.assign(grupo=df["grupo"].map(GRUPOS)).dropna(subset=["grupo"]), CARPETA_LISTAS)
    df["origen"] = "Sin dato"
    arch_origen = CARPETA_LISTAS / f"origen_{depto}.csv"
    if arch_origen.exists():
        o = pd.read_csv(arch_origen)[["species", "origen"]]
        corr = CARPETA_LISTAS / "correcciones_origen.csv"
        if corr.exists():
            c = pd.read_csv(corr)[["species", "origen"]]
            o = pd.concat([o[~o["species"].isin(c["species"])], c], ignore_index=True)
        df = df.drop(columns="origen").merge(o.drop_duplicates("species"), on="species", how="left")
        df["origen"] = df["origen"].fillna("Sin dato")
    # Amenaza: UICN (GBIF) y nacional (lista MADS de flora del repo; si no, lo que reporta GBIF)
    df["uicn"], df["mads"] = None, None
    arch_am = CARPETA_LISTAS / f"amenaza_{depto}.csv"
    if arch_am.exists():
        am = pd.read_csv(arch_am).drop_duplicates("species")
        df = df.drop(columns=["uicn", "mads"]).merge(am.rename(columns={"mads_gbif": "mads"}), on="species", how="left")
    # Lista oficial completa (pipeline/04) si existe; si no, la de árboles y palmas
    for nombre in ["amenazadas_mads_0126.csv", "amenazadas_mads_flora.csv"]:
        arch_mads = CARPETA_LISTAS / nombre
        if arch_mads.exists():
            mads = pd.read_csv(arch_mads).drop_duplicates("species").set_index("species")["categoria"]
            df["mads"] = df["species"].map(mads).fillna(df["mads"])
            break
    df["amenazada"] = df["uicn"].isin(AMENAZA) | df["mads"].isin(AMENAZA)
    nombres, geoms, _ = cargar_municipios(depto)
    if "h3_8" not in df.columns:
        df["h3_8"] = [h3.latlng_to_cell(la, lo, 8) for la, lo in zip(df["lat"], df["lon"])]
    df["municipio"] = asignar_municipio(df["lon"].to_numpy(), df["lat"].to_numpy(), nombres, geoms)
    return df, meta, unergy, avisos


# ================================================================ cálculo
_lgamma = np.vectorize(math.lgamma)


def _ln_comb(a, m):
    return _lgamma(a + 1) - math.lgamma(m + 1) - _lgamma(a - m + 1)


def riqueza(ni, m):
    """Indicadores de una unidad a partir de los registros por especie (ni).

    rarefactada: especies esperadas en m registros (Hurlbert 1971); NaN si hay menos de m.
    chao1: riqueza estimada con corrección de sesgo (Chao 1987).
    cobertura: proporción de la comunidad representada en la muestra (Chao y Jost 2012).
    """
    ni = np.asarray(ni, dtype=float)
    n, s = ni.sum(), len(ni)
    f1, f2 = (ni == 1).sum(), (ni == 2).sum()
    if n >= m:
        resto = n - ni
        prob = np.where(resto >= m, np.exp(_ln_comb(np.maximum(resto, m), m) - _ln_comb(np.array(n), m)), 0.0)
        rare = float((1 - prob).sum())
    else:
        rare = np.nan
    chao1 = s + f1 * (f1 - 1) / (2 * (f2 + 1))
    if f1 == 0 or n <= 1:
        cobertura = 1.0
    else:
        cobertura = 1 - f1 / n * ((n - 1) * f1 / ((n - 1) * f1 + 2 * f2))
    return {"especies": s, "n": int(n), "Rarefactada": rare, "Chao1": chao1,
            "Observada": float(s), "completitud": s / chao1 if chao1 else np.nan, "cobertura": cobertura}


def riqueza_por_unidad(df, col, m):
    conteos = df.groupby([col, "species"]).size()
    filas = {u: riqueza(g.to_numpy(), m) for u, g in conteos.groupby(level=0)}
    return pd.DataFrame.from_dict(filas, orient="index")


def puntaje_por_grupo(df, col, umbral, cob_min, metrica):
    """Percentil (0-100) de la métrica dentro de cada grupo y su promedio por unidad.

    Mezclar grupos en una sola rarefacción favorece las unidades con muestreos de varios grupos
    (cada registro de otro grupo casi siempre es una especie nueva). Aquí cada grupo se compara
    solo contra sí mismo y después se promedian los percentiles de los grupos con datos suficientes.
    """
    partes = {}
    for g, sub in df.groupby("grupo"):
        r = riqueza_por_unidad(sub, col, umbral)
        ok = (r["n"] >= umbral) & r[metrica].notna() & (r["cobertura"] >= cob_min)
        partes[g] = r.loc[ok, metrica].rank(pct=True, method="max") * 100
    p = pd.DataFrame(partes).reindex(columns=[g for g in ORDEN_GRUPOS if g in partes])
    salida = pd.DataFrame({"puntaje": p.mean(axis=1), "grupos_eval": p.notna().sum(axis=1)})
    salida["grupos_txt"] = [" · ".join(f"{g} {v:.0f}" for g, v in fila.dropna().items()) for _, fila in p.iterrows()]
    return salida


def colores_escala(valores, ok):
    """Color verde→rojo para valores entre 0 y 1; GRIS donde ok es False."""
    pos = pd.Series(valores).fillna(0).clip(0, 1).to_numpy() * (len(PALETA) - 1)
    i = np.clip(pos.astype(int), 0, len(PALETA) - 2)
    t = (pos - i)[:, None]
    rgb = (PALETA[i] * (1 - t) + PALETA[i + 1] * t).astype(int)
    return [[int(v) for v in c] + [ALFA] if s else GRIS for c, s in zip(rgb, ok)]


def clasificar(tabla, umbral, pct_hotspot, metrica, cob_min, por_grupo=False):
    """Agrega nivel y color según el percentil de la métrica entre unidades con datos suficientes.

    Suficiente = al menos `umbral` registros y cobertura de muestra >= cob_min. Sin el filtro de
    cobertura, las unidades donde casi cada registro es una especie distinta (inventarios muy
    incompletos) llegan al techo de la rarefacción y salen como falsos hotspots.
    """
    tabla = tabla.copy()
    if por_grupo:
        tabla["valor"] = tabla["puntaje"]
        tabla["suficiente"] = tabla["grupos_eval"].fillna(0) >= 1
    else:
        tabla["valor"] = tabla[metrica]
        tabla["suficiente"] = (tabla["n"] >= umbral) & tabla["valor"].notna() & (tabla["cobertura"] >= cob_min)
    ok = tabla["suficiente"]
    tabla["percentil"] = np.nan
    tabla.loc[ok, "percentil"] = tabla.loc[ok, "valor"].rank(pct=True, method="max")

    def nivel(p):
        if np.isnan(p):
            return "Pocos datos"
        if p > 1 - pct_hotspot:
            return "Hotspot"
        return "Alto" if p > 0.66 else ("Medio" if p > 0.33 else "Bajo")

    tabla["nivel"] = tabla["percentil"].map(nivel)
    tabla["color"] = colores_escala(tabla["percentil"], ok)
    etiqueta = {"Rarefactada": f"Rarefactada a {umbral} registros", "Chao1": "Chao1", "Observada": "Observada"}[metrica]
    # Tooltip: pydeck escapa el HTML de los valores, así que cada línea va en su propia columna
    tabla["l1"] = [f"{int(e)} especies observadas · {nv}" for e, nv in zip(tabla["especies"], tabla["nivel"])]
    def motivo(n, cob):
        if n < umbral:
            minimo = f"{umbral} de un mismo grupo" if por_grupo else str(umbral)
            return f"Muy pocos registros para comparar: tiene {int(n)}, se necesitan {minimo}"
        return "Muestreo muy incompleto: casi cada registro es una especie distinta"

    if por_grupo:
        tabla["l2"] = [f"Puntaje {v:.0f}/100 ({g})" if ok_ else motivo(n, c)
                       for v, g, ok_, n, c in zip(tabla["valor"], tabla["grupos_txt"], ok, tabla["n"], tabla["cobertura"])]
    else:
        tabla["l2"] = [f"{etiqueta}: {v:.1f}".replace(".", ",") if ok_ else motivo(n, c)
                       for v, ok_, n, c in zip(tabla["valor"], ok, tabla["n"], tabla["cobertura"])]
    tabla["l3"] = [f"Inventario {c:.0%} completo (Chao1)" if pd.notna(c) and n >= umbral else ""
                   for c, n in zip(tabla["completitud"], tabla["n"])]
    tabla["protegido"], tabla["vacio"] = np.nan, False
    return tabla


def por_celda(df, res, umbral, pct, metrica, cob_min, por_grupo=False):
    col = f"h3_{res}"
    mpio = df.groupby(col)["municipio"].agg(lambda m: m.mode().iat[0] if m.notna().any() else "Fuera del límite")
    t = riqueza_por_unidad(df, col, umbral).join(mpio)
    if por_grupo:
        t = t.join(puntaje_por_grupo(df, col, umbral, cob_min, metrica))
    t = t.rename_axis("h3").reset_index()
    t["nombre"] = t["municipio"]
    return clasificar(t, umbral, pct, metrica, cob_min, por_grupo)


def por_municipio(df, depto, umbral, pct, metrica, cob_min, por_grupo=False):
    nombres, geoms, _ = cargar_municipios(depto)
    con_mpio = df.dropna(subset=["municipio"])
    t = riqueza_por_unidad(con_mpio, "municipio", umbral)
    if por_grupo:
        t = t.join(puntaje_por_grupo(con_mpio, "municipio", umbral, cob_min, metrica))
    t = t.reindex(nombres)
    t[["especies", "n"]] = t[["especies", "n"]].fillna(0).astype(int)
    t = t.rename_axis("municipio").reset_index()
    t = clasificar(t, umbral, pct, metrica, cob_min, por_grupo)
    filas = []
    for (_, r), g in zip(t.iterrows(), geoms):
        for anillo in anillos(g):
            filas.append({**r.to_dict(), "nombre": r["municipio"], "poligono": anillo})
    return t, pd.DataFrame(filas)


def geometria_unidad(clave, unidad, depto):
    if unidad == "Hexágonos":
        return poligono_celda(clave)
    nombres, geoms, _ = cargar_municipios(depto)
    return dict(zip(nombres, geoms))[clave]


def marcar_proteccion(tabla, unidad, depto, union):
    """Fracción de cada hotspot dentro de las capas de conservación y si es vacío de conservación."""
    if union is None:
        return tabla
    tabla = tabla.copy()
    clave = "h3" if unidad == "Hexágonos" else "municipio"
    es_hot = tabla["nivel"] == "Hotspot"
    fr = []
    for k in tabla.loc[es_hot, clave]:
        g = geometria_unidad(k, unidad, depto)
        fr.append(g.intersection(union).area / g.area if g.area else 0.0)
    tabla.loc[es_hot, "protegido"] = fr
    tabla["vacio"] = es_hot & (tabla["protegido"].fillna(1) < UMBRAL_VACIO)
    tabla.loc[es_hot, "l3"] = [f"{l} · {p:.0%} protegido" if l else f"{p:.0%} protegido"
                               for l, p in zip(tabla.loc[es_hot, "l3"], tabla.loc[es_hot, "protegido"])]
    return tabla


def areas_que_cruzan(geom, capas, seleccion):
    """Nombres de las áreas (de las capas elegidas) que tocan una geometría."""
    return sorted({f"{p.get('nombre', 'Sin nombre')} ({n})" for n in seleccion
                   for g, p in capas.get(n, []) if g.intersects(geom)})


def capas_limites(depto, mostrar_nombres):
    nombres, geoms, contorno = cargar_municipios(depto)
    lineas_mpio = [{"path": a} for g in geoms for a in anillos(g)]
    lineas_depto = [{"path": a} for a in anillos(contorno)]
    capas = [
        pdk.Layer("PathLayer", data=lineas_mpio, get_path="path", get_color=[60, 60, 60, 230],
                  width_min_pixels=1.6, pickable=False),
        pdk.Layer("PathLayer", data=lineas_depto, get_path="path", get_color=hex_a_rgb(PURPLE_DARK) + [255],
                  width_min_pixels=3, pickable=False),
    ]
    if mostrar_nombres:
        etiquetas = [{"nombre": sin_tildes(n), "pos": list(g.representative_point().coords[0])}
                     for n, g in zip(nombres, geoms)]
        capas.append(pdk.Layer("TextLayer", data=etiquetas, get_position="pos", get_text="nombre",
                               get_size=11, get_color=[25, 25, 25], background=True,
                               get_background_color=[255, 255, 255, 190]))
    return capas


TOOLTIP_HTML = ('<div style="font-weight:700;font-size:13px;margin-bottom:3px">{nombre}</div>'
                '<div>{l1}</div><div>{l2}</div><div style="color:#6B6B80">{l3}</div>')
TOOLTIP_ESTILO = {"backgroundColor": "#FFFFFF", "color": "#1A1A2E", "fontSize": "12px", "lineHeight": "1.45",
                  "padding": "8px 12px", "borderRadius": "8px", "borderLeft": f"4px solid {PURPLE}",
                  "boxShadow": "0 2px 8px rgba(0,0,0,0.18)", "maxWidth": "280px"}


def dibujar_mapa(capas_datos, depto, mostrar_nombres, extra=None, alto=640, vista=None):
    _, _, contorno = cargar_municipios(depto)
    if vista is None:
        minx, miny, maxx, maxy = contorno.bounds
        vista = pdk.ViewState(latitude=(miny + maxy) / 2, longitude=(minx + maxx) / 2, zoom=7.1)
    fondo = pdk.Layer("PolygonLayer", data=[{"poligono": a} for a in anillos(contorno)], get_polygon="poligono",
                      get_fill_color=FONDO_SIN_DATOS, stroked=False, pickable=False)
    capas = [fondo] + capas_datos + capas_limites(depto, mostrar_nombres) + (extra or [])
    tooltip = {"html": TOOLTIP_HTML, "style": TOOLTIP_ESTILO}
    st.pydeck_chart(pdk.Deck(layers=capas, initial_view_state=vista, tooltip=tooltip,
                             map_provider="carto", map_style="light_no_labels"),
                    width="stretch", height=alto)


def hotspots_a_resaltar(tabla, solo_vacios):
    hot = tabla[tabla["nivel"] == "Hotspot"]
    return hot[hot["vacio"]] if solo_vacios else hot


def capas_hex(celdas, resaltar, solo_vacios=False):
    capas = [pdk.Layer("H3HexagonLayer", data=celdas, get_hexagon="h3", get_fill_color="color",
                       stroked=False, extruded=False, pickable=True)]
    hot = hotspots_a_resaltar(celdas, solo_vacios)
    if resaltar and len(hot):
        capas.append(pdk.Layer("H3HexagonLayer", data=hot, get_hexagon="h3", filled=False,
                               stroked=True, get_line_color=COLOR_HOTSPOT, line_width_min_pixels=2.5,
                               extruded=False, pickable=False))
    return capas


def capas_mpio(poligonos, resaltar, solo_vacios=False):
    capas = [pdk.Layer("PolygonLayer", data=poligonos, get_polygon="poligono",
                       get_fill_color="color", stroked=False, pickable=True)]
    hot = hotspots_a_resaltar(poligonos, solo_vacios)
    if resaltar and len(hot):
        capas.append(pdk.Layer("PolygonLayer", data=hot, get_polygon="poligono", filled=False,
                               stroked=True, get_line_color=COLOR_HOTSPOT, line_width_min_pixels=3))
    return capas


def capas_conservacion(capas, seleccion):
    datos = [{"poligono": a} for n in seleccion for g, _ in capas.get(n, [])
             for a in anillos(g) if g.geom_type in ("Polygon", "MultiPolygon")]
    if not datos:
        return []
    return [pdk.Layer("PolygonLayer", data=datos, get_polygon="poligono", get_fill_color=COLOR_CAPAS + [35],
                      get_line_color=COLOR_CAPAS + [230], stroked=True, line_width_min_pixels=1.8,
                      pickable=False)]


def leyenda(pct, umbral, metrica, cob_min, con_capas=False):
    stops = ", ".join(f"rgb({r},{g},{b})" for r, g, b in PALETA)
    capas_html = (f'<span style="margin-left:16px;display:inline-block;width:14px;height:14px;'
                  f'background:rgba({",".join(map(str, COLOR_CAPAS))},.15);border:2px solid {COLOR_CAPAS_HEX}"></span>'
                  "<span>Capas de conservación</span>") if con_capas else ""
    st.markdown(
        f"""<div style="display:flex;align-items:center;gap:12px;font-size:0.85rem;flex-wrap:wrap">
        <span style="display:inline-block;width:34px;height:12px;border-radius:2px;background:{COLOR_POCOS_HEX}"></span>
        <span>Pocos datos</span>
        <span style="margin-left:8px">Menor riqueza ({metrica.lower()})</span>
        <div style="width:220px;height:12px;border-radius:2px;background:linear-gradient(90deg,{stops})"></div>
        <span>Mayor riqueza</span>
        <span style="margin-left:16px;display:inline-block;width:14px;height:14px;border:2.5px solid {COLOR_HOTSPOT_HEX}"></span>
        <span>Hotspot ({int(pct*100)} % con mayor riqueza)</span>
        <span style="margin-left:16px;display:inline-block;width:14px;height:14px;background:rgba(200,200,200,.45);border:1px solid #999"></span>
        <span>Sin registros</span>{capas_html}</div>""",
        unsafe_allow_html=True)
    st.caption("El color es relativo dentro del departamento (percentil de riqueza). Azul: la zona tiene registros, "
               f"pero menos de {umbral} o un muestreo muy incompleto (cobertura menor a {cob_min:.0%}), así que no se "
               "compara. Gris: no hay registros; es falta de muestreo, no ausencia de especies.")


# ================================================================ exportar hotspots
def poligono_celda(celda):
    return Polygon([(lng, lat) for lat, lng in h3.cell_to_boundary(celda)])


def hotspots_a_poligonos(tabla, datos, depto, unidad, res, unidos=True, proteccion=None, capas=None, seleccion=()):
    """Hotspots como polígonos (unidos si son vecinos) con especies, registros, área, municipios
    y, si hay capas de conservación, el % protegido y las áreas que cruza."""
    hot = tabla[tabla["nivel"] == "Hotspot"]
    if hot.empty:
        return []
    if unidad == "Hexágonos":
        unidades = {c: poligono_celda(c) for c in hot["h3"]}
        col, mpio_de = f"h3_{res}", dict(zip(hot["h3"], hot["municipio"]))
    else:
        nombres, geoms, _ = cargar_municipios(depto)
        mp = dict(zip(nombres, geoms))
        unidades = {m: mp[m] for m in hot["municipio"] if m in mp}
        col, mpio_de = "municipio", {m: m for m in unidades}
    if unidos:
        union = unary_union([g.buffer(1e-6) for g in unidades.values()]).buffer(-1e-6)
        partes = list(union.geoms) if union.geom_type == "MultiPolygon" else [union]
        partes = [(p, [k for k, g in unidades.items() if p.buffer(1e-6).contains(g.representative_point())])
                  for p in partes]
    else:
        partes = [(g, [k]) for k, g in unidades.items()]
    salida = []
    for geom, claves in partes:
        sub = datos[datos[col].isin(claves)]
        area = a_plano(geom).area
        if proteccion is not None:
            prot = round(100 * a_plano(geom.intersection(proteccion)).area / area, 1) if area else 0.0
            cruza = ", ".join(areas_que_cruzan(geom, capas, seleccion))
        else:
            prot, cruza = None, ""
        salida.append({
            "geom": orient(geom, 1.0) if geom.geom_type == "Polygon" else geom,
            "unidades": len(claves),
            "especies": int(sub["species"].nunique()),
            "registros": int(len(sub)),
            "area_km2": round(area / 1e6, 2),
            "municipios": ", ".join(sorted({str(mpio_de[k]) for k in claves if mpio_de.get(k)})),
            "protegido_pct": prot,
            "areas_protegidas": cruza,
        })
    salida.sort(key=lambda d: (-d["especies"], -d["area_km2"]))
    for i, d in enumerate(salida, 1):
        d["nombre"] = f"Hotspot {i}"
    return salida


def _anillo_kml(coords):
    return " ".join(f"{x:.6f},{y:.6f},0" for x, y in coords)


def _poligono_kml(p):
    interiores = "".join(f"<innerBoundaryIs><LinearRing><coordinates>{_anillo_kml(r.coords)}</coordinates>"
                         "</LinearRing></innerBoundaryIs>" for r in p.interiors)
    return (f"<Polygon><outerBoundaryIs><LinearRing><coordinates>{_anillo_kml(p.exterior.coords)}"
            f"</coordinates></LinearRing></outerBoundaryIs>{interiores}</Polygon>")


def a_kmz(polis, titulo, descripcion):
    """KMZ (KML comprimido) para Google Earth, QGIS, ArcGIS."""
    marcas = []
    for d in polis:
        g = d["geom"]
        partes = list(g.geoms) if g.geom_type == "MultiPolygon" else [g]
        geometria = "".join(_poligono_kml(p) for p in partes)
        if len(partes) > 1:
            geometria = f"<MultiGeometry>{geometria}</MultiGeometry>"
        info = (f"<b>{escape(d['nombre'])}</b><br/>Especies: {d['especies']}<br/>Registros: {d['registros']}"
                f"<br/>Área: {d['area_km2']} km²<br/>Municipios: {escape(d['municipios'])}"
                + (f"<br/>Área protegida: {d['protegido_pct']} %<br/>Cruza: {escape(d['areas_protegidas']) or 'ninguna'}"
                   if d["protegido_pct"] is not None else ""))
        marcas.append(f"<Placemark><name>{escape(d['nombre'])}</name><description><![CDATA[{info}]]></description>"
                      f"<styleUrl>#hotspot</styleUrl>{geometria}</Placemark>")
    kml = ('<?xml version="1.0" encoding="UTF-8"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
           f"<name>{escape(titulo)}</name><description>{escape(descripcion)}</description>"
           '<Style id="hotspot"><LineStyle><color>ff4e1b2d</color><width>2.5</width></LineStyle>'
           '<PolyStyle><color>992730d7</color></PolyStyle></Style>' + "".join(marcas) + "</Document></kml>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("doc.kml", kml)
    return buf.getvalue()


def a_geojson(polis, descripcion):
    feats = [{"type": "Feature", "geometry": mapping(d["geom"]),
              "properties": {k: d[k] for k in ("nombre", "unidades", "especies", "registros", "area_km2", "municipios",
                                               "protegido_pct", "areas_protegidas")}}
             for d in polis]
    return json.dumps({"type": "FeatureCollection", "name": "hotspots", "description": descripcion,
                       "features": feats}, ensure_ascii=False).encode("utf-8")


# ================================================================ consulta por predio
def capas_predio(predio, zona, sel):
    color = np.where(sel["amenazada"].to_numpy()[:, None], [[215, 48, 39, 230]],
                     np.where((sel["origen"] == "Endémica").to_numpy()[:, None], [[123, 76, 201, 230]],
                              [[90, 90, 90, 160]]))
    puntos = pd.DataFrame({
        "lon": sel["lon"], "lat": sel["lat"], "color": color.tolist(), "nombre": sel["species"],
        "l1": [f"{g} · UICN {u if pd.notna(u) else 'NE'} · MADS {m if pd.notna(m) else '-'}"
               for g, u, m in zip(sel["grupo"], sel["uicn"], sel["mads"])],
        "l2": [f"{f if pd.notna(f) else 'Sin fuente'} {int(a) if pd.notna(a) else ''}".strip()
               for f, a in zip(sel["fuente"], sel["year"])],
        "l3": "",
    })
    lineas = [{"path": a} for a in anillos(predio)] if predio.geom_type in ("Polygon", "MultiPolygon") else []
    capas = [pdk.Layer("PolygonLayer", data=[{"poligono": a} for a in anillos(zona)], get_polygon="poligono",
                       get_fill_color=[120, 170, 230, 30], get_line_color=[60, 120, 200, 200],
                       stroked=True, line_width_min_pixels=1.5, pickable=False)]
    if lineas:
        capas.append(pdk.Layer("PathLayer", data=lineas, get_path="path", get_color=[0, 0, 0, 255],
                               width_min_pixels=3, pickable=False))
    capas.append(pdk.Layer("ScatterplotLayer", data=puntos, get_position=["lon", "lat"], get_fill_color="color",
                           get_radius=40, radius_min_pixels=3.5, stroked=True, get_line_color=[255, 255, 255],
                           line_width_min_pixels=0.6, pickable=True))
    return capas


def consultar_predio(predio, radio_km, datos, tabla, unidad, depto, capas, umbral, doi):
    """Registros, especies, hotspot más cercano y áreas de conservación alrededor de un predio."""
    predio_m = a_plano(predio)
    radio_m = radio_km * 1000 if radio_km > 0 or predio_m.area > 0 else 100  # punto o línea sin radio
    zona_m = predio_m.buffer(radio_m) if radio_m > 0 else predio_m
    zona = a_wgs84(zona_m)
    minx, miny, maxx, maxy = zona.bounds
    cerca = datos[datos["lon"].between(minx, maxx) & datos["lat"].between(miny, maxy)]
    sel = cerca[shapely.contains_xy(zona, cerca["lon"].to_numpy(), cerca["lat"].to_numpy())].copy()
    sel["dist_km"] = shapely.distance(predio_m, a_plano(shapely.points(sel["lon"], sel["lat"]))) / 1000

    sp = (sel.groupby("species")
             .agg(Grupo=("grupo", "first"), Familia=("family", "first"), Origen=("origen", "first"),
                  UICN=("uicn", "first"), MADS=("mads", "first"), Amenazada=("amenazada", "any"),
                  Registros=("species", "size"), dist=("dist_km", "min"), ultimo=("year", "max"))
             .reset_index()
             .rename(columns={"species": "Especie", "dist": "Distancia al predio (km)", "ultimo": "Último registro"}))
    sp["Distancia al predio (km)"] = sp["Distancia al predio (km)"].round(2)
    sp = sp.sort_values(["Amenazada", "Origen", "Grupo", "Especie"], ascending=[False, True, True, True],
                        key=lambda c: c.eq("Endémica") if c.name == "Origen" else c)
    sp["iNaturalist"] = "https://www.inaturalist.org/taxa/search?q=" + sp["Especie"].str.replace(" ", "+")

    hot = tabla[tabla["nivel"] == "Hotspot"]
    if hot.empty:
        dist_hot, hotspot_txt = None, "Sin hotspots"
    else:
        clave = "h3" if unidad == "Hexágonos" else "municipio"
        union_hot = unary_union([geometria_unidad(k, unidad, depto) for k in hot[clave]])
        dist_hot = predio_m.distance(a_plano(union_hot)) / 1000
        hotspot_txt = "Dentro de un hotspot" if dist_hot == 0 else f"{dist_hot:,.1f} km"

    filas = []
    for nombre_capa, entidades in capas.items():
        for g, prop in entidades:
            if g.intersects(zona):
                d = predio_m.distance(a_plano(g)) / 1000
                filas.append({"Capa": nombre_capa, "Área": prop.get("nombre", "Sin nombre"),
                              "Categoría": prop.get("categoria", ""), "Distancia al predio (km)": round(d, 2),
                              "Relación": "Se traslapa con el predio" if d == 0 else "Dentro del radio"})
    areas = pd.DataFrame(filas, columns=["Capa", "Área", "Categoría", "Distancia al predio (km)", "Relación"])
    areas = areas.sort_values("Distancia al predio (km)")

    # Texto de resumen
    n_sp, amen = len(sp), sp[sp["Amenazada"]]
    lista_amen = "; ".join(f"{r.Especie} ({', '.join(x for x in [f'UICN {r.UICN}' if r.UICN in AMENAZA else '', f'MADS {r.MADS}' if r.MADS in AMENAZA else ''] if x)})"
                           for r in amen.head(12).itertuples())
    if len(amen) > 12:
        lista_amen += f" y {len(amen) - 12} más"
    compl = riqueza(sel.groupby("species").size().to_numpy(), umbral)["completitud"] if len(sel) else np.nan
    zona_txt = f"en un radio de {radio_km:g} km alrededor del predio" if radio_km > 0 else "dentro del predio"
    partes = [f"{zona_txt[0].upper()}{zona_txt[1:]} hay {len(sel):,} registros de {n_sp:,} especies "
              f"({', '.join(sorted(sel['grupo'].unique())) or 'ningún grupo'}), según GBIF (https://doi.org/{doi})"
              f"{' e inventarios de Unergy' if (sel['fuente'] == 'Unergy').any() else ''}"
              f"{' y árboles de resoluciones de la CAR' if (sel['fuente'] == 'CAR').any() else ''}."]
    if n_sp:
        partes.append(f"De ellas, {len(amen)} están en alguna categoría de amenaza (Lista Roja UICN o "
                      f"Res. 0126 de 2024 del MADS){': ' + lista_amen if len(amen) else ''}. "
                      f"{int((sp['Origen'] == 'Endémica').sum())} son endémicas de Colombia.")
    if dist_hot is not None:
        partes.append("El predio se traslapa con un hotspot de biodiversidad del departamento." if dist_hot == 0
                      else f"El hotspot de biodiversidad más cercano está a {dist_hot:,.1f} km.")
    traslapes = areas[areas["Distancia al predio (km)"] == 0]
    if len(traslapes):
        partes.append("Se traslapa con: " + "; ".join(f"{r['Área']} ({r['Capa']})" for _, r in traslapes.iterrows()) + ".")
    elif len(areas):
        r = areas.iloc[0]
        partes.append(f"No se traslapa con las capas de conservación; la más cercana es {r['Área']} "
                      f"({r['Capa']}), a {r['Distancia al predio (km)']:,.1f} km.")
    if pd.notna(compl):
        partes.append(f"La completitud estimada del inventario en esta área es de {compl:.0%} (Chao1); "
                      + ("la información secundaria es insuficiente y se recomienda caracterización en campo."
                         if compl < 0.6 else "la información secundaria es razonablemente representativa."))
    else:
        partes.append("No hay registros suficientes para estimar la completitud; se recomienda caracterización en campo.")

    dx, dy = maxx - minx, maxy - miny
    vista = pdk.ViewState(latitude=(miny + maxy) / 2, longitude=(minx + maxx) / 2,
                          zoom=float(np.clip(math.log2(360 / max(dx, dy, 1e-4)) - 0.6, 6, 15)))
    return {"registros": sel, "especies": sp, "hotspot_txt": hotspot_txt, "areas": areas, "vista": vista, "zona": zona,
            "capas": capas_predio(predio, zona, sel), "resumen": " ".join(partes)}


# ================================================================ barra lateral
def titulo_con_boton(titulo, texto_boton, clave_pills, valores):
    """Título de un filtro con un botón a la derecha que vuelve a seleccionar todas sus opciones."""
    c1, c2 = st.columns([3, 1])
    c1.markdown(f"**{titulo}**")
    c2.button(texto_boton, key=f"btn_{clave_pills}", type="tertiary",
              on_click=lambda: st.session_state.update({clave_pills: list(valores)}))


deptos = departamentos()
if not deptos:
    st.title("Hotspots de biodiversidad")
    st.info("No hay datos todavía. Corre pipeline/01_procesar_gbif.py.")
    st.stop()

# Valores iniciales de los filtros con botón "Todos" (se manejan por session_state, sin default=)
for clave, inicial in {"pills_grupos": ORDEN_GRUPOS, "pills_origen": ORIGENES, "pills_amenaza": []}.items():
    st.session_state.setdefault(clave, list(inicial))

with st.sidebar:
    st.header("Filtros")
    depto = st.selectbox("Departamento", deptos, format_func=str.title)
    unidad = st.radio("Unidad de análisis", ["Hexágonos", "Municipios"], horizontal=True)
    res = 6
    if unidad == "Hexágonos":
        res = st.select_slider("Tamaño de hexágono", options=[5, 6, 7, 8], value=7,
                               format_func=lambda r: TAMANO_CELDA[r])
    metrica = st.radio("Métrica de riqueza", list(METRICAS), horizontal=True, key="metrica",
                       help=" ".join(f"{k}: {v}" for k, v in METRICAS.items()))
    titulo_con_boton("Grupos", "Todos", "pills_grupos", ORDEN_GRUPOS)
    grupos_sel = st.pills("Grupos", ORDEN_GRUPOS, selection_mode="multi",
                          format_func=lambda g: f"{ICONOS_GRUPO[g]} {g}", label_visibility="collapsed",
                          key="pills_grupos") or []
    combinar = st.radio("Comparar grupos", ["Por separado", "Todos juntos"], horizontal=True, key="combinar",
                        disabled=len(grupos_sel) < 2,
                        help="Por separado (recomendado): cada grupo se compara solo contra sí mismo y se "
                             "promedian los percentiles. Todos juntos: una sola rarefacción con todos los "
                             "registros, que favorece las zonas con muestreos de varios grupos.")
    por_grupo = combinar == "Por separado" and len(grupos_sel) > 1
    st.markdown("**Fuente de los datos**")
    firma = firma_unergy()
    fuentes_disp = ["GBIF"] + sorted({f for f, _, _ in firma}, key=list(FUENTES).index)
    fuentes_sel = st.pills("Fuente", fuentes_disp, selection_mode="multi", default=fuentes_disp,
                           format_func=lambda f: FUENTES[f], label_visibility="collapsed",
                           key="pills_fuente") or []
    titulo_con_boton("Origen", "Todos", "pills_origen", ORIGENES)
    origen_sel = st.pills("Origen", ORIGENES, selection_mode="multi",
                          format_func=lambda o: f"{ICONOS_ORIGEN[o]} {o}", label_visibility="collapsed",
                          key="pills_origen",
                          help="Según las listas de distribución de GBIF para Colombia "
                               "(pipeline/02_origen_especies.py). Deja solo «Endémica» para mapear endemismos.") or []
    c1, c2, c3 = st.columns([2.2, 1.6, 1])
    c1.markdown("**Estado de amenaza**")
    c2.button("Solo amenazadas", key="btn_amenazadas", type="tertiary",
              on_click=lambda: st.session_state.update(pills_amenaza=["CR", "EN", "VU"]))
    c3.button("Todas", key="btn_todas_amenaza", type="tertiary",
              on_click=lambda: st.session_state.update(pills_amenaza=[]))
    cats_sel = st.pills("Amenaza", list(CATEGORIAS), selection_mode="multi",
                        format_func=lambda c: CATEGORIAS[c], label_visibility="collapsed",
                        key="pills_amenaza",
                        help="Sin selección cuenta todas las especies. Al elegir una o varias categorías, "
                             "el mapa cuenta solo esas especies. NE incluye las que no tienen "
                             "evaluación UICN.") or []
    if cats_sel:
        fuentes_am = st.pills("Según", ["UICN", "MADS"], selection_mode="multi", default=["UICN", "MADS"],
                              key="pills_fuente_amenaza",
                              help="UICN: Lista Roja global. MADS: Resolución 0126 de 2024.") or []
    else:
        fuentes_am = ["UICN", "MADS"]
    resaltar = st.checkbox("Resaltar hotspots", value=True)
    nombres_mpio = st.checkbox("Nombres de municipios", value=True)

    firma_c = firma_capas(depto)
    capas_disp = cargar_capas(depto, firma_c)
    capas_sel, solo_vacios = [], False
    if capas_disp:
        st.markdown("**Capas de conservación**")
        capas_sel = st.pills("Capas", list(capas_disp), selection_mode="multi", default=list(capas_disp),
                             label_visibility="collapsed", key="pills_capas") or []
        solo_vacios = st.checkbox("Resaltar solo vacíos de conservación", value=False, disabled=not capas_sel,
                                  help=f"Hotspots con menos del {int(UMBRAL_VACIO*100)} % de su área dentro "
                                       "de las capas elegidas.")
    else:
        st.caption("Capas de conservación: aún no hay. Corre pipeline/05_capas_conservacion.py.")
    union_sel = union_capas(depto, tuple(capas_sel), firma_c)

    st.divider()
    st.subheader("Inventarios (Unergy y CAR)")

    with st.expander("Opciones avanzadas"):
        umbral_def = {5: 50, 6: 20, 7: 10, 8: 5}[res] if unidad == "Hexágonos" else 20
        umbral = st.number_input("Mínimo de registros para evaluar una unidad", 1, 500, umbral_def, 1,
                                 key=f"umbral_{unidad}_{res}",
                                 help="Por debajo de este valor no hay muestreo suficiente para "
                                      "comparar; la unidad se muestra en gris.")
        cob_min = st.slider("Cobertura mínima de muestra", 0.0, 0.9, 0.5, 0.05, key="cob_min",
                            help="Proporción de la comunidad representada en los registros (Chao y Jost 2012). "
                                 "Las unidades por debajo se muestran en gris: tienen tan pocos registros "
                                 "repetidos que no se pueden comparar.")
        pct_hot = st.slider("Qué % de unidades marcar como hotspot", 5, 25, 10, step=5, key="pct_hot",
                            help="Con 10, se marca como hotspot el 10 % de las unidades con mayor riqueza "
                                 "(entre las que tienen datos suficientes). No es un número de especies: "
                                 "subirlo marca más zonas, bajarlo deja solo las más ricas.") / 100
        archivo_proy = st.file_uploader("Proyectos (CSV con nombre, lat, lon)", type="csv")

df, meta, unergy, avisos = preparar(depto, firma, firma_listas())

with st.sidebar:
    if unergy is None:
        st.caption("Aún no hay inventarios para este departamento. Agrega Excel en data/unergy/ "
                   "con el formato de plantillas/inventario_unergy.xlsx.")
    else:
        for fuente, sub in unergy.groupby("fuente"):
            st.caption(f"{FUENTES[fuente]}: {sub['species'].nunique():,} especies en "
                       f"{sub['institutionCode'].nunique()} proyecto(s) o resolución(es).")
    if avisos:
        with st.expander("Revisión de los Excel"):
            for a in avisos:
                st.write("- " + a)

if not (CARPETA_LISTAS / f"origen_{depto}.csv").exists():
    st.sidebar.caption("Origen: aún sin datos. Corre pipeline/02_origen_especies.py.")
atributos = (df.drop_duplicates("species").set_index("species")[["grupo", "family", "origen", "uicn", "mads", "amenazada"]])
df_todas_fuentes = df[df["grupo"].isin(grupos_sel) & df["origen"].isin(origen_sel)]
df = df[df["grupo"].isin(grupos_sel) & df["origen"].isin(origen_sel) & df["fuente"].isin(fuentes_sel)]
df_consulta = df  # la consulta por predio no aplica el filtro de amenaza
def en_categorias(tabla):
    """True en las filas cuya especie está en las categorías de amenaza elegidas."""
    en_cat = pd.Series(False, index=tabla.index)
    if "UICN" in fuentes_am:
        en_cat |= tabla["uicn"].fillna("NE").isin(cats_sel)
    if "MADS" in fuentes_am:
        en_cat |= tabla["mads"].isin(cats_sel)
    return en_cat


if cats_sel:
    df = df[en_categorias(df)]
    if not (CARPETA_LISTAS / f"amenaza_{depto}.csv").exists():
        st.sidebar.caption("Amenaza: falta la categoría UICN. Corre pipeline/03_amenaza_especies.py.")
    if not (CARPETA_LISTAS / "amenazadas_mads_0126.csv").exists():
        st.sidebar.caption("MADS: por ahora solo árboles y palmas. Corre pipeline/04_lista_mads_0126.py "
                           "para la lista oficial completa.")
if df.empty:
    st.warning("Ningún registro cumple los filtros elegidos. Prueba quitando alguno.")
    st.stop()

proyectos = None
if archivo_proy:
    proyectos = pd.read_csv(archivo_proy)
    proyectos.columns = [c.strip().lower() for c in proyectos.columns]
    if not {"nombre", "lat", "lon"} <= set(proyectos.columns):
        st.sidebar.error("El CSV de proyectos debe tener columnas nombre, lat, lon.")
        proyectos = None


def capas_proyectos():
    if proyectos is None:
        return []
    return [pdk.Layer("ScatterplotLayer", data=proyectos, get_position=["lon", "lat"],
                      get_radius=1200, radius_min_pixels=6, get_fill_color=[20, 60, 160],
                      get_line_color=[255, 255, 255], stroked=True, line_width_min_pixels=1.5),
            pdk.Layer("TextLayer", data=proyectos.assign(nombre=proyectos["nombre"].astype(str).map(sin_tildes)),
                      get_position=["lon", "lat"], get_text="nombre",
                      get_size=12, get_color=[20, 60, 160], get_pixel_offset=[0, -14],
                      background=True, get_background_color=[255, 255, 255, 190])]


def construir(datos):
    if unidad == "Hexágonos":
        t = marcar_proteccion(por_celda(datos, res, umbral, pct_hot, metrica, cob_min, por_grupo), unidad, depto, union_sel)
        return t, capas_hex(t, resaltar, solo_vacios)
    t, poligonos = por_municipio(datos, depto, umbral, pct_hot, metrica, cob_min, por_grupo)
    t = marcar_proteccion(t, unidad, depto, union_sel)
    poligonos = poligonos.drop(columns=["protegido", "vacio", "l3"]).merge(
        t[["municipio", "protegido", "vacio", "l3"]], on="municipio", how="left")
    return t, capas_mpio(poligonos, resaltar, solo_vacios)


def capas_extra():
    return capas_conservacion(capas_disp, capas_sel) + capas_proyectos()


# ================================================================ página
doi = meta.get("doi_gbif", "")
st.markdown(f"""<div class="unergy-header"><div class="unergy-logo">🌿</div><div>
<h1>Hotspots de biodiversidad en {depto.title()}</h1>
<p>Unergy · Equipo forestal · Riqueza de especies registrada ({', '.join(grupos_sel) or 'sin grupos'}).
Fuente: <a href="https://doi.org/{doi}" target="_blank">GBIF {doi}</a>{' e inventarios Unergy' if 'Unergy' in fuentes_sel else ''}{' y resoluciones CAR' if 'CAR' in fuentes_sel else ''}</p>
</div></div>""", unsafe_allow_html=True)


def tarjetas(items):
    html = "".join(f'<div class="metric-card"><div class="label">{k}</div><div class="value">{v}</div></div>'
                   for k, v in items)
    st.markdown(f'<div class="metric-grid">{html}</div>', unsafe_allow_html=True)

tab_mapa, tab_consulta, tab_sdm, tab_detalle, tab_metodo = st.tabs(
    ["Mapa de hotspots", "Consulta por predio", "Distribución modelada", "Municipios y especies", "Método"])

pred_sdm, met_sdm, info_sdm = cargar_sdm(depto, firma_sdm(depto))


def especies_modeladas(con_amenaza=True):
    """Métricas de las especies válidas que cumplen los filtros de grupo, origen y (opcional) amenaza."""
    auc_min = st.session_state.get("auc_min_sdm", 0.7)
    m = met_sdm[met_sdm["valido"] & (met_sdm["auc_espacial"] >= auc_min)].drop(columns="grupo")
    m = m.join(atributos, on="species", how="inner")
    m = m[m["grupo"].isin(grupos_sel) & m["origen"].isin(origen_sel)]
    if con_amenaza and cats_sel:
        m = m[en_categorias(m)]
    return m


def modelos_en_zona(zona, especies_registradas):
    """Especies con hábitat idóneo modelado en la zona; marca si tienen registros en ella."""
    celdas = set(h3.geo_to_cells(zona.__geo_interface__, 7))
    c = zona.centroid
    celdas.add(h3.latlng_to_cell(c.y, c.x, 7))
    m = especies_modeladas(con_amenaza=False)
    p = pred_sdm[pred_sdm["h3_7"].isin(celdas) & pred_sdm["species"].isin(m["species"])]
    if p.empty:
        return pd.DataFrame()
    t = (p.groupby("species")["idoneidad"].max().rename("Idoneidad máx.").round(2).reset_index()
         .merge(m[["species", "grupo", "family", "uicn", "mads", "amenazada", "auc_espacial"]], on="species"))
    t["Registrada en el área"] = t["species"].isin(especies_registradas)
    t = t.rename(columns={"species": "Especie", "grupo": "Grupo", "family": "Familia", "uicn": "UICN",
                          "mads": "MADS", "amenazada": "Amenazada", "auc_espacial": "AUC del modelo"})
    return t.sort_values(["Registrada en el área", "Amenazada", "Idoneidad máx."], ascending=[True, False, False])


def categorias_txt(uicn, mads):
    """'UICN EN, MADS VU' con las categorías de amenaza que apliquen, o ''."""
    return ", ".join(f"{n} {c}" for n, c in (("UICN", uicn), ("MADS", mads)) if c in AMENAZA)


def leyenda_sdm(texto_bajo, texto_alto, con_hotspot):
    stops = ", ".join(f"rgb({r},{g},{b})" for r, g, b in PALETA)
    hot = (f'<span style="margin-left:16px;display:inline-block;width:14px;height:14px;'
           f'border:2.5px solid {COLOR_HOTSPOT_HEX}"></span><span>Hotspot modelado ({int(pct_hot*100)} %)</span>'
           if con_hotspot else "")
    st.markdown(f"""<div style="display:flex;align-items:center;gap:12px;font-size:0.85rem;flex-wrap:wrap">
        <span>{texto_bajo}</span>
        <div style="width:220px;height:12px;border-radius:2px;background:linear-gradient(90deg,{stops})"></div>
        <span>{texto_alto}</span>{hot}
        <span style="margin-left:16px;display:inline-block;width:14px;height:14px;background:rgba(200,200,200,.45);border:1px solid #999"></span>
        <span>Sin hábitat idóneo o sin predicción</span></div>""", unsafe_allow_html=True)

def linea_de_tiempo(datos):
    """Interruptor sobre el mapa para limitar los registros a un rango de años (como en Google Earth).

    Devuelve los datos filtrados y el rango elegido (None si está apagado). Los registros sin año
    (por ejemplo, los inventarios Unergy) quedan fuera mientras está activo.
    """
    anios = datos["year"].dropna()
    c1, c2 = st.columns([1, 3], vertical_alignment="center")
    activo = c1.toggle("🕒 Línea de tiempo", key="linea_tiempo", disabled=anios.empty,
                       help="Muestra el mapa solo con los registros del periodo elegido."
                            + (" Los registros seleccionados no tienen año." if anios.empty else ""))
    if not activo:
        return datos, None
    a_min, a_max = int(anios.min()), int(anios.max())
    rango = c2.slider("Años", a_min, a_max, (max(a_min, 2015), a_max), key="rango_anios",
                      label_visibility="collapsed")
    sin_anio = datos["year"].isna().sum()
    if sin_anio:
        st.caption(f"{sin_anio:,} registros sin año (por ejemplo, inventarios Unergy) no se muestran "
                   "mientras la línea de tiempo está activa.")
    filtrados = datos[datos["year"].between(*rango)]
    if filtrados.empty:
        st.warning("No hay registros en ese periodo; se muestra el mapa con todos los años.")
        return datos, None
    return filtrados, rango


with tab_mapa:
    df_mapa, rango_anios = linea_de_tiempo(df)
    tabla, capas = construir(df_mapa)
    hot = tabla[tabla["nivel"] == "Hotspot"]
    unidad_txt = "hexágonos" if unidad == "Hexágonos" else "municipios"
    mpios_hot = hot["municipio"].dropna().value_counts()
    items = [
        ("Especies registradas", f"{df_mapa['species'].nunique():,}"),
        (f"Hotspots ({unidad_txt})", f"{len(hot):,}"),
        ("Municipio con más hotspots" if unidad == "Hexágonos" else "Municipio con mayor riqueza",
         mpios_hot.index[0] if len(mpios_hot) and unidad == "Hexágonos"
         else tabla.sort_values("valor", ascending=False)["municipio"].iat[0]),
    ]
    if union_sel is not None and len(hot):
        items.append(("Hotspots sin protección", f"{int(hot['vacio'].sum())} de {len(hot)}"))
    tarjetas(items)
    dibujar_mapa(capas, depto, nombres_mpio, capas_extra())
    leyenda(pct_hot, umbral, metrica, cob_min, bool(capas_sel))

    st.markdown("#### Descargar hotspots")
    if hot.empty:
        st.caption("No hay hotspots con los filtros actuales.")
    else:
        forma = st.radio("Forma", ["Polígonos unidos", "Unidades separadas"], horizontal=True,
                         key="forma_descarga",
                         help="Unidos: los hexágonos (o municipios) hotspot vecinos se funden en un solo "
                              "polígono. Separados: una geometría por hexágono o municipio.")
        polis = hotspots_a_poligonos(tabla, df_mapa, depto, unidad, res, unidos=forma == "Polígonos unidos",
                                     proteccion=union_sel, capas=capas_disp, seleccion=capas_sel)
        desc = (f"Hotspots de biodiversidad en {depto.title()} (Unergy). Unidad: {unidad.lower()}"
                f"{'' if unidad == 'Municipios' else f', {TAMANO_CELDA[res]}'}; top {int(pct_hot*100)} % de riqueza "
                f"({metrica.lower()}). "
                f"Grupos: {', '.join(grupos_sel)}. Fuente: {', '.join(fuentes_sel)}. Origen: {', '.join(origen_sel)}. "
                f"Amenaza: {', '.join(cats_sel) if cats_sel else 'todas'}."
                + (f" Años: {rango_anios[0]} a {rango_anios[1]}." if rango_anios else "")
                + (f" Capas de conservación: {', '.join(capas_sel)}." if capas_sel else ""))
        base = f"hotspots_{depto}_{'hex' if unidad == 'Hexágonos' else 'municipios'}"
        st.caption(f"{len(polis)} polígono(s), {sum(p['area_km2'] for p in polis):,.1f} km² en total. "
                   "El KMZ abre en Google Earth; el GeoJSON, en QGIS o ArcGIS (desde ahí se exporta a shapefile).")
        if union_sel is not None:
            n_vacios = sum(p["protegido_pct"] < UMBRAL_VACIO * 100 for p in polis)
            st.caption(f"{n_vacios} de {len(polis)} polígono(s) tienen menos del {int(UMBRAL_VACIO*100)} % de su "
                       f"área en {', '.join(capas_sel)}: son vacíos de conservación, candidatos para nuevas "
                       "figuras de protección o para orientar compensaciones.")
        c1, c2 = st.columns(2)
        c1.download_button("⬇ Descargar KMZ", a_kmz(polis, f"Hotspots {depto.title()}", desc),
                           file_name=f"{base}.kmz", mime="application/vnd.google-earth.kmz",
                           width="stretch")
        c2.download_button("⬇ Descargar GeoJSON", a_geojson(polis, desc),
                           file_name=f"{base}.geojson", mime="application/geo+json", width="stretch")
        columnas = {"nombre": "Hotspot", "especies": "Especies", "registros": "Registros", "unidades": "Unidades",
                    "area_km2": "Área (km²)", "municipios": "Municipios"}
        if union_sel is not None:
            columnas |= {"protegido_pct": "% protegido", "areas_protegidas": "Áreas que cruza"}
        st.dataframe(pd.DataFrame([{k: d[k] for k in columnas} for d in polis]).rename(columns=columnas),
                     hide_index=True, width="stretch")

with tab_consulta:
    st.write("Sube el polígono de un predio, proyecto o trámite para ver qué se ha registrado alrededor, "
             "cuáles especies están amenazadas o son endémicas, qué tan cerca está de un hotspot y qué "
             "áreas de conservación toca.")
    c1, c2 = st.columns([2, 1])
    archivo_predio = c1.file_uploader("Predio o área de interés", type=FORMATOS, key="archivo_predio",
                                      help="KMZ, KML, GeoJSON o shapefile en .zip (con .shp, .shx, .dbf y .prj).")
    radio_km = c2.slider("Radio de búsqueda (km)", 0.0, 20.0, 5.0, 0.5,
                         help="Distancia alrededor del predio en la que se buscan registros.")
    st.caption("Usa los filtros de grupos, fuente y origen de la barra lateral (no el de amenaza). "
               "La distancia al hotspot usa el mapa con todos sus filtros.")
    predio = None
    if archivo_predio is not None:
        try:
            predio = unary_union([g for g, _ in leer_vector(archivo_predio.getvalue(), archivo_predio.name)])
        except Exception as e:  # el archivo lo sube el usuario: cualquier error de lectura se le muestra
            st.error(f"No pude leer el archivo: {e}")
    if predio is None:
        st.info("Esperando el archivo del predio.")
    else:
        consulta = consultar_predio(predio, radio_km, df_consulta, tabla, unidad, depto, capas_disp, umbral, doi)
        sel, sp = consulta["registros"], consulta["especies"]
        tarjetas([
            ("Especies registradas", f"{sp['Especie'].nunique():,}"),
            ("Amenazadas (UICN o MADS)", f"{int(sp['Amenazada'].sum()):,}"),
            ("Endémicas", f"{int((sp['Origen'] == 'Endémica').sum()):,}"),
            ("Hotspot más cercano", consulta["hotspot_txt"]),
        ])
        if len(sel) < umbral:
            st.warning(f"Solo hay {len(sel)} registros en el área de consulta. Esto indica falta de muestreo, "
                       "no baja biodiversidad: conviene caracterizar en campo.")
        dibujar_mapa(capas, depto, False, capas_extra() + consulta["capas"], alto=520, vista=consulta["vista"])
        st.caption("Puntos: 🔴 amenazada · 🟣 endémica · ⚪ otras. Línea negra: predio; azul claro: radio de búsqueda.")

        mod = modelos_en_zona(consulta["zona"], set(sp["Especie"])) if pred_sdm is not None else pd.DataFrame()
        resumen = consulta["resumen"]
        if len(mod):
            nuevas = mod[~mod["Registrada en el área"]]
            amen_n = nuevas[nuevas["Amenazada"]]
            resumen += (f" Además, los modelos de distribución estiman hábitat idóneo para {len(nuevas)} especies "
                        f"sin registros en el área" + (f", de ellas {len(amen_n)} amenazadas: "
                        + "; ".join(f"{r.Especie} ({categorias_txt(r.UICN, r.MADS)})" for r in amen_n.head(10).itertuples())
                        if len(amen_n) else "") + "; su presencia debe verificarse en campo.")
        st.markdown("#### Resumen")
        st.text_area("Texto para el informe o concepto técnico", resumen, height=190, key="resumen_predio")
        if not consulta["areas"].empty:
            st.markdown("#### Áreas de conservación cercanas")
            st.dataframe(consulta["areas"], hide_index=True, width="stretch")
        st.markdown("#### Especies registradas")
        st.dataframe(sp, hide_index=True, width="stretch",
                     column_config={"iNaturalist": st.column_config.LinkColumn(display_text="ver")})
        st.download_button("⬇ Descargar lista de especies (CSV)", sp.to_csv(index=False).encode("utf-8-sig"),
                           file_name=f"especies_{Path(archivo_predio.name).stem}.csv", mime="text/csv")
        if len(mod):
            st.markdown("#### Especies con hábitat idóneo según los modelos")
            st.caption("Especies que probablemente están en el área por clima, relieve y cobertura, aunque no tengan "
                       "registros ahí. Primero las que no tienen registros y, entre ellas, las amenazadas. Son "
                       "predicciones: sirven para orientar la caracterización en campo.")
            st.dataframe(mod, hide_index=True, width="stretch")

with tab_sdm:
    if pred_sdm is None:
        st.info("Aún no hay modelos de distribución para este departamento. Se generan con "
                "pipeline/gee_covariables.js (variables ambientales en Google Earth Engine) y "
                "pipeline/06_modelos_distribucion.py; ver el README.")
    else:
        st.slider("Confiabilidad mínima del modelo (AUC espacial)", 0.70, 0.95, 0.70, 0.05, key="auc_min_sdm",
                  help="Subirlo deja solo las especies cuyos modelos predicen mejor zonas que no usaron para "
                       "entrenar. 0,7 aceptable, 0,8 bueno, 0,9 excelente. También se aplica en la consulta por predio.")
        msel = especies_modeladas()
        st.write("Hábitat idóneo estimado con modelos de distribución de especies: dónde es probable que esté "
                 "cada especie según el clima, el relieve y la cobertura de bosque actual, aunque nadie la haya "
                 "registrado ahí.")
        st.caption(f"{info_sdm.get('especies_validas', len(met_sdm[met_sdm['valido']]))} de "
                   f"{info_sdm.get('especies_modeladas', len(met_sdm))} especies modeladas pasaron la validación "
                   f"espacial (AUC ≥ {info_sdm.get('auc_min', 0.7)}); {len(msel)} cumplen la confiabilidad elegida y "
                   f"los filtros de grupo, origen y amenaza. Solo se modelan especies con al menos {info_sdm.get('min_celdas', 20)} localidades, "
                   f"así que pesan más las comunes. Modelos del {info_sdm.get('fecha', 's.f.')}.")
        vista_sdm = st.radio("Ver", ["Riqueza modelada", "Una especie"], horizontal=True, key="vista_sdm")
        p = pred_sdm[pred_sdm["species"].isin(msel["species"])]
        if p.empty:
            st.info("Ninguna especie modelada cumple los filtros actuales.")
        elif vista_sdm == "Riqueza modelada":
            st.warning("Mapa exploratorio. En la prueba de sensibilidad los hotspots modelados cambiaron de lugar "
                       "al quitar los datos de los monitoreos mineros: la riqueza modelada todavía sigue en parte "
                       "a dónde se ha muestreado. Úsalo para orientar muestreos, no como conclusión. Los mapas por "
                       "especie con AUC alto son más confiables.")
            t = p.groupby("h3_7")["species"].nunique().rename("n").reset_index().rename(columns={"h3_7": "h3"})
            t["percentil"] = t["n"].rank(pct=True, method="max")
            t["nivel"] = np.where(t["percentil"] > 1 - pct_hot, "Hotspot", "")
            t["color"] = colores_escala(t["percentil"], [True] * len(t))
            mpio = municipio_de_celdas(depto, tuple(t["h3"]))
            t["nombre"] = t["h3"].map(mpio)
            t["municipio"] = t["nombre"]
            t["l1"] = [f"{n} especies con hábitat idóneo" for n in t["n"]]
            t["l2"] = [f"Hotspot modelado (top {int(pct_hot*100)} %)" if nv else f"Percentil {pc:.0%}"
                       for nv, pc in zip(t["nivel"], t["percentil"])]
            t["l3"] = ""
            t["vacio"] = False
            hot_m = t[t["nivel"] == "Hotspot"]
            items = [("Especies en el mapa", f"{p['species'].nunique():,}"),
                     ("Hotspots modelados (hexágonos)", f"{len(hot_m):,}"),
                     ("Municipio con más hotspots", hot_m["municipio"].value_counts().index[0] if len(hot_m) else "-")]
            if union_sel is not None and len(hot_m):
                lat, lon = np.array([h3.cell_to_latlng(c) for c in hot_m["h3"]]).T
                prot = shapely.contains_xy(union_sel, lon, lat)
                t.loc[hot_m.index, "vacio"] = ~prot
                items.append(("Hotspots modelados sin protección", f"{int((~prot).sum())} de {len(hot_m)}"))
            tarjetas(items)
            dibujar_mapa(capas_hex(t, resaltar, solo_vacios), depto, nombres_mpio, capas_extra())
            leyenda_sdm("Menos especies", "Más especies", True)
            st.caption("Hexágonos de ≈5 km². Riqueza modelada = número de especies cuyo hábitat se estima idóneo. "
                       "Es una predicción, no un inventario: sirve para orientar muestreos y priorizar, no reemplaza "
                       "la caracterización en campo.")
        else:
            opciones = msel.sort_values(["amenazada", "species"], ascending=[False, True])
            etiquetas = {r.species: f"{r.species} ({r.grupo}{' · ' + categorias_txt(r.uicn, r.mads) if categorias_txt(r.uicn, r.mads) else ''})"
                         for r in opciones.itertuples()}
            especie = st.selectbox("Especie (amenazadas primero)", list(etiquetas), format_func=etiquetas.get,
                                   key="especie_sdm")
            r = opciones.set_index("species").loc[especie]
            pe = p[p["species"] == especie].rename(columns={"h3_7": "h3"})
            escala = ((pe["idoneidad"] - r["umbral"]) / max(1 - r["umbral"], 1e-6)).clip(0, 1)
            pe = pe.assign(color=colores_escala(escala, [True] * len(pe)), nivel="", vacio=False,
                           nombre=especie, l1=[f"Idoneidad {v:.2f}" for v in pe["idoneidad"]],
                           l2=f"Umbral de presencia: {r['umbral']:.2f}", l3="")
            regs = df_todas_fuentes[df_todas_fuentes["species"] == especie]
            puntos = pd.DataFrame({"lon": regs["lon"], "lat": regs["lat"], "nombre": especie,
                                   "l1": regs["fuente"], "l2": regs["year"].map(lambda a: f"{int(a)}" if pd.notna(a) else ""),
                                   "l3": ""})
            items = [("Localidades usadas", f"{int(r['n_celdas'])}"),
                     ("AUC espacial", f"{r['auc_espacial']:.2f}"),
                     ("Hábitat idóneo", f"≈{len(pe) * 5.16:,.0f} km²")]
            if union_sel is not None and len(pe):
                lat, lon = np.array([h3.cell_to_latlng(c) for c in pe["h3"]]).T
                items.append(("Hábitat idóneo protegido", f"{shapely.contains_xy(union_sel, lon, lat).mean():.0%}"))
            tarjetas(items)
            capa_puntos = pdk.Layer("ScatterplotLayer", data=puntos, get_position=["lon", "lat"],
                                    get_fill_color=[20, 20, 20, 220], get_radius=60, radius_min_pixels=3,
                                    stroked=True, get_line_color=[255, 255, 255], line_width_min_pixels=0.8,
                                    pickable=True)
            dibujar_mapa(capas_hex(pe, False), depto, nombres_mpio, capas_extra() + [capa_puntos])
            leyenda_sdm("Idoneidad baja", "Idoneidad alta", False)
            st.caption("Puntos negros: registros de la especie (todas las fuentes). AUC espacial: qué tan bien el "
                       "modelo predice zonas que no usó para entrenar (0,5 = azar, 1 = perfecto; desde 0,7 es "
                       "aceptable). Hábitat idóneo protegido: parte del hábitat idóneo dentro de las capas de "
                       "conservación elegidas.")

with tab_detalle:
    st.subheader("Especies por municipio")
    mp = (df.dropna(subset=["municipio"]).groupby(["municipio", "grupo"])["species"].nunique()
            .unstack(fill_value=0).reindex(columns=[g for g in ORDEN_GRUPOS if g in grupos_sel], fill_value=0))
    mp.insert(0, "Total", df.dropna(subset=["municipio"]).groupby("municipio")["species"].nunique())
    st.dataframe(mp.sort_values("Total", ascending=False), width="stretch")

    st.subheader("Lista de especies")
    nombres, _, _ = cargar_municipios(depto)
    mpio_sel = st.selectbox("Municipio", ["Todos"] + sorted(mp.index))
    if unidad == "Hexágonos":
        opciones = tabla.sort_values("especies", ascending=False)
        if mpio_sel != "Todos":
            opciones = opciones[opciones["municipio"] == mpio_sel]
        celda = st.selectbox("Hexágono (ordenados por número de especies)", ["Todo el municipio"] + list(opciones["h3"]),
                             format_func=lambda h: h if h == "Todo el municipio" else
                             f"{int(opciones.set_index('h3').loc[h, 'especies'])} especies, {opciones.set_index('h3').loc[h, 'nivel']} ({h})")
    else:
        celda = "Todo el municipio"
    sel = df if mpio_sel == "Todos" else df[df["municipio"] == mpio_sel]
    if celda != "Todo el municipio":
        sel = sel[sel[f"h3_{res}"] == celda]
    sp = (sel.groupby(["grupo", "family", "species", "origen", "uicn", "mads"], dropna=False)["year"].max()
             .rename("último registro").reset_index().sort_values(["grupo", "family", "species"]))
    sp["iNaturalist"] = "https://www.inaturalist.org/taxa/search?q=" + sp["species"].str.replace(" ", "+")
    st.caption(f"{len(sp):,} especies")
    st.dataframe(sp.rename(columns={"grupo": "Grupo", "family": "Familia", "species": "Especie",
                                    "origen": "Origen", "uicn": "UICN", "mads": "MADS"}),
                 hide_index=True, width="stretch",
                 column_config={"iNaturalist": st.column_config.LinkColumn(display_text="ver")})

with tab_metodo:
    st.markdown(f"""
**Qué muestra el mapa.** La riqueza de especies de cada unidad (hexágono o municipio), con la
métrica elegida. El color es el percentil dentro del departamento y los hotspots son el
{int(pct_hot*100)} % de unidades con mayor riqueza, entre las que tienen al menos {umbral} registros
y una cobertura de muestra de al menos {cob_min:.0%} (Chao y Jost 2012). Sin ese filtro, las unidades
donde casi cada registro es una especie distinta (inventarios muy incompletos) saldrían como falsos
hotspots.

**Métricas de riqueza.** *Rarefactada*: especies esperadas en {umbral} registros, calculada para
cada unidad (Hurlbert 1971); pone a todas las unidades en el mismo esfuerzo de muestreo y es la
recomendada para comparar. *Chao1*: riqueza estimada a partir de las especies con uno y dos
registros (Chao 1987). *Observada*: especies registradas, sin corrección. La completitud del
inventario (observada / Chao1) aparece al pasar el cursor por cada unidad.

**Capas de conservación.** Áreas protegidas (RUNAP) y otras figuras recortadas al departamento
(pipeline/05_capas_conservacion.py). Un hotspot con menos del {int(UMBRAL_VACIO*100)} % de su
área dentro de las capas elegidas se marca como vacío de conservación.

**Consulta por predio.** Busca los registros dentro del radio elegido alrededor del polígono
subido, la distancia de cada especie al predio, el hotspot más cercano y las áreas de
conservación que toca. La distancia y el área se calculan en EPSG:9377.

**Sinónimos.** Los nombres de data/listas/sinonimos.csv se unifican al nombre aceptado (el que
usa GBIF) antes de cualquier cálculo, para que una especie no cuente dos veces por llamarse
distinto en cada fuente (p. ej. *Prosopis juliflora* y *Neltuma juliflora*).

**Distribución modelada.** Modelos de distribución de especies (pipeline/06) por hexágono de
≈5 km², con clima (WorldClim), relieve (SRTM) y cobertura de bosque actual (ESA WorldCover).
Presencias: hexágonos con registros de la especie; fondo: hexágonos con registros de cualquier
especie del mismo grupo (fondo de grupo objetivo, que compensa el sesgo de muestreo). Modelo de
presencia/fondo equivalente a MaxEnt (regresión logística regularizada con términos cuadráticos),
validado con bloques espaciales; solo se muestran las especies con AUC espacial ≥ 0,7. Los
hexágonos con condiciones fuera del rango muestreado no se predicen. Una especie está en un
hexágono si la idoneidad supera el percentil 10 de sus presencias. La riqueza modelada es la
suma de especies presentes; es una predicción y no reemplaza el muestreo en campo.

**Grupos.** Aves, mamíferos, herpetos (reptiles y anfibios), invertebrados (insectos,
arácnidos y otros) y flora. Con *Comparar grupos: por separado* (recomendado), la métrica se
calcula para cada grupo, cada unidad recibe su percentil dentro del grupo y el puntaje (0-100)
es el promedio de los grupos con datos suficientes. Así un muestreo de flora no compite con uno
de aves, y una zona con monitoreos de varios grupos no sale rica solo por mezclar grupos.

**Origen.** Endémica, nativa o introducida según las listas de distribución para Colombia
que reúne GBIF (por ejemplo GRIIS Colombia y el Catálogo de Plantas y Líquenes de Colombia).
Las especies sin información quedan como "Sin dato"; se pueden corregir a mano en
data/listas/correcciones_origen.csv.

**Amenaza.** Categoría UICN según GBIF y categoría nacional según la Res. 0126 de 2024
(lista oficial publicada en el SiB Colombia, https://doi.org/10.15472/frowz3). Al elegir CR, EN o VU en el filtro de amenaza, el mapa cuenta solo las especies en esas categorías según la UICN, la lista nacional o ambas.

**Descarga.** Los hotspots se pueden bajar como KMZ (Google Earth) o GeoJSON (QGIS, ArcGIS),
unidos en polígonos o como unidades separadas. El área se calcula en MAGNA-SIRGAS Colombia
Origen Nacional (EPSG:9377).

**Limitación.** La riqueza registrada depende de cuánto se ha muestreado: zonas con estudios
ambientales o mucha observación de aves se ven más ricas. La rarefacción reduce ese sesgo pero
no lo elimina (una unidad con 10 registros de un solo grupo sigue siendo poco representativa).
Por eso las unidades con pocos registros se muestran en gris y las vacías no se colorean.

**Datos.** GBIF.org (18 November 2026) GBIF Occurrence Download
https://doi.org/{meta.get('doi_gbif', '')}. Límites municipales: DANE, Marco Geoestadístico Nacional 2018.
""")
