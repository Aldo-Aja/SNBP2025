import pandas as pd
import numpy as np
import re
from fastapi import FastAPI
from pydantic import BaseModel
from typing import List, Optional

app = FastAPI()

# --- 1. LOAD DATA SAAT STARTUP ---
# Kita load data sekali saja agar super cepat saat ada request
try:
    # Load Database Jurusan
    # Asumsi struktur CSV: Kode PTN, Nama Kampus, Kode Jurusan, Nama Jurusan, Jenjang, Portofolio, Peminat 2024, DT 2024, Peminat 2025, DT 2025, Link
    df_db = pd.read_csv("database_jurusan.csv")
    
    # Pastikan nama kolom standar (jaga-jaga header csv beda dikit)
    # Kita rename kolom berdasarkan urutan index agar aman
    df_db.columns = [
        'kode_ptn', 'nama_ptn', 'kode_jurusan', 'nama_jurusan', 'jenjang', 
        'portofolio', 'peminat_2024', 'dt_2024', 'peminat_2025', 'dt_2025', 'link'
    ]

    # Bersihkan data numeric
    df_db['peminat_2025'] = pd.to_numeric(df_db['peminat_2025'], errors='coerce').fillna(0)
    df_db['dt_2025'] = pd.to_numeric(df_db['dt_2025'], errors='coerce').fillna(0)

    # HITUNG RASIO OTOMATIS (Rasio = Daya Tampung / Peminat)
    # Kita hindari pembagian dengan nol
    df_db['rasio'] = df_db.apply(
        lambda x: x['dt_2025'] / x['peminat_2025'] if x['peminat_2025'] > 0 else 0, 
        axis=1
    )

    # Load Mapping Sinonim
    # Delimiter file Anda adalah titik koma (;)
    df_sin = pd.read_csv("mapping_sinonim.csv", sep=';', header=None)
    sinonim_list = []
    for _, row in df_sin.iterrows():
        # Ambil kata-kata yang tidak kosong di setiap baris
        terms = [str(x).lower().strip() for x in row if pd.notna(x) and str(x).strip() != '']
        if terms:
            sinonim_list.append(terms)

    print("Data berhasil diload!")

except Exception as e:
    print(f"Error Loading Data: {e}")
    # Buat dataframe kosong agar tidak crash
    df_db = pd.DataFrame()
    sinonim_list = []

# --- 2. HELPER FUNCTIONS (LOGIKA PEMROSESAN TEKS) ---

def norm(text):
    if pd.isna(text) or text == "":
        return ""
    text = str(text).lower()
    text = re.sub(r"\(.*?\)", "", text) # Hapus (...)
    text = re.sub(r"[^a-z0-9 ]", " ", text) # Hapus simbol
    # Hapus stopwords (sesuai kode GAS Anda)
    stopwords = r"\b(prodi|program|studi|jurusan|ilmu|fakultas|sekolah|departemen|dan|of|and|the)\b"
    text = re.sub(stopwords, "", text)
    return re.sub(r"\s+", " ", text).strip()

def get_jenjang_group(text):
    s = str(text).upper()
    if re.search(r"(D3|D4|DIPLOMA|TERAPAN)", s):
        return "VOKASI"
    return "S1"

def check_synonym_match(input_norm, target_norm):
    # Cek setiap kelompok sinonim
    for group in sinonim_list:
        group_str = " ".join(group)
        # Jika input ada di dalam kelompok ini
        if any(term in input_norm for term in group):
            # Cek apakah target juga punya kata dari kelompok ini
            for term in group:
                if term in target_norm:
                    return True
    return False

def is_jurusan_match(input_norm, target_norm):
    # 1. Cek Sinonim
    if check_synonym_match(input_norm, target_norm):
        return True
        
    # 2. Blacklist Logic (Manajemen)
    if 'manajemen' in input_norm:
        if re.search(r"hutan|informatika|sumber|perairan|pendidikan|rekayasa|industri", target_norm):
            return False

    # 3. Word Overlap Logic
    words = [w for w in input_norm.split() if len(w) > 2]
    if not words: return False
    
    matches = sum(1 for w in words if w in target_norm)
    return (matches / len(words)) >= 0.5

