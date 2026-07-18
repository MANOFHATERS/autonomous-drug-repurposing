# =============================================================================
# phase2/drugos_graph/Dockerfile — Phase 2 KG builder service (SELF-CONTAINED)
# =============================================================================
# v116 ROOT FIX (Teammate 15, issue IN-013):
#   The previous Dockerfile used `FROM drugos-python-ml:2.0.0` (or
#   `FROM ${BASE_IMAGE}` defaulting to that tag). NO compose service and NO
#   Makefile target built `drugos-python-ml:2.0.0`, so
#   `docker-compose build phase2-kg-builder` FAILED on a fresh clone with
#   "manifest for drugos-python-ml:2.0.0 not found". Prior "ROOT FIX"
#   comments claimed this was fixed (by adding a build-arg) but the build
#   dependency was NEVER resolved — the image still didn't exist.
#
#   ROOT FIX: this Dockerfile is now SELF-CONTAINED — `FROM python:3.11-slim`
#   with all dependencies inlined. `docker build -f phase2/drugos_graph/
#   Dockerfile .` works on a fresh clone with ZERO prerequisites. No hidden
#   base-image dependency.
#
#   NOTE: docker-compose.yml uses Dockerfile.ml for phase2-kg-builder /
#   phase2-kg-api (for version alignment with phase3/phase4 — a Phase 3
#   checkpoint loaded by Phase 4 requires matching torch versions). This
#   Dockerfile is the STANDALONE alternative for operators who want a
#   smaller phase2-only image without torch (the KG service does NOT train
#   models — it loads the pre-built KG from Neo4j and serves HTTP).
#
# Build context: repo root.
# =============================================================================
FROM python:3.11-slim

# Pin pip for reproducible builds.
RUN pip install --no-cache-dir --upgrade \
    pip==24.2 \
    setuptools==75.1.0 \
    wheel==0.44.0

# System deps (no compilers — runtime only, smaller image).
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libpq5 \
    libxml2 \
    libxslt1.1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONHASHSEED=0

# Phase 2 KG dependencies: neo4j driver, pandas, networkx, fastapi, uvicorn.
# torch/PyG are NOT needed — the KG service does not train models. This keeps
# the image ~2GB smaller than Dockerfile.ml.
RUN pip install --no-cache-dir \
    "neo4j==5.20.0" \
    "pandas==2.1.4" \
    "numpy==1.26.4" \
    "networkx==3.2.1" \
    "scikit-learn==1.4.2" \
    "fastapi==0.110.3" \
    "uvicorn[standard]==0.29.0" \
    "pydantic==2.7.1" \
    "psycopg2-binary==2.9.9" \
    "sqlalchemy==2.0.30" \
    "requests==2.31.0" \
    "rapidfuzz==3.9.0" \
    "python-dotenv==1.0.1" \
    "pyyaml==6.0.1" \
    "certifi==2024.2.2" \
    "prometheus-client==0.20.0" \
    "psutil==5.9.8"

WORKDIR /opt/repo
# uid 10001 (not 1000) to avoid host uid collisions.
RUN useradd -m -u 10001 drugos && chown -R drugos:drugos /opt/repo

# Copy source directories the KG service needs.
COPY --chown=drugos:drugos phase2/ ./phase2/
COPY --chown=drugos:drugos phase1/ ./phase1/
COPY --chown=drugos:drugos common/ ./common/
COPY --chown=drugos:drugos shared/ ./shared/
COPY --chown=drugos:drugos scripts/ ./scripts/

USER drugos

ENV KG_SERVICE_PORT=8001 \
    KG_LOG_LEVEL=info \
    PYTHONPATH=/opt/repo:/opt/repo/phase1:/opt/repo/phase2

EXPOSE 8001

HEALTHCHECK --interval=15s --timeout=10s --retries=5 --start-period=30s \
    CMD curl -fsS http://localhost:8001/healthz || exit 1

# Default command runs the KG API. docker-compose overrides this with the
# bridge-build + uvicorn chain for phase2-kg-builder, or just uvicorn for
# phase2-kg-api.
CMD ["uvicorn", "phase2.drugos_graph.kg_api:app", "--host", "0.0.0.0", "--port", "8001"]
