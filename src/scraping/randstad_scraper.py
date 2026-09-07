#!/usr/bin/env python3
# =============================================================================
# randstad_scraper.py
# Tesis: "Sistema de Recomendación Laboral - Motor de Matching Inteligente"
# Alumno: Cicconi, Carlos Alberto - Maestría en Explotación de Datos (Austral)
# Módulo: T2 - Diseño y Desarrollo del Web Scraper
# Descripción: Extrae ofertas laborales de Randstad Argentina y las almacena
#              en formato CSV y SQLite para su posterior procesamiento.
#
# URL base: https://www.randstad.com.ar
# Estrategia: el buscador de empleos (/trabajos/) es una SPA React que
#             consume una API interna respaldada por Elasticsearch:
#               POST /api/search/search-results
#             Se llama a esa misma API directamente para el listado, y se
#             enriquece cada oferta con la página de detalle, que embebe la
#             descripción completa en JSON-LD (schema.org JobPosting).
#             No requiere autenticación. Uso exclusivamente académico.
# =============================================================================

import requests
import time
import random
import logging
import sqlite3
import csv
import json
import re
import math
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
LOG_FILE = LOGS_DIR / f"randstad_scraper_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
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
PAGES_PER_QUERY  = 5       # páginas de la API por búsqueda
RESULTS_PER_PAGE = 30      # tamaño de página observado de la API
DELAY_MIN        = 2.0     # Segundos mínimos entre requests (ético)
DELAY_MAX        = 4.0     # Segundos máximos entre requests
MAX_RETRIES      = 3       # Reintentos ante fallo de conexión
DB_FILENAME      = DATA_RAW_DIR / "randstad.db"
CSV_FILENAME     = DATA_RAW_DIR / f"ofertas_randstad_{datetime.now().strftime('%Y%m%d')}.csv"

# URL base y endpoint interno de búsqueda de Randstad Argentina
BASE_URL     = "https://www.randstad.com.ar"
API_SEARCH   = f"{BASE_URL}/api/search/search-results"

TAG_RE     = re.compile(r"<[^>]+>")
# JSON-LD del detalle: el sitio lo emite con comillas simples en el atributo
# type, por eso no alcanza con buscar type="application/ld+json".
JSONLD_RE  = re.compile(
    r"<script type=['\"]application/ld\+json['\"]>(.*?)</script>", re.S
)
SLUG_RE    = re.compile(r"[^a-z0-9]+")


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
    fuente:      str = "randstad"
    fecha_scrap: str = field(default_factory=lambda: datetime.now().isoformat())
    nivel:       Optional[str] = None
    tipo_empleo: Optional[str] = None
    modalidad:   Optional[str] = None
    salario:     Optional[str] = None


# =============================================================================
# CLASE PRINCIPAL: RandstadScraper
# =============================================================================

