#!/bin/bash
# Script rápido para activar el entorno virtual del proyecto
PROJECT_DIR="/media/carlos-a-cicconi/Common1/Repositorios-Ing.Carlos-Cicconi/Trabajo_Final_MCD"
source "$PROJECT_DIR/venv_tesis/bin/activate"
cd "$PROJECT_DIR"
echo "✅ Entorno activado. Estás en: $(pwd)"
echo "   Python: $(python --version)"
echo "   Para desactivar: deactivate"
