import streamlit as st
import pandas as pd
import numpy as np
import sqlite3
import json
import os
import io
import hashlib
from datetime import datetime
import plotly.express as px

# ============================================================
# PAGE CONFIG + STYLE (FINORA SIGNATURE PINK & CREAM)
# ============================================================
st.set_page_config(page_title="FINORA AI", page_icon="💗", layout="wide")

st.markdown("""
<style>
:root { --finora-pink: #FF5C8B; --finora-cream: #FFF5E8; --finora-rose: #F8C8D4; --finora-burgundy: #7A1F3D; --finora-text: #2D1B24; }
.stApp { background-color: var(--finora-cream); color: var(--finora-text); }
[data-testid="stSidebar"] { background-color: #FFF0F4; }
.stButton > button { background-color: var(--finora-pink); color: white !important; border-radius: 12px; border: none; font-weight: 600; padding: 10px 18px; }
.stButton > button:hover { background-color: var(--finora-burgundy); color: white !important; }
h1, h2, h3 { color: var(--finora-burgundy); }
.finora-header { background: linear-gradient(135deg, #FF5C8B 0%, #7A1F3D 100%); color: white; padding: 24px; border-radius: 16px; margin-bottom: 20px; }
.finora-header h1 { color: white; margin: 0; font-size: 30px; }
.finora-header p  { color: #FFF0F4; margin: 6px 0 0 0; }
div[data-testid="stMetric"] { background: white; padding: 16px; border-radius: 12px; border-left: 4px solid #FF5C8B; box-shadow: 0 2px 8px rgba(0,0,0,0.05); }
.stDataFrame { background: white; border-radius: 12px; }
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
    CREATE TABLE IF NOT EXISTS files (id INTEGER PRIMARY KEY AUTOINCREMENT, filename TEXT, file_hash TEXT UNIQUE, uploaded_at TEXT);
    CREATE TABLE IF NOT EXISTS analyses (id INTEGER PRIMARY KEY AUTOINCREMENT, file_id INTEGER, mode TEXT, selected_modules TEXT, status TEXT, summary_json TEXT, created_at TEXT);
    CREATE TABLE IF NOT EXISTS findings (id INTEGER PRIMARY KEY AUTOINCREMENT, analysis_id INTEGER, module TEXT, risk_level TEXT, title TEXT, detail TEXT, recommendation TEXT);
    """)
    conn.commit(); conn.close()
init_db()

def save_file(filename, content_bytes):
    file_hash = hashlib.md5(content_bytes).hexdigest()
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT id FROM files WHERE file_hash=?", (file_hash,))
    row = c.fetchone()
    if row: conn.close(); return row["id"]
    c.execute("INSERT INTO files(filename, file_hash, uploaded_at) VALUES (?,?,?)", (filename, file_hash, datetime.now().isoformat()))
    fid = c.lastrowid; conn.commit(); conn.close()
    return fid

def save_analysis(file_id, mode, selected_modules, summary):
    conn = get_conn(); c = conn.cursor()
    c.execute("INSERT INTO analyses(file_id, mode, selected_modules, status, summary_json, created_at) VALUES (?,?,?,?,?,?)",
              (file_id, mode, json.dumps(selected_modules), "completed", json.dumps(summary, default=str), datetime.now().isoformat()))
    aid = c.lastrowid; conn.commit(); conn.close()
    return aid

def save_findings(analysis_id, findings):
    if not findings: return
    conn = get_conn(); c = conn.cursor()
    for f in findings:
        c.execute("INSERT INTO findings(analysis_id, module, risk_level, title, detail, recommendation) VALUES (?,?,?,?,?,?)",
                  (analysis_id, f["module"], f["risk_level"], f["title"], f["detail"], f["recommendation"]))
    conn.commit(); conn.close()

def fetch_analyses():
    conn = get_conn()
    df = pd.read_sql_query("SELECT a.id, f.filename, a.mode, a.created_at FROM analyses a JOIN files f ON a.file_id = f.id ORDER BY a.created_at DESC", conn)
    conn.close(); return df

def fetch_findings(aid):
    conn = get_conn()
    df = pd.read_sql_query("SELECT * FROM findings WHERE analysis_id=?", conn, params=(aid,))
    conn.close(); return df

