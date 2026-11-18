"""
Hotspots de biodiversidad por departamento.

Correr:  streamlit run app.py
Datos:   data/procesados/<departamento>/registros.parquet  (pipeline/01_procesar_gbif.py)
         data/unergy/*.xlsx                                 (inventarios Unergy, opcional)
         data/limites/municipios_colombia.geojson           (DANE, MGN 2018)
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import h3
import pydeck as pdk
import shapely
import streamlit as st
from shapely.geometry import shape
from shapely.ops import unary_union

from unergy import cargar_unergy, normalizar_texto

st.set_page_config(page_title="Hotspots de biodiversidad", page_icon="🌿", layout="wide")

RAIZ = Path(__file__).parent
CARPETA_DATOS = RAIZ / "data" / "procesados"
CARPETA_UNERGY = RAIZ / "data" / "unergy"
ARCHIVO_LIMITES = RAIZ / "data" / "limites" / "municipios_colombia.geojson"
CARPETA_LISTAS = RAIZ / "data" / "listas"
ORIGENES = ["Endémica", "Nativa", "Introducida", "Sin dato"]

TAMANO_CELDA = {5: "≈253 km²", 6: "≈36 km²", 7: "≈5 km²", 8: "≈0,7 km²"}
GRUPOS = {
    "Aves": "Aves", "Mamíferos": "Mamíferos",
    "Reptiles": "Herpetos", "Anfibios": "Herpetos", "Herpetos": "Herpetos",
    "Insectos": "Invertebrados", "Arácnidos": "Invertebrados",
    "Otros invertebrados": "Invertebrados", "Invertebrados": "Invertebrados",
    "Plantas": "Flora", "Flora": "Flora",
}
ORDEN_GRUPOS = ["Aves", "Mamíferos", "Herpetos", "Invertebrados", "Flora"]

# ---- Colores (editar aquí) -----------------------------------------------
# Degradado de menos a más especies. Pon los códigos hex que quieras, de claro a oscuro.
# Colores corporativos Unergy (los mismos de la app de compensación)
PURPLE, PURPLE_DARK, PURPLE_MID, PURPLE_LIGHT = "#7B4CC9", "#2D1B4E", "#9B6FE8", "#D9CEEF"
# Mapa: verde (menos especies) -> amarillo -> rojo (más especies)
PALETA_HEX = ["#1A9850", "#66BD63", "#A6D96A", "#FEE08B", "#FDAE61", "#F46D43", "#D73027"]
COLOR_HOTSPOT_HEX = "#2D1B4E"   # borde de los hotspots (morado Unergy)
# --------------------------------------------------------------------------


def hex_a_rgb(h):
    h = h.lstrip("#")
    return [int(h[i:i + 2], 16) for i in (0, 2, 4)]


PALETA = np.array([hex_a_rgb(h) for h in PALETA_HEX])
COLOR_HOTSPOT = hex_a_rgb(COLOR_HOTSPOT_HEX)

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
  .metric-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; margin: 6px 0 14px; }}
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
  [data-testid="stSidebar"] hr {{ border-color: rgba(255,255,255,0.2) !important; }}
  [data-testid="stSidebar"] [data-testid="stFileUploaderDropzone"] {{ background: rgba(255,255,255,0.08) !important; }}
</style>
""", unsafe_allow_html=True)

GRIS = [190, 190, 190, 70]
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
    if not CARPETA_UNERGY.exists():
        return ()
    return tuple((p.name, p.stat().st_mtime) for p in sorted(CARPETA_UNERGY.glob("*.xlsx")))


@st.cache_data
def cargar_inventarios(firma):
    if not firma:
        return None, []
    return cargar_unergy(CARPETA_UNERGY)


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


@st.cache_data
def preparar(depto, firma, incluir_unergy):
    df, meta = cargar_gbif(depto)
    unergy_todo, avisos = cargar_inventarios(firma)
    unergy = None
    if unergy_todo is not None:
        unergy = unergy_todo[unergy_todo["departamento"] == normalizar_texto(depto)]
        unergy = unergy.drop(columns="departamento") if len(unergy) else None
    if unergy is not None and incluir_unergy:
        df = pd.concat([df, unergy], ignore_index=True)
    df = df.assign(grupo=df["grupo"].map(GRUPOS)).dropna(subset=["grupo"])
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
    nombres, geoms, _ = cargar_municipios(depto)
    if "h3_8" not in df.columns:
        df["h3_8"] = [h3.latlng_to_cell(la, lo, 8) for la, lo in zip(df["lat"], df["lon"])]
    df["municipio"] = asignar_municipio(df["lon"].to_numpy(), df["lat"].to_numpy(), nombres, geoms)
    return df, meta, unergy, avisos


