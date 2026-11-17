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
from google import genai

from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

# ============================================================
# CONFIG & STYLE GLOBAL
# ============================================================
LOGO_FILENAME = "logo.png"

st.set_page_config(page_title="FINORA AI - Pro Audit", page_icon=LOGO_FILENAME if os.path.exists(LOGO_FILENAME) else "💗", layout="wide")

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
    if row:
        conn.close(); return row["id"]
    c.execute("INSERT INTO files(filename, file_hash, uploaded_at) VALUES (?,?,?)",
              (filename, file_hash, datetime.now().isoformat()))
    fid = c.lastrowid; conn.commit(); conn.close()
    return fid

def save_analysis(file_id, mode, selected_modules, summary):
    conn = get_conn(); c = conn.cursor()
    c.execute("INSERT INTO analyses(file_id, mode, selected_modules, status, summary_json, created_at) VALUES (?,?,?,?,?,?)",
              (file_id, mode, json.dumps(selected_modules), "completed", json.dumps(summary, default=str), datetime.now().isoformat()))
    aid = c.lastrowid; conn.commit(); conn.close()
    return aid

def save_findings(analysis_id, findings):
    if not findings:
        return
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
# AI ENGINE
# ============================================================
def get_gemini_client():
    api_key = st.secrets.get("GEMINI_API_KEY", os.environ.get("GEMINI_API_KEY", ""))
    if not api_key:
        return None
    return genai.Client(api_key=api_key)

def ask_gemini_stream(prompt, audit_context, system_role="Asisten Auditor Profesional"):
    try:
        client = get_gemini_client()
        if client is None:
            yield "⚠️ API Key Gemini belum diatur di Secrets."
            return

        model_id = "gemini-3.8-flash"

        full_prompt = f"""
        Kamu adalah FINORA AI, {system_role}.
        Jawab pertanyaan berdasarkan konteks data yang diberikan secara ringkas, padat, dan profesional.

        [KONTEKS DATA]
        {audit_context}

        [PERTANYAAN / INSTRUKSI]
        {prompt}
        """

        if hasattr(client, "interactions"):
            try:
                stream = client.interactions.create(model=model_id, input=full_prompt, stream=True)
                for event in stream:
                    delta = getattr(event, "delta", None)
                    if delta and getattr(delta, "text", None): yield delta.text
                    elif getattr(event, "text", None): yield event.text
                return
            except Exception:
                pass 

        response_stream = client.models.generate_content_stream(model=model_id, contents=full_prompt)
        for chunk in response_stream:
            if getattr(chunk, "text", None): yield chunk.text

    except Exception as e:
        yield f"\n\n⚠️ Terjadi kesalahan: {str(e)}"

# ============================================================
# AUDIT ENGINE V5 (FITUR LAMA - AMAN)
# ============================================================
def auto_detect_columns(df):
    mapping = {"date": None, "amount": None, "desc": None, "debit": None, "credit": None, "budget": None, "invoice": None}
    for col in df.columns:
        c = str(col).lower()
        if not mapping["date"] and any(k in c for k in ["tanggal", "tgl", "date", "waktu", "periode"]): mapping["date"] = col
        if not mapping["amount"] and any(k in c for k in ["actual", "realisasi", "nominal", "jumlah", "total", "nilai", "amount", "harga"]): mapping["amount"] = col
        if not mapping["budget"] and any(k in c for k in ["anggaran", "budget", "pagu"]): mapping["budget"] = col
        if not mapping["desc"] and any(k in c for k in ["deskripsi", "keterangan", "uraian", "category", "kategori", "nama", "item", "barang"]): mapping["desc"] = col
        if not mapping["debit"] and any(k in c for k in ["debit", "masuk", "pemasukan", "in"]): mapping["debit"] = col
        if not mapping["credit"] and any(k in c for k in ["kredit", "keluar", "pengeluaran", "out"]): mapping["credit"] = col
        if not mapping["invoice"] and any(k in c for k in ["invoice", "faktur", "nota", "ref", "no."]): mapping["invoice"] = col
    return mapping

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
            findings.append({"module": "Observasi Nilai Negatif", "risk_level": "Rendah", "title": f"{len(flagged)} transaksi bernilai negatif", "detail": "Ditemukan nilai minus pada kolom nominal.", "recommendation": "Lakukan uji petik untuk memastikan transaksi negatif memiliki memo retur/koreksi."})
    return findings, flagged

def audit_overbudget(df, budget_col, amount_col):
    findings, flagged = [], pd.DataFrame()
    if budget_col and amount_col:
        try:
            b = pd.to_numeric(df[budget_col], errors="coerce").fillna(0)
            a = pd.to_numeric(df[amount_col], errors="coerce").fillna(0)
            flagged = df[a > b]
            if not flagged.empty:
                findings.append({"module": "Overbudget (Melebihi Pagu)", "risk_level": "Tinggi", "title": f"{len(flagged)} akun melebihi pagu anggaran", "detail": "Nilai realisasi lebih besar daripada anggaran.", "recommendation": "Minta penjelasan terkait kelebihan anggaran."})
        except: pass
    return findings, flagged

