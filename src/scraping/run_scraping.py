#!/usr/bin/env python3
# =============================================================================
# run_scraping.py
# Tesis: "Sistema de Recomendación Laboral - Motor de Matching Inteligente"
# Alumno: Cicconi, Carlos Alberto - Maestría en Explotación de Datos (Austral)
# Módulo: T2 - Diseño y Desarrollo del Web Scraper
#
# DESCRIPCIÓN:
#   Script orquestador que ejecuta todos los scrapers disponibles en secuencia,
#   consolida sus resultados en un único archivo CSV y SQLite, y garantiza:
#     1. No incluir ofertas publicadas hace más de 90 días
#     2. No duplicar ofertas ya presentes en el consolidado
#     3. No incluir ofertas cuyo link ya no esté activo al momento de consolidar
#
# USO:
#   python run_scraping.py                          # Todos, queries por defecto
#   python run_scraping.py --cv data/cvs/kw.json   # Queries + ubicación del CV
#   python run_scraping.py --solo linkedin          # Ejecutar solo uno
#   python run_scraping.py --skip workana           # Saltear uno o más
#   python run_scraping.py --solo-consolidar        # Solo consolidar sin scrapear
#   python run_scraping.py --max-dias 60            # Cambiar ventana temporal
#   python run_scraping.py --no-check-links         # No verificar links (más rápido)
#   python run_scraping.py --purgar-consolidado     # Revalida y limpia TODO lo ya consolidado
#
# INTEGRACIÓN CON cv_keyword_extractor.py:
#   1. Generá el JSON:
#        python src/scraping/cv_keyword_extractor.py data/cvs/MiCV.pdf \
#               --output data/cvs/keywords.json
#   2. Pasalo al orquestador:
#        python run_scraping.py --cv data/cvs/keywords.json
#
# ARCHIVOS GENERADOS:
#   data/raw/consolidado/ofertas_consolidadas.csv   → CSV maestro (acumulativo)
#   data/raw/consolidado/ofertas_consolidadas.db    → SQLite maestro
#   data/raw/consolidado/run_YYYYMMDD_HHMMSS.json  → Reporte de cada ejecución
#   logs/run_scraping_YYYYMMDD_HHMMSS.log          → Log completo
# =============================================================================

import sys
import os
import re
import csv
import json
import sqlite3
import logging
import argparse
import importlib
import traceback
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

# =============================================================================
# CONFIGURACIÓN
# =============================================================================

BASE_DIR        = Path("/media/carlos-a-cicconi/Common1/Repositorios-Ing.Carlos-Cicconi/Trabajo_Final_MCD")
DATA_RAW_DIR    = BASE_DIR / "data" / "raw"
CONSOLIDADO_DIR = DATA_RAW_DIR / "consolidado"
SCRAPERS_DIR    = BASE_DIR / "src" / "scraping"
LOGS_DIR        = BASE_DIR / "logs"

for d in [CONSOLIDADO_DIR, LOGS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

CONSOLIDADO_CSV = CONSOLIDADO_DIR / "ofertas_consolidadas.csv"
CONSOLIDADO_DB  = CONSOLIDADO_DIR / "ofertas_consolidadas.db"

MAX_DIAS_DEFAULT = 90   # Ventana temporal por defecto

# --- Verificación de links activos ---
LINK_CHECK_TIMEOUT = 15    # Segundos de timeout por request de verificación
LINK_CHECK_WORKERS = 8     # Verificaciones en paralelo (hilos)
LINK_CHECK_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept-Language": "es-AR,es;q=0.9,en;q=0.8",
}

# Frases típicas de "oferta ya no disponible" en portales de empleo AR/LatAm.
# Se buscan en el HTML de la página cuando el status HTTP es 200 pero el
# sitio igual informa (client-side o server-rendered) que el aviso venció.
FRASES_OFERTA_INACTIVA = [
    "oferta ya no está disponible",
    "esta oferta no está disponible",
    "esta oferta ya no está disponible",
    "ya no se encuentra disponible",
    "el aviso ya no está disponible",
    "oferta no encontrada",
    "aviso no encontrado",
    "búsqueda ya no está disponible",
    "esta búsqueda ha finalizado",
    "ha finalizado el plazo",
    "esta vacante ya no está disponible",
    "vacante no disponible",
    "este empleo expiró",
    "job is no longer available",
    "this job is no longer accepting applications",
    "posting has expired",
    "página no encontrada",
    "page not found",
]

# Columnas canónicas del consolidado — superconjunto de todos los scrapers
COLUMNAS = [
    "id_consolidado",   # Hash único: fuente + job_id (clave del consolidado)
    "job_id",           # ID original de la fuente
    "fuente",           # Nombre del scraper/plataforma
    "titulo",
    "empresa",
    "ubicacion",
    "descripcion",
    "fecha_pub",
    "fecha_pub_dt",     # fecha_pub normalizada a datetime ISO (para filtrado)
    "url",
    "keyword",
    "nivel",
    "tipo_empleo",
    "modalidad",
    "salario",
    "fecha_scrap",
    "fecha_consolidado",# Timestamp de cuando se agregó al consolidado
]

