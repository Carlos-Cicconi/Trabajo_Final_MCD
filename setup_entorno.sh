#!/bin/bash
# =============================================================================
# setup_entorno.sh
# Tesis: "Sistema de Recomendación Laboral - Motor de Matching Inteligente"
# Alumno: Cicconi, Carlos Alberto - Maestría en Explotación de Datos (Austral)
# Descripción: Crea el entorno virtual e instala todas las dependencias del proyecto
# =============================================================================

set -e  # Detener si ocurre algún error

# --- Colores para output ---
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
RED='\033[0;31m'
NC='\033[0m' # Sin color

echo -e "${CYAN}"
echo "============================================================"
echo "   SETUP - Sistema de Recomendación Laboral (Tesis MCD)    "
echo "   Alumno: Cicconi, Carlos Alberto                         "
echo "   Universidad Austral - Maestría en Explotación de Datos  "
echo "============================================================"
echo -e "${NC}"

# --- Directorio base del proyecto ---
PROJECT_DIR="/media/carlos-a-cicconi/Common1/Repositorios-Ing.Carlos-Cicconi/Trabajo_Final_MCD"
VENV_NAME="venv_tesis"
VENV_PATH="$PROJECT_DIR/$VENV_NAME"
PYTHON_MIN="3.9"

# =============================================================================
# 1. VERIFICAR QUE ESTAMOS EN EL DIRECTORIO CORRECTO
# =============================================================================
echo -e "${YELLOW}[1/7] Verificando directorio del proyecto...${NC}"

if [ ! -d "$PROJECT_DIR" ]; then
    echo -e "${RED}❌ El directorio del proyecto no existe: $PROJECT_DIR${NC}"
    echo -e "   Creándolo automáticamente..."
    mkdir -p "$PROJECT_DIR"
fi

cd "$PROJECT_DIR"
echo -e "${GREEN}✅ Directorio OK: $PROJECT_DIR${NC}"

# =============================================================================
# 2. VERIFICAR VERSIÓN DE PYTHON E INSTALAR DEPENDENCIAS DEL SISTEMA
# =============================================================================
echo -e "\n${YELLOW}[2/7] Verificando versión de Python e instalando dependencias del sistema...${NC}"

if ! command -v python3 &> /dev/null; then
    echo -e "${RED}❌ Python3 no está instalado. Instalando...${NC}"
    sudo apt-get update && sudo apt-get install -y python3 python3-pip
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PYTHON_MAJOR=$(python3 -c 'import sys; print(sys.version_info.major)')
PYTHON_MINOR=$(python3 -c 'import sys; print(sys.version_info.minor)')
echo -e "${GREEN}✅ Python $PYTHON_VERSION detectado${NC}"

# Verificar versión mínima
if [ "$PYTHON_MAJOR" -lt 3 ] || { [ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 9 ]; }; then
    echo -e "${RED}❌ Se requiere Python 3.9 o superior. Versión actual: $PYTHON_VERSION${NC}"
    exit 1
fi

# Instalar python3.X-venv y python3.X-pip según la versión detectada
# (necesario en Ubuntu/Debian con Python 3.12+)
echo -e "   Instalando python3.${PYTHON_MINOR}-venv y python3.${PYTHON_MINOR}-dev..."
sudo apt-get update -qq
sudo apt-get install -y \
    python3.${PYTHON_MINOR}-venv \
    python3.${PYTHON_MINOR}-dev \
    python3-pip \
    build-essential \
    libssl-dev \
    libffi-dev 2>/dev/null || {
        echo -e "${YELLOW}⚠️  Algunos paquetes opcionales no se pudieron instalar, continuando...${NC}"
    }
echo -e "${GREEN}✅ Dependencias del sistema instaladas${NC}"

# =============================================================================
# 3. CREAR ENTORNO VIRTUAL
# =============================================================================
echo -e "\n${YELLOW}[3/7] Creando entorno virtual '$VENV_NAME'...${NC}"

if [ -d "$VENV_PATH" ]; then
    echo -e "${YELLOW}⚠️  El entorno virtual ya existe. Recreando...${NC}"
    rm -rf "$VENV_PATH"
fi

python3 -m venv "$VENV_PATH"
echo -e "${GREEN}✅ Entorno virtual creado en: $VENV_PATH${NC}"

# Activar el entorno virtual
source "$VENV_PATH/bin/activate"
echo -e "${GREEN}✅ Entorno virtual activado${NC}"

# =============================================================================
# 4. ACTUALIZAR PIP Y HERRAMIENTAS BASE
# =============================================================================
echo -e "\n${YELLOW}[4/7] Actualizando pip y herramientas base...${NC}"

