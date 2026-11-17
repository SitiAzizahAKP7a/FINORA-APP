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

from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
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

def fetch_analysis_by_id(aid):
    conn = get_conn()
    df = pd.read_sql_query("""
        SELECT a.id, a.summary_json, f.filename 
        FROM analyses a JOIN files f ON a.file_id = f.id 
        WHERE a.id=?
    """, conn, params=(aid,))
    conn.close(); return df.iloc[0] if not df.empty else None

def fetch_findings(aid):
    conn = get_conn()
    df = pd.read_sql_query("SELECT * FROM findings WHERE analysis_id=?", conn, params=(aid,))
    conn.close(); return df

# ============================================================
# SMART AUTO-DETECT COLUMNS
# ============================================================
def auto_detect_columns(df):
    mapping = {"date": None, "amount": None, "desc": None, "debit": None, "credit": None, "budget": None, "invoice": None}
    for col in df.columns:
        col_lower = str(col).lower()
        if not mapping["date"] and any(k in col_lower for k in ["tanggal", "tgl", "date", "waktu", "periode"]):
            mapping["date"] = col
        if not mapping["amount"] and any(k in col_lower for k in ["actual", "realisasi", "nominal", "jumlah", "total", "nilai", "amount", "harga"]):
            mapping["amount"] = col
        if not mapping["budget"] and any(k in col_lower for k in ["anggaran", "budget", "pagu"]):
            mapping["budget"] = col
        if not mapping["desc"] and any(k in col_lower for k in ["deskripsi", "keterangan", "uraian", "category", "kategori", "nama", "item", "barang"]):
            mapping["desc"] = col
        if not mapping["debit"] and any(k in col_lower for k in ["debit", "masuk", "pemasukan", "in"]):
            mapping["debit"] = col
        if not mapping["credit"] and any(k in col_lower for k in ["kredit", "keluar", "pengeluaran", "out"]):
            mapping["credit"] = col
        if not mapping["invoice"] and any(k in col_lower for k in ["invoice", "faktur", "nota", "ref", "no."]):
            mapping["invoice"] = col
    return mapping

# ============================================================
# AUDIT ENGINE V5 (SMART & VERSATILE)
# ============================================================
def audit_empty(df, cols_to_check):
    findings, flagged = [], pd.DataFrame()
    masks = [df[c].isna() for c in cols_to_check if c]
    if masks:
        combined_mask = masks[0]
        for m in masks[1:]: combined_mask |= m
        flagged = df[combined_mask]
        if not flagged.empty:
            findings.append({"module": "Kelengkapan Data", "risk_level": "Tinggi", "title": f"{len(flagged)} baris memiliki data vital kosong", "detail": "Ada data kosong pada kolom Tanggal/Nominal/Deskripsi.", "recommendation": "Lengkapi data sebelum diproses."})
    return findings, flagged

def audit_duplicates(df, cols_to_check):
    findings, flagged = [], pd.DataFrame()
    if cols_to_check:
        flagged = df[df.duplicated(subset=cols_to_check, keep=False)]
        if not flagged.empty:
            findings.append({"module": "Data Duplikat", "risk_level": "Tinggi", "title": f"{len(flagged)} baris transaksi ganda (100% sama)", "detail": "Terdapat transaksi dengan nilai/deskripsi yang sama persis.", "recommendation": "Pastikan bukan input dua kali (double-entry)."})
    return findings, flagged

def audit_negative(df, amount_col):
    findings, flagged = [], pd.DataFrame()
    if amount_col:
        s = pd.to_numeric(df[amount_col], errors="coerce")
        flagged = df[s < 0]
        if not flagged.empty:
            findings.append({
                "module": "Observasi Nilai Negatif", 
                "risk_level": "Rendah", 
                "title": f"{len(flagged)} transaksi bernilai negatif", 
                "detail": "Ditemukan nilai minus pada kolom nominal. Nilai negatif tidak selalu salah (bisa jadi retur, diskon, atau kredit pendapatan), namun perlu dipastikan kewajarannya.", 
                "recommendation": "Lakukan uji petik untuk memastikan transaksi negatif ini memiliki memo retur atau bukti koreksi yang sah."
            })
    return findings, flagged

