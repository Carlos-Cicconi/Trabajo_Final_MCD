#!/usr/bin/env python3
# =============================================================================
# workana_scraper.py
# Tesis: "Sistema de Recomendación Laboral - Motor de Matching Inteligente"
# Alumno: Cicconi, Carlos Alberto - Maestría en Explotación de Datos (Austral)
# Módulo: T2 - Diseño y Desarrollo del Web Scraper
#
# Fuente: Workana (www.workana.com)
# Estrategia: Playwright (Chromium headless) + parser HTML clásico
#
# ARQUITECTURA CONFIRMADA POR DIAGNÓSTICO (2026-04-07):
#   Workana usa HTML clásico renderizado del lado del servidor con
#   clases CSS semánticas estables. No es una SPA pura — el contenido
#   existe en el DOM tras el renderizado de Playwright.
#
#   ESTRUCTURA DEL CARD (confirmada):
#   <div class="project-item js-project [project-item-featured]">
#     <div class="project-header">
#       <p class="date visible-xs"><strong>Hace N horas</strong></p>
#       <h2 class="h3 project-title">
#         <a href="/job/{slug}?ref=...">
#           <span title="{titulo_completo}">{titulo_truncado}</span>
#
#     <div class="project-body">
#       <div class="project-main-details">
#         <span class="date">Publicado: Hace N horas</span>
#         <span class="bids">Propuestas: N</span>
#       <div class="html-desc project-details">
#         <p class="text-expander-content">
#           <span>{descripcion_snippet}</span>
#       <div class="skills">
#         <a class="skill label label-info" ...><h3>{skill}</h3></a>
#
#     <div class="project-author hidden-xs">
#       <img alt="Freelancer X. Y." ...>   ← cliente (anónimo)
#       <span class="country-name">Argentina</span>
#
#     <div class="project-actions floating">
#       <p class="budget h4">
#         <span class="values"><span>USD N - M</span>   ← presupuesto
#
#   job_id: slug extraído del href (/job/{slug}), quitando ?ref=...
#   URL detalle: https://www.workana.com/job/{slug}
#
#   URL de búsqueda:
#     https://www.workana.com/jobs?category=it-programming
#                                 &language=es&country=AR
#                                 &search={keyword}&page={n}
#
#   El HTML de búsqueda NO tiene __NEXT_DATA__ (solo la home lo tiene).
#   Playwright es necesario para ejecutar el JS que renderiza los cards.
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
from urllib.parse import urlencode

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

LOG_FILE = LOGS_DIR / f"workana_scraper_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
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

# Workana organiza por categoría + búsqueda libre.
# URL base de búsqueda confirmada:
#   /jobs?category=it-programming&language=es&country=AR&search={q}&page={n}
SEARCH_QUERIES = [
    {"search": "data scientist",     "label": "Data Scientist"},
    {"search": "data analyst",       "label": "Data Analyst"},
    {"search": "machine learning",   "label": "Machine Learning Engineer"},
    {"search": "nlp",                "label": "NLP Engineer"},
    {"search": "data engineer",      "label": "Data Engineer"},
    {"search": "python",             "label": "Python Developer"},
    {"search": "backend",            "label": "Desarrollador Backend"},
    {"search": "full stack",         "label": "Desarrollador Full Stack"},
    {"search": "software engineer",  "label": "Software Engineer"},
    {"search": "ingeniero de datos", "label": "Ingeniero de Datos"},
]

PAGES_PER_QUERY  = 5
PAGE_TIMEOUT     = 30000
WAIT_TIMEOUT     = 12000
DELAY_PAGE_MIN   = 3.0
DELAY_PAGE_MAX   = 5.5
DELAY_DETAIL_MIN = 2.0
DELAY_DETAIL_MAX = 4.0
DELAY_QUERY_MIN  = 6.0
DELAY_QUERY_MAX  = 11.0

DB_FILENAME  = DATA_RAW_DIR / "ofertas_workana.db"
CSV_FILENAME = DATA_RAW_DIR / f"workana_{datetime.now().strftime('%Y%m%d')}.csv"
BASE_URL     = "https://www.workana.com"
SEARCH_URL   = f"{BASE_URL}/jobs"

# Selector CSS estable que confirma que los cards ya están en el DOM
WAIT_SELECTOR = "div.project-item"


# =============================================================================
# DATACLASS — compatible con todos los scrapers anteriores
# =============================================================================

