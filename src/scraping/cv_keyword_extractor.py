#!/usr/bin/env python3
# =============================================================================
# cv_keyword_extractor.py
# Tesis: "Sistema de Recomendación Laboral - Motor de Matching Inteligente"
# Alumno: Cicconi, Carlos Alberto - Maestría en Explotación de Datos (Austral)
# Módulo: T2 - Diseño y Desarrollo del Web Scraper
#
# DESCRIPCIÓN:
#   Lee un CV en PDF y extrae:
#     - Keywords/habilidades para usar como SEARCH_QUERIES en los scrapers.
#     - Ubicación del candidato (país, provincia, ciudad) para filtrar
#       búsquedas por localidad y siempre incluir ofertas remotas.
#
#   Estrategia de lectura del PDF en cascada:
#     1. pdfplumber / pypdf (PDF nativo)
#     2. ZIP con archivos .txt por página (formato Claude/Preview)
#     3. Texto plano UTF-8
#
#   Extracción de keywords:
#     A. Matching con vocabulario curado (sin API key).
#     B. (Opcional) Llamada a Claude API con --llm.
#
#   Extracción de ubicación:
#     - Patrones regex sobre las primeras líneas del CV (cabecera).
#     - Diccionario de provincias y ciudades argentinas.
#     - Fallback a "Argentina" si no se detecta nada más específico.
#
# USO:
#   python cv_keyword_extractor.py CV.pdf
#   python cv_keyword_extractor.py CV.pdf --llm
#   python cv_keyword_extractor.py CV.pdf --output data/cvs/keywords.json
#   python cv_keyword_extractor.py CV.pdf --format queries
#   python cv_keyword_extractor.py CV.pdf --format table
#
# JSON DE SALIDA (consumido por run_scraping.py):
#   {
#     "cv_path": "...",
#     "ubicacion": {
#       "ciudad":            "Rosario",
#       "provincia":         "Santa Fe",
#       "pais":              "Argentina",
#       "linkedin_location": "Rosario, Santa Fe, Argentina"
#     },
#     "keywords": ["Data Scientist", "Python", ...],
#     "search_queries": [
#       {"keyword": "data-scientist", "label": "Data Scientist",
#        "location": "Rosario, Santa Fe, Argentina"},
#       {"keyword": "data-scientist", "label": "Data Scientist (Remoto AR)",
#        "location": "Argentina"},
#       {"keyword": "data-scientist", "label": "Data Scientist (Remoto)",
#        "location": "Worldwide"},
#       ...
#     ]
#   }
#
# DEPENDENCIAS:
#   pip install pdfplumber pypdf requests
# =============================================================================

import re
import sys
import json
import zipfile
import logging
import argparse
from pathlib import Path
from typing import Optional

try:
    import pdfplumber
    _PDFPLUMBER_OK = True
except ImportError:
    _PDFPLUMBER_OK = False

try:
    from pypdf import PdfReader
    _PYPDF_OK = True
except ImportError:
    _PYPDF_OK = False

# =============================================================================
# LOGGING
# =============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)


# =============================================================================
# VOCABULARIO DE KEYWORDS
# (término_canónico, [variantes], slug_para_URL)
# =============================================================================