# Registro de todos los scrapers disponibles
# Formato: (módulo, clase, nombre_fuente, db_filename)
SCRAPERS_REGISTRO = [
    ("linkedin_scraper",     "LinkedInScraper",     "linkedin",     "db_linkedin.db"),
    ("computrabajo_scraper", "ComputrabajoScraper", "computrabajo", "db_computrabajo.db"),
    ("getonboard_scraper",   "GetOnBoardScraper",   "getonboard",   "db_getonboard.db"),
    ("bumeran_scraper",      "BumeranScraper",      "bumeran",      "db_bumeran.db"),
    ("zonajobs_scraper",     "ZonaJobsScraper",     "zonajobs",     "db_zonajobs.db"),
    # workana_scraper: DESACTIVADO — el parámetro "search" del listado
    # (/jobs?category=it-programming&search={keyword}) se ignora
    # server-side: probado con "python" vs "wordpress" (ambos dentro de la
    # misma categoría IT que usa el scraper) y devolvió los mismos 7
    # proyectos exactos, en el mismo orden, en ambos casos. El filtro de
    # categoría sí funciona, pero el de keyword no. El filtrado real por
    # keyword ocurre client-side contra un endpoint no identificado. No
    # reactivar en el orquestador hasta encontrar y adaptar el scraper a
    # ese endpoint real.
    # ("workana_scraper",    "WorkanaScraper",      "workana",      "db_workana.db"),
    ("opcionempleo_scraper", "OpcionempleoScraper", "opcionempleo", "db_opcionempleo.db"),
    # jobomas_scraper: DESACTIVADO — el modo "slug" (/{keyword}) que intenta
    # primero da 404 incluso con keywords conocidas ("python"), por lo que
    # en la práctica siempre cae al modo fallback por query params
    # (/?q={keyword}&l=argentina). Se probó ese fallback con dos keywords
    # muy distintas ("data-scientist" vs "gastronomia") y la respuesta fue
    # idéntica byte a byte (mismo tamaño, mismo listado genérico) — el
    # parámetro "q" se ignora server-side. El filtrado real ocurre
    # client-side contra un endpoint no identificado. No reactivar en el
    # orquestador hasta encontrar y adaptar el scraper a ese endpoint real.
    # ("jobomas_scraper",    "JobomasScraper",      "jobomas",      "db_jobomas.db"),
    ("jobrapido_scraper",    "JobrapidoScraper",    "jobrapido",    "db_jobrapido.db"),
    # jobleads_scraper: DESACTIVADO — el endpoint SSR que usa
    # (/ar/jobs?q={keyword}&country=AR) ignora el parámetro "q": se probó
    # con keywords muy distintas ("data-scientist" vs "gastronomia") y
    # devolvió el mismo listado genérico, en el mismo orden, sin relación
    # con ninguna de las dos búsquedas. El filtrado real ocurre client-side
    # contra un endpoint no identificado. Reactivar solo si se encuentra y
    # adapta el scraper a ese endpoint real.
    # ("jobleads_scraper",   "JobleadsScraper",     "jobleads",     "db_jobleads.db"),
    # buscojobs_scraper: DESACTIVADO — el endpoint SSR que usa
    # (/ofertas/rc744/trabajo-en-{keyword}) ignora el keyword tanto en el
    # path como en el query param "?que=", y siempre devuelve el mismo
    # listado genérico del sitio (confirmado con 2 keywords muy distintas:
    # mismos IDs, mismo orden). El filtrado real ocurre client-side contra
    # un endpoint no identificado. Reactivar solo si se encuentra y adapta
    # el scraper a ese endpoint real.
    # ("buscojobs_scraper",  "BuscojobsScraper",    "buscojobs",    "db_buscojobs.db"),
    ("freelancer_ar_scraper","FreelancerArScraper", "freelancer_ar","db_freelancer_ar.db"),
    ("randstad_scraper",     "RandstadScraper",     "randstad",     "db_randstad.db"),
    ("adecco_scraper",       "AdeccoScraper",       "adecco",       "db_adecco.db"),
]


# =============================================================================
# LOGGING
# =============================================================================

LOG_FILE = LOGS_DIR / f"run_scraping_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


# =============================================================================
# CARGA DE SEARCH_QUERIES DESDE JSON DEL CV
# =============================================================================

def cargar_queries_desde_cv(json_path: Path) -> Optional[list[dict]]:
    """
    Carga las SEARCH_QUERIES desde el JSON generado por cv_keyword_extractor.py.

    El JSON tiene la estructura:
      {
        "cv_path": "...",
        "ubicacion": {"ciudad": "Rosario", "provincia": "Santa Fe",
                      "pais": "Argentina", "linkedin_location": "Rosario, Santa Fe, Argentina"},
        "keywords": ["Data Scientist", ...],
        "search_queries": [
          {"keyword": "data-scientist", "label": "Data Scientist",
           "location": "Rosario, Santa Fe, Argentina"},
          {"keyword": "data-scientist", "label": "Data Scientist (Remoto AR)",
           "location": "Argentina"},
          {"keyword": "data-scientist", "label": "Data Scientist (Remoto)",
           "location": "Worldwide"},
          ...
        ]
      }

    Retorna la lista de search_queries o None si hay algún error.
    """
    if not json_path.exists():
        logger.error(f"Archivo de keywords no encontrado: {json_path}")
        logger.error(f"  Generalo con: python src/scraping/cv_keyword_extractor.py "
                     f"<CV.pdf> --output {json_path}")
        return None

    try:
        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)

        queries = data.get("search_queries", [])
        if not queries:
            logger.error(f"El archivo {json_path} no contiene 'search_queries'.")
            return None

        queries_validas = [
            q for q in queries
            if "keyword" in q and "label" in q
        ]
        if not queries_validas:
            logger.error("No se encontraron queries válidas en el JSON.")
            return None

        ubicacion = data.get("ubicacion", {})
        logger.info(f"✅ Keywords cargadas desde CV: {len(queries_validas)} búsquedas")
        if ubicacion:
            logger.info(f"   Ubicación del candidato: {ubicacion.get('linkedin_location','?')}")
        return queries_validas

    except json.JSONDecodeError as e:
        logger.error(f"Error leyendo JSON de keywords: {e}")
        return None
    except Exception as e:
        logger.error(f"Error inesperado cargando keywords: {e}")
        return None


