FROM python:3.11-slim

# Avoid .pyc and buffer; keep logs flowing to Render
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DSM_DB_PATH=/data/dsm.db

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# SQLite file lives here; mount a Render disk to /data to persist across deploys
RUN mkdir -p /data

EXPOSE 8000

# Render injects $PORT; default to 8000 locally
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
