FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libsndfile1 \
        ffmpeg \
        ca-certificates \
        curl \
        unzip \
    && rm -rf /var/lib/apt/lists/*

# yt-dlp needs a JavaScript runtime to solve YouTube's player challenges.
# Without one it warns "some formats may be missing" and most videos expose
# no audio format at all ("Requested format is not available"). deno is
# yt-dlp's default runtime and the install script handles the arch.
ARG DENO_INSTALL=/opt/deno
RUN curl -fsSL https://deno.land/install.sh \
      | DENO_INSTALL=${DENO_INSTALL} sh -s -- -y \
    && ln -s ${DENO_INSTALL}/bin/deno /usr/local/bin/deno \
    && deno --version

# deno caches the solver scripts here at runtime (container runs as uid 1000).
ENV DENO_DIR=/data/deno

WORKDIR /app

COPY pyproject.toml ./
RUN pip install --upgrade pip \
    && pip install \
        "fastapi>=0.115" \
        "uvicorn[standard]>=0.32" \
        "ytmusicapi>=1.8" \
        "librosa>=0.10.2" \
        "av>=13.1" \
        "numpy>=1.26,<2" \
        "pydantic>=2.9" \
        "requests>=2.32"

# yt-dlp moves fast because YouTube breaks extractors. Always pull the
# nightly so HF rebuilds pick up the freshest extractor without us
# needing to bump pyproject.toml.
RUN pip install --upgrade --pre "yt-dlp[default]"

COPY app ./app

RUN mkdir -p /data/audio /data/deno && chmod -R 777 /data

EXPOSE 7860

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7860"]