def inyectar_queries_en_scraper(scraper_instance, queries: list[dict], modulo) -> bool:
    """
    Inyecta las SEARCH_QUERIES del CV en el scraper, adaptando el formato
    según las particularidades de cada plataforma:

    - LinkedIn:  usa {"keyword", "label", "location"} — se pasa location completo.
    - Workana:   usa {"search", "label"}              — "keyword" → "search", sin location.
    - El resto:  usa {"keyword", "label"}              — se omite location.

    Las búsquedas de ubicación exacta + remoto ya vienen incluidas en el JSON
    (generadas por cv_keyword_extractor.py), por lo que LinkedIn recibirá
    las tres variantes (ciudad, país, worldwide) para cada keyword.
    """
    try:
        modulo_queries_orig = getattr(modulo, "SEARCH_QUERIES", [{}])
        usa_location = bool(modulo_queries_orig and "location" in modulo_queries_orig[0])
        usa_search   = bool(modulo_queries_orig and "search"   in modulo_queries_orig[0])

        if usa_location:
            # LinkedIn: preservar keyword + label + location
            queries_finales = [
                {"keyword":  q["keyword"],
                 "label":    q["label"],
                 "location": q.get("location", "Argentina")}
                for q in queries
            ]
        elif usa_search:
            # Workana: keyword → search, sin location
            queries_finales = [
                {"search": q["keyword"].replace("-", " "), "label": q["label"]}
                for q in queries
            ]
        else:
            # Resto de scrapers: solo keyword + label, sin location
            # Deduplicar por keyword para no repetir la misma búsqueda 3 veces
            vistos, queries_finales = set(), []
            for q in queries:
                if q["keyword"] not in vistos:
                    vistos.add(q["keyword"])
                    queries_finales.append({"keyword": q["keyword"], "label": q["label"]})

        modulo.SEARCH_QUERIES = queries_finales
        if hasattr(scraper_instance, "SEARCH_QUERIES"):
            scraper_instance.SEARCH_QUERIES = queries_finales

        logger.debug(f"  Queries inyectadas: {len(queries_finales)} "
                     f"(location={'sí' if usa_location else 'no'}, "
                     f"search={'sí' if usa_search else 'no'})")
        return True

    except Exception as e:
        logger.warning(f"  No se pudieron inyectar las queries: {e} — usando las del módulo")
        return False


# =============================================================================
# UTILIDADES DE FECHA
# =============================================================================

# Patrones de fecha que manejan las distintas fuentes
FECHA_PATRONES = [
    # ISO / timestamps exactos
    r"(\d{4}-\d{2}-\d{2})T",            # 2025-03-15T10:00:00
    r"^(\d{4}-\d{2}-\d{2})$",           # 2025-03-15
    r"^(\d{4}/\d{2}/\d{2})$",           # 2025/03/15
    r"(\d{2}/\d{2}/\d{4})",             # 15/03/2025
    r"(\d{2}-\d{2}-\d{4})",             # 15-03-2025
]

# Patrones relativos (ej: "Hace 2 días", "Publicado: hace 7 horas")
RELATIVOS = [
    (r"hace?\s+(\d+)\s+hora",     "hours"),
    (r"hace?\s+(\d+)\s+día",      "days"),
    (r"hace?\s+(\d+)\s+semana",   "weeks"),
    (r"hace?\s+(\d+)\s+mes",      "months"),
    (r"(\d+)\s+hour",             "hours"),
    (r"(\d+)\s+day",              "days"),
    (r"(\d+)\s+week",             "weeks"),
    (r"(\d+)\s+month",            "months"),
    (r"(\d+)\s+hora",             "hours"),
    (r"(\d+)\s+día",              "days"),
    (r"(\d+)\s+semana",           "weeks"),
    (r"(\d+)\s+mes",              "months"),
    (r"hoy",                      "today"),
    (r"ayer",                     "yesterday"),
    (r"today",                    "today"),
    (r"yesterday",                "yesterday"),
    # Ranges: "hace más de 15 días" → conservar (usar 15 días)
    (r"más de\s+(\d+)\s+día",     "days"),
    (r"more than\s+(\d+)\s+day",  "days"),
]

def parsear_fecha(fecha_str: str, fecha_scrap: str = None) -> Optional[datetime]:
    """
    Intenta convertir cualquier representación de fecha a datetime.
    Retorna None si no puede interpretar la fecha.

    Estrategias en orden:
    1. Patrones ISO exactos
    2. Patrones relativos ("hace N días")
    3. Timestamp Unix (int)
    4. N/A y otros → None
    """
    if not fecha_str or str(fecha_str).strip().upper() in ("N/A", "NONE", "", "NULL"):
        return None

    fecha_str = str(fecha_str).strip()

    # Referencia temporal: fecha_scrap o ahora
    ahora = datetime.now()
    if fecha_scrap:
        try:
            ahora = datetime.fromisoformat(str(fecha_scrap)[:19])
        except Exception:
            pass

    # 1. Patrones ISO exactos
    for patron in FECHA_PATRONES:
        m = re.search(patron, fecha_str)
        if m:
            fecha_clean = m.group(1).replace("/", "-")
            # Normalizar dd-mm-yyyy → yyyy-mm-dd
            parts = fecha_clean.split("-")
            if len(parts) == 3 and len(parts[0]) == 2:
                fecha_clean = f"{parts[2]}-{parts[1]}-{parts[0]}"
            try:
                return datetime.strptime(fecha_clean, "%Y-%m-%d")
            except ValueError:
                continue

    # 2. Patrones relativos
    fecha_lower = fecha_str.lower()
    for patron, unidad in RELATIVOS:
        m = re.search(patron, fecha_lower)
        if m:
            if unidad == "today":
                return ahora.replace(hour=0, minute=0, second=0)
            elif unidad == "yesterday":
                return (ahora - timedelta(days=1)).replace(hour=0, minute=0, second=0)
            n = int(m.group(1)) if m.lastindex else 1
            if unidad == "hours":
                return ahora - timedelta(hours=n)
            elif unidad == "days":
                return ahora - timedelta(days=n)
            elif unidad == "weeks":
                return ahora - timedelta(weeks=n)
            elif unidad == "months":
                return ahora - timedelta(days=n * 30)

    # 3. Timestamp Unix
    try:
        ts = int(float(fecha_str))
        if 1_000_000_000 < ts < 2_000_000_000:  # rango razonable
            return datetime.fromtimestamp(ts)
    except (ValueError, TypeError):
        pass

    # 4. Texto libre con año — intentar extraer solo el año
    m_anio = re.search(r"\b(202\d|201\d)\b", fecha_str)
    if m_anio:
        try:
            return datetime(int(m_anio.group(1)), 1, 1)
        except Exception:
            pass

    return None

