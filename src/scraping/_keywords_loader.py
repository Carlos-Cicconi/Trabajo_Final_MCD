#!/usr/bin/env python3
# =============================================================================
# _keywords_loader.py
# Tesis: "Sistema de Recomendación Laboral - Motor de Matching Inteligente"
# Alumno: Cicconi, Carlos Alberto - Maestría en Explotación de Datos (Austral)
#
# DESCRIPCIÓN:
#   Punto único de carga de SEARCH_QUERIES para todos los scrapers de
#   src/scraping/. Reemplaza las listas hardcodeadas que tenía cada scraper:
#   ahora cada uno arma su SEARCH_QUERIES por defecto leyendo
#   data/cvs/keywords.json (generado por cv_keyword_extractor.py, editable
#   con edit_keywords.py), adaptado al formato que ese scraper necesita.
#
#   Esto permite que cada scraper siga siendo ejecutable de forma
#   independiente (`if __name__ == "__main__"`) sin necesidad de pasar por
#   run_scraping.py, y que el orquestador use keywords.json por defecto sin
#   requerir el flag --cv.
#
#   run_scraping.py --cv <otro.json> sigue funcionando: inyecta las queries
#   de ese otro archivo por encima del default de cada scraper (ver
#   inyectar_queries_en_scraper() en run_scraping.py).
# =============================================================================

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Raíz del repo derivada de este archivo (src/scraping/_keywords_loader.py
# → sube dos niveles), no hardcodeada.
BASE_DIR = Path(__file__).resolve().parents[2]
DEFAULT_KEYWORDS_JSON = BASE_DIR / "data" / "cvs" / "keywords.json"


def _leer_search_queries(keywords_path: Path) -> list[dict]:
    """Lee 'search_queries' desde el JSON de keywords_path. [] si falla."""
    if not keywords_path.exists():
        logger.warning(
            f"No se encontró {keywords_path} — SEARCH_QUERIES queda vacío. "
            f"Generalo con cv_keyword_extractor.py o editalo con edit_keywords.py."
        )
        return []

    try:
        data = json.loads(keywords_path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"Error leyendo {keywords_path}: {e} — SEARCH_QUERIES queda vacío.")
        return []

    queries = [q for q in data.get("search_queries", []) if "keyword" in q and "label" in q]
    if not queries:
        logger.warning(f"{keywords_path} no contiene 'search_queries' válidas.")
    return queries


def cargar_search_queries(
    formato: str = "keyword_label",
    keywords_path: Optional[Path] = None,
) -> list[dict]:
    """
    Devuelve SEARCH_QUERIES en el formato que necesita cada scraper:
      - "location":      {"keyword", "label", "location"}  (LinkedIn)
      - "search":         {"search", "label"}                (Workana)
      - "string":         [label, label, ...]                 (GetOnBoard)
      - "keyword_label" (default): {"keyword", "label"}, deduplicado por
        keyword (el resto de los scrapers — no filtran por ubicación).

    Si data/cvs/keywords.json (o keywords_path) no existe o está vacío,
    retorna [] — el scraper no tendrá queries propias hasta que se le
    inyecten externamente (run_scraping.py --cv) o se genere el JSON.
    """
    path = keywords_path or DEFAULT_KEYWORDS_JSON
    queries = _leer_search_queries(path)
    if not queries:
        return []

    if formato == "location":
        return [
            {"keyword": q["keyword"], "label": q["label"], "location": q.get("location", "Argentina")}
            for q in queries
        ]

    if formato == "search":
        return [{"search": q["keyword"].replace("-", " "), "label": q["label"]} for q in queries]

    if formato == "string":
        # Deduplicar por keyword (no por label): el scraper no filtra por
        # ubicación, así que las variantes "X" / "X (Remoto)" del JSON
        # buscarían lo mismo dos veces.
        vistos, resultado = set(), []
        for q in queries:
            if q["keyword"] not in vistos:
                vistos.add(q["keyword"])
                resultado.append(q["label"])
        return resultado

    # "keyword_label" — deduplicar por keyword para no repetir la misma
    # búsqueda una vez por cada variante de ubicación del JSON.
    vistos, resultado = set(), []
    for q in queries:
        if q["keyword"] not in vistos:
            vistos.add(q["keyword"])
            resultado.append({"keyword": q["keyword"], "label": q["label"]})
    return resultado
