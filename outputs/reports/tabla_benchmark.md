# Tabla Comparativa de Benchmarks — T7

**Fecha:** 2026-09-16 08:45  
**Corpus:** 1,335 ofertas  |  **Ground truth:** similitud SBERT ≥ p75  
**Umbral de relevancia (θ):** 0.3236  |  **N relevantes:** 334

## Métricas de evaluación

| Sistema | P@5 | P@10 | P@20 | nDCG@10 | nDCG@20 | MRR |
|---|---|---|---|---|---|---|
| Keyword Match | 0.200 | 0.200 | 0.300 | 0.176 | 0.256 | 0.333 |
| Jaccard BoW | 0.000 | 0.300 | 0.300 | 0.208 | 0.246 | 0.167 |
| TF-IDF | 0.200 | 0.100 | 0.250 | 0.220 | 0.281 | 1.000 |
| SBERT puro | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| SBERT Híbrido ⭐ | **1.000** | **1.000** | **0.950** | **1.000** | **0.966** | **1.000** |

## Mejora relativa del Sistema Propuesto (%)

| Baseline vs. SBERT Híbrido | P@5 | P@10 | P@20 | nDCG@10 | nDCG@20 | MRR |
|---|---|---|---|---|---|---|
| Keyword Match | +400.0% | +400.0% | +216.7% | +467.2% | +276.7% | +200.0% |
| Jaccard BoW | +inf% | +233.3% | +216.7% | +380.1% | +293.3% | +499.9% |
| TF-IDF | +400.0% | +900.0% | +280.0% | +354.3% | +244.0% | +0.0% |
| SBERT puro | +0.0% | +0.0% | -5.0% | +0.0% | -3.4% | +0.0% |