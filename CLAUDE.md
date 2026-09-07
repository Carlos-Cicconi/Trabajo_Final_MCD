# CLAUDE.md

Este archivo brinda guía a Claude Code (claude.ai/code) al trabajar con código de este repositorio.

## Descripción general del proyecto

Proyecto de tesis de maestría ("Sistema de Recomendación Laboral - Motor de Matching Inteligente", Maestría en Explotación de Datos, Universidad Austral — Carlos Cicconi). Es un pipeline de punta a punta que scrapea ofertas laborales de múltiples portales de empleo argentinos/LatAm, las limpia y vectoriza (TF-IDF y embeddings SBERT), las matchea contra el CV de un candidato, y presenta los resultados en un dashboard de Streamlit. El plan de trabajo está organizado en tareas numeradas (T2–T9) que mapean directamente a módulos de `src/` y notebooks.

## Configuración del entorno

```bash
source activar_entorno.sh      # activa venv_tesis y cambia al directorio raíz del proyecto
```

`setup_entorno.sh` es el instalador desde cero (crea `venv_tesis`, instala dependencias fijadas desde `requirements.txt`, descarga el modelo de spaCy `es_core_news_md`, recursos de NLTK, y el modelo SentenceTransformer `paraphrase-multilingual-MiniLM-L12-v2` en `data/modelos/`). Volver a ejecutarlo borra y recrea `venv_tesis`, así que no correrlo sin necesidad.

No hay comandos de lint/test/build configurados — este es un pipeline de investigación/tesis, no una aplicación empaquetada. Validar los cambios ejecutando manualmente el script/notebook correspondiente.

`.gitignore` excluye `venv_tesis/` y `data/modelos/` (el modelo SBERT descargado) junto con extensiones binarias comunes (`*.so`, `*.bin`, `*.pt`, `*.safetensors`) — estos no deben volver a commitearse, se regeneran con `setup_entorno.sh`.

## Arquitectura

### 1. Scraping (`src/scraping/`, tarea T2)

Un módulo scraper por portal de empleo (`bumeran_scraper.py`, `computrabajo_scraper.py`, `getonboard_scraper.py`, `jobleads_scraper.py`, `jobomas_scraper.py`, `jobrapido_scraper.py`, `linkedin_scraper.py`, `opcionempleo_scraper.py`, `workana_scraper.py`, `zonajobs_scraper.py`). Cada uno es ejecutable de forma independiente (`if __name__ == "__main__"`) y registra su propio log en `logs/<scraper>_<timestamp>.log`.

`run_scraping.py` es el orquestador que corre todos (o un subconjunto) de los scrapers en secuencia y consolida su salida:

```bash
python src/scraping/run_scraping.py                        # corre todos los scrapers con queries por defecto
python src/scraping/run_scraping.py --cv data/cvs/keywords.json   # deriva queries + ubicación desde un CV
python src/scraping/run_scraping.py --solo linkedin         # corre un solo scraper
python src/scraping/run_scraping.py --skip workana          # omite uno o más scrapers
python src/scraping/run_scraping.py --solo-consolidar       # re-consolida sin scrapear
python src/scraping/run_scraping.py --max-dias 60           # cambia la ventana de "antigüedad máxima" (90 días por defecto)
```

Cada scraper por fuente escribe su salida cruda directamente en `data/raw/` siguiendo una convención de nombres fija: `ofertas_<sitio>_<fecha>.csv` (snapshot diario), `<sitio>.db` (SQLite acumulativo, leído por el orquestador para consolidar), y `stats_<sitio>_<fecha_hora>.json` (estadísticas por corrida). `SCRAPERS_REGISTRO` de `run_scraping.py` mapea cada módulo scraper a su archivo `<sitio>.db`.

Reglas de consolidación aplicadas por el orquestador: las ofertas más antiguas que `--max-dias` se descartan, y los duplicados (por `id_consolidado` = hash de fuente + job_id) nunca se vuelven a insertar. Las salidas son acumulativas, no se sobrescriben:
- `data/raw/consolidado/ofertas_consolidadas.csv` / `.db` (SQLite) — dataset consolidado maestro, columnas canónicas definidas en `COLUMNAS` en `run_scraping.py`.
- `data/raw/consolidado/run_<timestamp>.json` — reporte por corrida.
- `data/raw/diagnostico/` — snapshots de HTML crudo por fuente/query, conservados para depurar roturas de scrapers.

