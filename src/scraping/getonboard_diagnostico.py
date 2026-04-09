#!/usr/bin/env python3
# =============================================================================
# getonboard_diagnostico.py
# Inspecciona la estructura JSON real que devuelve la API de GetOnBoard
# para ajustar las claves del scraper principal.
# =============================================================================

import requests
import json
from pathlib import Path

DIAG_DIR = Path("/media/carlos-a-cicconi/Common1/Repositorios-Ing.Carlos-Cicconi/Trabajo_Final_MCD/data/raw/diagnostico")
DIAG_DIR.mkdir(parents=True, exist_ok=True)

API_SEARCH = "https://www.getonbrd.com/api/v0/search/jobs"

headers = {
    "Accept":          "application/json",
    "Accept-Language": "es-AR,es;q=0.9",
    "User-Agent":      "Mozilla/5.0 (X11; Linux x86_64) Chrome/122.0.0.0 Safari/537.36",
}

print("🔍 Consultando API de GetOnBoard...")
response = requests.get(
    API_SEARCH,
    params={"query": "Data Scientist", "per_page": 3, "page": 1},
    headers=headers,
    timeout=20
)

print(f"HTTP Status: {response.status_code}")

data = response.json()

# Guardar JSON completo para inspección
output_file = DIAG_DIR / "getonboard_raw.json"
output_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
print(f"JSON completo guardado en: {output_file}")

# Mostrar estructura del primer item
items = data.get("data", [])
print(f"\nTotal items en 'data': {len(items)}")
print(f"Claves raíz del JSON  : {list(data.keys())}")

if items:
    item = items[0]
    print(f"\n=== ESTRUCTURA DEL PRIMER ITEM ===")
    print(f"Claves del item       : {list(item.keys())}")
    print(f"item['id']            : {item.get('id')}")
    print(f"item['type']          : {item.get('type')}")

    attrs = item.get("attributes", {})
    print(f"\nClaves en 'attributes': {list(attrs.keys())}")
    print(f"\nValores clave:")
    for k in ["title", "description", "remote_modality", "country",
              "min_salary", "max_salary", "salary_currency",
              "published_at", "created_at", "slug", "seniority",
              "modality", "locations", "company_name"]:
        v = attrs.get(k, "⚠️  NO EXISTE")
        print(f"  attributes['{k}'] = {str(v)[:120]}")

    rels = item.get("relationships", {})
    print(f"\nClaves en 'relationships': {list(rels.keys())}")
    for k, v in rels.items():
        print(f"  relationships['{k}'] = {str(v)[:120]}")

    # Verificar si hay 'included' en la raíz
    included = data.get("included", [])
    print(f"\n'included' en raíz    : {len(included)} elementos")
    if included:
        print(f"  Primer included type: {included[0].get('type')}")
        print(f"  Primer included keys: {list(included[0].keys())}")
        print(f"  Primer included attrs: {list(included[0].get('attributes', {}).keys())}")

print("\n✅ Diagnóstico completo.")
