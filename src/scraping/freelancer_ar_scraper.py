#!/usr/bin/env python3
# =============================================================================
# freelancer_ar_scraper.py
# Tesis: "Sistema de Recomendación Laboral - Motor de Matching Inteligente"
# Alumno: Cicconi, Carlos Alberto - Maestría en Explotación de Datos (Austral)
# Módulo: T2 - Diseño y Desarrollo del Web Scraper
# Descripción: Extrae ofertas laborales de freelancer.ar y las almacena en
#              formato CSV y SQLite para su posterior procesamiento.
#
# URL base: https://freelancer.ar
# Estrategia: el sitio es un WordPress con el plugin WP Job Manager, que
#             expone la API REST estándar de WordPress en /wp-json/wp/v2/
#             job-listings. Se consume esa API en vez de parsear HTML.
#             No requiere autenticación. Uso exclusivamente académico.
#
# Nota de acceso: el WAF del sitio bloquea con HTTP 403 los requests con
# User-Agent de navegador (Chrome/Firefox) sobre la home, pero el endpoint
# /wp-json/ responde con normalidad. Este scraper NO envía un User-Agent
# de navegador simulado: usa el default de requests (más liviano y menos
# propenso a ese bloqueo) y baja frecuencia de requests.
#
# Nota sobre alcance geográfico: el sitio agrega ofertas de varios países
# (se observaron avisos de Reino Unido junto con avisos argentinos) y no
# expone una taxonomía de país/región en la API. Por eso, tras traer los
# resultados de cada búsqueda, se filtran client-side quedándose solo con
# los que mencionan Argentina o alguna de sus provincias/ciudades en la
# ubicación, el título o la empresa.
# =============================================================================

import requests
import time
import random
import logging
import sqlite3
import csv
import json
import re
from datetime import datetime
from dataclasses import dataclass, asdict, field
from typing import Optional
from pathlib import Path
from html import unescape

# =============================================================================
# CONFIGURACIÓN DEL PROYECTO
# =============================================================================

BASE_DIR     = Path("/media/carlos-a-cicconi/Common1/Repositorios-Ing.Carlos-Cicconi/Trabajo_Final_MCD")
DATA_RAW_DIR = BASE_DIR / "data" / "raw"
LOGS_DIR     = BASE_DIR / "logs"

DATA_RAW_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)

# --- Logging ---
LOG_FILE = LOGS_DIR / f"freelancer_ar_scraper_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


# =============================================================================
# CONFIGURACIÓN DEL SCRAPER
# =============================================================================

# Búsquedas objetivo — mismo criterio que el resto de los scrapers
SEARCH_QUERIES = [
    {"keyword": "data scientist",           "label": "Data Scientist"},
    {"keyword": "data analyst",             "label": "Data Analyst"},
    {"keyword": "machine learning",         "label": "Machine Learning Engineer"},
    {"keyword": "nlp",                      "label": "NLP Engineer"},
    {"keyword": "data engineer",            "label": "Data Engineer"},
    {"keyword": "software engineer",        "label": "Software Engineer"},
    {"keyword": "python",                   "label": "Python Developer"},
    {"keyword": "desarrollador backend",    "label": "Desarrollador Backend"},
    {"keyword": "desarrollador full stack", "label": "Desarrollador Full Stack"},
    {"keyword": "ingeniero de datos",       "label": "Ingeniero de Datos"},
]

# Parámetros de scraping
PAGES_PER_QUERY = 5       # páginas de la API por búsqueda
PER_PAGE        = 20      # ofertas por página (tamaño de página de la API)
DELAY_MIN       = 2.0     # Segundos mínimos entre requests (ético)
DELAY_MAX       = 4.0     # Segundos máximos entre requests
MAX_RETRIES     = 3       # Reintentos ante fallo de conexión
DB_FILENAME     = DATA_RAW_DIR / "db_freelancer_ar.db"
CSV_FILENAME    = DATA_RAW_DIR / f"ofertas_freelancer_ar_{datetime.now().strftime('%Y%m%d')}.csv"

# URL base y endpoint REST de freelancer.ar
BASE_URL     = "https://freelancer.ar"
API_ENDPOINT = f"{BASE_URL}/wp-json/wp/v2/job-listings"

