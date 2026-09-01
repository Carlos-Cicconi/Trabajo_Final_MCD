#!/usr/bin/env python3
# =============================================================================
# computrabajo_scraper.py
# Tesis: "Sistema de Recomendación Laboral - Motor de Matching Inteligente"
# Alumno: Cicconi, Carlos Alberto - Maestría en Explotación de Datos (Austral)
# Módulo: T2 - Diseño y Desarrollo del Web Scraper
# Descripción: Extrae ofertas laborales de Computrabajo Argentina y las
#              almacena en formato CSV y SQLite para su posterior procesamiento.
#
# URL base: https://www.computrabajo.com.ar
# Estrategia: HTML scraping sobre páginas públicas de resultados y detalle.
#             No requiere autenticación. Uso exclusivamente académico.
# =============================================================================

import requests
import time
import random
import logging
import sqlite3
import csv
import hashlib
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

BASE_DIR     = Path("/media/carlos-a-cicconi/Common1/Repositorios-Ing.Carlos-Cicconi/Trabajo_Final_MCD")
DATA_RAW_DIR = BASE_DIR / "data" / "raw"
LOGS_DIR     = BASE_DIR / "logs"

DATA_RAW_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)

# --- Logging ---
LOG_FILE = LOGS_DIR / f"computrabajo_scraper_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
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

# Búsquedas objetivo — mismas que LinkedIn para construir corpus comparable
# Computrabajo usa slugs en la URL: espacios → guiones, todo minúsculas
SEARCH_QUERIES = [
    {"keyword": "data-scientist",           "label": "Data Scientist"},
    {"keyword": "data-analyst",             "label": "Data Analyst"},
    {"keyword": "machine-learning",         "label": "Machine Learning Engineer"},
    {"keyword": "nlp-engineer",             "label": "NLP Engineer"},
    {"keyword": "data-engineer",            "label": "Data Engineer"},
    {"keyword": "software-engineer",        "label": "Software Engineer"},
    {"keyword": "python",                   "label": "Python Developer"},
    {"keyword": "desarrollador-backend",    "label": "Desarrollador Backend"},
    {"keyword": "desarrollador-full-stack", "label": "Desarrollador Full Stack"},
    {"keyword": "ingeniero-de-datos",       "label": "Ingeniero de Datos"},
]

# Parámetros de scraping
PAGES_PER_QUERY = 5       # ~20 ofertas por página → ~100 por búsqueda
DELAY_MIN       = 3.0     # Segundos mínimos entre requests (ético)
DELAY_MAX       = 6.0     # Segundos máximos entre requests
MAX_RETRIES     = 3       # Reintentos ante fallo de conexión
DB_FILENAME     = DATA_RAW_DIR / "computrabajo.db"
CSV_FILENAME    = DATA_RAW_DIR / f"ofertas_computrabajo_{datetime.now().strftime('%Y%m%d')}.csv"

# URL base de Computrabajo Argentina
BASE_URL        = "https://www.computrabajo.com.ar"


# =============================================================================
# DATACLASS: Estructura de una oferta laboral
# (Idéntica a linkedin_scraper.py para facilitar la unificación del corpus)
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
    fuente:      str = "computrabajo"
    fecha_scrap: str = field(default_factory=lambda: datetime.now().isoformat())
    nivel:       Optional[str] = None
    tipo_empleo: Optional[str] = None
    modalidad:   Optional[str] = None
    salario:     Optional[str] = None   # Computrabajo a veces publica salario


# =============================================================================
# CLASE PRINCIPAL: ComputrabajoScraper
# =============================================================================