VOCABULARIO = [
    # ── Roles de Datos / IA ──────────────────────────────────────────────────
    ("Data Scientist",           ["data scientist", "científico de datos", "ciencia de datos"],
                                  "data-scientist"),
    ("Data Analyst",             ["data analyst", "analista de datos", "análisis de datos"],
                                  "data-analyst"),
    ("Data Engineer",            ["data engineer", "ingeniero de datos", "ingeniería de datos",
                                   "data pipeline", "etl"],
                                  "data-engineer"),
    ("Machine Learning Engineer",["machine learning", "ml engineer", "aprendizaje automático",
                                   "aprendizaje de máquina"],
                                  "machine-learning"),
    ("NLP Engineer",             ["nlp", "natural language processing", "procesamiento de lenguaje",
                                   "text mining"],
                                  "nlp"),
    ("BI / Analytics",           ["power bi", "tableau", "looker", "business intelligence",
                                   "analítica", "kpi", "kpis", "dashboard"],
                                  "business-intelligence"),

    # ── Roles de Software ────────────────────────────────────────────────────
    ("Software Engineer",        ["software engineer", "ingeniero de software",
                                   "desarrollador de software"],
                                  "software-engineer"),
    ("Python Developer",         ["python"],
                                  "python"),
    ("Desarrollador Backend",    ["backend", "back-end", "back end", "desarrollador backend",
                                   "flask", "fastapi", "django"],
                                  "desarrollador-backend"),
    ("Desarrollador Full Stack", ["full stack", "full-stack", "fullstack",
                                   "desarrollador full stack"],
                                  "desarrollador-full-stack"),
    ("DevOps / MLOps",           ["devops", "mlops", "docker", "kubernetes", "ci/cd",
                                   "airflow", "dbt"],
                                  "devops"),

    # ── Tecnologías / Lenguajes ──────────────────────────────────────────────
    ("R Developer",              [" r,", " r.", "lenguaje r", "rstudio", "tidyverse"],
                                  "r"),
    ("Julia Developer",          ["julia"],
                                  "julia"),
    ("SQL",                      ["sql", "postgresql", "mysql", "bigquery", "redshift"],
                                  "sql"),
    ("Cloud",                    ["aws", "azure", "gcp", "google cloud", "cloud"],
                                  "cloud"),
    ("Spark / Big Data",         ["spark", "hadoop", "big data", "databricks", "hive"],
                                  "spark"),

    # ── Roles de Gestión / Control ──────────────────────────────────────────
    ("Control de Gestión",       ["control de gestión", "controlling", "planificación estratégica",
                                   "presupuesto", "budgeting"],
                                  "control-de-gestion"),
    ("Consultor",                ["consultor", "consultoría", "consulting"],
                                  "consultor"),
    ("Gerente de Operaciones",   ["gerente", "manager", "jefe de operaciones", "operaciones"],
                                  "gerente-operaciones"),
    ("Compras / Procurement",    ["compras", "procurement", "sourcing", "licitación",
                                   "supply chain", "mro"],
                                  "compras"),
    ("Ingeniero Industrial",     ["ingeniero industrial", "ingeniería industrial",
                                   "mejora continua", "lean", "optimización"],
                                  "ingeniero-industrial"),
    ("Finanzas / CAPEX",         ["finanzas", "capex", "inversión", "evaluación financiera",
                                   "costo", "costos"],
                                  "finanzas"),

    # ── Herramientas transversales ───────────────────────────────────────────
    ("SAP",                      ["sap"],
                                  "sap"),
    ("Excel / VBA",              ["excel", "vba", "google apps script", "spreadsheet"],
                                  "excel"),
    ("Google Apps Script",       ["google apps script", "google apps", "bot", "automatización"],
                                  "automatizacion"),
]


# =============================================================================
# DATOS DE UBICACIÓN — Argentina
# =============================================================================

# Provincias argentinas con sus variantes de escritura
PROVINCIAS_AR = {
    "Buenos Aires":        ["buenos aires", "bsas", "bs as", "gba", "conurbano",
                            "provincia de buenos aires"],
    "CABA":                ["ciudad autónoma", "ciudad autonoma", "caba",
                            "capital federal", "ciudad de buenos aires"],
    "Catamarca":           ["catamarca"],
    "Chaco":               ["chaco"],
    "Chubut":              ["chubut"],
    "Córdoba":             ["córdoba", "cordoba"],
    "Corrientes":          ["corrientes"],
    "Entre Ríos":          ["entre ríos", "entre rios"],
    "Formosa":             ["formosa"],
    "Jujuy":               ["jujuy"],
    "La Pampa":            ["la pampa"],
    "La Rioja":            ["la rioja"],
    "Mendoza":             ["mendoza"],
    "Misiones":            ["misiones"],
    "Neuquén":             ["neuquén", "neuquen"],
    "Río Negro":           ["río negro", "rio negro"],
    "Salta":               ["salta"],
    "San Juan":            ["san juan"],
    "San Luis":            ["san luis"],
    "Santa Cruz":          ["santa cruz"],
    "Santa Fe":            ["santa fe"],
    "Santiago del Estero": ["santiago del estero"],
    "Tierra del Fuego":    ["tierra del fuego"],
    "Tucumán":             ["tucumán", "tucuman"],
}

