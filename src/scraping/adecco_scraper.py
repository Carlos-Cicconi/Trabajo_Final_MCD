#!/usr/bin/env python3
# =============================================================================
# adecco_scraper.py
# Tesis: "Sistema de Recomendación Laboral - Motor de Matching Inteligente"
# Alumno: Cicconi, Carlos Alberto - Maestría en Explotación de Datos (Austral)
# Módulo: T2 - Diseño y Desarrollo del Web Scraper
# Descripción: Extrae ofertas laborales de Adecco Argentina y las almacena
#              en formato CSV y SQLite para su posterior procesamiento.
#
# URL base: https://empleo.adecco.com.ar
# Estrategia: subdominio ASP.NET dedicado a la bolsa de empleo, con una API
#             JSON limpia (sin protección anti-bot) que consume el propio
#             front-end del sitio:
#               POST /api/Opportunities/SearchOpportunities   (listado)
#               GET  /api/Opportunities/GetOpportunityDetail  (detalle)
#             No requiere autenticación. Uso exclusivamente académico.
#
# Nota sobre la API: cuando una búsqueda no tiene coincidencias, el backend
# devuelve {"IsSuccessful": false, "Result": []} en vez de un resultado
# vacío exitoso — se confirmó probando keywords con y sin coincidencias
# reales ("python"/"ventas" devuelven IsSuccessful=true; "gastronomia" en
# el catálogo vigente al momento de la prueba devolvía false). Por eso acá
# se trata IsSuccessful=false como "0 resultados", no como error.
#
# Nota sobre paginación: el endpoint de búsqueda no pagina — devuelve todos
# los resultados que matchean en una sola respuesta.
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
LOG_FILE = LOGS_DIR / f"adecco_scraper_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
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
DELAY_MIN    = 1.5     # Segundos mínimos entre requests (ético)
DELAY_MAX    = 3.0     # Segundos máximos entre requests
MAX_RETRIES  = 3       # Reintentos ante fallo de conexión
DB_FILENAME  = DATA_RAW_DIR / "db_adecco.db"
CSV_FILENAME = DATA_RAW_DIR / f"ofertas_adecco_{datetime.now().strftime('%Y%m%d')}.csv"

# URL base y endpoints de la API de Adecco Argentina
BASE_URL     = "https://empleo.adecco.com.ar"
API_SEARCH   = f"{BASE_URL}/api/Opportunities/SearchOpportunities"
API_DETAIL   = f"{BASE_URL}/api/Opportunities/GetOpportunityDetail"

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
    fuente:      str = "adecco"
    fecha_scrap: str = field(default_factory=lambda: datetime.now().isoformat())
    nivel:       Optional[str] = None
    tipo_empleo: Optional[str] = None
    modalidad:   Optional[str] = None
    salario:     Optional[str] = None


# =============================================================================
# CLASE PRINCIPAL: AdeccoScraper
# =============================================================================