class ComputrabajoScraper:
    """
    Scraper para Computrabajo Argentina (computrabajo.com.ar).
    Opera sobre páginas HTML públicas sin autenticación.
    Uso: exclusivamente académico (Tesis MCD - Universidad Austral).

    Estructura de URLs:
      - Búsqueda:  /trabajo-de-{keyword}?p={page}
      - Detalle:   /trabajo/{slug-de-la-oferta}
    """

    def __init__(self):
        self.session  = requests.Session()
        self.ua       = UserAgent()
        self.seen_ids = set()
        self._setup_db()
        logger.info("ComputrabajoScraper inicializado correctamente")

    # -----------------------------------------------------------------------
    # HEADERS Y REQUESTS
    # -----------------------------------------------------------------------

    def _get_headers(self) -> dict:
        """Genera headers que simulan un navegador real."""
        return {
            "User-Agent":      self.ua.random,
            "Accept-Language": "es-AR,es;q=0.9,en;q=0.8",
            "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection":      "keep-alive",
            "Referer":         BASE_URL,
            "DNT":             "1",
        }

    def _request(self, url: str) -> Optional[requests.Response]:
        """Realiza un GET con reintentos y delay ético entre requests."""
        for intento in range(1, MAX_RETRIES + 1):
            try:
                time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))
                response = self.session.get(
                    url,
                    headers=self._get_headers(),
                    timeout=20
                )
                if response.status_code == 200:
                    return response
                elif response.status_code == 429:
                    wait = 90 * intento
                    logger.warning(f"Rate limit (429). Esperando {wait}s...")
                    time.sleep(wait)
                elif response.status_code == 403:
                    logger.warning(f"HTTP 403 en {url}. Rotando User-Agent y reintentando...")
                    time.sleep(15 * intento)
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
    # GENERACIÓN DE job_id
    # -----------------------------------------------------------------------

    def _make_job_id(self, url: str) -> str:
        """
        Computrabajo no expone un ID numérico explícito.
        Se genera un hash MD5 sobre la URL del detalle, que es única por oferta.
        Esto garantiza deduplicación consistente entre ejecuciones.
        """
        return hashlib.md5(url.encode()).hexdigest()[:16]

    # -----------------------------------------------------------------------
    # PARSING DE PÁGINA DE RESULTADOS
    # -----------------------------------------------------------------------

    def _parse_search_page(self, html: str, keyword_label: str) -> list[OfertaLaboral]:
        """
        Parsea la página de listado de resultados de Computrabajo.
        Cada tarjeta de oferta tiene la clase 'box_offer' o similar.
        """
        soup    = BeautifulSoup(html, "lxml")
        ofertas = []

        # Computrabajo usa artículos con clase 'box_offer' para cada tarjeta
        cards = soup.find_all("article", class_=lambda c: c and "box_offer" in c)

        # Fallback: buscar por estructura de links de ofertas
        if not cards:
            cards = soup.find_all("article")

        logger.debug(f"   Cards encontradas en HTML: {len(cards)}")

        for card in cards:
            try:
                # --- URL y job_id ---
                link_tag = card.find("a", class_=lambda c: c and "js-o-link" in (c or ""))
                if not link_tag:
                    link_tag = card.find("h2") and card.find("h2").find("a")
                if not link_tag:
                    link_tag = card.find("a", href=lambda h: h and "/trabajo/" in h)
                if not link_tag:
                    continue

                href   = link_tag.get("href", "")
                url    = href if href.startswith("http") else BASE_URL + href
                job_id = self._make_job_id(url)

                if job_id in self.seen_ids:
                    continue
                self.seen_ids.add(job_id)

                # --- Título ---
                titulo_tag = card.find("h2") or card.find("h3")
                titulo = titulo_tag.get_text(strip=True) if titulo_tag else "N/A"

                # --- Empresa ---
                empresa_tag = card.find("a", class_=lambda c: c and "company" in (c or "").lower())
                if not empresa_tag:
                    empresa_tag = card.find("p", class_=lambda c: c and "company" in (c or "").lower())
                empresa = empresa_tag.get_text(strip=True) if empresa_tag else "N/A"

                # --- Ubicación ---
                ubicacion_tag = card.find("span", class_=lambda c: c and "location" in (c or "").lower())
                if not ubicacion_tag:
                    ubicacion_tag = card.find("li", class_=lambda c: c and "location" in (c or "").lower())
                ubicacion = ubicacion_tag.get_text(strip=True) if ubicacion_tag else "Argentina"

                # --- Fecha de publicación ---
                fecha_tag = card.find("time") or card.find("span", class_=lambda c: c and "date" in (c or "").lower())
                fecha_pub = fecha_tag.get("datetime", fecha_tag.get_text(strip=True)) if fecha_tag else "N/A"

                oferta = OfertaLaboral(
                    job_id      = job_id,
                    titulo      = titulo,
                    empresa     = empresa,
                    ubicacion   = ubicacion,
                    descripcion = "",   # Se completa en _enrich_with_details()
                    fecha_pub   = fecha_pub,
                    url         = url,
                    keyword     = keyword_label,
                )
                ofertas.append(oferta)

            except Exception as e:
                logger.debug(f"Error parseando card: {e}")
                continue

        return ofertas

    # -----------------------------------------------------------------------
    # ENRIQUECIMIENTO CON DESCRIPCIÓN COMPLETA (página de detalle)
    # -----------------------------------------------------------------------

    def _enrich_with_details(self, oferta: OfertaLaboral) -> OfertaLaboral:
        """
        Accede a la página de detalle de cada oferta para extraer:
        - Descripción completa del puesto
        - Nivel/seniority
        - Tipo de empleo (full-time, part-time, etc.)
        - Modalidad (presencial, remoto, híbrido)
        - Salario (cuando está disponible)
        """
        response = self._request(oferta.url)
        if not response:
            return oferta

        soup = BeautifulSoup(response.text, "lxml")

        # --- Descripción completa ---
        # Computrabajo envuelve la descripción en un div con id o clase específica
        desc = (
            soup.find("div", id="description_offer")
            or soup.find("div", class_=lambda c: c and "description" in (c or "").lower())
            or soup.find("section", class_=lambda c: c and "description" in (c or "").lower())
        )
        if desc:
            oferta.descripcion = desc.get_text(separator=" ", strip=True)

        # --- Metadatos de la oferta (lista de características) ---
        # Computrabajo muestra una lista de tags/chips con los atributos del puesto
        tags = soup.find_all("li", class_=lambda c: c and "tag" in (c or "").lower())
        if not tags:
            tags = soup.find_all("span", class_=lambda c: c and "tag" in (c or "").lower())

        for tag in tags:
            texto = tag.get_text(strip=True).lower()
            if any(x in texto for x in ["full time", "part time", "tiempo completo", "tiempo parcial"]):
                oferta.tipo_empleo = tag.get_text(strip=True)
            elif any(x in texto for x in ["remoto", "remote", "híbrido", "presencial", "home office"]):
                oferta.modalidad = tag.get_text(strip=True)
            elif any(x in texto for x in ["senior", "semi", "junior", "trainee", "jr", "sr", "ssr"]):
                oferta.nivel = tag.get_text(strip=True)

        # --- Salario (si está publicado) ---
        salario_tag = (
            soup.find("span", class_=lambda c: c and "salary" in (c or "").lower())
            or soup.find("div",  class_=lambda c: c and "salary" in (c or "").lower())
            or soup.find("p",    class_=lambda c: c and "salary" in (c or "").lower())
        )
        if salario_tag:
            oferta.salario = salario_tag.get_text(strip=True)

        # --- Empresa (completar si quedó N/A en el listado) ---
        if oferta.empresa == "N/A":
            empresa_tag = soup.find("a", class_=lambda c: c and "company" in (c or "").lower())
            if empresa_tag:
                oferta.empresa = empresa_tag.get_text(strip=True)

        return oferta

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
        Para cada keyword itera sobre las páginas de resultados, extrae las
        tarjetas y luego enriquece cada oferta con su página de detalle.
        """
        logger.info("=" * 60)
        logger.info("INICIO DEL SCRAPING - Computrabajo Argentina")
        logger.info(f"Búsquedas: {len(SEARCH_QUERIES)} | Páginas por búsqueda: {PAGES_PER_QUERY}")
        logger.info("=" * 60)

        total_guardadas = 0
        stats = []

        for query in SEARCH_QUERIES:
            keyword      = query["keyword"]
            keyword_label= query["label"]
            logger.info(f"\n🔍 Buscando: '{keyword_label}'")

            ofertas_query = []

            for page in range(1, PAGES_PER_QUERY + 1):

                # Computrabajo pagina con ?p=N (página 1 no lleva parámetro)
                if page == 1:
                    url = f"{BASE_URL}/trabajo-de-{keyword}"
                else:
                    url = f"{BASE_URL}/trabajo-de-{keyword}?p={page}"

                logger.info(f"   Página {page}/{PAGES_PER_QUERY} → {url}")

                response = self._request(url)
                if not response:
                    logger.warning(f"   ⚠️  Sin respuesta. Saltando página {page}.")
                    continue

                nuevas = self._parse_search_page(response.text, keyword_label)
                logger.info(f"   → {len(nuevas)} ofertas nuevas encontradas")

                if not nuevas:
                    logger.info("   → Sin más resultados para esta búsqueda.")
                    break

                # Enriquecer cada oferta con su página de detalle
                for oferta in nuevas:
                    logger.debug(f"      Detalle: {oferta.url}")
                    oferta = self._enrich_with_details(oferta)
                    self._save_to_db(oferta)
                    ofertas_query.append(oferta)
                    total_guardadas += 1

            self._export_to_csv(ofertas_query)
            stats.append({"keyword": keyword_label, "ofertas": len(ofertas_query)})
            logger.info(f"✅ '{keyword_label}': {len(ofertas_query)} ofertas guardadas")

        # --- Reporte final ---
        logger.info("\n" + "=" * 60)
        logger.info("SCRAPING FINALIZADO - Computrabajo")
        logger.info(f"Total de ofertas únicas recopiladas: {total_guardadas}")
        logger.info(f"Base de datos : {DB_FILENAME}")
        logger.info(f"CSV exportado : {CSV_FILENAME}")
        logger.info(f"Log guardado  : {LOG_FILE}")
        logger.info("=" * 60)

        # Guardar estadísticas en JSON
        stats_file = DATA_RAW_DIR / f"stats_computrabajo_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(stats_file, "w", encoding="utf-8") as f:
            json.dump({
                "fuente":          "computrabajo",
                "fecha":           datetime.now().isoformat(),
                "total_ofertas":   total_guardadas,
                "detalle_por_keyword": stats
            }, f, ensure_ascii=False, indent=2)
        logger.info(f"Estadísticas guardadas: {stats_file}")

        return total_guardadas


# =============================================================================
# EJECUCIÓN
# =============================================================================

if __name__ == "__main__":
    print("\n🚀 Iniciando scraper de Computrabajo Argentina...")
    print("   Proyecto: Tesis MCD - Sistema de Recomendación Laboral")
    print("   Alumno: Cicconi, Carlos Alberto\n")

    scraper = ComputrabajoScraper()
    total   = scraper.run()

    print(f"\n✅ Proceso completado. {total} ofertas recopiladas.")
    print(f"   Revisá los datos en: {DATA_RAW_DIR}")
