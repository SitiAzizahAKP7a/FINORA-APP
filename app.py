# ============================================================
# FINORA AI — Financial Analysis & Audit Intelligence
# Single-file Streamlit App
# Author: FINORA
# ============================================================

import streamlit as st
import pandas as pd
import numpy as np
import sqlite3
import json
import os
import io
import hashlib
from datetime import datetime
from xml.sax.saxutils import escape

import plotly.express as px
import plotly.graph_objects as go

# ============================================================
# PAGE CONFIG + STYLE
# ============================================================
st.set_page_config(
    page_title="FINORA AI",
    page_icon="💗",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
:root {
  --finora-pink: #FF5C8B;
  --finora-cream: #FFF6E9;
  --finora-rose: #F8C8D4;
  --finora-burgundy: #7A1F3D;
  --finora-text: #2D1B24;
}

.stApp {
  background-color: var(--finora-cream);
  color: var(--finora-text);
}

[data-testid="stSidebar"] {
  background-color: #FFF0F4;
}

.stButton > button {
  background-color: var(--finora-pink);
  color: white !important;
  border-radius: 12px;
  border: none;
  font-weight: 600;
  padding: 10px 18px;
}
.stButton > button:hover {
  background-color: var(--finora-burgundy);
  color: white !important;
}

h1, h2, h3 { color: var(--finora-burgundy); }

.finora-header {
  background: linear-gradient(135deg, #FF5C8B 0%, #7A1F3D 100%);
  color: white;
  padding: 24px;
  border-radius: 16px;
  margin-bottom: 20px;
}
.finora-header h1 { color: white; margin: 0; font-size: 30px; }
.finora-header p  { color: #FFF0F4; margin: 6px 0 0 0; }

div[data-testid="stMetric"] {
  background: white;
  padding: 16px;
  border-radius: 12px;
  border-left: 4px solid #FF5C8B;
  box-shadow: 0 2px 8px rgba(0,0,0,0.05);
}

.stDataFrame { background: white; border-radius: 12px; }

.risk-high   { color: #C62828; font-weight: 700; }
.risk-medium { color: #EF6C00; font-weight: 700; }
.risk-low    { color: #2E7D32; font-weight: 700; }
</style>
""", unsafe_allow_html=True)

# ============================================================
# DATABASE
# ============================================================
DB_PATH = "finora.db"

def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_conn()
    c = conn.cursor()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS files (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        filename TEXT,
        file_hash TEXT UNIQUE,
        uploaded_at TEXT
    );
    CREATE TABLE IF NOT EXISTS analyses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        file_id INTEGER,
        mode TEXT,
        selected_modules TEXT,
        status TEXT,
        summary_json TEXT,
        created_at TEXT
    );
    CREATE TABLE IF NOT EXISTS findings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        analysis_id INTEGER,
        module TEXT,
        risk_level TEXT,
        title TEXT,
        detail TEXT,
        recommendation TEXT
    );
    CREATE TABLE IF NOT EXISTS chats (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        analysis_id INTEGER,
        role TEXT,
        message TEXT,
        created_at TEXT
    );
    """)
    conn.commit()
    conn.close()

init_db()

def save_file(filename, content_bytes):
    file_hash = hashlib.md5(content_bytes).hexdigest()
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT id FROM files WHERE file_hash=?", (file_hash,))
    row = c.fetchone()
    if row:
        conn.close()
        return row["id"]
    c.execute("INSERT INTO files(filename, file_hash, uploaded_at) VALUES (?,?,?)",
              (filename, file_hash, datetime.now().isoformat()))
    fid = c.lastrowid
    conn.commit()
    conn.close()
    return fid

def save_analysis(file_id, mode, selected_modules, summary):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""INSERT INTO analyses(file_id, mode, selected_modules, status, summary_json, created_at)
                 VALUES (?,?,?,?,?,?)""",
              (file_id, mode, json.dumps(selected_modules), "completed",
               json.dumps(summary, default=str), datetime.now().isoformat()))
    aid = c.lastrowid
    conn.commit()
    conn.close()
    return aid

def save_findings(analysis_id, findings):
    if not findings:
        return
    conn = get_conn()
    c = conn.cursor()
    for f in findings:
        c.execute("""INSERT INTO findings(analysis_id, module, risk_level, title, detail, recommendation)
                     VALUES (?,?,?,?,?,?)""",
                  (analysis_id, f["module"], f["risk_level"], f["title"],
                   f["detail"], f["recommendation"]))
    conn.commit()
    conn.close()

def save_chat(analysis_id, role, message):
    conn = get_conn()
    c = conn.cursor()
    c.execute("INSERT INTO chats(analysis_id, role, message, created_at) VALUES (?,?,?,?)",
              (analysis_id, role, message, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def fetch_analyses():
    conn = get_conn()
    df = pd.read_sql_query("""
        SELECT a.id, f.filename, a.mode, a.selected_modules, a.created_at
        FROM analyses a JOIN files f ON a.file_id = f.id
        ORDER BY a.created_at DESC
    """, conn)
    conn.close()
    return df

def fetch_analysis(aid):
    conn = get_conn()
    df = pd.read_sql_query("SELECT * FROM analyses WHERE id=?", conn, params=(aid,))
    conn.close()
    return df.iloc[0] if not df.empty else None

def fetch_findings(aid):
    conn = get_conn()
    df = pd.read_sql_query("SELECT * FROM findings WHERE analysis_id=?", conn, params=(aid,))
    conn.close()
    return df

def fetch_chats(aid):
    conn = get_conn()
    df = pd.read_sql_query("SELECT * FROM chats WHERE analysis_id=? ORDER BY id", conn, params=(aid,))
    conn.close()
    return df

# ============================================================
# EXCEL READER
# ============================================================
def read_excel_sheets(file):
    try:
        xls = pd.ExcelFile(file)
        return xls.sheet_names
    except Exception as e:
        st.error(f"Gagal membaca Excel: {e}")
        return []

def load_sheet(file, sheet):
    try:
        return pd.read_excel(file, sheet_name=sheet)
    except Exception as e:
        st.error(f"Gagal memuat sheet: {e}")
        return pd.DataFrame()

# ============================================================
# AUDIT ENGINE
# ============================================================
def audit_duplicates(df, desc_col=None):
    findings = []
    if desc_col and desc_col in df.columns:
        dup = df[df.duplicated(subset=[desc_col], keep=False)]
    else:
        dup = df[df.duplicated(keep=False)]
    if not dup.empty:
        findings.append({
            "module": "Audit Duplikat",
            "risk_level": "Tinggi",
            "title": f"Terdeteksi {len(dup)} baris duplikat",
            "detail": f"Baris duplikat: {len(dup)} dari total {len(df)} baris.",
            "recommendation": "Verifikasi apakah duplikasi disengaja atau kesalahan input."
        })
    return findings

def audit_empty(df):
    findings = []
    if df.empty:
        return findings
    for col, cnt in df.isna().sum().items():
        if cnt > 0:
            pct = cnt / len(df) * 100
            if pct > 20:
                findings.append({
                    "module": "Audit Data Kosong",
                    "risk_level": "Sedang" if pct < 50 else "Tinggi",
                    "title": f"Kolom '{col}' memiliki {cnt} data kosong ({pct:.1f}%)",
                    "detail": f"Data kosong pada kolom {col}.",
                    "recommendation": "Lengkapi data atau konfirmasi bahwa data memang tidak ada."
                })
    return findings

def audit_outliers(df, amount_col):
    findings = []
    if amount_col not in df.columns:
        return findings
    s = pd.to_numeric(df[amount_col], errors="coerce").dropna()
    if s.empty or s.std() == 0 or pd.isna(s.std()):
        return findings
    z = ((s - s.mean()) / s.std()).abs()
    outliers = int((z > 3).sum())
    if outliers > 0:
        findings.append({
            "module": "Audit Nominal Tidak Wajar",
            "risk_level": "Tinggi" if outliers > 5 else "Sedang",
            "title": f"Terdeteksi {outliers} nilai ekstrem (Z-score > 3)",
            "detail": f"Rata-rata: {s.mean():,.2f}, Standar deviasi: {s.std():,.2f}",
            "recommendation": "Periksa transaksi dengan nominal di luar kewajaran."
        })
    return findings

def audit_debit_credit(df, debit_col, credit_col):
    findings = []
    if debit_col not in df.columns or credit_col not in df.columns:
        return findings
    d = pd.to_numeric(df[debit_col], errors="coerce").fillna(0).sum()
    c = pd.to_numeric(df[credit_col], errors="coerce").fillna(0).sum()
    diff = abs(d - c)
    if diff > 0.01:
        findings.append({
            "module": "Audit Debit/Kredit",
            "risk_level": "Tinggi" if (d > 0 and diff > d * 0.01) else "Sedang",
            "title": f"Debit dan Kredit tidak seimbang (selisih {diff:,.2f})",
            "detail": f"Total Debit: {d:,.2f}, Total Kredit: {c:,.2f}",
            "recommendation": "Periksa entri jurnal dan pastikan double-entry balance."
        })
    return findings

def audit_dates(df, date_col, period_start=None, period_end=None):
    findings = []
    if date_col not in df.columns:
        return findings
    dates = pd.to_datetime(df[date_col], errors="coerce")
    invalid = int(dates.isna().sum())
    if invalid > 0:
        findings.append({
            "module": "Audit Tanggal",
            "risk_level": "Sedang",
            "title": f"{invalid} tanggal tidak valid",
            "detail": f"Kolom {date_col} memiliki {invalid} nilai bukan tanggal valid.",
            "recommendation": "Perbaiki format tanggal."
        })
    if period_start is not None and period_end is not None:
        out = int(((dates < period_start) | (dates > period_end)).sum())
        if out > 0:
            findings.append({
                "module": "Audit Periode",
                "risk_level": "Sedang",
                "title": f"{out} transaksi di luar periode",
                "detail": f"Periode: {period_start.date()} s.d. {period_end.date()}",
                "recommendation": "Verifikasi transaksi di luar periode."
            })
    return findings

# ============================================================
# FINANCE ENGINE
# ============================================================
FINANCE_KEYWORDS = {
    "revenue":             ["pendapatan", "revenue", "penjualan", "sales"],
    "expense":             ["beban", "biaya", "expense", "cost"],
    "gross_profit":        ["laba kotor", "gross profit"],
    "operating_income":    ["laba operasi", "laba usaha", "operating income"],
    "net_income":          ["laba bersih", "net income", "net profit", "laba tahun berjalan"],
    "current_assets":      ["aset lancar", "aktiva lancar", "current assets"],
    "total_assets":        ["total aset", "total aktiva", "total assets", "jumlah aset"],
    "current_liabilities": ["liabilitas jangka pendek", "kewajiban lancar",
                            "utang lancar", "current liabilities"],
    "total_liabilities":   ["total liabilitas", "total kewajiban",
                            "total utang", "total liabilities"],
    "equity":              ["total ekuitas", "ekuitas", "equity", "total modal"],
    "cash":                ["kas dan setara kas", "kas", "cash"],
    "inventory":           ["persediaan", "inventory"],
    "receivable":          ["piutang", "receivable", "account receivable"],
    "interest_expense":    ["beban bunga", "interest expense"],
    "tax_expense":         ["beban pajak", "tax expense"],
}

def match_keyword(label, keyword_list):
    if not isinstance(label, str):
        return False
    l = label.lower().strip()
    return any(k in l for k in keyword_list)

def extract_finance_values(df, label_col, value_col):
    values = {}
    if label_col not in df.columns or value_col not in df.columns:
        return values
    for _, row in df.iterrows():
        label = row[label_col]
        val = pd.to_numeric(row[value_col], errors="coerce")
        if pd.isna(val):
            continue
        for key, kws in FINANCE_KEYWORDS.items():
            if key in values:
                continue
            if match_keyword(label, kws):
                values[key] = float(val)
    return values

def compute_ratios(v):
    r = {}
    def safe_div(a, b):
        return a / b if b else None

    if "current_assets" in v and v.get("current_liabilities"):
        r["Current Ratio"] = safe_div(v["current_assets"], v["current_liabilities"])
        if "inventory" in v:
            r["Quick Ratio"] = safe_div(v["current_assets"] - v["inventory"],
                                        v["current_liabilities"])
    if "total_liabilities" in v and v.get("equity"):
        r["Debt to Equity"] = safe_div(v["total_liabilities"], v["equity"])
    if "total_liabilities" in v and v.get("total_assets"):
        r["Debt to Asset"] = safe_div(v["total_liabilities"], v["total_assets"])
    if "gross_profit" in v and v.get("revenue"):
        r["Gross Profit Margin (%)"] = safe_div(v["gross_profit"], v["revenue"]) * 100
    if "net_income" in v and v.get("revenue"):
        r["Net Profit Margin (%)"] = safe_div(v["net_income"], v["revenue"]) * 100
    if "net_income" in v and v.get("total_assets"):
        r["ROA (%)"] = safe_div(v["net_income"], v["total_assets"]) * 100
    if "net_income" in v and v.get("equity"):
        r["ROE (%)"] = safe_div(v["net_income"], v["equity"]) * 100
    return {k: val for k, val in r.items() if val is not None}

def interpret_ratio(name, val):
    if name == "Current Ratio":
        if val < 1:   return "Likuiditas rendah — aset lancar tidak menutup kewajiban jangka pendek."
        if val < 1.5: return "Likuiditas cukup, perlu pemantauan."
        return "Likuiditas sehat."
    if name == "Quick Ratio":
        if val < 1: return "Kemampuan bayar jangka pendek tanpa persediaan terbatas."
        return "Likuiditas cepat memadai."
    if name == "Debt to Equity":
        if val > 2: return "Risiko solvabilitas tinggi, beban utang berat."
        if val > 1: return "Struktur modal cukup berutang, perlu kehati-hatian."
        return "Struktur modal konservatif."
    if name == "Debt to Asset":
        if val > 0.6: return "Sebagian besar aset dibiayai utang."
        return "Struktur aset masih wajar."
    if name in ("Gross Profit Margin (%)", "Net Profit Margin (%)", "ROA (%)", "ROE (%)"):
        if val < 0:  return "Negatif — perlu perhatian serius."
        if val < 5:  return "Margin tipis, perlu efisiensi."
        if val < 15: return "Margin wajar."
        return "Margin baik."
    return ""

# ============================================================
# GEMINI
# ============================================================
def call_gemini(prompt, context):
    try:
        import google.generativeai as genai
    except ImportError:
        return "Library google-generativeai belum terpasang. Tambahkan ke requirements.txt."
    api_key = ""
    try:
        api_key = st.secrets.get("GEMINI_API_KEY", "")
    except Exception:
        api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        return ("API key Gemini belum diatur. Tambahkan GEMINI_API_KEY di "
                "`.streamlit/secrets.toml` (lokal) atau di Secrets Streamlit Cloud.")
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-1.5-flash")
        full_prompt = f"""Kamu adalah FINORA AI, asisten analisis laporan keuangan dan audit.
Jawab berdasarkan konteks di bawah. JANGAN menyatakan kecurangan secara pasti.
Sebutkan bahwa setiap temuan bersifat indikasi dan perlu verifikasi dokumen
serta prosedur pemeriksaan.

KONTEKS ANALISIS:
{context}

PERTANYAAN PENGGUNA:
{prompt}
"""
        resp = model.generate_content(full_prompt)
        return resp.text
    except Exception as e:
        return f"Error Gemini: {e}"

# ============================================================
# EXPORT
# ============================================================
def export_excel(df_dict, filename):
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        for name, df in df_dict.items():
            df.to_excel(writer, sheet_name=name[:31], index=False)
    buf.seek(0)
    return buf.getvalue()

def export_pdf(title, sections):
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
        from reportlab.lib.units import cm
    except ImportError:
        return None
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm,
        topMargin=2 * cm, bottomMargin=2 * cm,
    )
    styles = getSampleStyleSheet()
    story = [Paragraph(f"<b>{escape(title)}</b>", styles["Title"]),
             Spacer(1, 0.4 * cm)]
    for sec in sections:
        story.append(Paragraph(f"<b>{escape(sec['heading'])}</b>", styles["Heading2"]))
        body = "<br/>".join(escape(line) for line in str(sec["body"]).split("\n"))
        story.append(Paragraph(body, styles["BodyText"]))
        story.append(Spacer(1, 0.3 * cm))
    doc.build(story)
    buf.seek(0)
    return buf.getvalue()

# ============================================================
# UI HELPERS
# ============================================================
def finora_header(title, subtitle):
    st.markdown(f"""
    <div class="finora-header">
        <h1>💗 {title}</h1>
        <p>{subtitle}</p>
    </div>
    """, unsafe_allow_html=True)

def risk_badge(level):
    colors = {"Tinggi": "#C62828", "Sedang": "#EF6C00", "Rendah": "#2E7D32"}
    c = colors.get(level, "#666")
    return (f"<span style='background:{c};color:white;padding:2px 8px;"
            f"border-radius:8px;font-size:12px;font-weight:700;'>{level}</span>")

def display_findings(findings):
    if not findings:
        st.info("Tidak ada temuan.")
        return
    for f in findings:
        with st.expander(f"⚠️ {f['title']} — {f['risk_level']}"):
            st.markdown(f"**Modul:** {f['module']}")
            st.markdown(f"**Risiko:** {risk_badge(f['risk_level'])}", unsafe_allow_html=True)
            st.markdown(f"**Detail:** {f['detail']}")
            st.markdown(f"**Rekomendasi:** {f['recommendation']}")

# ============================================================
# PAGES
# ============================================================
def page_dashboard():
    finora_header("Dashboard", "Ringkasan seluruh aktivitas analisis FINORA AI")

    conn = get_conn()
    total_files = pd.read_sql_query("SELECT COUNT(*) as c FROM files", conn).iloc[0]["c"]
    total_analyses = pd.read_sql_query("SELECT COUNT(*) as c FROM analyses", conn).iloc[0]["c"]
    total_findings = pd.read_sql_query("SELECT COUNT(*) as c FROM findings", conn).iloc[0]["c"]
    high_risk = pd.read_sql_query(
        "SELECT COUNT(*) as c FROM findings WHERE risk_level='Tinggi'", conn
    ).iloc[0]["c"]
    conn.close()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("📁 File Dianalisis", total_files)
    c2.metric("📊 Analisis Dilakukan", total_analyses)
    c3.metric("⚠️ Total Temuan", total_findings)
    c4.metric("🔴 Risiko Tinggi", high_risk)

    st.markdown("---")

    col1, col2 = st.columns(2)
    with col1:
        st.subheader("📈 Aktivitas Analisis")
        recent = fetch_analyses()
        if not recent.empty:
            recent["created_at"] = pd.to_datetime(recent["created_at"])
            by_date = (recent.groupby(recent["created_at"].dt.date)
                       .size().reset_index(name="count"))
            fig = px.bar(by_date, x="created_at", y="count",
                         labels={"created_at": "Tanggal", "count": "Jumlah"},
                         color_discrete_sequence=["#FF5C8B"])
            fig.update_layout(plot_bgcolor="white", paper_bgcolor="white")
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("Belum ada analisis.")

    with col2:
        st.subheader("🛡️ Distribusi Risiko")
        conn = get_conn()
        risk = pd.read_sql_query(
            "SELECT risk_level, COUNT(*) as count FROM findings GROUP BY risk_level", conn
        )
        conn.close()
        if not risk.empty:
            fig = px.pie(risk, names="risk_level", values="count",
                         color="risk_level",
                         color_discrete_map={"Tinggi": "#C62828",
                                             "Sedang": "#EF6C00",
                                             "Rendah": "#2E7D32"})
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("Belum ada temuan.")

    st.markdown("---")
    st.subheader("🕐 Analisis Terakhir")
    recent = fetch_analyses()
    if not recent.empty:
        recent["created_at"] = pd.to_datetime(recent["created_at"]).dt.strftime("%Y-%m-%d %H:%M")
        st.dataframe(recent.head(10), use_container_width=True)
    else:
        st.info("Belum ada riwayat analisis.")

    st.markdown("---")
    st.subheader("⚡ Shortcut")
    cc = st.columns(4)
    shortcuts = [("🛡️ Audit", "Audit"),
                 ("📈 Laporan Keuangan", "Laporan Keuangan"),
                 ("⚙️ Analisis Custom", "Analisis Custom"),
                 ("🤖 AI Assistant", "AI Assistant")]
    for col, (label, target) in zip(cc, shortcuts):
        if col.button(label, use_container_width=True):
            st.session_state["pending_nav"] = target
            st.rerun()


def page_audit():
    finora_header("Audit Otomatis", "Upload Excel dan jalankan pengujian audit")

    uploaded = st.file_uploader("Upload file Excel", type=["xlsx", "xls"], key="audit_file")
    if not uploaded:
        return

    sheets = read_excel_sheets(uploaded)
    if not sheets:
        return
    sheet = st.selectbox("Pilih sheet", sheets, key="audit_sheet")
    df = load_sheet(uploaded, sheet)
    if df.empty:
        st.warning("Sheet kosong atau gagal dimuat.")
        return

    st.subheader("Preview Data")
    st.dataframe(df.head(20), use_container_width=True)

    st.subheader("Pemetaan Kolom")
    c1, c2 = st.columns(2)
    with c1:
        date_col = st.selectbox("Kolom Tanggal", [""] + list(df.columns), key="a_date")
        amount_col = st.selectbox("Kolom Nominal", [""] + list(df.columns), key="a_amount")
        desc_col = st.selectbox("Kolom Deskripsi (opsional)",
                                [""] + list(df.columns), key="a_desc")
    with c2:
        debit_col = st.selectbox("Kolom Debit (opsional)", [""] + list(df.columns), key="a_debit")
        credit_col = st.selectbox("Kolom Kredit (opsional)",
                                  [""] + list(df.columns), key="a_credit")

    st.subheader("Pengujian Audit")
    col1, col2 = st.columns(2)
    with col1:
        c_dup = st.checkbox("Data Duplikat", value=True)
        c_empty = st.checkbox("Data Kosong", value=True)
        c_out = st.checkbox("Nominal Tidak Wajar", value=True)
    with col2:
        c_dc = st.checkbox("Debit/Kredit Tidak Seimbang", value=True)
        c_date = st.checkbox("Tanggal Tidak Valid", value=True)
        c_period = st.checkbox("Transaksi di Luar Periode", value=False)

    ps = pe = None
    if c_period:
        st.markdown("**Periode Audit**")
        cp1, cp2 = st.columns(2)
        ps = cp1.date_input("Dari tanggal", value=datetime.now().replace(month=1, day=1))
        pe = cp2.date_input("Sampai tanggal", value=datetime.now())

    if st.button("🚀 Jalankan Audit", use_container_width=True):
        findings = []
        if c_dup:
            findings += audit_duplicates(df, desc_col or None)
        if c_empty:
            findings += audit_empty(df)
        if c_out and amount_col:
            findings += audit_outliers(df, amount_col)
        if c_dc and debit_col and credit_col:
            findings += audit_debit_credit(df, debit_col, credit_col)
        if c_date and date_col:
            findings += audit_dates(
                df, date_col,
                pd.Timestamp(ps) if ps else None,
                pd.Timestamp(pe) if pe else None,
            )

        if not findings:
            st.success("✅ Tidak ada temuan. Data terlihat bersih "
                       "(verifikasi manual tetap diperlukan).")
        else:
            st.warning(f"Ditemukan {len(findings)} temuan audit.")
            display_findings(findings)

            selected = [lbl for lbl, chk in [
                ("Data Duplikat", c_dup), ("Data Kosong", c_empty),
                ("Nominal Tidak Wajar", c_out), ("Debit/Kredit", c_dc),
                ("Tanggal", c_date), ("Periode", c_period)] if chk]

            fid = save_file(uploaded.name, uploaded.getvalue())
            summary = {
                "total_findings": len(findings),
                "high_risk": sum(1 for f in findings if f["risk_level"] == "Tinggi"),
            }
            aid = save_analysis(fid, "audit", selected, summary)
            save_findings(aid, findings)
            st.success(f"💾 Analisis disimpan dengan ID #{aid}")

            df_exp = pd.DataFrame(findings)
            st.download_button(
                "📥 Download Temuan (Excel)",
                data=export_excel({"Temuan Audit": df_exp}, "audit.xlsx"),
                file_name="finora_audit_findings.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
            pdf_body = "\n".join(f"- [{f['risk_level']}] {f['title']}" for f in findings)
            pdf_data = export_pdf("FINORA Audit Report", [
                {"heading": "Ringkasan", "body": f"Total temuan: {len(findings)}"},
                {"heading": "Temuan", "body": pdf_body},
            ])
            if pdf_data:
                st.download_button(
                    "📥 Download PDF",
                    data=pdf_data,
                    file_name="finora_audit_report.pdf",
                    mime="application/pdf",
                )


def page_laporan():
    finora_header("Laporan Keuangan", "Upload laporan keuangan dan analisis otomatis")

    uploaded = st.file_uploader("Upload file Excel", type=["xlsx", "xls"], key="lp_file")
    if not uploaded:
        return

    sheets = read_excel_sheets(uploaded)
    if not sheets:
        return
    sheet = st.selectbox("Pilih sheet", sheets, key="lp_sheet")
    df = load_sheet(uploaded, sheet)
    if df.empty:
        st.warning("Sheet kosong.")
        return

    st.subheader("Preview Data")
    st.dataframe(df.head(20), use_container_width=True)

    st.subheader("Pemetaan Kolom")
    c1, c2 = st.columns(2)
    label_col = c1.selectbox("Kolom Akun/Nama", [""] + list(df.columns), key="lp_label")
    value_col = c2.selectbox("Kolom Nominal", [""] + list(df.columns), key="lp_value")

    if not label_col or not value_col:
        st.info("Pilih kolom akun dan nominal terlebih dahulu.")
        return

    if st.button("🚀 Analisis Laporan", use_container_width=True):
        values = extract_finance_values(df, label_col, value_col)
        ratios = compute_ratios(values)

        st.subheader("🔍 Nilai Terdeteksi")
        if values:
            st.dataframe(pd.DataFrame([
                {"Item": k.replace("_", " ").title(), "Nilai": v}
                for k, v in values.items()
            ]), use_container_width=True)
        else:
            st.warning("Tidak ada item keuangan terdeteksi. Pastikan label akun sesuai.")

        st.subheader("📊 Rasio Keuangan")
        if ratios:
            rdf = pd.DataFrame([
                {"Rasio": k, "Nilai": round(v, 4), "Interpretasi": interpret_ratio(k, v)}
                for k, v in ratios.items()
            ])
            st.dataframe(rdf, use_container_width=True)
            fig = px.bar(rdf, x="Rasio", y="Nilai", color="Nilai",
                         color_continuous_scale=["#F8C8D4", "#FF5C8B", "#7A1F3D"])
            fig.update_layout(plot_bgcolor="white", paper_bgcolor="white")
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("Rasio tidak dapat dihitung (item kunci tidak lengkap).")

        if values or ratios:
            fid = save_file(uploaded.name, uploaded.getvalue())
            summary = {
                "values_detected": len(values),
                "ratios_computed": len(ratios),
                "ratios": {k: round(v, 4) for k, v in ratios.items()},
            }
            aid = save_analysis(fid, "laporan", ["laporan_keuangan"], summary)
            save_findings(aid, [])
            st.success(f"💾 Analisis disimpan dengan ID #{aid}")

            exp = {
                "Nilai": pd.DataFrame([{"Item": k, "Nilai": v} for k, v in values.items()]),
                "Rasio": pd.DataFrame([
                    {"Rasio": k, "Nilai": v, "Interpretasi": interpret_ratio(k, v)}
                    for k, v in ratios.items()
                ]),
            }
            st.download_button(
                "📥 Download Excel",
                data=export_excel(exp, "laporan.xlsx"),
                file_name="finora_laporan.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )


def page_custom():
    finora_header("Analisis Custom",
                  "Upload 1 file Excel, centang modul yang ingin dijalankan sekaligus")

    uploaded = st.file_uploader("Upload file Excel", type=["xlsx", "xls"], key="custom_file")
    if not uploaded:
        return

    sheets = read_excel_sheets(uploaded)
    if not sheets:
        return
    sheet = st.selectbox("Pilih sheet", sheets, key="custom_sheet")
    df = load_sheet(uploaded, sheet)
    if df.empty:
        st.warning("Sheet kosong.")
        return

    st.subheader("Preview Data")
    st.dataframe(df.head(15), use_container_width=True)

    st.subheader("📈 Bagian Laporan Keuangan")
    c1, c2 = st.columns(2)
    fin_label = c1.selectbox("Kolom Akun (Laporan)", [""] + list(df.columns), key="c_label")
    fin_value = c2.selectbox("Kolom Nominal (Laporan)", [""] + list(df.columns), key="c_value")
    fin_items = st.multiselect(
        "Item laporan yang diperiksa",
        ["Neraca", "Laba Rugi", "Arus Kas", "Rasio Keuangan"],
        default=["Rasio Keuangan"],
    )

    st.subheader("🛡️ Bagian Audit")
    c3, c4, c5 = st.columns(3)
    date_col = c3.selectbox("Kolom Tanggal (Audit)", [""] + list(df.columns), key="c_date")
    amount_col = c4.selectbox("Kolom Nominal (Audit)", [""] + list(df.columns), key="c_amount")
    desc_col = c5.selectbox("Kolom Deskripsi", [""] + list(df.columns), key="c_desc")

    c6, c7 = st.columns(2)
    debit_col = c6.selectbox("Kolom Debit", [""] + list(df.columns), key="c_debit")
    credit_col = c7.selectbox("Kolom Kredit", [""] + list(df.columns), key="c_credit")

    audit_items = st.multiselect(
        "Pengujian audit yang diperiksa",
        ["Data Duplikat", "Data Kosong", "Nominal Tidak Wajar",
         "Debit/Kredit Tidak Seimbang", "Tanggal Tidak Valid"],
        default=["Data Duplikat", "Data Kosong"],
    )

    if st.button("🚀 Jalankan Analisis Custom", use_container_width=True):
        all_findings = []
        values, ratios = {}, {}

        if fin_items and fin_label and fin_value:
            values = extract_finance_values(df, fin_label, fin_value)
            ratios = compute_ratios(values)

        if "Data Duplikat" in audit_items:
            all_findings += audit_duplicates(df, desc_col or None)
        if "Data Kosong" in audit_items:
            all_findings += audit_empty(df)
        if "Nominal Tidak Wajar" in audit_items and amount_col:
            all_findings += audit_outliers(df, amount_col)
        if "Debit/Kredit Tidak Seimbang" in audit_items and debit_col and credit_col:
            all_findings += audit_debit_credit(df, debit_col, credit_col)
        if "Tanggal Tidak Valid" in audit_items and date_col:
            all_findings += audit_dates(df, date_col)

        st.subheader("📊 Hasil Laporan Keuangan")
        if values:
            st.dataframe(pd.DataFrame([
                {"Item": k, "Nilai": v} for k, v in values.items()
            ]), use_container_width=True)
        if ratios:
            st.dataframe(pd.DataFrame([
                {"Rasio": k, "Nilai": round(v, 4), "Interpretasi": interpret_ratio(k, v)}
                for k, v in ratios.items()
            ]), use_container_width=True)

        st.subheader("🛡️ Hasil Audit")
        if all_findings:
            display_findings(all_findings)
        else:
            st.success("Tidak ada temuan audit.")

        fid = save_file(uploaded.name, uploaded.getvalue())
        summary = {
            "values": values,
            "ratios": {k: round(v, 4) for k, v in ratios.items()},
            "total_findings": len(all_findings),
        }
        aid = save_analysis(fid, "custom", fin_items + audit_items, summary)
        save_findings(aid, all_findings)
        st.success(f"💾 Analisis disimpan dengan ID #{aid}")

        exp = {}
        if values:
            exp["Nilai Keuangan"] = pd.DataFrame([{"Item": k, "Nilai": v}
                                                  for k, v in values.items()])
        if ratios:
            exp["Rasio"] = pd.DataFrame([{"Rasio": k, "Nilai": v}
                                         for k, v in ratios.items()])
        if all_findings:
            exp["Temuan Audit"] = pd.DataFrame(all_findings)
        if exp:
            st.download_button(
                "📥 Download Hasil (Excel)",
                data=export_excel(exp, "custom.xlsx"),
                file_name="finora_custom_result.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )


def page_ai():
    finora_header("FINORA AI Assistant",
                  "Tanya AI tentang hasil analisis yang sudah disimpan")

    recent = fetch_analyses()
    if recent.empty:
        st.info("Belum ada analisis. Lakukan analisis terlebih dahulu.")
        return

    options = {
        f"#{row['id']} — {row['filename']} ({row['mode']}, {str(row['created_at'])[:10]})": int(row["id"])
        for _, row in recent.iterrows()
    }
    chosen = st.selectbox("Pilih analisis yang ingin didiskusikan", list(options.keys()))
    aid = options[chosen]

    an = fetch_analysis(aid)
    if an is None:
        st.error("Analisis tidak ditemukan.")
        return
    findings = fetch_findings(aid)

    c1, c2, c3 = st.columns(3)
    c1.metric("Mode", an["mode"])
    c2.metric("Temuan", len(findings))
    c3.metric("Tanggal", str(an["created_at"])[:10])

    if not findings.empty:
        with st.expander("Lihat temuan"):
            st.dataframe(findings[["module", "risk_level", "title"]],
                         use_container_width=True)

    st.subheader("💬 Percakapan")
    chats = fetch_chats(aid)
    for _, chat in chats.iterrows():
        role = "🧑 Anda" if chat["role"] == "user" else "🤖 FINORA AI"
        st.markdown(f"**{role}:** {chat['message']}")

    question = st.text_area("Pertanyaan Anda",
                            placeholder="Contoh: Apa risiko terbesar dari laporan ini?")
    if st.button("Kirim ke AI", use_container_width=True):
        if not question.strip():
            st.warning("Pertanyaan tidak boleh kosong.")
        else:
            context = f"Mode: {an['mode']}\nSummary: {an['summary_json']}\n"
            if not findings.empty:
                context += "Temuan:\n" + "\n".join(
                    f"- [{r['risk_level']}] {r['title']}: {r['detail']}"
                    for _, r in findings.iterrows()
                )
            save_chat(aid, "user", question)
            with st.spinner("AI sedang berpikir..."):
                answer = call_gemini(question, context)
            save_chat(aid, "assistant", answer)
            st.rerun()


def page_riwayat():
    finora_header("Riwayat & Simpan", "Semua analisis yang pernah dilakukan")

    df = fetch_analyses()
    if df.empty:
        st.info("Belum ada riwayat.")
        return
    st.dataframe(df, use_container_width=True)

    st.subheader("Detail Analisis")
    aid = st.selectbox("Pilih ID analisis", df["id"].tolist())
    an = fetch_analysis(aid)
    findings = fetch_findings(aid)

    if an is not None:
        c1, c2 = st.columns(2)
        c1.metric("File ID", an["file_id"])
        c2.metric("Mode", an["mode"])

        st.markdown("**Modul Terpilih**")
        try:
            st.json(json.loads(an["selected_modules"]))
        except Exception:
            st.write(an["selected_modules"])

        st.markdown("**Summary**")
        try:
            st.json(json.loads(an["summary_json"]))
        except Exception:
            st.write(an["summary_json"])

        if not findings.empty:
            st.markdown("**Temuan**")
            display_findings(findings.to_dict("records"))
            st.download_button(
                "📥 Download Temuan (Excel)",
                data=export_excel({"Temuan": findings}, "riwayat.xlsx"),
                file_name=f"finora_analysis_{aid}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

# ============================================================
# MAIN
# ============================================================
NAV_OPTIONS = ["Dashboard", "Audit", "Laporan Keuangan",
               "Analisis Custom", "AI Assistant", "Riwayat"]

def main():
    if "nav" not in st.session_state or st.session_state["nav"] not in NAV_OPTIONS:
        st.session_state["nav"] = "Dashboard"

    if "pending_nav" in st.session_state:
        target = st.session_state.pop("pending_nav")
        if target in NAV_OPTIONS:
            st.session_state["nav"] = target

    st.sidebar.markdown("""
    <div style="text-align:center;padding:16px 0;">
        <h1 style="color:#FF5C8B;margin:0;">💗 FINORA</h1>
        <p style="color:#7A1F3D;font-size:12px;margin:4px 0 0 0;">
            Financial Analysis &<br/>Audit Intelligence
        </p>
    </div>
    """, unsafe_allow_html=True)

    nav = st.sidebar.radio("Navigasi", NAV_OPTIONS, key="nav")

    st.sidebar.markdown("---")
    st.sidebar.caption("⚠️ Temuan bersifat indikasi dan perlu verifikasi dokumen "
                       "serta prosedur pemeriksaan.")
    st.sidebar.caption("🔒 API key Gemini disimpan di Secrets.")

    if nav == "Dashboard":
        page_dashboard()
    elif nav == "Audit":
        page_audit()
    elif nav == "Laporan Keuangan":
        page_laporan()
    elif nav == "Analisis Custom":
        page_custom()
    elif nav == "AI Assistant":
        page_ai()
    elif nav == "Riwayat":
        page_riwayat()

if __name__ == "__main__":
    main()
