"""
Hotspots de biodiversidad - vista general de registros GBIF por departamento.

Correr:  streamlit run app.py
Datos:   data/procesados/<departamento>/registros.parquet y metadata.json
         (los genera pipeline/01_procesar_gbif.py)
         data/unergy/*.xlsx  inventarios de Unergy (opcional, formato en plantillas/)
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pydeck as pdk
import streamlit as st

from unergy import cargar_unergy

st.set_page_config(page_title="Hotspots de biodiversidad", page_icon="🌿", layout="wide")

CARPETA_DATOS = Path(__file__).parent / "data" / "procesados"
CARPETA_UNERGY = Path(__file__).parent / "data" / "unergy"
TAMANO_CELDA = {5: "≈253 km²", 6: "≈36 km²", 7: "≈5 km²"}
GRIS_SIN_DATOS = [170, 170, 170, 90]
# Escala de verdes: bajo -> alto
ESCALA = np.array([[237, 248, 233], [186, 228, 179], [116, 196, 118],
                   [49, 163, 84], [0, 109, 44], [0, 68, 27]])


# ---------------------------------------------------------------- datos
@st.cache_data
def departamentos():
    if not CARPETA_DATOS.exists():
        return []
    return sorted(p.name for p in CARPETA_DATOS.iterdir() if (p / "registros.parquet").exists())


@st.cache_data
def cargar(depto):
    df = pd.read_parquet(CARPETA_DATOS / depto / "registros.parquet")
    meta_path = CARPETA_DATOS / depto / "metadata.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    return df, meta


@st.cache_data
def cargar_inventarios(firma):
    """firma cambia cuando cambian los Excel, para recargar."""
    return cargar_unergy(CARPETA_UNERGY)


def firma_unergy():
    return tuple((p.name, p.stat().st_mtime) for p in sorted(CARPETA_UNERGY.glob("*.xlsx")))


def resumir_celdas(df, res, umbral):
    col = f"h3_{res}"
    g = (df.groupby(col)
           .agg(registros=("species", "size"), especies=("species", "nunique"),
                ultimo_anio=("year", "max"), fuentes=("fuente", "nunique"))
           .reset_index().rename(columns={col: "h3"}))
    g["datos_suficientes"] = g["registros"] >= umbral
    return g


def colorear(valores, suficientes):
    """Color en escala log; gris si la celda tiene pocos registros."""
    v = np.log1p(valores.to_numpy(dtype=float))
    v = (v - v.min()) / (v.max() - v.min()) if v.max() > v.min() else np.zeros_like(v)
    pos = v * (len(ESCALA) - 1)
    i = np.clip(pos.astype(int), 0, len(ESCALA) - 2)
    t = (pos - i)[:, None]
    rgb = (ESCALA[i] * (1 - t) + ESCALA[i + 1] * t).astype(int)
    colores = [list(c) + [190] for c in rgb]
    return [c if ok else GRIS_SIN_DATOS for c, ok in zip(colores, suficientes)]


def leer_proyectos(archivo):
    p = pd.read_csv(archivo)
    p.columns = [c.strip().lower() for c in p.columns]
    faltan = {"nombre", "lat", "lon"} - set(p.columns)
    if faltan:
        st.sidebar.error(f"Al CSV de proyectos le faltan columnas: {', '.join(faltan)}")
        return None
    return p


# ---------------------------------------------------------------- barra lateral
deptos = departamentos()
if not deptos:
    st.title("Hotspots de biodiversidad")
    st.info("No hay datos todavía. Corre pipeline/01_procesar_gbif.py y copia la carpeta "
            "del departamento en data/procesados/.")
    st.stop()

with st.sidebar:
    st.header("Filtros")
    depto = st.selectbox("Departamento", deptos, format_func=str.title)
    df_gbif, meta = cargar(depto)

    unergy_todo, avisos_unergy = cargar_inventarios(firma_unergy()) if CARPETA_UNERGY.exists() else (None, [])
    unergy = None
    if unergy_todo is not None:
        unergy = unergy_todo[unergy_todo["departamento"] == depto].drop(columns="departamento")
        unergy = unergy if len(unergy) else None
    df_total = pd.concat([df_gbif, unergy], ignore_index=True) if unergy is not None else df_gbif

    res = st.select_slider("Tamaño de celda", options=[5, 6, 7], value=6,
                           format_func=lambda r: TAMANO_CELDA[r])
    capa = st.radio("Colorear por", ["Número de especies", "Número de registros"])

    grupos = sorted(df_total["grupo"].unique())
    grupos_sel = st.multiselect("Grupos", grupos, default=grupos)
    fuentes = sorted(df_total["fuente"].unique())
    fuentes_sel = st.multiselect("Fuentes", fuentes, default=fuentes)

    anios = df_total["year"].dropna()
    a_min, a_max = int(anios.min()), int(anios.max())
    rango = st.slider("Años", a_min, a_max, (a_min, a_max))
    incluir_sin_anio = st.checkbox("Incluir registros sin año", value=True)

    umbral = st.number_input("Mínimo de registros para considerar la celda con datos",
                             min_value=1, value=20, step=5,
                             help="Las celdas por debajo se pintan de gris: no hay "
                                  "suficiente muestreo para saber qué tan diversas son.")
    archivo_proy = st.file_uploader("Proyectos (CSV con nombre, lat, lon)", type="csv")

    st.divider()
    st.subheader("Datos Unergy")
    if unergy is None:
        st.caption("Aún no hay inventarios de Unergy para este departamento. Agrega Excel "
                   "en data/unergy/ con el formato de plantillas/inventario_unergy.xlsx.")
        ver_puntos_unergy = False
    else:
        st.caption(f"{len(unergy):,} presencias de {unergy['species'].nunique():,} especies "
                   f"en {unergy['institutionCode'].nunique()} proyecto(s). Se pueden quitar "
                   f"desmarcando 'Unergy' en Fuentes.")
        ver_puntos_unergy = st.checkbox("Mostrar puntos de inventarios", value=True)
    if avisos_unergy:
        with st.expander("Revisión de los Excel"):
            for a in avisos_unergy:
                st.write("- " + a)

# ---------------------------------------------------------------- filtrado
f = df_total[df_total["grupo"].isin(grupos_sel) & df_total["fuente"].isin(fuentes_sel)]
en_rango = f["year"].between(*rango)
f = f[en_rango | (f["year"].isna() & incluir_sin_anio)]

if f.empty:
    st.warning("Ningún registro cumple los filtros. Amplía los grupos, las fuentes o los años.")
    st.stop()

celdas = resumir_celdas(f, res, umbral)
metrica = "especies" if capa == "Número de especies" else "registros"
celdas["color"] = colorear(celdas[metrica], celdas["datos_suficientes"])
proyectos = leer_proyectos(archivo_proy) if archivo_proy else None

# ---------------------------------------------------------------- encabezado
st.title(f"Biodiversidad registrada en {depto.title()}")
st.caption(f"Registros de GBIF (eBird, estudios ambientales, colecciones, universidades, "
           f"iNaturalist){' e inventarios de Unergy' if unergy is not None else ''}. "
           f"Descarga GBIF: https://doi.org/{meta.get('doi_gbif', '')}")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Registros", f"{len(f):,}")
c2.metric("Especies", f"{f['species'].nunique():,}")
c3.metric("Celdas con registros", f"{len(celdas):,}")
pct = 100 * (~celdas["datos_suficientes"]).mean()
c4.metric("Celdas con pocos datos", f"{pct:.0f} %",
          help=f"Celdas con menos de {umbral} registros")

tab_mapa, tab_celda, tab_especies, tab_fuentes = st.tabs(
    ["Mapa", "Detalle de celda", "Especies y grupos", "Datos y método"])

# ---------------------------------------------------------------- mapa
with tab_mapa:
    capas = [pdk.Layer(
        "H3HexagonLayer", data=celdas, get_hexagon="h3", get_fill_color="color",
        get_line_color=[255, 255, 255, 80], line_width_min_pixels=0.5,
        pickable=True, extruded=False, stroked=True)]
    if ver_puntos_unergy:
        pts = f[f["fuente"] == "Unergy"]
        capas.append(pdk.Layer("ScatterplotLayer", data=pts, get_position=["lon", "lat"],
                               get_radius=120, radius_min_pixels=3, get_fill_color=[30, 90, 200, 200],
                               pickable=False))
    if proyectos is not None:
        capas += [
            pdk.Layer("ScatterplotLayer", data=proyectos, get_position=["lon", "lat"],
                      get_radius=1500, radius_min_pixels=6, get_fill_color=[200, 30, 30],
                      get_line_color=[255, 255, 255], stroked=True, line_width_min_pixels=1.5,
                      pickable=True),
            pdk.Layer("TextLayer", data=proyectos, get_position=["lon", "lat"],
                      get_text="nombre", get_size=13, get_color=[60, 20, 20],
                      get_pixel_offset=[0, -16]),
        ]
    vista = pdk.ViewState(latitude=float(f["lat"].mean()), longitude=float(f["lon"].mean()),
                          zoom=7, pitch=0)
    tooltip = {"html": "<b>{especies}</b> especies<br/>{registros} registros<br/>"
                       "Último registro: {ultimo_anio}<br/>Celda: {h3}",
               "style": {"fontSize": "12px"}}
    st.pydeck_chart(pdk.Deck(layers=capas, initial_view_state=vista, tooltip=tooltip,
                             map_provider="carto", map_style="light"),
                    width="stretch", height=620)
    st.caption(f"Verde más oscuro = más {metrica} (escala logarítmica). Gris = menos de "
               f"{umbral} registros. Sin color = ningún registro: es falta de muestreo, "
               f"no ausencia de especies. Puntos azules = inventarios Unergy; rojos = proyectos.")

# ---------------------------------------------------------------- detalle de celda
with tab_celda:
    st.subheader("Celdas con más especies")
    ranking = celdas.sort_values("especies", ascending=False)
    st.dataframe(ranking[["h3", "especies", "registros", "ultimo_anio", "fuentes"]].head(30),
                 hide_index=True, width="stretch")

    celda = st.selectbox("Ver especies de una celda", ranking["h3"],
                         format_func=lambda h: f"{h} ({int(ranking.set_index('h3').loc[h, 'especies'])} especies)")
    sp = (f[f[f"h3_{res}"] == celda]
          .groupby(["grupo", "family", "species"], dropna=False)
          .agg(registros=("species", "size"), ultimo_anio=("year", "max"))
          .reset_index().sort_values(["grupo", "registros"], ascending=[True, False]))
    sp["iNaturalist"] = "https://www.inaturalist.org/taxa/search?q=" + sp["species"].str.replace(" ", "+")
    st.dataframe(sp, hide_index=True, width="stretch",
                 column_config={"iNaturalist": st.column_config.LinkColumn(display_text="ver")})

# ---------------------------------------------------------------- especies y grupos
with tab_especies:
    col_a, col_b = st.columns(2)
    with col_a:
        st.subheader("Especies por grupo")
        st.bar_chart(f.groupby("grupo")["species"].nunique().sort_values(ascending=False))
    with col_b:
        st.subheader("Registros por año")
        st.line_chart(f.dropna(subset=["year"]).groupby("year").size())

    st.subheader("Especies más registradas")
    top = (f.groupby(["grupo", "species"]).size().rename("registros")
             .reset_index().sort_values("registros", ascending=False).head(50))
    st.dataframe(top, hide_index=True, width="stretch")

# ---------------------------------------------------------------- datos y método
with tab_fuentes:
    st.subheader("Registros por fuente")
    st.bar_chart(f["fuente"].value_counts())
    st.subheader("Instituciones que más aportan")
    st.dataframe(f.groupby(["fuente", "institutionCode"]).size().rename("registros")
                   .reset_index().sort_values("registros", ascending=False).head(25),
                 hide_index=True, width="stretch")
    st.subheader("Procesamiento")
    if meta:
        st.json(meta)
    st.markdown(
        "Los registros vienen de una descarga formal de GBIF (citable con el DOI). "
        "Se quitaron registros sin coordenadas, con coordenadas problemáticas o con "
        "incertidumbre alta, los no identificados a especie y los duplicados. "
        "Los registros de iNaturalist de especies amenazadas tienen la ubicación "
        "desplazada, así que no sirven a escala de predio.")
