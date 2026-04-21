# Pinned to bookworm so apt installs a coherent ffmpeg/ffprobe + libs combo.
# Trixie ships ffmpeg 7.x (libavdevice.so.61); partial upgrades broke our
# linkage once — pin + runtime verification below prevents a repeat.
FROM python:3.12-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && ffmpeg -version >/dev/null \
 && ffprobe -version >/dev/null

WORKDIR /srv

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app

RUN groupadd --system app && useradd --system --gid app --home /srv app \
 && chown -R app:app /srv
USER app

ENV PORT=8080
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3).status==200 else 1)"

CMD ["python", "-m", "app.main"]