# Ciudades argentinas relevantes
CIUDADES_AR = [
    "rosario", "córdoba", "cordoba", "mendoza", "tucumán", "tucuman",
    "la plata", "mar del plata", "salta", "santa fe", "san juan",
    "resistencia", "neuquén", "neuquen", "formosa", "posadas", "corrientes",
    "bahía blanca", "bahia blanca", "san miguel de tucumán",
    "santiago del estero", "san salvador de jujuy", "paraná", "parana",
    "rawson", "río gallegos", "rio gallegos", "ushuaia", "viedma",
    "santa rosa", "san luis", "catamarca", "san nicolás", "san nicolas",
    "campana", "zárate", "zarate", "quilmes", "lanús", "lanus",
    "avellaneda", "lomas de zamora", "berazategui", "florencio varela",
    "morón", "moron", "tigre", "san isidro", "vicente lópez", "vicente lopez",
    "palermo", "belgrano", "puerto madryn",
]

# Mapa ciudad → provincia (para inferir provincia desde la ciudad)
CIUDAD_A_PROVINCIA = {
    "rosario":              "Santa Fe",
    "santa fe":             "Santa Fe",
    "córdoba":              "Córdoba",
    "cordoba":              "Córdoba",
    "mendoza":              "Mendoza",
    "tucumán":              "Tucumán",
    "tucuman":              "Tucumán",
    "la plata":             "Buenos Aires",
    "mar del plata":        "Buenos Aires",
    "bahía blanca":         "Buenos Aires",
    "bahia blanca":         "Buenos Aires",
    "campana":              "Buenos Aires",
    "zárate":               "Buenos Aires",
    "zarate":               "Buenos Aires",
    "san nicolás":          "Buenos Aires",
    "san nicolas":          "Buenos Aires",
    "quilmes":              "Buenos Aires",
    "lanús":                "Buenos Aires",
    "lanus":                "Buenos Aires",
    "avellaneda":           "Buenos Aires",
    "lomas de zamora":      "Buenos Aires",
    "berazategui":          "Buenos Aires",
    "florencio varela":     "Buenos Aires",
    "morón":                "Buenos Aires",
    "moron":                "Buenos Aires",
    "tigre":                "Buenos Aires",
    "san isidro":           "Buenos Aires",
    "vicente lópez":        "Buenos Aires",
    "vicente lopez":        "Buenos Aires",
    "salta":                "Salta",
    "neuquén":              "Neuquén",
    "neuquen":              "Neuquén",
    "resistencia":          "Chaco",
    "corrientes":           "Corrientes",
    "posadas":              "Misiones",
    "paraná":               "Entre Ríos",
    "parana":               "Entre Ríos",
    "formosa":              "Formosa",
    "san juan":             "San Juan",
    "san luis":             "San Luis",
    "catamarca":            "Catamarca",
    "santiago del estero":  "Santiago del Estero",
    "san salvador de jujuy":"Jujuy",
    "rawson":               "Chubut",
    "río gallegos":         "Santa Cruz",
    "rio gallegos":         "Santa Cruz",
    "ushuaia":              "Tierra del Fuego",
    "viedma":               "Río Negro",
    "santa rosa":           "La Pampa",
    "puerto madryn":        "Chubut",
}

# Países
PAISES = {
    "Argentina":      ["argentina"],
    "Uruguay":        ["uruguay"],
    "Chile":          ["chile"],
    "Brasil":         ["brasil", "brazil"],
    "Colombia":       ["colombia"],
    "México":         ["méxico", "mexico"],
    "España":         ["españa", "spain", "espana"],
    "Estados Unidos": ["estados unidos", "usa", "eeuu", "united states"],
}


# =============================================================================
# LECTURA DEL CV
# =============================================================================