# ============================================================
# AUDIT ENGINE V2 (LEBIH DALAM & REALISTIS)
# ============================================================
def audit_duplicates(df, desc_col=None):
    findings = []
    if desc_col and desc_col in df.columns:
        dup = df[df.duplicated(subset=[desc_col], keep=False)]
    else:
        dup = df[df.duplicated(keep=False)]
    if not dup.empty:
        findings.append({
            "module": "Data Duplikat", "risk_level": "Tinggi",
            "title": f"Terdeteksi {len(dup)} baris data duplikat",
            "detail": f"Terdapat transaksi dengan nilai/deskripsi yang sama persis.",
            "recommendation": "Lakukan sampling pada baris tersebut untuk memastikan bukan double-entry."
        })
    return findings

def audit_empty(df):
    findings = []
    for col in df.columns:
        cnt = df[col].isna().sum()
        if cnt > 0:
            pct = cnt / len(df) * 100
            if pct > 10: # Toleransi 10%
                findings.append({
                    "module": "Data Kosong", "risk_level": "Sedang" if pct < 30 else "Tinggi",
                    "title": f"Kolom '{col}' memiliki {cnt} data kosong ({pct:.1f}%)",
                    "detail": "Banyak sel kosong dapat mengindikasikan input data yang tidak lengkap.",
                    "recommendation": "Minta divisi terkait melengkapi data atau konfirmasi nilai kosong."
                })
    return findings

def audit_weekend(df, date_col):
    findings = []
    try:
        dates = pd.to_datetime(df[date_col], errors="coerce")
        weekend_tx = df[dates.dt.dayofweek >= 5] # 5=Sabtu, 6=Minggu
        if not weekend_tx.empty:
            findings.append({
                "module": "Transaksi Weekend", "risk_level": "Tinggi",
                "title": f"Terdeteksi {len(weekend_tx)} transaksi di hari Sabtu/Minggu",
                "detail": "Transaksi di luar hari kerja biasanya memerlukan otorisasi khusus atau berisiko tinggi.",
                "recommendation": "Periksa bukti otorisasi untuk transaksi akhir pekan."
            })
    except: pass
    return findings

def audit_round_numbers(df, amount_col):
    findings = []
    try:
        s = pd.to_numeric(df[amount_col], errors="coerce").dropna()
        # Cek nominal kelipatan 1.000.000 yang besar
        round_nums = s[(s >= 1000000) & (s % 1000000 == 0)]
        if not round_nums.empty:
            findings.append({
                "module": "Angka Bulat Mencurigakan", "risk_level": "Sedang",
                "title": f"Terdeteksi {len(round_nums)} transaksi dengan nominal bulat (>= 1 Juta)",
                "detail": "Nominal bulat besar sering digunakan untuk estimasi atau mark-up.",
                "recommendation": "Cocokkan dengan invoice/kuitansi asli untuk memastikan nilai sebenarnya."
            })
    except: pass
    return findings

def audit_outliers_iqr(df, amount_col):
    findings = []
    try:
        s = pd.to_numeric(df[amount_col], errors="coerce").dropna()
        if s.empty: return findings
        Q1, Q3 = s.quantile(0.25), s.quantile(0.75)
        IQR = Q3 - Q1
        lower, upper = Q1 - 1.5 * IQR, Q3 + 1.5 * IQR
        outliers = s[(s < lower) | (s > upper)]
        if not outliers.empty:
            findings.append({
                "module": "Anomali Nominal (Outlier)", "risk_level": "Tinggi",
                "title": f"Terdeteksi {len(outliers)} nilai ekstrem (di luar batas IQR)",
                "detail": f"Batas wajar: {lower:,.2f} s.d {upper:,.2f}",
                "recommendation": "Periksa transaksi terbesar/terkecil, pastikan bukan salah ketik (typo)."
            })
    except: pass
    return findings

def audit_balance(df, debit_col, credit_col):
    findings = []
    try:
        d = pd.to_numeric(df[debit_col], errors="coerce").fillna(0).sum()
        c = pd.to_numeric(df[credit_col], errors="coerce").fillna(0).sum()
        diff = abs(d - c)
        if diff > 1: # Toleransi 1 Rupiah
            findings.append({
                "module": "Debit/Kredit", "risk_level": "Tinggi",
                "title": f"Selisih Debit & Kredit: {diff:,.2f}",
                "detail": f"Total Debit: {d:,.2f} | Total Kredit: {c:,.2f}",
                "recommendation": "Jurnal tidak balance. Periksa kembali entri yang hilang atau salah input."
            })
    except: pass
    return findings

# ============================================================
# UI HELPERS
# ============================================================
def finora_header(title, subtitle):
    st.markdown(f'<div class="finora-header"><h1>💗 {title}</h1><p>{subtitle}</p></div>', unsafe_allow_html=True)