# --- Diccionario acotado para filtrar ofertas argentinas ---
# (El sitio agrega ofertas de otros países; no hay taxonomía de región en
# la API, así que se filtra por texto sobre ubicación/título/empresa.)
PROVINCIAS_AR = [
    "buenos aires", "bsas", "bs as", "gba", "conurbano", "caba",
    "capital federal", "ciudad autónoma", "ciudad autonoma",
    "catamarca", "chaco", "chubut", "córdoba", "cordoba", "corrientes",
    "entre ríos", "entre rios", "formosa", "jujuy", "la pampa", "la rioja",
    "mendoza", "misiones", "neuquén", "neuquen", "río negro", "rio negro",
    "salta", "san juan", "san luis", "santa cruz", "santa fe",
    "santiago del estero", "tierra del fuego", "tucumán", "tucuman",
]
CIUDADES_AR = [
    "rosario", "la plata", "mar del plata", "resistencia", "posadas",
    "bahía blanca", "bahia blanca", "san miguel de tucumán",
    "san salvador de jujuy", "paraná", "parana", "rawson",
    "río gallegos", "rio gallegos", "ushuaia", "viedma", "santa rosa",
    "san nicolás", "san nicolas", "quilmes", "lanús", "lanus",
    "avellaneda", "lomas de zamora", "berazategui", "florencio varela",
    "morón", "moron", "tigre", "san isidro", "vicente lópez",
    "vicente lópez", "puerto madryn",
]
TERMINOS_ARGENTINA = ["argentina", ".ar", "arg."] + PROVINCIAS_AR + CIUDADES_AR

TAG_RE = re.compile(r"<[^>]+>")


# =============================================================================
# DATACLASS: Estructura de una oferta laboral
# (Idéntica al resto de los scrapers para facilitar la unificación del corpus)
# =============================================================================

@dataclass
class OfertaLaboral:
    job_id:      str
    titulo:      str
    empresa:     str
    ubicacion:   str
    descripcion: str
    fecha_pub:   str
    url:         str
    keyword:     str
    fuente:      str = "freelancer_ar"
    fecha_scrap: str = field(default_factory=lambda: datetime.now().isoformat())
    nivel:       Optional[str] = None
    tipo_empleo: Optional[str] = None
    modalidad:   Optional[str] = None
    salario:     Optional[str] = None


# =============================================================================
# CLASE PRINCIPAL: FreelancerArScraper
# =============================================================================

