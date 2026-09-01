#!/usr/bin/env python3
"""
app.py — Prototipo / Dashboard de Demostración
T9: Sistema de Recomendación Laboral — Motor de Matching Inteligente

Tesis: Maestría en Explotación de Datos — Universidad Austral
Alumno: Cicconi, Carlos Alberto

Ejecución:
    streamlit run app.py

Dependencias (ya instaladas en venv_tesis):
    pip install streamlit plotly scikit-learn pandas numpy
"""

import re
import json
import pickle
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from sklearn.decomposition import PCA
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

warnings.filterwarnings("ignore")

# ══════════════════════════════════════════════════════════════════════════════
# RUTAS — resueltas desde la ubicación de este archivo
# Estructura esperada (post-reorganización):
#   dashboard/app.py                ← este archivo
#   data/processed/                 ← CSVs procesados
#   data/embeddings/                ← vectores .npy
#   outputs/models/                 ← modelos .pkl
#   outputs/rankings/               ← CSVs de ranking y tuning
#   outputs/reports/                ← JSONs de métricas y config
# ══════════════════════════════════════════════════════════════════════════════
_ROOT        = Path(__file__).resolve().parent.parent   # Trabajo_Final_MCD/
_PROCESSED   = _ROOT / "data"      / "processed"
_EMBEDDINGS  = _ROOT / "data"      / "embeddings"
_MODELS      = _ROOT / "outputs"   / "models"
_RANKINGS    = _ROOT / "outputs"   / "rankings"
_REPORTS     = _ROOT / "outputs"   / "reports"


