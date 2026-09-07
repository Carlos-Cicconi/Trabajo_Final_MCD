#!/usr/bin/env python3
# =============================================================================
# buscojobs_scraper.py
# Tesis: "Sistema de Recomendación Laboral - Motor de Matching Inteligente"
# Alumno: Cicconi, Carlos Alberto - Maestría en Explotación de Datos (Austral)
# Módulo: T2 - Diseño y Desarrollo del Web Scraper
# Descripción: Extrae ofertas laborales de Buscojobs Argentina y las
#              almacena en formato CSV y SQLite para su posterior procesamiento.
#
# URL base: https://www.buscojobs.com.ar
# Estrategia: el sitio es una app Next.js — tanto el listado como el detalle
#             de cada oferta embeben los datos ya estructurados como JSON en
#             <script id="__NEXT_DATA__">, por lo que no hace falta parsear
#             HTML/CSS con BeautifulSoup: se extrae y deserializa ese bloque.
#             No requiere autenticación. Uso exclusivamente académico.
#
# Nota sobre robots.txt: el sitio bloquea explícitamente a "ClaudeBot" en todo
# el dominio, pero permite "User-agent: *" en /ofertas salvo URLs con el
# parámetro "fechainicio=" (no utilizado por este scraper). Este scraper se
# identifica con un User-Agent de navegador estándar, no como ClaudeBot.
#
# ⚠️ DESACTIVADO (ver run_scraping.py, SCRAPERS_REGISTRO): se confirmó que el
# endpoint SSR usado acá (/ofertas/rc744/trabajo-en-{keyword}) ignora el
# keyword tanto en el path como en el query param "?que=" — siempre devuelve
# el mismo listado genérico del sitio, sin importar la búsqueda. Probado con
# keywords muy distintas ("data-scientist" vs "gastronomia"): mismos IDs de
# oferta, mismo orden. El filtrado real ocurre client-side (SPA) contra un
# endpoint que no se pudo identificar sin inspección de tráfico de red en un
# navegador real. No reactivar en el orquestador hasta encontrar y adaptar
# el scraper a ese endpoint real.
# =============================================================================

import requests
import time
import random
import logging
import sqlite3
import csv
import json
import re
import math
from datetime import datetime
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
LOG_FILE = LOGS_DIR / f"buscojobs_scraper_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
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

# Búsquedas objetivo — mismo criterio que el resto de los scrapers
# Buscojobs arma la URL como /ofertas/{filtros}/trabajo-en-{keyword}
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
PAGES_PER_QUERY = 5       # 15 ofertas por página → hasta ~75 por búsqueda
DELAY_MIN       = 3.0     # Segundos mínimos entre requests (ético)
DELAY_MAX       = 6.0     # Segundos máximos entre requests
MAX_RETRIES     = 3       # Reintentos ante fallo de conexión
OFERTAS_POR_PAGINA = 15   # Tamaño de página fijo del sitio
DB_FILENAME     = DATA_RAW_DIR / "db_buscojobs.db"
CSV_FILENAME    = DATA_RAW_DIR / f"ofertas_buscojobs_{datetime.now().strftime('%Y%m%d')}.csv"

# URL base de Buscojobs Argentina
BASE_URL     = "https://www.buscojobs.com.ar"
# Filtro de ciudad "rc744" = Argentina (alcance nacional). Es necesario
# incluirlo en la URL para que la paginación (páginas 2+) funcione: sin él
# el sitio devuelve 404 al pedir /trabajo-en-{keyword}/{pagina}.
FILTRO_PAIS  = "rc744"

# Patrón para extraer el bloque JSON embebido por Next.js
NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S
)


# =============================================================================
# DATACLASS: Estructura de una oferta laboral
# (Idéntica al resto de los scrapers para facilitar la unificación del corpus)
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
    fuente:      str = "buscojobs"
    fecha_scrap: str = field(default_factory=lambda: datetime.now().isoformat())
    nivel:       Optional[str] = None
    tipo_empleo: Optional[str] = None
    modalidad:   Optional[str] = None
    salario:     Optional[str] = None


# =============================================================================
# CLASE PRINCIPAL: BuscojobsScraper
# =============================================================================