def audit_overbudget(df, budget_col, amount_col):
    findings, flagged = [], pd.DataFrame()
    if budget_col and amount_col:
        try:
            b = pd.to_numeric(df[budget_col], errors="coerce").fillna(0)
            a = pd.to_numeric(df[amount_col], errors="coerce").fillna(0)
            flagged = df[a > b]
            if not flagged.empty:
                findings.append({"module": "Overbudget (Melebihi Pagu)", "risk_level": "Tinggi", "title": f"{len(flagged)} akun melebihi pagu anggaran", "detail": "Nilai realisasi lebih besar daripada anggaran yang ditetapkan.", "recommendation": "Minta penjelasan terkait pergeseran atau kelebihan anggaran."})
        except: pass
    return findings, flagged

def audit_weekend(df, date_col):
    findings, flagged = [], pd.DataFrame()
    if date_col:
        try:
            dates = pd.to_datetime(df[date_col], errors="coerce")
            flagged = df[dates.dt.dayofweek >= 5]
            if not flagged.empty:
                findings.append({"module": "Transaksi Weekend", "risk_level": "Tinggi", "title": f"{len(flagged)} transaksi di hari Sabtu/Minggu", "detail": "Transaksi di luar hari kerja berisiko tinggi.", "recommendation": "Periksa otorisasi transaksi akhir pekan."})
        except: pass
    return findings, flagged

def audit_suspicious_words(df, desc_col):
    findings, flagged = [], pd.DataFrame()
    keywords = ["adjustment", "adj", "void", "koreksi", "manual", "write-off", "hapus"]
    if desc_col:
        try:
            pattern = '|'.join(keywords)
            flagged = df[df[desc_col].astype(str).str.lower().str.contains(pattern, na=False)]
            if not flagged.empty:
                findings.append({"module": "Kata Kunci Mencurigakan", "risk_level": "Sedang", "title": f"{len(flagged)} transaksi mengandung kata kunci adjustment/void", "detail": "Transaksi manual sering menjadi celah manipulasi.", "recommendation": "Minta penjelasan tertulis dari pembuat jurnal."})
        except: pass
    return findings, flagged

def audit_outliers_iqr(df, amount_col):
    findings, flagged = [], pd.DataFrame()
    if amount_col:
        try:
            s = pd.to_numeric(df[amount_col], errors="coerce").dropna()
            if not s.empty:
                Q1, Q3 = s.quantile(0.25), s.quantile(0.75)
                IQR = Q3 - Q1
                lower, upper = Q1 - 1.5 * IQR, Q3 + 1.5 * IQR
                flagged = df[(pd.to_numeric(df[amount_col], errors="coerce") < lower) | (pd.to_numeric(df[amount_col], errors="coerce") > upper)]
                if not flagged.empty:
                    findings.append({"module": "Anomali Nominal (Outlier)", "risk_level": "Tinggi", "title": f"{len(flagged)} nilai ekstrem (di luar batas IQR)", "detail": f"Batas wajar: {lower:,.0f} s.d {upper:,.0f}", "recommendation": "Uji petik transaksi terbesar/terkecil, pastikan bukan typo."})
        except: pass
    return findings, flagged

# ============================================================
# EXPORT FUNCTIONS
# ============================================================
def generate_excel(flagged_data_dict, findings_list):
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
        df_findings = pd.DataFrame(findings_list)
        if not df_findings.empty:
            df_findings.columns = ["Modul", "Tingkat Risiko", "Judul", "Detail", "Rekomendasi"]
            df_findings.to_excel(writer, sheet_name="Ringkasan Temuan", index=False)
        for modul, df_flag in flagged_data_dict.items():
            if not df_flag.empty:
                sheet_name = modul[:31]
                df_flag.to_excel(writer, sheet_name=sheet_name, index=False)
    buffer.seek(0)
    return buffer

from reportlab.platypus import Table, TableStyle

