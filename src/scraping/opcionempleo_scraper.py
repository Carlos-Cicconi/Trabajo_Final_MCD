#!/usr/bin/env python3
# =============================================================================
# opcionempleo_scraper.py
# Tesis: "Sistema de Recomendación Laboral - Motor de Matching Inteligente"
# Alumno: Cicconi, Carlos Alberto - Maestría en Explotación de Datos (Austral)
# Módulo: T2 - Diseño y Desarrollo del Web Scraper
#
# Fuente: OpcionEmpleo Argentina (www.opcionempleo.com.ar)
# Grupo:  Jobijoba Group (Francia)
#
# PROTECCIÓN DETECTADA (diagnóstico 2026-04-08, reconfirmado 2026-09-05):
#   Cloudflare Turnstile — "managed challenge" invisible.
#   requests/urllib devuelven la página de challenge sin contenido.
#   Chromium headless TAMPOCO la resuelve (fingerprint de GPU/display
#   insuficiente para pasar el score de Turnstile) — devuelve un shell
#   vacío sin lanzar error, lo que hace fallar el scraping en silencio.
#   SOLUCIÓN: Playwright con headless=False (requiere sesión gráfica X
#   en la máquina que ejecuta el scraper) sí resuelve el challenge.
#
# ESTRUCTURA DEL DOM (inferida del grupo Jobijoba — misma plataforma
# que otras versiones regionales como opcionempleo.com.mx):
#   Cards: <article class="job"> o <div class="job-item">
#   Título: h2 o h3 dentro del card
#   URL:    <a href="/oferta-{id}-{slug}.html">
#
# URL de búsqueda: /trabajo-{keyword}.html?p={pagina}
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

for d in [DATA_RAW_DIR, LOGS_DIR, DIAG_DIR]:
    d.mkdir(parents=True, exist_ok=True)

LOG_FILE = LOGS_DIR / f"opcionempleo_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
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
PAGE_TIMEOUT     = 45000   # Más tiempo para que resuelva el Turnstile
WAIT_TIMEOUT     = 20000   # Esperar hasta 20s al challenge
DELAY_PAGE_MIN   = 4.0
DELAY_PAGE_MAX   = 7.0
DELAY_DETAIL_MIN = 3.0
DELAY_DETAIL_MAX = 5.5
DELAY_QUERY_MIN  = 8.0
DELAY_QUERY_MAX  = 14.0

DB_FILENAME  = DATA_RAW_DIR / "opcionempleo.db"
CSV_FILENAME = DATA_RAW_DIR / f"ofertas_opcionempleo_{datetime.now().strftime('%Y%m%d')}.csv"
BASE_URL     = "https://www.opcionempleo.com.ar"

# Selector que confirma que el Turnstile fue resuelto y hay contenido real
# (cuando hay challenge, solo hay .cf-turnstile; cuando hay contenido, hay cards)
WAIT_SELECTOR_CONTENT = "article, .job, .job-item, .offer, h2.title, [class*='job-card']"
WAIT_SELECTOR_CHALLENGE = ".cf-turnstile"


# =============================================================================
# DATACLASS
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
    fuente:      str = "opcionempleo"
    fecha_scrap: str = field(default_factory=lambda: datetime.now().isoformat())
    nivel:       Optional[str] = None
    tipo_empleo: Optional[str] = None
    modalidad:   Optional[str] = None
    salario:     Optional[str] = None


# =============================================================================
# HELPERS
# =============================================================================

def limpiar(t: str) -> str:
    if not t: return ""
    return re.sub(r"\s+", " ", re.sub(r"[\x00-\x1f]", " ", t)).strip()


# =============================================================================
# CLASE PRINCIPAL
# =============================================================================