# --- 3. API ENDPOINT ---

class FilterRequest(BaseModel):
    jurusan_input: str
    ptn_asal: str # Nama atau Kode PTN asal siswa
    rasio_siswa: float # Nilai rasio siswa (desimal, misal 0.04)

@app.post("/rekomendasi")
def get_rekomendasi(req: FilterRequest):
    if df_db.empty:
        return {"status": "error", "message": "Database belum siap"}

    input_norm = norm(req.jurusan_input)
    input_jenjang = get_jenjang_group(req.jurusan_input)
    
    # --- STEP A: FILTER PANDAS (Cepat) ---
    # 1. Filter Jenjang
    mask_jenjang = df_db['jenjang'].apply(get_jenjang_group) == input_jenjang
    
    # 2. Filter PTN Asal (Jangan rekomendasikan PTN sendiri jika itu logikanya)
    # Asumsi: input ptn_asal adalah string nama PTN atau kode. Sesuaikan dengan kolom 'kode_ptn' atau 'nama_ptn'
    mask_ptn = df_db['kode_ptn'].astype(str) != str(req.ptn_asal)
    
    # 3. Filter Rasio
    # Logika GAS: diff = rasio_target - rasio_siswa
    # Syarat: (diff >= -0.02 && diff <= 0.90) OR (rasio_siswa == 0 && diff > 0)
    diff = df_db['rasio'] - req.rasio_siswa
    mask_rasio = ((diff >= -0.02) & (diff <= 0.90))
    if req.rasio_siswa == 0:
        mask_rasio = mask_rasio | (diff > 0)

    # Gabungkan semua mask filter dasar
    filtered = df_db[mask_jenjang & mask_ptn & mask_rasio].copy()

    # --- STEP B: FILTER LOGIC TEXT (Agak Berat, tapi Python tetap cepat) ---
    
    # Siapkan flag khusus
    is_gigi = "gigi" in input_norm
    is_hewan = "hewan" in input_norm
    is_dokter = ("dokter" in input_norm or "kedokteran" in input_norm) and not is_gigi and not is_hewan
    is_teknik = input_norm.startswith("teknik")

    def complex_filter(row):
        target_norm = norm(row['nama_jurusan'])
        
        # Strict Keywords
        if is_teknik and not target_norm.startswith("teknik"): return False
        if is_gigi and "gigi" not in target_norm: return False
        if not is_gigi and "gigi" in target_norm: return False
        if is_hewan and "hewan" not in target_norm: return False
        if not is_hewan and "hewan" in target_norm: return False
        if is_dokter and not ("dokter" in target_norm or "kedokteran" in target_norm): return False
        
        # Match Nama/Sinonim
        return is_jurusan_match(input_norm, target_norm)

    # Terapkan filter text
    final_results = filtered[filtered.apply(complex_filter, axis=1)]

    # --- STEP C: SORTING & OUTPUT ---
    # Sort berdasarkan rasio (semakin kecil rasio = semakin ketat)
    # Di GAS code lama: sort by val (rasio) ascending
    final_results = final_results.sort_values(by='rasio', ascending=True).head(10)

    output = []
    for _, row in final_results.iterrows():
        # Format text untuk tampilan Spreadsheet
        # Format: NAMA JURUSAN - NAMA PTN (XX,XX%)
        pct = row['rasio'] * 100
        text_display = f"{row['nama_jurusan']} - {row['nama_ptn']} ({pct:.2f}%)"
        
        output.append({
            "kode_ptn": str(row['kode_ptn']),
            "nama_jurusan": row['nama_jurusan'],
            "jenjang": row['jenjang'],
            "rasio": row['rasio'],
            "text_display": text_display
        })

    return {"status": "success", "data": output}

@app.get("/")
def home():
    return {"message": "API Rasionalisasi is Running"}