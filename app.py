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
:root {
  --finora-pink: #FF5C8B;
  --finora-cream: #FFF5E8;
  --finora-rose: #F8C8D4;
  --finora-burgundy: #7A1F3D;
  --finora-text: #2D1B24;
}
.stApp { background-color: var(--finora-cream); color: var(--finora-text); }
[data-testid="stSidebar"] { background-color: #FFF0F4; }
.stButton > button {
  background-color: var(--finora-pink); color: white !important;
  border-radius: 12px; border: none; font-weight: 600; padding: 10px 18px;
}
.stButton > button:hover { background-color: var(--finora-burgundy); color: white !important; }
h1, h2, h3 { color: var(--finora-burgundy); }
.finora-header {
  background: linear-gradient(135deg, #FF5C8B 0%, #7A1F3D 100%);
  color: white; padding: 24px; border-radius: 16px; margin-bottom: 20px;
}
.finora-header h1 { color: white; margin: 0; font-size: 30px; }
.finora-header p  { color: #FFF0F4; margin: 6px 0 0 0; }
div[data-testid="stMetric"] {
  background: white; padding: 16px; border-radius: 12px;
  border-left: 4px solid #FF5C8B; box-shadow: 0 2px 8px rgba(0,0,0,0.05);
}
.stDataFrame { background: white; border-radius: 12px; }
</style>
""", unsafe_allow_html=True)

# ============================================================
# DATABASE (SQLite)
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
    CREATE TABLE IF NOT EXISTS chats (id INTEGER PRIMARY KEY AUTOINCREMENT, analysis_id INTEGER, role TEXT, message TEXT, created_at TEXT);
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

def save_chat(analysis_id, role, message):
    conn = get_conn(); c = conn.cursor()
    c.execute("INSERT INTO chats(analysis_id, role, message, created_at) VALUES (?,?,?,?)", (analysis_id, role, message, datetime.now().isoformat()))
    conn.commit(); conn.close()

def fetch_analyses():
    conn = get_conn()
    df = pd.read_sql_query("SELECT a.id, f.filename, a.mode, a.created_at FROM analyses a JOIN files f ON a.file_id = f.id ORDER BY a.created_at DESC", conn)
    conn.close(); return df

def fetch_analysis(aid):
    conn = get_conn()
    df = pd.read_sql_query("SELECT * FROM analyses WHERE id=?", conn, params=(aid,))
    conn.close(); return df.iloc[0] if not df.empty else None

def fetch_findings(aid):
    conn = get_conn()
    df = pd.read_sql_query("SELECT * FROM findings WHERE analysis_id=?", conn, params=(aid,))
    conn.close(); return df

def fetch_chats(aid):
    conn = get_conn()
    df = pd.read_sql_query("SELECT * FROM chats WHERE analysis_id=? ORDER BY id", conn, params=(aid,))
    conn.close(); return df

# ============================================================
# ENGINES (AUDIT & FINANCE)
# ============================================================
def audit_duplicates(df, desc_col=None):
    findings = []
    dup = df[df.duplicated(subset=[desc_col], keep=False)] if desc_col else df[df.duplicated(keep=False)]
    if not dup.empty:
        findings.append({"module": "Data Duplikat", "risk_level": "Tinggi", "title": f"Terdeteksi {len(dup)} baris duplikat", "detail": f"Baris duplikat: {len(dup)}.", "recommendation": "Verifikasi data ganda."})
    return findings

def audit_empty(df):
    findings = []
    for col, cnt in df.isna().sum().items():
        if cnt > 0:
            pct = cnt / len(df) * 100
            if pct > 20:
                findings.append({"module": "Data Kosong", "risk_level": "Sedang" if pct < 50 else "Tinggi", "title": f"Kolom '{col}' memiliki {cnt} data kosong", "detail": f"Persentase kosong: {pct:.1f}%", "recommendation": "Lengkapi data."})
    return findings

def audit_outliers(df, amount_col):
    findings = []
    if amount_col not in df.columns: return findings
    s = pd.to_numeric(df[amount_col], errors="coerce").dropna()
    if s.empty or s.std() == 0 or pd.isna(s.std()): return findings
    outliers = int((((s - s.mean()) / s.std()).abs() > 3).sum())
    if outliers > 0:
        findings.append({"module": "Nominal Tidak Wajar", "risk_level": "Tinggi" if outliers > 5 else "Sedang", "title": f"Terdeteksi {outliers} nilai ekstrem (Outlier)", "detail": "Nilai berada di luar 3 standar deviasi.", "recommendation": "Periksa transaksi dengan nominal ekstrem."})
    return findings

def audit_debit_credit(df, debit_col, credit_col):
    findings = []
    if debit_col not in df.columns or credit_col not in df.columns: return findings
    d = pd.to_numeric(df[debit_col], errors="coerce").fillna(0).sum()
    c = pd.to_numeric(df[credit_col], errors="coerce").fillna(0).sum()
    diff = abs(d - c)
    if diff > 0.01:
        findings.append({"module": "Debit/Kredit", "risk_level": "Tinggi", "title": f"Debit & Kredit tidak seimbang (selisih {diff:,.2f})", "detail": f"Total Debit: {d:,.2f}, Total Kredit: {c:,.2f}", "recommendation": "Periksa entri jurnal."})
    return findings

def audit_dates(df, date_col):
    findings = []
    if date_col not in df.columns: return findings
    invalid = int(pd.to_datetime(df[date_col], errors="coerce").isna().sum())
    if invalid > 0:
        findings.append({"module": "Tanggal Tidak Valid", "risk_level": "Sedang", "title": f"{invalid} tanggal tidak valid", "detail": f"Kolom {date_col} memiliki format salah.", "recommendation": "Perbaiki format tanggal."})
    return findings

FINANCE_KEYWORDS = {
    "revenue": ["pendapatan", "revenue", "penjualan", "sales"],
    "expense": ["beban", "biaya", "expense", "cost"],
    "gross_profit": ["laba kotor", "gross profit"],
    "net_income": ["laba bersih", "net income", "net profit"],
    "current_assets": ["aset lancar", "aktiva lancar", "current assets"],
    "total_assets": ["total aset", "total aktiva", "total assets"],
    "current_liabilities": ["liabilitas jangka pendek", "kewajiban lancar", "current liabilities"],
    "total_liabilities": ["total liabilitas", "total kewajiban", "total liabilities"],
    "equity": ["total ekuitas", "ekuitas", "equity", "total modal"],
    "inventory": ["persediaan", "inventory"],
}
def extract_finance_values(df, label_col, value_col):
    values = {}
    if label_col not in df.columns or value_col not in df.columns: return values
    for _, row in df.iterrows():
        label = str(row[label_col]).lower()
        val = pd.to_numeric(row[value_col], errors="coerce")
        if pd.isna(val): continue
        for key, kws in FINANCE_KEYWORDS.items():
            if key not in values and any(k in label for k in kws):
                values[key] = float(val)
    return values

def compute_ratios(v):
    r = {}
    def safe_div(a, b): return a / b if b else None
    if "current_assets" in v and v.get("current_liabilities"):
        r["Current Ratio"] = safe_div(v["current_assets"], v["current_liabilities"])
        if "inventory" in v: r["Quick Ratio"] = safe_div(v["current_assets"] - v["inventory"], v["current_liabilities"])
    if "total_liabilities" in v and v.get("equity"): r["Debt to Equity"] = safe_div(v["total_liabilities"], v["equity"])
    if "total_liabilities" in v and v.get("total_assets"): r["Debt to Asset"] = safe_div(v["total_liabilities"], v["total_assets"])
    if "gross_profit" in v and v.get("revenue"): r["Gross Profit Margin (%)"] = safe_div(v["gross_profit"], v["revenue"]) * 100
    if "net_income" in v and v.get("revenue"): r["Net Profit Margin (%)"] = safe_div(v["net_income"], v["revenue"]) * 100
    if "net_income" in v and v.get("total_assets"): r["ROA (%)"] = safe_div(v["net_income"], v["total_assets"]) * 100
    if "net_income" in v and v.get("equity"): r["ROE (%)"] = safe_div(v["net_income"], v["equity"]) * 100
    return {k: val for k, val in r.items() if val is not None}

def interpret_ratio(name, val):
    if name == "Current Ratio": return "Likuiditas sehat." if val >= 1.5 else "Perlu pemantauan." if val >= 1 else "Likuiditas rendah."
    if name == "Debt to Equity": return "Risiko tinggi." if val > 2 else "Wajar." if val <= 1 else "Cukup berutang."
    return "Perlu evaluasi." if val < 0 else "Margin baik." if val > 15 else "Margin wajar."

# ============================================================
# UI HELPERS
# ============================================================
def finora_header(title, subtitle):
    st.markdown(f'<div class="finora-header"><h1>💗 {title}</h1><p>{subtitle}</p></div>', unsafe_allow_html=True)

def display_findings(findings):
    if not findings: st.info("Tidak ada temuan."); return
    for f in findings:
        with st.expander(f"⚠️ {f['title']} — {f['risk_level']}"):
            st.markdown(f"**Modul:** {f['module']} | **Risiko:** {f['risk_level']}")
            st.markdown(f"**Detail:** {f['detail']}")
            st.markdown(f"**Rekomendasi:** {f['recommendation']}")

def call_gemini(prompt, context):
    try:
        import google.generativeai as genai
        api_key = st.secrets.get("GEMINI_API_KEY", os.environ.get("GEMINI_API_KEY", ""))
        if not api_key: return "API Key Gemini belum diatur."
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-1.5-flash")
        resp = model.generate_content(f"Konteks: {context}\n\nPertanyaan: {prompt}\n(Jawab sebagai asisten audit, sebutkan bahwa temuan perlu verifikasi)")
        return resp.text
    except Exception as e: return f"Error Gemini: {e}"

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
    else:
        st.info("Belum ada aktivitas.")

def page_audit():
    finora_header("Audit Otomatis", "Pemeriksaan data, pengujian audit, dan penilaian risiko")
    tab1, tab2 = st.tabs(["🛡️ Mode Mandiri", "⚙️ Mode Custom"])
    
    with tab1:
        st.markdown("### Analisis Mandiri Audit")
        uploaded = st.file_uploader("Upload Excel", type=["xlsx", "xls"], key="audit_ind_file")
        if uploaded:
            df = pd.read_excel(uploaded)
            st.dataframe(df.head(), use_container_width=True)
            
            c1, c2 = st.columns(2)
            date_col = c1.selectbox("Kolom Tanggal", [""] + list(df.columns), key="i_date")
            amount_col = c2.selectbox("Kolom Nominal", [""] + list(df.columns), key="i_amount")
            
            st.markdown("**Pilih Pengujian:**")
            c_dup = st.checkbox("Data Duplikat", value=True)
            c_empty = st.checkbox("Data Kosong", value=True)
            c_out = st.checkbox("Nominal Tidak Wajar", value=True)
            
            if st.button("🚀 Jalankan Audit Mandiri"):
                findings = []
                if c_dup: findings += audit_duplicates(df)
                if c_empty: findings += audit_empty(df)
                if c_out and amount_col: findings += audit_outliers(df, amount_col)
                
                display_findings(findings)
                fid = save_file(uploaded.name, uploaded.getvalue())
                aid = save_analysis(fid, "Audit Mandiri", ["basic_audit"], {"total_findings": len(findings)})
                save_findings(aid, findings)
                st.success(f"💾 Disimpan di ID #{aid}")

    with tab2:
        st.markdown("### Analisis Custom Audit (1 File, Banyak Ceklis)")
        uploaded_c = st.file_uploader("Upload Excel", type=["xlsx", "xls"], key="audit_cust_file")
        if uploaded_c:
            df = pd.read_excel(uploaded_c)
            st.dataframe(df.head(), use_container_width=True)
            
            c1, c2, c3 = st.columns(3)
            date_col = c1.selectbox("Kolom Tanggal", [""] + list(df.columns), key="c_date")
            amount_col = c2.selectbox("Kolom Nominal", [""] + list(df.columns), key="c_amount")
            debit_col = c3.selectbox("Kolom Debit", [""] + list(df.columns), key="c_debit")
            
            c4, c5 = st.columns(2)
            credit_col = c4.selectbox("Kolom Kredit", [""] + list(df.columns), key="c_credit")
            desc_col = c5.selectbox("Kolom Deskripsi", [""] + list(df.columns), key="c_desc")
            
            st.markdown("**Pilih Pengujian (Ceklis):**")
            c_dup = st.checkbox("Cek Duplikat", value=True)
            c_empty = st.checkbox("Cek Data Kosong", value=True)
            c_out = st.checkbox("Cek Nominal Ekstrem", value=True)
            c_dc = st.checkbox("Cek Debit/Kredit", value=True)
            c_date = st.checkbox("Cek Tanggal", value=True)
            
            if st.button("🚀 Jalankan Custom Audit"):
                findings = []
                if c_dup: findings += audit_duplicates(df, desc_col or None)
                if c_empty: findings += audit_empty(df)
                if c_out and amount_col: findings += audit_outliers(df, amount_col)
                if c_dc and debit_col and credit_col: findings += audit_debit_credit(df, debit_col, credit_col)
                if c_date and date_col: findings += audit_dates(df, date_col)
                
                display_findings(findings)
                fid = save_file(uploaded_c.name, uploaded_c.getvalue())
                aid = save_analysis(fid, "Audit Custom", ["custom_audit"], {"total_findings": len(findings)})
                save_findings(aid, findings)
                st.success(f"💾 Disimpan di ID #{aid}")

def page_laporan():
    finora_header("Laporan Keuangan", "Analisis laporan, rasio, dan interpretasi keuangan")
    tab1, tab2 = st.tabs(["📈 Mode Mandiri", "⚙️ Mode Custom"])
    
    with tab1:
        st.markdown("### Analisis Laporan Mandiri")
        uploaded = st.file_uploader("Upload Excel", type=["xlsx", "xls"], key="lap_ind_file")
        if uploaded:
            df = pd.read_excel(uploaded)
            st.dataframe(df.head(), use_container_width=True)
            
            c1, c2 = st.columns(2)
            label_col = c1.selectbox("Kolom Akun", [""] + list(df.columns), key="l_label")
            val_col = c2.selectbox("Kolom Nominal", [""] + list(df.columns), key="l_val")
            
            if st.button("🚀 Hitung Rasio"):
                vals = extract_finance_values(df, label_col, val_col)
                ratios = compute_ratios(vals)
                
                st.markdown("**Nilai Terdeteksi:**")
                st.json(vals)
                
                if ratios:
                    st.markdown("**Hasil Rasio:**")
                    st.dataframe(pd.DataFrame([{"Rasio": k, "Nilai": round(v, 2), "Interpretasi": interpret_ratio(k, v)} for k, v in ratios.items()]), use_container_width=True)
                    
                    fid = save_file(uploaded.name, uploaded.getvalue())
                    aid = save_analysis(fid, "Laporan Mandiri", ["basic_finance"], {"ratios": ratios})
                    st.success(f"💾 Disimpan di ID #{aid}")
                else:
                    st.warning("Rasio tidak dapat dihitung.")

    with tab2:
        st.markdown("### Analisis Laporan Custom")
        uploaded_c = st.file_uploader("Upload Excel", type=["xlsx", "xls"], key="lap_cust_file")
        if uploaded_c:
            df = pd.read_excel(uploaded_c)
            st.dataframe(df.head(), use_container_width=True)
            
            c1, c2 = st.columns(2)
            label_col = c1.selectbox("Kolom Akun", [""] + list(df.columns), key="lc_label")
            val_col = c2.selectbox("Kolom Nominal", [""] + list(df.columns), key="lc_val")
            
            st.markdown("**Pilih Analisis (Ceklis):**")
            c_laba = st.checkbox("Analisis Laba Rugi", value=True)
            c_neraca = st.checkbox("Analisis Neraca", value=True)
            c_rasio = st.checkbox("Analisis Rasio", value=True)
            
            if st.button("🚀 Jalankan Custom Laporan"):
                vals = extract_finance_values(df, label_col, val_col)
                ratios = compute_ratios(vals)
                
                if c_laba or c_neraca:
                    st.markdown("**Nilai Terdeteksi:**")
                    st.json(vals)
                if c_rasio and ratios:
                    st.markdown("**Rasio:**")
                    st.dataframe(pd.DataFrame([{"Rasio": k, "Nilai": round(v, 2)} for k, v in ratios.items()]), use_container_width=True)
                
                fid = save_file(uploaded_c.name, uploaded_c.getvalue())
                aid = save_analysis(fid, "Laporan Custom", ["custom_finance"], {"ratios": ratios})
                st.success(f"💾 Disimpan di ID #{aid}")

def page_simpan():
    finora_header("Simpan", "Arsip, riwayat, dan file yang pernah dianalisis")
    df = fetch_analyses()
    if df.empty:
        st.info("Belum ada riwayat."); return
    st.dataframe(df, use_container_width=True)
    
    st.markdown("---")
    st.subheader("Detail & AI Assistant")
    aid = st.selectbox("Pilih ID Analisis", df["id"].tolist())
    an = fetch_analysis(aid)
    findings = fetch_findings(aid)
    
    if an:
        st.json(json.loads(an['summary_json']))
        if not findings.empty:
            display_findings(findings.to_dict('records'))
        
        st.markdown("### 🤖 Tanya FINORA AI")
        q = st.text_input("Pertanyaan tentang analisis ini:")
        if st.button("Kirim ke AI"):
            if q:
                ctx = f"Mode: {an['mode']}\nSummary: {an['summary_json']}\nTemuan: {findings.to_dict('records') if not findings.empty else 'Tidak ada'}"
                with st.spinner("AI berpikir..."):
                    ans = call_gemini(q, ctx)
                st.info(ans)

def page_ai():
    finora_header("FINORA AI Assistant", "Tanyakan apa pun mengenai data dan hasil analisis keuanganmu")
    df = fetch_analyses()
    if df.empty:
        st.info("Belum ada analisis."); return
        
    options = {f"#{row['id']} - {row['filename']}": int(row['id']) for _, row in df.iterrows()}
    chosen = st.selectbox("Pilih Analisis", list(options.keys()))
    aid = options[chosen]
    an = fetch_analysis(aid)
    findings = fetch_findings(aid)
    
    chats = fetch_chats(aid)
    for _, c in chats.iterrows():
        st.markdown(f"**{'🧑 Kamu' if c['role']=='user' else '🤖 AI'}:** {c['message']}")
        
    q = st.text_area("Pesan untuk AI:")
    if st.button("Kirim"):
        if q:
            save_chat(aid, "user", q)
            ctx = f"Mode: {an['mode']}\nSummary: {an['summary_json']}\nTemuan: {findings.to_dict('records') if not findings.empty else 'Tidak ada'}"
            with st.spinner("AI sedang menganalisis..."):
                ans = call_gemini(q, ctx)
            save_chat(aid, "assistant", ans)
            st.rerun()

# ============================================================
# MAIN NAVIGATION
# ============================================================
def main():
    st.sidebar.markdown('<h1 style="color:#FF5C8B;text-align:center;">💗 FINORA</h1>', unsafe_allow_html=True)
    st.sidebar.markdown('<p style="text-align:center;font-size:12px;color:#7A1F3D;">Financial Intelligence<br>Platform</p>', unsafe_allow_html=True)
    st.sidebar.markdown("---")
    
    # Hanya 4 Menu Utama + AI Assistant
    nav = st.sidebar.radio("Navigasi", ["Dashboard", "Audit", "Laporan Keuangan", "Simpan", "AI Assistant"])
    
    st.sidebar.markdown("---")
    st.sidebar.caption("⚠️ Temuan bersifat indikasi dan perlu verifikasi lanjut.")
    
    if nav == "Dashboard": page_dashboard()
    elif nav == "Audit": page_audit()
    elif nav == "Laporan Keuangan": page_laporan()
    elif nav == "Simpan": page_simpan()
    elif nav == "AI Assistant": page_ai()

if __name__ == "__main__":
    main()