class OpcionempleoScraper:
    """
    Scraper para OpcionEmpleo Argentina usando Playwright.
    OpcionEmpleo está protegido por Cloudflare Turnstile — un challenge
    interactivo JS que bloquea requests/urllib. Playwright ejecuta el
    challenge automáticamente en el navegador headless.
    Uso: exclusivamente académico (Tesis MCD - Universidad Austral).
    """

    def __init__(self):
        self.seen_ids = set()
        self._setup_db()
        logger.info("OpcionempleoScraper (Playwright) inicializado")

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
    # PLAYWRIGHT — navegación con manejo de Turnstile
    # -----------------------------------------------------------------------

    async def _fetch_page(self, page, url: str,
                          wait_extra: float = 3.0) -> Optional[str]:
        """
        Navega a una URL y espera a que el Cloudflare Turnstile se resuelva.
        Turnstile es un challenge JS que Playwright puede resolver automáticamente
        porque ejecuta el navegador real con su motor JS completo.
        """
        try:
            await page.goto(url, timeout=PAGE_TIMEOUT,
                            wait_until="domcontentloaded")

            # Detectar si hay Turnstile challenge
            await asyncio.sleep(2.0)
            html_inicial = await page.content()

            if "cf-turnstile" in html_inicial or "Cloudflare" in html_inicial:
                logger.info(f"   ⏳ Turnstile detectado — esperando resolución automática...")
                # Esperar a que desaparezca el challenge y aparezca contenido real
                try:
                    await page.wait_for_function(
                        "() => !document.querySelector('.cf-turnstile') || "
                        "document.querySelector('article, .job, .job-item, h2, .offer')",
                        timeout=WAIT_TIMEOUT
                    )
                    logger.info("   ✅ Turnstile resuelto")
                except PlaywrightTimeout:
                    logger.warning("   ⚠️  Turnstile no se resolvió a tiempo — continuando igual")

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

    def _parse_search_page(self, html: str, keyword_label: str,
                           page_num: int) -> list:
        """
        Parsea la página de resultados de OpcionEmpleo.
        OpcionEmpleo (Jobijoba Group) usa HTML semántico con clases estables.
        Selectores adaptativos en cascada — guardamos diagnóstico si falla.
        """
        soup    = BeautifulSoup(html, "lxml")
        ofertas = []

        # Verificar que no sea el challenge de Cloudflare
        if "cf-turnstile" in html and len(html) < 10000:
            logger.warning("   ⚠️  Aún mostrando challenge de Cloudflare")
            (DIAG_DIR / f"opcionempleo_challenge_p{page_num}.html"
             ).write_text(html, encoding="utf-8")
            return []

        # Buscar cards de oferta — OpcionEmpleo/Jobijoba usa varias estructuras
        cards = (
            soup.find_all("article") or
            soup.find_all("div", class_=re.compile(
                r"\bjob\b|\bjob-item\b|\boffer\b|\bcard\b|\blisting\b", re.I)) or
            soup.find_all("li", class_=re.compile(
                r"\bjob\b|\boffer\b|\bitem\b", re.I))
        )

        # Diagnóstico en primera página si no hay cards
        if not cards and page_num == 1:
            (DIAG_DIR / f"opcionempleo_{keyword_label.replace(' ','_')}_rendered.html"
             ).write_text(html, encoding="utf-8")
            logger.warning("   ⚠️  Sin cards — HTML renderizado guardado en diagnostico/")
            # Mostrar estructura para diagnóstico
            title = soup.find("title")
            logger.info(f"   <title>: {title.text.strip()[:80] if title else 'N/A'}")
            body_text = soup.get_text(separator=" ", strip=True)
            logger.info(f"   Body (300 chars): {body_text[:300]}")
            return []

        logger.debug(f"   Cards encontrados: {len(cards)}")

        for card in cards:
            try:
                # Buscar link de oferta
                link = (
                    card.find("a", href=re.compile(
                        r"oferta|empleo|trabajo|job|offerj", re.I)) or
                    card.find("a", href=re.compile(r"\d{4,}"))
                )
                if not link:
                    link = card.find("h2") and card.find("h2").find("a")
                if not link:
                    link = card.find("a", href=True)
                if not link:
                    continue

                href   = link.get("href", "")
                url    = href if href.startswith("http") else BASE_URL + href
                m      = re.search(r"(\d{5,})", href)
                job_id = m.group(1) if m else re.sub(r"[^a-z0-9-]","",href)[-40:]
                if not job_id or job_id in self.seen_ids:
                    continue
                self.seen_ids.add(job_id)

                # Título
                titulo_tag = (
                    card.find("h2") or card.find("h3") or
                    card.find(class_=re.compile(r"title|titulo", re.I)) or
                    link
                )
                titulo = limpiar(titulo_tag.get_text()) if titulo_tag else "N/A"

                # Empresa
                empresa_tag = card.find(class_=re.compile(
                    r"company|empresa|employer|brand|recruiter", re.I))
                empresa = limpiar(empresa_tag.get_text()) if empresa_tag else "N/A"

                # Ubicación
                ubicacion_tag = card.find(class_=re.compile(
                    r"location|ubicacion|city|lugar|place|province", re.I))
                ubicacion = limpiar(
                    ubicacion_tag.get_text()) if ubicacion_tag else "Argentina"

                # Fecha
                fecha_tag = card.find(class_=re.compile(
                    r"date|fecha|time|ago|posted|when|publi", re.I))
                fecha_pub = limpiar(fecha_tag.get_text()) if fecha_tag else "N/A"

                # Salario (Jobijoba a veces lo muestra)
                salario_tag = card.find(class_=re.compile(
                    r"salary|salario|wage|remunera", re.I))
                salario = limpiar(salario_tag.get_text()) if salario_tag else None

                ofertas.append(OfertaLaboral(
                    job_id=job_id, titulo=titulo, empresa=empresa,
                    ubicacion=ubicacion, descripcion="",
                    fecha_pub=fecha_pub, url=url, keyword=keyword_label,
                    salario=salario,
                ))

            except Exception as e:
                logger.debug(f"   Card error: {e}")

        return ofertas

    # -----------------------------------------------------------------------
    # PARSING — página de detalle
    # -----------------------------------------------------------------------

    def _parse_detail(self, html: str, oferta: OfertaLaboral) -> OfertaLaboral:
        """Extrae descripción completa de la página de detalle."""
        if "cf-turnstile" in html and len(html) < 10000:
            return oferta  # Aún en challenge, no procesar

        soup = BeautifulSoup(html, "lxml")
        for sel in [
            {"class": re.compile(r"description|descripcion|detail|body|content|offer-desc", re.I)},
            {"id":    re.compile(r"description|detail|job-body|content", re.I)},
        ]:
            div = soup.find(["div", "section", "article"], attrs=sel)
            if div:
                t = limpiar(div.get_text(separator=" "))
                if len(t) > len(oferta.descripcion):
                    oferta.descripcion = t
                break
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
            # headless=True es detectado por el Turnstile "managed challenge"
            # de Cloudflare (falta fingerprint real de GPU/display) y nunca
            # obtiene la cookie cf_clearance — confirmado empíricamente
            # 2026-09-05: headless devuelve el shell vacío (~12KB), headful
            # resuelve el challenge y entrega el HTML completo con ofertas.
            browser = await pw.chromium.launch(
                headless=False,
                args=[
                    "--no-sandbox",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                    "--window-size=1366,768",
                ]
            )
            # Sin user_agent explícito: un UA fijo (ej. "Chrome/122") queda
            # desincronizado de la versión real del Chromium instalado y ese
            # desajuste es detectado por el Turnstile "managed challenge" de
            # Cloudflare — confirmado empíricamente 2026-09-05 (con UA
            # hardcodeado el challenge nunca se resuelve; sin él, sí).
            # Tampoco se agrega add_init_script con overrides de navigator.*:
            # esas propiedades sobreescritas (ej. webdriver -> undefined en
            # vez de false) son en sí mismas una señal de automatización.
            context = await browser.new_context(
                viewport={"width": 1366, "height": 768},
                locale="es-AR",
                timezone_id="America/Argentina/Buenos_Aires",
                extra_http_headers={
                    "Accept-Language": "es-AR,es;q=0.9,en-US;q=0.8",
                }
            )

            page = await context.new_page()

            # Warmup — visitar la home para obtener cookies y pasar el Turnstile inicial
            logger.info("Inicializando sesión en OpcionEmpleo...")
            logger.info("(Puede tardar hasta 20s mientras se resuelve el Turnstile)")
            await self._fetch_page(page, BASE_URL, wait_extra=4.0)

            for query in SEARCH_QUERIES:
                keyword = query["keyword"]
                label   = query["label"]
                logger.info(f"\n🔍 Buscando: '{label}'")
                ofertas_query = []

                for page_num in range(1, PAGES_PER_QUERY + 1):
                    # URL de búsqueda de OpcionEmpleo
                    if page_num == 1:
                        url = f"{BASE_URL}/trabajo-{keyword}.html"
                    else:
                        url = f"{BASE_URL}/trabajo-{keyword}.html?p={page_num}"

                    logger.info(f"   Página {page_num}/{PAGES_PER_QUERY} → {url}")
                    await asyncio.sleep(random.uniform(DELAY_PAGE_MIN,
                                                       DELAY_PAGE_MAX))

                    html = await self._fetch_page(page, url, wait_extra=3.0)
                    if not html:
                        logger.warning("   ⚠️  Sin contenido. Saltando.")
                        continue

                    nuevas = self._parse_search_page(html, label, page_num)
                    logger.info(f"   → {len(nuevas)} ofertas encontradas")

                    if not nuevas:
                        logger.info("   → Sin más resultados.")
                        break

                    for oferta in nuevas:
                        await asyncio.sleep(random.uniform(DELAY_DETAIL_MIN,
                                                           DELAY_DETAIL_MAX))
                        detail_html = await self._fetch_page(
                            page, oferta.url, wait_extra=2.5)
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
        logger.info("SCRAPING FINALIZADO - OpcionEmpleo Argentina")
        logger.info(f"Total ofertas únicas: {total_guardadas}")
        logger.info(f"DB : {DB_FILENAME}")
        logger.info(f"CSV: {CSV_FILENAME}")
        logger.info(f"Log: {LOG_FILE}")
        logger.info("=" * 60)

        stats_file = DATA_RAW_DIR / f"stats_opcionempleo_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(stats_file, "w", encoding="utf-8") as f:
            json.dump({
                "fuente":              "opcionempleo",
                "estrategia":          "playwright + turnstile",
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
    print("\n🚀 Iniciando scraper de OpcionEmpleo Argentina (Playwright)...")
    print("   Proyecto: Tesis MCD - Sistema de Recomendación Laboral")
    print("   Alumno: Cicconi, Carlos Alberto")
    print("   ℹ️  Cloudflare Turnstile → requiere Chromium headful (necesita sesión gráfica X)\n")
    print("   ⏳ El primer arranque puede tardar ~20s (resolución del Turnstile)\n")

    scraper = OpcionempleoScraper()
    total   = scraper.run()

    print(f"\n✅ Proceso completado. {total} ofertas recopiladas.")
    print(f"   Revisá los datos en: {DATA_RAW_DIR}")