# ================================================================ cálculo
def clasificar(tabla, umbral, pct_hotspot):
    """Agrega nivel y color según el percentil de riqueza entre unidades con datos suficientes."""
    tabla = tabla.copy()
    tabla["suficiente"] = tabla["n"] >= umbral
    ok = tabla["suficiente"]
    tabla["percentil"] = np.nan
    tabla.loc[ok, "percentil"] = tabla.loc[ok, "especies"].rank(pct=True, method="max")

    def nivel(p):
        if np.isnan(p):
            return "Pocos datos"
        if p > 1 - pct_hotspot:
            return "Hotspot"
        return "Alto" if p > 0.66 else ("Medio" if p > 0.33 else "Bajo")

    tabla["nivel"] = tabla["percentil"].map(nivel)
    pos = tabla["percentil"].fillna(0).to_numpy() * (len(PALETA) - 1)
    i = np.clip(pos.astype(int), 0, len(PALETA) - 2)
    t = (pos - i)[:, None]
    rgb = (PALETA[i] * (1 - t) + PALETA[i + 1] * t).astype(int)
    tabla["color"] = [[int(v) for v in c] + [ALFA] if s else GRIS for c, s in zip(rgb, ok)]
    return tabla


def por_celda(df, res, umbral, pct):
    col = f"h3_{res}"
    t = (df.groupby(col).agg(especies=("species", "nunique"), n=("species", "size"),
                             municipio=("municipio", lambda m: m.mode().iat[0] if m.notna().any() else "Fuera del límite"))
           .reset_index().rename(columns={col: "h3"}))
    t["nombre"] = t["municipio"]
    return clasificar(t, umbral, pct)


def por_municipio(df, depto, umbral, pct):
    nombres, geoms, _ = cargar_municipios(depto)
    t = (df.dropna(subset=["municipio"]).groupby("municipio")
           .agg(especies=("species", "nunique"), n=("species", "size")))
    t = t.reindex(nombres).fillna(0).reset_index().rename(columns={"index": "municipio"})
    t["especies"] = t["especies"].astype(int)
    t = clasificar(t, umbral, pct)
    filas = []
    for (_, r), g in zip(t.iterrows(), geoms):
        for anillo in anillos(g):
            filas.append({**r.to_dict(), "nombre": r["municipio"], "poligono": anillo})
    return t, pd.DataFrame(filas)


# ================================================================ mapa
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


def dibujar_mapa(capas_datos, depto, mostrar_nombres, extra=None, alto=640):
    _, _, contorno = cargar_municipios(depto)
    minx, miny, maxx, maxy = contorno.bounds
    vista = pdk.ViewState(latitude=(miny + maxy) / 2, longitude=(minx + maxx) / 2, zoom=7.1)
    capas = capas_datos + capas_limites(depto, mostrar_nombres) + (extra or [])
    tooltip = {"html": "<b>{nombre}</b><br/>{especies} especies<br/>{nivel}",
               "style": {"fontSize": "12px"}}
    st.pydeck_chart(pdk.Deck(layers=capas, initial_view_state=vista, tooltip=tooltip,
                             map_provider="carto", map_style="light_no_labels"),
                    width="stretch", height=alto)


def capas_hex(celdas, resaltar):
    capas = [pdk.Layer("H3HexagonLayer", data=celdas, get_hexagon="h3", get_fill_color="color",
                       stroked=False, extruded=False, pickable=True)]
    hot = celdas[celdas["nivel"] == "Hotspot"]
    if resaltar and len(hot):
        capas.append(pdk.Layer("H3HexagonLayer", data=hot, get_hexagon="h3", filled=False,
                               stroked=True, get_line_color=COLOR_HOTSPOT, line_width_min_pixels=2.5,
                               extruded=False, pickable=False))
    return capas


def capas_mpio(poligonos, resaltar):
    capas = [pdk.Layer("PolygonLayer", data=poligonos, get_polygon="poligono",
                       get_fill_color="color", stroked=False, pickable=True)]
    hot = poligonos[poligonos["nivel"] == "Hotspot"]
    if resaltar and len(hot):
        capas.append(pdk.Layer("PolygonLayer", data=hot, get_polygon="poligono", filled=False,
                               stroked=True, get_line_color=COLOR_HOTSPOT, line_width_min_pixels=3))
    return capas