def audit_weekend(df, date_col):
    findings, flagged = [], pd.DataFrame()
    if date_col:
        try:
            dates = pd.to_datetime(df[date_col], errors="coerce")
            flagged = df[dates.dt.dayofweek >= 5]
            if not flagged.empty:
                findings.append({"module": "Transaksi Weekend", "risk_level": "Tinggi", "title": f"{len(flagged)} transaksi di hari Sabtu/Minggu", "detail": "Transaksi di luar hari kerja.", "recommendation": "Periksa otorisasi transaksi akhir pekan."})
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
                    findings.append({"module": "Anomali Nominal (Outlier)", "risk_level": "Tinggi", "title": f"{len(flagged)} nilai ekstrem (di luar batas IQR)", "detail": f"Batas wajar: {lower:,.0f} s.d {upper:,.0f}", "recommendation": "Uji petik transaksi terbesar/terkecil."})
        except: pass
    return findings, flagged

# ============================================================
# SMART FINANCIAL ENGINE (FITUR BARU - FULL AUTO & APBD SUPPORT)
# ============================================================
def auto_extract_financials(df, entity_type):
    """
    Mesin pintar membaca baris laporan keuangan (Perusahaan / APBD).
    """
    results = {
        # Perusahaan
        "pendapatan": 0.0, "laba": 0.0, "aset_lancar": 0.0, "liabilitas_lancar": 0.0,
        "total_aset": 0.0, "total_liabilitas": 0.0, "ekuitas": 0.0,
        # Pemerintahan (APBD)
        "real_pendapatan": 0.0, "ang_pendapatan": 0.0,
        "real_belanja": 0.0, "ang_belanja": 0.0,
        "surplus": 0.0, "pad": 0.0
    }
    
    str_cols = df.select_dtypes(include=['object', 'string']).columns.tolist()
    num_cols = df.select_dtypes(include=['number', 'float', 'int']).columns.tolist()
    
    if not str_cols or not num_cols: return results

    name_col = str_cols[0] # Asumsi kolom nama akun
    
    # Deteksi Kolom Realisasi & Anggaran
    real_col, ang_col = None, None
    for c in num_cols:
        c_str = str(c).lower()
        if 'realisasi' in c_str or 'real' in c_str: real_col = c
        elif 'anggaran' in c_str or 'ang' in c_str or 'pagu' in c_str: ang_col = c
    
    # Jika tidak terdeteksi berdasarkan nama, tebak berdasarkan posisi
    if not real_col and len(num_cols) > 0: real_col = num_cols[0]
    if not ang_col and len(num_cols) > 1: ang_col = num_cols[1]

    # --- LOGIKA EKSTRAKSI PEMERINTAHAN (APBD) ---
    if "Pemerintahan" in entity_type:
        def sum_keyword(kw_list, target_col):
            if not target_col: return 0.0
            total = 0.0
            for kw in kw_list:
                mask = df[name_col].astype(str).str.lower().str.contains(kw, na=False)
                # Hindari double counting jika ada baris "Total"
                if mask.any():
                    total += pd.to_numeric(df.loc[mask, target_col], errors='coerce').sum()
            return total

        # Ekstraksi Angka APBD
        results["real_pendapatan"] = sum_keyword(["pendapatan asli", "dana transfer", "lain-lain pendapatan yang sah", "total pendapatan"], real_col)
        results["ang_pendapatan"] = sum_keyword(["pendapatan asli", "dana transfer", "lain-lain pendapatan yang sah", "total pendapatan"], ang_col)
        
        results["real_belanja"] = sum_keyword(["belanja", "total belanja"], real_col)
        results["ang_belanja"] = sum_keyword(["belanja", "total belanja"], ang_col)

        # PAD (Pendapatan Asli Daerah)
        results["pad"] = sum_keyword(["pendapatan asli daerah", "pad"], real_col)

        # Surplus / Defisit
        mask_surplus = df[name_col].astype(str).str.lower().str.contains("surplus|defisit", na=False)
        if mask_surplus.any():
            results["surplus"] = pd.to_numeric(df.loc[mask_surplus, real_col], errors='coerce').sum()
        else:
            results["surplus"] = results["real_pendapatan"] - results["real_belanja"]

    # --- LOGIKA EKSTRAKSI PERUSAHAAN ---
    else:
        keywords = {
            "pendapatan": ["pendapatan", "penjualan", "revenue", "omzet"],
            "laba": ["laba bersih", "net income", "laba tahun berjalan"],
            "aset_lancar": ["total aset lancar", "current assets"],
            "liabilitas_lancar": ["total liabilitas jangka pendek", "current liabilities"],
            "total_aset": ["total aset", "total assets"],
            "total_liabilitas": ["total liabilitas", "total liabilities"],
            "ekuitas": ["total ekuitas", "total equity"]
        }
        for key, kws in keywords.items():
            for kw in kws:
                mask = df[name_col].astype(str).str.lower().str.contains(kw, na=False)
                if mask.any():
                    results[key] += pd.to_numeric(df.loc[mask, real_col], errors='coerce').sum()

    return results

