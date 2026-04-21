# Stage 1: bring a known-good ffmpeg 7.1 build (Ubuntu 24.04, glibc 2.39).
FROM jrottenberg/ffmpeg:7.1-ubuntu AS ffmpeg

# Stage 2: runtime. python:3.12-slim ships Debian trixie (glibc 2.41).
# ffmpeg/ffprobe only require up to GLIBC_2.34, so trixie satisfies them.
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Copy ffmpeg binaries + their bundled shared libs into /opt/ffmpeg so they
# do NOT shadow the base image's system libs (e.g. libssl.so.3 that Python's
# _ssl extension links against). Skip libc/libm/libgcc — use trixie's.
# ca-certificates is already installed on python:3.12-slim, no apt needed.
COPY --from=ffmpeg /usr/local/bin/ffmpeg /opt/ffmpeg/bin/ffmpeg
COPY --from=ffmpeg /usr/local/bin/ffprobe /opt/ffmpeg/bin/ffprobe
COPY --from=ffmpeg /usr/local/lib /opt/ffmpeg/lib

# Wrap ffmpeg/ffprobe so only these processes see /opt/ffmpeg/lib. Keeping
# /etc/ld.so.conf.d/ untouched is intentional: a global LD path would let
# jrottenberg's libssl shadow Python's.
RUN rm -f /opt/ffmpeg/lib/libc.so* /opt/ffmpeg/lib/libm.so* /opt/ffmpeg/lib/libgcc_s.so* \
 && printf '#!/bin/sh\nexec env LD_LIBRARY_PATH=/opt/ffmpeg/lib /opt/ffmpeg/bin/ffmpeg "$@"\n' > /usr/local/bin/ffmpeg \
 && printf '#!/bin/sh\nexec env LD_LIBRARY_PATH=/opt/ffmpeg/lib /opt/ffmpeg/bin/ffprobe "$@"\n' > /usr/local/bin/ffprobe \
 && chmod +x /usr/local/bin/ffmpeg /usr/local/bin/ffprobe \
 && ffmpeg -version >/dev/null \
 && ffprobe -version >/dev/null \
 && python3 -c "import ssl; ssl.create_default_context()"

WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

RUN groupadd --system app && useradd --system --gid app --home /srv app \
 && chown -R app:app /srv
USER app

ENV PORT=8000
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3).status==200 else 1)"

CMD ["python", "-m", "app.main"]
