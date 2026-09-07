# Contexto histórico de decisiones (conversaciones con Claude AI)

Este documento sintetiza el "por qué" detrás de decisiones de diseño y arquitectura tomadas
en 5 conversaciones previas con Claude AI (no Claude Code) durante el desarrollo de este
proyecto de tesis. No repite código (eso está en el repo) — se enfoca en motivos, descartes
y hallazgos que no son derivables leyendo el código actual. La fuente cruda (`conversations.json`,
exportada desde Claude AI) ya no se conserva en el repo; este documento es la síntesis definitiva.

## 01 — Carga de plan de trabajo, scrapers, orquestador

### LinkedIn
No hay API pública gratuita para ofertas de trabajo. Se usa el endpoint público no autenticado
`linkedin.com/jobs-guest/jobs/api/...` (HTML parseable, sin login). Elegido por ser el enfoque
más simple y suficiente para el corpus académico.

### Indeed — descartado
Bloquea sistemáticamente desde Argentina con **HTTP 403 a nivel de infraestructura de red
(Cloudflare, TLS/JA3 fingerprint)**. Se probaron en orden: `requests` (403 inmediato) →
Playwright headless (pasa el 403 pero cae en un challenge de Cloudflare "Security Check") →
`curl_cffi` (impersona TLS de Chrome real, tampoco lo resolvió de forma sostenida). Conclusión:
es un bloqueo deliberado por región, no un problema de código. **Reemplazado por GetOnBoard**
(API JSON pública, 100% tech, sin protecciones agresivas, salario frecuente).

### Computrabajo
HTML scraping puro (no hay API). `job_id` = hash MD5 de la URL (no hay ID explícito publicado).
Selectores CSS defensivos en cascada porque el sitio cambia clases sin aviso.

### GetOnBoard
API JSON pública. La estructura real (descubierta por diagnóstico, no documentada) difiere de
lo asumido inicialmente: `id` es un slug de texto (no numérico), `company` es un objeto anidado
dentro de `attributes` (no en `relationships`), `seniority`/`modality` vienen como
`{'data': {'id': N, 'type': ...}}`, `published_at` es timestamp Unix, y no existe campo de
moneda separado (siempre USD, hardcodeado).

### Bumeran / ZonaJobs
Ambos son **la misma plataforma** (grupo Jobint): el HTML de Bumeran contiene
`window.SITE_ID` con valores `"ZJAR"` (ZonaJobs AR) y `"BMVE"` (Bumeran), y hay assets
cruzados entre ambos (ej. logo de ZonaJobs embebido en cards de Bumeran). Implicación para T3:
**puede haber ofertas duplicadas entre ambas fuentes** — conviene deduplicación cruzada por
título+empresa+fecha además de por `job_id`.
Técnicamente son SPA React puras (sin SSR) con **styled-components** — clases CSS son hashes
sin semántica (`sc-cMfllC`, etc.), por lo que el `job_id` se extrae vía regex sobre el atributo
`aria-labelledby` (`job-posting-(\d+)`), que es estable aunque cambien las clases.
Requieren Playwright (SPA), pero a diferencia de Indeed **no tienen Cloudflare**, por lo que
Playwright headless estándar funciona sin fricción.

### Workana
Fuente elegida deliberadamente para cubrir un segmento que ninguna otra fuente cubre: empleo
freelance/por proyecto (vs. relación de dependencia). Motivos: descripciones de proyecto muy
ricas en vocabulario técnico (mejor calidad de embeddings), API/HTML público sin restricciones,
cobertura regional LATAM. Se descartaron como alternativas de menor prioridad: AR.Talent.com
(agregador, poco diferenciador), InfoJobs Argentina (mismo grupo que Bumeran/ZonaJobs → alto
solapamiento), Hired/Turing/Toptal (orientadas a mercado global/inglés, poco relevantes para AR).
Técnicamente Workana no usa React/Vue/Nuxt como se sospechaba inicialmente — es **HTML clásico
con clases CSS semánticas estables** (`project-item`, `project-title`, `a.skill`, etc.).

