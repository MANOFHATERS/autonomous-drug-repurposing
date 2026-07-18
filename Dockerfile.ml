# =============================================================================
# Dockerfile.ml — Multi-stage ML image for Phase 3 (GT) + Phase 4 (RL)
# =============================================================================
# v116 ROOT FIX (Teammate 15, issues IN-006/IN-012/IN-061):
#   IN-012 (MEDIUM): added ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
#       PYTHONHASHSEED=0 for reproducible builds + no .pyc bloat. Pinned
#       pip/setuptools/wheel in the builder stage.
#   IN-061 (LOW): added EXPOSE 8002 8003 + HEALTHCHECK so `docker run`
#       (without compose) has port documentation + liveness monitoring.
#   IN-012 (MEDIUM): changed uid 1000 → 10001 to avoid collisions with host
#       users on dev machines (uid 1000 is the default first human user on
#       most Linux distros — colliding causes permission errors on bind mounts).
#   IN-006 (HIGH): GPU support is in Dockerfile.gpu + docker-compose.gpu.yml
#       (override). This Dockerfile stays CPU-only so CI/dev laptops work.
#
# Multi-stage build:
#   Stage 1 (builder): compile heavy deps (torch, PyG, RDKit wheels)
#   Stage 2 (runtime): copy only installed site-packages + repo source
# =============================================================================
FROM python:3.14-slim AS builder

# IN-012: pin pip for reproducible builder stage.
RUN pip install --no-cache-dir --upgrade \
    pip==24.2 \
    setuptools==75.1.0 \
    wheel==0.44.0

# ─── System deps for building Python wheels ──────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    git \
    libpq-dev \
    libxml2-dev \
    libxslt1-dev \
    && rm -rf /var/lib/apt/lists/*

# ─── PyTorch CPU + PyTorch Geometric (aligned to torch 2.2.0) ───────────
# IN-070 ROOT FIX: torch and PyG wheels are aligned to the EXACT same patch
# version (2.2.0) to avoid undefined-symbol crashes from ABI drift.
RUN pip install --no-cache-dir --prefix=/install \
    "torch==2.2.0+cpu" \
    --index-url https://download.pytorch.org/whl/cpu

RUN pip install --no-cache-dir --prefix=/install \
    "torch-geometric==2.5.3" \
    "torch-scatter==2.1.2+pt22cpu" \
    "torch-sparse==0.6.18+pt22cpu" \
    -f https://data.pyg.org/whl/torch-2.2.0+cpu.html

RUN pip install --no-cache-dir --prefix=/install \
    "rdkit==2024.3.5" \
    "pandas==2.1.4" \
    "numpy==1.26.4" \
    "scikit-learn==1.4.2" \
    "networkx==3.2.1" \
    "transformers==4.40.2" \
    "stable-baselines3==2.3.0" \
    "gymnasium==0.29.1" \
    "biopython==1.83" \
    "mlflow==2.15.1" \
    "fastapi==0.110.3" \
    "uvicorn[standard]==0.29.0" \
    "pydantic==2.7.1" \
    "psycopg2-binary==2.9.9" \
    "sqlalchemy==2.0.30" \
    "neo4j==5.20.0" \
    "requests==2.31.0" \
    "rapidfuzz==3.9.0" \
    "python-dotenv==1.0.1" \
    "lxml==5.2.1" \
    "filelock==3.14.0" \
    "pyarrow==15.0.2" \
    "pyyaml==6.0.1" \
    "certifi==2024.2.2" \
    "prometheus-client==0.20.0" \
    "psutil==5.9.8"

# =============================================================================
# Stage 2 — Runtime image
# =============================================================================
FROM python:3.14-slim AS runtime

# IN-012: reproducibility env vars.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONHASHSEED=0

# ─── Runtime system deps (no compilers — smaller image) ──────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libpq5 \
    libxml2 \
    libxslt1.1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    && rm -rf /var/lib/apt/lists/*

# ─── Copy installed Python packages from builder ────────────────────────
COPY --from=builder /install /usr/local

# IN-092 v117 ROOT FIX (Teammate 8): COPY the repo source directories
# into the runtime image. The previous runtime stage had NO COPY
# statements for source dirs — only the installed Python packages
# were copied. This meant `docker run drugos-phase3-gt` (without the
# docker-compose bind mount) failed with:
#     ModuleNotFoundError: No module named 'scripts'
# because /opt/repo was empty (only WORKDIR created the directory).
#
# The docker-compose.yml bind-mounts `./:/opt/repo` for phase3-trainer
# and phase4-rl, which SHADOWS this COPY at runtime. But `docker run`
# (without compose) is broken without these COPYs. The audit (IN-092)
# explicitly requires:
#   "Add COPY --chown=drugos:drugos phase1/ ./phase1/, etc. (matching
#    phase2/drugos_graph/Dockerfile pattern). The docker-compose bind
#    mount will shadow this, but `docker run` will work."
#
# This matches the phase2/drugos_graph/Dockerfile pattern (lines 75-79).
# All directories the ML services (phase3-trainer, phase3-gt-api,
# phase4-rl) need at runtime are COPYed:
#   - phase1/    : Phase 1 processed_data (CSVs) + service code
#   - phase2/    : Phase 2 KG builder + bridge code
#   - phase4/    : Phase 4 writeback module (rl/rl_drug_ranker imports it)
#   - rl/        : Phase 4 RL ranker code
#   - graph_transformer/ : Phase 3 GT model code
#   - shared/    : Shared contracts (writeback, feature_names, etc.)
#   - common/    : Common utilities
#   - scripts/   : gt_api.py, rl_api.py (the FastAPI apps)
#   - run_4phase.py : the 4-phase pipeline entrypoint
WORKDIR /opt/repo
# IN-012: uid 10001 (not 1000) to avoid host uid collisions.
RUN useradd -m -u 10001 drugos && chown -R drugos:drugos /opt/repo
# IN-092: COPY source dirs with --chown so the drugos user owns them.
# These are COPYed BEFORE USER drugos so the COPY runs as root (Docker
# requirement) but the files are owned by drugos after COPY.
COPY --chown=drugos:drugos phase1/ ./phase1/
COPY --chown=drugos:drugos phase2/ ./phase2/
COPY --chown=drugos:drugos phase4/ ./phase4/
COPY --chown=drugos:drugos rl/ ./rl/
COPY --chown=drugos:drugos graph_transformer/ ./graph_transformer/
COPY --chown=drugos:drugos shared/ ./shared/
COPY --chown=drugos:drugos common/ ./common/
COPY --chown=drugos:drugos scripts/ ./scripts/
COPY --chown=drugos:drugos run_4phase.py ./
USER drugos

# IN-061: EXPOSE both ports since this image is shared by phase3 (8002) and
# phase4 (8003). compose maps the correct port per service.
EXPOSE 8002 8003

# IN-061: HEALTHCHECK in the Dockerfile (fallback for `docker run` without
# compose). compose overrides with its own healthcheck per service.
HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=120s \
    CMD curl -fsS http://localhost:8002/healthz || exit 1

# ─── Default command (overridden by docker-compose per service) ─────────
CMD ["uvicorn", "scripts.gt_api:app", "--host", "0.0.0.0", "--port", "8002"]