def hitung_rasio_dari_nilai(nilai, entity_type):
    rasio = {}
    
    if "Pemerintahan" in entity_type:
        # Rasio Sektor Publik
        real_p = nilai.get("real_pendapatan", 0)
        ang_p = nilai.get("ang_pendapatan", 0)
        real_b = nilai.get("real_belanja", 0)
        ang_b = nilai.get("ang_belanja", 0)
        pad = nilai.get("pad", 0)
        
        rasio["Efektivitas Pendapatan (%)"] = round((real_p / ang_p) * 100, 2) if ang_p > 0 else 0.0
        rasio["Efisiensi Belanja (%)"] = round((real_b / ang_b) * 100, 2) if ang_b > 0 else 0.0
        rasio["Kemandirian Daerah (%)"] = round((pad / real_p) * 100, 2) if real_p > 0 else 0.0
        
        # Rasio Pertumbuhan & Lainnya (Placeholder karena butuh historis)
        rasio["Pertumbuhan Pendapatan (%)"] = 0.0 
        
    else:
        # Rasio Perusahaan
        pendapatan = nilai.get("pendapatan", 0)
        laba = nilai.get("laba", 0)
        aset_lancar = nilai.get("aset_lancar", 0)
        liab_lancar = nilai.get("liabilitas_lancar", 0)
        total_aset = nilai.get("total_aset", 0)
        total_liab = nilai.get("total_liabilitas", 0)
        ekuitas = nilai.get("ekuitas", 0)

        rasio["Current Ratio (x)"] = round(aset_lancar / liab_lancar, 2) if liab_lancar > 0 else 0.0
        rasio["Net Profit Margin (%)"] = round((laba / pendapatan) * 100, 2) if pendapatan > 0 else 0.0
        rasio["ROA (%)"] = round((laba / total_aset) * 100, 2) if total_aset > 0 else 0.0
        rasio["ROE (%)"] = round((laba / ekuitas) * 100, 2) if ekuitas > 0 else 0.0
        rasio["Debt to Equity (x)"] = round(total_liab / ekuitas, 2) if ekuitas > 0 else 0.0
    
    return rasio

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
                df_flag.to_excel(writer, sheet_name=modul[:31], index=False)
    buffer.seek(0)
    return buffer

def generate_pdf(entitas, periode, filename, score, findings_list, flagged_data):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=30, leftMargin=30, topMargin=30, bottomMargin=30)
    elements = []
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(name='Title', parent=styles['Heading1'], textColor=colors.HexColor('#7A1F3D'), alignment=1)
    heading_style = ParagraphStyle(name='Heading', parent=styles['Heading2'], textColor=colors.HexColor('#FF5C8B'))
    normal_style = styles['Normal']

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
    t_header.setStyle(TableStyle([('FONTNAME', (0, 0), (-1, -1), 'Helvetica'), ('FONTSIZE', (0, 0), (-1, -1), 10), ('BOTTOMPADDING', (0, 0), (-1, -1), 6)]))
    elements.append(t_header)
    elements.append(Spacer(1, 20))

    elements.append(Paragraph("A. Ringkasan Temuan (Summary of Findings)", heading_style))
    elements.append(Spacer(1, 10))
    summary_data = [["No", "Kode", "Kategori Uji", "Risiko", "Deskripsi Singkat"]]
    for idx, f in enumerate(findings_list, 1):
        summary_data.append([str(idx), f"F-{idx:02d}", f['module'], f['risk_level'], f['title']])
    t_summary = Table(summary_data, colWidths=[30, 50, 100, 60, 230])
    t_summary.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#FF5C8B')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('GRID', (0, 0), (-1, -1), 1, colors.grey),
    ]))
    elements.append(t_summary)
    elements.append(Spacer(1, 20))

    elements.append(Paragraph("B. Rincian Temuan & Bukti", heading_style))
    for idx, f in enumerate(findings_list, 1):
        elements.append(Paragraph(f"<b>F-{idx:02d}: {f['title']}</b>", normal_style))
        elements.append(Paragraph(f"<b>1. Kondisi:</b> {f['detail']}", normal_style))
        elements.append(Paragraph(f"<b>2. Rekomendasi:</b> {f['recommendation']}", normal_style))
        
        modul_name = f['module']
        df_flag = pd.DataFrame()
        if modul_name in flagged_data and not flagged_data[modul_name].empty: df_flag = flagged_data[modul_name]
        elif "Kelengkapan" in modul_name and "Kelengkapan Kosong" in flagged_data: df_flag = flagged_data["Kelengkapan Kosong"]
        elif "Duplikat" in modul_name and "Duplikasi Ekstrem" in flagged_data: df_flag = flagged_data["Duplikasi Ekstrem"]
        elif "Negatif" in modul_name and "Nilai Negatif" in flagged_data: df_flag = flagged_data["Nilai Negatif"]
        elif "Overbudget" in modul_name and "Overbudget" in flagged_data: df_flag = flagged_data["Overbudget"]

        if not df_flag.empty:
            elements.append(Spacer(1, 5))
            elements.append(Paragraph("<i>Bukti Audit (Data Terindikasi):</i>", normal_style))
            preview_df = df_flag.head(5)
            raw_data = [preview_df.columns.tolist()] + preview_df.values.tolist()
            raw_data = [[str(cell) for cell in row] for row in raw_data]
            t_raw = Table(raw_data)
            t_raw.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey), ('GRID', (0, 0), (-1, -1), 0.5, colors.grey), ('FONTSIZE', (0, 0), (-1, -1), 8)]))
            elements.append(t_raw)
        elements.append(Spacer(1, 20))

    doc.build(elements)
    buffer.seek(0)
    return buffer