pip install --upgrade pip setuptools wheel --quiet
echo -e "${GREEN}✅ pip, setuptools y wheel actualizados${NC}"

# =============================================================================
# 5. INSTALAR DEPENDENCIAS DEL PROYECTO  (compatibles con Python 3.12)
# =============================================================================
echo -e "\n${YELLOW}[5/7] Instalando dependencias del proyecto...${NC}"
echo -e "   Esto puede tardar varios minutos (PyTorch + sentence-transformers son grandes)...\n"

# --- Scraping y HTTP ---
echo -e "   📦 Instalando librerías de scraping..."
pip install --quiet \
    requests==2.32.3 \
    beautifulsoup4==4.12.3 \
    lxml==5.3.1 \
    scrapy==2.12.0 \
    fake-useragent==2.1.0 \
    httpx==0.28.1 \
    playwright==1.62.0

# Playwright necesita descargar los binarios de navegador (Chromium) y,
# en Linux, sus dependencias de sistema. Usado por los scrapers de
# bumeran, zonajobs, workana y opcionempleo (requieren JS rendering).
echo -e "   🔽 Instalando navegador Chromium para Playwright..."
python3 -m playwright install --with-deps chromium

# --- Manejo de datos ---
echo -e "   📦 Instalando librerías de datos..."
pip install --quiet \
    pandas==2.2.3 \
    numpy==2.2.4 \
    openpyxl==3.1.5

# --- NLP y preprocesamiento de texto ---
echo -e "   📦 Instalando librerías de NLP (spaCy, NLTK, scikit-learn)..."
pip install --quiet \
    nltk==3.9.1 \
    spacy==3.8.4 \
    scikit-learn==1.6.1

# --- PyTorch CPU — debe instalarse ANTES que sentence-transformers ---
echo -e "   📦 Instalando PyTorch CPU (puede tardar varios minutos)..."
pip install --quiet \
    torch==2.6.0 \
    --index-url https://download.pytorch.org/whl/cpu

# --- Embeddings y modelos Transformers (T5 del plan de trabajo) ---
echo -e "   📦 Instalando transformers y sentence-transformers..."
pip install --quiet \
    transformers==4.51.3 \
    sentence-transformers==4.1.0 \
    huggingface-hub==0.30.2

# --- Almacenamiento y utilidades ---
echo -e "   📦 Instalando SQLAlchemy y utilidades..."
pip install --quiet \
    sqlalchemy==2.0.40 \
    tqdm==4.67.1 \
    python-dotenv==1.1.0

# --- Visualización y dashboard (T9: prototipo Streamlit) ---
echo -e "   📦 Instalando Streamlit y visualización..."
pip install --quiet \
    streamlit==1.44.1 \
    plotly==6.0.1 \
    matplotlib==3.10.1 \
    seaborn==0.13.2

# --- Jupyter (T3: exploración y análisis de datos) ---
echo -e "   📦 Instalando Jupyter..."
pip install --quiet \
    jupyter==1.1.1 \
    ipykernel==6.29.5

echo -e "${GREEN}✅ Todas las dependencias instaladas${NC}"

# =============================================================================
# 6. DESCARGAR MODELOS NLP NECESARIOS
# =============================================================================
echo -e "\n${YELLOW}[6/7] Descargando modelos NLP...${NC}"

# Modelo de spaCy para español
echo -e "   🔽 Descargando modelo de spaCy para español (es_core_news_md)..."
python3 -m spacy download es_core_news_md --quiet

# Recursos de NLTK para español
echo -e "   🔽 Descargando recursos de NLTK..."
python3 -c "
import nltk
nltk.download('punkt', quiet=True)
nltk.download('punkt_tab', quiet=True)
nltk.download('stopwords', quiet=True)
nltk.download('wordnet', quiet=True)
nltk.download('averaged_perceptron_tagger', quiet=True)
print('✅ Recursos NLTK descargados')
"

# Modelo de embeddings multilingüe para español (HuggingFace)
# paraphrase-multilingual-MiniLM-L12-v2:
#   - Soporta 50+ idiomas incluyendo español
#   - Liviano (~420 MB) y rápido en CPU
#   - Ideal para similitud semántica entre CVs y ofertas (T5/T6 del plan)
echo -e "   🔽 Descargando modelo de embeddings multilingüe (puede tardar ~5 min)..."
echo -e "      Modelo: paraphrase-multilingual-MiniLM-L12-v2"
python3 -c "
from sentence_transformers import SentenceTransformer
import os

