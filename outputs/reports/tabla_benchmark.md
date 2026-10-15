# Tabla Comparativa de Benchmarks — T7

**Fecha:** 2026-10-14 16:15  
**Corpus:** 3,423 ofertas  |  **Ground truth:** similitud SBERT ≥ p75  
**Umbral de relevancia (θ):** 0.6291  |  **N relevantes:** 856

## Métricas de evaluación

| Sistema | P@5 | P@10 | P@20 | nDCG@10 | nDCG@20 | MRR |
|---|---|---|---|---|---|---|
| Keyword Match | 1.000 | 0.700 | 0.650 | 0.786 | 0.726 | 1.000 |
| Jaccard BoW | 1.000 | 0.800 | 0.800 | 0.848 | 0.826 | 1.000 |
| TF-IDF | 1.000 | 1.000 | 0.950 | 1.000 | 0.963 | 1.000 |
| SBERT puro | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| SBERT Híbrido ⭐ | **1.000** | **1.000** | **1.000** | **1.000** | **1.000** | **1.000** |

## Mejora relativa del Sistema Propuesto (%)

| Baseline vs. SBERT Híbrido | P@5 | P@10 | P@20 | nDCG@10 | nDCG@20 | MRR |
|---|---|---|---|---|---|---|
| Keyword Match | +0.0% | +42.9% | +53.8% | +27.2% | +37.7% | +0.0% |
| Jaccard BoW | +0.0% | +25.0% | +25.0% | +17.9% | +21.1% | +0.0% |
| TF-IDF | +0.0% | +0.0% | +5.3% | +0.0% | +3.9% | +0.0% |
| SBERT puro | +0.0% | +0.0% | +0.0% | +0.0% | +0.0% | +0.0% |