# ============================================================
# UI COMPONENTS & KKA RENDER
# ============================================================
def finora_header(title, subtitle):
    st.markdown(f'<div class="finora-header"><h1>💗 {title}</h1><p>{subtitle}</p></div>', unsafe_allow_html=True)

def render_audit_results(all_findings, flagged_data, filename, entitas, periode, score):
    color = "#C62828" if score > 50 else "#EF6C00" if score > 20 else "#2E7D32"
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

    tab_kka, tab_grafik, tab_ai = st.tabs(["📝 Laporan Temuan & Bukti", "📊 Visualisasi Grafik", "🤖 Tanya FINORA AI"])

    with tab_kka:
        st.markdown("### A. Ringkasan Temuan")
        summary_data = [{"No": idx, "Kode": f"F-{idx:02d}", "Kategori Uji": f["module"], "Risiko": f["risk_level"], "Deskripsi": f["title"]} for idx, f in enumerate(all_findings, 1)]
        if summary_data: st.table(pd.DataFrame(summary_data))
        else: st.info("Tidak ada temuan.")

        st.markdown("---")
        st.markdown("### B. Rincian Temuan")
        for idx, f in enumerate(all_findings, 1):
            with st.expander(f"[{f['risk_level']}] F-{idx:02d}: {f['title']}"):
                c1, c2 = st.columns([1, 2])
                with c1:
                    st.markdown(f"**1. Kondisi**\n{f['detail']}")
                    st.markdown("**2. Kriteria**\nSOP Internal / SAK/PSAK.")
                with c2:
                    st.markdown("**3. Sebab**\nKelemahan kontrol internal / human error.")
                    st.markdown("**4. Akibat**\nBerpotensi kerugian finansial / laporan tidak wajar.")
                st.error(f"**5. Rekomendasi:** {f['recommendation']}")
                
                modul_name = f['module']
                if modul_name in flagged_data and not flagged_data[modul_name].empty:
                    st.dataframe(flagged_data[modul_name].head(10), use_container_width=True)
                elif "Kelengkapan" in modul_name and "Kelengkapan Kosong" in flagged_data:
                     st.dataframe(flagged_data["Kelengkapan Kosong"].head(10), use_container_width=True)
                elif "Duplikat" in modul_name and "Duplikasi Ekstrem" in flagged_data:
                     st.dataframe(flagged_data["Duplikasi Ekstrem"].head(10), use_container_width=True)
                elif "Negatif" in modul_name and "Nilai Negatif" in flagged_data:
                     st.dataframe(flagged_data["Nilai Negatif"].head(10), use_container_width=True)
                elif "Overbudget" in modul_name and "Overbudget" in flagged_data:
                     st.dataframe(flagged_data["Overbudget"].head(10), use_container_width=True)

        st.markdown("---")
        st.warning(f"Ditemukan **{len(all_findings)} indikasi masalah**. Data memerlukan verifikasi lebih lanjut.")
        
        st.markdown("### 📥 Download Kertas Kerja Audit")
        col1, col2 = st.columns(2)
        excel_buffer = generate_excel(flagged_data, all_findings)
        col1.download_button("⬇️ Download Excel", data=excel_buffer, file_name=f"KKA_{entitas}.xlsx", use_container_width=True)
        
        pdf_buffer = generate_pdf(entitas, periode, filename, score, all_findings, flagged_data)
        col2.download_button("⬇️ Download PDF", data=pdf_buffer, file_name=f"KKA_{entitas}.pdf", use_container_width=True)

    with tab_grafik:
        if all_findings:
            c1, c2 = st.columns(2)
            with c1:
                risk_df = pd.DataFrame(all_findings)["risk_level"].value_counts().reset_index()
                fig_pie = px.pie(risk_df, names="risk_level", values="count", color="risk_level", color_discrete_map={"Tinggi": "#C62828", "Sedang": "#EF6C00", "Rendah": "#2E7D32"}, hole=0.4)
                st.plotly_chart(fig_pie, use_container_width=True)
            with c2:
                mod_df = pd.DataFrame(all_findings)["module"].value_counts().reset_index()
                fig_bar = px.bar(mod_df, x="count", y="module", orientation='h', color_discrete_sequence=["#FF5C8B"])
                st.plotly_chart(fig_bar, use_container_width=True)
        else: st.info("Belum ada data.")

    with tab_ai:
        st.markdown("### 🤖 Tanya FINORA AI")
        context_text = f"Entitas: {entitas} | Skor: {score}/100\nTemuan:\n" + "".join([f"- [{f['risk_level']}] {f['module']} ({f['title']})\n" for f in all_findings])
        if "ai_chat_history" not in st.session_state: st.session_state.ai_chat_history = []
        for msg in st.session_state.ai_chat_history:
            with st.chat_message(msg["role"]): st.markdown(msg["content"])
        with st.form(key="ai_chat_form", clear_on_submit=True):
            prompt = st.text_input("Tulis pertanyaan audit Anda...")
            if st.form_submit_button("Kirim ke AI", use_container_width=True) and prompt:
                st.session_state.ai_chat_history.append({"role": "user", "content": prompt})
                with st.chat_message("assistant"): response = st.write_stream(ask_gemini_stream(prompt, context_text))
                st.session_state.ai_chat_history.append({"role": "assistant", "content": response})
                st.rerun()

