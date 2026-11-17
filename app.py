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
import plotly.graph_objects as go

# Library untuk PDF
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

# ============================================================
# CONFIG & STYLE
# ============================================================
st.set_page_config(page_title="FINORA AI - Pro Audit", page_icon="💗", layout="wide")

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
# AUDIT ENGINE V3 (DEEP SCAN & FLAGGED DATA)
# ============================================================
def audit_duplicates(df, desc_col=None):
    findings = []
    flagged = pd.DataFrame()
    if desc_col and desc_col in df.columns:
        flagged = df[df.duplicated(subset=[desc_col], keep=False)]
    else:
        flagged = df[df.duplicated(keep=False)]
    if not flagged.empty:
        findings.append({"module": "Data Duplikat", "risk_level": "Tinggi", "title": f"{len(flagged)} baris data duplikat", "detail": "Terdapat transaksi dengan nilai/deskripsi yang sama persis.", "recommendation": "Lakukan sampling pada baris tersebut."})
    return findings, flagged

def audit_empty(df):
    findings = []
    for col in df.columns:
        cnt = df[col].isna().sum()
        if cnt > 0:
            pct = cnt / len(df) * 100
            if pct > 10:
                findings.append({"module": "Data Kosong", "risk_level": "Sedang" if pct < 30 else "Tinggi", "title": f"Kolom '{col}' memiliki {cnt} data kosong ({pct:.1f}%)", "detail": "Input data tidak lengkap.", "recommendation": "Minta divisi terkait melengkapi data."})
    return findings, pd.DataFrame()

def audit_weekend(df, date_col):
    findings, flagged = [], pd.DataFrame()
    try:
        dates = pd.to_datetime(df[date_col], errors="coerce")
        flagged = df[dates.dt.dayofweek >= 5]
        if not flagged.empty:
            findings.append({"module": "Transaksi Weekend", "risk_level": "Tinggi", "title": f"{len(flagged)} transaksi di hari Sabtu/Minggu", "detail": "Transaksi di luar hari kerja berisiko tinggi.", "recommendation": "Periksa otorisasi transaksi akhir pekan."})
    except: pass
    return findings, flagged

def audit_round_numbers(df, amount_col):
    findings, flagged = [], pd.DataFrame()
    try:
        s = pd.to_numeric(df[amount_col], errors="coerce")
        flagged = df[(s >= 5000000) & (s % 5000000 == 0)] # Diubah jadi >= 5 Juta
        if not flagged.empty:
            findings.append({"module": "Angka Bulat Mencurigakan", "risk_level": "Sedang", "title": f"{len(flagged)} transaksi nominal bulat besar (>= 5 Juta)", "detail": "Nominal bulat besar sering digunakan untuk estimasi/mark-up.", "recommendation": "Cocokkan dengan invoice asli."})
    except: pass
    return findings, flagged

def audit_outliers_iqr(df, amount_col):
    findings, flagged = [], pd.DataFrame()
    try:
        s = pd.to_numeric(df[amount_col], errors="coerce").dropna()
        if s.empty: return findings, flagged
        Q1, Q3 = s.quantile(0.25), s.quantile(0.75)
        IQR = Q3 - Q1
        lower, upper = Q1 - 1.5 * IQR, Q3 + 1.5 * IQR
        flagged = df[(pd.to_numeric(df[amount_col], errors="coerce") < lower) | (pd.to_numeric(df[amount_col], errors="coerce") > upper)]
        if not flagged.empty:
            findings.append({"module": "Anomali Nominal (Outlier)", "risk_level": "Tinggi", "title": f"{len(flagged)} nilai ekstrem (di luar batas IQR)", "detail": f"Batas wajar: {lower:,.0f} s.d {upper:,.0f}", "recommendation": "Periksa transaksi terbesar/terkecil, pastikan bukan typo."})
    except: pass
    return findings, flagged

def audit_balance(df, debit_col, credit_col):
    findings, flagged = [], pd.DataFrame()
    try:
        d = pd.to_numeric(df[debit_col], errors="coerce").fillna(0)
        c = pd.to_numeric(df[credit_col], errors="coerce").fillna(0)
        diff = abs(d.sum() - c.sum())
        if diff > 1:
            findings.append({"module": "Debit/Kredit", "risk_level": "Tinggi", "title": f"Selisih Debit & Kredit: {diff:,.2f}", "detail": f"Total D: {d.sum():,.2f} | Total K: {c.sum():,.2f}", "recommendation": "Jurnal tidak balance."})
    except: pass
    return findings, flagged

