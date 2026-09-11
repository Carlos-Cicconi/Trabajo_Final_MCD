# Tabla Comparativa de Benchmarks — T7

**Fecha:** 2026-09-11 00:55  
**Corpus:** 1,402 ofertas  |  **Ground truth:** similitud SBERT ≥ p75  
**Umbral de relevancia (θ):** 0.0528  |  **N relevantes:** 351

## Métricas de evaluación

| Sistema | P@5 | P@10 | P@20 | nDCG@10 | nDCG@20 | MRR |
|---|---|---|---|---|---|---|
| Keyword Match | 0.400 | 0.400 | 0.600 | 0.344 | 0.509 | 0.333 |
| Jaccard BoW | 1.000 | 1.000 | 0.900 | 1.000 | 0.928 | 1.000 |
| TF-IDF | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| SBERT puro | 0.200 | 0.200 | 0.250 | 0.151 | 0.203 | 0.200 |
| SBERT Híbrido ⭐ | **0.400** | **0.500** | **0.400** | **0.457** | **0.401** | **0.500** |

## Mejora relativa del Sistema Propuesto (%)

| Baseline vs. SBERT Híbrido | P@5 | P@10 | P@20 | nDCG@10 | nDCG@20 | MRR |
|---|---|---|---|---|---|---|
| Keyword Match | +0.0% | +25.0% | -33.3% | +32.7% | -21.2% | +50.0% |
| Jaccard BoW | -60.0% | -50.0% | -55.6% | -54.3% | -56.8% | -50.0% |
| TF-IDF | -60.0% | -50.0% | -60.0% | -54.3% | -59.9% | -50.0% |
| SBERT puro | +100.0% | +150.0% | +60.0% | +202.0% | +97.7% | +150.0% |