def leer_pdf_nativo(ruta: Path) -> Optional[str]:
    if _PDFPLUMBER_OK:
        try:
            with pdfplumber.open(str(ruta)) as pdf:
                texto = "\n".join(page.extract_text() or "" for page in pdf.pages)
            if texto.strip():
                logger.info(f"  Texto extraído con pdfplumber ({len(texto):,} chars)")
                return texto
        except Exception as e:
            logger.debug(f"  pdfplumber falló: {e}")

    if _PYPDF_OK:
        try:
            reader = PdfReader(str(ruta))
            texto  = "\n".join(page.extract_text() or "" for page in reader.pages)
            if texto.strip():
                logger.info(f"  Texto extraído con pypdf ({len(texto):,} chars)")
                return texto
        except Exception as e:
            logger.debug(f"  pypdf falló: {e}")

    return None


def leer_zip_claude(ruta: Path) -> Optional[str]:
    """Lee el formato ZIP generado por Claude (archivos 1.txt, 2.txt … por página)."""
    try:
        with zipfile.ZipFile(str(ruta)) as z:
            txt_files = sorted(
                [n for n in z.namelist() if n.endswith(".txt") and n[0].isdigit()],
                key=lambda x: int(re.match(r"(\d+)", x).group(1))
            )
            if not txt_files:
                return None
            texto = "\n".join(
                z.read(n).decode("utf-8", errors="replace") for n in txt_files
            )
            logger.info(f"  Texto extraído desde ZIP ({len(txt_files)} páginas, "
                        f"{len(texto):,} chars)")
            return texto
    except Exception as e:
        logger.debug(f"  Lectura ZIP falló: {e}")
        return None


def leer_texto_plano(ruta: Path) -> Optional[str]:
    try:
        texto = ruta.read_text(encoding="utf-8", errors="replace")
        if len(texto.strip()) > 100:
            logger.info(f"  Leído como texto plano ({len(texto):,} chars)")
            return texto
    except Exception as e:
        logger.debug(f"  Lectura texto plano falló: {e}")
    return None


def extraer_texto_cv(ruta: Path) -> str:
    logger.info(f"Leyendo CV: {ruta}")
    texto = leer_pdf_nativo(ruta) or leer_zip_claude(ruta) or leer_texto_plano(ruta)
    if not texto:
        raise ValueError(
            f"No se pudo extraer texto del CV '{ruta}'. "
            "Verificá que sea un PDF válido, un ZIP con .txt, o texto plano."
        )
    return texto


# =============================================================================
# EXTRACCIÓN DE UBICACIÓN
# =============================================================================