# ══════════════════════════════════════════════════════════════════════════════
# CONFIGURACIÓN DE PÁGINA
# ══════════════════════════════════════════════════════════════════════════════
st.set_page_config(
    page_title="Motor de Matching Laboral",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ══════════════════════════════════════════════════════════════════════════════
# CONSTANTES
# ══════════════════════════════════════════════════════════════════════════════
COLORES_FUENTE = {
    "linkedin":     "#0077B5",
    "computrabajo": "#E8392A",
    "getonboard":   "#00C9A7",
    "bumeran":      "#FF6B35",
    "zonajobs":     "#4A90D9",
    "workana":      "#6C5CE7",
    "opcionempleo": "#FDCB6E",
    "jobrapido":    "#55EFC4",
    "jobomas":      "#74B9FF",
    "jobleads":     "#A29BFE",
}

W_DEFAULT = 0.70   # peso SBERT por defecto (T6 base)

# ══════════════════════════════════════════════════════════════════════════════
# CARGA DE DATOS (cacheada para performance)
# ══════════════════════════════════════════════════════════════════════════════
@st.cache_data(show_spinner="Cargando corpus de ofertas...")
def cargar_corpus() -> pd.DataFrame:
    # Archivos con descripcion completa (preferidos)
    ARCHIVOS_CON_DESC = [
        _PROCESSED / "ofertas_preprocesadas.csv",
        _PROCESSED / "ofertas_con_descripcion.csv",
    ]
    # Archivos de ranking (sin descripcion — se enriquecen si es posible)
    ARCHIVOS_RANKING = [
        _RANKINGS / "ranking_final_optimizado.csv",
        _RANKINGS / "ranking_final_combinado.csv",
    ]

    # Intento 1: archivos con descripcion
    for fname in ARCHIVOS_CON_DESC:
        if Path(fname).exists():
            df = pd.read_csv(fname, low_memory=False)
            if "descripcion" in df.columns:
                df = df[df["descripcion"].fillna("").str.len() > 50].copy()
            return df.reset_index(drop=True)

    # Intento 2: archivos de ranking + enriquecimiento con descripcion
    for fname in ARCHIVOS_RANKING:
        if Path(fname).exists():
            df = pd.read_csv(fname, low_memory=False)
            # Intentar agregar descripcion desde corpus completo
            for desc_fname in ARCHIVOS_CON_DESC:
                if Path(desc_fname).exists():
                    try:
                        df_desc = pd.read_csv(
                            desc_fname, low_memory=False,
                            usecols=lambda c: c in ["id_consolidado", "descripcion"]
                        )
                        if "id_consolidado" in df.columns and "id_consolidado" in df_desc.columns:
                            df = df.merge(df_desc, on="id_consolidado", how="left")
                    except Exception:
                        pass
                    break
            if "descripcion" not in df.columns:
                df["descripcion"] = ""
            return df.reset_index(drop=True)

    st.error(f"No se encontró el corpus. Verificá que los archivos existan en:\n  {_PROCESSED}\n  {_RANKINGS}")
    st.stop()

@st.cache_data(show_spinner="Cargando vectores SBERT...")
def cargar_embeddings():
    if (_EMBEDDINGS / "embeddings_ofertas.npy").exists() and (_EMBEDDINGS / "embedding_cv.npy").exists():
        emb_of = np.load(_EMBEDDINGS / "embeddings_ofertas.npy")
        emb_cv = np.load(_EMBEDDINGS / "embedding_cv.npy")
        return emb_of, emb_cv
    return None, None

@st.cache_resource(show_spinner="Cargando vectorizador TF-IDF...")
def cargar_tfidf():
    if (_MODELS / "tfidf_vectorizer.pkl").exists():
        with open(_MODELS / "tfidf_vectorizer.pkl", "rb") as f:
            return pickle.load(f)
    return None

@st.cache_data(show_spinner="Cargando métricas...")
def cargar_metricas():
    metricas = {}
    for fname in ["benchmark_metricas.json", "metricas_evaluacion.json"]:
        ruta = _REPORTS / fname
        if ruta.exists():
            with open(ruta) as f:
                metricas[fname] = json.load(f)
    return metricas

@st.cache_data(show_spinner="Cargando configuración óptima...")
def cargar_config():
    if (_REPORTS / "config_optima.json").exists():
        with open(_REPORTS / "config_optima.json") as f:
            return json.load(f)
    return {}

@st.cache_data(show_spinner="Cargando resultados de tuning...")
def cargar_tuning():
    if (_RANKINGS / "tuning_resultados.csv").exists():
        return pd.read_csv(_RANKINGS / "tuning_resultados.csv")
    return pd.DataFrame()

# ══════════════════════════════════════════════════════════════════════════════
# FUNCIONES DE UTILIDAD
# ══════════════════════════════════════════════════════════════════════════════
def normalizar_mm(serie: pd.Series) -> pd.Series:
    mn, mx = serie.min(), serie.max()
    return (serie - mn) / (mx - mn) if mx > mn else serie * 0

def leer_cv_pdf(archivo) -> str:
    """Lee texto de un PDF subido por el usuario."""
    try:
        import pdfplumber
        import io
        with pdfplumber.open(io.BytesIO(archivo.read())) as pdf:
            return "\n".join(p.extract_text() or "" for p in pdf.pages).strip()
    except Exception:
        pass
    try:
        from pypdf import PdfReader
        import io
        reader = PdfReader(io.BytesIO(archivo.read()))
        return "\n".join(p.extract_text() or "" for p in reader.pages).strip()
    except Exception as e:
        st.warning(f"No se pudo leer el PDF: {e}")
        return ""

def calcular_score_hibrido(df: pd.DataFrame,
                            w_embed: float,
                            emb_of, emb_cv_arr) -> pd.Series:
    """Recalcula el score híbrido con el w_embed dado."""
    tiene_embed = emb_of is not None and "similitud_embed" in df.columns
    tiene_tfidf = "similitud_tfidf" in df.columns

    if tiene_embed and tiene_tfidf:
        em_n = normalizar_mm(df["similitud_embed"].fillna(0))
        tf_n = normalizar_mm(df["similitud_tfidf"].fillna(0))
        return w_embed * em_n + (1 - w_embed) * tf_n
    elif tiene_embed:
        return normalizar_mm(df["similitud_embed"].fillna(0))
    elif tiene_tfidf:
        return normalizar_mm(df["similitud_tfidf"].fillna(0))
    elif "score_final" in df.columns:
        return normalizar_mm(df["score_final"].fillna(0))
    elif "score_optimizado" in df.columns:
        return normalizar_mm(df["score_optimizado"].fillna(0))
    else:
        return pd.Series(0.0, index=df.index)

def calcular_score_cv_nuevo(cv_texto: str, df: pd.DataFrame,
                             vectorizer, emb_of,
                             w_embed: float) -> pd.Series:
    """Calcula score para un CV nuevo subido por el usuario."""
    scores_tf = pd.Series(0.0, index=df.index)
    scores_em = pd.Series(0.0, index=df.index)

    col_doc = "texto_procesado" if "texto_procesado" in df.columns else "descripcion"
    corpus  = df[col_doc].fillna("").tolist()

    if vectorizer is not None:
        try:
            cv_vec    = vectorizer.transform([cv_texto])
            mat       = vectorizer.transform(corpus)
            sims      = cosine_similarity(cv_vec, mat).flatten()
            scores_tf = normalizar_mm(pd.Series(sims, index=df.index))
        except Exception:
            pass

    if emb_of is not None:
        try:
            from sentence_transformers import SentenceTransformer
            model     = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")
            emb_cv_n  = model.encode([cv_texto[:2000]], normalize_embeddings=True)[0]
            sims_em   = cosine_similarity(emb_cv_n.reshape(1,-1), emb_of).flatten()
            scores_em = normalizar_mm(pd.Series(sims_em, index=df.index))
        except Exception:
            pass

    return w_embed * scores_em + (1 - w_embed) * scores_tf

def badge_fuente(fuente: str) -> str:
    color = COLORES_FUENTE.get(str(fuente).lower(), "#888")
    return f'<span style="background:{color};color:white;padding:2px 8px;border-radius:10px;font-size:0.75em">{fuente}</span>'

def badge_modalidad(modalidad: str) -> str:
    colores = {"Remoto": "#00b894", "Híbrido": "#fdcb6e", "Presencial": "#e17055"}
    color   = colores.get(str(modalidad), "#888")
    return f'<span style="background:{color};color:white;padding:2px 8px;border-radius:10px;font-size:0.75em">{modalidad}</span>'

# ══════════════════════════════════════════════════════════════════════════════
# SIDEBAR
# ══════════════════════════════════════════════════════════════════════════════
def render_sidebar(config: dict) -> tuple:
    with st.sidebar:
        st.image("https://upload.wikimedia.org/wikipedia/commons/thumb/8/8e/"
                 "Artificial_Intelligence_-_Gemini_Generated_Image.jpg/240px-"
                 "Artificial_Intelligence_-_Gemini_Generated_Image.jpg",
                 use_container_width=True)

        st.title("🎯 Motor de Matching")
        st.caption("Sistema de Recomendación Laboral\nTesis MCD — Universidad Austral\nCicconi, Carlos A.")
        st.divider()

        # CV
        st.subheader("📄 CV del candidato")
        uso_cv = st.radio("Fuente del CV",
                          ["Usar CV por defecto (Cicconi)", "Subir mi propio CV (PDF)"],
                          index=0)
        cv_file = None
        if uso_cv == "Subir mi propio CV (PDF)":
            cv_file = st.file_uploader("Seleccioná tu CV", type=["pdf"])

        st.divider()

        # Ponderación
        st.subheader("⚙️ Parámetros del motor")
        w_opt = config.get("tuning_A", {}).get("w_embed", W_DEFAULT)
        w_embed = st.slider(
            "Peso de SBERT (semántico)",
            min_value=0.0, max_value=1.0,
            value=float(w_opt), step=0.05,
            help=f"Valor óptimo encontrado en Tuning A (T8): {w_opt:.2f}\n"
                 f"Peso TF-IDF = {1-w_opt:.2f}"
        )
        st.caption(f"SBERT: {w_embed:.0%}  |  TF-IDF: {1-w_embed:.0%}")

        # Filtros
        st.divider()
        st.subheader("🔍 Filtros")
        top_n = st.slider("Top-N ofertas a mostrar", 5, 50, 20, 5)

        return uso_cv, cv_file, w_embed, top_n

# ══════════════════════════════════════════════════════════════════════════════
# PÁGINA 1 — RECOMENDACIONES
# ══════════════════════════════════════════════════════════════════════════════
def pagina_recomendaciones(df: pd.DataFrame, emb_of, emb_cv,
                            vectorizer, uso_cv, cv_file,
                            w_embed: float, top_n: int):
    st.header("🏆 Ofertas Recomendadas")

    # Calcular scores
    cv_nuevo = uso_cv == "Subir mi propio CV (PDF)" and cv_file is not None

    if cv_nuevo:
        with st.spinner("Procesando tu CV y calculando similitudes..."):
            cv_texto = leer_cv_pdf(cv_file)
            if cv_texto:
                df["score_live"] = calcular_score_cv_nuevo(
                    cv_texto, df, vectorizer, emb_of, w_embed)
                col_score = "score_live"
                st.success(f"✅ CV procesado ({len(cv_texto):,} caracteres)")
            else:
                col_score = None
    else:
        df["score_live"] = calcular_score_hibrido(df, w_embed, emb_of, emb_cv)
        col_score = "score_live"

    if col_score is None:
        st.warning("No se pudo procesar el CV. Verificá que sea un PDF válido.")
        return

    # Filtros de columna
    col_f1, col_f2, col_f3 = st.columns(3)
    with col_f1:
        kws_disp = ["Todos"] + sorted(df["keyword"].dropna().unique().tolist()) \
                   if "keyword" in df.columns else ["Todos"]
        kw_sel = st.selectbox("Keyword", kws_disp)
    with col_f2:
        fuentes_disp = ["Todas"] + sorted(df["fuente"].dropna().unique().tolist()) \
                       if "fuente" in df.columns else ["Todas"]
        f_sel = st.selectbox("Fuente", fuentes_disp)
    with col_f3:
        mod_disp = ["Todas"] + sorted(df["modalidad"].dropna().unique().tolist()) \
                   if "modalidad" in df.columns else ["Todas"]
        m_sel = st.selectbox("Modalidad", mod_disp)

    # Aplicar filtros
    mask = pd.Series(True, index=df.index)
    if kw_sel != "Todos"  and "keyword"  in df.columns: mask &= df["keyword"]  == kw_sel
    if f_sel  != "Todas"  and "fuente"   in df.columns: mask &= df["fuente"]   == f_sel
    if m_sel  != "Todas"  and "modalidad" in df.columns: mask &= df["modalidad"] == m_sel

    df_fil = df[mask].sort_values(col_score, ascending=False).head(top_n).copy()
    df_fil["rank"] = range(1, len(df_fil) + 1)

    st.divider()

    # Métricas resumen
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Ofertas rankeadas",   f"{len(df[mask]):,}")
    m2.metric("Score máximo",        f"{df_fil[col_score].max():.4f}" if len(df_fil) else "—")
    m3.metric("Score promedio Top-N",f"{df_fil[col_score].mean():.4f}" if len(df_fil) else "—")
    m4.metric("Fuentes cubiertas",
              str(df_fil["fuente"].nunique()) if "fuente" in df_fil.columns else "—")

    st.divider()

    if df_fil.empty:
        st.warning("No hay ofertas con los filtros seleccionados.")
        return

    # Tarjetas de oferta
    for _, row in df_fil.iterrows():
        score   = row[col_score]
        titulo  = str(row.get("titulo",   "Sin título"))
        empresa = str(row.get("empresa",  "N/A"))
        kw      = str(row.get("keyword",  ""))
        fuente  = str(row.get("fuente",   ""))
        modal   = str(row.get("modalidad",""))
        url     = str(row.get("url",      ""))
        desc    = str(row.get("descripcion",""))[:400]

        # Color de barra según score
        pct   = min(int(score * 100), 100)
        color = "#00b894" if score >= 0.6 else "#fdcb6e" if score >= 0.35 else "#b2bec3"

        with st.expander(
            f"**#{int(row['rank'])}**  {titulo[:70]}  —  "
            f"Score: **{score:.4f}**", expanded=False
        ):
            col_i, col_d = st.columns([2, 1])
            with col_i:
                st.markdown(
                    f"**Empresa:** {empresa}  \n"
                    f"**Keyword:** {kw}  \n"
                    f"**Modalidad:** {modal}  \n"
                    f"**Fuente:** {fuente}",
                    unsafe_allow_html=True
                )
                st.markdown(
                    f'<div style="background:#f0f2f6;padding:10px;border-radius:8px;'
                    f'font-size:0.85em">{desc}...</div>',
                    unsafe_allow_html=True
                )
                if url and url.startswith("http"):
                    st.markdown(f"[🔗 Ver oferta completa]({url})")
            with col_d:
                # Gauge visual del score
                fig_g = go.Figure(go.Indicator(
                    mode  = "gauge+number",
                    value = score * 100,
                    title = {"text": "Match %", "font": {"size": 12}},
                    gauge = {
                        "axis":  {"range": [0, 100]},
                        "bar":   {"color": color},
                        "steps": [
                            {"range": [0,  35], "color": "#f8f9fa"},
                            {"range": [35, 60], "color": "#fff3cd"},
                            {"range": [60,100], "color": "#d4edda"},
                        ],
                    },
                    number = {"suffix": "%", "font": {"size": 18}},
                ))
                fig_g.update_layout(height=180, margin=dict(t=30,b=0,l=10,r=10))
                st.plotly_chart(fig_g, use_container_width=True, key=f"gauge_{row['rank']}")

    # Tabla descargable
    st.divider()
    st.subheader("📥 Exportar resultados")
    cols_exp = ["rank","titulo","empresa","keyword","fuente","modalidad",col_score,"url"]
    cols_exp = [c for c in cols_exp if c in df_fil.columns]
    csv_bytes = df_fil[cols_exp].to_csv(index=False).encode("utf-8")
    st.download_button("⬇️ Descargar Top-N en CSV",
                       data=csv_bytes,
                       file_name="recomendaciones_matching.csv",
                       mime="text/csv")

# ══════════════════════════════════════════════════════════════════════════════
# PÁGINA 2 — BENCHMARK Y MÉTRICAS
# ══════════════════════════════════════════════════════════════════════════════
def pagina_benchmark(metricas: dict, df_tuning: pd.DataFrame):
    st.header("📊 Benchmark y Métricas de Evaluación")

    # Tabla benchmark
    bench = metricas.get("benchmark_metricas.json", {})
    if bench and "sistemas" in bench:
        st.subheader("Comparativa de sistemas (T7)")
        sistemas = bench["sistemas"]
        df_met   = pd.DataFrame(sistemas).T.round(4)

        metricas_show = [c for c in ["P@5","P@10","P@20","nDCG@5","nDCG@10","nDCG@20","MRR"]
                         if c in df_met.columns]
        if metricas_show:
            df_show = df_met[metricas_show].copy()
            df_show.index = [i.split(" — ")[1] if " — " in i else i
                             for i in df_show.index]

            # Resaltar máximo por columna
            def highlight_max(s):
                is_max = s == s.max()
                return ["background-color: #d4edda; font-weight: bold"
                        if v else "" for v in is_max]
            st.dataframe(df_show.style.apply(highlight_max), use_container_width=True)

            # Gráfico Precision@K
            Ks = [5, 10, 20]
            fig_p = go.Figure()
            colores = px.colors.qualitative.Set2
            for i, (nombre, vals) in enumerate(sistemas.items()):
                nombre_c = nombre.split(" — ")[1] if " — " in nombre else nombre
                p_vals   = [vals.get(f"P@{k}", 0) for k in Ks]
                fig_p.add_trace(go.Scatter(
                    x=Ks, y=p_vals, name=nombre_c,
                    mode="lines+markers",
                    line=dict(color=colores[i % len(colores)], width=2),
                    marker=dict(size=8)
                ))
            fig_p.update_layout(
                title="Precision@K — todos los sistemas",
                xaxis_title="K", yaxis_title="Precision@K",
                legend=dict(orientation="h", y=-0.25),
                height=350
            )
            st.plotly_chart(fig_p, use_container_width=True)

            # Gráfico nDCG@K
            fig_n = go.Figure()
            for i, (nombre, vals) in enumerate(sistemas.items()):
                nombre_c = nombre.split(" — ")[1] if " — " in nombre else nombre
                n_vals   = [vals.get(f"nDCG@{k}", 0) for k in Ks]
                fig_n.add_trace(go.Scatter(
                    x=Ks, y=n_vals, name=nombre_c,
                    mode="lines+markers",
                    line=dict(color=colores[i % len(colores)], width=2),
                    marker=dict(size=8, symbol="square")
                ))
            fig_n.update_layout(
                title="nDCG@K — todos los sistemas",
                xaxis_title="K", yaxis_title="nDCG@K",
                legend=dict(orientation="h", y=-0.25),
                height=350
            )
            st.plotly_chart(fig_n, use_container_width=True)
    else:
        st.info("ℹ️  benchmark_metricas.json no encontrado. Ejecutar T7 para generarlo.")

    # Curva de tuning de ponderación
    st.divider()
    st.subheader("Tuning A — Curva de ponderación óptima (T8)")
    if not df_tuning.empty and "tuning" in df_tuning.columns:
        df_t_a = df_tuning[df_tuning["tuning"] == "A_ponderacion"].copy()
        if not df_t_a.empty and "w_embed" in df_t_a.columns:
            fig_t = go.Figure()
            for col, color, name in [
                ("nDCG@10", "#0077B5", "nDCG@10"),
                ("P@10",    "#E8392A", "Precision@10"),
                ("MRR",     "#00b894", "MRR"),
            ]:
                if col in df_t_a.columns:
                    fig_t.add_trace(go.Scatter(
                        x=df_t_a["w_embed"], y=df_t_a[col],
                        name=name, mode="lines+markers",
                        line=dict(color=color, width=2), marker=dict(size=6)
                    ))
            # Línea vertical en w_opt
            w_best = df_t_a.loc[df_t_a["nDCG@10"].idxmax(), "w_embed"] \
                     if "nDCG@10" in df_t_a.columns else W_DEFAULT
            fig_t.add_vline(x=w_best, line_dash="dash", line_color="red",
                            annotation_text=f"w_opt={w_best:.2f}",
                            annotation_position="top right")
            fig_t.add_vline(x=0.70, line_dash="dot", line_color="orange",
                            annotation_text="T6 base (0.70)",
                            annotation_position="bottom right")
            fig_t.update_layout(
                title="Métricas en función del peso SBERT (w_embed)",
                xaxis_title="w_embed", yaxis_title="Métrica",
                legend=dict(orientation="h", y=-0.25), height=380
            )
            st.plotly_chart(fig_t, use_container_width=True)
        else:
            st.info("ℹ️  No se encontraron resultados del Tuning A.")
    else:
        st.info("ℹ️  tuning_resultados.csv no encontrado. Ejecutar T8 para generarlo.")

# ══════════════════════════════════════════════════════════════════════════════
# PÁGINA 3 — EXPLORACIÓN DEL CORPUS
# ══════════════════════════════════════════════════════════════════════════════
def pagina_corpus(df: pd.DataFrame, emb_of, emb_cv):
    st.header("🔭 Exploración del Corpus")

    # KPIs
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total ofertas", f"{len(df):,}")
    c2.metric("Fuentes",       str(df["fuente"].nunique())   if "fuente"   in df.columns else "—")
    c3.metric("Keywords",      str(df["keyword"].nunique())  if "keyword"  in df.columns else "—")
    c4.metric("Con descripción",
              f"{df['descripcion'].fillna('').str.len().gt(50).sum():,}")

    st.divider()
    col_izq, col_der = st.columns(2)

    # Distribución por fuente
    with col_izq:
        if "fuente" in df.columns:
            cnt_f = df["fuente"].value_counts().reset_index()
            cnt_f.columns = ["fuente", "n"]
            fig_f = px.bar(
                cnt_f.sort_values("n"), x="n", y="fuente",
                orientation="h", title="Ofertas por fuente",
                color="fuente",
                color_discrete_map={f: c for f, c in COLORES_FUENTE.items()},
                labels={"n": "N ofertas", "fuente": ""},
            )
            fig_f.update_layout(showlegend=False, height=350)
            st.plotly_chart(fig_f, use_container_width=True)

    # Distribución por keyword
    with col_der:
        if "keyword" in df.columns:
            cnt_k = df["keyword"].value_counts().reset_index()
            cnt_k.columns = ["keyword", "n"]
            fig_k = px.bar(
                cnt_k.sort_values("n"), x="n", y="keyword",
                orientation="h", title="Ofertas por keyword",
                color_discrete_sequence=["#0077B5"],
                labels={"n": "N ofertas", "keyword": ""},
            )
            fig_k.update_layout(showlegend=False, height=350)
            st.plotly_chart(fig_k, use_container_width=True)

    st.divider()

    # Distribución por modalidad
    col_m, col_id = st.columns(2)
    with col_m:
        if "modalidad" in df.columns:
            cnt_m = df["modalidad"].value_counts(dropna=False).reset_index()
            cnt_m.columns = ["modalidad", "n"]
            cnt_m["modalidad"] = cnt_m["modalidad"].fillna("Sin datos")
            fig_m = px.pie(
                cnt_m, values="n", names="modalidad",
                title="Distribución por modalidad",
                color_discrete_sequence=px.colors.qualitative.Set3,
                hole=0.4,
            )
            fig_m.update_traces(textposition="inside", textinfo="percent+label")
            fig_m.update_layout(height=350, showlegend=False)
            st.plotly_chart(fig_m, use_container_width=True)

    # Distribución por idioma
    with col_id:
        if "idioma" in df.columns:
            cnt_i = df["idioma"].value_counts().reset_index()
            cnt_i.columns = ["idioma", "n"]
            fig_i = px.pie(
                cnt_i, values="n", names="idioma",
                title="Distribución por idioma",
                color_discrete_map={"es": "#0077B5", "en": "#E8392A", "mix": "#fdcb6e"},
                hole=0.4,
            )
            fig_i.update_traces(textposition="inside", textinfo="percent+label")
            fig_i.update_layout(height=350, showlegend=False)
            st.plotly_chart(fig_i, use_container_width=True)

    # Mapa vectorial PCA 2D
    st.divider()
    st.subheader("🗺️ Mapa del espacio vectorial (PCA 2D)")

    if emb_of is not None and len(emb_of) == len(df):
        with st.spinner("Calculando PCA..."):
            all_emb  = np.vstack([emb_of, emb_cv.reshape(1,-1)]) \
                       if emb_cv is not None else emb_of
            pca      = PCA(n_components=2, random_state=42)
            coords   = pca.fit_transform(all_emb)
            c_of     = coords[:len(emb_of)]
            c_cv     = coords[len(emb_of)] if emb_cv is not None else None

        df_pca = df[["keyword","fuente","titulo"]].copy()
        df_pca["x"] = c_of[:,0]
        df_pca["y"] = c_of[:,1]

        fig_pca = px.scatter(
            df_pca, x="x", y="y",
            color="keyword" if "keyword" in df_pca.columns else None,
            hover_data={"titulo": True, "fuente": True, "keyword": True,
                        "x": False, "y": False},
            title=f"Espacio vectorial SBERT (PCA 2D) — varianza explicada: "
                  f"{pca.explained_variance_ratio_.sum()*100:.1f}%",
            opacity=0.55, height=500,
            labels={"x": "PC1", "y": "PC2", "keyword": "Keyword"},
        )
        if c_cv is not None:
            fig_pca.add_trace(go.Scatter(
                x=[c_cv[0]], y=[c_cv[1]],
                mode="markers+text",
                marker=dict(size=20, color="black", symbol="star"),
                text=["★ CV"], textposition="top right",
                name="CV del candidato",
                showlegend=True,
            ))
        fig_pca.update_layout(legend=dict(orientation="h", y=-0.15))
        st.plotly_chart(fig_pca, use_container_width=True)
    else:
        st.info("ℹ️  Vectores SBERT no disponibles. Ejecutar T5 para generarlos.")

    # Score promedio por keyword (heatmap fuente x keyword)
    st.divider()
    st.subheader("🔥 Heatmap: N ofertas por fuente × keyword")
    if "fuente" in df.columns and "keyword" in df.columns:
        pivot = df.groupby(["fuente","keyword"]).size().unstack(fill_value=0)
        fig_h = px.imshow(
            pivot, text_auto=True,
            color_continuous_scale="Blues",
            title="Número de ofertas por fuente × keyword",
            aspect="auto", height=400,
        )
        fig_h.update_coloraxes(showscale=False)
        st.plotly_chart(fig_h, use_container_width=True)

# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════
def main():
    # Cargar datos
    df        = cargar_corpus()
    emb_of, emb_cv = cargar_embeddings()
    vectorizer = cargar_tfidf()
    metricas   = cargar_metricas()
    config     = cargar_config()
    df_tuning  = cargar_tuning()

    # Sidebar
    uso_cv, cv_file, w_embed, top_n = render_sidebar(config)

    # Navegación por tabs
    tab1, tab2, tab3 = st.tabs([
        "🏆 Recomendaciones",
        "📊 Benchmark y Métricas",
        "🔭 Exploración del Corpus",
    ])

    with tab1:
        pagina_recomendaciones(df.copy(), emb_of, emb_cv,
                               vectorizer, uso_cv, cv_file,
                               w_embed, top_n)
    with tab2:
        pagina_benchmark(metricas, df_tuning)

    with tab3:
        pagina_corpus(df.copy(), emb_of, emb_cv)

    # Footer
    st.divider()
    st.caption(
        "🎓 **Tesis MCD** — Cicconi, Carlos Alberto — Universidad Austral  \n"
        "Motor de Recomendación Laboral · SBERT + TF-IDF · "
        f"Corpus: {len(df):,} ofertas"
    )

if __name__ == "__main__":
    main()
