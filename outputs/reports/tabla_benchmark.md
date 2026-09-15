# Tabla Comparativa de Benchmarks — T7

**Fecha:** 2026-09-15 16:11  
**Corpus:** 2,476 ofertas  |  **Ground truth:** similitud SBERT ≥ p75  
**Umbral de relevancia (θ):** 0.0543  |  **N relevantes:** 619

## Métricas de evaluación

| Sistema | P@5 | P@10 | P@20 | nDCG@10 | nDCG@20 | MRR |
|---|---|---|---|---|---|---|
| Keyword Match | 0.400 | 0.300 | 0.400 | 0.379 | 0.421 | 1.000 |
| Jaccard BoW | 1.000 | 1.000 | 0.900 | 1.000 | 0.926 | 1.000 |
| TF-IDF | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| SBERT puro | 0.200 | 0.200 | 0.200 | 0.155 | 0.173 | 0.200 |
| SBERT Híbrido ⭐ | **0.400** | **0.400** | **0.400** | **0.401** | **0.403** | **0.500** |

## Mejora relativa del Sistema Propuesto (%)

| Baseline vs. SBERT Híbrido | P@5 | P@10 | P@20 | nDCG@10 | nDCG@20 | MRR |
|---|---|---|---|---|---|---|
| Keyword Match | +0.0% | +33.3% | +0.0% | +5.9% | -4.2% | -50.0% |
| Jaccard BoW | -60.0% | -60.0% | -55.6% | -59.9% | -56.5% | -50.0% |
| TF-IDF | -60.0% | -60.0% | -60.0% | -59.9% | -59.7% | -50.0% |
| SBERT puro | +100.0% | +100.0% | +100.0% | +159.2% | +133.4% | +150.0% |