@dataclass
class OfertaLaboral:
    job_id:      str
    titulo:      str
    empresa:     str           # En Workana = cliente (anónimo)
    ubicacion:   str
    descripcion: str
    fecha_pub:   str
    url:         str
    keyword:     str
    fuente:      str = "workana"
    fecha_scrap: str = field(default_factory=lambda: datetime.now().isoformat())
    nivel:       Optional[str] = None
    tipo_empleo: Optional[str] = None   # skills del proyecto
    modalidad:   Optional[str] = "Remoto"
    salario:     Optional[str] = None   # presupuesto del proyecto


# =============================================================================
# HELPERS
# =============================================================================

def limpiar_texto(texto: str) -> str:
    if not texto:
        return ""
    limpio = re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", texto)
    return re.sub(r"\s+", " ", limpio).strip()


# =============================================================================
# CLASE PRINCIPAL
# =============================================================================

class WorkanaScraper:
    """
    Scraper para Workana usando Playwright.
    Workana renderiza los cards con JS — Playwright ejecuta el JS y expone
    el DOM con clases CSS semánticas estables (project-item, project-title,
    project-body, etc.).
    Estructura confirmada por diagnóstico del HTML renderizado (2026-04-07).
    Uso: exclusivamente académico (Tesis MCD - Universidad Austral).
    """

    def __init__(self):
        self.seen_ids = set()
        self._setup_db()
        logger.info("WorkanaScraper (Playwright) inicializado")

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
            logger.error(f"Error Playwright: {e}")
            return None

    # -----------------------------------------------------------------------
    # PARSING — página de resultados
    # -----------------------------------------------------------------------

    def _parse_search_page(self, html: str, keyword_label: str) -> list:
        """
        Parsea la página de resultados de Workana ya renderizada por Playwright.

        Selectores confirmados por diagnóstico:
          Card:        div.project-item  (también puede tener .project-item-featured)
          Título:      h2.project-title > span > a > span[title]
          URL/slug:    h2.project-title > span > a[href="/job/{slug}"]
          Fecha:       span.date  (dentro de project-main-details)
          Descripción: div.html-desc.project-details > div > p > span
          Skills:      a.skill  (dentro de div.skills)
          Cliente:     img[alt] dentro de div.project-author
          País:        span.country-name
          Presupuesto: p.budget > span.values > span
        """
        soup    = BeautifulSoup(html, "lxml")
        ofertas = []

        cards = soup.find_all("div", class_=re.compile(r"\bproject-item\b"))
        logger.debug(f"   Cards encontrados: {len(cards)}")

        for card in cards:
            try:
                # --- URL y job_id (slug) ---
                link = card.find("a", href=re.compile(r"^/job/"))
                if not link:
                    continue
                href = link.get("href", "")
                # Quitar parámetros de tracking (?ref=...)
                slug   = re.sub(r'\?.*$', '', href).split("/job/")[-1]
                job_id = slug

                if job_id in self.seen_ids:
                    continue
                self.seen_ids.add(job_id)

                url = f"{BASE_URL}/job/{slug}"

                # --- Título completo (en el atributo title del span) ---
                span_title = link.find("span", title=True)
                titulo = limpiar_texto(
                    span_title["title"] if span_title
                    else link.get_text(strip=True)
                )

                # --- Fecha de publicación ---
                details_div = card.find("div", class_="project-main-details")
                fecha_pub   = "N/A"
                if details_div:
                    fecha_span = details_div.find("span", class_="date")
                    if fecha_span:
                        fecha_pub = limpiar_texto(
                            fecha_span.get_text(strip=True)
                            .replace("Publicado:", "").strip()
                        )

                # --- Descripción snippet ---
                desc_div = card.find("div", class_="html-desc project-details")
                snippet  = ""
                if desc_div:
                    p    = desc_div.find("p", class_="text-expander-content")
                    span = p.find("span") if p else None
                    if span:
                        snippet = limpiar_texto(span.get_text(strip=True))

                # --- Skills (se usan como tipo_empleo — descripción técnica) ---
                skills = [
                    limpiar_texto(s.get_text(strip=True))
                    for s in card.find_all("a", class_="skill")
                ]
                tipo_empleo = ", ".join(skills[:6]) if skills else None

                # --- Cliente (anónimo en Workana para no registrados) ---
                author_div = card.find("div", class_="project-author")
                empresa    = "Cliente anónimo"
                ubicacion  = "Argentina"
                if author_div:
                    img = author_div.find("img", class_="media-object")
                    if img and img.get("alt"):
                        empresa = limpiar_texto(img["alt"])
                    country = author_div.find("span", class_="country-name")
                    if country:
                        ubicacion = limpiar_texto(country.get_text(strip=True))

                # --- Presupuesto del proyecto ---
                budget_p = card.find("p", class_=re.compile(r"\bbudget\b"))
                salario  = None
                if budget_p:
                    values = budget_p.find("span", class_="values")
                    if values:
                        inner = values.find("span")
                        if inner:
                            salario = limpiar_texto(inner.get_text(strip=True))

                ofertas.append(OfertaLaboral(
                    job_id      = job_id,
                    titulo      = titulo,
                    empresa     = empresa,
                    ubicacion   = ubicacion,
                    descripcion = snippet,
                    fecha_pub   = fecha_pub,
                    url         = url,
                    keyword     = keyword_label,
                    tipo_empleo = tipo_empleo,
                    salario     = salario,
                ))

            except Exception as e:
                logger.debug(f"   Error en card: {e}")

        if not ofertas:
            logger.warning(f"   ⚠️  Sin ofertas parseadas.")

        return ofertas

    # -----------------------------------------------------------------------
    # PARSING — página de detalle (descripción completa)
    # -----------------------------------------------------------------------

    def _parse_detail(self, html: str, oferta: OfertaLaboral) -> OfertaLaboral:
        """
        Extrae la descripción completa desde la página de detalle.
        En Workana el detalle tiene el texto completo sin truncar.
        Selectores en detalle:
          div.project-description  o  div.html-desc
        """
        soup = BeautifulSoup(html, "lxml")

        # Descripción completa
        desc = (
            soup.find("div", class_="project-description")
            or soup.find("div", class_=re.compile(r"html-desc"))
            or soup.find("div", class_="description")
        )
        if desc:
            texto = limpiar_texto(desc.get_text(separator=" ", strip=True))
            if len(texto) > len(oferta.descripcion):
                oferta.descripcion = texto

        # Presupuesto más detallado si no se obtuvo en el listado
        if not oferta.salario:
            budget = soup.find("p", class_=re.compile(r"\bbudget\b"))
            if budget:
                values = budget.find("span", class_="values")
                if values:
                    inner = values.find("span")
                    if inner:
                        oferta.salario = limpiar_texto(inner.get_text(strip=True))

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

            # Warmup
            logger.info("Inicializando sesión en Workana...")
            await self._fetch_page(page, BASE_URL,
                                   wait_selector=WAIT_SELECTOR,
                                   wait_extra=3.0)

            for query in SEARCH_QUERIES:
                keyword = query["search"]
                label   = query["label"]
                logger.info(f"\n🔍 Buscando: '{label}'")
                ofertas_query = []

                for page_num in range(1, PAGES_PER_QUERY + 1):
                    params = {
                        "category": "it-programming",
                        "language": "es",
                        "country":  "AR",
                        "search":   keyword,
                        "page":     page_num,
                    }
                    url = f"{SEARCH_URL}?{urlencode(params)}"
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
                    logger.info(f"   → {len(nuevas)} ofertas nuevas encontradas")

                    if not nuevas:
                        logger.info("   → Sin más resultados.")
                        break

                    for oferta in nuevas:
                        # Enriquecer con descripción completa desde detalle
                        await asyncio.sleep(random.uniform(DELAY_DETAIL_MIN,
                                                           DELAY_DETAIL_MAX))
                        detail_html = await self._fetch_page(
                            page, oferta.url,
                            wait_selector="div.project-description, div.html-desc",
                            wait_extra=2.0
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
        logger.info("SCRAPING FINALIZADO - Workana")
        logger.info(f"Total ofertas únicas: {total_guardadas}")
        logger.info(f"DB : {DB_FILENAME}")
        logger.info(f"CSV: {CSV_FILENAME}")
        logger.info(f"Log: {LOG_FILE}")
        logger.info("=" * 60)

        stats_file = DATA_RAW_DIR / f"stats_workana_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(stats_file, "w", encoding="utf-8") as f:
            json.dump({
                "fuente":              "workana",
                "estrategia":          "playwright + html-clasico",
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
    print("\n🚀 Iniciando scraper de Workana (Playwright + HTML clásico)...")
    print("   Proyecto: Tesis MCD - Sistema de Recomendación Laboral")
    print("   Alumno: Cicconi, Carlos Alberto")
    print("   ℹ️  Selectores confirmados por diagnóstico del DOM renderizado\n")

    scraper = WorkanaScraper()
    total   = scraper.run()

    print(f"\n✅ Proceso completado. {total} ofertas recopiladas.")
    print(f"   Revisá los datos en: {DATA_RAW_DIR}")