def generate_pdf(entitas, periode, filename, score, findings_list, flagged_data):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=30, leftMargin=30, topMargin=30, bottomMargin=30)
    elements = []
    
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(name='Title', parent=styles['Heading1'], textColor=colors.HexColor('#7A1F3D'), alignment=1)
    heading_style = ParagraphStyle(name='Heading', parent=styles['Heading2'], textColor=colors.HexColor('#FF5C8B'))
    normal_style = styles['Normal']
    
    # 1. KOP KKA
    elements.append(Paragraph("KERTAS KERJA AUDIT (KKA)", title_style))
    elements.append(Paragraph("FINORA Financial Intelligence System", normal_style))
    elements.append(Spacer(1, 10))
    
    header_data = [
        ["Nama Entitas", f": {entitas if entitas else '-'}"],
        ["Periode Audit", f": {periode if periode else '-'}"],
        ["Nama File", f": {filename}"],
        ["Tanggal Pemeriksaan", f": {datetime.now().strftime('%d %B %Y')}"],
        ["Skor Risiko", f": {score}/100"]
    ]
    t_header = Table(header_data, colWidths=[120, 350])
    t_header.setStyle(TableStyle([
        ('FONTNAME', (0,0), (-1,-1), 'Helvetica'),
        ('FONTSIZE', (0,0), (-1,-1), 10),
        ('BOTTOMPADDING', (0,0), (-1,-1), 6),
    ]))
    elements.append(t_header)
    elements.append(Spacer(1, 20))
    
    # 2. RINGKASAN TEMUAN
    elements.append(Paragraph("A. Ringkasan Temuan (Summary of Findings)", heading_style))
    elements.append(Spacer(1, 10))
    
    summary_data = [["No", "Kode", "Kategori Uji", "Risiko", "Deskripsi Singkat"]]
    for idx, f in enumerate(findings_list, 1):
        summary_data.append([str(idx), f"F-{idx:02d}", f['module'], f['risk_level'], f['title']])
    
    t_summary = Table(summary_data, colWidths=[30, 50, 100, 60, 230])
    t_summary.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#FF5C8B')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
        ('ALIGN', (0,0), (-1,-1), 'LEFT'),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,-1), 9),
        ('BOTTOMPADDING', (0,0), (-1,0), 8),
        ('GRID', (0,0), (-1,-1), 1, colors.grey),
    ]))
    elements.append(t_summary)
    elements.append(Spacer(1, 20))
    
    # 3. RINCIAN TEMUAN
    elements.append(Paragraph("B. Rincian Temuan, Bukti, dan Rekomendasi", heading_style))
    elements.append(Spacer(1, 10))
    
    for idx, f in enumerate(findings_list, 1):
        elements.append(Paragraph(f"<b>F-{idx:02d}: {f['title']}</b>", normal_style))
        elements.append(Paragraph(f"<b>1. Kondisi:</b> {f['detail']}", normal_style))
        elements.append(Paragraph(f"<b>2. Rekomendasi:</b> {f['recommendation']}", normal_style))
        
        # Tampilkan Bukti Data Mentah ke PDF
        modul_name = f['module']
        df_flag = pd.DataFrame()
        if modul_name in flagged_data and not flagged_data[modul_name].empty:
            df_flag = flagged_data[modul_name]
        elif "Kelengkapan" in modul_name and "Kelengkapan Kosong" in flagged_data:
            df_flag = flagged_data["Kelengkapan Kosong"]
        elif "Duplikat" in modul_name and "Duplikasi Ekstrem" in flagged_data:
            df_flag = flagged_data["Duplikasi Ekstrem"]
        elif "Negatif" in modul_name and "Nilai Negatif" in flagged_data:
            df_flag = flagged_data["Nilai Negatif"]
        elif "Overbudget" in modul_name and "Overbudget" in flagged_data:
            df_flag = flagged_data["Overbudget"]

        if not df_flag.empty:
            elements.append(Spacer(1, 5))
            elements.append(Paragraph("<i>Bukti Audit (Data Terindikasi):</i>", normal_style))
            
            # Ambil max 5 baris untuk PDF agar tidak kepanjangan
            preview_df = df_flag.head(5)
            raw_data = [preview_df.columns.tolist()] + preview_df.values.tolist()
            
            # Bersihkan data dari tipe data pandas yang aneh
            raw_data = [[str(cell) for cell in row] for row in raw_data]
            
            t_raw = Table(raw_data)
            t_raw.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (-1,0), colors.lightgrey),
                ('GRID', (0,0), (-1,-1), 0.5, colors.grey),
                ('FONTSIZE', (0,0), (-1,-1), 8),
            ]))
            elements.append(t_raw)
        
        elements.append(Spacer(1, 20))

    # 4. KESIMPULAN
    elements.append(Paragraph("C. Kesimpulan Auditor", heading_style))
    elements.append(Paragraph(f"Ditemukan {len(findings_list)} indikasi masalah. Data memerlukan perbaikan dan verifikasi dokumen lebih lanjut.", normal_style))

    doc.build(elements)
    buffer.seek(0)
    return buffer
