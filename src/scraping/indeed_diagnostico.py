#!/usr/bin/env python3
# =============================================================================
# indeed_diagnostico.py
# Guarda el HTML crudo de Indeed para inspeccionar la estructura real
# y ajustar los selectores del scraper principal.
# =============================================================================

import asyncio
import logging
from pathlib import Path
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

BASE_DIR     = Path("/media/carlos-a-cicconi/Common1/Repositorios-Ing.Carlos-Cicconi/Trabajo_Final_MCD")
DIAG_DIR     = BASE_DIR / "data" / "raw" / "diagnostico"
DIAG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

TEST_URL = "https://ar.indeed.com/jobs?q=Data+Scientist&l=Argentina&start=0&sort=date&fromage=30"

async def diagnostico():
    async with async_playwright() as pw:

        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled",
                  "--disable-dev-shm-usage", "--window-size=1366,768"]
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
            extra_http_headers={"Accept-Language": "es-AR,es;q=0.9,en-US;q=0.8"}
        )
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver',  { get: () => undefined });
            Object.defineProperty(navigator, 'plugins',    { get: () => [1,2,3,4,5] });
            Object.defineProperty(navigator, 'languages',  { get: () => ['es-AR','es','en'] });
            window.chrome = { runtime: {} };
        """)

        page = await context.new_page()

        # --- 1. Warmup en la home ---
        logger.info("Warmup en la home de Indeed...")
        await page.goto("https://ar.indeed.com", timeout=30000, wait_until="domcontentloaded")
        await asyncio.sleep(3)

        # Guardar HTML de la home
        home_html = await page.content()
        (DIAG_DIR / "home.html").write_text(home_html, encoding="utf-8")
        logger.info(f"Home guardada ({len(home_html)} chars) → {DIAG_DIR}/home.html")

        # --- 2. Navegar a búsqueda ---
        logger.info(f"Navegando a búsqueda: {TEST_URL}")
        await page.goto(TEST_URL, timeout=30000, wait_until="domcontentloaded")
        await asyncio.sleep(5)   # esperar renderizado JS

        search_html = await page.content()
        (DIAG_DIR / "search_result.html").write_text(search_html, encoding="utf-8")
        logger.info(f"Búsqueda guardada ({len(search_html)} chars) → {DIAG_DIR}/search_result.html")

        # --- 3. Captura de pantalla (ver qué muestra visualmente) ---
        await page.screenshot(path=str(DIAG_DIR / "screenshot_search.png"), full_page=True)
        logger.info(f"Screenshot guardado → {DIAG_DIR}/screenshot_search.png")

        # --- 4. Análisis rápido del HTML ---
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(search_html, "lxml")

        logger.info("\n===== ANÁLISIS DEL HTML =====")

        # Buscar data-jk (selector original)
        data_jk = soup.find_all(lambda t: t.get("data-jk"))
        logger.info(f"  [data-jk]            : {len(data_jk)} elementos")

        # Buscar otros atributos data- que puedan ser el ID de oferta
        for attr in ["data-job-id", "data-jobid", "data-id", "data-entity-urn",
                     "data-testid", "data-result-index", "data-hitorigin"]:
            found = soup.find_all(attrs={attr: True})
            if found:
                logger.info(f"  [{attr}]     : {len(found)} elementos — ejemplo: {found[0].get(attr)[:60]}")

        # Buscar clases que contengan "job" o "result"
        job_classes = set()
        for tag in soup.find_all(class_=True):
            for cls in tag.get("class", []):
                if any(x in cls.lower() for x in ["job", "result", "card", "mosaic"]):
                    job_classes.add(cls)
        if job_classes:
            logger.info(f"  Clases con 'job/result/card': {sorted(job_classes)[:15]}")

        # Detectar si Indeed mostró CAPTCHA o redirección
        title = soup.find("title")
        logger.info(f"  <title> de la página : {title.text if title else 'N/A'}")

        # Mostrar primeros 500 chars del body para orientación
        body = soup.find("body")
        if body:
            texto_body = body.get_text(separator=" ", strip=True)[:500]
            logger.info(f"  Inicio del body      : {texto_body}")

        # Buscar scripts con JSON de datos (Indeed a veces incrusta JSON)
        scripts = soup.find_all("script", type="application/ld+json")
        logger.info(f"  Scripts JSON-LD      : {len(scripts)} encontrados")
        for i, s in enumerate(scripts[:3]):
            logger.info(f"    Script {i}: {s.text[:200]}")

        await browser.close()
        logger.info("\n✅ Diagnóstico completo.")
        logger.info(f"   Revisá los archivos en: {DIAG_DIR}")
        logger.info("   Archivos generados:")
        logger.info("     - home.html           → HTML de la home de Indeed")
        logger.info("     - search_result.html  → HTML de la página de resultados")
        logger.info("     - screenshot_search.png → Captura visual de lo que ve Playwright")

if __name__ == "__main__":
    print("\n🔍 Ejecutando diagnóstico de Indeed con Playwright...")
    asyncio.run(diagnostico())