def audit_negative_values(df, amount_col):
    findings, flagged = [], pd.DataFrame()
    try:
        s = pd.to_numeric(df[amount_col], errors="coerce")
        flagged = df[s < 0]
        if not flagged.empty:
            findings.append({"module": "Nominal Negatif", "risk_level": "Tinggi", "title": f"{len(flagged)} transaksi bernilai negatif", "detail": "Nominal negatif pada kolom pengeluaran/pemasukan bisa jadi indikasi reversal atau error.", "recommendation": "Verifikasi apakah transaksi pembalik (void) atau salah input."})
    except: pass
    return findings, flagged

def audit_suspicious_words(df, desc_col):
    findings, flagged = [], pd.DataFrame()
    keywords = ["adjustment", "adj", "void", "koreksi", "manual", "write-off", "hapus"]
    try:
        pattern = '|'.join(keywords)
        flagged = df[df[desc_col].astype(str).str.lower().str.contains(pattern, na=False)]
        if not flagged.empty:
            findings.append({"module": "Kata Kunci Mencurigakan", "risk_level": "Sedang", "title": f"{len(flagged)} transaksi mengandung kata kunci adjustment/void", "detail": "Transaksi manual atau penyesuaian sering menjadi celah manipulasi.", "recommendation": "Minta penjelasan tertulis dari pembuat jurnal."})
    except: pass
    return findings, flagged

# ============================================================
# EXPORT FUNCTIONS (PDF & EXCEL)
# ============================================================
def generate_excel(flagged_data_dict, findings_list):
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
        # Sheet 1: Ringkasan Temuan
        df_findings = pd.DataFrame(findings_list)
        if not df_findings.empty:
            df_findings.columns = ["Modul", "Tingkat Risiko", "Judul", "Detail", "Rekomendasi"]
            df_findings.to_excel(writer, sheet_name="Ringkasan Temuan", index=False)
        
        # Sheet 2 dst: Data Mentah yang Bermasalah
        for modul, df_flag in flagged_data_dict.items():
            if not df_flag.empty:
                sheet_name = modul[:31] # Excel max 31 chars
                df_flag.to_excel(writer, sheet_name=sheet_name, index=False)
                
    buffer.seek(0)
    return buffer

def generate_pdf(findings_list):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=30, leftMargin=30, topMargin=30, bottomMargin=30)
    elements = []
    
    # Styles
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(name='Title', parent=styles['Heading1'], textColor=colors.HexColor('#7A1F3D'), alignment=1)
    heading_style = ParagraphStyle(name='Heading', parent=styles['Heading2'], textColor=colors.HexColor('#FF5C8B'))
    normal_style = styles['Normal']
    
    # Title
    elements.append(Paragraph("Laporan Hasil Audit FINORA", title_style))
    elements.append(Paragraph(f"Tanggal Cetak: {datetime.now().strftime('%d %B %Y %H:%M')}", normal_style))
    elements.append(Spacer(1, 15))
    
    if not findings_list:
        elements.append(Paragraph("✅ Tidak ada temuan pelanggaran (High-Level).", normal_style))
    else:
        elements.append(Paragraph(f"Ditemukan {len(findings_list)} indikasi masalah:", heading_style))
        elements.append(Spacer(1, 10))
        
        for i, f in enumerate(findings_list):
            # Kotak per temuan
            elements.append(Paragraph(f"<b>{i+1}. [{f['risk_level']}] {f['title']}</b>", normal_style))
            elements.append(Paragraph(f"<i>Modul: {f['module']}</i>", normal_style))
            elements.append(Paragraph(f"Analisis: {f['detail']}", normal_style))
            elements.append(Paragraph(f"Rekomendasi: {f['recommendation']}", normal_style))
            elements.append(Spacer(1, 10))

    doc.build(elements)
    buffer.seek(0)
    return buffer

# ============================================================
# UI COMPONENTS
# ============================================================
def finora_header(title, subtitle):
    st.markdown(f'<div class="finora-header"><h1>💗 {title}</h1><p>{subtitle}</p></div>', unsafe_allow_html=True)

