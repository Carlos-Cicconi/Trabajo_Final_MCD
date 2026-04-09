#!/usr/bin/env python3
# =============================================================================
# jobleads_scraper.py
# Tesis: "Sistema de Recomendación Laboral - Motor de Matching Inteligente"
# Alumno: Cicconi, Carlos Alberto - Maestría en Explotación de Datos (Austral)
# Módulo: T2 - Diseño y Desarrollo del Web Scraper
#
# Fuente: JobLeads (www.jobleads.com)
# Grupo:  JobLeads GmbH (Alemania) — plataforma de empleo profesional/ejecutivo
# Tecnología: Next.js (SSR) — tiene __NEXT_DATA__ con datos estructurados
#             Si falla, fallback a Playwright
# Estrategia: requests + BeautifulSoup (__NEXT_DATA__) → Playwright si SPA
#
# URL búsqueda: /ar/jobs?q={keyword}&page={n}
#               /jobs?q={keyword}&country=AR&page={n}
# =============================================================================

import asyncio
import requests, time, random, logging, sqlite3, csv, json, re
from datetime import datetime
from bs4 import BeautifulSoup
from dataclasses import dataclass, asdict, field
from typing import Optional
from pathlib import Path
from fake_useragent import UserAgent
from urllib.parse import urlencode

try:
    from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
    PLAYWRIGHT_OK = True
except ImportError:
    PLAYWRIGHT_OK = False

BASE_DIR     = Path("/media/carlos-a-cicconi/Common1/Repositorios-Ing.Carlos-Cicconi/Trabajo_Final_MCD")
DATA_RAW_DIR = BASE_DIR / "data" / "raw"
LOGS_DIR     = BASE_DIR / "logs"
DIAG_DIR     = DATA_RAW_DIR / "diagnostico"
for d in [DATA_RAW_DIR, LOGS_DIR, DIAG_DIR]:
    d.mkdir(parents=True, exist_ok=True)

LOG_FILE = LOGS_DIR / f"jobleads_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
logging.basicConfig(level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"),
              logging.StreamHandler()])
logger = logging.getLogger(__name__)

# =============================================================================
SEARCH_QUERIES = [
    {"keyword": "data scientist",     "label": "Data Scientist"},
    {"keyword": "data analyst",       "label": "Data Analyst"},
    {"keyword": "machine learning",   "label": "Machine Learning Engineer"},
    {"keyword": "nlp engineer",       "label": "NLP Engineer"},
    {"keyword": "data engineer",      "label": "Data Engineer"},
    {"keyword": "software engineer",  "label": "Software Engineer"},
    {"keyword": "python developer",   "label": "Python Developer"},
    {"keyword": "backend developer",  "label": "Desarrollador Backend"},
    {"keyword": "full stack developer","label": "Desarrollador Full Stack"},
    {"keyword": "ingeniero de datos", "label": "Ingeniero de Datos"},
]

PAGES_PER_QUERY  = 5
DELAY_MIN        = 2.5; DELAY_MAX        = 5.0
DELAY_DETAIL_MIN = 2.0; DELAY_DETAIL_MAX = 4.0
DELAY_QUERY_MIN  = 6.0; DELAY_QUERY_MAX  = 11.0
MAX_RETRIES      = 3
PAGE_TIMEOUT     = 30000
WAIT_TIMEOUT     = 12000

DB_FILENAME  = DATA_RAW_DIR / "ofertas_jobleads.db"
CSV_FILENAME = DATA_RAW_DIR / f"jobleads_{datetime.now().strftime('%Y%m%d')}.csv"
BASE_URL     = "https://www.jobleads.com"

# =============================================================================
@dataclass
class OfertaLaboral:
    job_id: str; titulo: str; empresa: str; ubicacion: str
    descripcion: str; fecha_pub: str; url: str; keyword: str
    fuente: str = "jobleads"
    fecha_scrap: str = field(default_factory=lambda: datetime.now().isoformat())
    nivel: Optional[str] = None; tipo_empleo: Optional[str] = None
    modalidad: Optional[str] = None; salario: Optional[str] = None

def limpiar(t):
    if not t: return ""
    return re.sub(r"\s+", " ", re.sub(r"[\x00-\x1f]", " ", t)).strip()

