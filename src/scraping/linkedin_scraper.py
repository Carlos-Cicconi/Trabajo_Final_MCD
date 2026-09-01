#!/usr/bin/env python3
# =============================================================================
# linkedin_scraper.py
# Tesis: "Sistema de Recomendación Laboral - Motor de Matching Inteligente"
# Alumno: Cicconi, Carlos Alberto - Maestría en Explotación de Datos (Austral)
# Módulo: T2 - Diseño y Desarrollo del Web Scraper
# Descripción: Extrae ofertas laborales de LinkedIn (endpoint público) y las
#              almacena en formato CSV y SQLite para su posterior procesamiento.
# =============================================================================

import requests
import time
import random
import logging
import sqlite3
import csv
import os
import json
from datetime import datetime
from bs4 import BeautifulSoup
from dataclasses import dataclass, asdict, field
from typing import Optional
from pathlib import Path
from fake_useragent import UserAgent

# =============================================================================
# CONFIGURACIÓN DEL PROYECTO
# =============================================================================

BASE_DIR = Path("/media/carlos-a-cicconi/Common1/Repositorios-Ing.Carlos-Cicconi/Trabajo_Final_MCD")
DATA_RAW_DIR  = BASE_DIR / "data" / "raw"
LOGS_DIR      = BASE_DIR / "logs"

DATA_RAW_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)

# --- Logging ---
LOG_FILE = LOGS_DIR / f"scraper_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
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

# Búsquedas objetivo (alineadas con el corpus de tu tesis: Tecnología, DS, Ing.)
SEARCH_QUERIES = [
    {"keyword": "Data Scientist",          "location": "Argentina"},
    {"keyword": "Data Analyst",            "location": "Argentina"},
    {"keyword": "Machine Learning Engineer","location": "Argentina"},
    {"keyword": "NLP Engineer",            "location": "Argentina"},
    {"keyword": "Data Engineer",           "location": "Argentina"},
    {"keyword": "Software Engineer",       "location": "Argentina"},
    {"keyword": "Python Developer",        "location": "Argentina"},
    {"keyword": "Desarrollador Backend",   "location": "Argentina"},
    {"keyword": "Desarrollador Full Stack","location": "Argentina"},
    {"keyword": "Ingeniero de Datos",      "location": "Argentina"},
]

# Parámetros de scraping
PAGES_PER_QUERY    = 5       # 25 ofertas por página → ~125 por búsqueda
DELAY_MIN          = 2.5     # Segundos mínimos entre requests (ético)
DELAY_MAX          = 5.0     # Segundos máximos entre requests
MAX_RETRIES        = 3       # Reintentos ante fallo de conexión
DB_FILENAME        = DATA_RAW_DIR / "linkedin.db"
CSV_FILENAME       = DATA_RAW_DIR / f"ofertas_linkedin_{datetime.now().strftime('%Y%m%d')}.csv"


# =============================================================================
# DATACLASS: Estructura de una oferta laboral
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
    fecha_scrap: str = field(default_factory=lambda: datetime.now().isoformat())
    nivel:       Optional[str] = None
    tipo_empleo: Optional[str] = None
    modalidad:   Optional[str] = None


# =============================================================================
# CLASE PRINCIPAL: LinkedInScraper
# =============================================================================