def render_audit_results(all_findings, flagged_data, uploaded_file):
    if not all_findings:
        st.success("✅ Luar biasa! Tidak ada temuan anomali pada data ini.")
        return

    # 1. Hitung Skor Risiko
    high_count = sum(1 for f in all_findings if f["risk_level"] == "Tinggi")
    med_count = sum(1 for f in all_findings if f["risk_level"] == "Sedang")
    score = min(100, (high_count * 20) + (med_count * 10))
    color = "#C62828" if score > 50 else "#EF6C00" if score > 20 else "#2E7D32"

    st.markdown(f"<h3>🚨 Skor Risiko Audit: <span style='color:{color}; font-size:36px;'>{score}/100</span></h3>", unsafe_allow_html=True)
    
    # 2. Tabs untuk Hasil
    tab_sum, tab_grafik, tab_data, tab_ai = st.tabs(["📝 Daftar Temuan", "📊 Visualisasi Grafik", "📂 Data Terindikasi", "🤖 Rekomendasi AI"])
    
    with tab_sum:
        for f in all_findings:
            with st.expander(f"⚠️ [{f['risk_level']}] {f['title']}"):
                st.info(f"**Analisis:** {f['detail']}")
                st.warning(f"**Rekomendasi Auditor:** {f['recommendation']}")
                
        # Tombol Export
        st.markdown("### 📥 Download Laporan Audit")
        col1, col2 = st.columns(2)
        
        # Excel Download
        excel_buffer = generate_excel(flagged_data, all_findings)
        col1.download_button(
            label="⬇️ Download Excel (Multi-Sheet)",
            data=excel_buffer,
            file_name=f"FINORA_Audit_{datetime.now().strftime('%Y%m%d')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True
        )
        
        # PDF Download
        pdf_buffer = generate_pdf(all_findings)
        col2.download_button(
            label="⬇️ Download PDF Report",
            data=pdf_buffer,
            file_name=f"FINORA_Audit_{datetime.now().strftime('%Y%m%d')}.pdf",
            mime="application/pdf",
            use_container_width=True
        )
        
        # Simpan DB
        fid = save_file(uploaded_file.name, uploaded_file.getvalue())
        aid = save_analysis(fid, "Audit Pro", ["audit_v3"], {"total_findings": len(all_findings), "score": score})
        save_findings(aid, all_findings)
        st.success(f"💾 Analisis berhasil disimpan ke sistem (ID #{aid}).")

    with tab_grafik:
        c1, c2 = st.columns(2)
        # Grafik 1: Distribusi Risiko
        with c1:
            st.markdown("**Distribusi Temuan Berdasarkan Risiko**")
            risk_df = pd.DataFrame(all_findings)["risk_level"].value_counts().reset_index()
            risk_df.columns = ["Tingkat", "Jumlah"]
            fig_pie = px.pie(risk_df, names="Tingkat", values="Jumlah", color="Tingkat",
                             color_discrete_map={"Tinggi": "#C62828", "Sedang": "#EF6C00", "Rendah": "#2E7D32"},
                             hole=0.4)
            st.plotly_chart(fig_pie, use_container_width=True)
            
        # Grafik 2: Bar Chart Modul
        with c2:
            st.markdown("**Modul Pengujian yang Terpicu**")
            mod_df = pd.DataFrame(all_findings)["module"].value_counts().reset_index()
            mod_df.columns = ["Modul", "Jumlah"]
            fig_bar = px.bar(mod_df, x="Jumlah", y="Modul", orientation='h', color_discrete_sequence=["#FF5C8B"])
            st.plotly_chart(fig_bar, use_container_width=True)

    with tab_data:
        st.markdown("Silakan unduh file Excel di tab sebelumnya untuk melihat data secara lengkap. Berikut preview data yang terindikasi bermasalah:")
        for modul, df_flag in flagged_data.items():
            if not df_flag.empty:
                st.markdown(f"**Data Terindikasi: {modul}**")
                st.dataframe(df_flag.head(10), use_container_width=True)

    with tab_ai:
        st.markdown("Gunakan asisten FINORA AI untuk membedah hasil audit ini lebih dalam.")
        q = st.text_input("Tanyakan sesuatu tentang hasil audit ini (Contoh: Mengapa ada transaksi weekend?):")
        if st.button("Tanya AI"):
            st.info("Fitur AI memerlukan konfigurasi API Key (segera hadir dalam pembaruan).")

