#!/usr/bin/env python3
# =============================================================================
# zonajobs_scraper.py
# Tesis: "Sistema de Recomendación Laboral - Motor de Matching Inteligente"
# Alumno: Cicconi, Carlos Alberto - Maestría en Explotación de Datos (Austral)
# Módulo: T2 - Diseño y Desarrollo del Web Scraper
#
# Fuente: ZonaJobs Argentina (www.zonajobs.com.ar)
# Estrategia: Playwright (Chromium headless)
#
# ARQUITECTURA CONFIRMADA POR DIAGNÓSTICO (2026-04-06):
#   ZonaJobs y Bumeran son la misma plataforma tecnológica (Jobint/Adevinta).
#   Evidencia directa encontrada en el HTML de Bumeran:
#     - window.SITE_ID = "ZJAR" (ZonaJobs AR) / "BMVE" (Bumeran)
#     - Imagen con alt="Aviso de Zonajobs" dentro de cards de Bumeran
#     - Mismas clases CSS styled-components (sc-cMfllC, sc-bQmweE, etc.)
#     - Mismo patrón de IDs: header-col-job-posting-{id}, data-col-job-posting-{id}
#     - Mismo patrón de URL: /empleos-busqueda-{keyword}.html?page={n}
#     - Mismo patrón de detalle: /empleos/{slug}-{id}.html
#
#   El scraper es estructuralmente idéntico al de Bumeran.
#   Solo difieren: BASE_URL, nombre de fuente, y archivos de salida.
# =============================================================================

import asyncio
import logging
import sqlite3
import csv
import json
import re
import random
from datetime import datetime
from bs4 import BeautifulSoup
from dataclasses import dataclass, asdict, field
from typing import Optional
from pathlib import Path

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

# =============================================================================
# CONFIGURACIÓN DEL PROYECTO
# =============================================================================

BASE_DIR     = Path("/media/carlos-a-cicconi/Common1/Repositorios-Ing.Carlos-Cicconi/Trabajo_Final_MCD")
DATA_RAW_DIR = BASE_DIR / "data" / "raw"
LOGS_DIR     = BASE_DIR / "logs"
DIAG_DIR     = DATA_RAW_DIR / "diagnostico"

DATA_RAW_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)
DIAG_DIR.mkdir(parents=True, exist_ok=True)

LOG_FILE = LOGS_DIR / f"zonajobs_scraper_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
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
# CONFIGURACIÓN
# =============================================================================

SEARCH_QUERIES = [
    {"keyword": "data-scientist",           "label": "Data Scientist"},
    {"keyword": "data-analyst",             "label": "Data Analyst"},
    {"keyword": "machine-learning",         "label": "Machine Learning Engineer"},
    {"keyword": "nlp",                      "label": "NLP Engineer"},
    {"keyword": "data-engineer",            "label": "Data Engineer"},
    {"keyword": "software-engineer",        "label": "Software Engineer"},
    {"keyword": "python",                   "label": "Python Developer"},
    {"keyword": "desarrollador-backend",    "label": "Desarrollador Backend"},
    {"keyword": "desarrollador-full-stack", "label": "Desarrollador Full Stack"},
    {"keyword": "ingeniero-de-datos",       "label": "Ingeniero de Datos"},
]

PAGES_PER_QUERY  = 5
PAGE_TIMEOUT     = 30000
WAIT_TIMEOUT     = 12000
DELAY_PAGE_MIN   = 3.0
DELAY_PAGE_MAX   = 6.0
DELAY_DETAIL_MIN = 2.0
DELAY_DETAIL_MAX = 4.5
DELAY_QUERY_MIN  = 7.0
DELAY_QUERY_MAX  = 13.0

DB_FILENAME  = DATA_RAW_DIR / "zonajobs.db"
CSV_FILENAME = DATA_RAW_DIR / f"ofertas_zonajobs_{datetime.now().strftime('%Y%m%d')}.csv"

# ─── ÚNICA DIFERENCIA CON BUMERAN ───────────────────────────────────────────
BASE_URL     = "https://www.zonajobs.com.ar"
# ────────────────────────────────────────────────────────────────────────────

