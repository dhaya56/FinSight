# syntax=docker/dockerfile:1

# The FinSight API: FastAPI behind uvicorn, with the cross-encoder in-process.
#
# Two stages. The builder resolves wheels into a virtual environment; the runtime
# carries that environment and nothing that built it. pip, its wheel cache and the
# constraints file never reach the shipped layers.
#
# **Pinned by digest, not by tag.** `python:3.12-slim` moves — it is a rolling tag —
# so two builds a week apart from identical source would differ. The digest below was
# resolved by pulling the tag and reading it back; the tag is kept in a comment so a
# human can see which one it was. Python 3.12.15, Debian trixie.
ARG PYTHON_DIGEST=sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1

FROM python@${PYTHON_DIGEST} AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_ROOT_USER_ACTION=ignore \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

# Named separately because both files are called requirements.txt and a single COPY
# would collide them.
COPY requirements.txt /build/requirements-lock.txt
COPY docker/api/requirements.txt /build/requirements-api.txt

# The CPU-only torch wheel, staged by scripts/stage_build_wheels.py.
#
# **The build reaches no second index.** Every pin resolves from PyPI except torch,
# whose CPU build is published elsewhere — and measured from inside this container,
# that index presents a certificate chain this host's network re-issues and the
# container cannot verify. The two ways to make pip fetch it anyway are to stop
# verifying certificates (forbidden, §10) or to install the issuing root into the
# image (which embeds an organisation's CA in a distributable artefact and leaves the
# container trusting it at runtime). So the wheel is fetched where verification
# succeeds and carried in, and `--find-links` resolves it from disk. torch's own
# dependencies come from PyPI, which verifies normally.
COPY docker/api/wheels/ /wheels/

# The lock file carries option lines that pip rejects inside a constraints file, so
# they are stripped. The constraint still pins `torch==2.14.1+cpu` exactly, and only
# the staged wheel satisfies that — PyPI's same-version wheel is the accelerator
# build and carries a dozen vendor packages this host has no device for (ENV-001).
#
# No compiler is installed. Every pin resolves to a wheel, `psycopg-binary` included,
# so the builder needs no toolchain and the runtime inherits none.
RUN python -m venv /opt/venv \
 && grep -v '^[[:space:]]*-' /build/requirements-lock.txt > /build/constraints.txt \
 && /opt/venv/bin/pip install \
      --find-links /wheels \
      --constraint /build/constraints.txt \
      --requirement /build/requirements-api.txt \
 && find /opt/venv -name '__pycache__' -type d -prune -exec rm -rf {} + \
 && find /opt/venv -name '*.pyc' -delete


FROM python@${PYTHON_DIGEST} AS runtime

# A fixed uid/gid rather than a name, so a mounted volume's ownership is predictable
# and does not depend on the order users were created in the base image.
RUN groupadd --system --gid 10001 finsight \
 && useradd --system --uid 10001 --gid 10001 --no-create-home \
      --shell /usr/sbin/nologin finsight

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/src \
    HF_HOME=/opt/models/hf \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    OMP_NUM_THREADS=4
#
# PYTHONPATH rather than `pip install .`:
#   `persistence/database.py` resolves MIGRATIONS_PATH as `__file__`'s fourth parent
#   plus `migrations`. Under the source layout that is the repository root, which is
#   correct. Installed into site-packages it becomes `<venv>/lib/python3.12/migrations`,
#   which does not exist — and readiness compares the database revision against that
#   directory, so the API would report itself permanently un-ready. Shipping the source
#   tree keeps the derivation true and keeps the image layout identical to the one the
#   tests run against. Making the path configurable is the better fix and is recorded
#   as an open item in ENV-014.
#
# HF_HUB_OFFLINE and TRANSFORMERS_OFFLINE are belt and braces over the adapter's own
# `local_files_only=True`: if a future change dropped that argument, the environment
# still refuses the download rather than silently reaching the network from inside a
# request path.
#
# OMP_NUM_THREADS is set from measurement, not left to torch's own sizing. Left
# alone, torch reads the host's CPU count and ignores the container's limit, and
# every surplus thread contends with host-native Ollama — the one process this
# deployment cannot afford to starve (ADR-004 records an indexing run lost to exactly
# that contention).
#
# MEASURED in ENV-014, reranking 25 candidates in the container:
#
#     1 thread   5,094 ms
#     2 threads  2,648 ms
#     4 threads  2,248 ms   <- the knee
#     8 threads  2,300 ms
#
# Four, because torch reports 4 even when asked for 8: it clamps to the physical
# cores of this 4-core / 8-thread host, so the two settings are the same run. An
# earlier revision shipped 2 as a cautious guess and cost about 18% of rerank
# latency for nothing. Compose can override it (API_OMP_THREADS) for a host with a
# different core count.

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app

# Source, schema history and version metadata. Ordered least- to most-frequently
# changed so an edit to application code does not invalidate the model layer.
COPY docker/api/model/ /opt/models/hf/hub/
COPY alembic.ini pyproject.toml /app/
COPY migrations/ /app/migrations/
COPY src/ /app/src/

# Root owns the application and the runtime user only reads it, so a compromised
# process cannot rewrite its own code. Nothing here is written at runtime: the
# container runs with a read-only root filesystem and tmpfs for the paths that need
# to be writable (see compose.yaml).
#
# **That is established by COPY, not by a recursive chmod.** An earlier revision ran
# `chown -R root:root && chmod -R a-w` over /app, /opt/venv and /opt/models. Every
# file it touched was rewritten, and a rewritten file is a new layer entry — so the
# virtual environment, torch included, was stored twice in the image. Measured on the
# UI image, which had the same mistake: a 374 MiB venv inside a 1.19 GB image. COPY
# already lands root-owned files that group and other cannot write, which is exactly
# what the chmod was asserting.
USER 10001:10001

EXPOSE 8000

# Readiness, not liveness: this is the signal for "may receive traffic", and it is
# safe to use here because the probe classifies Qdrant as DEGRADABLE (§10.9) and only
# PostgreSQL and the schema revision are essential. A liveness check would report
# healthy while the API was returning 503 to every query.
#
# Python rather than curl: the slim base ships neither curl nor wget, and installing
# one to answer a health check would add a binary to the attack surface for the sake
# of a request the interpreter can already make.
#
# The 503 is caught rather than allowed to propagate: urllib raises HTTPError on a
# non-2xx, and an uncaught traceback would report unhealthy for the right reason with
# a stack trace in the daemon log on every probe during start-up.
HEALTHCHECK --interval=15s --timeout=5s --start-period=40s --retries=3 \
    CMD ["python", "-c", "import sys,urllib.request\ntry:\n    sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health/ready',timeout=4).status==200 else 1)\nexcept Exception:\n    sys.exit(1)"]

# Exec form, so uvicorn is PID 1's direct child and receives SIGTERM unwrapped by a
# shell. Compose sets `init: true` to reap zombies and forward signals.
#
# One worker. The process holds an lru_cached pipeline carrying the cross-encoder and
# an httpx client (the leak ENV-012 records), so workers do not share it — each would
# load its own copy of the model into a host that has already run out of memory once.
# Horizontal scaling belongs to replicas with a shared index, not to forked workers.
#
# **No `--proxy-headers`.** Nothing sits in front of this container, and enabling it
# would make uvicorn trust X-Forwarded-For from whoever connected — letting a client
# choose the address that reaches the application. It goes in when a reverse proxy
# does, with `--forwarded-allow-ips` naming that proxy rather than `*`.
CMD ["uvicorn", "finsight.api.app:create_app", "--factory", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "1", "--timeout-graceful-shutdown", "30"]