def display_findings(findings):
    if not findings:
        st.success("✅ Tidak ada temuan pelanggaran (High-Level).")
        return
        
    # Hitung Risk Score (0-100)
    score = min(100, len(findings) * 15 + sum(20 for f in findings if f["risk_level"] == "Tinggi"))
    color = "#C62828" if score > 60 else "#EF6C00" if score > 30 else "#2E7D32"
    
    st.markdown(f"### 🚨 Audit Risk Score: <span style='color:{color}; font-size:32px; font-weight:bold;'>{score}/100</span>", unsafe_allow_html=True)
    
    for f in findings:
        with st.expander(f"⚠️ [{f['risk_level']}] {f['title']}"):
            st.markdown(f"**Modul Uji:** {f['module']}")
            st.info(f"**Analisis:** {f['detail']}")
            st.warning(f"**Rekomendasi Auditor:** {f['recommendation']}")

# ============================================================
# PAGES
# ============================================================
def page_dashboard():
    finora_header("Dashboard", "Ringkasan seluruh aktivitas FINORA AI")
    conn = get_conn()
    t_files = pd.read_sql_query("SELECT COUNT(*) c FROM files", conn).iloc[0]['c']
    t_analyses = pd.read_sql_query("SELECT COUNT(*) c FROM analyses", conn).iloc[0]['c']
    t_findings = pd.read_sql_query("SELECT COUNT(*) c FROM findings", conn).iloc[0]['c']
    h_risk = pd.read_sql_query("SELECT COUNT(*) c FROM findings WHERE risk_level='Tinggi'", conn).iloc[0]['c']
    conn.close()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("📁 File Dianalisis", t_files)
    c2.metric("📊 Analisis Dilakukan", t_analyses)
    c3.metric("⚠️ Total Temuan", t_findings)
    c4.metric("🔴 Risiko Tinggi", h_risk)
    
    st.markdown("---")
    st.subheader("🕐 Analisis Terakhir")
    recent = fetch_analyses()
    if not recent.empty:
        recent['created_at'] = pd.to_datetime(recent['created_at']).dt.strftime("%Y-%m-%d %H:%M")
        st.dataframe(recent.head(5), use_container_width=True)

