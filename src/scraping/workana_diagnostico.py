#!/usr/bin/env python3
# =============================================================================
# workana_diagnostico.py
# Inspecciona la estructura real de Workana para ajustar el scraper.
# Guarda HTML/JSON en data/raw/diagnostico/ para análisis.
# =============================================================================

import requests
import json
import re
import time
from pathlib import Path
from bs4 import BeautifulSoup

BASE_DIR = Path("/media/carlos-a-cicconi/Common1/Repositorios-Ing.Carlos-Cicconi/Trabajo_Final_MCD")
DIAG_DIR = BASE_DIR / "data" / "raw" / "diagnostico"
DIAG_DIR.mkdir(parents=True, exist_ok=True)

BASE_URL = "https://www.workana.com"

HEADERS_HTML = {
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-AR,es;q=0.9,en;q=0.8",
    "User-Agent":      "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
}
HEADERS_JSON = {**HEADERS_HTML, "Accept": "application/json, text/plain, */*",
                "X-Requested-With": "XMLHttpRequest"}

session = requests.Session()

print("=" * 60)
print("DIAGNÓSTICO DE WORKANA")
print("=" * 60)

# =============================================================================
# 1. HOME — obtener cookies de sesión y detectar tecnología
# =============================================================================
print("\n[1/5] Visitando home para obtener cookies...")
r_home = session.get(BASE_URL, headers=HEADERS_HTML, timeout=20)
print(f"  HTTP {r_home.status_code} | {len(r_home.text):,} chars")
print(f"  Cookies: {dict(session.cookies)}")
(DIAG_DIR / "workana_home.html").write_text(r_home.text, encoding="utf-8")
print(f"  Home guardada → diagnostico/workana_home.html")

# Detectar tecnología
has_nuxt   = "__NUXT__" in r_home.text
has_next   = "__NEXT_DATA__" in r_home.text
has_vue    = "vue" in r_home.text.lower()
has_react  = "react" in r_home.text.lower()
js_only    = "You need to enable JavaScript" in r_home.text
print(f"  __NUXT__:{has_nuxt} | __NEXT_DATA__:{has_next} | Vue:{has_vue} | React:{has_react} | JS-only:{js_only}")

time.sleep(2)

# =============================================================================
# 2. PÁGINA DE BÚSQUEDA HTML
# =============================================================================
print("\n[2/5] Probando página HTML de búsqueda...")

search_urls = [
    f"{BASE_URL}/jobs?category=it-programming&language=es&country=AR",
    f"{BASE_URL}/jobs?language=es&country=AR&search=data+scientist",
    f"{BASE_URL}/freelancers/jobs?category=it-programming",
    f"{BASE_URL}/trabajos?categoria=informatica-tecnologia",
]

for url in search_urls:
    try:
        r = session.get(url, headers=HEADERS_HTML, timeout=15,
                        allow_redirects=True)
        print(f"\n  URL: {url}")
        print(f"  → HTTP {r.status_code} | {len(r.text):,} chars | Final: {r.url[:80]}")

        if r.status_code == 200 and len(r.text) > 5000:
            # Análisis rápido
            soup = BeautifulSoup(r.text, "lxml")
            title = soup.find("title")
            print(f"  → <title>: {title.text.strip()[:80] if title else 'N/A'}")

            has_nuxt_p = "__NUXT__" in r.text
            has_next_p = "__NEXT_DATA__" in r.text
            js_only_p  = "You need to enable JavaScript" in r.text
            print(f"  → __NUXT__:{has_nuxt_p} | __NEXT_DATA__:{has_next_p} | JS-only:{js_only_p}")

            # Buscar links de proyectos
            job_links = soup.find_all("a", href=re.compile(r"/job/|/project/|/trabajo/|/proyecto/"))
            print(f"  → Links de proyectos: {len(job_links)}")
            for l in job_links[:3]:
                print(f"    href='{l.get('href','')}' | texto='{l.get_text(strip=True)[:60]}'")

            # Guardar el primero que tenga contenido
            slug = url.split("?")[0].split("/")[-1] or "jobs"
            fname = f"workana_{slug}_{r.status_code}.html"
            (DIAG_DIR / fname).write_text(r.text, encoding="utf-8")
            print(f"  → Guardado: diagnostico/{fname}")
            break
    except Exception as e:
        print(f"  Error: {e}")
    time.sleep(1.5)

