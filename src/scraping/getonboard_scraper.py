#!/usr/bin/env python3
# =============================================================================
# getonboard_scraper.py
# Tesis: "Sistema de Recomendación Laboral - Motor de Matching Inteligente"
# Alumno: Cicconi, Carlos Alberto - Maestría en Explotación de Datos (Austral)
# Módulo: T2 - Diseño y Desarrollo del Web Scraper
#
# Fuente: GetOnBoard (www.getonbrd.com) — reemplaza a Indeed en el corpus.
# Estrategia: API JSON pública no autenticada.
#
# Estructura real confirmada por diagnóstico (2026-03-30):
#   item['id']                      → slug de texto (e.g. "data-scientist-senior-...")
#   item['attributes']['title']     → título del puesto
#   item['attributes']['company']   → dict con datos de empresa
#   item['attributes']['description'] → HTML con descripción
#   item['attributes']['functions'] → HTML con funciones del puesto
#   item['attributes']['seniority'] → {'data': {'id': N, 'type': 'seniority'}}
#   item['attributes']['modality']  → {'data': {'id': N, 'type': 'modality'}}
#   item['attributes']['remote_modality'] → string (e.g. "remote_local")
#   item['attributes']['min_salary'] / ['max_salary'] → numérico (USD)
#   item['attributes']['published_at'] → timestamp Unix (int)
#   item['links']['public_url']     → URL pública de la oferta
# =============================================================================

import requests
import time
import random
import logging
import sqlite3
import csv
import json
import re
from datetime import datetime, timezone
from dataclasses import dataclass, asdict, field
from typing import Optional
from pathlib import Path

# =============================================================================
# CONFIGURACIÓN DEL PROYECTO
# =============================================================================

BASE_DIR     = Path("/media/carlos-a-cicconi/Common1/Repositorios-Ing.Carlos-Cicconi/Trabajo_Final_MCD")
DATA_RAW_DIR = BASE_DIR / "data" / "raw"
LOGS_DIR     = BASE_DIR / "logs"

DATA_RAW_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)

LOG_FILE = LOGS_DIR / f"getonboard_scraper_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
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
    "Data Scientist",
    "Data Analyst",
    "Machine Learning",
    "NLP",
    "Data Engineer",
    "Software Engineer",
    "Python",
    "Backend",
    "Full Stack",
    "Ingeniero de Datos",
]

PAGES_PER_QUERY = 5
DELAY_MIN       = 1.5
DELAY_MAX       = 3.5
DELAY_QUERY_MIN = 4.0
DELAY_QUERY_MAX = 8.0
MAX_RETRIES     = 3

DB_FILENAME  = DATA_RAW_DIR / "ofertas_getonboard.db"
CSV_FILENAME = DATA_RAW_DIR / f"getonboard_{datetime.now().strftime('%Y%m%d')}.csv"
API_SEARCH   = "https://www.getonbrd.com/api/v0/search/jobs"
WEB_BASE     = "https://www.getonbrd.com"

# Mapeos confirmados por diagnóstico
SENIORITY_MAP = {
    1: "Trainee",
    2: "Junior",
    3: "Semi-Senior",
    4: "Senior",
    5: "Staff / Lead",
}

MODALITY_MAP = {
    1: "Full-Time",
    2: "Part-Time",
    3: "Freelance / Proyecto",
}

REMOTE_MAP = {
    "remote_local":   "Remoto (local)",
    "remote":         "Remoto",
    "full_remote":    "Remoto",
    "hybrid":         "Híbrido",
    "hybrid_remote":  "Híbrido",
    "on_site":        "Presencial",
    "presential":     "Presencial",
}


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
    fuente:      str = "getonboard"
    fecha_scrap: str = field(default_factory=lambda: datetime.now().isoformat())
    nivel:       Optional[str] = None
    tipo_empleo: Optional[str] = None
    modalidad:   Optional[str] = None
    salario:     Optional[str] = None


# =============================================================================
# HELPERS
# =============================================================================

def limpiar_html(texto: str) -> str:
    """Elimina tags HTML y normaliza espacios en blanco."""
    if not texto:
        return ""
    limpio = re.sub(r"<[^>]+>", " ", texto)
    limpio = re.sub(r"&nbsp;", " ", limpio)
    limpio = re.sub(r"&amp;",  "&", limpio)
    limpio = re.sub(r"&lt;",   "<", limpio)
    limpio = re.sub(r"&gt;",   ">", limpio)
    return re.sub(r"\s+", " ", limpio).strip()

