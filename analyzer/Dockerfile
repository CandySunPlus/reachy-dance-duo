FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    QQ_CREDENTIAL_PATH=/data/qq/credential.json

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libsndfile1 \
        ffmpeg \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
RUN pip install --upgrade pip \
    && pip install \
        "fastapi>=0.115" \
        "uvicorn[standard]>=0.32" \
        "librosa>=0.10.2" \
        "av>=13.1" \
        "numpy>=1.26,<2" \
        "pydantic>=2.9" \
        "httpx>=0.27" \
        "qqmusic-api-python>=0.8.2,<0.9"

COPY app ./app

# /data/audio is the LRU audio cache; /data/qq holds the QQ credential and is
# kept private (0600 files inside a 0700 directory).
RUN mkdir -p /data/audio /data/qq \
    && chown -R 1000:1000 /data \
    && chmod 700 /data/qq \
    && chmod 777 /data/audio

EXPOSE 7860

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7860"]