def extraer_ubicacion(texto: str) -> dict:
    """
    Extrae la ubicación del candidato a partir del texto del CV.

    Estrategia:
    1. Analizar las primeras 15 líneas (cabecera) donde suele aparecer
       la dirección del candidato.
    2. Buscar el patrón "Ciudad, Provincia, País" o "Ciudad, CP País".
    3. Matching contra diccionario de ciudades y provincias argentinas.
    4. Fallback a Argentina si no se detecta nada más específico.

    Retorna un dict con: ciudad, provincia, pais, linkedin_location.
    """
    resultado = {
        "ciudad":            None,
        "provincia":         None,
        "pais":              "Argentina",
        "linkedin_location": "Argentina",
    }

    lineas      = texto.splitlines()
    # Cabecera estricta: solo las primeras 5 líneas (nombre + contacto)
    # para detectar la ubicación real del candidato sin confundirla
    # con las ciudades que aparecen en la experiencia laboral.
    cabecera_5   = " ".join(lineas[:5])
    # Cabecera ampliada: 15 líneas como fallback
    cabecera_15  = " ".join(lineas[:15])
    texto_lower  = texto.lower()
    cab5_low     = cabecera_5.lower()
    cab15_low    = cabecera_15.lower()

    # ── 1. Patrón explícito en cabecera: "Ciudad, [CP] Provincia/País" ───────
    # Cubre: "Rosario, Santa Fe, Argentina" o "Rosario, 2000 Argentina"
    patron = re.search(
        r"([A-ZÁÉÍÓÚÑÜ][a-záéíóúñü\s]{2,30}),\s*\d{0,5}\s*"
        r"([A-ZÁÉÍÓÚÑÜ][a-záéíóúñü\s]{2,30})(?:,\s*"
        r"(Argentina|Uruguay|Chile|Brasil|Colombia|México|España|Spain))?",
        cabecera_5
    )
    if patron:
        ciudad_cand  = patron.group(1).strip()
        segundo_cand = patron.group(2).strip()
        pais_cand    = patron.group(3)

        if ciudad_cand.lower() in CIUDADES_AR:
            resultado["ciudad"] = ciudad_cand.title()

        # ¿El segundo grupo es una provincia explícita?
        for prov_canon, variantes in PROVINCIAS_AR.items():
            if segundo_cand.lower() in variantes or \
               segundo_cand.lower() == prov_canon.lower():
                resultado["provincia"] = prov_canon
                break

        if pais_cand:
            resultado["pais"] = pais_cand.strip()

    # ── 2. Buscar ciudad en cabecera estricta (5 líneas) ─────────────────────
    if not resultado["ciudad"]:
        for ciudad in sorted(CIUDADES_AR, key=len, reverse=True):
            if ciudad in cab5_low:
                resultado["ciudad"] = ciudad.title()
                break

    # ── 3. Inferir provincia desde la ciudad detectada ────────────────────────
    # Esto evita que ciudades de la experiencia laboral contaminen la provincia.
    if resultado["ciudad"] and not resultado["provincia"]:
        prov_inferida = CIUDAD_A_PROVINCIA.get(resultado["ciudad"].lower())
        if prov_inferida:
            resultado["provincia"] = prov_inferida

    # ── 4. Búsqueda de provincia en cabecera estricta (si aún no hay) ────────
    if not resultado["provincia"]:
        for prov_canon, variantes in PROVINCIAS_AR.items():
            for variante in variantes:
                if variante in cab5_low:
                    resultado["provincia"] = prov_canon
                    break
            if resultado["provincia"]:
                break

    # ── 5. Detección de país en cabecera estricta ────────────────────────────
    for pais_canon, variantes in PAISES.items():
        for variante in variantes:
            if variante in cab5_low:
                resultado["pais"] = pais_canon
                break
        if resultado["pais"] != "Argentina":
            break

    # ── 6. Construir linkedin_location ───────────────────────────────────────
    partes = []
    if resultado["ciudad"]:
        partes.append(resultado["ciudad"])
    if resultado["provincia"] and resultado["provincia"] != resultado["ciudad"]:
        partes.append(resultado["provincia"])
    if resultado["pais"]:
        partes.append(resultado["pais"])

    resultado["linkedin_location"] = ", ".join(partes) if partes else "Argentina"

    return resultado


# =============================================================================
# EXTRACCIÓN DE KEYWORDS — vocabulario curado
# =============================================================================

def extraer_keywords_vocabulario(texto: str) -> list[dict]:
    texto_lower = texto.lower()
    encontrados = []
    vistos      = set()

    for (canon, variantes, slug) in VOCABULARIO:
        for variante in variantes:
            patron = re.compile(
                r"(?<![a-záéíóúñü])" + re.escape(variante) + r"(?![a-záéíóúñü])",
                re.IGNORECASE
            )
            if patron.search(texto_lower):
                if canon not in vistos:
                    encontrados.append({"label": canon, "keyword": slug, "match": variante})
                    vistos.add(canon)
                break

    return encontrados


# =============================================================================
# EXTRACCIÓN DE KEYWORDS — Claude API (--llm)
# =============================================================================

PROMPT_LLM = """\
Sos un sistema de extracción de información de CVs para una plataforma de \
búsqueda laboral en Argentina.

Dado el texto de un CV, extraé todas las habilidades, roles, tecnologías, \
lenguajes de programación, frameworks y áreas de expertise del candidato \
que sean relevantes para una búsqueda de empleo.

Para cada término generá:
  - "label": nombre canónico del rol/skill tal como aparecería en una oferta
  - "keyword": versión para URL (minúsculas, palabras separadas por guiones, sin tildes)

Respondé ÚNICAMENTE con un JSON válido, sin texto adicional ni bloques de código.
Ejemplo: [{{"label": "Data Scientist", "keyword": "data-scientist"}}]

TEXTO DEL CV:
{texto}
"""


