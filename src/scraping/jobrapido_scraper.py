#!/usr/bin/env python3
# =============================================================================
# jobrapido_scraper.py
# Tesis: "Sistema de Recomendación Laboral - Motor de Matching Inteligente"
# Alumno: Cicconi, Carlos Alberto - Maestría en Explotación de Datos (Austral)
# Módulo: T2 - Diseño y Desarrollo del Web Scraper
#
# Fuente: Jobrapido Argentina (ar.jobrapido.com)
# Grupo:  Jobrapido.com (Italia) — agregador con cobertura global
# Tecnología: HTML SSR muy liviano, sin frameworks pesados
# Estrategia: requests + BeautifulSoup
#
# URL búsqueda: /?q={keyword}&l=Argentina&p={n}
# URL detalle:  /trabajo/{id}-{slug}
# =============================================================================

import requests, time, random, logging, sqlite3, csv, json, re
from datetime import datetime
from bs4 import BeautifulSoup
from dataclasses import dataclass, asdict, field
from typing import Optional
from pathlib import Path
from fake_useragent import UserAgent

BASE_DIR     = Path("/media/carlos-a-cicconi/Common1/Repositorios-Ing.Carlos-Cicconi/Trabajo_Final_MCD")
DATA_RAW_DIR = BASE_DIR / "data" / "raw"
LOGS_DIR     = BASE_DIR / "logs"
DIAG_DIR     = DATA_RAW_DIR / "diagnostico"
for d in [DATA_RAW_DIR, LOGS_DIR, DIAG_DIR]:
    d.mkdir(parents=True, exist_ok=True)

LOG_FILE = LOGS_DIR / f"jobrapido_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
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
    {"keyword": "nlp",                "label": "NLP Engineer"},
    {"keyword": "data engineer",      "label": "Data Engineer"},
    {"keyword": "software engineer",  "label": "Software Engineer"},
    {"keyword": "python",             "label": "Python Developer"},
    {"keyword": "desarrollador backend",    "label": "Desarrollador Backend"},
    {"keyword": "desarrollador full stack", "label": "Desarrollador Full Stack"},
    {"keyword": "ingeniero de datos",       "label": "Ingeniero de Datos"},
]

PAGES_PER_QUERY  = 5
DELAY_MIN        = 2.0; DELAY_MAX        = 4.5
DELAY_DETAIL_MIN = 1.5; DELAY_DETAIL_MAX = 3.5
DELAY_QUERY_MIN  = 5.0; DELAY_QUERY_MAX  = 9.0
MAX_RETRIES      = 3

DB_FILENAME  = DATA_RAW_DIR / "ofertas_jobrapido.db"
CSV_FILENAME = DATA_RAW_DIR / f"jobrapido_{datetime.now().strftime('%Y%m%d')}.csv"
BASE_URL     = "https://ar.jobrapido.com"

# =============================================================================
@dataclass
class OfertaLaboral:
    job_id: str; titulo: str; empresa: str; ubicacion: str
    descripcion: str; fecha_pub: str; url: str; keyword: str
    fuente: str = "jobrapido"
    fecha_scrap: str = field(default_factory=lambda: datetime.now().isoformat())
    nivel: Optional[str] = None; tipo_empleo: Optional[str] = None
    modalidad: Optional[str] = None; salario: Optional[str] = None

def limpiar(t):
    if not t: return ""
    return re.sub(r"\s+", " ", re.sub(r"[\x00-\x1f]", " ", t)).strip()