def es_vigente(fecha_str: str, fecha_scrap: str, max_dias: int) -> tuple[bool, str]:
    """
    Determina si una oferta está dentro de la ventana temporal.
    Retorna (vigente: bool, fecha_iso: str)
    """
    dt = parsear_fecha(fecha_str, fecha_scrap)
    if dt is None:
        # Si no podemos parsear la fecha, incluir la oferta pero marcarla
        return True, "sin-fecha"

    fecha_iso  = dt.strftime("%Y-%m-%d")
    limite     = datetime.now() - timedelta(days=max_dias)
    return dt >= limite, fecha_iso


# =============================================================================
# VERIFICACIÓN DE LINKS ACTIVOS
# =============================================================================

def verificar_link_activo(url: str, timeout: int = LINK_CHECK_TIMEOUT) -> bool:
    """
    Verifica si el link de una oferta sigue activo/accesible.

    Es deliberadamente permisivo ante la ambigüedad (timeouts, errores de
    conexión, bloqueos anti-bot con 403/429, errores 5xx transitorios):
    en esos casos se asume que la oferta sigue activa, para no descartar
    ofertas válidas por un falso positivo. Solo se descarta ante evidencia
    clara: HTTP 404/410, o una frase típica de "oferta no disponible" en
    el cuerpo de una respuesta 200.
    """
    if not url or not url.strip().lower().startswith("http"):
        return True  # Sin URL verificable: no se puede evaluar, se conserva

    try:
        resp = requests.get(
            url, headers=LINK_CHECK_HEADERS, timeout=timeout, allow_redirects=True
        )
    except requests.exceptions.RequestException as e:
        logger.debug(f"    Link no verificable ({e.__class__.__name__}), se conserva: {url}")
        return True

    if resp.status_code in (404, 410):
        return False

    if resp.status_code >= 400:
        # No se puede confirmar (bloqueo anti-bot, rate limit, error del
        # servidor) — se conserva la oferta en vez de descartarla a ciegas.
        logger.debug(f"    Link con HTTP {resp.status_code} (no concluyente), se conserva: {url}")
        return True

    cuerpo = resp.text.lower()
    if any(frase in cuerpo for frase in FRASES_OFERTA_INACTIVA):
        return False

    return True


def filtrar_links_activos(candidatas: list[dict], workers: int = LINK_CHECK_WORKERS) -> tuple[list[dict], int]:
    """
    Verifica en paralelo el link de cada oferta candidata.
    Retorna (activas, cantidad_descartada_por_link).
    """
    if not candidatas:
        return [], 0

    activas = []
    descartadas = 0

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futuros = {
            executor.submit(verificar_link_activo, fila["url"]): fila
            for fila in candidatas
        }
        for futuro in as_completed(futuros):
            fila = futuros[futuro]
            try:
                activo = futuro.result()
            except Exception as e:
                logger.debug(f"    Error verificando link, se conserva: {fila.get('url')}: {e}")
                activo = True

            if activo:
                activas.append(fila)
            else:
                descartadas += 1
                logger.info(f"    🔗❌ Link inactivo, descartada: {fila.get('titulo', '')[:60]} → {fila.get('url')}")

    return activas, descartadas


# =============================================================================
# CONSOLIDADO — SQLite maestro
# =============================================================================

