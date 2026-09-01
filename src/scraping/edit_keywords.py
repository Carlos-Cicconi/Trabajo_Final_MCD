#!/usr/bin/env python3
# =============================================================================
# edit_keywords.py
# Tesis: "Sistema de Recomendación Laboral - Motor de Matching Inteligente"
# Alumno: Cicconi, Carlos Alberto - Maestría en Explotación de Datos (Austral)
# Módulo: T2 - Diseño y Desarrollo del Web Scraper
#
# DESCRIPCIÓN:
#   Editor interactivo del archivo keywords.json generado por
#   cv_keyword_extractor.py. Permite agregar y eliminar keywords
#   y ubicaciones antes de ejecutar el orquestador de scraping.
#
#   El archivo keywords.json tiene esta estructura:
#     - ubicacion:      ciudad, provincia, país, linkedin_location
#     - keywords:       lista de labels (ej: "Data Scientist")
#     - search_queries: lista de {keyword, label, location}
#                       cada keyword genera 3 búsquedas automáticamente:
#                         1. ubicación exacta del candidato
#                         2. país (remoto local)
#                         3. Worldwide (remoto global)
#
# USO:
#   python edit_keywords.py data/cvs/keywords.json
#
# DEPENDENCIAS: ninguna (solo stdlib)
# =============================================================================

import json
import re
import sys
import shutil
from pathlib import Path
from datetime import datetime


# =============================================================================
# COLORES ANSI para la terminal
# =============================================================================

class C:
    RESET  = "\033[0m"
    BOLD   = "\033[1m"
    CYAN   = "\033[96m"
    GREEN  = "\033[92m"
    YELLOW = "\033[93m"
    RED    = "\033[91m"
    GREY   = "\033[90m"
    WHITE  = "\033[97m"

def bold(t):    return f"{C.BOLD}{t}{C.RESET}"
def cyan(t):    return f"{C.CYAN}{t}{C.RESET}"
def green(t):   return f"{C.GREEN}{t}{C.RESET}"
def yellow(t):  return f"{C.YELLOW}{t}{C.RESET}"
def red(t):     return f"{C.RED}{t}{C.RESET}"
def grey(t):    return f"{C.GREY}{t}{C.RESET}"


# =============================================================================
# UTILIDADES
# =============================================================================

def limpiar_pantalla():
    print("\033[2J\033[H", end="")

def separador(char="─", ancho=60):
    print(grey(char * ancho))

def pausar():
    input(grey("\n  Presioná Enter para continuar..."))

def normalizar_slug(label: str) -> str:
    """Convierte un label a slug URL (minúsculas, guiones, sin tildes)."""
    mapa = str.maketrans("áéíóúÁÉÍÓÚñÑüÜ", "aeiouAEIOUnNuU")
    slug = label.lower().translate(mapa)
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    return re.sub(r"\s+", "-", slug.strip())

def input_con_default(prompt: str, default: str = "") -> str:
    """Pide input mostrando el valor por defecto entre corchetes."""
    if default:
        valor = input(f"{prompt} [{default}]: ").strip()
        return valor if valor else default
    return input(f"{prompt}: ").strip()

def confirmar(pregunta: str) -> bool:
    resp = input(f"{yellow('?')} {pregunta} (s/n): ").strip().lower()
    return resp in ("s", "si", "sí", "y", "yes")


# =============================================================================
# CARGA Y GUARDADO DEL JSON
# =============================================================================

def cargar_json(ruta: Path) -> dict:
    with open(ruta, encoding="utf-8") as f:
        return json.load(f)