# =============================================================================
class JobrapidoScraper:
    def __init__(self):
        self.session  = requests.Session()
        self.ua       = UserAgent()
        self.seen_ids = set()
        self._setup_db()
        self._diagnostico()
        logger.info("JobrapidoScraper inicializado")

    def _headers(self):
        return {"User-Agent": self.ua.random,
                "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                "Accept-Language": "es-AR,es;q=0.9",
                "Referer": BASE_URL}

    def _diagnostico(self):
        logger.info("Ejecutando diagnóstico de Jobrapido...")
        tests = [
            (BASE_URL, {"q": "python", "l": "Argentina"}),
            (f"{BASE_URL}/trabajo/python/argentina", {}),
            (f"{BASE_URL}/trabajos", {"q": "python", "l": "Argentina"}),
        ]
        for url, params in tests:
            try:
                time.sleep(1)
                r = self.session.get(url, params=params, headers=self._headers(),
                                     timeout=15, allow_redirects=True)
                logger.info(f"  {r.url[:70]} → HTTP {r.status_code} | {len(r.text):,} chars")
                if r.status_code == 200 and len(r.text) > 8000:
                    (DIAG_DIR / "jobrapido_test.html").write_text(r.text, encoding="utf-8")
                    soup = BeautifulSoup(r.text, "lxml")
                    title = soup.find("title")
                    logger.info(f"  <title>: {title.text.strip()[:70] if title else 'N/A'}")
                    js_only = "You need to enable JavaScript" in r.text
                    logger.info(f"  JS-only:{js_only}")
                    for cls in ["job","result","listing","item","card","vacancy"]:
                        found = soup.find_all(class_=re.compile(cls, re.I))
                        if found:
                            sample = " ".join(found[0].get("class",[]))
                            logger.info(f"  Clase '{cls}': {len(found)} | ejemplo: {sample[:60]}")
                    links = soup.find_all("a", href=re.compile(r"\d{4,}|/trabajo/|/job/|/empleo/"))
                    logger.info(f"  Links de oferta: {len(links)}")
                    for l in links[:3]:
                        logger.info(f"    {l.get('href','')} | {l.get_text(strip=True)[:60]}")
                    # Detectar paginación
                    pag = soup.find(class_=re.compile(r"paginat|pagina|page-nav", re.I))
                    if pag: logger.info(f"  Paginación: {pag.get_text(strip=True)[:80]}")
                    self.search_url    = r.url.split("?")[0]
                    self.search_params = dict(p.split("=") for p in r.url.split("?")[1].split("&")) if "?" in r.url else {"q":"{keyword}","l":"Argentina"}
                    return
            except Exception as e:
                logger.debug(f"  Error: {e}")
        self.search_url    = BASE_URL
        self.search_params = {"q": "{keyword}", "l": "Argentina"}

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

    def _parse_search(self, html, keyword):
        soup    = BeautifulSoup(html, "lxml")
        ofertas = []

        # Jobrapido usa estructura muy limpia con clases simples
        cards = (
            soup.find_all("article") or
            soup.find_all(attrs={"data-id": True}) or
            soup.find_all(attrs={"data-job": True}) or
            soup.find_all("div", class_=re.compile(r"\bjob\b|\bitem\b|\bcard\b|\bresult\b", re.I)) or
            soup.find_all("li",  class_=re.compile(r"\bjob\b|\bitem\b|\bresult\b", re.I))
        )
        logger.debug(f"   Cards: {len(cards)}")

        for card in cards:
            try:
                job_id = card.get("data-id") or card.get("data-job") or ""
                link   = card.find("a", href=re.compile(r"\d{4,}|/trabajo/|/job/|/empleo/"))
                if not link: continue
                href = link.get("href","")
                url  = href if href.startswith("http") else BASE_URL + href
                if not job_id:
                    m = re.search(r"(\d{4,})", href)
                    job_id = m.group(1) if m else href.split("/")[-1][:40]
                if not job_id or job_id in self.seen_ids: continue
                self.seen_ids.add(job_id)

                titulo_tag   = card.find(["h2","h3","h1","strong"]) or link
                empresa_tag  = card.find(class_=re.compile(r"company|empresa|employer", re.I))
                ubicacion_tag= card.find(class_=re.compile(r"location|ubicacion|city|place", re.I))
                fecha_tag    = card.find(class_=re.compile(r"date|fecha|time|ago|posted|when", re.I))
                salario_tag  = card.find(class_=re.compile(r"salary|salario|wage|remunera", re.I))

                ofertas.append(OfertaLaboral(
                    job_id    = job_id,
                    titulo    = limpiar(titulo_tag.get_text()) if titulo_tag else "N/A",
                    empresa   = limpiar(empresa_tag.get_text()) if empresa_tag else "N/A",
                    ubicacion = limpiar(ubicacion_tag.get_text()) if ubicacion_tag else "Argentina",
                    descripcion = "",
                    fecha_pub = limpiar(fecha_tag.get_text()) if fecha_tag else "N/A",
                    url=url, keyword=keyword,
                    salario=limpiar(salario_tag.get_text()) if salario_tag else None,
                ))
            except Exception as e: logger.debug(f"Card error: {e}")

        if not ofertas:
            (DIAG_DIR / f"jobrapido_{keyword.replace(' ','_')}.html"
             ).write_text(html, encoding="utf-8")
            logger.warning("   Sin ofertas — HTML guardado en diagnostico/")
        return ofertas

    def _enrich(self, oferta):
        r = self._get(oferta.url)
        if not r: return oferta
        soup = BeautifulSoup(r.text, "lxml")
        for sel in [{"class": re.compile(r"description|descripcion|detail|body|content", re.I)},
                    {"id": re.compile(r"description|detail|job", re.I)}]:
            div = soup.find(["div","section","article"], attrs=sel)
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

    def run(self):
        logger.info("="*60); logger.info("INICIO - Jobrapido Argentina")
        total, stats = 0, []

        for q in SEARCH_QUERIES:
            keyword, label = q["keyword"], q["label"]
            logger.info(f"\n🔍 '{label}'"); oq = []

            for page in range(1, PAGES_PER_QUERY+1):
                params = {"q": keyword, "l": "Argentina", "p": page}
                logger.info(f"   Página {page}/{PAGES_PER_QUERY}")
                r = self._get(self.search_url, params)
                if not r: continue
                nuevas = self._parse_search(r.text, label)
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
        (DATA_RAW_DIR / f"stats_jobrapido_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
         ).write_text(json.dumps({"fuente":"jobrapido","fecha":datetime.now().isoformat(),
            "total":total,"detalle":stats}, ensure_ascii=False, indent=2))
        return total

if __name__ == "__main__":
    print("\n🚀 Jobrapido Argentina\n   Alumno: Cicconi, Carlos Alberto\n")
    total = JobrapidoScraper().run()
    print(f"\n✅ {total} ofertas recopiladas.")