# ============================================================
# HALAMAN AUDIT PROFESIONAL
# ============================================================
def page_audit():
    finora_header("Audit Profesional", "Sistem deteksi otomatis anomali transaksi")
    with st.container():
        st.markdown("### 1. Upload Data")
        c1, c2 = st.columns([3, 1])
        uploaded = c1.file_uploader("Upload File Excel/CSV", type=["xlsx", "csv"], key="audit_upload")
        header_row = c2.number_input("Header di baris ke-", min_value=0, value=0, key="audit_header")

    if uploaded:
        df = pd.read_excel(uploaded, header=header_row) if uploaded.name.endswith('xlsx') else pd.read_csv(uploaded, header=header_row)
        detected = auto_detect_columns(df)
        
        st.markdown("### 2. Pemetaan Kolom")
        with st.expander("⚙️ Koreksi Pemetaan Kolom", expanded=False):
            c1, c2 = st.columns(2)
            date_col = c1.selectbox("Kolom Tanggal", [""] + list(df.columns), index=list(df.columns).index(detected["date"])+1 if detected["date"] else 0)
            desc_col = c2.selectbox("Kolom Deskripsi", [""] + list(df.columns), index=list(df.columns).index(detected["desc"])+1 if detected["desc"] else 0)
            c3, c4, c5 = st.columns(3)
            amount_col = c3.selectbox("Kolom Nominal", [""] + list(df.columns), index=list(df.columns).index(detected["amount"])+1 if detected["amount"] else 0)
            budget_col = c4.selectbox("Kolom Anggaran", [""] + list(df.columns), index=list(df.columns).index(detected["budget"])+1 if detected["budget"] else 0)
            invoice_col = c5.selectbox("Kolom No. Faktur", [""] + list(df.columns), index=list(df.columns).index(detected["invoice"])+1 if detected["invoice"] else 0)

        st.markdown("### 3. Informasi KKA")
        c_kka1, c_kka2 = st.columns(2)
        entitas = c_kka1.text_input("Nama Entitas / Perusahaan", placeholder="Contoh: PT Maju Jaya", key="audit_entitas")
        periode = c_kka2.text_input("Periode Audit", placeholder="Contoh: Tahun Buku 2024", key="audit_periode")
        
        st.markdown("### 4. Jalankan Prosedur")
        if st.button("🚀 JALANKAN PROSEDUR AUDIT", use_container_width=True):
            with st.spinner("Memproses data..."):
                all_findings = []
                flagged_data = {}
                
                cols_to_check = [c for c in [date_col, amount_col, desc_col] if c]
                f, flag = audit_empty(df, cols_to_check); all_findings.extend(f); flagged_data["Kelengkapan Kosong"] = flag
                
                subset_dup = [c for c in [date_col, amount_col, desc_col, invoice_col] if c]
                f, flag = audit_duplicates(df, subset_dup); all_findings.extend(f); flagged_data["Duplikasi Ekstrem"] = flag
                
                if date_col:
                    f, flag = audit_weekend(df, date_col); all_findings.extend(f); flagged_data["Transaksi Weekend"] = flag
                if desc_col:
                    f, flag = audit_suspicious_words(df, desc_col); all_findings.extend(f); flagged_data["Kata Kunci"] = flag
                if amount_col:
                    f, flag = audit_negative(df, amount_col); all_findings.extend(f); flagged_data["Nilai Negatif"] = flag
                    f, flag = audit_outliers_iqr(df, amount_col); all_findings.extend(f); flagged_data["Outlier IQR"] = flag
                if budget_col and amount_col:
                    f, flag = audit_overbudget(df, budget_col, amount_col); all_findings.extend(f); flagged_data["Overbudget"] = flag

                high_count = sum(1 for f in all_findings if f["risk_level"] == "Tinggi")
                med_count = sum(1 for f in all_findings if f["risk_level"] == "Sedang")
                score = min(100, (high_count * 20) + (med_count * 10))

                st.session_state['audit_results'] = {
                    "findings": all_findings, "flagged": flagged_data, "filename": uploaded.name,
                    "entitas": entitas, "periode": periode, "score": score
                }
                st.session_state.ai_chat_history = []

                flagged_json = {k: v.to_dict(orient="records") for k, v in flagged_data.items() if not v.empty}
                summary_to_save = {"total_findings": len(all_findings), "score": score, "entitas": entitas, "periode": periode, "flagged_data": flagged_json}
                fid = save_file(uploaded.name, uploaded.getvalue())
                aid = save_analysis(fid, "Audit Profesional", ["audit_v5"], summary_to_save)
                save_findings(aid, all_findings)
                st.success(f"💾 KKA untuk **{entitas}** berhasil disimpan (ID #{aid}).")

    if 'audit_results' in st.session_state:
        res = st.session_state['audit_results']
        st.markdown("---")
        render_audit_results(res["findings"], res["flagged"], res["filename"], res["entitas"], res["periode"], res["score"])