# ============================================================
# UI COMPONENTS & KKA RENDER
# ============================================================
def finora_header(title, subtitle):
    st.markdown(f'<div class="finora-header"><h1>💗 {title}</h1><p>{subtitle}</p></div>', unsafe_allow_html=True)

def render_audit_results(all_findings, flagged_data, filename, entitas, periode, score, is_save_view=False):
    if not all_findings:
        st.success("✅ Luar biasa! Tidak ada temuan pelanggaran pada data ini. KKA Bersih.")
        return

    color = "#C62828" if score > 50 else "#EF6C00" if score > 20 else "#2E7D32"

    # HEADER KKA
    st.markdown(f"""
    <div style="background-color: white; padding: 20px; border-radius: 10px; border: 2px solid #FF5C8B; margin-bottom: 20px;">
        <h2 style="color: #7A1F3D; text-align: center; margin-bottom: 5px;">KERTAS KERJA AUDIT (KKA)</h2>
        <p style="text-align: center; color: gray; margin-top: 0;">FINORA Financial Intelligence System</p>
        <hr>
        <table style="width: 100%; font-size: 14px;">
            <tr><td><b>Nama Entitas</b></td><td>: {entitas if entitas else '-'}</td></tr>
            <tr><td><b>Periode Audit</b></td><td>: {periode if periode else '-'}</td></tr>
            <tr><td><b>Nama File</b></td><td>: {filename}</td></tr>
            <tr><td><b>Tanggal Pemeriksaan</b></td><td>: {datetime.now().strftime('%d %B %Y')}</td></tr>
            <tr><td><b>Skor Risiko</b></td><td>: <span style='color:{color}; font-weight:bold; font-size:18px;'>{score}/100</span></td></tr>
        </table>
    </div>
    """, unsafe_allow_html=True)
    
    tab_kka, tab_grafik = st.tabs(["📝 Laporan Temuan & Bukti", "📊 Visualisasi Grafik"])
    
    with tab_kka:
        st.markdown("### A. Ringkasan Temuan (Summary of Findings)")
        
        summary_data = []
        for idx, f in enumerate(all_findings, 1):
            summary_data.append({
                "No": idx,
                "Kode Temuan": f"F-{idx:02d}",
                "Kategori Uji": f["module"],
                "Tingkat Risiko": f["risk_level"],
                "Deskripsi Singkat": f["title"]
            })
        st.table(pd.DataFrame(summary_data))

        st.markdown("---")
        st.markdown("### B. Rincian Temuan, Bukti, dan Rekomendasi")
        st.info("Klik masing-masing temuan di bawah ini untuk melihat Kondisi, Kriteria, Bukti (Data), dan Rekomendasi.")
        
        for idx, f in enumerate(all_findings, 1):
            with st.expander(f"[{f['risk_level']}] F-{idx:02d}: {f['title']}"):
                c1, c2 = st.columns([1, 2])
                with c1:
                    st.markdown(f"**1. Kondisi (Condition)**\n{f['detail']}")
                    st.markdown(f"**2. Kriteria (Criteria)**\nSOP Internal / Prinsip Akuntansi (SAK/PSAK) / Ketentuan Perpajakan.")
                with c2:
                    st.markdown(f"**3. Sebab (Cause)**\nKelemahan sistem kontrol internal atau kesalahan input (human error).")
                    st.markdown(f"**4. Akibat (Effect)**\nBerpotensi menimbulkan kerugian finansial, denda, atau laporan keuangan tidak wajar.")
                
                st.error(f"**5. Rekomendasi Auditor:** {f['recommendation']}")
                
                # TAMPILKAN BUKTI DATA MENTAH (Langsung di dalam KKA)
                modul_name = f['module']
                if modul_name in flagged_data and not flagged_data[modul_name].empty:
                    st.markdown("**📂 Bukti Audit (Data Terindikasi):**")
                    st.dataframe(flagged_data[modul_name].head(10), use_container_width=True)
                elif "Kelengkapan" in modul_name and "Kelengkapan Kosong" in flagged_data:
                     st.markdown("**📂 Bukti Audit (Data Kosong):**")
                     st.dataframe(flagged_data["Kelengkapan Kosong"].head(10), use_container_width=True)
                elif "Duplikat" in modul_name and "Duplikasi Ekstrem" in flagged_data:
                     st.markdown("**📂 Bukti Audit (Data Duplikat):**")
                     st.dataframe(flagged_data["Duplikasi Ekstrem"].head(10), use_container_width=True)
                elif "Negatif" in modul_name and "Nilai Negatif" in flagged_data:
                     st.markdown("**📂 Bukti Audit (Nilai Negatif):**")
                     st.dataframe(flagged_data["Nilai Negatif"].head(10), use_container_width=True)
                elif "Overbudget" in modul_name and "Overbudget" in flagged_data:
                     st.markdown("**📂 Bukti Audit (Overbudget):**")
                     st.dataframe(flagged_data["Overbudget"].head(10), use_container_width=True)

        st.markdown("---")
        st.markdown("### C. Kesimpulan Auditor")
        st.warning(f"Ditemukan **{len(all_findings)} indikasi masalah**. Kesimpulan: Data memerlukan perbaikan dan verifikasi dokumen lebih lanjut sebelum dapat disajikan sebagai laporan final.")
        
        st.markdown("### 📥 Download Kertas Kerja Audit")
        col1, col2 = st.columns(2)
        excel_buffer = generate_excel(flagged_data, all_findings)
        col1.download_button("⬇️ Download Excel (Multi-Sheet)", data=excel_buffer, file_name=f"KKA_{entitas}_{datetime.now().strftime('%Y%m%d')}.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", use_container_width=True)
        pdf_buffer = generate_pdf(all_findings)
        col2.download_button("⬇️ Download PDF Report", data=pdf_buffer, file_name=f"KKA_{entitas}_{datetime.now().strftime('%Y%m%d')}.pdf", mime="application/pdf", use_container_width=True)
        
        # LOGIKA SIMPAN KE DATABASE (Hanya jalan kalau bukan dari menu riwayat)
        if not is_save_view:
            # Rekonstruksi dictionary of dataframes ke format JSON agar bisa disimpan
            flagged_json = {k: v.to_dict(orient="records") for k, v in flagged_data.items() if not v.empty}
            summary_to_save = {
                "total_findings": len(all_findings), 
                "score": score, 
                "entitas": entitas,
                "periode": periode,
                "flagged_data": flagged_json
            }
            # (Simpan fungsi ini ada di luar render_audit_results karena butuh akses uploaded_file)
            return summary_to_save

    with tab_grafik:
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Distribusi Tingkat Risiko**")
            risk_df = pd.DataFrame(all_findings)["risk_level"].value_counts().reset_index()
            risk_df.columns = ["Tingkat", "Jumlah"]
            fig_pie = px.pie(risk_df, names="Tingkat", values="Jumlah", color="Tingkat", color_discrete_map={"Tinggi": "#C62828", "Sedang": "#EF6C00", "Rendah": "#2E7D32"}, hole=0.4)
            st.plotly_chart(fig_pie, use_container_width=True)
            
        with c2:
            st.markdown("**Prosedur Audit yang Menemukan Indikasi**")
            mod_df = pd.DataFrame(all_findings)["module"].value_counts().reset_index()
            mod_df.columns = ["Modul", "Jumlah"]
            fig_bar = px.bar(mod_df, x="Jumlah", y="Modul", orientation='h', color_discrete_sequence=["#FF5C8B"])
            st.plotly_chart(fig_bar, use_container_width=True)