# Selector CSS estable para los cards (idéntico a Bumeran — misma plataforma)
CARD_CSS_CLASS = "sc-cMfllC"
WAIT_SELECTOR  = "div.sc-cMfllC"


# =============================================================================
# DATACLASS — compatible con todos los scrapers anteriores
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
    fuente:      str = "zonajobs"
    fecha_scrap: str = field(default_factory=lambda: datetime.now().isoformat())
    nivel:       Optional[str] = None
    tipo_empleo: Optional[str] = None
    modalidad:   Optional[str] = None
    salario:     Optional[str] = None


# =============================================================================
# HELPERS
# =============================================================================

def limpiar_texto(texto: str) -> str:
    if not texto:
        return ""
    limpio = re.sub(r"[\x00-\x1f\x7f-\x9f⁣️]", " ", texto)
    return re.sub(r"\s+", " ", limpio).strip()


# =============================================================================
# CLASE PRINCIPAL
# =============================================================================

class ZonaJobsScraper:
    """
    Scraper para ZonaJobs Argentina usando Playwright.

    ZonaJobs y Bumeran comparten la misma plataforma tecnológica (Jobint).
    DOM idéntico: SPA React + styled-components, mismos IDs semánticos,
    misma estructura de cards y URLs. Confirmado por análisis del HTML
    de Bumeran que contiene referencias directas a ZonaJobs (SITE_ID="ZJAR").

    Uso: exclusivamente académico (Tesis MCD - Universidad Austral).
    """

    def __init__(self):
        self.seen_ids = set()
        self._setup_db()
        logger.info("ZonaJobsScraper (Playwright) inicializado")

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
    # PLAYWRIGHT
    # -----------------------------------------------------------------------

    async def _fetch_page(self, page, url: str,
                          wait_selector: str = None,
                          wait_extra: float = 2.5) -> Optional[str]:
        try:
            await page.goto(url, timeout=PAGE_TIMEOUT,
                            wait_until="domcontentloaded")
            if wait_selector:
                try:
                    await page.wait_for_selector(wait_selector,
                                                 timeout=WAIT_TIMEOUT)
                except PlaywrightTimeout:
                    logger.debug(f"Selector '{wait_selector}' no apareció")
            await asyncio.sleep(wait_extra + random.uniform(0.5, 1.5))
            return await page.content()
        except PlaywrightTimeout:
            logger.warning(f"Timeout: {url}")
            return None
        except Exception as e:
            logger.error(f"Error Playwright en {url}: {e}")
            return None

    # -----------------------------------------------------------------------
    # PARSING — página de resultados
    # -----------------------------------------------------------------------

    def _parse_search_page(self, html: str, keyword_label: str) -> list:
        """
        Extrae cards del HTML renderizado.
        Lógica idéntica a bumeran_scraper.py — misma plataforma tecnológica.

        Selectores confirmados:
          - Card:     div.sc-cMfllC
          - job_id:   regex en aria-labelledby="header-col-job-posting-{id}"
          - Título:   h2 dentro de id="header-col-job-posting-{id}"
          - Empresa:  h3 (después de la fecha) dentro del header-col
          - Snippet:  p dentro del header-col
          - Modalidad/Ubicación: h3 dentro de id="data-col-job-posting-{id}"
        """
        soup    = BeautifulSoup(html, "lxml")
        ofertas = []

        cards = soup.find_all("div", class_=lambda c:
                              c and CARD_CSS_CLASS in (c if isinstance(c, str)
                                                       else " ".join(c)))
        logger.debug(f"   Cards encontrados: {len(cards)}")

        for card in cards:
            try:
                # job_id y URL
                link = card.find("a", attrs={
                    "aria-labelledby": re.compile(r"job-posting-\d+")
                })
                if not link:
                    continue

                m = re.search(r"job-posting-(\d+)",
                              link.get("aria-labelledby", ""))
                if not m:
                    continue
                job_id = m.group(1)

                if job_id in self.seen_ids:
                    continue
                self.seen_ids.add(job_id)

                href = link.get("href", "")
                url  = BASE_URL + href if href.startswith("/") else href

                # Header col → título, empresa, snippet, fecha
                header    = card.find(id=f"header-col-job-posting-{job_id}")
                titulo    = "N/A"
                empresa   = "N/A"
                snippet   = ""
                fecha_pub = "N/A"

                if header:
                    h2 = header.find("h2")
                    if h2:
                        titulo = limpiar_texto(h2.get_text(strip=True))

                    for h3 in header.find_all("h3"):
                        t = h3.get_text(strip=True)
                        if "publicado" in t.lower() or "hace" in t.lower():
                            fecha_pub = limpiar_texto(t)
                        elif empresa == "N/A" and titulo != "N/A":
                            empresa = limpiar_texto(t)

                    p = header.find("p")
                    if p:
                        snippet = limpiar_texto(p.get_text(strip=True))

                # Data col → ubicación y modalidad
                data_col  = card.find(id=f"data-col-job-posting-{job_id}")
                ubicacion = "Argentina"
                modalidad = None

                if data_col:
                    for h3 in data_col.find_all("h3"):
                        t = limpiar_texto(h3.get_text(strip=True))
                        if not t:
                            continue
                        if any(x in t.lower() for x in
                               ["remoto", "híbrido", "hibrido",
                                "presencial", "home office"]):
                            modalidad = t
                        else:
                            ubicacion = t

                ofertas.append(OfertaLaboral(
                    job_id      = job_id,
                    titulo      = titulo,
                    empresa     = empresa,
                    ubicacion   = ubicacion,
                    descripcion = snippet,
                    fecha_pub   = fecha_pub,
                    url         = url,
                    keyword     = keyword_label,
                    modalidad   = modalidad,
                ))

            except Exception as e:
                logger.debug(f"   Error en card: {e}")

        # Si no se encontraron cards, guardar HTML para diagnóstico
        if not ofertas:
            diag_file = DIAG_DIR / f"zonajobs_rendered_{keyword_label.replace(' ','_')}.html"
            diag_file.write_text(html, encoding="utf-8")
            logger.warning(f"   ⚠️  Sin ofertas. HTML guardado: {diag_file.name}")

        return ofertas

    # -----------------------------------------------------------------------
    # PARSING — página de detalle
    # -----------------------------------------------------------------------

    def _parse_detail(self, html: str, oferta: OfertaLaboral) -> OfertaLaboral:
        """Obtiene descripción completa y metadatos de la página de detalle."""
        soup = BeautifulSoup(html, "lxml")

        header = soup.find(id=f"header-col-job-posting-{oferta.job_id}")
        if header:
            p = header.find("p")
            if p:
                desc = limpiar_texto(p.get_text(strip=True))
                if len(desc) > len(oferta.descripcion):
                    oferta.descripcion = desc

        data_col = soup.find(id=f"data-col-job-posting-{oferta.job_id}")
        if data_col:
            for h3 in data_col.find_all("h3"):
                t = limpiar_texto(h3.get_text(strip=True)).lower()
                if not t:
                    continue
                if any(x in t for x in ["full time", "part time",
                                          "tiempo completo", "freelance"]):
                    oferta.tipo_empleo = t.title()
                elif any(x in t for x in ["senior", "semi", "junior",
                                           "trainee", "jr", "sr"]):
                    oferta.nivel = t.title()
                elif any(x in t for x in ["remoto", "híbrido", "presencial"]):
                    if not oferta.modalidad:
                        oferta.modalidad = t.title()

        return oferta

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
        logger.info(f"Exportadas {len(ofertas)} ofertas → {CSV_FILENAME}")

    # -----------------------------------------------------------------------
    # NÚCLEO ASINCRÓNICO
    # -----------------------------------------------------------------------

    async def _run_async(self) -> int:
        total_guardadas = 0
        stats = []

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                    "--window-size=1366,768",
                ]
            )
            context = await browser.new_context(
                viewport={"width": 1366, "height": 768},
                user_agent=(
                    "Mozilla/5.0 (X11; Linux x86_64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/122.0.0.0 Safari/537.36"
                ),
                locale="es-AR",
                timezone_id="America/Argentina/Buenos_Aires",
                extra_http_headers={
                    "Accept-Language": "es-AR,es;q=0.9,en-US;q=0.8",
                }
            )
            await context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver',
                                      { get: () => undefined });
                Object.defineProperty(navigator, 'plugins',
                                      { get: () => [1,2,3,4,5] });
                Object.defineProperty(navigator, 'languages',
                                      { get: () => ['es-AR','es','en'] });
                window.chrome = { runtime: {} };
            """)

            page = await context.new_page()

            # Warmup con la home de ZonaJobs
            logger.info("Inicializando sesión en ZonaJobs...")
            await self._fetch_page(page, BASE_URL,
                                   wait_selector=WAIT_SELECTOR,
                                   wait_extra=3.0)

            for query in SEARCH_QUERIES:
                keyword = query["keyword"]
                label   = query["label"]
                logger.info(f"\n🔍 Buscando: '{label}'")
                ofertas_query = []

                for page_num in range(1, PAGES_PER_QUERY + 1):
                    if page_num == 1:
                        url = f"{BASE_URL}/empleos-busqueda-{keyword}.html"
                    else:
                        url = f"{BASE_URL}/empleos-busqueda-{keyword}.html?page={page_num}"

                    logger.info(f"   Página {page_num}/{PAGES_PER_QUERY} → {url}")
                    await asyncio.sleep(random.uniform(DELAY_PAGE_MIN,
                                                       DELAY_PAGE_MAX))

                    html = await self._fetch_page(
                        page, url,
                        wait_selector=WAIT_SELECTOR,
                        wait_extra=3.0
                    )
                    if not html:
                        logger.warning("   ⚠️  Sin contenido. Saltando.")
                        continue

                    nuevas = self._parse_search_page(html, label)
                    logger.info(f"   → {len(nuevas)} ofertas encontradas")

                    if not nuevas:
                        logger.info("   → Sin más resultados.")
                        break

                    for oferta in nuevas:
                        await asyncio.sleep(random.uniform(DELAY_DETAIL_MIN,
                                                           DELAY_DETAIL_MAX))
                        detail_html = await self._fetch_page(
                            page, oferta.url,
                            wait_selector=WAIT_SELECTOR,
                            wait_extra=2.5
                        )
                        if detail_html:
                            oferta = self._parse_detail(detail_html, oferta)

                        self._save_to_db(oferta)
                        ofertas_query.append(oferta)
                        total_guardadas += 1

                self._export_to_csv(ofertas_query)
                stats.append({"keyword": label, "ofertas": len(ofertas_query)})
                logger.info(f"✅ '{label}': {len(ofertas_query)} ofertas guardadas")

                pausa = random.uniform(DELAY_QUERY_MIN, DELAY_QUERY_MAX)
                logger.info(f"   💤 Pausa: {pausa:.1f}s")
                await asyncio.sleep(pausa)

            await browser.close()

        logger.info("\n" + "=" * 60)
        logger.info("SCRAPING FINALIZADO - ZonaJobs Argentina")
        logger.info(f"Total ofertas únicas: {total_guardadas}")
        logger.info(f"DB : {DB_FILENAME}")
        logger.info(f"CSV: {CSV_FILENAME}")
        logger.info(f"Log: {LOG_FILE}")
        logger.info("=" * 60)

        stats_file = DATA_RAW_DIR / f"stats_zonajobs_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(stats_file, "w", encoding="utf-8") as f:
            json.dump({
                "fuente":              "zonajobs",
                "nota":                "Misma plataforma que Bumeran (Jobint/Adevinta)",
                "fecha":               datetime.now().isoformat(),
                "total_ofertas":       total_guardadas,
                "detalle_por_keyword": stats,
            }, f, ensure_ascii=False, indent=2)

        return total_guardadas

    def run(self) -> int:
        return asyncio.run(self._run_async())


# =============================================================================
# EJECUCIÓN
# =============================================================================

if __name__ == "__main__":
    print("\n🚀 Iniciando scraper de ZonaJobs Argentina (Playwright)...")
    print("   Proyecto: Tesis MCD - Sistema de Recomendación Laboral")
    print("   Alumno: Cicconi, Carlos Alberto")
    print("   ℹ️  Misma plataforma que Bumeran (Jobint) — DOM idéntico\n")

    scraper = ZonaJobsScraper()
    total   = scraper.run()

    print(f"\n✅ Proceso completado. {total} ofertas recopiladas.")
    print(f"   Revisá los datos en: {DATA_RAW_DIR}")