# ============================================================
# HALAMAN ANALISIS KEUANGAN (PINTAR APBD & PERUSAHAAN)
# ============================================================
def page_financial_analysis():
    finora_header("Analisis Laporan Keuangan", "Evaluasi Kesehatan Finansial & Prospek")
    
    st.markdown("### 1. Upload Laporan Keuangan")
    c1, c2 = st.columns([2, 1])
    uploaded_fin = c1.file_uploader("Upload File Excel/CSV (APBD / Neraca / Laba Rugi)", type=["xlsx", "csv"], key="fin_upload")
    entity_type = c2.selectbox("Jenis Entitas", ["Perusahaan Swasta / Tbk", "Pemerintahan / BLU"])

    if uploaded_fin:
        df_fin = pd.read_excel(uploaded_fin) if uploaded_fin.name.endswith('xlsx') else pd.read_csv(uploaded_fin)
        
        st.success("✨ File berhasil dibaca. FINORA AI akan otomatis mengelompokkan akun dan menghitung rasio.")
        
        if st.button("📊 ANALISIS KEUANGAN OTOMATIS", use_container_width=True):
            with st.spinner("Membaca akun, mengelompokkan, dan menghitung rasio..."):
                nilai_utama = auto_extract_financials(df_fin, entity_type)
                rasio = hitung_rasio_dari_nilai(nilai_utama, entity_type)
                
                st.session_state['fin_results'] = {
                    "rasio": rasio, 
                    "nilai": nilai_utama, 
                    "entity_type": entity_type
                }

    # Tampilkan Hasil
    if 'fin_results' in st.session_state:
        res = st.session_state['fin_results']
        rasio = res["rasio"]
        nilai = res["nilai"]
        is_pemda = "Pemerintahan" in res['entity_type']
        
        st.markdown("---")
        tab_rasio, tab_grafik, tab_ai_advisor = st.tabs(["📊 Ringkasan Rasio", "📈 Visualisasi & Prospek", "🧠 AI Financial Advisor"])
        
        with tab_rasio:
            st.markdown(f"### Kesehatan Finansial: **{res['entity_type']}**")
            
            if is_pemda:
                # Tampilan Khusus APBD
                m1, m2, m3, m4 = st.columns(4)
                m1.metric("Realisasi Pendapatan", f"Rp {nilai.get('real_pendapatan', 0):,.0f}")
                m2.metric("Realisasi Belanja", f"Rp {nilai.get('real_belanja', 0):,.0f}")
                m3.metric("Surplus / Defisit", f"Rp {nilai.get('surplus', 0):,.0f}")
                m4.metric("Pendapatan Asli Daerah (PAD)", f"Rp {nilai.get('pad', 0):,.0f}")
                
                st.markdown("#### Rasio Keuangan Sektor Publik")
                r1, r2, r3, r4 = st.columns(4)
                r1.metric("Efektivitas Pendapatan", f"{rasio.get('Efektivitas Pendapatan (%)', 0)} %", help="Realisasi / Anggaran Pendapatan. (Target > 100%)")
                r2.metric("Efisiensi Belanja", f"{rasio.get('Efisiensi Belanja (%)', 0)} %", help="Realisasi / Anggaran Belanja. (Target < 100%)")
                r3.metric("Kemandirian Daerah", f"{rasio.get('Kemandirian Daerah (%)', 0)} %", help="PAD / Total Pendapatan. (Target > 50% untuk mandiri)")
                r4.metric("Pertumbuhan Pendapatan", f"{rasio.get('Pertumbuhan Pendapatan (%)', 0)} %", help="Butuh data tahun sebelumnya untuk menghitung YoY.")
            else:
                # Tampilan Perusahaan
                m1, m2, m3, m4 = st.columns(4)
                m1.metric("Total Pendapatan", f"Rp {nilai.get('pendapatan', 0):,.0f}")
                m2.metric("Laba Bersih", f"Rp {nilai.get('laba', 0):,.0f}")
                m3.metric("Total Aset", f"Rp {nilai.get('total_aset', 0):,.0f}")
                m4.metric("Total Ekuitas", f"Rp {nilai.get('ekuitas', 0):,.0f}")
                
                st.markdown("#### Rasio Keuangan Utama")
                r1, r2, r3, r4, r5 = st.columns(5)
                r1.metric("Current Ratio", f"{rasio.get('Current Ratio (x)', 0)} x")
                r2.metric("Net Profit Margin", f"{rasio.get('Net Profit Margin (%)', 0)} %")
                r3.metric("ROA", f"{rasio.get('ROA (%)', 0)} %")
                r4.metric("ROE", f"{rasio.get('ROE (%)', 0)} %")
                r5.metric("Debt to Equity", f"{rasio.get('Debt to Equity (x)', 0)} x")

        with tab_grafik:
            st.markdown("### Analisis Visual Prospek")
            if is_pemda:
                fig = go.Figure(data=[
                    go.Bar(name='Anggaran Pendapatan', x=['Pendapatan'], y=[nilai.get('ang_pendapatan', 0)], marker_color='#F8C8D4'),
                    go.Bar(name='Realisasi Pendapatan', x=['Pendapatan'], y=[nilai.get('real_pendapatan', 0)], marker_color='#FF5C8B'),
                    go.Bar(name='Anggaran Belanja', x=['Belanja'], y=[nilai.get('ang_belanja', 0)], marker_color='#F8C8D4'),
                    go.Bar(name='Realisasi Belanja', x=['Belanja'], y=[nilai.get('real_belanja', 0)], marker_color='#7A1F3D')
                ])
                fig.update_layout(barmode='group', title="Anggaran vs Realisasi APBD", height=400)
                st.plotly_chart(fig, use_container_width=True)
            else:
                fig = go.Figure(data=[
                    go.Bar(name='Pendapatan', x=['Keuangan'], y=[nilai.get('pendapatan', 0)], marker_color='#FF5C8B'),
                    go.Bar(name='Laba Bersih', x=['Keuangan'], y=[nilai.get('laba', 0)], marker_color='#7A1F3D')
                ])
                fig.update_layout(barmode='group', title="Pendapatan vs Laba Bersih", height=400)
                st.plotly_chart(fig, use_container_width=True)

        with tab_ai_advisor:
            st.markdown("### 🤖 AI Financial Advisor")
            st.info("AI akan menganalisis prospek dan kesehatan finansial berdasarkan hasil ekstraksi otomatis di atas.")
            
            if st.button("Buat Laporan Analisis AI", use_container_width=True):
                if is_pemda:
                    context_ai = f"""
                    Entitas: Pemerintahan / BLU
                    Realisasi Pendapatan: Rp {nilai.get('real_pendapatan', 0):,.0f}, Anggaran: Rp {nilai.get('ang_pendapatan', 0):,.0f}
                    Realisasi Belanja: Rp {nilai.get('real_belanja', 0):,.0f}, Anggaran: Rp {nilai.get('ang_belanja', 0):,.0f}
                    Surplus/Defisit: Rp {nilai.get('surplus', 0):,.0f}
                    PAD: Rp {nilai.get('pad', 0):,.0f}
                    Rasio Efektivitas: {rasio.get('Efektivitas Pendapatan (%)', 0)}%, Efisiensi: {rasio.get('Efisiensi Belanja (%)', 0)}%, Kemandirian: {rasio.get('Kemandirian Daerah (%)', 0)}%
                    """
                    prompt_ai = "Sebagai Konsultan Keuangan Daerah, berikan analisis atas APBD ini (Kesehatan Fiskal, Kemandirian, dan Rekomendasi Optimalisasi Pendapatan/Belanja)."
                else:
                    context_ai = f"""
                    Entitas: Perusahaan
                    Pendapatan: Rp {nilai.get('pendapatan', 0):,.0f}, Laba: Rp {nilai.get('laba', 0):,.0f}
                    Total Aset: Rp {nilai.get('total_aset', 0):,.0f}, Ekuitas: Rp {nilai.get('ekuitas', 0):,.0f}
                    Rasio: Current Ratio {rasio.get('Current Ratio (x)', 0)}x, NPM {rasio.get('Net Profit Margin (%)', 0)}%, ROA {rasio.get('ROA (%)', 0)}%, ROE {rasio.get('ROE (%)', 0)}%
                    """
                    prompt_ai = "Sebagai Analis Keuangan Senior, berikan analisis kesehatan finansial perusahaan dan rekomendasi strategis ke depan."
                
                with st.chat_message("assistant"):
                    response = st.write_stream(ask_gemini_stream(prompt_ai, context_ai, system_role="Konsultan Keuangan & Fiskal Ahli"))
                st.success("Analisis AI Selesai.")