class LinkedInScraper:
    """
    Scraper para el endpoint público de LinkedIn Jobs.
    Opera sobre linkedin.com/jobs-guest/ que no requiere autenticación,
    siendo el mismo endpoint que usa LinkedIn para usuarios no logueados.
    Uso: exclusivamente académico (Tesis MCD - Universidad Austral).
    """

    BASE_SEARCH_URL = (
        "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
        "?keywords={keyword}&location={location}&start={start}&f_WT=2"
    )
    BASE_DETAIL_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"

    def __init__(self):
        self.session  = requests.Session()
        self.ua       = UserAgent()
        self.seen_ids = set()
        self._setup_db()
        logger.info("LinkedInScraper inicializado correctamente")

    def _get_headers(self) -> dict:
        """Genera headers aleatorios para cada request."""
        return {
            "User-Agent":      self.ua.random,
            "Accept-Language": "es-AR,es;q=0.9,en;q=0.8",
            "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection":      "keep-alive",
            "Referer":         "https://www.linkedin.com/jobs/",
        }

    def _request(self, url: str) -> Optional[requests.Response]:
        """Realiza un request con reintentos y delay ético."""
        for intento in range(1, MAX_RETRIES + 1):
            try:
                time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))
                response = self.session.get(url, headers=self._get_headers(), timeout=15)
                if response.status_code == 200:
                    return response
                elif response.status_code == 429:
                    wait = 60 * intento
                    logger.warning(f"Rate limit (429). Esperando {wait}s antes de reintentar...")
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
                    fecha_scrap TEXT,
                    nivel       TEXT,
                    tipo_empleo TEXT,
                    modalidad   TEXT
                )
            """)
            conn.commit()
        logger.info(f"Base de datos lista: {DB_FILENAME}")

    def _save_to_db(self, oferta: OfertaLaboral):
        """Guarda una oferta en SQLite (ignora duplicados)."""
        with sqlite3.connect(DB_FILENAME) as conn:
            try:
                conn.execute("""
                    INSERT OR IGNORE INTO ofertas VALUES
                    (:job_id, :titulo, :empresa, :ubicacion, :descripcion,
                     :fecha_pub, :url, :keyword, :fecha_scrap, :nivel,
                     :tipo_empleo, :modalidad)
                """, asdict(oferta))
                conn.commit()
            except sqlite3.Error as e:
                logger.error(f"Error guardando en DB: {e}")

    # -----------------------------------------------------------------------
    # PARSING DE RESULTADOS DE BÚSQUEDA
    # -----------------------------------------------------------------------

    def _parse_job_cards(self, html: str, keyword: str) -> list[OfertaLaboral]:
        """Parsea las tarjetas de resultado de una página de búsqueda."""
        soup    = BeautifulSoup(html, "lxml")
        ofertas = []

        for card in soup.find_all("li"):
            try:
                # Extraer job_id del atributo data-entity-urn
                div = card.find("div", {"data-entity-urn": True})
                if not div:
                    continue
                job_id = div["data-entity-urn"].split(":")[-1]

                if job_id in self.seen_ids:
                    continue
                self.seen_ids.add(job_id)

                titulo   = card.find("h3", class_="base-search-card__title")
                empresa  = card.find("h4", class_="base-search-card__subtitle")
                ubicacion= card.find("span", class_="job-search-card__location")
                fecha    = card.find("time")

                oferta = OfertaLaboral(
                    job_id   = job_id,
                    titulo   = titulo.text.strip()    if titulo    else "N/A",
                    empresa  = empresa.text.strip()   if empresa   else "N/A",
                    ubicacion= ubicacion.text.strip() if ubicacion else "N/A",
                    descripcion= "",  # Se completa en _enrich_with_details()
                    fecha_pub= fecha["datetime"] if fecha and fecha.has_attr("datetime") else "N/A",
                    url      = f"https://www.linkedin.com/jobs/view/{job_id}",
                    keyword  = keyword,
                )
                ofertas.append(oferta)

            except Exception as e:
                logger.debug(f"Error parseando card: {e}")
                continue

        return ofertas

    # -----------------------------------------------------------------------
    # ENRIQUECIMIENTO CON DESCRIPCIÓN COMPLETA
    # -----------------------------------------------------------------------

    def _enrich_with_details(self, oferta: OfertaLaboral) -> OfertaLaboral:
        """
        Obtiene la descripción completa y metadatos adicionales
        desde el endpoint de detalle de cada oferta.
        """
        url      = self.BASE_DETAIL_URL.format(job_id=oferta.job_id)
        response = self._request(url)

        if not response:
            return oferta

        soup = BeautifulSoup(response.text, "lxml")

        # Descripción completa
        desc_div = soup.find("div", class_="description__text")
        if desc_div:
            oferta.descripcion = desc_div.get_text(separator=" ", strip=True)

        # Metadatos adicionales (nivel, tipo, modalidad)
        criteria = soup.find_all("li", class_="description__job-criteria-item")
        for item in criteria:
            header = item.find("h3")
            value  = item.find("span")
            if not header or not value:
                continue
            header_text = header.text.strip().lower()
            value_text  = value.text.strip()
            if "seniority" in header_text or "nivel" in header_text:
                oferta.nivel = value_text
            elif "employment" in header_text or "tipo" in header_text:
                oferta.tipo_empleo = value_text
            elif "work" in header_text or "modalidad" in header_text:
                oferta.modalidad = value_text

        return oferta

    # -----------------------------------------------------------------------
    # EXPORTAR A CSV
    # -----------------------------------------------------------------------

    def _export_to_csv(self, ofertas: list[OfertaLaboral]):
        """Exporta todas las ofertas a un archivo CSV."""
        if not ofertas:
            return

        fieldnames = list(asdict(ofertas[0]).keys())
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
        """
        logger.info("=" * 60)
        logger.info("INICIO DEL SCRAPING - LinkedIn Jobs")
        logger.info(f"Búsquedas: {len(SEARCH_QUERIES)} | Páginas por búsqueda: {PAGES_PER_QUERY}")
        logger.info("=" * 60)

        total_guardadas = 0
        stats = []

        for query in SEARCH_QUERIES:
            keyword  = query["keyword"]
            location = query["location"]
            logger.info(f"\n🔍 Buscando: '{keyword}' en '{location}'")

            ofertas_query = []

            for page in range(0, PAGES_PER_QUERY * 25, 25):
                url = self.BASE_SEARCH_URL.format(
                    keyword=requests.utils.quote(keyword),
                    location=requests.utils.quote(location),
                    start=page
                )
                logger.info(f"   Página {page // 25 + 1}/{PAGES_PER_QUERY} → {url}")

                response = self._request(url)
                if not response:
                    logger.warning(f"   ⚠️ Sin respuesta. Saltando página {page}.")
                    continue

                nuevas = self._parse_job_cards(response.text, keyword)
                logger.info(f"   → {len(nuevas)} ofertas nuevas encontradas")

                if not nuevas:
                    logger.info("   → No hay más resultados para esta búsqueda.")
                    break

                # Enriquecer con descripción completa
                for oferta in nuevas:
                    oferta = self._enrich_with_details(oferta)
                    self._save_to_db(oferta)
                    ofertas_query.append(oferta)
                    total_guardadas += 1

            self._export_to_csv(ofertas_query)
            stats.append({"keyword": keyword, "ofertas": len(ofertas_query)})
            logger.info(f"✅ '{keyword}': {len(ofertas_query)} ofertas guardadas")

        # --- Reporte final ---
        logger.info("\n" + "=" * 60)
        logger.info("SCRAPING FINALIZADO")
        logger.info(f"Total de ofertas únicas recopiladas: {total_guardadas}")
        logger.info(f"Base de datos: {DB_FILENAME}")
        logger.info(f"CSV exportado: {CSV_FILENAME}")
        logger.info(f"Log guardado:  {LOG_FILE}")
        logger.info("=" * 60)

        # Guardar estadísticas en JSON
        stats_file = DATA_RAW_DIR / f"stats_linkedin_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(stats_file, "w", encoding="utf-8") as f:
            json.dump({
                "fecha": datetime.now().isoformat(),
                "total_ofertas": total_guardadas,
                "detalle_por_keyword": stats
            }, f, ensure_ascii=False, indent=2)
        logger.info(f"Estadísticas guardadas: {stats_file}")

        return total_guardadas


# =============================================================================
# EJECUCIÓN
# =============================================================================

if __name__ == "__main__":
    print("\n🚀 Iniciando scraper de LinkedIn Jobs...")
    print("   Proyecto: Tesis MCD - Sistema de Recomendación Laboral")
    print("   Alumno: Cicconi, Carlos Alberto\n")

    scraper = LinkedInScraper()
    total   = scraper.run()

    print(f"\n✅ Proceso completado. {total} ofertas recopiladas.")
    print(f"   Revisá los datos en: {DATA_RAW_DIR}")