class RandstadScraper:
    """
    Scraper para Randstad Argentina (randstad.com.ar), consultora de RRHH
    con bolsa de empleo pública en /trabajos/.
    Opera sobre la API interna del buscador (sin autenticación) y sobre las
    páginas de detalle públicas.
    Uso: exclusivamente académico (Tesis MCD - Universidad Austral).

    Estructura:
      - Búsqueda: POST /api/search/search-results
        Body: {"data": {"currentRoute": {"routeName": "search",
                          "path": "/trabajos/page-{n}/:searchParams*",
                          "params": {"searchParams": "page-{n}"}},
                         "currentLanguage": "es",
                         "searchParams": {"query": "{keyword}", "page": n}}}
        Respuesta: estructura de Elasticsearch en
        searchResults.hits.{total, hits[]._source}
      - Detalle: /trabajos/{titulo-slug}_{ciudad-slug}_{id}/
        Trae la descripción completa en
        <script type='application/ld+json'> (schema.org JobPosting)
    """

    def __init__(self):
        self.session  = requests.Session()
        self.seen_ids = set()
        self._setup_db()
        logger.info("RandstadScraper inicializado correctamente")

    # -----------------------------------------------------------------------
    # HEADERS Y REQUESTS
    # -----------------------------------------------------------------------

    def _get_headers(self, json_api: bool = False) -> dict:
        headers = {
            "Accept-Language": "es-AR,es;q=0.9,en;q=0.8",
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/120.0 Safari/537.36"),
        }
        if json_api:
            headers["Content-Type"] = "application/json"
            headers["Accept"]       = "application/json"
        return headers

    def _post(self, url: str, payload: dict) -> Optional[requests.Response]:
        for intento in range(1, MAX_RETRIES + 1):
            try:
                time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))
                response = self.session.post(
                    url, json=payload, headers=self._get_headers(json_api=True), timeout=20
                )
                if response.status_code == 200:
                    return response
                elif response.status_code == 429:
                    wait = 90 * intento
                    logger.warning(f"Rate limit (429). Esperando {wait}s...")
                    time.sleep(wait)
                else:
                    logger.warning(f"HTTP {response.status_code} en POST {url}")
            except requests.exceptions.RequestException as e:
                logger.error(f"Error de conexión (intento {intento}/{MAX_RETRIES}): {e}")
                time.sleep(10 * intento)

        logger.error(f"Fallaron todos los intentos para: {url}")
        return None

    def _get(self, url: str) -> Optional[requests.Response]:
        for intento in range(1, MAX_RETRIES + 1):
            try:
                time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))
                response = self.session.get(url, headers=self._get_headers(), timeout=20)
                if response.status_code == 200:
                    return response
                elif response.status_code in (404, 410):
                    logger.info(f"HTTP {response.status_code} en {url} (oferta vencida)")
                    return None
                elif response.status_code == 429:
                    wait = 90 * intento
                    logger.warning(f"Rate limit (429). Esperando {wait}s...")
                    time.sleep(wait)
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
    # CONSTRUCCIÓN DE PAYLOAD Y URL
    # -----------------------------------------------------------------------

    def _payload_busqueda(self, keyword: str, page: int) -> dict:
        """
        Arma el body del POST a /api/search/search-results.
        La página 1 usa el path base; de la 2 en adelante el sitio agrega
        el segmento /page-{n}/ a currentRoute (así lo hace el front-end).
        """
        if page == 1:
            path   = "/trabajos/:searchParams*"
            params = {}
        else:
            path   = f"/trabajos/page-{page}/:searchParams*"
            params = {"searchParams": f"page-{page}"}

        return {
            "data": {
                "currentRoute": {"routeName": "search", "path": path, "params": params},
                "currentLanguage": "es",
                "searchParams": {"query": keyword, "page": page},
            }
        }

    def _slug(self, texto: str) -> str:
        texto = texto.lower().strip()
        texto = SLUG_RE.sub("-", texto).strip("-")
        return texto

    def _url_detalle(self, source: dict, job_id: str) -> str:
        """
        Patrón observado en el sitio: /trabajos/{titulo-slug}_{ciudad-slug}_{id}/
        Se reconstruye el slug en vez de depender de BlueXSanitized (que a
        veces viene vacío) para tener siempre una URL válida.
        """
        titulo = source.get("JobInformation", {}).get("Title", "")
        ciudad = source.get("JobLocation", {}).get("City", "")
        slug_titulo = self._slug(titulo) or "trabajo"
        slug_ciudad = self._slug(ciudad) or "argentina"
        return f"{BASE_URL}/trabajos/{slug_titulo}_{slug_ciudad}_{job_id}/"

    # -----------------------------------------------------------------------
    # PARSING DE LOS RESULTADOS DE LA API
    # -----------------------------------------------------------------------

    def _salario_de(self, salary: dict) -> Optional[str]:
        if not salary:
            return None
        try:
            smin = float(salary.get("SalaryMin") or 0)
            smax = float(salary.get("SalaryMax") or 0)
        except (TypeError, ValueError):
            return None
        if smin <= 0 and smax <= 0:
            return None
        unidad = salary.get("CompensationType") or ""
        return f"{smin:.0f}-{smax:.0f} ({unidad})".strip()

    def _parse_hit(self, hit: dict, keyword_label: str) -> Optional[OfertaLaboral]:
        try:
            job_id = str(hit["_id"])
            if job_id in self.seen_ids:
                return None
            self.seen_ids.add(job_id)

            source   = hit.get("_source", {}) or {}
            info     = source.get("JobInformation", {}) or {}
            location = source.get("JobLocation", {}) or {}
            identity = source.get("JobIdentity", {}) or {}
            dates    = source.get("JobDates", {}) or {}

            ciudad   = location.get("City") or ""
            region   = location.get("Region") or ""
            ubicacion = ", ".join(p for p in (ciudad, region) if p) or "Argentina"

            return OfertaLaboral(
                job_id      = job_id,
                titulo      = info.get("Title") or "N/A",
                empresa     = identity.get("CompanyName") or "Randstad Argentina",
                ubicacion   = ubicacion,
                descripcion = self._limpiar_html(info.get("Description") or ""),
                fecha_pub   = dates.get("DateCreated") or "N/A",
                url         = self._url_detalle(source, job_id),
                keyword     = keyword_label,
                nivel       = info.get("Education") or None,
                tipo_empleo = info.get("JobType") or None,
                salario     = self._salario_de(source.get("Salary")),
            )
        except Exception as e:
            logger.debug(f"Error parseando hit: {e}")
            return None

    def _limpiar_html(self, html: str) -> str:
        if not html:
            return ""
        return unescape(TAG_RE.sub(" ", html)).strip()

    # -----------------------------------------------------------------------
    # ENRIQUECIMIENTO CON EL DETALLE DE LA OFERTA (JSON-LD JobPosting)
    # -----------------------------------------------------------------------

    def _enrich_with_details(self, oferta: OfertaLaboral) -> OfertaLaboral:
        response = self._get(oferta.url)
        if not response:
            return oferta

        match = JSONLD_RE.search(response.text)
        if not match:
            logger.debug(f"Sin JSON-LD en el detalle: {oferta.url}")
            return oferta

        try:
            data = json.loads(match.group(1))
        except json.JSONDecodeError as e:
            logger.debug(f"JSON-LD inválido ({oferta.url}): {e}")
            return oferta

        descripcion_completa = data.get("description")
        if descripcion_completa:
            oferta.descripcion = self._limpiar_html(descripcion_completa)

        remoto = data.get("jobLocationType") or data.get("applicantLocationRequirements")
        if remoto and "TELECOMMUTE" in json.dumps(remoto).upper():
            oferta.modalidad = "Remoto"

        empleo = data.get("employmentType")
        if empleo:
            oferta.tipo_empleo = oferta.tipo_empleo or (
                empleo if isinstance(empleo, str) else ", ".join(empleo)
            )

        return oferta

    # -----------------------------------------------------------------------
    # EXPORTAR A CSV
    # -----------------------------------------------------------------------

    def _export_to_csv(self, ofertas: list[OfertaLaboral]):
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
        logger.info("=" * 60)
        logger.info("INICIO DEL SCRAPING - Randstad Argentina")
        logger.info(f"Búsquedas: {len(SEARCH_QUERIES)} | Páginas por búsqueda: {PAGES_PER_QUERY}")
        logger.info("=" * 60)

        total_guardadas = 0
        stats = []

        for query in SEARCH_QUERIES:
            keyword       = query["keyword"]
            keyword_label = query["label"]
            logger.info(f"\n🔍 Buscando: '{keyword_label}'")

            ofertas_query = []
            max_paginas   = PAGES_PER_QUERY

            for page in range(1, PAGES_PER_QUERY + 1):
                logger.info(f"   Página {page}/{max_paginas} → query='{keyword}'")

                response = self._post(API_SEARCH, self._payload_busqueda(keyword, page))
                if not response:
                    logger.warning(f"   ⚠️  Sin respuesta. Saltando página {page}.")
                    continue

                try:
                    hits_data = response.json()["searchResults"]["hits"]
                except (KeyError, json.JSONDecodeError):
                    logger.warning("   ⚠️  Estructura de respuesta inesperada.")
                    break

                total = hits_data.get("total", 0)
                hits  = hits_data.get("hits", [])

                if page == 1 and total:
                    max_paginas = min(PAGES_PER_QUERY, math.ceil(total / RESULTS_PER_PAGE))

                if not hits:
                    logger.info("   → Sin más resultados para esta búsqueda.")
                    break

                nuevas_pagina = 0
                for hit in hits:
                    oferta = self._parse_hit(hit, keyword_label)
                    if not oferta:
                        continue
                    oferta = self._enrich_with_details(oferta)
                    self._save_to_db(oferta)
                    ofertas_query.append(oferta)
                    total_guardadas += 1
                    nuevas_pagina += 1

                logger.info(f"   → {nuevas_pagina} ofertas nuevas (total en el sitio: {total})")

                if page >= max_paginas:
                    break

            self._export_to_csv(ofertas_query)
            stats.append({"keyword": keyword_label, "ofertas": len(ofertas_query)})
            logger.info(f"✅ '{keyword_label}': {len(ofertas_query)} ofertas guardadas")

        # --- Reporte final ---
        logger.info("\n" + "=" * 60)
        logger.info("SCRAPING FINALIZADO - Randstad")
        logger.info(f"Total de ofertas únicas recopiladas: {total_guardadas}")
        logger.info(f"Base de datos : {DB_FILENAME}")
        logger.info(f"CSV exportado : {CSV_FILENAME}")
        logger.info(f"Log guardado  : {LOG_FILE}")
        logger.info("=" * 60)

        stats_file = DATA_RAW_DIR / f"stats_randstad_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(stats_file, "w", encoding="utf-8") as f:
            json.dump({
                "fuente":              "randstad",
                "fecha":               datetime.now().isoformat(),
                "total_ofertas":       total_guardadas,
                "detalle_por_keyword": stats
            }, f, ensure_ascii=False, indent=2)
        logger.info(f"Estadísticas guardadas: {stats_file}")

        return total_guardadas


# =============================================================================
# EJECUCIÓN
# =============================================================================

if __name__ == "__main__":
    print("\n🚀 Iniciando scraper de Randstad Argentina...")
    print("   Proyecto: Tesis MCD - Sistema de Recomendación Laboral")
    print("   Alumno: Cicconi, Carlos Alberto\n")

    scraper = RandstadScraper()
    total   = scraper.run()

    print(f"\n✅ Proceso completado. {total} ofertas recopiladas.")
    print(f"   Revisá los datos en: {DATA_RAW_DIR}")