def leyenda(pct, umbral):
    stops = ", ".join(f"rgb({r},{g},{b})" for r, g, b in PALETA)
    st.markdown(
        f"""<div style="display:flex;align-items:center;gap:12px;font-size:0.85rem;flex-wrap:wrap">
        <span>Menos especies</span>
        <div style="width:220px;height:12px;border-radius:2px;background:linear-gradient(90deg,{stops})"></div>
        <span>Más especies</span>
        <span style="margin-left:16px;display:inline-block;width:14px;height:14px;border:2.5px solid {COLOR_HOTSPOT_HEX}"></span>
        <span>Hotspot ({int(pct*100)} % con más especies)</span>
        <span style="margin-left:16px;display:inline-block;width:14px;height:14px;background:rgba(190,190,190,.5)"></span>
        <span>Pocos datos (menos de {umbral} registros)</span></div>""",
        unsafe_allow_html=True)
    st.caption("El color es relativo dentro del departamento (percentil de riqueza). "
               "Las zonas sin color no tienen registros: es falta de muestreo, no ausencia de especies.")


# ================================================================ barra lateral
deptos = departamentos()
if not deptos:
    st.title("Hotspots de biodiversidad")
    st.info("No hay datos todavía. Corre pipeline/01_procesar_gbif.py.")
    st.stop()

with st.sidebar:
    st.header("Filtros")
    depto = st.selectbox("Departamento", deptos, format_func=str.title)
    unidad = st.radio("Unidad de análisis", ["Hexágonos", "Municipios"], horizontal=True)
    res = 6
    if unidad == "Hexágonos":
        res = st.select_slider("Tamaño de hexágono", options=[5, 6, 7, 8], value=7,
                               format_func=lambda r: TAMANO_CELDA[r])
    grupos_sel = st.multiselect("Grupos", ORDEN_GRUPOS, default=ORDEN_GRUPOS)
    origen_sel = st.multiselect("Origen", ORIGENES, default=ORIGENES,
                                help="Según las listas de distribución de GBIF para Colombia "
                                     "(pipeline/02_origen_especies.py).")
    pct_hot = st.slider("Hotspots: % de unidades con más especies", 5, 25, 10, step=5) / 100
    resaltar = st.checkbox("Resaltar hotspots", value=True)
    nombres_mpio = st.checkbox("Nombres de municipios", value=True)

    st.divider()
    st.subheader("Datos Unergy")
    firma = firma_unergy()
    incluir_unergy = st.checkbox("Incluir inventarios Unergy", value=True)

    with st.expander("Opciones avanzadas"):
        umbral_def = {5: 50, 6: 20, 7: 10, 8: 5}[res] if unidad == "Hexágonos" else 20
        umbral = st.number_input("Mínimo de registros para evaluar una unidad", 1, 500, umbral_def, 1,
                                 key=f"umbral_{unidad}_{res}",
                                 help="Por debajo de este valor no hay muestreo suficiente para "
                                      "comparar; la unidad se muestra en gris.")
        archivo_proy = st.file_uploader("Proyectos (CSV con nombre, lat, lon)", type="csv")

df, meta, unergy, avisos = preparar(depto, firma, incluir_unergy)

with st.sidebar:
    if unergy is None:
        st.caption("Aún no hay inventarios para este departamento. Agrega Excel en data/unergy/ "
                   "con el formato de plantillas/inventario_unergy.xlsx.")
    else:
        st.caption(f"{unergy['species'].nunique():,} especies en "
                   f"{unergy['institutionCode'].nunique()} proyecto(s).")
    if avisos:
        with st.expander("Revisión de los Excel"):
            for a in avisos:
                st.write("- " + a)

if not (CARPETA_LISTAS / f"origen_{depto}.csv").exists():
    st.sidebar.caption("Origen: aún sin datos. Corre pipeline/02_origen_especies.py.")
df = df[df["grupo"].isin(grupos_sel) & df["origen"].isin(origen_sel)]
if df.empty:
    st.warning("Ningún registro cumple los filtros de grupo y origen.")
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
        t = por_celda(datos, res, umbral, pct_hot)
        return t, capas_hex(t, resaltar)
    t, poligonos = por_municipio(datos, depto, umbral, pct_hot)
    return t, capas_mpio(poligonos, resaltar)


# ================================================================ página
doi = meta.get("doi_gbif", "")
st.markdown(f"""<div class="unergy-header"><div class="unergy-logo">🌿</div><div>
<h1>Hotspots de biodiversidad en {depto.title()}</h1>
<p>Unergy · Equipo forestal · Riqueza de especies registrada ({', '.join(grupos_sel)}).
Fuente: <a href="https://doi.org/{doi}" target="_blank">GBIF {doi}</a>{' e inventarios Unergy' if unergy is not None and incluir_unergy else ''}</p>
</div></div>""", unsafe_allow_html=True)


