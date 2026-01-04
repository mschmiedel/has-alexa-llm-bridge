# Schlankes Base-Image
FROM python:3.11-slim

# Umgebungsvariablen setzen
# PYTHONDONTWRITEBYTECODE: Verhindert .pyc Dateien
# PYTHONUNBUFFERED: Logs direkt ausgeben (wichtig für Docker Logs)
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /

# Dependencies installieren (Caching Layer nutzen!)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App Code kopieren
COPY app/ ./app/

# Port exponieren (Doku-Zwecke)
EXPOSE 8000

# Startbefehl (Production-Ready ohne Reload)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