class AdeccoScraper:
    """
    Scraper para Adecco Argentina (empleo.adecco.com.ar), consultora de RRHH
    con bolsa de empleo pública.
    Opera sobre la API JSON del sitio, sin autenticación.
    Uso: exclusivamente académico (Tesis MCD - Universidad Austral).

    Estructura de la API:
      - Búsqueda: POST /api/Opportunities/SearchOpportunities
        Body: {"Country": "AR", "Text": "{keyword}", "JobLocation": "",
               "State": 0, "AreaId": null}
        Respuesta: {"IsSuccessful": bool, "Result": [{"City", "Description",
                    "IdOpportunity", "Salary", "Title"}, ...]}
        IsSuccessful=false equivale a "0 resultados" (no es un error).
        No pagina: devuelve todas las coincidencias en una sola respuesta.
      - Detalle: GET /api/Opportunities/GetOpportunityDetail?opportunityId={id}
        Trae: JobTitle, JobLocation, StateArea, Responsabilities,
        CandidateProfile, AdditionalInfo, ClientDescription, CreatedOn,
        SalaryLow/High, Area, Openings, EmploymentType, Benefits.
    """

    def __init__(self):
        self.session  = requests.Session()
        self.seen_ids = set()
        self._setup_db()
        logger.info("AdeccoScraper inicializado correctamente")

    # -----------------------------------------------------------------------
    # HEADERS Y REQUESTS
    # -----------------------------------------------------------------------

    def _get_headers(self) -> dict:
        return {
            "Accept":          "application/json",
            "Accept-Language": "es-AR,es;q=0.9,en;q=0.8",
        }

    def _post(self, url: str, payload: dict) -> Optional[requests.Response]:
        for intento in range(1, MAX_RETRIES + 1):
            try:
                time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))
                response = self.session.post(
                    url, json=payload,
                    headers={**self._get_headers(), "Content-Type": "application/json; charset=utf-8"},
                    timeout=20
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
    # UTILIDADES DE TEXTO
    # -----------------------------------------------------------------------

    def _limpiar_html(self, texto: str) -> str:
        """Quita tags HTML y decodifica entidades (a veces doblemente
        codificadas, ej. '&amp;nbsp;' -> '&nbsp;' -> ' ')."""
        if not texto:
            return ""
        texto = unescape(unescape(texto))
        return TAG_RE.sub(" ", texto).replace("\xa0", " ").strip()

    def _salario_de(self, salary_str: Optional[str] = None,
                     low: Optional[str] = None, high: Optional[str] = None) -> Optional[str]:
        """Arma el string de salario, descartando el caso "0.00 - 0.00" /
        "$0 - $0" que Adecco usa para "sin publicar"."""
        if low is not None or high is not None:
            texto = f"{low or '$0'} - {high or '$0'}"
        else:
            texto = salary_str or ""
        limpio = re.sub(r"[^\d]", "", texto)
        if not limpio or int(limpio) == 0:
            return None
        return texto.strip()

    # -----------------------------------------------------------------------
    # PARSING DE LA BÚSQUEDA
    # -----------------------------------------------------------------------

    def _parse_item(self, item: dict, keyword_label: str) -> Optional[OfertaLaboral]:
        try:
            job_id = str(item.get("IdOpportunity") or "")
            if not job_id or job_id in self.seen_ids:
                return None
            self.seen_ids.add(job_id)

            ubicacion = self._limpiar_html(item.get("City", "")).replace("-", ", ", 1) or "Argentina"

            return OfertaLaboral(
                job_id      = job_id,
                titulo      = self._limpiar_html(item.get("Title", "")) or "N/A",
                empresa     = "Adecco Argentina",
                ubicacion   = ubicacion,
                descripcion = self._limpiar_html(item.get("Description", "")),
                fecha_pub   = "N/A",   # se completa en _enrich_with_details()
                url         = f"{BASE_URL}/Opportunities/Opportunity.aspx?oppId={job_id}",
                keyword     = keyword_label,
                salario     = self._salario_de(salary_str=item.get("Salary")),
            )
        except Exception as e:
            logger.debug(f"Error parseando item: {e}")
            return None

    # -----------------------------------------------------------------------
    # ENRIQUECIMIENTO CON EL DETALLE DE LA OFERTA
    # -----------------------------------------------------------------------

    def _enrich_with_details(self, oferta: OfertaLaboral) -> OfertaLaboral:
        response = self._get(f"{API_DETAIL}?opportunityId={oferta.job_id}")
        if not response:
            return oferta

        try:
            data = response.json()
        except json.JSONDecodeError:
            return oferta

        if not data.get("IsSuccessful"):
            return oferta

        detalle = data.get("Result") or {}

        if detalle.get("JobTitle"):
            oferta.titulo = self._limpiar_html(detalle["JobTitle"])

        ciudad   = detalle.get("JobLocation") or ""
        provincia = detalle.get("StateArea") or ""
        if ciudad or provincia:
            oferta.ubicacion = ", ".join(p for p in (ciudad, provincia) if p)

        partes_desc = [
            self._limpiar_html(detalle.get("ClientDescription", "")),
            self._limpiar_html(detalle.get("Responsabilities", "")),
            self._limpiar_html(detalle.get("CandidateProfile", "")),
        ]
        descripcion_completa = "\n\n".join(p for p in partes_desc if p)
        if descripcion_completa:
            oferta.descripcion = descripcion_completa

        if detalle.get("CreatedOn"):
            oferta.fecha_pub = detalle["CreatedOn"]

        if detalle.get("EmploymentType"):
            oferta.tipo_empleo = detalle["EmploymentType"]

        info_adicional = self._limpiar_html(detalle.get("AdditionalInfo", "")).lower()
        if "remoto" in info_adicional or "home office" in info_adicional:
            oferta.modalidad = "Remoto"
        elif "hibrido" in info_adicional or "híbrido" in info_adicional:
            oferta.modalidad = "Híbrido"
        elif "presencial" in info_adicional:
            oferta.modalidad = "Presencial"

        salario = self._salario_de(low=detalle.get("SalaryLow"), high=detalle.get("SalaryHigh"))
        if salario:
            oferta.salario = salario

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
        """
        Ejecuta el scraping completo sobre todas las búsquedas configuradas.
        El endpoint de búsqueda de Adecco no pagina: una sola llamada por
        keyword trae todas las coincidencias.
        """
        logger.info("=" * 60)
        logger.info("INICIO DEL SCRAPING - Adecco Argentina")
        logger.info(f"Búsquedas: {len(SEARCH_QUERIES)}")
        logger.info("=" * 60)

        total_guardadas = 0
        stats = []

        for query in SEARCH_QUERIES:
            keyword       = query["keyword"]
            keyword_label = query["label"]
            logger.info(f"\n🔍 Buscando: '{keyword_label}'")

            payload = {
                "Country":     "AR",
                "Text":        keyword,
                "JobLocation": "",
                "State":       0,
                "AreaId":      None,
            }
            response = self._post(API_SEARCH, payload)

            ofertas_query = []

            if not response:
                logger.warning(f"   ⚠️  Sin respuesta para '{keyword_label}'.")
            else:
                try:
                    data = response.json()
                except json.JSONDecodeError:
                    data = {}

                # IsSuccessful=false significa "0 resultados", no un error.
                items = data.get("Result") or []
                logger.info(f"   → {len(items)} ofertas encontradas")

                for item in items:
                    oferta = self._parse_item(item, keyword_label)
                    if not oferta:
                        continue
                    oferta = self._enrich_with_details(oferta)
                    self._save_to_db(oferta)
                    ofertas_query.append(oferta)
                    total_guardadas += 1

            self._export_to_csv(ofertas_query)
            stats.append({"keyword": keyword_label, "ofertas": len(ofertas_query)})
            logger.info(f"✅ '{keyword_label}': {len(ofertas_query)} ofertas guardadas")

        # --- Reporte final ---
        logger.info("\n" + "=" * 60)
        logger.info("SCRAPING FINALIZADO - Adecco")
        logger.info(f"Total de ofertas únicas recopiladas: {total_guardadas}")
        logger.info(f"Base de datos : {DB_FILENAME}")
        logger.info(f"CSV exportado : {CSV_FILENAME}")
        logger.info(f"Log guardado  : {LOG_FILE}")
        logger.info("=" * 60)

        stats_file = DATA_RAW_DIR / f"stats_adecco_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(stats_file, "w", encoding="utf-8") as f:
            json.dump({
                "fuente":              "adecco",
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
    print("\n🚀 Iniciando scraper de Adecco Argentina...")
    print("   Proyecto: Tesis MCD - Sistema de Recomendación Laboral")
    print("   Alumno: Cicconi, Carlos Alberto\n")

    scraper = AdeccoScraper()
    total   = scraper.run()

    print(f"\n✅ Proceso completado. {total} ofertas recopiladas.")
    print(f"   Revisá los datos en: {DATA_RAW_DIR}")
