# AleaMaris TRNG — Dockerfile
# FastAPI + uvicorn. Containers usually have no camera, so by default this runs
# the *demo* pipeline on sample.MP4 (a recording: its output is NOT secret) and
# lets the DRBG fall back to os.urandom once the video is used up.
# For a real TRNG, pass a camera through (--device /dev/video0) and set
# ALEAMARIS_USE_CAM=1, ALEAMARIS_CREDIT_FILE_SOURCE=0.

FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src
COPY sample.MP4 /app/sample.MP4

ENV ALEAMARIS_VIDEO=/app/sample.MP4 \
    ALEAMARIS_CREDIT_FILE_SOURCE=1 \
    ALEAMARIS_ALLOW_URANDOM=1

EXPOSE 8080

CMD ["uvicorn", "--app-dir", "src", "api.app:app", "--host", "0.0.0.0", "--port", "8080"]