### OpcionEmpleo
Usa **Cloudflare Turnstile** (challenge JS interactivo, distinto del bloqueo TLS de Indeed).
A diferencia de Indeed, Turnstile **sí se resuelve con Playwright** porque requiere ejecución
de JS, no impersonación TLS a bajo nivel.

### Jobomas, Jobrapido, JobLeads
- **Jobomas**: HTML SSR. Bug detectado en producción: las URLs de detalle vienen con prefijo
  `./` y se concatenaban mal con `BASE_URL` → 404s. Además el modo "queries inyectadas desde CV"
  no traía el campo `slug` (solo `keyword`/`label`) → el scraper debía generar el slug
  dinámicamente en vez de asumir que siempre viene en la query.
- **Jobrapido**: el más simple de los diez, HTML muy limpio sin frameworks.
- **JobLeads**: Next.js SSR. El listado funciona con `requests`, pero las páginas de **detalle**
  devuelven HTTP 403 sistemático → requieren Playwright solo para el detalle (modo híbrido,
  una sola instancia de browser reutilizada por eficiencia).

### Bug recurrente: nombres de archivo de diagnóstico
Varios scrapers (Jobrapido, JobLeads) rompían al guardar el HTML de diagnóstico porque el
`label` de la keyword (ej. "BI / Analytics") contiene `/`, interpretado como separador de
directorio. Fix aplicado: sanitizar con `safe_filename()` antes de usar el label como nombre
de archivo. Si se agregan scrapers nuevos con el mismo patrón de diagnóstico, replicar el fix.

### Orquestador (`run_scraping.py`)
- Deduplicación: `id_consolidado = fuente::job_id`. Una misma oferta que aparece en Bumeran y
  ZonaJobs se guarda **dos veces** (una por fuente) — es intencional, no un bug, porque el
  keyword/fecha de scrap puede diferir entre ambas.
- Normalización de fechas: cada fuente usa un formato distinto (ISO, timestamp Unix, texto
  relativo tipo "Publicado hace más de 15 días" o "Hace 7 horas", o directamente "N/A" en
  Computrabajo). Se normalizan todas a `fecha_pub_dt` (ISO). Si no se puede interpretar, la
  oferta se incluye igual y se marca `fecha_pub_dt = "sin-fecha"` (no se descarta por defecto).
- El script detecta su propia ubicación con `Path(__file__).resolve().parent` para funcionar
  tanto desde la raíz como desde `src/scraping/`. El logging se configura de forma diferida
  (`setup_logging()` llamado desde `main()`, no a nivel de módulo) porque el disco externo del
  autor tiene errores de I/O intermitentes (`/dev/sdb1: Can't lookup blockdev`); si falla la
  escritura en `logs/`, hace fallback automático a `/tmp`.

## 02 — Extractor de palabras clave desde CV (dos conversaciones)

Hubo dos intentos paralelos de esta tarea en conversaciones separadas de Claude AI (el usuario
no tenía forma de que una conversación viera el trabajo de la otra — cada conversación de
Claude AI es independiente y sin acceso a otras, a menos que se suban los archivos al
"proyecto" compartido). La segunda conversación es la que consolidó el resultado real usado
en el repo (`cv_keyword_extractor.py`).

### Diseño del extractor
- **Lectura del PDF en cascada**: `pdfplumber` primero, `pypdf` como respaldo, texto plano
  como último recurso. Importante: si cae al fallback de texto plano sobre un PDF real (no
  legible como texto), el resultado son bytes binarios sin sentido — hay que verificar que
  el PDF se haya podido parsear correctamente cuando el output da 0 keywords con muchos
  caracteres leídos (fue un bug real: "142k chars pero 0 matches").