def extraer_keywords_llm(texto: str) -> list[dict]:
    try:
        import requests as req
        logger.info("  Llamando a Claude API para extracción semántica...")

        response = req.post(
            "https://api.anthropic.com/v1/messages",
            headers={"Content-Type": "application/json"},
            json={
                "model":      "claude-sonnet-4-20250514",
                "max_tokens": 1000,
                "messages":   [{"role": "user",
                                "content": PROMPT_LLM.format(texto=texto[:8000])}],
            },
            timeout=60
        )

        if response.status_code != 200:
            logger.error(f"  API error {response.status_code}: {response.text[:200]}")
            return []

        raw_txt = "".join(
            b["text"] for b in response.json().get("content", [])
            if b.get("type") == "text"
        )
        raw_txt = re.sub(r"```(?:json)?", "", raw_txt).strip()
        items   = json.loads(raw_txt)
        logger.info(f"  Claude API devolvió {len(items)} keywords")
        return items

    except Exception as e:
        logger.error(f"  Error en llamada LLM: {e}")
        return []


# =============================================================================
# NORMALIZACIÓN Y DEDUPLICACIÓN
# =============================================================================

def normalizar_slug(label: str) -> str:
    mapa = str.maketrans("áéíóúÁÉÍÓÚñÑüÜ", "aeiouAEIOUnNuU")
    slug = label.lower().translate(mapa)
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    return re.sub(r"\s+", "-", slug.strip())


def deduplicar(keywords: list[dict]) -> list[dict]:
    vistos, limpios = set(), []
    for kw in keywords:
        label_norm = kw.get("label", "").strip().lower()
        if label_norm and label_norm not in vistos:
            vistos.add(label_norm)
            if not kw.get("keyword"):
                kw["keyword"] = normalizar_slug(kw["label"])
            limpios.append(kw)
    return limpios


# =============================================================================
# CONSTRUCCIÓN DE SEARCH_QUERIES CON UBICACIÓN + REMOTO
# =============================================================================

def construir_search_queries(keywords: list[dict], ubicacion: dict) -> list[dict]:
    """
    Por cada keyword genera tres búsquedas:
      1. Ubicación exacta del candidato  (ej: "Rosario, Santa Fe, Argentina")
      2. País del candidato              (ej: "Argentina")  → captura "Remoto - Argentina"
      3. Worldwide                                          → captura 100% remote globales

    Las tres van al consolidado y se deduplicarán por job_id.
    Para scrapers que no usan "location" (Bumeran, Computrabajo, etc.)
    el campo se ignora en la inyección — solo LinkedIn lo consume.
    """
    queries    = []
    loc_exacta = ubicacion["linkedin_location"]
    loc_pais   = ubicacion["pais"]
    loc_remoto = "Worldwide"

    for kw in keywords:
        slug  = kw["keyword"]
        label = kw["label"]

        # 1 — Ubicación exacta
        queries.append({
            "keyword":  slug,
            "label":    label,
            "location": loc_exacta,
        })

        # 2 — Remoto a nivel país (solo si la ubicación exacta no es ya el país)
        if loc_exacta != loc_pais:
            queries.append({
                "keyword":  slug,
                "label":    f"{label} (Remoto AR)",
                "location": loc_pais,
            })

        # 3 — Remoto worldwide
        queries.append({
            "keyword":  slug,
            "label":    f"{label} (Remoto)",
            "location": loc_remoto,
        })

    return queries


# =============================================================================
# FORMATO DE SALIDA
# =============================================================================

def imprimir_resultado(keywords: list[dict], ubicacion: dict,
                       search_queries: list[dict], fmt: str):

    if fmt == "json":
        print(json.dumps(
            {"keywords":       [kw["label"] for kw in keywords],
             "ubicacion":      ubicacion,
             "search_queries": search_queries},
            ensure_ascii=False, indent=2
        ))

    elif fmt == "queries":
        print("SEARCH_QUERIES = [")
        for q in search_queries:
            loc = f', "location": "{q["location"]}"' if q.get("location") else ""
            print(f'    {{"keyword": "{q["keyword"]}", "label": "{q["label"]}"{loc}}},')
        print("]")

    elif fmt == "table":
        print(f"\n📍 Ubicación detectada:")
        print(f"   Ciudad   : {ubicacion['ciudad'] or '(no detectada)'}")
        print(f"   Provincia: {ubicacion['provincia'] or '(no detectada)'}")
        print(f"   País     : {ubicacion['pais']}")
        print(f"   LinkedIn : {ubicacion['linkedin_location']}")
        print(f"\n{'Label':<40} {'Keyword':<35} {'Match'}")
        print("-" * 95)
        for kw in keywords:
            print(f"{kw.get('label',''):<40} {kw.get('keyword',''):<35} "
                  f"{kw.get('match','')}")
        print(f"\nTotal: {len(keywords)} keywords → "
              f"{len(search_queries)} búsquedas (ubicación + remoto)")

    else:  # text
        print(f"\n📍 Ubicación: {ubicacion['linkedin_location']}")
        print("\n📋 Keywords extraídas:")
        for kw in keywords:
            print(f"  • {kw['label']}")
        print(f"\nTotal: {len(keywords)} keywords → {len(search_queries)} búsquedas")


