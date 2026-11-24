"""
08 - Modelos de distribución de BioModelos (Instituto Humboldt).

Detecta los GeoTIFF (o ZIP con GeoTIFF) nuevos en data/biomodelos/entrada/, los inspecciona,
los pasa a hexágonos H3 de cada departamento y actualiza el catálogo. Los que ya estaban
incorporados no se vuelven a procesar (se reconocen por su contenido, no por el nombre).

La app hace lo mismo sola cada vez que arranca y encuentra archivos nuevos; este script sirve
para procesar en el computador o en Colab y subir los resultados ya listos.

Correr desde la carpeta del repo:
    python pipeline/08_biomodelos.py                 # todos los departamentos con datos
    python pipeline/08_biomodelos.py --depto cesar   # solo uno
    python pipeline/08_biomodelos.py --forzar        # reprocesar todo (p. ej. tras reemplazar un archivo dañado)

Qué genera (ver data/biomodelos/LEEME.md):
    data/biomodelos/catalogo/catalogo_modelos.csv           catálogo completo (se regenera)
    data/biomodelos/metadata/metadatos_modelos.csv          una fila nueva por modelo; se llena a mano
    data/biomodelos/procesados/vistas/<ID>.png              vista previa nacional de cada modelo
    data/biomodelos/procesados/<depto>/biomodelos_h3.parquet  presencia por hexágono H3 (res 5, 6, 7)
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import biomodelos as bm  # noqa: E402


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--depto", action="append", help="Departamento (se puede repetir). Por defecto, todos.")
    p.add_argument("--forzar", action="store_true", help="Reprocesar también los ya incorporados.")
    a = p.parse_args()

    archivos = bm.listar_entrada()
    print(f"{len(archivos)} GeoTIFF en {bm.ENTRADA.relative_to(bm.RAIZ)} (incluidos los de ZIP)")
    r = bm.actualizar(deptos=a.depto, forzar=a.forzar)
    print(f"\nNuevos: {r['nuevos']} · reprocesados: {r['reprocesados']} · sin cambios: {r['sin_cambios']} · "
          f"copias repetidas: {r['duplicados']} · con error: {r['errores']}")

    cat, _ = bm.cargar((a.depto or bm.deptos_disponibles() or ["cesar"])[0])
    pend = cat[cat["estado_identificacion"] == bm.PENDIENTE]
    usados = cat[cat["modelo_principal"] == "Sí"]
    print(f"Catálogo: {len(cat)} modelos, {usados['nombre_cientifico'].nunique()} especies en el análisis, "
          f"{len(pend)} pendientes de identificar.")
    if len(pend):
        print("\nPendientes de identificar (escribir el nombre en data/biomodelos/metadata/metadatos_modelos.csv):")
        print(pend[["id_modelo", "archivo_original", "tipo_valores"]].to_string(index=False))
    otros = cat[cat["uso_en_analisis"].str.startswith("No") & (cat["estado_identificacion"] != bm.PENDIENTE)]
    if len(otros):
        print("\nNo entran al análisis:")
        print(otros[["id_modelo", "archivo_original", "uso_en_analisis"]].to_string(index=False))


if __name__ == "__main__":
    main()