class FreelancerArScraper:
    """
    Scraper para freelancer.ar (ex trabajando.com.ar), portal de empleo
    construido sobre WordPress + WP Job Manager.
    Opera sobre la API REST pública, sin autenticación.
    Uso: exclusivamente académico (Tesis MCD - Universidad Austral).

    Estructura de la API:
      - Listado/búsqueda: GET /wp-json/wp/v2/job-listings
                           ?search={keyword}&page={n}&per_page={per_page}
        Paginación informada en headers X-WP-Total / X-WP-TotalPages.
      - Cada item ya trae todo el detalle (no hace falta un segundo request):
        title.rendered, content.rendered, link, date,
        meta._company_name, meta._job_location, meta._remote_position,
        meta._job_salary(+_currency/_unit), class_list (tipo de contrato).
    """

    def __init__(self):
        self.session  = requests.Session()
        self.seen_ids = set()
        self._setup_db()
        logger.info("FreelancerArScraper inicializado correctamente")

    # -----------------------------------------------------------------------
    # HEADERS Y REQUESTS
    # -----------------------------------------------------------------------

    def _get_headers(self) -> dict:
        """
        Headers mínimos: el WAF del sitio bloquea explícitamente los
        User-Agent de navegador de escritorio en ciertas rutas. Se usa el
        User-Agent por defecto de requests, que no dispara ese bloqueo.
        """
        return {
            "Accept":          "application/json",
            "Accept-Language": "es-AR,es;q=0.9,en;q=0.8",
        }

    def _request(self, url: str, params: Optional[dict] = None) -> Optional[requests.Response]:
        """Realiza un GET con reintentos y delay ético entre requests."""
        for intento in range(1, MAX_RETRIES + 1):
            try:
                time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))
                response = self.session.get(
                    url,
                    params=params,
                    headers=self._get_headers(),
                    timeout=20
                )
                if response.status_code == 200:
                    return response
                elif response.status_code == 400:
                    # La API devuelve 400 al pedir una página fuera de rango
                    logger.info(f"HTTP 400 en {url} (sin más páginas)")
                    return None
                elif response.status_code == 429:
                    wait = 90 * intento
                    logger.warning(f"Rate limit (429). Esperando {wait}s...")
                    time.sleep(wait)
                elif response.status_code == 403:
                    logger.warning(f"HTTP 403 en {url}. Reintentando con backoff...")
                    time.sleep(20 * intento)
                else:
                    logger.warning(f"HTTP {response.status_code} en: {url}")
            except requests.exceptions.RequestException as e:
                logger.error(f"Error de conexión (intento {intento}/{MAX_RETRIES}): {e}")
                time.sleep(10 * intento)

        logger.error(f"Fallaron todos los intentos para: {url}")
        return None

    # -----------------------------------------------------------------------
    # BASE DE DATOS SQLite
    # -----------------------------------------------------------------------

    def _setup_db(self):
        """Crea la tabla de ofertas si no existe."""
        with sqlite3.connect(DB_FILENAME) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ofertas (
                    job_id      TEXT PRIMARY KEY,
                    titulo      TEXT,
                    empresa     TEXT,
                    ubicacion   TEXT,
                    descripcion TEXT,
                    fecha_pub   TEXT,
                    url         TEXT,
                    keyword     TEXT,
                    fuente      TEXT,
                    fecha_scrap TEXT,
                    nivel       TEXT,
                    tipo_empleo TEXT,
                    modalidad   TEXT,
                    salario     TEXT
                )
            """)
            conn.commit()
        logger.info(f"Base de datos lista: {DB_FILENAME}")

    def _save_to_db(self, oferta: OfertaLaboral):
        """Guarda una oferta en SQLite (ignora duplicados por job_id)."""
        with sqlite3.connect(DB_FILENAME) as conn:
            try:
                conn.execute("""
                    INSERT OR IGNORE INTO ofertas VALUES
                    (:job_id, :titulo, :empresa, :ubicacion, :descripcion,
                     :fecha_pub, :url, :keyword, :fuente, :fecha_scrap,
                     :nivel, :tipo_empleo, :modalidad, :salario)
                """, asdict(oferta))
                conn.commit()
            except sqlite3.Error as e:
                logger.error(f"Error guardando en DB: {e}")

    # -----------------------------------------------------------------------
    # FILTRO GEOGRÁFICO (el sitio agrega ofertas de otros países)
    # -----------------------------------------------------------------------

    def _es_oferta_argentina(self, item: dict) -> bool:
        """
        El sitio no expone taxonomía de país. Se considera argentina toda
        oferta que:
        - mencione Argentina/una provincia/ciudad argentina en la ubicación,
          el título o el nombre de la empresa, o
        - esté marcada como remota sin ubicación específica (se incluye,
          igual que hacen el resto de los scrapers con avisos "Worldwide").
        """
        meta      = item.get("meta", {}) or {}
        ubicacion = (meta.get("_job_location") or "").lower()
        titulo    = TAG_RE.sub("", item.get("title", {}).get("rendered", "")).lower()
        empresa   = (meta.get("_company_name") or "").lower()
        remoto    = bool(meta.get("_remote_position"))

        texto = f"{ubicacion} {titulo} {empresa}"
        if any(termino in texto for termino in TERMINOS_ARGENTINA):
            return True

        # Remoto sin ubicación declarada: no se puede descartar con certeza,
        # se incluye (igual criterio que el resto del corpus para "Worldwide")
        if remoto and not ubicacion:
            return True

        return False

    # -----------------------------------------------------------------------
    # PARSING DE LOS RESULTADOS DE LA API
    # -----------------------------------------------------------------------

    def _limpiar_html(self, html: str) -> str:
        """Quita tags HTML y decodifica entidades de content.rendered."""
        if not html:
            return ""
        return unescape(TAG_RE.sub(" ", html)).strip()

    def _tipo_empleo_de(self, class_list: list) -> Optional[str]:
        """Extrae el tipo de contrato desde class_list (ej: job-type-full-time)."""
        for c in class_list or []:
            if c.startswith("job-type-"):
                return c.replace("job-type-", "").replace("-", " ").title()
        return None

    def _salario_de(self, meta: dict) -> Optional[str]:
        monto    = meta.get("_job_salary")
        moneda   = meta.get("_job_salary_currency") or ""
        unidad   = meta.get("_job_salary_unit") or ""
        if not monto:
            return None
        return " ".join(str(p) for p in (moneda, monto, unidad) if p).strip()

    def _parse_item(self, item: dict, keyword_label: str) -> Optional[OfertaLaboral]:
        try:
            job_id = str(item["id"])
            if job_id in self.seen_ids:
                return None
            self.seen_ids.add(job_id)

            meta = item.get("meta", {}) or {}

            return OfertaLaboral(
                job_id      = job_id,
                titulo      = self._limpiar_html(item.get("title", {}).get("rendered", "")) or "N/A",
                empresa     = meta.get("_company_name") or "N/A",
                ubicacion   = meta.get("_job_location") or "Argentina",
                descripcion = self._limpiar_html(item.get("content", {}).get("rendered", "")),
                fecha_pub   = item.get("date") or "N/A",
                url         = item.get("link") or f"{BASE_URL}/?post_type=job_listing&p={job_id}",
                keyword     = keyword_label,
                modalidad   = "Remoto" if meta.get("_remote_position") else None,
                tipo_empleo = self._tipo_empleo_de(item.get("class_list")),
                salario     = self._salario_de(meta),
            )
        except Exception as e:
            logger.debug(f"Error parseando item: {e}")
            return None

    # -----------------------------------------------------------------------
    # EXPORTAR A CSV
    # -----------------------------------------------------------------------

    def _export_to_csv(self, ofertas: list[OfertaLaboral]):
        """Exporta las ofertas al archivo CSV acumulativo."""
        if not ofertas:
            return

        fieldnames  = list(asdict(ofertas[0]).keys())
        file_exists = CSV_FILENAME.exists()

        with open(CSV_FILENAME, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            if not file_exists:
                writer.writeheader()
            for oferta in ofertas:
                writer.writerow(asdict(oferta))

        logger.info(f"Exportadas {len(ofertas)} ofertas a CSV: {CSV_FILENAME}")

    # -----------------------------------------------------------------------
    # MÉTODO PRINCIPAL: run()
    # -----------------------------------------------------------------------

    def run(self):
        """
        Ejecuta el scraping completo sobre todas las búsquedas configuradas.
        Para cada keyword recorre las páginas de la API REST, descarta las
        ofertas que no parecen argentinas y guarda el resto.
        """
        logger.info("=" * 60)
        logger.info("INICIO DEL SCRAPING - freelancer.ar")
        logger.info(f"Búsquedas: {len(SEARCH_QUERIES)} | Páginas por búsqueda: {PAGES_PER_QUERY}")
        logger.info("=" * 60)

        total_guardadas   = 0
        total_descartadas = 0
        stats = []

        for query in SEARCH_QUERIES:
            keyword       = query["keyword"]
            keyword_label = query["label"]
            logger.info(f"\n🔍 Buscando: '{keyword_label}'")

            ofertas_query = []

            for page in range(1, PAGES_PER_QUERY + 1):
                params = {"search": keyword, "page": page, "per_page": PER_PAGE}
                logger.info(f"   Página {page}/{PAGES_PER_QUERY} → search='{keyword}'")

                response = self._request(API_ENDPOINT, params=params)
                if not response:
                    logger.info("   → Sin más resultados para esta búsqueda.")
                    break

                try:
                    items = response.json()
                except json.JSONDecodeError:
                    logger.warning("   ⚠️  Respuesta no es JSON válido.")
                    break

                if not items:
                    logger.info("   → Sin más resultados para esta búsqueda.")
                    break

                nuevas_pagina = 0
                for item in items:
                    if not self._es_oferta_argentina(item):
                        total_descartadas += 1
                        continue

                    oferta = self._parse_item(item, keyword_label)
                    if not oferta:
                        continue

                    self._save_to_db(oferta)
                    ofertas_query.append(oferta)
                    total_guardadas += 1
                    nuevas_pagina += 1

                logger.info(f"   → {nuevas_pagina} ofertas argentinas nuevas de {len(items)} recibidas")

                total_pages = response.headers.get("X-WP-TotalPages")
                if total_pages and page >= int(total_pages):
                    break

            self._export_to_csv(ofertas_query)
            stats.append({"keyword": keyword_label, "ofertas": len(ofertas_query)})
            logger.info(f"✅ '{keyword_label}': {len(ofertas_query)} ofertas guardadas")

        # --- Reporte final ---
        logger.info("\n" + "=" * 60)
        logger.info("SCRAPING FINALIZADO - freelancer.ar")
        logger.info(f"Total de ofertas únicas recopiladas: {total_guardadas}")
        logger.info(f"Descartadas por no ser de Argentina : {total_descartadas}")
        logger.info(f"Base de datos : {DB_FILENAME}")
        logger.info(f"CSV exportado : {CSV_FILENAME}")
        logger.info(f"Log guardado  : {LOG_FILE}")
        logger.info("=" * 60)

        # Guardar estadísticas en JSON
        stats_file = DATA_RAW_DIR / f"stats_freelancer_ar_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(stats_file, "w", encoding="utf-8") as f:
            json.dump({
                "fuente":              "freelancer_ar",
                "fecha":               datetime.now().isoformat(),
                "total_ofertas":       total_guardadas,
                "descartadas_no_ar":   total_descartadas,
                "detalle_por_keyword": stats
            }, f, ensure_ascii=False, indent=2)
        logger.info(f"Estadísticas guardadas: {stats_file}")

        return total_guardadas


# =============================================================================
# EJECUCIÓN
# =============================================================================

if __name__ == "__main__":
    print("\n🚀 Iniciando scraper de freelancer.ar...")
    print("   Proyecto: Tesis MCD - Sistema de Recomendación Laboral")
    print("   Alumno: Cicconi, Carlos Alberto\n")

    scraper = FreelancerArScraper()
    total   = scraper.run()

    print(f"\n✅ Proceso completado. {total} ofertas recopiladas.")
    print(f"   Revisá los datos en: {DATA_RAW_DIR}")