MODEL_NAME = 'paraphrase-multilingual-MiniLM-L12-v2'
MODELS_DIR = os.path.join('$PROJECT_DIR', 'data', 'modelos')
os.makedirs(MODELS_DIR, exist_ok=True)

print(f'   Descargando {MODEL_NAME}...')
model = SentenceTransformer(MODEL_NAME, cache_folder=MODELS_DIR)

# Verificación rápida: vectorizar una oración de prueba
test = model.encode('Desarrollador Python con experiencia en Data Science')
print(f'   ✅ Modelo cargado correctamente. Dimensión del vector: {len(test)}')
print(f'   📁 Guardado en: {MODELS_DIR}')
"

echo -e "${GREEN}✅ Modelos NLP listos${NC}"

# =============================================================================
# 7. CREAR ESTRUCTURA DE CARPETAS DEL PROYECTO
# =============================================================================
echo -e "\n${YELLOW}[7/7] Creando estructura de carpetas del proyecto...${NC}"

mkdir -p "$PROJECT_DIR/data/raw"
mkdir -p "$PROJECT_DIR/data/processed"
mkdir -p "$PROJECT_DIR/data/embeddings"
mkdir -p "$PROJECT_DIR/data/cvs"
mkdir -p "$PROJECT_DIR/data/modelos"
mkdir -p "$PROJECT_DIR/src/scraping"
mkdir -p "$PROJECT_DIR/src/preprocessing"
mkdir -p "$PROJECT_DIR/src/models"
mkdir -p "$PROJECT_DIR/src/matching"
mkdir -p "$PROJECT_DIR/notebooks"
mkdir -p "$PROJECT_DIR/outputs/resultados"
mkdir -p "$PROJECT_DIR/dashboard"
mkdir -p "$PROJECT_DIR/logs"

echo -e "${GREEN}✅ Estructura de carpetas creada${NC}"

# =============================================================================
# GENERAR requirements.txt
# =============================================================================
echo -e "\n   📄 Generando requirements.txt..."
pip freeze > "$PROJECT_DIR/requirements.txt"
echo -e "${GREEN}✅ requirements.txt generado${NC}"

# =============================================================================
# GENERAR SCRIPT DE ACTIVACIÓN RÁPIDA
# =============================================================================
cat > "$PROJECT_DIR/activar_entorno.sh" << 'ACTIVATE_EOF'
#!/bin/bash
# Script rápido para activar el entorno virtual del proyecto
PROJECT_DIR="/media/carlos-a-cicconi/Common1/Repositorios-Ing.Carlos-Cicconi/Trabajo_Final_MCD"
source "$PROJECT_DIR/venv_tesis/bin/activate"
cd "$PROJECT_DIR"
echo "✅ Entorno activado. Estás en: $(pwd)"
echo "   Python: $(python --version)"
echo "   Para desactivar: deactivate"
ACTIVATE_EOF
chmod +x "$PROJECT_DIR/activar_entorno.sh"

# =============================================================================
# RESUMEN FINAL
# =============================================================================
echo -e "\n${CYAN}"
echo "============================================================"
echo "   ✅ SETUP COMPLETADO EXITOSAMENTE"
echo "============================================================"
echo ""
echo "  Estructura del proyecto:"
echo "  $PROJECT_DIR/"
echo "  ├── venv_tesis/           ← Entorno virtual Python"
echo "  ├── data/"
echo "  │   ├── raw/              ← Datos crudos del scraper"
echo "  │   ├── processed/        ← Datos limpios"
echo "  │   ├── embeddings/       ← Vectores generados"
echo "  │   ├── modelos/          ← Modelo HuggingFace descargado"
echo "  │   └── cvs/              ← CVs para testing"
echo "  ├── src/"
echo "  │   ├── scraping/         ← T2: Scripts de scraping"
echo "  │   ├── preprocessing/    ← T4: Preprocesamiento NLP"
echo "  │   ├── models/           ← T5: Modelos de embeddings"
echo "  │   └── matching/         ← T6: Motor de similitud"
echo "  ├── notebooks/            ← T3: EDA con Jupyter"
echo "  ├── dashboard/            ← T9: Prototipo Streamlit"
echo "  ├── outputs/              ← Resultados y métricas"
echo "  ├── activar_entorno.sh    ← Activación rápida"
echo "  └── requirements.txt      ← Dependencias del proyecto"
echo ""
echo "  Para comenzar a trabajar:"
echo "  $ source activar_entorno.sh"
echo ""
echo "  Para correr el scraper:"
echo "  $ python src/scraping/linkedin_scraper.py"
echo ""
echo "  Para abrir Jupyter:"
echo "  $ jupyter notebook notebooks/"
echo "============================================================"
echo -e "${NC}"

deactivate