def setup_consolidado_db():
    """Crea la tabla consolidada si no existe."""
    with sqlite3.connect(CONSOLIDADO_DB) as conn:
        cols_sql = ",\n    ".join([
            "id_consolidado TEXT PRIMARY KEY",
            "job_id         TEXT",
            "fuente         TEXT",
            "titulo         TEXT",
            "empresa        TEXT",
            "ubicacion      TEXT",
            "descripcion    TEXT",
            "fecha_pub      TEXT",
            "fecha_pub_dt   TEXT",
            "url            TEXT",
            "keyword        TEXT",
            "nivel          TEXT",
            "tipo_empleo    TEXT",
            "modalidad      TEXT",
            "salario        TEXT",
            "fecha_scrap    TEXT",
            "fecha_consolidado TEXT",
        ])
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS ofertas (
                {cols_sql}
            )
        """)
        # Índices para búsqueda y deduplicación rápida
        conn.execute("CREATE INDEX IF NOT EXISTS idx_fuente    ON ofertas(fuente)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_fecha_pub ON ofertas(fecha_pub_dt)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_titulo    ON ofertas(titulo)")
        conn.commit()
    logger.info(f"Consolidado DB lista: {CONSOLIDADO_DB}")


def get_ids_existentes() -> set:
    """Retorna el conjunto de id_consolidado ya presentes en el consolidado."""
    if not CONSOLIDADO_DB.exists():
        return set()
    with sqlite3.connect(CONSOLIDADO_DB) as conn:
        rows = conn.execute("SELECT id_consolidado FROM ofertas").fetchall()
    return {r[0] for r in rows}


def hacer_id_consolidado(fuente: str, job_id: str) -> str:
    """
    Genera el identificador único del consolidado.
    Formato: {fuente}::{job_id}
    Garantiza unicidad inter-fuente (un mismo job_id puede existir
    en LinkedIn y en GetOnBoard con distinto contenido).
    """
    return f"{fuente}::{job_id}"


def insertar_en_consolidado(conn: sqlite3.Connection, row: dict):
    """Inserta una fila en el consolidado (ignorando duplicados por id_consolidado)."""
    conn.execute("""
        INSERT OR IGNORE INTO ofertas (
            id_consolidado, job_id, fuente, titulo, empresa, ubicacion,
            descripcion, fecha_pub, fecha_pub_dt, url, keyword, nivel,
            tipo_empleo, modalidad, salario, fecha_scrap, fecha_consolidado
        ) VALUES (
            :id_consolidado, :job_id, :fuente, :titulo, :empresa, :ubicacion,
            :descripcion, :fecha_pub, :fecha_pub_dt, :url, :keyword, :nivel,
            :tipo_empleo, :modalidad, :salario, :fecha_scrap, :fecha_consolidado
        )
    """, row)


# =============================================================================
# LEER DESDE DB INDIVIDUAL DE CADA SCRAPER
# =============================================================================

def leer_db_fuente(db_path: Path, fuente: str) -> list[dict]:
    """
    Lee todas las ofertas de la DB individual de un scraper.
    Normaliza los campos al esquema canónico del consolidado,
    manejando las diferencias entre scrapers (ej: LinkedIn sin 'fuente').
    """
    if not db_path.exists():
        logger.warning(f"  DB no encontrada: {db_path}")
        return []

    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            # Obtener columnas disponibles
            cols_info = conn.execute("PRAGMA table_info(ofertas)").fetchall()
            cols_disponibles = {r["name"] for r in cols_info}

            rows = conn.execute("SELECT * FROM ofertas").fetchall()

        result = []
        for row in rows:
            r = dict(row)
            # Normalizar 'fuente' — LinkedIn no la tiene
            if "fuente" not in cols_disponibles or not r.get("fuente"):
                r["fuente"] = fuente
            # Normalizar 'salario' — LinkedIn no la tiene
            if "salario" not in cols_disponibles:
                r["salario"] = None
            result.append(r)

        logger.info(f"  Leídas {len(result):,} ofertas de {db_path.name}")
        return result

    except sqlite3.Error as e:
        logger.error(f"  Error leyendo {db_path}: {e}")
        return []


# =============================================================================
# CONSOLIDAR — procesar y filtrar ofertas de todas las fuentes
# =============================================================================

def consolidar(max_dias: int, verificar_links: bool = True,
                link_workers: int = LINK_CHECK_WORKERS) -> dict:
    """
    Proceso principal de consolidación:
    1. Lee todas las DBs individuales
    2. Descarta duplicados respecto al consolidado existente
    3. Filtra por ventana temporal (max_dias)
    4. Descarta ofertas cuyo link ya no está activo (salvo --no-check-links)
    5. Inserta las nuevas en el consolidado (DB + CSV)
    6. Retorna estadísticas de la ejecución
    """
    setup_consolidado_db()
    ids_existentes    = get_ids_existentes()
    fecha_consolidado = datetime.now().isoformat()
    limite_temporal   = datetime.now() - timedelta(days=max_dias)

    stats_total = {
        "fecha_ejecucion":   fecha_consolidado,
        "max_dias":          max_dias,
        "limite_temporal":   limite_temporal.strftime("%Y-%m-%d"),
        "ids_previos":       len(ids_existentes),
        "fuentes":           {},
        "nuevas_total":      0,
        "descartadas_fecha": 0,
        "descartadas_dup":   0,
        "descartadas_link":  0,
        "verificacion_links": verificar_links,
    }

    nuevas_global = []

    with sqlite3.connect(CONSOLIDADO_DB) as conn:
        for modulo, clase, fuente, db_nombre in SCRAPERS_REGISTRO:
            db_path = DATA_RAW_DIR / db_nombre
            logger.info(f"\n  📂 Procesando: {fuente} ({db_path.name})")

            ofertas_fuente = leer_db_fuente(db_path, fuente)
            stats_fuente   = {
                "leidas":             len(ofertas_fuente),
                "descartadas_fecha":  0,
                "descartadas_dup":    0,
                "descartadas_link":   0,
                "nuevas":             0,
            }

            candidatas = []   # Pasaron dup + fecha, faltan pasar el check de link

            for oferta in ofertas_fuente:
                job_id = str(oferta.get("job_id", "")).strip()
                if not job_id:
                    continue

                id_cons = hacer_id_consolidado(fuente, job_id)

                # --- Filtro 1: duplicado en consolidado ---
                if id_cons in ids_existentes:
                    stats_fuente["descartadas_dup"] += 1
                    stats_total["descartadas_dup"]  += 1
                    continue

                # --- Filtro 2: ventana temporal (90 días) ---
                fecha_pub   = str(oferta.get("fecha_pub", "") or "")
                fecha_scrap = str(oferta.get("fecha_scrap", "") or "")
                vigente, fecha_pub_dt = es_vigente(fecha_pub, fecha_scrap, max_dias)

                if not vigente:
                    stats_fuente["descartadas_fecha"] += 1
                    stats_total["descartadas_fecha"]  += 1
                    continue

                # --- Construir fila canónica ---
                fila = {
                    "id_consolidado":   id_cons,
                    "job_id":           job_id,
                    "fuente":           oferta.get("fuente") or fuente,
                    "titulo":           str(oferta.get("titulo", "") or ""),
                    "empresa":          str(oferta.get("empresa", "") or ""),
                    "ubicacion":        str(oferta.get("ubicacion", "") or ""),
                    "descripcion":      str(oferta.get("descripcion", "") or ""),
                    "fecha_pub":        fecha_pub,
                    "fecha_pub_dt":     fecha_pub_dt,
                    "url":              str(oferta.get("url", "") or ""),
                    "keyword":          str(oferta.get("keyword", "") or ""),
                    "nivel":            str(oferta.get("nivel", "") or "") or None,
                    "tipo_empleo":      str(oferta.get("tipo_empleo", "") or "") or None,
                    "modalidad":        str(oferta.get("modalidad", "") or "") or None,
                    "salario":          str(oferta.get("salario", "") or "") or None,
                    "fecha_scrap":      fecha_scrap,
                    "fecha_consolidado": fecha_consolidado,
                }
                candidatas.append(fila)

            # --- Filtro 3: link activo ---
            if verificar_links and candidatas:
                logger.info(f"    🔗 Verificando {len(candidatas):,} links...")
                candidatas, descartadas_link = filtrar_links_activos(candidatas, workers=link_workers)
                stats_fuente["descartadas_link"] += descartadas_link
                stats_total["descartadas_link"]  += descartadas_link

            for fila in candidatas:
                insertar_en_consolidado(conn, fila)
                ids_existentes.add(fila["id_consolidado"])   # Evitar dups intra-ejecución
                nuevas_global.append(fila)
                stats_fuente["nuevas"]    += 1
                stats_total["nuevas_total"] += 1

            conn.commit()
            stats_total["fuentes"][fuente] = stats_fuente
            logger.info(
                f"  ✅ {fuente}: leídas={stats_fuente['leidas']:,} | "
                f"nuevas={stats_fuente['nuevas']:,} | "
                f"dup={stats_fuente['descartadas_dup']:,} | "
                f"old={stats_fuente['descartadas_fecha']:,} | "
                f"link-inactivo={stats_fuente['descartadas_link']:,}"
            )

    # --- Actualizar CSV consolidado ---
    _actualizar_csv(nuevas_global)

    return stats_total, nuevas_global


def _actualizar_csv(nuevas: list[dict]):
    """
    Agrega las filas nuevas al CSV consolidado acumulativo.
    Si el archivo no existe, lo crea con encabezado.
    """
    if not nuevas:
        return

    file_exists = CONSOLIDADO_CSV.exists()
    with open(CONSOLIDADO_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNAS, extrasaction="ignore")
        if not file_exists:
            writer.writeheader()
        for fila in nuevas:
            writer.writerow(fila)

    logger.info(f"  CSV consolidado actualizado: +{len(nuevas):,} filas → {CONSOLIDADO_CSV}")


def _reescribir_csv_completo(filas: list[dict]):
    """
    Reescribe el CSV consolidado completo desde cero (a diferencia de
    _actualizar_csv, que solo agrega). Se usa después de purgar_consolidado(),
    que borra filas de la DB y por lo tanto no puede limitarse a "appendear".
    """
    with open(CONSOLIDADO_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNAS, extrasaction="ignore")
        writer.writeheader()
        for fila in filas:
            writer.writerow(fila)
    logger.info(f"  CSV consolidado reescrito: {len(filas):,} filas → {CONSOLIDADO_CSV}")


# =============================================================================
# PURGAR CONSOLIDADO — revalidar y limpiar lo ya consolidado
# =============================================================================

def purgar_consolidado(max_dias: int, verificar_links: bool = True,
                        link_workers: int = LINK_CHECK_WORKERS) -> dict:
    """
    A diferencia de consolidar() —que solo filtra ofertas NUEVAS antes de
    insertarlas— esta función revalida TODO lo que ya está en el consolidado
    maestro y elimina lo que ya no corresponde:
      1. Ofertas cuya fecha_pub_dt quedó fuera de la ventana de max_dias.
      2. Ofertas cuyo link ya no está activo (salvo verificar_links=False).
    Reescribe tanto ofertas_consolidadas.db como el CSV.
    """
    stats = {
        "fecha_ejecucion":    datetime.now().isoformat(),
        "max_dias":           max_dias,
        "verificacion_links": verificar_links,
        "total_previo":       0,
        "eliminadas_fecha":   0,
        "eliminadas_link":    0,
        "eliminadas_total":   0,
        "conservadas":        0,
    }

    if not CONSOLIDADO_DB.exists():
        logger.warning("  No existe el consolidado todavía — nada que purgar.")
        return stats

    with sqlite3.connect(CONSOLIDADO_DB) as conn:
        conn.row_factory = sqlite3.Row
        filas = [dict(r) for r in conn.execute("SELECT * FROM ofertas").fetchall()]

    stats["total_previo"] = len(filas)
    logger.info(f"  Revisando {len(filas):,} ofertas ya consolidadas...")

    limite_temporal = datetime.now() - timedelta(days=max_dias)
    ids_eliminar    = []
    candidatas      = []

    # --- Filtro 1: antigüedad ---
    for fila in filas:
        fecha_pub_dt = str(fila.get("fecha_pub_dt") or "")
        dt = None
        if fecha_pub_dt and fecha_pub_dt != "sin-fecha":
            try:
                dt = datetime.strptime(fecha_pub_dt, "%Y-%m-%d")
            except ValueError:
                dt = None

        if dt is not None and dt < limite_temporal:
            ids_eliminar.append(fila["id_consolidado"])
            stats["eliminadas_fecha"] += 1
            continue

        candidatas.append(fila)

    # --- Filtro 2: link activo ---
    if verificar_links and candidatas:
        logger.info(f"  🔗 Verificando {len(candidatas):,} links...")
        activas, descartadas_link = filtrar_links_activos(candidatas, workers=link_workers)
        ids_activas = {f["id_consolidado"] for f in activas}
        for fila in candidatas:
            if fila["id_consolidado"] not in ids_activas:
                ids_eliminar.append(fila["id_consolidado"])
        stats["eliminadas_link"] = descartadas_link
        candidatas = activas

    stats["conservadas"]      = len(candidatas)
    stats["eliminadas_total"] = len(ids_eliminar)

    if not ids_eliminar:
        logger.info("  ✅ Nada para purgar — el consolidado ya está al día.")
        return stats

    # --- Borrar de la DB (en lotes, para no exceder el límite de parámetros de SQLite) ---
    LOTE = 500
    with sqlite3.connect(CONSOLIDADO_DB) as conn:
        for i in range(0, len(ids_eliminar), LOTE):
            lote = ids_eliminar[i:i + LOTE]
            placeholders = ",".join("?" for _ in lote)
            conn.execute(f"DELETE FROM ofertas WHERE id_consolidado IN ({placeholders})", lote)
        conn.commit()

    # --- Reescribir el CSV consolidado completo desde la DB ya purgada ---
    with sqlite3.connect(CONSOLIDADO_DB) as conn:
        conn.row_factory = sqlite3.Row
        filas_finales = [dict(r) for r in conn.execute("SELECT * FROM ofertas").fetchall()]
    _reescribir_csv_completo(filas_finales)

    logger.info(
        f"  🗑️  Purgadas {stats['eliminadas_total']:,} ofertas "
        f"(antigüedad={stats['eliminadas_fecha']:,} | link-inactivo={stats['eliminadas_link']:,})"
    )
    logger.info(f"  Consolidado actualizado: {stats['conservadas']:,} ofertas vigentes")

    return stats


# =============================================================================
# EJECUTAR SCRAPERS
# =============================================================================

def ejecutar_scraper(modulo_nombre: str, clase_nombre: str,
                     fuente: str,
                     cv_queries: Optional[list[dict]] = None) -> dict:
    """
    Importa dinámicamente el módulo del scraper y ejecuta su método run().
    Si cv_queries no es None, inyecta esas queries antes de ejecutar.
    """
    resultado = {
        "fuente":         fuente,
        "exito":          False,
        "ofertas":        0,
        "error":          None,
        "duracion_s":     0,
        "queries_origen": "cv" if cv_queries else "modulo",
    }
    t_inicio = datetime.now()

    try:
        logger.info(f"\n{'='*60}")
        logger.info(f"  EJECUTANDO: {fuente} ({clase_nombre})")
        if cv_queries:
            logger.info(f"  Queries: desde CV ({len(cv_queries)} búsquedas)")
        else:
            logger.info(f"  Queries: definidas en el módulo")
        logger.info(f"{'='*60}")

        if str(SCRAPERS_DIR) not in sys.path:
            sys.path.insert(0, str(SCRAPERS_DIR))

        modulo  = importlib.import_module(modulo_nombre)
        clase   = getattr(modulo, clase_nombre)
        scraper = clase()

        if cv_queries:
            ok = inyectar_queries_en_scraper(scraper, cv_queries, modulo)
            if ok:
                logger.info(f"  ✅ Queries del CV inyectadas en {fuente}")
            else:
                logger.warning(f"  ⚠️  Usando queries del módulo para {fuente}")

        total = scraper.run()

        resultado["exito"]   = True
        resultado["ofertas"] = total
        logger.info(f"  ✅ {fuente}: {total:,} ofertas recopiladas")

    except Exception as e:
        resultado["error"] = str(e)
        logger.error(f"  ❌ {fuente} falló: {e}")
        logger.debug(traceback.format_exc())

    resultado["duracion_s"] = (datetime.now() - t_inicio).seconds
    return resultado


# =============================================================================
# MAIN
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Orquestador de scrapers — Tesis MCD Cicconi Carlos Alberto",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  # Queries por defecto (hardcodeadas en cada scraper)
  python run_scraping.py

  # Keywords + ubicación extraídas del CV
  python run_scraping.py --cv data/cvs/keywords.json

  # Solo LinkedIn, con keywords del CV
  python run_scraping.py --cv data/cvs/keywords.json --solo linkedin

  # Generar el JSON primero:
  python src/scraping/cv_keyword_extractor.py data/cvs/MiCV.pdf \\
         --output data/cvs/keywords.json
        """
    )
    parser.add_argument(
        "--cv", metavar="KEYWORDS_JSON",
        help="JSON generado por cv_keyword_extractor.py. Incluye keywords "
             "y ubicación del candidato. LinkedIn usará búsquedas por ciudad, "
             "país y 'Worldwide' (remoto). El resto de scrapers usará solo las keywords."
    )
    parser.add_argument(
        "--solo", nargs="+", metavar="FUENTE",
        help="Ejecutar solo estas fuentes (ej: --solo linkedin getonboard)"
    )
    parser.add_argument(
        "--skip", nargs="+", metavar="FUENTE",
        help="Saltear estas fuentes (ej: --skip workana)"
    )
    parser.add_argument(
        "--solo-consolidar", action="store_true",
        help="Solo consolidar sin ejecutar scrapers"
    )
    parser.add_argument(
        "--purgar-consolidado", action="store_true",
        help="No scrapear ni consolidar ofertas nuevas: revalida TODO el "
             "consolidado ya existente (antigüedad + link activo, salvo "
             "--no-check-links) y elimina lo que ya no corresponde"
    )
    parser.add_argument(
        "--max-dias", type=int, default=MAX_DIAS_DEFAULT,
        help=f"Ventana temporal en días (default: {MAX_DIAS_DEFAULT})"
    )
    parser.add_argument(
        "--no-check-links", action="store_true",
        help="No verificar si el link de cada oferta sigue activo al consolidar "
             "(la verificación está activada por default; desactivarla acelera la corrida)"
    )
    parser.add_argument(
        "--link-workers", type=int, default=LINK_CHECK_WORKERS,
        help=f"Verificaciones de link en paralelo (default: {LINK_CHECK_WORKERS})"
    )
    args = parser.parse_args()

    t_inicio_total = datetime.now()

    # ── Cargar queries del CV ─────────────────────────────────────────────────
    cv_queries = None
    if args.cv:
        cv_queries = cargar_queries_desde_cv(Path(args.cv))
        if cv_queries is None:
            logger.error("No se pudieron cargar las queries del CV. Abortando.")
            return 1

    logger.info("\n" + "=" * 65)
    logger.info("  ORQUESTADOR DE SCRAPING — Tesis MCD")
    logger.info("  Alumno: Cicconi, Carlos Alberto")
    logger.info("  Universidad Austral — Maestría en Explotación de Datos")
    logger.info("=" * 65)
    logger.info(f"  Fecha/hora inicio : {t_inicio_total.strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"  Ventana temporal  : {args.max_dias} días")
    logger.info(f"  Verificar links   : {'No' if args.no_check_links else f'Sí ({args.link_workers} hilos)'}")
    logger.info(f"  Scrapers activos  : {len(SCRAPERS_REGISTRO)}")
    logger.info(f"  Solo consolidar   : {args.solo_consolidar}")
    logger.info(f"  Origen de queries : {'CV → ' + args.cv if cv_queries else 'módulos (por defecto)'}")
    if args.solo:
        logger.info(f"  Filtro --solo     : {args.solo}")
    if args.skip:
        logger.info(f"  Filtro --skip     : {args.skip}")

    # -----------------------------------------------------------------------
    # MODO PURGA: revalida el consolidado existente y sale (no scrapea)
    # -----------------------------------------------------------------------
    if args.purgar_consolidado:
        logger.info("\n" + "=" * 65)
        logger.info("  MODO PURGA — revalidando el consolidado existente")
        logger.info("=" * 65)

        stats_purga = purgar_consolidado(
            args.max_dias,
            verificar_links=not args.no_check_links,
            link_workers=args.link_workers,
        )

        duracion_total = (datetime.now() - t_inicio_total).seconds
        logger.info("\n" + "=" * 65)
        logger.info("  RESUMEN DE LA PURGA")
        logger.info("=" * 65)
        logger.info(f"  Ofertas revisadas           : {stats_purga['total_previo']:,}")
        logger.info(f"  Eliminadas (antigüedad)     : {stats_purga['eliminadas_fecha']:,}")
        logger.info(f"  Eliminadas (link inactivo)  : {stats_purga['eliminadas_link']:,}"
                    + ("" if stats_purga["verificacion_links"] else " (verificación desactivada)"))
        logger.info(f"  Total eliminadas            : {stats_purga['eliminadas_total']:,}")
        logger.info(f"  Ofertas conservadas         : {stats_purga['conservadas']:,}")
        logger.info(f"  Duración total              : {duracion_total // 60}m {duracion_total % 60}s")

        reporte_path = CONSOLIDADO_DIR / f"purga_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(reporte_path, "w", encoding="utf-8") as f:
            json.dump({**stats_purga, "duracion_segundos": duracion_total}, f, ensure_ascii=False, indent=2)
        logger.info(f"  Reporte: {reporte_path}")

        return 0

    resultados_scrapers = []

    # -----------------------------------------------------------------------
    # FASE 1: EJECUTAR SCRAPERS
    # -----------------------------------------------------------------------
    if not args.solo_consolidar:
        scrapers_a_ejecutar = []
        for entrada in SCRAPERS_REGISTRO:
            modulo, clase, fuente, _ = entrada
            if args.solo and fuente not in args.solo:
                continue
            if args.skip and fuente in args.skip:
                logger.info(f"  ⏭️  Salteando: {fuente}")
                continue
            scrapers_a_ejecutar.append(entrada)

        logger.info(f"\n  Scrapers a ejecutar: {[s[2] for s in scrapers_a_ejecutar]}")

        for modulo, clase, fuente, _ in scrapers_a_ejecutar:
            resultado = ejecutar_scraper(modulo, clase, fuente, cv_queries=cv_queries)
            resultados_scrapers.append(resultado)

    # -----------------------------------------------------------------------
    # FASE 2: CONSOLIDAR
    # -----------------------------------------------------------------------
    logger.info("\n" + "=" * 65)
    logger.info("  FASE 2: CONSOLIDACIÓN")
    logger.info("=" * 65)

    stats, nuevas = consolidar(
        args.max_dias,
        verificar_links=not args.no_check_links,
        link_workers=args.link_workers,
    )

    # -----------------------------------------------------------------------
    # REPORTE FINAL
    # -----------------------------------------------------------------------
    duracion_total = (datetime.now() - t_inicio_total).seconds

    logger.info("\n" + "=" * 65)
    logger.info("  RESUMEN FINAL")
    logger.info("=" * 65)

    # Totales del consolidado
    total_en_db = 0
    if CONSOLIDADO_DB.exists():
        with sqlite3.connect(CONSOLIDADO_DB) as conn:
            total_en_db = conn.execute("SELECT COUNT(*) FROM ofertas").fetchone()[0]

    logger.info(f"  Ofertas nuevas incorporadas : {stats['nuevas_total']:,}")
    logger.info(f"  Descartadas (antigüedad)    : {stats['descartadas_fecha']:,}")
    logger.info(f"  Descartadas (duplicado)     : {stats['descartadas_dup']:,}")
    logger.info(f"  Descartadas (link inactivo) : {stats['descartadas_link']:,}"
                + ("" if stats["verificacion_links"] else " (verificación desactivada)"))
    logger.info(f"  Total acumulado en DB       : {total_en_db:,}")
    logger.info(f"  Ventana temporal aplicada   : últimos {args.max_dias} días (desde {stats['limite_temporal']})")
    logger.info(f"  Duración total              : {duracion_total // 60}m {duracion_total % 60}s")
    logger.info(f"\n  Archivos de salida:")
    logger.info(f"    DB : {CONSOLIDADO_DB}")
    logger.info(f"    CSV: {CONSOLIDADO_CSV}")
    logger.info(f"    Log: {LOG_FILE}")

    if resultados_scrapers:
        logger.info(f"\n  Resultado por scraper:")
        for r in resultados_scrapers:
            estado = "✅" if r["exito"] else "❌"
            origen = "📄CV" if r.get("queries_origen") == "cv" else "🔧mod"
            logger.info(f"    {estado} {r['fuente']:15} | {origen} | "
                        f"ofertas={r['ofertas']:,} | "
                        f"tiempo={r['duracion_s']}s"
                        + (f" | error={r['error'][:60]}" if r["error"] else ""))

    logger.info(f"\n  Por fuente (consolidación):")
    for fuente, s in stats["fuentes"].items():
        logger.info(f"    {fuente:15}: nuevas={s['nuevas']:,} | "
                    f"dup={s['descartadas_dup']:,} | "
                    f"old={s['descartadas_fecha']:,} | "
                    f"link-inactivo={s['descartadas_link']:,}")

    # Guardar reporte JSON
    reporte = {
        "fecha_ejecucion":    stats["fecha_ejecucion"],
        "max_dias":           args.max_dias,
        "verificacion_links": stats["verificacion_links"],
        "limite_temporal":    stats["limite_temporal"],
        "duracion_segundos":  duracion_total,
        "total_acumulado_db": total_en_db,
        "nuevas_incorporadas":stats["nuevas_total"],
        "descartadas_fecha":  stats["descartadas_fecha"],
        "descartadas_dup":    stats["descartadas_dup"],
        "descartadas_link":   stats["descartadas_link"],
        "scrapers_ejecutados":resultados_scrapers,
        "consolidacion_fuentes": stats["fuentes"],
    }
    reporte_path = CONSOLIDADO_DIR / f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    reporte_path.write_text(
        json.dumps(reporte, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    logger.info(f"    Reporte: {reporte_path}")
    logger.info("=" * 65)

    return 0 if stats["nuevas_total"] >= 0 else 1


if __name__ == "__main__":
    sys.exit(main())
