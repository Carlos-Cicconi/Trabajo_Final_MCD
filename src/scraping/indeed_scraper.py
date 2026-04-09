#!/usr/bin/env python3
# =============================================================================
# indeed_scraper.py
# Tesis: "Sistema de Recomendación Laboral - Motor de Matching Inteligente"
# Alumno: Cicconi, Carlos Alberto - Maestría en Explotación de Datos (Austral)
# Módulo: T2 - Diseño y Desarrollo del Web Scraper
#
# Estrategia anti-Cloudflare: curl_cffi
#   Indeed está protegido por Cloudflare, que bloquea requests estándar
#   (Python requests, urllib) e incluso Playwright headless verificando el
#   TLS fingerprint (JA3/JA4 hash). curl_cffi resuelve esto impersonando
#   el fingerprint TLS exacto de Chrome, superando el Security Check.
#
# Instalación (una sola vez, con el entorno activo):
#   pip install curl-cffi
# =============================================================================

import logging
import sqlite3
import csv
import json
import re
import random
import time
from datetime import datetime
from bs4 import BeautifulSoup
from dataclasses import dataclass, asdict, field
from typing import Optional
from pathlib import Path
from urllib.parse import urlencode

from curl_cffi import requests as cffi_requests   # reemplaza a requests estándar

# =============================================================================
# CONFIGURACIÓN DEL PROYECTO
# =============================================================================

BASE_DIR     = Path("/media/carlos-a-cicconi/Common1/Repositorios-Ing.Carlos-Cicconi/Trabajo_Final_MCD")
DATA_RAW_DIR = BASE_DIR / "data" / "raw"
LOGS_DIR     = BASE_DIR / "logs"

DATA_RAW_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)

LOG_FILE = LOGS_DIR / f"indeed_scraper_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
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

SEARCH_QUERIES = [
    {"keyword": "Data Scientist",            "location": "Argentina"},
    {"keyword": "Data Analyst",              "location": "Argentina"},
    {"keyword": "Machine Learning Engineer", "location": "Argentina"},
    {"keyword": "NLP Engineer",              "location": "Argentina"},
    {"keyword": "Data Engineer",             "location": "Argentina"},
    {"keyword": "Software Engineer",         "location": "Argentina"},
    {"keyword": "Python Developer",          "location": "Argentina"},
    {"keyword": "Desarrollador Backend",     "location": "Argentina"},
    {"keyword": "Desarrollador Full Stack",  "location": "Argentina"},
    {"keyword": "Ingeniero de Datos",        "location": "Argentina"},
]

PAGES_PER_QUERY  = 5
DELAY_MIN        = 4.0
DELAY_MAX        = 8.0
DELAY_DETAIL_MIN = 3.0
DELAY_DETAIL_MAX = 6.0
DELAY_QUERY_MIN  = 10.0
DELAY_QUERY_MAX  = 18.0
MAX_RETRIES      = 3

DB_FILENAME  = DATA_RAW_DIR / "ofertas_indeed.db"
CSV_FILENAME = DATA_RAW_DIR / f"indeed_{datetime.now().strftime('%Y%m%d')}.csv"
BASE_URL     = "https://ar.indeed.com"
SEARCH_URL   = f"{BASE_URL}/jobs"
DETAIL_URL   = f"{BASE_URL}/viewjob"


# =============================================================================
# DATACLASS — compatible con linkedin_scraper.py y computrabajo_scraper.py
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
    fuente:      str = "indeed"
    fecha_scrap: str = field(default_factory=lambda: datetime.now().isoformat())
    nivel:       Optional[str] = None
    tipo_empleo: Optional[str] = None
    modalidad:   Optional[str] = None
    salario:     Optional[str] = None


# =============================================================================
# CLASE PRINCIPAL
# =============================================================================