- **Vocabulario curado** (~25 entradas con variantes por rol, ej. "control de gestión" /
  "controlling" / "planificación estratégica" → mismo label): elegido en vez de NLP puro
  porque es más confiable para el dominio específico y no requiere API key.
- **Modo `--llm`** (opcional): usa la API de Claude para extracción semántica más rica cuando
  el vocabulario curado no captura algún término inusual.
- **Extracción de ubicación**: analiza solo las primeras 5 líneas del CV (cabecera) para evitar
  que ciudades mencionadas en experiencia laboral contaminen la detección (bug real: detectaba
  "Buenos Aires" en vez de "Santa Fe" por menciones de Campana/San Nicolás en el historial
  laboral). La provincia se infiere desde la ciudad vía un mapa `CIUDAD_A_PROVINCIA` cuando el
  CV no la menciona explícitamente (ej. "Rosario, 2000 Argentina" sin decir "Santa Fe").
- **Generación de queries con 3 variantes por keyword**: ubicación exacta (presencial),
  país completo (remoto local), y "Worldwide" (remoto global) — así una sola keyword cubre
  presencial + remoto AR + remoto global.

### Integración con el orquestador (`run_scraping.py --cv`)
- La inyección de queries distingue tres casos por scraper: **LinkedIn** recibe las 3 variantes
  con `location`; **Workana** recibe solo `keyword`→`search` sin location (no tiene ese filtro);
  **el resto** recibe keyword+label deduplicado (sin triplicar innecesariamente las búsquedas).
- Bug post-integración: cuando las queries vienen inyectadas desde el CV, no traen el campo
  `slug` que algunos scrapers (Jobomas) esperaban — hubo que generar el slug dinámicamente
  desde el keyword en vez de asumir que siempre está presente en la query.

### Editor interactivo de keywords (`edit_keywords.py`)
Permite agregar/eliminar keywords y editar ubicación antes de correr el scraping, sin tocar
el JSON a mano. Hace backup automático con timestamp antes de cada guardado
(`keywords.backup_YYYYMMDD_HHMMSS.json`).

Bug real corregido: al eliminar una keyword desde el menú, el script regeneraba el slug con
`normalizar_slug(label)` para buscarla en `search_queries`, pero ese slug podía no coincidir
con el slug real guardado en el JSON (ej. label "BI / Analytics" → `normalizar_slug` genera
`bi-analytics`, pero el JSON real tenía `business-intelligence` porque `cv_keyword_extractor.py`
usa su propio mapeo de vocabulario). Fix: buscar el slug real leyendo directamente las queries
del JSON por label (ignorando sufijos `(Remoto)`/`(Remoto AR)`), usando `normalizar_slug()`
solo como fallback si el label no aparece en ninguna query.

**Lección general**: cualquier lugar del código que necesite mapear un label de keyword a su
slug debe usar el slug tal como está persistido en `keywords.json`, no regenerarlo con una
función de normalización — pueden divergir.

## 03 — Recolección, limpieza, exploración y notebooks T3–T9

### T3 — Limpieza y EDA
Sobre el consolidado real (2.576 filas × 17 columnas en la corrida usada como referencia):
- Deduplicación en dos niveles: por URL, luego por título+empresa+keyword, priorizando la fila
  con descripción más larga y la fuente de "mayor calidad".
- Normalización: `keyword_norm` unifica slugs y labels en 19 categorías canónicas;
  `modalidad_norm` colapsa a 4 categorías (Presencial/Remoto/Híbrido/Presencial y remoto);
  `ubicacion_norm` unifica variantes de "Capital Federal"/"CABA"/etc.
- Salidas para T4: `ofertas_limpias.csv` (corpus completo limpio) y
  `ofertas_con_descripcion.csv` (subconjunto con descripción ≥50 chars — es el input real del
  motor de matching). En la corrida de referencia: 1.942 y 1.279 filas respectivamente. Estas
  salidas ya no tienen `fecha_pub`/`fecha_consolidado`, y las columnas normalizadas pasan a
  llamarse `ubicacion`/`keyword`/`modalidad` (sin sufijo `_norm`).