`cv_keyword_extractor.py` lee un CV en PDF y produce el `keywords.json` que consume `run_scraping.py --cv`: extrae palabras clave de búsqueda (matching contra vocabulario curado, o vía `--llm` llamando a la API de Claude) y la ubicación del candidato (regex + diccionario de provincias/ciudades de Argentina) para sesgar/filtrar las búsquedas.

Nota: varios scripts (ej. `run_scraping.py`) hardcodean `BASE_DIR` como la ruta absoluta de este repo en lugar de derivarla de `__file__` — tenerlo en cuenta si el repo se mueve o clona en otro lugar.

### 2. Preprocesamiento, modelado, matching (`src/preprocessing/`, `src/models/`, `src/matching/`, tareas T4–T6)

Actualmente son directorios vacíos/placeholder — la lógica correspondiente vive en los notebooks (ver abajo) y todavía no fue extraída a módulos reutilizables. Al productivizar la lógica de los notebooks, es acá donde debe ir.

### 3. Notebooks (`notebooks/`, tareas T3–T9)

Secuenciales, cada uno correspondiente a un capítulo/entregable de la tesis:
- `T3_Limpieza_y_EDA.ipynb` — limpieza y análisis exploratorio de los datos scrapeados consolidados.
- `T4_Preprocesamiento_TF-IDF.ipynb` — preprocesamiento de texto y vectorización TF-IDF.
- `T5_Embeddings_SBERT.ipynb` — generación de embeddings SBERT (escribe en `data/embeddings/`).
- `T6_Motor_Matching.ipynb` — el motor de matching/similitud entre CV y ofertas.
- `T7_Benchmark.ipynb` — benchmarking de los distintos enfoques de matching.
- `T8_Analisis_Tuning.ipynb` — análisis y tuning de hiperparámetros.
- `T9_Prototipo_Dashboard.ipynb` — prototipado para el dashboard de Streamlit.

Los artefactos derivados de estos notebooks se guardan en `data/processed/`, `data/embeddings/`, y `outputs/` (`models/`, `rankings/`, `reports/`, `figures/`, `resultados/`).

### 4. Dashboard (`dashboard/app.py`, tarea T9)

Prototipo Streamlit para demostrar el motor de matching.

```bash
streamlit run dashboard/app.py
```

Las rutas se resuelven en relación al directorio padre de `dashboard/app.py` (`_ROOT`), leyendo desde `data/processed/`, `data/embeddings/`, `outputs/models/`, `outputs/rankings/`, `outputs/reports/`. Usa TF-IDF + PCA + similitud coseno (scikit-learn) junto con Plotly para visualización.

### Resumen del flujo de datos

```
scrapers (src/scraping/*_scraper.py)
  → run_scraping.py consolida → data/raw/consolidado/ofertas_consolidadas.{csv,db}
  → notebooks T3 (limpieza/EDA) → data/processed/
  → notebooks T4/T5 (TF-IDF, SBERT) → data/embeddings/, outputs/models/
  → notebook T6 (motor de matching) → outputs/rankings/, outputs/reports/
  → dashboard/app.py lee data/processed/, data/embeddings/, outputs/
```

Lado de entrada del CV: `cv_keyword_extractor.py` (PDF → `data/cvs/keywords.json`) alimenta tanto a `run_scraping.py --cv` (queries de búsqueda/ubicación) como al motor de matching (perfil del candidato).

## Contexto histórico de decisiones

`docs/contexto_tesis/CONTEXTO_CONVERSACIONES.md` sintetiza decisiones de diseño y arquitectura tomadas en conversaciones previas con Claude AI durante el desarrollo de este proyecto — por qué se descartaron ciertas fuentes/enfoques (ej. Indeed por bloqueo Cloudflare), estructuras de datos/API reales de cada scraper, bugs ya corregidos y sus causas raíz, y el razonamiento detrás de la reorganización de carpetas actual. Consultarlo antes de tocar scrapers, el extractor de keywords del CV, o la estructura de carpetas del proyecto.
