# Tabla Comparativa de Benchmarks — T7

**Fecha:** 2026-09-15 23:44  
**Corpus:** 1,335 ofertas  |  **Ground truth:** similitud SBERT ≥ p75  
**Umbral de relevancia (θ):** 0.0597  |  **N relevantes:** 334

## Métricas de evaluación

| Sistema | P@5 | P@10 | P@20 | nDCG@10 | nDCG@20 | MRR |
|---|---|---|---|---|---|---|
| Keyword Match | 0.200 | 0.100 | 0.100 | 0.139 | 0.128 | 0.500 |
| Jaccard BoW | 1.000 | 0.800 | 0.900 | 0.857 | 0.908 | 1.000 |
| TF-IDF | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| SBERT puro | 0.200 | 0.200 | 0.300 | 0.173 | 0.256 | 0.250 |
| SBERT Híbrido ⭐ | **0.600** | **0.700** | **0.700** | **0.597** | **0.633** | **0.500** |

## Mejora relativa del Sistema Propuesto (%)

| Baseline vs. SBERT Híbrido | P@5 | P@10 | P@20 | nDCG@10 | nDCG@20 | MRR |
|---|---|---|---|---|---|---|
| Keyword Match | +200.0% | +600.0% | +600.0% | +329.4% | +394.5% | +0.0% |
| Jaccard BoW | -40.0% | -12.5% | -22.2% | -30.4% | -30.3% | -50.0% |
| TF-IDF | -40.0% | -30.0% | -30.0% | -40.3% | -36.7% | -50.0% |
| SBERT puro | +200.0% | +250.0% | +133.3% | +244.4% | +147.7% | +100.0% |