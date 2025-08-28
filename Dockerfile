# AleaMaris TRNG — Dockerfile
# Arranca FastAPI con uvicorn usando sample.MP4 como fuente de vídeo

FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Dependencias mínimas para opencv-python (lectura de vídeo)
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       libgl1 \
       libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Instalar deps
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copiar código y vídeo de muestra
COPY src ./src
COPY sample.MP4 /app/sample.MP4

# Variables por defecto: usa sample.MP4 y sin API key
ENV ALEAMARIS_VIDEO=/app/sample.MP4 \
    ALEAMARIS_API_KEY="" \
    ALEAMARIS_ALLOW_URANDOM=1

EXPOSE 8080

CMD ["uvicorn", "--app-dir", "src", "api.app:app", "--host", "0.0.0.0", "--port", "8080"]