def timestamp_a_fecha(ts) -> str:
    """Convierte timestamp Unix (int o str) a fecha YYYY-MM-DD."""
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        return str(ts) if ts else "N/A"


# =============================================================================
# CLASE PRINCIPAL
# =============================================================================

class GetOnBoardScraper:
    """
    Scraper para GetOnBoard usando su API JSON pública.
    Estructura de respuesta confirmada por diagnóstico el 2026-03-30.
    Uso: exclusivamente académico (Tesis MCD - Universidad Austral).
    """

    def __init__(self):
        self.session  = requests.Session()
        self.seen_ids = set()
        self._setup_db()
        logger.info("GetOnBoardScraper inicializado")

    def _get_headers(self) -> dict:
        return {
            "Accept":          "application/json",
            "Accept-Language": "es-AR,es;q=0.9,en;q=0.8",
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            "Referer": WEB_BASE,
        }

    # -----------------------------------------------------------------------
    # REQUEST
    # -----------------------------------------------------------------------

    def _get_json(self, url: str, params: dict = None) -> Optional[dict]:
        for intento in range(1, MAX_RETRIES + 1):
            try:
                time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))
                r = self.session.get(url, params=params,
                                     headers=self._get_headers(), timeout=20)
                if r.status_code == 200:
                    return r.json()
                elif r.status_code == 429:
                    wait = 60 * intento
                    logger.warning(f"Rate limit. Esperando {wait}s...")
                    time.sleep(wait)
                else:
                    logger.warning(f"HTTP {r.status_code} (intento {intento})")
                    time.sleep(10 * intento)
            except Exception as e:
                logger.error(f"Error (intento {intento}/{MAX_RETRIES}): {e}")
                time.sleep(10 * intento)
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
    # PARSING — estructura real confirmada por diagnóstico
    # -----------------------------------------------------------------------

    def _parse_item(self, item: dict, keyword: str) -> Optional[OfertaLaboral]:
        try:
            # --- ID: es el slug de texto ---
            job_id = str(item.get("id", "")).strip()
            if not job_id or job_id in self.seen_ids:
                return None
            self.seen_ids.add(job_id)

            attrs = item.get("attributes", {})

            # --- Título ---
            titulo = attrs.get("title", "N/A")

            # --- Empresa: viene en attributes['company'] como dict ---
            company = attrs.get("company", {})
            if isinstance(company, dict):
                empresa = (
                    company.get("name")
                    or company.get("short_name")
                    or "N/A"
                )
            else:
                empresa = str(company) if company else "N/A"

            # --- Descripción: concatenar description + functions (ambos HTML) ---
            desc_raw  = attrs.get("description", "") or ""
            func_raw  = attrs.get("functions", "")   or ""
            descripcion = limpiar_html(desc_raw)
            if func_raw:
                funciones = limpiar_html(func_raw)
                if funciones:
                    descripcion = f"{descripcion} {funciones}".strip()

            # --- Modalidad: remote_modality es un string directo ---
            remote_str = attrs.get("remote_modality", "")
            modalidad  = REMOTE_MAP.get(remote_str, remote_str if remote_str else None)

            # --- Ubicación: inferida de remote_modality y countries ---
            countries = attrs.get("countries", [])
            if isinstance(countries, list) and countries:
                ubicacion = ", ".join(str(c) for c in countries[:3])
            elif modalidad and "Remoto" in modalidad:
                ubicacion = "Remoto"
            else:
                ubicacion = "Argentina"

            # --- Seniority: {'data': {'id': N, 'type': 'seniority'}} ---
            seniority_raw = attrs.get("seniority", {})
            nivel = None
            if isinstance(seniority_raw, dict):
                seniority_id = seniority_raw.get("data", {}).get("id")
                nivel = SENIORITY_MAP.get(seniority_id)

            # --- Tipo de empleo: {'data': {'id': N, 'type': 'modality'}} ---
            modality_raw = attrs.get("modality", {})
            tipo_empleo = None
            if isinstance(modality_raw, dict):
                modality_id = modality_raw.get("data", {}).get("id")
                tipo_empleo = MODALITY_MAP.get(modality_id)

            # --- Salario: min/max en USD (sin campo currency separado) ---
            min_sal = attrs.get("min_salary")
            max_sal = attrs.get("max_salary")
            salario = None
            if min_sal and max_sal:
                salario = f"USD {min_sal} - {max_sal}"
            elif min_sal:
                salario = f"USD desde {min_sal}"
            elif max_sal:
                salario = f"USD hasta {max_sal}"

            # --- Fecha de publicación: timestamp Unix ---
            fecha_pub = timestamp_a_fecha(attrs.get("published_at"))

            # --- URL pública ---
            links = item.get("links", {})
            url   = links.get("public_url") or f"{WEB_BASE}/jobs/{job_id}"

            return OfertaLaboral(
                job_id      = job_id,
                titulo      = titulo,
                empresa     = empresa,
                ubicacion   = ubicacion,
                descripcion = descripcion,
                fecha_pub   = fecha_pub,
                url         = url,
                keyword     = keyword,
                nivel       = nivel,
                tipo_empleo = tipo_empleo,
                modalidad   = modalidad,
                salario     = salario,
            )

        except Exception as e:
            logger.debug(f"Error parseando item '{item.get('id')}': {e}")
            return None

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
        logger.info("INICIO DEL SCRAPING - GetOnBoard (API JSON)")
        logger.info(f"Búsquedas: {len(SEARCH_QUERIES)} | Páginas: {PAGES_PER_QUERY}")
        logger.info("=" * 60)

        total_guardadas = 0
        stats = []

        for keyword in SEARCH_QUERIES:
            logger.info(f"\n🔍 Buscando: '{keyword}'")
            ofertas_query = []

            for page in range(1, PAGES_PER_QUERY + 1):
                logger.info(f"   Página {page}/{PAGES_PER_QUERY}")

                data = self._get_json(API_SEARCH, params={
                    "query":    keyword,
                    "per_page": 20,
                    "page":     page,
                })

                if not data:
                    logger.warning(f"   ⚠️  Sin respuesta. Saltando página {page}.")
                    continue

                items = data.get("data", [])
                logger.info(f"   → {len(items)} items recibidos")

                if not items:
                    logger.info("   → Sin más resultados.")
                    break

                nuevas = 0
                for item in items:
                    oferta = self._parse_item(item, keyword)
                    if oferta:
                        self._save_to_db(oferta)
                        ofertas_query.append(oferta)
                        total_guardadas += 1
                        nuevas += 1

                logger.info(f"   → {nuevas} ofertas nuevas guardadas en esta página")

            self._export_to_csv(ofertas_query)
            stats.append({"keyword": keyword, "ofertas": len(ofertas_query)})
            logger.info(f"✅ '{keyword}': {len(ofertas_query)} ofertas guardadas")

            pausa = random.uniform(DELAY_QUERY_MIN, DELAY_QUERY_MAX)
            logger.info(f"   💤 Pausa: {pausa:.1f}s")
            time.sleep(pausa)

        logger.info("\n" + "=" * 60)
        logger.info("SCRAPING FINALIZADO - GetOnBoard")
        logger.info(f"Total ofertas únicas: {total_guardadas}")
        logger.info(f"DB : {DB_FILENAME}")
        logger.info(f"CSV: {CSV_FILENAME}")
        logger.info(f"Log: {LOG_FILE}")
        logger.info("=" * 60)

        stats_file = DATA_RAW_DIR / f"stats_getonboard_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(stats_file, "w", encoding="utf-8") as f:
            json.dump({
                "fuente":              "getonboard",
                "fecha":               datetime.now().isoformat(),
                "total_ofertas":       total_guardadas,
                "detalle_por_keyword": stats,
            }, f, ensure_ascii=False, indent=2)

        return total_guardadas


# =============================================================================
# EJECUCIÓN
# =============================================================================

if __name__ == "__main__":
    print("\n🚀 Iniciando scraper de GetOnBoard...")
    print("   Proyecto: Tesis MCD - Sistema de Recomendación Laboral")
    print("   Alumno: Cicconi, Carlos Alberto")
    print("   ℹ️  API JSON pública — reemplaza a Indeed\n")

    scraper = GetOnBoardScraper()
    total   = scraper.run()

    print(f"\n✅ Proceso completado. {total} ofertas recopiladas.")
    print(f"   Revisá los datos en: {DATA_RAW_DIR}")