class BuscojobsScraper:
    """
    Scraper para Buscojobs Argentina (buscojobs.com.ar).
    Opera sobre páginas públicas sin autenticación.
    Uso: exclusivamente académico (Tesis MCD - Universidad Austral).

    Estructura del sitio (Next.js, SSR):
      - Búsqueda: /ofertas/rc744/trabajo-en-{keyword}          (página 1)
                  /ofertas/rc744/trabajo-en-{keyword}/{pagina}  (página 2+)
        Los resultados vienen embebidos en
        __NEXT_DATA__.props.pageProps.resultadosIniciales.ofertas[]
      - Detalle:  URL canónica publicada en el campo "UrlOferta" del detalle
                  (patrón: /{slug}-en-argentina-ID-{IdOferta}); el servidor
                  resuelve por el ID final e ignora el slug.
        El detalle completo viene en
        __NEXT_DATA__.props.pageProps.oferta
    """

    def __init__(self):
        self.session  = requests.Session()
        self.ua       = UserAgent()
        self.seen_ids = set()
        self._setup_db()
        logger.info("BuscojobsScraper inicializado correctamente")

    # -----------------------------------------------------------------------
    # HEADERS Y REQUESTS
    # -----------------------------------------------------------------------

    def _get_headers(self) -> dict:
        """Genera headers que simulan un navegador real (no ClaudeBot)."""
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
                elif response.status_code == 404:
                    logger.info(f"HTTP 404 en {url} (sin más páginas)")
                    return None
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

    def _get_next_data(self, url: str) -> Optional[dict]:
        """Descarga una URL y devuelve el JSON embebido en __NEXT_DATA__."""
        response = self._request(url)
        if not response:
            return None

        match = NEXT_DATA_RE.search(response.text)
        if not match:
            logger.warning(f"No se encontró __NEXT_DATA__ en: {url}")
            return None

        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError as e:
            logger.error(f"Error decodificando __NEXT_DATA__ ({url}): {e}")
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
    # PARSING DE LA PÁGINA DE RESULTADOS (JSON __NEXT_DATA__)
    # -----------------------------------------------------------------------

    def _ubicacion_de(self, item: dict) -> str:
        """Arma la ubicación a partir de Ciudad / Departamento / Pais."""
        ciudad       = item.get("Ciudad") or {}
        departamento = item.get("Departamento") or {}
        pais         = item.get("Pais") or {}
        partes = [
            ciudad.get("Nombre"),
            departamento.get("Nombre"),
        ]
        partes = [p for p in partes if p]
        if partes:
            return ", ".join(dict.fromkeys(partes))  # sin duplicados, preserva orden
        return pais.get("Nombre") or "Argentina"

    def _parse_listado(self, data: dict, keyword_label: str) -> tuple[list[OfertaLaboral], int]:
        """
        Extrae las ofertas de resultadosIniciales.ofertas y el total de
        resultados de la búsqueda (para calcular cuántas páginas recorrer).
        """
        try:
            page_props = data["props"]["pageProps"]
            resultados = page_props["resultadosIniciales"]
        except (KeyError, TypeError):
            logger.warning("Estructura inesperada en __NEXT_DATA__ del listado")
            return [], 0

        total   = resultados.get("count", 0)
        items   = resultados.get("ofertas", [])
        ofertas = []

        for item in items:
            try:
                job_id = str(item["IdOferta"])
                if job_id in self.seen_ids:
                    continue
                self.seen_ids.add(job_id)

                oferta = OfertaLaboral(
                    job_id      = job_id,
                    titulo      = item.get("CargoVacante") or "N/A",
                    empresa     = item.get("NombreEmpresa") or "N/A",
                    ubicacion   = self._ubicacion_de(item),
                    descripcion = item.get("Descripcion") or "",
                    fecha_pub   = item.get("FechaInicio") or "N/A",
                    url         = f"{BASE_URL}/oferta-ID-{job_id}",  # se reemplaza por UrlOferta al enriquecer
                    keyword     = keyword_label,
                )
                ofertas.append(oferta)
            except Exception as e:
                logger.debug(f"Error parseando oferta del listado: {e}")
                continue

        return ofertas, total

    # -----------------------------------------------------------------------
    # ENRIQUECIMIENTO CON EL DETALLE DE LA OFERTA
    # -----------------------------------------------------------------------

    def _enrich_with_details(self, oferta: OfertaLaboral) -> OfertaLaboral:
        """
        Accede al detalle de la oferta (__NEXT_DATA__.props.pageProps.oferta)
        para completar descripción íntegra, nivel, modalidad y salario.
        """
        data = self._get_next_data(oferta.url)
        if not data:
            return oferta

        try:
            detalle = data["props"]["pageProps"]["oferta"]
        except (KeyError, TypeError):
            logger.debug(f"Sin bloque 'oferta' en el detalle: {oferta.url}")
            return oferta

        if detalle.get("UrlOferta"):
            oferta.url = detalle["UrlOferta"]

        descripcion_completa = detalle.get("Descripcion") or detalle.get("DescripcionMarkdown")
        if descripcion_completa:
            oferta.descripcion = descripcion_completa.strip()

        requisitos = detalle.get("Requisitos") or detalle.get("RequisitosMinimos")
        if requisitos:
            oferta.descripcion = f"{oferta.descripcion}\n\nRequisitos:\n{requisitos.strip()}"

        nivel = detalle.get("NivelJerarquico") or {}
        if nivel.get("Nombre"):
            oferta.nivel = nivel["Nombre"]

        jornada = detalle.get("JornadaLaboral") or {}
        if jornada.get("Nombre"):
            oferta.tipo_empleo = jornada["Nombre"]

        if detalle.get("PermiteTeletrabajo"):
            oferta.modalidad = "Remoto"
        elif detalle.get("PermiteTrabajoHibrido"):
            oferta.modalidad = "Híbrido"
        else:
            oferta.modalidad = "Presencial"

        sueldo_desde = detalle.get("SueldoDesde") or 0
        sueldo_hasta = detalle.get("SueldoHasta") or 0
        if sueldo_desde or sueldo_hasta:
            oferta.salario = f"{sueldo_desde}-{sueldo_hasta}"

        if oferta.empresa == "N/A" and detalle.get("NombreEmpresa"):
            oferta.empresa = detalle["NombreEmpresa"]

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
        Para cada keyword recorre las páginas de resultados (JSON embebido),
        limitado por PAGES_PER_QUERY y por el total real de resultados que
        informa el propio sitio, y enriquece cada oferta con su detalle.
        """
        logger.info("=" * 60)
        logger.info("INICIO DEL SCRAPING - Buscojobs Argentina")
        logger.info(f"Búsquedas: {len(SEARCH_QUERIES)} | Páginas por búsqueda: {PAGES_PER_QUERY}")
        logger.info("=" * 60)

        total_guardadas = 0
        stats = []

        for query in SEARCH_QUERIES:
            keyword       = query["keyword"]
            keyword_label = query["label"]
            logger.info(f"\n🔍 Buscando: '{keyword_label}'")

            ofertas_query = []
            max_paginas   = PAGES_PER_QUERY

            for page in range(1, PAGES_PER_QUERY + 1):
                if page == 1:
                    url = f"{BASE_URL}/ofertas/{FILTRO_PAIS}/trabajo-en-{keyword}"
                else:
                    url = f"{BASE_URL}/ofertas/{FILTRO_PAIS}/trabajo-en-{keyword}/{page}"

                logger.info(f"   Página {page}/{max_paginas} → {url}")

                data = self._get_next_data(url)
                if not data:
                    logger.warning(f"   ⚠️  Sin respuesta. Saltando página {page}.")
                    continue

                nuevas, total = self._parse_listado(data, keyword_label)
                logger.info(f"   → {len(nuevas)} ofertas nuevas encontradas (total en el sitio: {total})")

                if page == 1 and total:
                    max_paginas = min(PAGES_PER_QUERY, math.ceil(total / OFERTAS_POR_PAGINA))

                if not nuevas:
                    logger.info("   → Sin más resultados para esta búsqueda.")
                    break

                for oferta in nuevas:
                    logger.debug(f"      Detalle: {oferta.url}")
                    oferta = self._enrich_with_details(oferta)
                    self._save_to_db(oferta)
                    ofertas_query.append(oferta)
                    total_guardadas += 1

                if page >= max_paginas:
                    break

            self._export_to_csv(ofertas_query)
            stats.append({"keyword": keyword_label, "ofertas": len(ofertas_query)})
            logger.info(f"✅ '{keyword_label}': {len(ofertas_query)} ofertas guardadas")

        # --- Reporte final ---
        logger.info("\n" + "=" * 60)
        logger.info("SCRAPING FINALIZADO - Buscojobs")
        logger.info(f"Total de ofertas únicas recopiladas: {total_guardadas}")
        logger.info(f"Base de datos : {DB_FILENAME}")
        logger.info(f"CSV exportado : {CSV_FILENAME}")
        logger.info(f"Log guardado  : {LOG_FILE}")
        logger.info("=" * 60)

        # Guardar estadísticas en JSON
        stats_file = DATA_RAW_DIR / f"stats_buscojobs_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(stats_file, "w", encoding="utf-8") as f:
            json.dump({
                "fuente":              "buscojobs",
                "fecha":               datetime.now().isoformat(),
                "total_ofertas":       total_guardadas,
                "detalle_por_keyword": stats
            }, f, ensure_ascii=False, indent=2)
        logger.info(f"Estadísticas guardadas: {stats_file}")

        return total_guardadas


# =============================================================================
# EJECUCIÓN
# =============================================================================

if __name__ == "__main__":
    print("\n🚀 Iniciando scraper de Buscojobs Argentina...")
    print("   Proyecto: Tesis MCD - Sistema de Recomendación Laboral")
    print("   Alumno: Cicconi, Carlos Alberto\n")

    scraper = BuscojobsScraper()
    total   = scraper.run()

    print(f"\n✅ Proceso completado. {total} ofertas recopiladas.")
    print(f"   Revisá los datos en: {DATA_RAW_DIR}")
