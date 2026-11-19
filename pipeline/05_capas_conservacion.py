"""
05 - Capas de conservación (áreas protegidas y otras figuras) recortadas al departamento.

La app las usa para saber qué parte de cada hotspot está protegida y marcar los
vacíos de conservación (hotspots fuera de estas capas).

Capas sugeridas:
    - RUNAP (áreas protegidas del SINAP): https://runap.parquesnacionales.gov.co
    - Reservas forestales de Ley 2ª de 1959 (MADS / SIAC)
    - Zonificación de POMCA, DRMI, áreas de importancia estratégica (la CAR)
Se aceptan shapefile en .zip (con .shp, .shx, .dbf y .prj), GeoJSON, KML o KMZ.

1. Guardar los archivos en data/crudos/ (esa carpeta no se sube a GitHub).
2. Editar DEPTO y CAPAS abajo.
3. Correr desde la carpeta del repo:
       pip install pyshp shapely pyproj
       python pipeline/05_capas_conservacion.py
4. Subir data/capas/<DEPTO>/ a GitHub.

Salida: data/capas/<DEPTO>/<nombre_capa>.geojson con las propiedades nombre y categoria.
"""
import json
import sys
from pathlib import Path

import shapely
from shapely.geometry import mapping, shape
from shapely.ops import unary_union

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from geodatos import leer_vector  # noqa: E402
from unergy import normalizar_texto  # noqa: E402

# ================== CONFIGURACIÓN (editar) ==================
DEPTO = "cesar"
CAPAS = [
    # archivo en data/crudos, nombre que verá la app, campo con el nombre del área, campo con la categoría
    {"archivo": "runap.zip", "capa": "RUNAP", "campo_nombre": "nombre", "campo_categoria": "categoria"},
    # {"archivo": "ley2.zip", "capa": "Reservas Ley 2", "campo_nombre": "NOMBRE", "campo_categoria": "TIPO"},
]
TOLERANCIA_GRADOS = 0.0005  # simplificación (~50 m) para que el GeoJSON no pese tanto
# ============================================================

RAIZ = Path(__file__).resolve().parent.parent
LIMITES = RAIZ / "data" / "limites" / "municipios_colombia.geojson"
SALIDA = RAIZ / "data" / "capas" / DEPTO


def contorno_departamento(depto):
    datos = json.loads(LIMITES.read_text(encoding="utf-8"))
    geoms = [shape(f["geometry"]) for f in datos["features"]
             if normalizar_texto(f["properties"]["DPTO_CNMBR"]) == normalizar_texto(depto)]
    if not geoms:
        sys.exit(f"No encontré el departamento '{depto}' en {LIMITES.name}.")
    return unary_union(geoms)


def campo(props, nombre):
    """Busca el campo sin importar mayúsculas."""
    for k, v in props.items():
        if k.lower() == nombre.lower():
            return v
    return None


def main():
    contorno = contorno_departamento(DEPTO)
    shapely.prepare(contorno)
    SALIDA.mkdir(parents=True, exist_ok=True)
    for c in CAPAS:
        ruta = RAIZ / "data" / "crudos" / c["archivo"]
        if not ruta.exists():
            print(f"[{c['capa']}] no existe {ruta}, la salto.")
            continue
        entidades = leer_vector(ruta.read_bytes(), ruta.name)
        feats = []
        for g, p in entidades:
            if not contorno.intersects(g):
                continue
            recorte = g.intersection(contorno).simplify(TOLERANCIA_GRADOS, preserve_topology=True)
            if recorte.is_empty or recorte.area == 0:
                continue
            feats.append({"type": "Feature", "geometry": mapping(recorte),
                          "properties": {"nombre": str(campo(p, c["campo_nombre"]) or "Sin nombre"),
                                         "categoria": str(campo(p, c["campo_categoria"]) or c["capa"])}})
        if not feats:
            campos = sorted(entidades[0][1]) if entidades else []
            print(f"[{c['capa']}] ninguna entidad cae en {DEPTO}. Campos del archivo: {campos}")
            continue
        destino = SALIDA / f"{c['capa']}.geojson"
        destino.write_text(json.dumps({"type": "FeatureCollection", "name": c["capa"], "features": feats},
                                      ensure_ascii=False), encoding="utf-8")
        print(f"[{c['capa']}] {len(feats)} áreas -> {destino.relative_to(RAIZ)}")


if __name__ == "__main__":
    main()