def page_audit():
    finora_header("Audit Otomatis v2", "Pemeriksaan data, anomali, dan fraud detection")
    tab1, tab2 = st.tabs(["🛡️ Mode Mandiri", "⚙️ Mode Custom"])
    
    with tab1:
        st.markdown("### Analisis Mandiri Audit")
        uploaded = st.file_uploader("Upload Excel/CSV", type=["xlsx", "csv"], key="audit_ind")
        if uploaded:
            # FITUR BARU: Deteksi Header Row
            header_row = st.number_input("Header data ada di baris ke-berapa? (0 = baris pertama)", min_value=0, value=0, step=1)
            df = pd.read_excel(uploaded, header=header_row) if uploaded.name.endswith('xlsx') else pd.read_csv(uploaded, header=header_row)
            
            st.write("Preview Data:")
            st.dataframe(df.head(10), use_container_width=True)
            
            c1, c2 = st.columns(2)
            date_col = c1.selectbox("Kolom Tanggal", [""] + list(df.columns))
            amount_col = c2.selectbox("Kolom Nominal (Debit/Kredit)", [""] + list(df.columns))
            
            st.markdown("**Pilih Pengujian Audit:**")
            c_dup = st.checkbox("Cek Duplikat & Data Kosong", value=True)
            c_weekend = st.checkbox("Cek Transaksi Hari Libur (Weekend)", value=True)
            c_round = st.checkbox("Cek Angka Bulat Mencurigakan (>=1 Juta)", value=True)
            c_outlier = st.checkbox("Cek Anomali Nilai (IQR Outliers)", value=True)
            
            if st.button("🚀 Jalankan Audit Mandiri", use_container_width=True):
                findings = []
                if c_dup: findings += audit_duplicates(df) + audit_empty(df)
                if c_weekend and date_col: findings += audit_weekend(df, date_col)
                if c_round and amount_col: findings += audit_round_numbers(df, amount_col)
                if c_outlier and amount_col: findings += audit_outliers_iqr(df, amount_col)
                
                display_findings(findings)
                
                fid = save_file(uploaded.name, uploaded.getvalue())
                aid = save_analysis(fid, "Audit Mandiri v2", ["audit_core"], {"total_findings": len(findings)})
                save_findings(aid, findings)
                st.success(f"💾 Laporan Audit disimpan di ID #{aid}")

    with tab2:
        st.markdown("### Analisis Custom Audit")
        uploaded_c = st.file_uploader("Upload Excel/CSV", type=["xlsx", "csv"], key="audit_cust")
        if uploaded_c:
            header_row = st.number_input("Header data ada di baris ke-berapa?", min_value=0, value=0, key="h_row")
            df = pd.read_excel(uploaded_c, header=header_row) if uploaded_c.name.endswith('xlsx') else pd.read_csv(uploaded_c, header=header_row)
            
            st.dataframe(df.head(5), use_container_width=True)
            
            c1, c2, c3 = st.columns(3)
            date_col = c1.selectbox("Kolom Tanggal", [""] + list(df.columns), key="c_date")
            amount_col = c2.selectbox("Kolom Nominal", [""] + list(df.columns), key="c_amount")
            desc_col = c3.selectbox("Kolom Deskripsi", [""] + list(df.columns), key="c_desc")
            
            c4, c5 = st.columns(2)
            debit_col = c4.selectbox("Kolom Debit", [""] + list(df.columns), key="c_debit")
            credit_col = c5.selectbox("Kolom Kredit", [""] + list(df.columns), key="c_credit")
            
            st.markdown("**Pilih Pengujian (Ceklis):**")
            c_dup = st.checkbox("Cek Duplikat", value=True)
            c_empty = st.checkbox("Cek Data Kosong", value=True)
            c_weekend = st.checkbox("Cek Transaksi Weekend", value=True)
            c_round = st.checkbox("Cek Nominal Bulat", value=True)
            c_outlier = st.checkbox("Cek Outlier (IQR)", value=True)
            c_balance = st.checkbox("Cek Balance D/C", value=True)
            
            if st.button("🚀 Jalankan Custom Audit", use_container_width=True):
                findings = []
                if c_dup: findings += audit_duplicates(df, desc_col or None)
                if c_empty: findings += audit_empty(df)
                if c_weekend and date_col: findings += audit_weekend(df, date_col)
                if c_round and amount_col: findings += audit_round_numbers(df, amount_col)
                if c_outlier and amount_col: findings += audit_outliers_iqr(df, amount_col)
                if c_balance and debit_col and credit_col: findings += audit_balance(df, debit_col, credit_col)
                
                display_findings(findings)
                
                fid = save_file(uploaded_c.name, uploaded_c.getvalue())
                aid = save_analysis(fid, "Audit Custom v2", ["custom_audit"], {"total_findings": len(findings)})
                save_findings(aid, findings)
                st.success(f"💾 Laporan Audit disimpan di ID #{aid}")

def page_simpan():
    finora_header("Pusat Laporan & Arsip", "Riwayat analisis dan unduh laporan")
    df = fetch_analyses()
    if df.empty:
        st.info("Belum ada riwayat."); return
    st.dataframe(df, use_container_width=True)
    
    st.markdown("---")
    aid = st.selectbox("Pilih ID Analisis untuk melihat detail", df["id"].tolist())
    findings = fetch_findings(aid)
    if not findings.empty:
        display_findings(findings.to_dict('records'))
        # Fitur Download Laporan
        csv = findings.to_csv(index=False).encode('utf-8')
        st.download_button("📥 Download Temuan (CSV)", csv, f"Finora_Audit_{aid}.csv", "text/csv")

def page_ai():
    finora_header("FINORA AI Assistant", "Tanyakan analisis mendalam ke Gemini AI")
    st.info("Fitur AI akan segera hadir di pembaruan berikutnya.")

# ============================================================
# MAIN
# ============================================================
def main():
    st.sidebar.markdown('<h1 style="color:#FF5C8B;text-align:center;">💗 FINORA</h1>', unsafe_allow_html=True)
    st.sidebar.markdown('<p style="text-align:center;font-size:12px;color:#7A1F3D;">Financial Intelligence<br>Platform</p>', unsafe_allow_html=True)
    st.sidebar.markdown("---")
    
    nav = st.sidebar.radio("Navigasi", ["Dashboard", "Audit", "Simpan", "AI Assistant"])
    
    st.sidebar.markdown("---")
    st.sidebar.caption("⚠️ Temuan bersifat indikasi dan perlu verifikasi dokumen fisik.")
    
    if nav == "Dashboard": page_dashboard()
    elif nav == "Audit": page_audit()
    elif nav == "Simpan": page_simpan()
    elif nav == "AI Assistant": page_ai()

if __name__ == "__main__":
    main()