# =============================================================================
class JobleadsScraper:
    def __init__(self):
        self.session  = requests.Session()
        self.ua       = UserAgent()
        self.seen_ids = set()
        self.modo     = "requests"
        self._setup_db()
        self._diagnostico()
        logger.info(f"JobleadsScraper inicializado (modo: {self.modo})")

    def _headers(self):
        return {"User-Agent": self.ua.random,
                "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                "Accept-Language": "es-AR,es;q=0.9,en;q=0.8",
                "Referer": BASE_URL}

    def _diagnostico(self):
        logger.info("Ejecutando diagnóstico de JobLeads...")
        tests = [
            f"{BASE_URL}/ar/jobs?q=python",
            f"{BASE_URL}/jobs?q=python&country=AR",
            f"{BASE_URL}/es/ar/jobs?q=python",
            f"{BASE_URL}/ar/ofertas?q=python",
        ]
        for url in tests:
            try:
                time.sleep(1)
                r = self.session.get(url, headers=self._headers(), timeout=15,
                                     allow_redirects=True)
                logger.info(f"  {url} → HTTP {r.status_code} | {len(r.text):,} chars | Final: {r.url[:70]}")
                if r.status_code == 200 and len(r.text) > 5000:
                    (DIAG_DIR / "jobleads_test.html").write_text(r.text, encoding="utf-8")
                    soup = BeautifulSoup(r.text, "lxml")
                    title = soup.find("title")
                    logger.info(f"  <title>: {title.text.strip()[:70] if title else 'N/A'}")
                    has_next = "__NEXT_DATA__" in r.text
                    js_only  = "You need to enable JavaScript" in r.text
                    logger.info(f"  __NEXT_DATA__:{has_next} | JS-only:{js_only}")

                    if has_next:
                        nd = json.loads(soup.find("script", id="__NEXT_DATA__").string)
                        (DIAG_DIR / "jobleads_next_data.json").write_text(
                            json.dumps(nd, indent=2, ensure_ascii=False), encoding="utf-8")
                        props = nd.get("props",{}).get("pageProps",{})
                        logger.info(f"  pageProps keys: {list(props.keys())[:12]}")
                        # Buscar lista de jobs
                        def buscar_lista(obj, depth=0):
                            if depth > 5: return []
                            if isinstance(obj, list) and obj and isinstance(obj[0], dict):
                                if any(k in obj[0] for k in ["title","id","slug","jobId"]):
                                    return obj
                            if isinstance(obj, dict):
                                for v in obj.values():
                                    r2 = buscar_lista(v, depth+1)
                                    if r2: return r2
                            return []
                        items = buscar_lista(props)
                        if items:
                            logger.info(f"  Items en __NEXT_DATA__: {len(items)}")
                            logger.info(f"  Keys: {list(items[0].keys())[:12]}")
                        self.modo        = "next"
                        self.search_url  = r.url.split("?")[0]
                    elif js_only:
                        logger.info("  → SPA pura, usando Playwright")
                        self.modo       = "playwright"
                        self.search_url = url.split("?")[0]
                    else:
                        # HTML con contenido
                        for cls in ["job","result","listing","card","offer","vacancy"]:
                            found = soup.find_all(class_=re.compile(cls, re.I))
                            if found:
                                sample = " ".join(found[0].get("class",[]))
                                logger.info(f"  Clase '{cls}': {len(found)} | {sample[:60]}")
                        links = soup.find_all("a", href=re.compile(r"\d{4,}|/job/|/empleo/|/oferta/"))
                        logger.info(f"  Links oferta: {len(links)}")
                        for l in links[:3]:
                            logger.info(f"    {l.get('href','')} | {l.get_text(strip=True)[:60]}")
                        self.modo       = "requests"
                        self.search_url = r.url.split("?")[0]
                    return
            except Exception as e:
                logger.debug(f"  Error: {e}")
        self.modo       = "playwright" if not PLAYWRIGHT_OK else "playwright"
        self.search_url = f"{BASE_URL}/ar/jobs"

    def _setup_db(self):
        with sqlite3.connect(DB_FILENAME) as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS ofertas (
                job_id TEXT PRIMARY KEY, titulo TEXT, empresa TEXT,
                ubicacion TEXT, descripcion TEXT, fecha_pub TEXT,
                url TEXT, keyword TEXT, fuente TEXT, fecha_scrap TEXT,
                nivel TEXT, tipo_empleo TEXT, modalidad TEXT, salario TEXT)""")
            conn.commit()
        logger.info(f"DB lista: {DB_FILENAME}")

    def _save(self, o):
        with sqlite3.connect(DB_FILENAME) as conn:
            try:
                conn.execute("INSERT OR IGNORE INTO ofertas VALUES "
                    "(:job_id,:titulo,:empresa,:ubicacion,:descripcion,"
                    ":fecha_pub,:url,:keyword,:fuente,:fecha_scrap,"
                    ":nivel,:tipo_empleo,:modalidad,:salario)", asdict(o))
                conn.commit()
            except sqlite3.Error as e: logger.error(f"DB: {e}")

    def _get(self, url, params=None):
        for i in range(1, MAX_RETRIES+1):
            try:
                time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))
                r = self.session.get(url, params=params, headers=self._headers(),
                                     timeout=20, allow_redirects=True)
                if r.status_code == 200: return r
                elif r.status_code == 429: time.sleep(60*i)
                else:
                    logger.warning(f"HTTP {r.status_code} (intento {i})")
                    time.sleep(10*i)
            except Exception as e:
                logger.error(f"Error (intento {i}): {e}"); time.sleep(10*i)
        return None

    def _parse_next_data(self, html, keyword):
        """Extrae ofertas del __NEXT_DATA__ de Next.js."""
        soup = BeautifulSoup(html, "lxml")
        ofertas = []
        next_script = soup.find("script", id="__NEXT_DATA__")
        if not next_script: return []
        try:
            nd    = json.loads(next_script.string)
            props = nd.get("props",{}).get("pageProps",{})

            def buscar_lista(obj, depth=0):
                if depth > 5: return []
                if isinstance(obj, list) and obj and isinstance(obj[0], dict):
                    if any(k in obj[0] for k in ["title","id","slug","jobId","job_id"]):
                        return obj
                if isinstance(obj, dict):
                    for v in obj.values():
                        r2 = buscar_lista(v, depth+1)
                        if r2: return r2
                return []

            items = buscar_lista(props)
            for item in items:
                job_id = str(item.get("id") or item.get("jobId") or item.get("slug",""))
                if not job_id or job_id in self.seen_ids: continue
                self.seen_ids.add(job_id)

                titulo = item.get("title") or item.get("name") or "N/A"
                comp   = item.get("company") or item.get("employer") or {}
                empresa = comp.get("name","N/A") if isinstance(comp, dict) else str(comp)
                loc    = item.get("location") or item.get("city") or {}
                ubicacion = loc.get("name","Argentina") if isinstance(loc,dict) else str(loc)
                desc   = limpiar(re.sub(r"<[^>]+>"," ",
                         item.get("description") or item.get("summary","") or ""))
                fecha  = str(item.get("publishedAt") or item.get("date","N/A")).split("T")[0]
                slug   = item.get("slug") or job_id
                url    = item.get("url") or f"{BASE_URL}/ar/jobs/{slug}"
                if not url.startswith("http"): url = BASE_URL + url

                ofertas.append(OfertaLaboral(
                    job_id=job_id, titulo=limpiar(titulo), empresa=limpiar(empresa),
                    ubicacion=limpiar(ubicacion), descripcion=desc,
                    fecha_pub=fecha, url=url, keyword=keyword,
                ))
        except Exception as e:
            logger.debug(f"__NEXT_DATA__ error: {e}")

        if not ofertas:
            (DIAG_DIR / f"jobleads_{keyword.replace(' ','_')}.html"
             ).write_text(html, encoding="utf-8")
            logger.warning("   Sin ofertas en __NEXT_DATA__ — HTML guardado")
        return ofertas

    def _parse_html(self, html, keyword):
        """Parser HTML clásico como fallback."""
        soup    = BeautifulSoup(html, "lxml")
        ofertas = []
        cards = (
            soup.find_all("article") or
            soup.find_all(attrs={"data-job-id": True}) or
            soup.find_all("div", class_=re.compile(r"job|result|card|listing|offer", re.I))
        )
        for card in cards:
            try:
                link = card.find("a", href=re.compile(r"\d{4,}|/job/|/empleo/|/oferta/"))
                if not link: continue
                href   = link.get("href","")
                url    = href if href.startswith("http") else BASE_URL + href
                m      = re.search(r"(\d{4,})", href)
                job_id = m.group(1) if m else href.split("/")[-1][:40]
                if not job_id or job_id in self.seen_ids: continue
                self.seen_ids.add(job_id)

                titulo_tag   = card.find(["h2","h3","h1"]) or link
                empresa_tag  = card.find(class_=re.compile(r"company|empresa|employer", re.I))
                ubicacion_tag= card.find(class_=re.compile(r"location|ubicacion|city", re.I))
                fecha_tag    = card.find(class_=re.compile(r"date|fecha|time|ago|posted", re.I))

                ofertas.append(OfertaLaboral(
                    job_id=job_id,
                    titulo=limpiar(titulo_tag.get_text()) if titulo_tag else "N/A",
                    empresa=limpiar(empresa_tag.get_text()) if empresa_tag else "N/A",
                    ubicacion=limpiar(ubicacion_tag.get_text()) if ubicacion_tag else "Argentina",
                    descripcion="",
                    fecha_pub=limpiar(fecha_tag.get_text()) if fecha_tag else "N/A",
                    url=url, keyword=keyword,
                ))
            except Exception as e: logger.debug(f"Card error: {e}")

        if not ofertas:
            (DIAG_DIR / f"jobleads_{keyword.replace(' ','_')}.html"
             ).write_text(html, encoding="utf-8")
            logger.warning("   Sin ofertas HTML — guardado en diagnostico/")
        return ofertas

    def _enrich(self, oferta):
        if oferta.descripcion: return oferta
        r = self._get(oferta.url)
        if not r: return oferta
        soup = BeautifulSoup(r.text, "lxml")
        # Intentar __NEXT_DATA__ primero
        ns = soup.find("script", id="__NEXT_DATA__")
        if ns:
            try:
                nd    = json.loads(ns.string)
                props = nd.get("props",{}).get("pageProps",{})
                def buscar_desc(obj, depth=0):
                    if depth > 5: return ""
                    if isinstance(obj, dict):
                        for k in ["description","body","detail","content","summary"]:
                            if k in obj and isinstance(obj[k],str) and len(obj[k])>50:
                                return obj[k]
                        for v in obj.values():
                            r2 = buscar_desc(v, depth+1)
                            if r2: return r2
                    return ""
                desc = limpiar(re.sub(r"<[^>]+"," ", buscar_desc(props)))
                if desc: oferta.descripcion = desc; return oferta
            except Exception: pass
        for sel in [{"class": re.compile(r"description|detail|body|content", re.I)},
                    {"id": re.compile(r"description|detail|job", re.I)}]:
            div = soup.find(["div","section"], attrs=sel)
            if div:
                t = limpiar(div.get_text(separator=" "))
                if len(t) > len(oferta.descripcion): oferta.descripcion = t
                break
        return oferta

    def _export_csv(self, ofertas):
        if not ofertas: return
        fn = list(asdict(ofertas[0]).keys()); ex = CSV_FILENAME.exists()
        with open(CSV_FILENAME, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fn)
            if not ex: w.writeheader()
            for o in ofertas: w.writerow(asdict(o))
        logger.info(f"CSV: {len(ofertas)} exportadas")

    # -----------------------------------------------------------------------
    # RUN sincrónico (requests / next)
    # -----------------------------------------------------------------------
    def _run_sync(self):
        logger.info("="*60); logger.info(f"INICIO - JobLeads (modo: {self.modo})")
        total, stats = 0, []

        for q in SEARCH_QUERIES:
            keyword, label = q["keyword"], q["label"]
            logger.info(f"\n🔍 '{label}'"); oq = []

            for page in range(1, PAGES_PER_QUERY+1):
                params = {"q": keyword, "country": "AR", "page": page}
                logger.info(f"   Página {page}/{PAGES_PER_QUERY}")
                r = self._get(self.search_url, params)
                if not r: continue

                if self.modo == "next":
                    nuevas = self._parse_next_data(r.text, label)
                else:
                    nuevas = self._parse_html(r.text, label)

                logger.info(f"   → {len(nuevas)} encontradas")
                if not nuevas: break
                for o in nuevas:
                    time.sleep(random.uniform(DELAY_DETAIL_MIN, DELAY_DETAIL_MAX))
                    o = self._enrich(o)
                    self._save(o); oq.append(o); total += 1

            self._export_csv(oq)
            stats.append({"keyword": label, "ofertas": len(oq)})
            logger.info(f"✅ '{label}': {len(oq)} guardadas")
            time.sleep(random.uniform(DELAY_QUERY_MIN, DELAY_QUERY_MAX))

        logger.info(f"\n{'='*60}\nFINALIZADO — {total} ofertas")
        (DATA_RAW_DIR / f"stats_jobleads_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
         ).write_text(json.dumps({"fuente":"jobleads","modo":self.modo,
            "fecha":datetime.now().isoformat(),"total":total,"detalle":stats},
            ensure_ascii=False, indent=2))
        return total

    # -----------------------------------------------------------------------
    # RUN asincrónico (Playwright)
    # -----------------------------------------------------------------------
    async def _run_playwright(self):
        logger.info("="*60); logger.info("INICIO - JobLeads (Playwright)")
        total, stats = 0, []

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True, args=[
                "--no-sandbox","--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage","--window-size=1366,768"])
            context = await browser.new_context(
                viewport={"width":1366,"height":768},
                user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                locale="es-AR", timezone_id="America/Argentina/Buenos_Aires",
                extra_http_headers={"Accept-Language":"es-AR,es;q=0.9"})
            await context.add_init_script("""
                Object.defineProperty(navigator,'webdriver',{get:()=>undefined});
                Object.defineProperty(navigator,'plugins',{get:()=>[1,2,3,4,5]});
                window.chrome={runtime:{}};""")
            page = await context.new_page()

            logger.info("Inicializando sesión en JobLeads...")
            await page.goto(BASE_URL, timeout=PAGE_TIMEOUT, wait_until="domcontentloaded")
            await asyncio.sleep(random.uniform(2.0, 3.5))

            for q in SEARCH_QUERIES:
                keyword, label = q["keyword"], q["label"]
                logger.info(f"\n🔍 '{label}'"); oq = []

                for page_num in range(1, PAGES_PER_QUERY+1):
                    params = urlencode({"q":keyword,"country":"AR","page":page_num})
                    url    = f"{self.search_url}?{params}"
                    logger.info(f"   Página {page_num}/{PAGES_PER_QUERY}")
                    await asyncio.sleep(random.uniform(DELAY_MIN, DELAY_MAX))

                    try:
                        await page.goto(url, timeout=PAGE_TIMEOUT, wait_until="domcontentloaded")
                        try:
                            await page.wait_for_selector(
                                "article, [class*='job'], [class*='result'], [data-job-id]",
                                timeout=WAIT_TIMEOUT)
                        except PlaywrightTimeout: pass
                        await asyncio.sleep(random.uniform(2.0, 3.5))
                        html = await page.content()

                        # Guardar primera página para diagnóstico
                        if page_num == 1 and label == "Data Scientist":
                            (DIAG_DIR / "jobleads_playwright.html"
                             ).write_text(html, encoding="utf-8")
                            logger.info("   HTML Playwright guardado en diagnostico/")

                        nuevas = (self._parse_next_data(html, label)
                                  if "__NEXT_DATA__" in html
                                  else self._parse_html(html, label))
                        logger.info(f"   → {len(nuevas)} encontradas")
                        if not nuevas: break

                        for o in nuevas:
                            await asyncio.sleep(random.uniform(DELAY_DETAIL_MIN, DELAY_DETAIL_MAX))
                            await page.goto(o.url, timeout=PAGE_TIMEOUT, wait_until="domcontentloaded")
                            await asyncio.sleep(2.0)
                            detail_html = await page.content()
                            soup = BeautifulSoup(detail_html, "lxml")
                            ns = soup.find("script", id="__NEXT_DATA__")
                            if ns:
                                try:
                                    nd = json.loads(ns.string)
                                    props = nd.get("props",{}).get("pageProps",{})
                                    def bd(obj, d=0):
                                        if d>5: return ""
                                        if isinstance(obj,dict):
                                            for k in ["description","body","content"]:
                                                if k in obj and isinstance(obj[k],str) and len(obj[k])>50: return obj[k]
                                            for v in obj.values():
                                                r2=bd(v,d+1)
                                                if r2: return r2
                                        return ""
                                    desc = limpiar(re.sub(r"<[^>]+"," ",bd(props)))
                                    if desc: o.descripcion = desc
                                except Exception: pass
                            self._save(o); oq.append(o); total += 1

                    except Exception as e:
                        logger.error(f"   Error Playwright: {e}")

                self._export_csv(oq)
                stats.append({"keyword": label, "ofertas": len(oq)})
                logger.info(f"✅ '{label}': {len(oq)} guardadas")
                await asyncio.sleep(random.uniform(DELAY_QUERY_MIN, DELAY_QUERY_MAX))

            await browser.close()

        logger.info(f"\n{'='*60}\nFINALIZADO — {total} ofertas")
        (DATA_RAW_DIR / f"stats_jobleads_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
         ).write_text(json.dumps({"fuente":"jobleads","modo":"playwright",
            "fecha":datetime.now().isoformat(),"total":total,"detalle":stats},
            ensure_ascii=False, indent=2))
        return total

    def run(self):
        if self.modo == "playwright":
            if not PLAYWRIGHT_OK:
                logger.error("Playwright no disponible.")
                return 0
            return asyncio.run(self._run_playwright())
        return self._run_sync()

if __name__ == "__main__":
    print("\n🚀 JobLeads Argentina\n   Alumno: Cicconi, Carlos Alberto\n")
    total = JobleadsScraper().run()
    print(f"\n✅ {total} ofertas recopiladas.")