# ============================================================
# PAGES
# ============================================================
def page_audit():
    finora_header("Audit Otomatis Pro", "Pemeriksaan data mendalam, anomali, dan fraud detection")
    
    with st.container():
        st.markdown("### 1. Upload Data")
        c1, c2 = st.columns([3,1])
        uploaded = c1.file_uploader("Upload File Excel/CSV", type=["xlsx", "csv"])
        header_row = c2.number_input("Header di baris ke-", min_value=0, value=0, help="Ubah ke 1 atau 2 jika baris pertama Excel adalah Judul.")
    
    if uploaded:
        df = pd.read_excel(uploaded, header=header_row) if uploaded.name.endswith('xlsx') else pd.read_csv(uploaded, header=header_row)
        
        st.markdown("### 2. Pemetaan Kolom (Wajib)")
        c1, c2, c3 = st.columns(3)
        date_col = c1.selectbox("Pilih Kolom Tanggal", [""] + list(df.columns))
        amount_col = c2.selectbox("Pilih Kolom Nominal", [""] + list(df.columns))
        desc_col = c3.selectbox("Pilih Kolom Deskripsi", [""] + list(df.columns))
        
        c4, c5 = st.columns(2)
        debit_col = c4.selectbox("Pilih Kolom Debit (Opsional)", [""] + list(df.columns))
        credit_col = c5.selectbox("Pilih Kolom Kredit (Opsional)", [""] + list(df.columns))
        
        st.markdown("### 3. Pilih Pengujian & Jalankan")
        st.info("Sistem akan memindai data berdasarkan algoritma audit (Outlier, Duplikat, Weekend, dll).")
        
        if st.button("🚀 JALANKAN AUDIT MENDALAM", use_container_width=True):
            with st.spinner("Memproses data..."):
                all_findings = []
                flagged_data = {}
                
                # Jalankan fungsi audit
                if desc_col:
                    f, flag = audit_duplicates(df, desc_col)
                    all_findings.extend(f); flagged_data["Duplikat"] = flag
                    
                f, flag = audit_empty(df)
                all_findings.extend(f); flagged_data["Data Kosong"] = flag
                
                if date_col:
                    f, flag = audit_weekend(df, date_col)
                    all_findings.extend(f); flagged_data["Transaksi Weekend"] = flag
                    
                if amount_col:
                    f, flag = audit_round_numbers(df, amount_col)
                    all_findings.extend(f); flagged_data["Nominal Bulat"] = flag
                    
                    f, flag = audit_outliers_iqr(df, amount_col)
                    all_findings.extend(f); flagged_data["Outlier IQR"] = flag
                    
                    f, flag = audit_negative_values(df, amount_col)
                    all_findings.extend(f); flagged_data["Nominal Negatif"] = flag
                    
                if debit_col and credit_col:
                    f, flag = audit_balance(df, debit_col, credit_col)
                    all_findings.extend(f); flagged_data["Debit Kredit"] = flag
                    
                if desc_col:
                    f, flag = audit_suspicious_words(df, desc_col)
                    all_findings.extend(f); flagged_data["Kata Kunci"] = flag

                # Render Hasil
                st.markdown("---")
                render_audit_results(all_findings, flagged_data, uploaded)

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
        st.dataframe(findings, use_container_width=True)

# ============================================================
# MAIN
# ============================================================
def main():
    st.sidebar.markdown('<h1 style="color:#FF5C8B;text-align:center;">💗 FINORA</h1>', unsafe_allow_html=True)
    st.sidebar.markdown('<p style="text-align:center;font-size:12px;color:#7A1F3D;">Financial Intelligence<br>Platform</p>', unsafe_allow_html=True)
    st.sidebar.markdown("---")
    
    nav = st.sidebar.radio("Navigasi", ["Dashboard", "Audit Pro", "Simpan"])
    
    st.sidebar.markdown("---")
    st.sidebar.caption("⚠️ Temuan bersifat indikasi dan perlu verifikasi dokumen fisik.")
    
    if nav == "Dashboard": page_dashboard()
    elif nav == "Audit Pro": page_audit()
    elif nav == "Simpan": page_simpan()

if __name__ == "__main__":
    main()
