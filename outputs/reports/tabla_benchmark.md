# Tabla Comparativa de Benchmarks — T7

**Fecha:** 2026-04-24 22:49  
**Corpus:** 1,279 ofertas  |  **Ground truth:** similitud SBERT ≥ p75  
**Umbral de relevancia (θ):** 0.0660  |  **N relevantes:** 320

## Métricas de evaluación

| Sistema | P@5 | P@10 | P@20 | nDCG@10 | nDCG@20 | MRR |
|---|---|---|---|---|---|---|
| Keyword Match | 0.400 | 0.600 | 0.400 | 0.477 | 0.376 | 0.333 |
| Jaccard BoW | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| TF-IDF | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| SBERT puro | 0.600 | 0.400 | 0.450 | 0.538 | 0.528 | 1.000 |
| SBERT Híbrido ⭐ | **1.000** | **0.800** | **0.650** | **0.860** | **0.738** | **1.000** |

## Mejora relativa del Sistema Propuesto (%)

| Baseline vs. SBERT Híbrido | P@5 | P@10 | P@20 | nDCG@10 | nDCG@20 | MRR |
|---|---|---|---|---|---|---|
| Keyword Match | +150.0% | +33.3% | +62.5% | +80.2% | +96.4% | +200.0% |
| Jaccard BoW | +0.0% | -20.0% | -35.0% | -14.0% | -26.2% | +0.0% |
| TF-IDF | +0.0% | -20.0% | -35.0% | -14.0% | -26.2% | +0.0% |
| SBERT puro | +66.7% | +100.0% | +44.4% | +59.8% | +39.8% | +0.0% |