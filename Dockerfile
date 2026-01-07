# Schlankes Base-Image
FROM python:3.12-slim

# Umgebungsvariablen setzen
# PYTHONDONTWRITEBYTECODE: Verhindert .pyc Dateien
# PYTHONUNBUFFERED: Logs direkt ausgeben (wichtig für Docker Logs)
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Kopiere den INHALT von app direkt nach /app
COPY app/ .

# Jetzt liegt main.py direkt in /app
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]