# ============================================================
# PAGES
# ============================================================
def page_audit():
    finora_header("Audit Profesional", "Sistem deteksi otomatis (Laporan Pemerintah, Perusahaan, Pembelian, Penjualan)")
    
    with st.container():
        st.markdown("### 1. Upload Data")
        c1, c2 = st.columns([3,1])
        uploaded = c1.file_uploader("Upload File Excel/CSV", type=["xlsx", "csv"])
        header_row = c2.number_input("Header di baris ke-", min_value=0, value=0, help="Ubah ke 1 atau 2 jika baris pertama Excel adalah Judul.")
    
    if uploaded:
        df = pd.read_excel(uploaded, header=header_row) if uploaded.name.endswith('xlsx') else pd.read_csv(uploaded, header=header_row)
        
        detected = auto_detect_columns(df)
        
        st.markdown("### 2. Pemetaan Kolom (AI Auto-Detect)")
        st.success("✨ FINORA AI telah mendeteksi otomatis kolom pada file kamu. Jika ada yang salah, silakan koreksi di menu di bawah.")
        
        with st.expander("⚙️ Lihat / Koreksi Pemetaan Kolom"):
            c1, c2 = st.columns(2)
            date_col = c1.selectbox("Kolom Tanggal", [""] + list(df.columns), index=list(df.columns).index(detected["date"])+1 if detected["date"] else 0)
            desc_col = c2.selectbox("Kolom Deskripsi/Kategori", [""] + list(df.columns), index=list(df.columns).index(detected["desc"])+1 if detected["desc"] else 0)
            
            c3, c4, c5 = st.columns(3)
            amount_col = c3.selectbox("Kolom Nominal/Actual", [""] + list(df.columns), index=list(df.columns).index(detected["amount"])+1 if detected["amount"] else 0)
            budget_col = c4.selectbox("Kolom Anggaran/Pagu (Opsional)", [""] + list(df.columns), index=list(df.columns).index(detected["budget"])+1 if detected["budget"] else 0)
            invoice_col = c5.selectbox("Kolom No. Faktur/Invoice (Opsional)", [""] + list(df.columns), index=list(df.columns).index(detected["invoice"])+1 if detected["invoice"] else 0)
            
            c6, c7 = st.columns(2)
            debit_col = c6.selectbox("Kolom Debit (Opsional)", [""] + list(df.columns), index=list(df.columns).index(detected["debit"])+1 if detected["debit"] else 0)
            credit_col = c7.selectbox("Kolom Kredit (Opsional)", [""] + list(df.columns), index=list(df.columns).index(detected["credit"])+1 if detected["credit"] else 0)
        
        st.markdown("### 3. Informasi Kertas Kerja Audit (KKA)")
        c_kka1, c_kka2 = st.columns(2)
        entitas = c_kka1.text_input("Nama Entitas / Perusahaan", placeholder="Contoh: PT Maju Jaya / Pemkot X")
        periode = c_kka2.text_input("Periode Audit", placeholder="Contoh: Tahun Buku 2024")
        
        st.markdown("### 4. Jalankan Prosedur Audit")
        st.info("Sistem akan memindahkan data, menguji sampel, dan menyusun Kertas Kerja Audit.")
        
        if st.button("🚀 JALANKAN PROSEDUR AUDIT", use_container_width=True):
            with st.spinner("Memproses data..."):
                all_findings = []
                flagged_data = {}
                
                cols_to_check = [c for c in [date_col, amount_col or debit_col, desc_col] if c]
                f, flag = audit_empty(df, cols_to_check)
                all_findings.extend(f); flagged_data["Kelengkapan Kosong"] = flag

                subset_dup = [c for c in [date_col, amount_col or debit_col, desc_col, invoice_col] if c]
                f, flag = audit_duplicates(df, subset_dup)
                all_findings.extend(f); flagged_data["Duplikasi Ekstrem"] = flag

                if date_col:
                    f, flag = audit_weekend(df, date_col)
                    all_findings.extend(f); flagged_data["Transaksi Weekend"] = flag
                    
                if desc_col:
                    f, flag = audit_suspicious_words(df, desc_col)
                    all_findings.extend(f); flagged_data["Kata Kunci"] = flag

                if amount_col:
                    f, flag = audit_negative(df, amount_col)
                    all_findings.extend(f); flagged_data["Nilai Negatif"] = flag
                    
                    f, flag = audit_outliers_iqr(df, amount_col)
                    all_findings.extend(f); flagged_data["Outlier IQR"] = flag
                
                if budget_col and amount_col:
                    f, flag = audit_overbudget(df, budget_col, amount_col)
                    all_findings.extend(f); flagged_data["Overbudget"] = flag

                high_count = sum(1 for f in all_findings if f["risk_level"] == "Tinggi")
                med_count = sum(1 for f in all_findings if f["risk_level"] == "Sedang")
                score = min(100, (high_count * 20) + (med_count * 10))

                st.markdown("---")
                # Render UI dan ambil data untuk disimpan
                summary_to_save = render_audit_results(all_findings, flagged_data, uploaded.name, entitas, periode, score, is_save_view=False)
                
                if summary_to_save:
                    fid = save_file(uploaded.name, uploaded.getvalue())
                    aid = save_analysis(fid, "Audit Profesional", ["audit_v5"], summary_to_save)
                    save_findings(aid, all_findings)
                    st.success(f"💾 KKA untuk **{entitas}** berhasil diarsipkan (ID #{aid}).")

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
    finora_header("Pusat Laporan & Arsip", "Riwayat analisis dan Kertas Kerja Audit tersimpan")
    df = fetch_analyses()
    if df.empty:
        st.info("Belum ada riwayat."); return
    st.dataframe(df, use_container_width=True)
    
    st.markdown("---")
    aid = st.selectbox("Pilih ID Analisis untuk membuka KKA lengkap", df["id"].tolist())
    
    if aid:
        an = fetch_analysis_by_id(aid)
        if an:
            summary_data = json.loads(an["summary_json"])
            
            # Rekonstruksi data
            entitas = summary_data.get("entitas", "-")
            periode = summary_data.get("periode", "-")
            score = summary_data.get("score", 0)
            raw_flagged = summary_data.get("flagged_data", {})
            flagged_data = {k: pd.DataFrame(v) for k, v in raw_flagged.items()}
            
            findings_df = fetch_findings(aid)
            findings_list = findings_df.to_dict("records")
            
            # Tampilkan KKA Persis Seperti Aslinya
            render_audit_results(findings_list, flagged_data, an["filename"], entitas, periode, score, is_save_view=True)

# ============================================================
# MAIN
# ============================================================
def main():
    st.sidebar.markdown('<h1 style="color:#FF5C8B;text-align:center;">💗 FINORA</h1>', unsafe_allow_html=True)
    st.sidebar.markdown('<p style="text-align:center;font-size:12px;color:#7A1F3D;">Financial Intelligence<br>Platform</p>', unsafe_allow_html=True)
    st.sidebar.markdown("---")
    
    nav = st.sidebar.radio("Navigasi", ["Dashboard", "Audit Profesional", "Simpan"])
    
    st.sidebar.markdown("---")
    st.sidebar.caption("⚠️ Temuan bersifat indikasi dan perlu verifikasi dokumen fisik.")
    
    if nav == "Dashboard": page_dashboard()
    elif nav == "Audit Profesional": page_audit()
    elif nav == "Simpan": page_simpan()

if __name__ == "__main__":
    main()
