# Gunakan image Python ringan
FROM python:3.9-slim

# Set folder kerja
WORKDIR /app

# Copy requirements dan install dulu (agar cache efisien)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy semua file lain (main.py, csv)
COPY . .

# Berikan izin ke folder (wajib untuk Hugging Face)
RUN chmod -R 777 /app

# Jalankan aplikasi dengan port 7860 (Port wajib Hugging Face)
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "7860"]