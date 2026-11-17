def audit_round_numbers(df, amount_col):
    findings, flagged = [], pd.DataFrame()
    try:
        s = pd.to_numeric(df[amount_col], errors="coerce")
        # Diubah: minimal 10 Juta dan kelipatan 1 Juta
        flagged = df[(s >= 10000000) & (s % 1000000 == 0)] 
        if not flagged.empty:
            findings.append({
                "module": "Observasi Nominal Bulat", 
                "risk_level": "Rendah", # Diubah jadi Rendah (Observasi)
                "title": f"{len(flagged)} transaksi dengan nominal bulat besar (>= 10 Juta)", 
                "detail": "Nominal bulat besar tidak selalu salah, namun sering menjadi indikasi estimasi tanpa rincian tagihan.", 
                "recommendation": "Lakukan uji petik dan minta dokumen pendukung (invoice/kuitansi) untuk memastikan kewajaran."
            })
    except: pass
    return findings, flagged