def guardar_json(ruta: Path, data: dict):
    """Guarda el JSON y crea un backup automático con timestamp."""
    # Backup antes de sobreescribir
    backup = ruta.with_suffix(f".backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    shutil.copy2(ruta, backup)

    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(green(f"\n  ✅ Guardado: {ruta.name}"))
    print(grey(f"     Backup:   {backup.name}"))


# =============================================================================
# LÓGICA DE QUERIES
# Cada keyword se expande en 3 búsquedas: ubicación exacta, país y Worldwide.
# =============================================================================

def queries_de_keyword(label: str, keyword: str, ubicacion: dict) -> list:
    """Genera las 3 search_queries estándar para un keyword dado."""
    loc_exacta = ubicacion.get("linkedin_location", "Argentina")
    loc_pais   = ubicacion.get("pais", "Argentina")

    queries = [
        {"keyword": keyword, "label": label,
         "location": loc_exacta},
    ]
    if loc_exacta != loc_pais:
        queries.append(
            {"keyword": keyword, "label": f"{label} (Remoto AR)",
             "location": loc_pais}
        )
    queries.append(
        {"keyword": keyword, "label": f"{label} (Remoto)",
         "location": "Worldwide"}
    )
    return queries

def keywords_unicas(data: dict) -> list:
    """
    Extrae los keywords únicos del JSON leyendo el slug REAL desde
    search_queries (no regenerándolo con normalizar_slug, que puede diferir).
    Devuelve lista de (label, keyword_slug_real) deduplicada,
    ignorando las variantes Remoto/Remoto AR.
    """
    vistos, resultado = set(), []
    for q in data.get("search_queries", []):
        lbl = q.get("label", "")
        kw  = q.get("keyword", "")
        # Ignorar las variantes generadas automáticamente
        if "(Remoto" in lbl:
            continue
        if kw not in vistos:
            vistos.add(kw)
            resultado.append((lbl, kw))
    return resultado

def slug_real_de_label(label: str, data: dict) -> set:
    """
    Devuelve el/los slugs reales asociados a un label en search_queries.
    Compara ignorando sufijos de remoto para encontrar la entrada base.
    """
    slugs = set()
    for q in data.get("search_queries", []):
        q_base = (q.get("label", "")
                  .replace(" (Remoto AR)", "")
                  .replace(" (Remoto)", "")
                  .strip())
        if q_base == label:
            slugs.add(q.get("keyword", ""))
    return slugs if slugs else {normalizar_slug(label)}

def ubicaciones_unicas(data: dict) -> list:
    """Devuelve las ubicaciones únicas presentes en search_queries."""
    return sorted(set(
        q.get("location", "")
        for q in data.get("search_queries", [])
        if q.get("location")
    ))

def reconstruir_queries(data: dict) -> list:
    """
    Reconstruye search_queries completo a partir de keywords[] y ubicacion.
    Se llama después de cualquier modificación para mantener consistencia.
    """
    ubicacion = data.get("ubicacion", {})
    queries   = []
    for label in data.get("keywords", []):
        slug = normalizar_slug(label)
        queries.extend(queries_de_keyword(label, slug, ubicacion))
    return queries


# =============================================================================
# PANTALLAS DEL MENÚ
# =============================================================================

def mostrar_estado(data: dict, ruta: Path):
    """Muestra el resumen del estado actual del JSON."""
    limpiar_pantalla()
    ub = data.get("ubicacion", {})
    kws = data.get("keywords", [])
    queries = data.get("search_queries", [])

    print(bold(cyan("\n  ╔══════════════════════════════════════════════╗")))
    print(bold(cyan(  "  ║      EDITOR DE KEYWORDS — SCRAPING TESIS     ║")))
    print(bold(cyan(  "  ╚══════════════════════════════════════════════╝")))
    print(f"\n  {grey('Archivo:')} {ruta.name}")
    separador()

    print(f"\n  {bold('📍 Ubicación actual:')}")
    print(f"     Ciudad   : {ub.get('ciudad') or grey('(no detectada)')}")
    print(f"     Provincia: {ub.get('provincia') or grey('(no detectada)')}")
    print(f"     País     : {ub.get('pais', 'Argentina')}")
    print(f"     LinkedIn : {cyan(ub.get('linkedin_location', 'Argentina'))}")

    separador()
    print(f"\n  {bold('🔑 Keywords activas')} ({len(kws)} keywords → "
          f"{len(queries)} búsquedas totales):\n")

    for i, label in enumerate(kws, 1):
        slug = normalizar_slug(label)
        print(f"  {grey(str(i).rjust(2))}. {label:<35} {grey(slug)}")

    separador()


def menu_principal(data: dict, ruta: Path) -> str:
    mostrar_estado(data, ruta)
    print(f"\n  {bold('¿Qué querés hacer?')}\n")
    print(f"  {cyan('1')}. Agregar keyword")
    print(f"  {cyan('2')}. Eliminar keyword")
    print(f"  {cyan('3')}. Cambiar ubicación")
    print(f"  {cyan('4')}. Ver todas las search_queries")
    print(f"  {cyan('5')}. Guardar y salir")
    print(f"  {cyan('6')}. Salir sin guardar")
    separador()
    return input(f"\n  Opción: ").strip()


# =============================================================================
# OPERACIONES
# =============================================================================

def agregar_keyword(data: dict):
    limpiar_pantalla()
    print(bold(cyan("\n  ── AGREGAR KEYWORD ──────────────────────────────\n")))

    label = input_con_default("  Label (nombre visible, ej: 'Machine Learning Engineer')")
    if not label:
        print(red("  Label vacío. Operación cancelada."))
        pausar(); return

    # Proponer slug automático pero permitir editarlo
    slug_sugerido = normalizar_slug(label)
    print(f"\n  {grey('Slug sugerido:')} {slug_sugerido}")
    slug = input_con_default("  Slug para URL (Enter para aceptar el sugerido)",
                             slug_sugerido)
    slug = normalizar_slug(slug) if slug != slug_sugerido else slug_sugerido

    # Verificar si ya existe
    existentes = [normalizar_slug(k) for k in data.get("keywords", [])]
    if slug in existentes:
        print(yellow(f"\n  ⚠️  El keyword '{slug}' ya existe."))
        pausar(); return

    # Vista previa de las queries que se generarán
    ubicacion = data.get("ubicacion", {})
    nuevas    = queries_de_keyword(label, slug, ubicacion)
    print(f"\n  {bold('Se agregarán estas búsquedas:')}")
    for q in nuevas:
        print(f"    • {q['label']:<40} → {grey(q['location'])}")

    if not confirmar("\n  ¿Confirmar?"):
        print(grey("  Cancelado.")); pausar(); return

    data["keywords"].append(label)
    data["search_queries"].extend(nuevas)

    print(green(f"\n  ✅ '{label}' agregado con {len(nuevas)} búsquedas."))
    pausar()


def eliminar_keyword(data: dict):
    limpiar_pantalla()
    print(bold(cyan("\n  ── ELIMINAR KEYWORD ─────────────────────────────\n")))

    kws = data.get("keywords", [])
    if not kws:
        print(red("  No hay keywords para eliminar.")); pausar(); return

    for i, label in enumerate(kws, 1):
        print(f"  {cyan(str(i))}. {label}")

    print(f"\n  {grey('0. Cancelar')}")
    separador()

    try:
        opcion = int(input("\n  Número a eliminar: ").strip())
    except ValueError:
        print(red("  Entrada inválida.")); pausar(); return

    if opcion == 0:
        print(grey("  Cancelado.")); pausar(); return
    if opcion < 1 or opcion > len(kws):
        print(red("  Número fuera de rango.")); pausar(); return

    label = kws[opcion - 1]

    # Buscar el slug real en search_queries comparando por label base
    # (ignorando sufijos como "(Remoto)" o "(Remoto AR)").
    # Esto evita el bug donde normalizar_slug("BI / Analytics") genera
    # "bi-analytics" pero el JSON real tiene "business-intelligence".
    slugs_reales = set()
    for q in data["search_queries"]:
        q_label_base = (q.get("label", "")
                        .replace(" (Remoto AR)", "")
                        .replace(" (Remoto)", "")
                        .strip())
        if q_label_base == label:
            slugs_reales.add(q.get("keyword", ""))

    if not slugs_reales:
        slugs_reales = {normalizar_slug(label)}

    queries_a_eliminar = [
        q for q in data["search_queries"]
        if q.get("keyword") in slugs_reales
    ]
    print(f"\n  {yellow('⚠️  Se eliminarán:')}")
    print(f"     Keyword  : {label}  {grey('(slug: ' + ', '.join(slugs_reales) + ')')}")
    print(f"     Búsquedas: {len(queries_a_eliminar)} entradas en search_queries")
    for q in queries_a_eliminar:
        print(f"     {grey(chr(8226))} {q['label']:<45} {grey(q['location'])}")

    if not confirmar("\n  ¿Confirmar eliminación?"):
        print(grey("  Cancelado.")); pausar(); return

    data["keywords"].remove(label)
    data["search_queries"] = [
        q for q in data["search_queries"]
        if q.get("keyword") not in slugs_reales
    ]

    print(green(f"\n  ✅ '{label}' eliminado ({len(queries_a_eliminar)} búsquedas quitadas)."))
    pausar()


def cambiar_ubicacion(data: dict):
    limpiar_pantalla()
    print(bold(cyan("\n  ── CAMBIAR UBICACIÓN ────────────────────────────\n")))

    ub = data.get("ubicacion", {})
    print(f"  Ubicación actual: {cyan(ub.get('linkedin_location', 'Argentina'))}\n")

    print(f"  {bold('¿Qué querés modificar?')}\n")
    print(f"  {cyan('1')}. Cambiar ciudad")
    print(f"  {cyan('2')}. Cambiar provincia")
    print(f"  {cyan('3')}. Cambiar país")
    print(f"  {cyan('4')}. Editar linkedin_location directamente")
    print(f"  {cyan('5')}. Agregar una ubicación extra a todas las queries")
    print(f"  {cyan('6')}. Eliminar una ubicación de las queries")
    print(f"  {cyan('0')}. Volver")
    separador()

    opcion = input("\n  Opción: ").strip()

    if opcion == "0":
        return

    elif opcion == "1":
        nueva = input_con_default("  Nueva ciudad", ub.get("ciudad", ""))
        if nueva:
            ub["ciudad"] = nueva
            _reconstruir_linkedin_location(ub)
            _actualizar_location_en_queries(data, ub)
            print(green(f"  ✅ Ciudad actualizada a '{nueva}'"))

    elif opcion == "2":
        nueva = input_con_default("  Nueva provincia", ub.get("provincia", ""))
        if nueva:
            ub["provincia"] = nueva
            _reconstruir_linkedin_location(ub)
            _actualizar_location_en_queries(data, ub)
            print(green(f"  ✅ Provincia actualizada a '{nueva}'"))

    elif opcion == "3":
        nuevo = input_con_default("  Nuevo país", ub.get("pais", "Argentina"))
        if nuevo:
            # Actualizar el país también en las queries de "Remoto AR"
            old_pais = ub.get("pais", "Argentina")
            ub["pais"] = nuevo
            _reconstruir_linkedin_location(ub)
            for q in data["search_queries"]:
                if q.get("location") == old_pais:
                    q["location"] = nuevo
                    q["label"] = q["label"].replace(
                        f"(Remoto AR)", f"(Remoto AR)"
                    )
            _actualizar_location_en_queries(data, ub)
            print(green(f"  ✅ País actualizado a '{nuevo}'"))

    elif opcion == "4":
        nueva = input_con_default(
            "  LinkedIn location (ej: 'Buenos Aires, Argentina')",
            ub.get("linkedin_location", "")
        )
        if nueva:
            old_loc = ub.get("linkedin_location", "")
            ub["linkedin_location"] = nueva
            # Actualizar en todas las queries que usaban la ubicación exacta anterior
            for q in data["search_queries"]:
                if q.get("location") == old_loc:
                    q["location"] = nueva
            print(green(f"  ✅ linkedin_location actualizado a '{nueva}'"))

    elif opcion == "5":
        print(f"\n  {bold('Agregar una ubicación extra a todas las queries')}")
        print(f"  {grey('Ejemplos: Córdoba, Argentina | Buenos Aires, Argentina | Montevideo, Uruguay')}\n")
        nueva_loc = input("  Nueva ubicación: ").strip()
        if not nueva_loc:
            print(red("  Ubicación vacía. Cancelado.")); pausar(); return

        sufijo = input_con_default(
            "  Sufijo para el label (ej: 'CABA' → 'Data Scientist (CABA)')",
            nueva_loc.split(",")[0].strip()
        )

        kws = keywords_unicas(data)
        queries_nuevas = []
        for label, slug in kws:
            queries_nuevas.append({
                "keyword":  slug,
                "label":    f"{label} ({sufijo})",
                "location": nueva_loc,
            })

        print(f"\n  {bold('Se agregarán:')}")
        for q in queries_nuevas[:3]:
            print(f"    • {q['label']:<45} → {grey(q['location'])}")
        if len(queries_nuevas) > 3:
            print(f"    {grey(f'  ... y {len(queries_nuevas)-3} más')}")

        if confirmar(f"\n  ¿Agregar {len(queries_nuevas)} búsquedas con ubicación '{nueva_loc}'?"):
            data["search_queries"].extend(queries_nuevas)
            print(green(f"\n  ✅ {len(queries_nuevas)} búsquedas agregadas."))

    elif opcion == "6":
        locs = ubicaciones_unicas(data)
        if not locs:
            print(red("  No hay ubicaciones.")); pausar(); return

        print(f"\n  {bold('Ubicaciones actuales:')}\n")
        for i, loc in enumerate(locs, 1):
            n = sum(1 for q in data["search_queries"] if q.get("location") == loc)
            print(f"  {cyan(str(i))}. {loc:<45} {grey(f'({n} búsquedas)')}")
        print(f"  {grey('0. Cancelar')}")
        separador()

        try:
            idx = int(input("\n  Número a eliminar: ").strip())
        except ValueError:
            print(red("  Entrada inválida.")); pausar(); return

        if idx == 0:
            print(grey("  Cancelado.")); pausar(); return
        if idx < 1 or idx > len(locs):
            print(red("  Número fuera de rango.")); pausar(); return

        loc_a_eliminar = locs[idx - 1]
        n = sum(1 for q in data["search_queries"] if q.get("location") == loc_a_eliminar)

        if confirmar(f"\n  ¿Eliminar las {n} búsquedas con ubicación '{loc_a_eliminar}'?"):
            data["search_queries"] = [
                q for q in data["search_queries"]
                if q.get("location") != loc_a_eliminar
            ]
            print(green(f"\n  ✅ {n} búsquedas eliminadas."))

    else:
        print(red("  Opción inválida."))

    data["ubicacion"] = ub
    pausar()


def _reconstruir_linkedin_location(ub: dict):
    """Reconstruye linkedin_location a partir de ciudad, provincia y país."""
    partes = []
    if ub.get("ciudad"):
        partes.append(ub["ciudad"])
    if ub.get("provincia") and ub.get("provincia") != ub.get("ciudad"):
        partes.append(ub["provincia"])
    if ub.get("pais"):
        partes.append(ub["pais"])
    ub["linkedin_location"] = ", ".join(partes) if partes else "Argentina"


def _actualizar_location_en_queries(data: dict, ub: dict):
    """
    Actualiza la ubicación exacta (no Remoto AR ni Worldwide) en todas las
    queries que usaban la ubicación anterior del candidato.
    """
    nueva_loc = ub.get("linkedin_location", "Argentina")
    pais      = ub.get("pais", "Argentina")

    for q in data["search_queries"]:
        loc = q.get("location", "")
        # Solo tocar las que NO son país ni Worldwide
        if loc not in (pais, "Worldwide") and "(Remoto" not in q.get("label", ""):
            q["location"] = nueva_loc


def ver_queries(data: dict):
    limpiar_pantalla()
    print(bold(cyan("\n  ── TODAS LAS SEARCH QUERIES ─────────────────────\n")))

    queries = data.get("search_queries", [])
    if not queries:
        print(grey("  (sin queries)"))
        pausar(); return

    # Agrupar por keyword para mejor legibilidad
    grupos: dict = {}
    for q in queries:
        kw = q.get("keyword", "")
        grupos.setdefault(kw, []).append(q)

    for kw, qs in grupos.items():
        print(f"  {bold(cyan(kw))}")
        for q in qs:
            print(f"    {q['label']:<45} {grey('→')} {q['location']}")
        print()

    print(grey(f"  Total: {len(queries)} búsquedas en {len(grupos)} keywords"))
    pausar()


# =============================================================================
# MAIN
# =============================================================================

def main():
    if len(sys.argv) < 2:
        print(red("\n  Uso: python edit_keywords.py <ruta/al/keywords.json>\n"))
        sys.exit(1)

    ruta = Path(sys.argv[1])
    if not ruta.exists():
        print(red(f"\n  Archivo no encontrado: {ruta}\n"))
        sys.exit(1)

    data     = cargar_json(ruta)
    modified = False

    while True:
        opcion = menu_principal(data, ruta)

        if opcion == "1":
            agregar_keyword(data)
            modified = True

        elif opcion == "2":
            eliminar_keyword(data)
            modified = True

        elif opcion == "3":
            cambiar_ubicacion(data)
            modified = True

        elif opcion == "4":
            ver_queries(data)

        elif opcion == "5":
            if modified:
                guardar_json(ruta, data)
            else:
                print(grey("\n  Sin cambios para guardar."))
                pausar()
            break

        elif opcion == "6":
            if modified and not confirmar("  Hay cambios sin guardar. ¿Salir igual?"):
                continue
            print(grey("\n  Saliendo sin guardar.\n"))
            break

        else:
            print(red("  Opción inválida."))
            pausar()


if __name__ == "__main__":
    main()