### T4 — Preprocesamiento y TF-IDF
- El corpus es **bilingüe** (~66% español, ~33% inglés) — se detecta idioma por heurística de
  palabras funcionales y se corre spaCy con ambos modelos (`es_core_news_md` + `en_core_web_md`).
- Pipeline NLP: limpieza HTML/URLs → tokenización spaCy → filtro POS (solo sustantivos, verbos,
  adjetivos) → lematización → stop-words de dominio laboral.
- El CV se pondera por sección: habilidades ×3, resumen ×2, experiencia ×2, educación ×1.
- Vectorizador TF-IDF: bigramas, `min_df=2`, `sublinear_tf=True`.
- Limitación documentada explícitamente (justifica T5/SBERT): hay gaps semánticos evidentes,
  ej. "mejora continua" vs "continuous improvement" da similitud 0 con TF-IDF puro.
- `pickle5` **no es necesario** — Python 3.12 ya soporta el protocolo 5 nativamente en `pickle`
  de la stdlib; no instalarlo si aparece como dependencia sugerida en algún lugar viejo.
- Salidas para T5/T6: `tfidf_vectorizer.pkl`, `tfidf_matrix.pkl`, `ofertas_preprocesadas.csv`,
  `tfidf_ranking_baseline.csv`.

### T5 — Embeddings SBERT
- Modelo elegido: `paraphrase-multilingual-MiniLM-L12-v2` (384 dims, 50+ idiomas, ~470MB) —
  ya cacheado localmente en `data/modelos/` desde `setup_entorno.sh`; se carga desde caché sin
  red en ejecuciones posteriores.
- **Por qué SBERT y no Doc2Vec**: Doc2Vec requiere inferencia iterativa costosa para documentos
  nuevos (ej. un CV nunca visto) y necesita un corpus propio grande para aprender
  representaciones de calidad — con ~1.279 ofertas no alcanza. SBERT viene pre-entrenado sobre
  miles de millones de oraciones y encodea texto nuevo en milisegundos.
- Chunking de textos largos (mediana ~2000 chars): fragmentos de 800 chars con solapamiento de
  100, luego mean pooling sobre los embeddings de los chunks.
- **Bug crítico de congelamiento de PC**: la función original de chunking buscaba puntos/saltos
  de línea para cortar "limpio", pero si el texto no tenía puntuación, el puntero de avance
  podía quedar estancado (`inicio = corte - overlap` sin garantía de avance) → bucle infinito.
  Fix: `inicio = max(fin - overlap, inicio + 1)`, que garantiza avance mínimo de 1 posición
  siempre. **Cualquier lógica de chunking futura debe garantizar explícitamente que el puntero
  de posición avance en cada iteración**, sin depender de heurísticas de corte "bonito".
- Prioridad de fuente del texto del CV: PDF real > texto lematizado (`cv_texto_limpio.txt`).
  Los lemas (salida de spaCy en T4) son subóptimos para SBERT porque el modelo fue entrenado
  con oraciones naturales, no bolsas de lemas.

### T6 — Motor de matching
- Score híbrido: SBERT + TF-IDF ponderados (normalización Min-Max antes de combinar). Peso
  base usado: 70% SBERT / 30% TF-IDF.
- Métricas (Precision@K, nDCG@K, MRR) implementadas desde cero sin librerías externas. Como no
  hay ground truth de juicio humano, se usa como proxy de relevancia el **percentil 75 del
  score final** — limitación documentada explícitamente en el propio notebook/tesis.
- Correlación de Spearman baja entre SBERT y TF-IDF se usa como argumento de que el híbrido
  aporta información complementaria (no redundante).