# ============================================================
# HALAMAN LAINNYA (DASHBOARD, SIMPAN, LANDING)
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
            entitas = summary_data.get("entitas", "-")
            periode = summary_data.get("periode", "-")
            score = summary_data.get("score", 0)
            raw_flagged = summary_data.get("flagged_data", {})
            flagged_data = {k: pd.DataFrame(v) for k, v in raw_flagged.items()}
            
            findings_df = fetch_findings(aid)
            findings_list = findings_df.to_dict("records")
            render_audit_results(findings_list, flagged_data, an["filename"], entitas, periode, score)

def page_landing():
    st.markdown("""<style>[data-testid="collapsedControl"], [data-testid="stSidebar"] { display: none; }</style>""", unsafe_allow_html=True)
    col1, col2, col3 = st.columns([1, 1.5, 1])
    with col2:
        if os.path.exists(LOGO_FILENAME):
            st.image(LOGO_FILENAME, width=250)
        else:
            st.markdown("<h1 style='text-align:center; font-size: 5rem;'>💗</h1>", unsafe_allow_html=True)

    st.markdown("""
    <div style="text-align: center; padding: 20px; background: linear-gradient(135deg, #FFF0F4 0%, #FFF5E8 100%); border-radius: 20px; margin-bottom: 40px; border: 2px solid #FF5C8B; box-shadow: 0 10px 30px rgba(255, 92, 139, 0.15);">
        <h1 style="color: #7A1F3D; font-size: 3.5rem; margin-bottom: 15px; font-weight: 800;">FINORA AI</h1>
        <p style="color: #2D1B24; font-size: 1.3rem; max-width: 800px; margin: 0 auto; line-height: 1.6;">
            Platform Kecerdasan Finansial untuk mendeteksi anomali (Audit) dan menyusun <b>Kertas Kerja Audit (KKA)</b> serta <b>Analisis Laporan Keuangan</b> secara otomatis.
        </p>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("<h2 style='text-align: center; color: #7A1F3D; margin-bottom: 30px; font-weight: 700;'>Dua Senjata Utama FINORA</h2>", unsafe_allow_html=True)
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("""
        <div style="background: white; padding: 30px 20px; border-radius: 15px; box-shadow: 0 4px 15px rgba(0,0,0,0.05); text-align: center; border-top: 5px solid #FF5C8B;">
            <div style="font-size: 3rem; margin-bottom: 15px;">🔎</div>
            <h3 style="color: #7A1F3D; margin-bottom: 10px;">Audit Profesional</h3>
            <p style="color: #555; font-size: 0.95rem;">Mendeteksi duplikasi, transaksi weekend, overbudget, outlier, dan anomali lainnya. Hasil siap diunduh dalam format KKA (PDF & Excel).</p>
        </div>
        """, unsafe_allow_html=True)
    with col2:
        st.markdown("""
        <div style="background: white; padding: 30px 20px; border-radius: 15px; box-shadow: 0 4px 15px rgba(0,0,0,0.05); text-align: center; border-top: 5px solid #FF5C8B;">
            <div style="font-size: 3rem; margin-bottom: 15px;">📈</div>
            <h3 style="color: #7A1F3D; margin-bottom: 10px;">Analisis Keuangan</h3>
            <p style="color: #555; font-size: 0.95rem;">Hitung rasio (Likuiditas, Profitabilitas, Solvabilitas) dan dapatkan *insight* prospek bisnis/pemerintahan dari AI Financial Advisor.</p>
        </div>
        """, unsafe_allow_html=True)

    st.markdown("<br><br><br>", unsafe_allow_html=True)
    col_cta1, col_cta2, col_cta3 = st.columns([1, 2, 1])
    with col_cta2:
        st.markdown("""<style>.stButton > button { background: linear-gradient(135deg, #FF5C8B 0%, #7A1F3D 100%) !important; color: white !important; font-size: 1.3rem !important; padding: 20px 30px !important; border-radius: 12px !important; border: none !important; font-weight: 800 !important; box-shadow: 0 6px 20px rgba(255, 92, 139, 0.4) !important; transition: all 0.3s ease !important; width: 100%; }</style>""", unsafe_allow_html=True)
        if st.button("🚀 START YOUR ANALYSIS", use_container_width=True):
            st.session_state.app_started = True
            st.rerun()

# ============================================================
# MAIN (LOGIC ROUTING)
# ============================================================
def main():
    if "app_started" not in st.session_state:
        st.session_state.app_started = False

    if not st.session_state.app_started:
        page_landing()
    else:
        try:
            st.sidebar.image(LOGO_FILENAME, width=180)
        except:
            st.sidebar.markdown('<h1 style="color:#FF5C8B;text-align:center;">💗 FINORA</h1>', unsafe_allow_html=True)
            
        st.sidebar.markdown('<p style="text-align:center;font-size:12px;color:#7A1F3D;">Financial Intelligence<br>Platform</p>', unsafe_allow_html=True)
        st.sidebar.markdown("---")

        nav = st.sidebar.radio("Navigasi", [
            "Dashboard", 
            "Audit Profesional", 
            "Analisis Keuangan", 
            "Simpan"
        ])

        st.sidebar.markdown("---")
        if st.sidebar.button("🏠 Kembali ke Halaman Depan", use_container_width=True):
            st.session_state.app_started = False
            st.rerun()

        st.sidebar.caption("⚠️ Temuan bersifat indikasi dan perlu verifikasi dokumen fisik.")

        if nav == "Dashboard":
            page_dashboard()
        elif nav == "Audit Profesional":
            page_audit()
        elif nav == "Analisis Keuangan":
            page_financial_analysis()
        elif nav == "Simpan":
            page_simpan()

if __name__ == "__main__":
    main()
