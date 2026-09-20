# Tabla Comparativa de Benchmarks — T7

**Fecha:** 2026-09-19 12:27  
**Corpus:** 2,497 ofertas  |  **Ground truth:** similitud SBERT ≥ p75  
**Umbral de relevancia (θ):** 0.6158  |  **N relevantes:** 625

## Métricas de evaluación

| Sistema | P@5 | P@10 | P@20 | nDCG@10 | nDCG@20 | MRR |
|---|---|---|---|---|---|---|
| Keyword Match | 0.400 | 0.600 | 0.600 | 0.462 | 0.504 | 0.250 |
| Jaccard BoW | 0.800 | 0.800 | 0.900 | 0.845 | 0.900 | 1.000 |
| TF-IDF | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| SBERT puro | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| SBERT Híbrido ⭐ | **1.000** | **1.000** | **1.000** | **1.000** | **1.000** | **1.000** |

## Mejora relativa del Sistema Propuesto (%)

| Baseline vs. SBERT Híbrido | P@5 | P@10 | P@20 | nDCG@10 | nDCG@20 | MRR |
|---|---|---|---|---|---|---|
| Keyword Match | +150.0% | +66.7% | +66.7% | +116.6% | +98.3% | +300.0% |
| Jaccard BoW | +25.0% | +25.0% | +11.1% | +18.3% | +11.1% | +0.0% |
| TF-IDF | +0.0% | +0.0% | +0.0% | +0.0% | +0.0% | +0.0% |
| SBERT puro | +0.0% | +0.0% | +0.0% | +0.0% | +0.0% | +0.0% |