### T7 — Benchmark
5 sistemas comparados (evolución agregada tras feedback del usuario, que notó la ausencia de
SBERT "puro" como control): B0 Keyword Match → B1 Jaccard BoW → B2 TF-IDF → B3 SBERT puro →
S SBERT Híbrido. La razón de incluir B3 explícitamente: aísla la contribución real del 30% de
TF-IDF en el híbrido (si B3≈S, el componente TF-IDF no aporta; si S>B3, se justifica la mezcla).
Ground truth: relevancia binaria (SBERT ≥ percentil 75) y graduada (3 niveles), con nota
metodológica explícita sobre el sesgo de usar SBERT como su propio proxy de evaluación.

### T8 — Tuning
- Tuning A: grid search de peso SBERT/TF-IDF (0.00–1.00, paso 0.05) maximizando nDCG@10.
- Tuning B: sensibilidad del umbral de relevancia (percentiles p50–p90).
- Tuning C: grid search de hiperparámetros TF-IDF (`ngram_range` × `min_df` × `max_features`,
  27 configuraciones), usando el peso óptimo de A.
- Patrón defensivo recurrente en estos notebooks: variables intermedias (`em_n`, `tf_n`,
  `w_opt`, `res_tfidf`) pueden no existir si una celda anterior no corrió o si las similitudes
  no estaban disponibles — las celdas posteriores las reconstruyen con guardas
  `'variable' not in dir()` en vez de asumir que siempre existen.

### T9 — Dashboard
- `app.py` y el notebook de documentación comparten el mismo bug/fix: la carga de datos
  filtraba por columna `descripcion`, pero los archivos de **ranking resumido**
  (`ranking_final_optimizado.csv`, `ranking_final_combinado.csv`) no tienen esa columna
  (se omite deliberadamente en T6/T7/T8 para reducir tamaño). Fix aplicado en ambos: priorizar
  `ofertas_preprocesadas.csv`/`ofertas_con_descripcion.csv` (que sí tienen descripción); si solo
  hay un archivo de ranking, enriquecerlo con merge por `id_consolidado`; como último recurso,
  crear la columna vacía en vez de crashear. **Cualquier cambio a la lógica de carga de datos
  en T9/`app.py` debe preservar esta cascada** porque no todos los artefactos tienen las mismas
  columnas.

## 04 — Reorganización de carpetas del proyecto

La estructura actual del repo (`data/processed/`, `data/embeddings/`, `outputs/{models,
rankings,reports,figures}/`, `dashboard/app.py`) es el resultado de una reorganización
deliberada ejecutada vía `reorganizar_proyecto.sh` (script generado ad-hoc, no versionado
necesariamente) a partir de una estructura más plana y desordenada. Decisiones clave tomadas:

- `run_scraping.py` se movió de la raíz a ubicación consistente con el resto de scrapers.
- Duplicados entre `notebooks/` y `data/` (CSVs, CV, `keywords.json`) se eliminaron, dejando
  una única copia canónica (en `data/cvs/`, `data/processed/`, etc.) — el script comparaba
  contenido antes de borrar y renombraba a `.bak` si divergían en vez de perder datos.
- Todos los notebooks (T3–T9) y `dashboard/app.py` fueron editados para resolver rutas de forma
  **absoluta y relativa a la ubicación del propio archivo** (`Path(__file__).resolve().parent...`
  o bloque `_ROOT`/`_PROC`/`_EMBEDDINGS`/`_MODELS`/`_RANKINGS`/`_REPORTS`/`_FIGS` al inicio de
  cada notebook), en vez de rutas relativas al directorio de trabajo (`Path("archivo.csv")`)
  que rompían según desde dónde se ejecutara. Este patrón de bloque de rutas al inicio del
  archivo es la convención esperada para cualquier notebook o script nuevo que se agregue.
- Carpetas vacías (`src/matching/`, `src/models/`, `src/preprocessing/`) se dejaron como
  placeholders intencionales para productivizar lógica de notebooks más adelante (ver
  CLAUDE.md, sección Arquitectura).