def tarjetas(items):
    html = "".join(f'<div class="metric-card"><div class="label">{k}</div><div class="value">{v}</div></div>'
                   for k, v in items)
    st.markdown(f'<div class="metric-grid">{html}</div>', unsafe_allow_html=True)

tab_mapa, tab_tiempo, tab_detalle, tab_metodo = st.tabs(
    ["Mapa de hotspots", "Multitemporal", "Municipios y especies", "Método"])

with tab_mapa:
    tabla, capas = construir(df)
    hot = tabla[tabla["nivel"] == "Hotspot"]
    unidad_txt = "hexágonos" if unidad == "Hexágonos" else "municipios"
    mpios_hot = hot["municipio"].dropna().value_counts()
    tarjetas([
        ("Especies registradas", f"{df['species'].nunique():,}"),
        (f"Hotspots ({unidad_txt})", f"{len(hot):,}"),
        ("Municipio con más hotspots" if unidad == "Hexágonos" else "Municipio con más especies",
         mpios_hot.index[0] if len(mpios_hot) and unidad == "Hexágonos"
         else tabla.sort_values("especies", ascending=False)["municipio"].iat[0]),
    ])
    dibujar_mapa(capas, depto, nombres_mpio, capas_proyectos())
    leyenda(pct_hot, umbral)

with tab_tiempo:
    st.write("Compara cómo cambia el mapa según el periodo de los registros.")
    anios = df["year"].dropna()
    periodo = st.radio("Periodo", ["Antes de 2000", "2000 a 2014", "2015 en adelante", "Personalizado"],
                       index=2, horizontal=True)
    rangos = {"Antes de 2000": (int(anios.min()), 1999), "2000 a 2014": (2000, 2014),
              "2015 en adelante": (2015, int(anios.max()))}
    if periodo == "Personalizado":
        rango = st.slider("Años", int(anios.min()), int(anios.max()), (2015, int(anios.max())))
    else:
        rango = rangos[periodo]
    sub = df[df["year"].between(*rango)]
    if sub.empty:
        st.info("No hay registros en ese periodo.")
    else:
        t_sub, capas_sub = construir(sub)
        st.caption(f"{rango[0]} a {rango[1]}: {sub['species'].nunique():,} especies, "
                   f"{(t_sub['nivel'] == 'Hotspot').sum()} hotspots.")
        dibujar_mapa(capas_sub, depto, nombres_mpio, capas_proyectos(), alto=560)
        leyenda(pct_hot, umbral)

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
    sp = (sel.groupby(["grupo", "family", "species", "origen"], dropna=False)["year"].max()
             .rename("último registro").reset_index().sort_values(["grupo", "family", "species"]))
    sp["iNaturalist"] = "https://www.inaturalist.org/taxa/search?q=" + sp["species"].str.replace(" ", "+")
    st.caption(f"{len(sp):,} especies")
    st.dataframe(sp.rename(columns={"grupo": "Grupo", "family": "Familia", "species": "Especie",
                                    "origen": "Origen"}),
                 hide_index=True, width="stretch",
                 column_config={"iNaturalist": st.column_config.LinkColumn(display_text="ver")})

with tab_metodo:
    st.markdown(f"""
**Qué muestra el mapa.** La riqueza de especies registrada en cada unidad (hexágono o municipio).
El color es el percentil dentro del departamento y los hotspots son el {int(pct_hot*100)} % de
unidades con más especies, entre las que tienen al menos {umbral} registros.

**Grupos.** Aves, mamíferos, herpetos (reptiles y anfibios), invertebrados (insectos,
arácnidos y otros) y flora.

**Origen.** Endémica, nativa o introducida según las listas de distribución para Colombia
que reúne GBIF (por ejemplo GRIIS Colombia y el Catálogo de Plantas y Líquenes de Colombia).
Las especies sin información quedan como "Sin dato"; se pueden corregir a mano en
data/listas/correcciones_origen.csv.

**Limitación.** La riqueza registrada depende de cuánto se ha muestreado: zonas con estudios
ambientales o mucha observación de aves se ven más ricas. Por eso las unidades con pocos
registros se muestran en gris y las vacías no se colorean.

**Datos.** GBIF.org (18 November 2026) GBIF Occurrence Download
https://doi.org/{meta.get('doi_gbif', '')}. Límites municipales: DANE, Marco Geoestadístico Nacional 2018.
""")