# =============================================================================
# 3. APIs REST — probar múltiples endpoints
# =============================================================================
print("\n[3/5] Probando endpoints de API REST...")

api_candidates = [
    # API v2 (documentada en algunos foros)
    (f"{BASE_URL}/api/jobs",
     {"category": "it-programming", "language": "es", "country": "AR", "page": 1}),
    (f"{BASE_URL}/api/v2/jobs",
     {"category": "it-programming", "language": "es", "page": 1}),
    # API GraphQL
    (f"{BASE_URL}/graphql", None),
    # API interna Nuxt
    (f"{BASE_URL}/_api/jobs",
     {"category": "it-programming", "page": 1}),
    (f"{BASE_URL}/api/projects",
     {"category": "it-programming", "language": "es", "page": 1}),
    # Endpoint con search
    (f"{BASE_URL}/api/jobs/search",
     {"q": "data scientist", "language": "es", "page": 1}),
    # Sin /api/
    (f"{BASE_URL}/jobs.json",
     {"category": "it-programming", "language": "es"}),
]

for api_url, params in api_candidates:
    try:
        r = session.get(api_url, params=params, headers=HEADERS_JSON,
                        timeout=10)
        ct = r.headers.get("content-type", "?")
        print(f"\n  {api_url}")
        print(f"  → HTTP {r.status_code} | CT: {ct[:60]}")
        if r.status_code == 200:
            if "json" in ct:
                data = r.json()
                print(f"  → ✅ JSON! Keys raíz: {list(data.keys())[:10]}")
                # Buscar listas con items
                for k, v in data.items():
                    if isinstance(v, list) and v:
                        print(f"     data['{k}']: {len(v)} items")
                        if isinstance(v[0], dict):
                            print(f"     Keys primer item: {list(v[0].keys())[:12]}")
                fname = f"workana_api_{api_url.split('/')[-1]}.json"
                (DIAG_DIR / fname).write_text(
                    json.dumps(data, indent=2, ensure_ascii=False),
                    encoding="utf-8"
                )
                print(f"  → Guardado: diagnostico/{fname}")
            else:
                print(f"  → Respuesta no-JSON: {r.text[:150]}")
    except Exception as e:
        print(f"  → Error: {e}")
    time.sleep(1)

# =============================================================================
# 4. INTERCEPTAR REQUESTS XHR desde el HTML
# =============================================================================
print("\n[4/5] Buscando referencias a API en el HTML de la home...")
soup_home = BeautifulSoup(r_home.text, "lxml")

# Buscar en scripts
api_refs   = set()
fetch_refs = set()
for script in soup_home.find_all("script"):
    content = script.string or ""
    # Buscar URLs de API
    for m in re.finditer(r'["\'](/(?:api|graphql|_api)[^"\']{0,80})["\']', content):
        api_refs.add(m.group(1))
    # Buscar fetch/axios calls
    for m in re.finditer(r'(?:fetch|axios\.get)\(["\']([^"\']{5,80})["\']', content):
        fetch_refs.add(m.group(1))
    # Buscar baseURL
    for m in re.finditer(r'baseURL["\s:]+["\']([^"\']{5,60})["\']', content):
        print(f"  baseURL encontrado: {m.group(1)}")

if api_refs:
    print(f"  API refs en scripts: {sorted(api_refs)[:10]}")
if fetch_refs:
    print(f"  Fetch/axios refs: {sorted(fetch_refs)[:10]}")

# Buscar en meta tags y data attributes
for tag in soup_home.find_all(attrs={"data-api": True}):
    print(f"  data-api: {tag.get('data-api')}")

# =============================================================================
# 5. RESUMEN Y PRÓXIMOS PASOS
# =============================================================================
print("\n[5/5] Resumen del diagnóstico")
print(f"  Archivos generados en: {DIAG_DIR}")
for f in sorted(DIAG_DIR.glob("workana_*")):
    print(f"    {f.name} ({f.stat().st_size:,} bytes)")

print("""
Para enviar a Claude:
  1. La salida completa de esta consola
  2. El contenido de diagnostico/workana_home.html (primeros 3000 chars)
     → head -c 3000 data/raw/diagnostico/workana_home.html
  3. Si existe workana_api_*.json, su contenido completo
""")
print("✅ Diagnóstico completo.")
