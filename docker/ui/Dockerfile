# syntax=docker/dockerfile:1

# The FinSight UI: Streamlit, talking to the API over HTTP and to nothing else.
#
# Same two-stage shape and same digest as the API image, so both derive from one
# verified base. What differs is what is *absent*: no database driver, no
# object-store client, no vector-store client, no model runtime. The most exposed
# tier in the system cannot reach a datastore because it carries nothing capable of
# it — a stronger property than configuration that merely withholds the credentials.
ARG PYTHON_DIGEST=sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1

FROM python@${PYTHON_DIGEST} AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_ROOT_USER_ACTION=ignore \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

COPY requirements.txt /build/requirements-lock.txt
COPY docker/ui/requirements.txt /build/requirements-ui.txt

RUN python -m venv /opt/venv \
 && grep -v '^[[:space:]]*-' /build/requirements-lock.txt > /build/constraints.txt \
 && /opt/venv/bin/pip install \
      --constraint /build/constraints.txt \
      --requirement /build/requirements-ui.txt \
 && find /opt/venv -name '__pycache__' -type d -prune -exec rm -rf {} + \
 && find /opt/venv -name '*.pyc' -delete


FROM python@${PYTHON_DIGEST} AS runtime

RUN groupadd --system --gid 10002 finsight \
 && useradd --system --uid 10002 --gid 10002 --no-create-home \
      --shell /usr/sbin/nologin finsight

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/src \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    STREAMLIT_SERVER_FILE_WATCHER_TYPE=none
#
# A different uid from the API's 10001, so the two containers are distinguishable in
# host-level audit output rather than both appearing as the same actor.
#
# Usage statistics off: Streamlit reports to its vendor by default, and §10 forbids
# telemetry leaving this deployment. The file watcher is off because the source is
# read-only here — watching it costs inotify handles to detect edits that cannot
# happen.

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app

COPY .streamlit/config.toml /app/.streamlit/config.toml
COPY pyproject.toml /app/
COPY src/ /app/src/

# **No `chown -R` or `chmod -R` here, deliberately.** A recursive mode change rewrites
# every file it touches, and a rewritten file is a new layer entry — so the venv would
# be stored twice and the image would carry a duplicate it never reads. Measured on the
# first build of this image: a 374 MiB virtual environment inside a 1.19 GB image.
#
# It bought nothing either way. COPY lands files owned by root with group and other
# unable to write them, the process runs as an unprivileged uid, and Compose mounts the
# root filesystem read-only. The permissions the chmod was setting were already the
# permissions in place.
USER 10002:10002

EXPOSE 8501

# Streamlit's own health endpoint. It reports that the server is serving, which is all
# readiness means here: the UI holds no dependency of its own, and whether the API is
# reachable is the API's readiness to answer. A UI that reported itself unhealthy
# because the API was down would remove the one surface able to say so.
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import sys,urllib.request\ntry:\n    sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health',timeout=4).status==200 else 1)\nexcept Exception:\n    sys.exit(1)"]

# Exec form for signal delivery, as in the API image.
#
# CORS and XSRF protection are left at their defaults rather than disabled. The usual
# reason to disable them is a reverse proxy, and there isn't one; turning them off
# pre-emptively is how a browser-facing app ends up accepting cross-origin posts.
CMD ["streamlit", "run", "/app/src/finsight/ui/app.py", \
     "--server.address", "0.0.0.0", "--server.port", "8501"]
