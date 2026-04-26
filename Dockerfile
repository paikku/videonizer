# Stage 1: bring a known-good ffmpeg 7.1 build (Ubuntu 24.04, glibc 2.39).
FROM jrottenberg/ffmpeg:7.1-ubuntu AS ffmpeg

# Stage 2: runtime. python:3.12-slim ships Debian trixie (glibc 2.41).
# ffmpeg/ffprobe only require up to GLIBC_2.34, so trixie satisfies them.
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UVICORN_RELOAD=0

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

# --- pip index --------------------------------------------------------------
# Build-time ARGs so airgapped builds can redirect pip at an internal mirror.
# Empty defaults = fall back to public PyPI. All three are picked up via pip's
# well-known env vars — no Dockerfile conditionals.
#
# Example (substitute your own index URLs):
#   docker build \
#     --build-arg PIP_INDEX_URL=<your-index-url> \
#     --build-arg PIP_EXTRA_INDEX_URL=<your-extra-index-url> \
#     --build-arg PIP_TRUSTED_HOST=<your-host> \
#     -t videonizer .
ARG PIP_INDEX_URL=
ARG PIP_EXTRA_INDEX_URL=
ARG PIP_TRUSTED_HOST=
ENV PIP_INDEX_URL=${PIP_INDEX_URL} \
    PIP_EXTRA_INDEX_URL=${PIP_EXTRA_INDEX_URL} \
    PIP_TRUSTED_HOST=${PIP_TRUSTED_HOST}

COPY requirements.txt .
# ultralytics pulls `opencv-python` (non-headless) as a transitive dep. The
# non-headless wheel dynamically loads libGL.so.1, which is NOT present in
# python:3.12-slim → runtime crash on first FastSAM inference. Uninstall it
# and force-reinstall the headless variant so `import cv2` doesn't touch
# libGL. Keep the `|| true` in case the non-headless wheel didn't land
# (future ultralytics versions may switch to headless).
RUN pip install --no-cache-dir -r requirements.txt \
 && (pip uninstall -y opencv-python opencv-contrib-python || true) \
 && pip install --no-cache-dir --force-reinstall --no-deps opencv-python-headless==4.10.0.84

COPY app ./app

# --- Segmentation model weights --------------------------------------------
# All five public model ids back onto a weight file committed under ./weights/.
# Files larger than GitHub's 100MB limit are committed as split chunks
# (`<name>.pt.part_00`, `<name>.pt.part_01`, ...) and reassembled here so
# the running container only ever sees the original .pt file.
COPY weights/ /opt/segment-weights/
RUN cd /opt/segment-weights \
 && for first in *.part_00; do \
      [ -f "$first" ] || continue ; \
      out="${first%.part_00}" ; \
      cat "${out}".part_* > "${out}" ; \
      rm "${out}".part_* ; \
      echo "reassembled ${out}: $(stat -c '%s' ${out}) bytes" ; \
    done
ENV SEGMENT_WEIGHTS_DIR=/opt/segment-weights \
    YOLO_CONFIG_DIR=/tmp/ultralytics

RUN groupadd --system app && useradd --system --gid app --home /srv app \
 && chown -R app:app /srv
USER app

ENV PORT=8000
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3).status==200 else 1)"

# Dev tip:
# -v $(pwd)/app:/srv/app -e UVICORN_RELOAD=1
# 를 사용하면 소스만 바꿔도 이미지 재빌드 없이 컨테이너 내에서 자동 리로드됩니다.
CMD ["sh", "-c", "if [ \"$UVICORN_RELOAD\" = \"1\" ]; then exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --reload --reload-dir /srv/app; else exec python -m app.main; fi"]