class IndeedScraper:
    """
    Scraper para Indeed Argentina usando curl_cffi para impersonar
    el TLS fingerprint de Chrome y superar la protección Cloudflare.
    Uso: exclusivamente académico (Tesis MCD - Universidad Austral).
    """

    def __init__(self):
        self.seen_ids = set()
        # Sesión curl_cffi impersonando Chrome 110 (JA3/JA4 fingerprint real)
        self.session  = cffi_requests.Session(impersonate="chrome110")
        self._setup_db()
        self._warmup()
        logger.info("IndeedScraper (curl_cffi) inicializado")

    # -----------------------------------------------------------------------
    # WARMUP Y HEADERS
    # -----------------------------------------------------------------------

    def _get_headers(self, referer: str = BASE_URL) -> dict:
        return {
            "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "es-AR,es;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept-Encoding": "gzip, deflate, br",
            "Referer":         referer,
            "DNT":             "1",
            "Connection":      "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }

    def _warmup(self):
        """Visita la home para obtener cookies de sesión antes de buscar."""
        try:
            logger.info("Calentando sesión en Indeed (curl_cffi)...")
            self.session.get(BASE_URL, headers=self._get_headers(), timeout=20)
            time.sleep(random.uniform(2.0, 4.0))
        except Exception as e:
            logger.warning(f"Warmup fallido (no crítico): {e}")

    # -----------------------------------------------------------------------
    # REQUEST CON REINTENTOS
    # -----------------------------------------------------------------------

    def _request(self, url: str, referer: str = BASE_URL) -> Optional[str]:
        """
        Realiza GET con curl_cffi (TLS fingerprint Chrome) y devuelve el HTML.
        Verifica que la respuesta no sea la página de Cloudflare Security Check.
        """
        for intento in range(1, MAX_RETRIES + 1):
            try:
                time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))
                response = self.session.get(
                    url,
                    headers=self._get_headers(referer=referer),
                    timeout=25
                )

                if response.status_code != 200:
                    logger.warning(f"HTTP {response.status_code} (intento {intento}) → {url}")
                    time.sleep(20 * intento)
                    continue

                html = response.text

                # Verificar que no sea página de Cloudflare/Security Check
                if any(x in html for x in [
                    "Security Check", "security check",
                    "Enable JavaScript and cookies",
                    "Ray ID", "cf-browser-verification",
                    "Just a moment"
                ]):
                    logger.warning(f"⚠️  Cloudflare challenge detectado (intento {intento}). Esperando...")
                    time.sleep(45 * intento)
                    continue

                return html

            except Exception as e:
                logger.error(f"Error curl_cffi (intento {intento}/{MAX_RETRIES}): {e}")
                time.sleep(15 * intento)

        logger.error(f"Fallaron todos los intentos para: {url}")
        return None

    # -----------------------------------------------------------------------
    # BASE DE DATOS
    # -----------------------------------------------------------------------

    def _setup_db(self):
        with sqlite3.connect(DB_FILENAME) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ofertas (
                    job_id TEXT PRIMARY KEY, titulo TEXT, empresa TEXT,
                    ubicacion TEXT, descripcion TEXT, fecha_pub TEXT,
                    url TEXT, keyword TEXT, fuente TEXT, fecha_scrap TEXT,
                    nivel TEXT, tipo_empleo TEXT, modalidad TEXT, salario TEXT
                )
            """)
            conn.commit()
        logger.info(f"Base de datos lista: {DB_FILENAME}")

    def _save_to_db(self, oferta: OfertaLaboral):
        with sqlite3.connect(DB_FILENAME) as conn:
            try:
                conn.execute("""
                    INSERT OR IGNORE INTO ofertas VALUES
                    (:job_id,:titulo,:empresa,:ubicacion,:descripcion,
                     :fecha_pub,:url,:keyword,:fuente,:fecha_scrap,
                     :nivel,:tipo_empleo,:modalidad,:salario)
                """, asdict(oferta))
                conn.commit()
            except sqlite3.Error as e:
                logger.error(f"DB error: {e}")

    # -----------------------------------------------------------------------
    # CONSTRUCCIÓN DE URL
    # -----------------------------------------------------------------------

    def _build_search_url(self, keyword: str, location: str, start: int) -> str:
        return f"{SEARCH_URL}?{urlencode({'q': keyword, 'l': location, 'start': start, 'sort': 'date', 'fromage': '30'})}"

    # -----------------------------------------------------------------------
    # PARSING — página de resultados
    # -----------------------------------------------------------------------

    def _parse_search_page(self, html: str, keyword: str) -> list:
        soup    = BeautifulSoup(html, "lxml")
        ofertas = []

        # Indeed usa data-jk como identificador canónico de cada oferta
        cards = soup.find_all(lambda tag: tag.get("data-jk"))

        # Fallback: buscar por clase jobTitle o resultado de mosaic
        if not cards:
            cards = soup.find_all("div", class_=lambda c: c and "job_seen_beacon" in (c or ""))
        if not cards:
            cards = soup.find_all("li", class_=lambda c: c and "css-" in (c or ""))

        logger.debug(f"   Cards encontradas: {len(cards)}")

        for card in cards:
            try:
                job_id = card.get("data-jk", "").strip()
                if not job_id or job_id in self.seen_ids:
                    continue
                self.seen_ids.add(job_id)

                # Título
                titulo_tag = card.find("h2", class_=lambda c: c and "jobTitle" in (c or ""))
                if titulo_tag:
                    span   = titulo_tag.find("span", attrs={"title": True})
                    titulo = span["title"] if span else titulo_tag.get_text(strip=True)
                else:
                    titulo = "N/A"

                # Empresa
                empresa_tag = (
                    card.find("span", attrs={"data-testid": "company-name"})
                    or card.find("span", class_=lambda c: c and "companyName" in (c or ""))
                )
                empresa = empresa_tag.get_text(strip=True) if empresa_tag else "N/A"

                # Ubicación
                ubicacion_tag = (
                    card.find("div",  attrs={"data-testid": "text-location"})
                    or card.find("span", attrs={"data-testid": "text-location"})
                    or card.find("div", class_=lambda c: c and "companyLocation" in (c or ""))
                )
                ubicacion = ubicacion_tag.get_text(strip=True) if ubicacion_tag else "Argentina"

                # Salario
                salario_tag = (
                    card.find("div", attrs={"data-testid": "attribute_snippet_testid"})
                    or card.find("div", class_=lambda c: c and "salary" in (c or "").lower())
                )
                salario = salario_tag.get_text(strip=True) if salario_tag else None

                # Fecha
                fecha_tag = card.find("span", class_=lambda c: c and "date" in (c or "").lower())
                fecha_pub = fecha_tag.get_text(strip=True) if fecha_tag else "N/A"

                ofertas.append(OfertaLaboral(
                    job_id=job_id, titulo=titulo, empresa=empresa,
                    ubicacion=ubicacion, descripcion="",
                    fecha_pub=fecha_pub, url=f"{DETAIL_URL}?jk={job_id}",
                    keyword=keyword, salario=salario,
                ))

            except Exception as e:
                logger.debug(f"Error parseando card: {e}")

        return ofertas

    # -----------------------------------------------------------------------
    # PARSING — página de detalle
    # -----------------------------------------------------------------------

    def _parse_detail_page(self, html: str, oferta: OfertaLaboral) -> OfertaLaboral:
        soup = BeautifulSoup(html, "lxml")

        desc = (
            soup.find("div", id="jobDescriptionText")
            or soup.find("div", attrs={"data-testid": "jobsearch-JobComponent-description"})
            or soup.find("div", class_=lambda c: c and "jobsearch-jobDescriptionText" in (c or ""))
        )
        if desc:
            oferta.descripcion = desc.get_text(separator=" ", strip=True)

        for m in soup.find_all("div", attrs={"data-testid": re.compile(r"(jobDetails|attribute)", re.I)}):
            texto = m.get_text(separator=" ", strip=True).lower()
            if any(x in texto for x in ["full-time","full time","tiempo completo","part-time","contrato"]):
                oferta.tipo_empleo = m.get_text(strip=True)
            elif any(x in texto for x in ["remoto","remote","híbrido","hybrid","presencial","home office"]):
                oferta.modalidad = m.get_text(strip=True)
            elif any(x in texto for x in ["senior","semi senior","junior","trainee","jr","sr","ssr"]):
                oferta.nivel = m.get_text(strip=True)

        if not oferta.salario:
            sal = soup.find("div", attrs={"data-testid": re.compile(r"salary", re.I)})
            if sal:
                oferta.salario = sal.get_text(strip=True)

        return oferta

    # -----------------------------------------------------------------------
    # DIAGNÓSTICO — guardar HTML crudo si no encuentra ofertas
    # -----------------------------------------------------------------------

    def _save_debug_html(self, html: str, nombre: str):
        """Guarda el HTML crudo para diagnóstico cuando el parser no encuentra resultados."""
        debug_dir = DATA_RAW_DIR / "diagnostico"
        debug_dir.mkdir(exist_ok=True)
        path = debug_dir / f"{nombre}_{datetime.now().strftime('%H%M%S')}.html"
        path.write_text(html, encoding="utf-8")
        logger.warning(f"   🔎 HTML de diagnóstico guardado: {path}")

    # -----------------------------------------------------------------------
    # CSV
    # -----------------------------------------------------------------------

    def _export_to_csv(self, ofertas: list):
        if not ofertas:
            return
        fieldnames  = list(asdict(ofertas[0]).keys())
        file_exists = CSV_FILENAME.exists()
        with open(CSV_FILENAME, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            if not file_exists:
                writer.writeheader()
            for o in ofertas:
                writer.writerow(asdict(o))
        logger.info(f"Exportadas {len(ofertas)} ofertas a CSV: {CSV_FILENAME}")

    # -----------------------------------------------------------------------
    # RUN
    # -----------------------------------------------------------------------

    def run(self) -> int:
        logger.info("=" * 60)
        logger.info("INICIO DEL SCRAPING - Indeed Argentina (curl_cffi)")
        logger.info(f"Búsquedas: {len(SEARCH_QUERIES)} | Páginas: {PAGES_PER_QUERY}")
        logger.info("=" * 60)

        total_guardadas = 0
        stats = []

        for query in SEARCH_QUERIES:
            keyword  = query["keyword"]
            location = query["location"]
            logger.info(f"\n🔍 Buscando: '{keyword}' en '{location}'")

            ofertas_query = []

            for page_num in range(PAGES_PER_QUERY):
                start = page_num * 10
                url   = self._build_search_url(keyword, location, start)
                logger.info(f"   Página {page_num + 1}/{PAGES_PER_QUERY} → {url}")

                html = self._request(url, referer=BASE_URL)
                if not html:
                    logger.warning(f"   ⚠️  Sin respuesta. Saltando página {page_num + 1}.")
                    continue

                nuevas = self._parse_search_page(html, keyword)
                logger.info(f"   → {len(nuevas)} ofertas nuevas encontradas")

                # Si no encuentra nada aun teniendo HTML, guardar para diagnóstico
                if not nuevas:
                    self._save_debug_html(html, f"search_{keyword.replace(' ','_')}_p{page_num+1}")
                    logger.info("   → Sin más resultados.")
                    break

                for oferta in nuevas:
                    time.sleep(random.uniform(DELAY_DETAIL_MIN, DELAY_DETAIL_MAX))
                    detail_html = self._request(oferta.url, referer=url)
                    if detail_html:
                        oferta = self._parse_detail_page(detail_html, oferta)
                    self._save_to_db(oferta)
                    ofertas_query.append(oferta)
                    total_guardadas += 1

            self._export_to_csv(ofertas_query)
            stats.append({"keyword": keyword, "ofertas": len(ofertas_query)})
            logger.info(f"✅ '{keyword}': {len(ofertas_query)} ofertas guardadas")

            pausa = random.uniform(DELAY_QUERY_MIN, DELAY_QUERY_MAX)
            logger.info(f"   💤 Pausa entre búsquedas: {pausa:.1f}s")
            time.sleep(pausa)

        logger.info("\n" + "=" * 60)
        logger.info("SCRAPING FINALIZADO - Indeed Argentina")
        logger.info(f"Total ofertas únicas: {total_guardadas}")
        logger.info(f"DB: {DB_FILENAME} | CSV: {CSV_FILENAME}")
        logger.info("=" * 60)

        stats_file = DATA_RAW_DIR / f"stats_indeed_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(stats_file, "w", encoding="utf-8") as f:
            json.dump({"fuente": "indeed", "fecha": datetime.now().isoformat(),
                       "total_ofertas": total_guardadas, "detalle_por_keyword": stats},
                      f, ensure_ascii=False, indent=2)

        return total_guardadas


# =============================================================================
# EJECUCIÓN
# =============================================================================

if __name__ == "__main__":
    print("\n🚀 Iniciando scraper de Indeed Argentina (curl_cffi / TLS impersonation)...")
    print("   Proyecto: Tesis MCD - Sistema de Recomendación Laboral")
    print("   Alumno: Cicconi, Carlos Alberto\n")

    scraper = IndeedScraper()
    total   = scraper.run()

    print(f"\n✅ Proceso completado. {total} ofertas recopiladas.")
    print(f"   Revisá los datos en: {DATA_RAW_DIR}")