# =============================================================================
# MAIN
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Extrae keywords y ubicación de un CV para usar como input "
            "de los scrapers.\nTesis MCD — Cicconi, Carlos Alberto"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  python cv_keyword_extractor.py CV.pdf
  python cv_keyword_extractor.py CV.pdf --llm
  python cv_keyword_extractor.py CV.pdf --output data/cvs/keywords.json
  python cv_keyword_extractor.py CV.pdf --format queries
  python cv_keyword_extractor.py CV.pdf --format table
        """
    )
    parser.add_argument("cv_path",
                        help="Ruta al CV (PDF, ZIP con .txt, o texto plano)")
    parser.add_argument("--llm", action="store_true",
                        help="Usar Claude API para extracción semántica enriquecida")
    parser.add_argument("--output", metavar="ARCHIVO",
                        help="Guardar resultado en JSON (ej: data/cvs/keywords.json)")
    parser.add_argument("--format", choices=["text", "json", "queries", "table"],
                        default="table",
                        help="Formato de salida (default: table)")
    parser.add_argument("--verbose", action="store_true",
                        help="Mostrar texto extraído del CV")

    args = parser.parse_args()

    ruta = Path(args.cv_path)
    if not ruta.exists():
        logger.error(f"Archivo no encontrado: {ruta}")
        sys.exit(1)

    # ── Leer CV ──────────────────────────────────────────────────────────────
    try:
        texto = extraer_texto_cv(ruta)
    except ValueError as e:
        logger.error(str(e))
        sys.exit(1)

    if args.verbose:
        print("\n" + "=" * 60 + "\nTEXTO EXTRAÍDO DEL CV\n" + "=" * 60)
        print(texto[:3000])
        if len(texto) > 3000:
            print(f"... [{len(texto) - 3000:,} chars más]")
        print("=" * 60 + "\n")

    # ── Extraer ubicación ────────────────────────────────────────────────────
    logger.info("Extrayendo ubicación del candidato...")
    ubicacion = extraer_ubicacion(texto)
    logger.info(f"  → {ubicacion['linkedin_location']}")

    # ── Extraer keywords ─────────────────────────────────────────────────────
    logger.info("Extrayendo keywords...")
    keywords_vocab = extraer_keywords_vocabulario(texto)
    logger.info(f"  Vocabulario curado: {len(keywords_vocab)} matches")

    keywords_llm = []
    if args.llm:
        keywords_llm = extraer_keywords_llm(texto)
        logger.info(f"  Claude API: {len(keywords_llm)} keywords")

    keywords = deduplicar(keywords_vocab + keywords_llm)
    logger.info(f"  Total tras deduplicar: {len(keywords)} keywords")

    # ── Construir search_queries ──────────────────────────────────────────────
    search_queries = construir_search_queries(keywords, ubicacion)
    logger.info(f"  Search queries generadas: {len(search_queries)} "
                f"({len(keywords)} keywords × 3 ubicaciones)")

    # ── Imprimir resultado ────────────────────────────────────────────────────
    imprimir_resultado(keywords, ubicacion, search_queries, args.format)

    # ── Guardar JSON ──────────────────────────────────────────────────────────
    if args.output:
        resultado = {
            "cv_path":        str(ruta),
            "ubicacion":      ubicacion,
            "keywords":       [kw["label"] for kw in keywords],
            "search_queries": search_queries,
        }
        Path(args.output).write_text(
            json.dumps(resultado, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        logger.info(f"Resultado guardado en: {args.output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
