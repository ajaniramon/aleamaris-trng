# AleaMaris TRNG — Dockerfile
# FastAPI + uvicorn. Containers usually have no camera, so by default the
# collector runs on sample.MP4 *without crediting it* (a recording is not
# entropy): /trng/bytes answers 503 and the DRBG is seeded from os.urandom.
# - Real TRNG: pass a camera through (--device /dev/video0), ALEAMARIS_USE_CAM=1.
# - Pipeline demo: ALEAMARIS_CREDIT_FILE_SOURCE=1 (output is reproducible and
#   /trng/bytes marks it with an X-TRNG-Demo header).

FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src
COPY sample.MP4 /app/sample.MP4

ENV ALEAMARIS_VIDEO=/app/sample.MP4 \
    ALEAMARIS_CREDIT_FILE_SOURCE=0 \
    ALEAMARIS_ALLOW_URANDOM=1

EXPOSE 8080

CMD ["uvicorn", "--app-dir", "src", "api.app:app", "--host", "0.0.0.0", "--port", "8080"]
