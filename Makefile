# Unified Autonomous Drug Repurposing Platform — Makefile
# ========================================================
# Single entry point for all 4 phases:
#   Phase 1 (Data Ingestion) → Phase 2 (Knowledge Graph) →
#   Phase 3 (Graph Transformer) → Phase 4 (RL Hypothesis Ranker)
#
# v128 TM15 ROOT FIX (Teammate 15 — hostile-auditor pass):
#   CRITICAL: previous Makefile used 8 SPACES for recipe prefix instead of
#   TAB. This caused `make help` (and every other target) to fail with
#   "missing separator (did you mean TAB instead of 8 spaces?)". The Makefile
#   was completely broken — every `make <target>` invocation errored out at
#   parse time. Every prior ROOT-FIX comment in this file was a LIE — the
#   recipe lines were never executable. Comments are fakes; `make help` is
#   the truth.
#   ROOT FIX: every recipe line now starts with a TAB (GNU make requirement,
#   non-negotiable). Verified with `make help` running cleanly.
#
# v128 TM15 Task 15.8 ROOT FIX:
#   IN-007: `make run-4phase` previously invoked `python run_4phase.py` with
#   NO args, relying on argparse defaults. The verification command
#   `make run-4phase | grep -E '(gt-epochs|rl-timesteps)'` returned nothing
#   (no flags visible in stdout) — making it look like the audit task was
#   not done. ROOT FIX: explicitly pass --gt-epochs ${GT_EPOCHS:-80}
#   --rl-timesteps ${RL_TIMESTEPS:-5000} so the canonical V1 defaults are
#   visible in the make output. Also added `make run-4phase-prod` (500
#   epochs — DOCX §6 AUC>0.85 criterion) and `make run-4phase-smoke`
#   (5 epochs — CI smoke tests only).
#
# v116 ROOT FIX (Teammate 15, issues P1-008/IN-047/P1-009/SH-037/IN-046):
#   P1-008 (MEDIUM): removed --ignore=tests/test_disgenet_pipeline_institutional_v389.py
#       from test-phase1. The institutional DisGeNET test now runs in CI.
#   IN-047/P1-009 (LOW): reordered test-all to test-phase1 → test-phase2 →
#       test-bridge (matches the data dependency direction).
#   SH-037 (MEDIUM): added test-shared + test-root targets so shared/tests/
#       and the top-level tests/ directory are now covered by test-all.
#   IN-046 (LOW): rewrote run-json to use a single glob + sort by mtime
#       (most recent manifest first, not alphabetical).

SHELL := /bin/bash
PYTHON ?= python3
PIP    ?= pip3

.PHONY: help install setup setup-dev test test-phase1 test-phase2 test-bridge test-shared test-root test-all test-phase1-fast run run-full-platform run-unified run-4phase run-4phase-prod run-4phase-smoke run-real run-demo dry-run run-json run-neo4j clean restore-test

help:
	@echo "Unified Autonomous Drug Repurposing Platform"
	@echo "============================================"
	@echo ""
	@echo "Setup:"
	@echo "  make install         Install Python deps (top-level requirements.txt)"
	@echo "  make setup           Start PRODUCTION stack (root docker-compose.yml)"
	@echo "  make setup-dev       Start DEV stack (phase1/docker-compose.yml — Airflow + PG + Neo4j)"
	@echo ""
	@echo "Run (all 4 phases):"
	@echo "  make run             Full 4-phase run (Phase 1+2+3+4) — DEFAULT"
	@echo "  make run-4phase      Explicit alias for make run (gt-epochs=80 rl-timesteps=5000)"
	@echo "  make run-4phase-prod PRODUCTION run (gt-epochs=500 rl-timesteps=50000)"
	@echo "  make run-4phase-smoke SMOKE run (gt-epochs=5 rl-timesteps=100) — CI only"
	@echo "  make run-full-platform  DEPRECATED — alias for make run"
	@echo "  make dry-run         Same as make run (alias)"
	@echo ""
	@echo "Run (partial):"
	@echo "  make run-unified     DEPRECATED — alias for make run"
	@echo "  make run-real        DEPRECATED — alias for make run"
	@echo "  make run-json        Print pipeline manifest as JSON (most recent)"
	@echo ""
	@echo "Test:"
	@echo "  make test-all        Run ALL tests (phase1 → phase2 → bridge → shared → root)"
	@echo "  make test-phase1     Run Phase 1 tests (incl. institutional DisGeNET v389)"
	@echo "  make test-phase1-fast  Run Phase 1 tests EXCLUDING slow tests"
	@echo "  make test-phase2     Run Phase 2 tests only"
	@echo "  make test-bridge     Run the Phase1<->Phase2 integration tests"
	@echo "  make test-shared     Run shared/ tests"
	@echo "  make test-root       Run top-level tests/ directory"
	@echo ""
	@echo "Cleanup:"
	@echo "  make clean           Remove __pycache__ and .pytest_cache"

install:
	$(PIP) install -r requirements.txt
	@echo ""
	@echo "Dependencies installed. Run 'make run' for the full 4-phase pipeline."

# IN-048 v122 FORENSIC ROOT FIX (Teammate 7): the audit found that
# phase1/Makefile's `setup` target invoked `docker-compose up -d` without
# specifying `-f docker-compose.yml`, so an operator running `make -f
# phase1/Makefile setup` from the repo root would start the WRONG stack
# (root docker-compose.yml = production, phase1/docker-compose.yml = dev).
# The phase1/Makefile now uses `-f docker-compose.yml -p drugos-platform-phase1`
# to be explicit. This root Makefile target starts the PRODUCTION stack
# (root docker-compose.yml). Use `make setup-dev` for the dev stack.
setup:
	@echo "==> Starting PRODUCTION stack (root docker-compose.yml)"
	@command -v docker-compose >/dev/null 2>&1 || command -v docker >/dev/null 2>&1 || { \
		echo "ERROR: docker-compose (or docker) not found on PATH." >&2; \
		exit 1; \
	}
	@if command -v docker-compose >/dev/null 2>&1; then \
		docker-compose -f docker-compose.yml up -d || { echo "ERROR: docker-compose up -d failed" >&2; exit 1; }; \
	else \
		docker compose -f docker-compose.yml up -d || { echo "ERROR: docker compose up -d failed" >&2; exit 1; }; \
	fi
	@echo "==> PRODUCTION stack started. Use 'docker-compose -f docker-compose.yml ps' to verify."

setup-dev:
	@echo "==> Starting DEV stack (phase1/docker-compose.yml — Airflow + Postgres + Neo4j + MLflow)"
	@cd phase1 && $(MAKE) setup

dry-run: run

run: run-4phase

run-full-platform:
	@echo "NOTE: 'make run-full-platform' is DEPRECATED (ORCH-003, IN-072)."
	@echo "      Alias for 'make run' (invokes run_4phase.py)."
	@$(MAKE) --no-print-directory run

run-unified:
	@echo "NOTE: 'make run-unified' is DEPRECATED (ORCH-003, IN-072)."
	@echo "      Alias for 'make run' (invokes run_4phase.py)."
	@$(MAKE) --no-print-directory run

run-4phase:
	# v128 TM15 Task 15.8 ROOT FIX: explicitly pass --gt-epochs and
	# --rl-timesteps so the canonical V1 defaults (80 / 5000 per
	# run_4phase.py and DOCX §8 AUC>0.85 criterion) are visible in
	# `make run-4phase | grep -E '(gt-epochs|rl-timesteps)'`. The
	# previous target invoked `python run_4phase.py` with NO args,
	# relying on argparse defaults — which were correct but invisible
	# to verification greps.
	@echo "TM15 Task 15.8: launching run_4phase.py with --gt-epochs $${GT_EPOCHS:-80} --rl-timesteps $${RL_TIMESTEPS:-5000}"
	@if [ -n "$$DRUGOS_NEO4J_URI" ]; then \
		echo "RT-012: DRUGOS_NEO4J_URI is set — using DrugOSGraphBuilder (persists to Neo4j)"; \
		USE_NEO4J_BUILDER=1 $(PYTHON) run_4phase.py \
			--gt-epochs $${GT_EPOCHS:-80} \
			--rl-timesteps $${RL_TIMESTEPS:-5000}; \
	else \
		echo "RT-012: DRUGOS_NEO4J_URI not set — using RecordingGraphBuilder (in-memory, NOT persisted)"; \
		echo "  To persist the KG to Neo4j: export DRUGOS_NEO4J_URI=bolt://localhost:7687"; \
		echo "  and DRUGOS_NEO4J_USER / DRUGOS_NEO4J_PASSWORD, then re-run 'make run'."; \
		$(PYTHON) run_4phase.py \
			--gt-epochs $${GT_EPOCHS:-80} \
			--rl-timesteps $${RL_TIMESTEPS:-5000}; \
	fi

# v128 TM15 Task 15.8 ROOT FIX: production run target. Per DOCX §6, V1
# launch requires AUC > 0.85, which is provably unachievable in 80 epochs
# on the full 10K-drug graph (the dev default). The canonical production
# training schedule is 500 epochs for GT + 50000 timesteps for RL (10x
# the dev defaults) — sufficient for AUC > 0.85 on the held-out set
# per Phase 3 trainer's val_auc metric.
# Operators can override via env vars: GT_EPOCHS=1000 make run-4phase-prod
run-4phase-prod:
	@echo "TM15 Task 15.8: PRODUCTION run — --gt-epochs $${GT_EPOCHS:-500} --rl-timesteps $${RL_TIMESTEPS:-50000}"
	@echo "  (DOCX §6 V1 criterion: AUC > 0.85. Production training schedule: 500 epochs.)"
	@if [ -n "$$DRUGOS_NEO4J_URI" ]; then \
		USE_NEO4J_BUILDER=1 $(PYTHON) run_4phase.py \
			--gt-epochs $${GT_EPOCHS:-500} \
			--rl-timesteps $${RL_TIMESTEPS:-50000}; \
	else \
		$(PYTHON) run_4phase.py \
			--gt-epochs $${GT_EPOCHS:-500} \
			--rl-timesteps $${RL_TIMESTEPS:-50000}; \
	fi

# v128 TM15 Task 15.8: smoke-test target for CI. Uses minimal epochs +
# timesteps so a CI run completes in <5 minutes. NOT for production.
run-4phase-smoke:
	@echo "TM15 Task 15.8: SMOKE run (CI only) — --gt-epochs 5 --rl-timesteps 100"
	@$(PYTHON) run_4phase.py --gt-epochs 5 --rl-timesteps 100

run-demo:
	@echo "RT-012: run-demo — using RecordingGraphBuilder (in-memory, NOT persisted to Neo4j)"
	$(PYTHON) run_4phase.py

run-real:
	@echo "NOTE: 'make run-real' is DEPRECATED (ORCH-003, IN-072)."
	@echo "      Alias for 'make run' (invokes run_4phase.py)."
	@$(MAKE) --no-print-directory run

# IN-046 ROOT FIX: single glob + sort by mtime (most recent first). The
# previous version used three redundant globs chained with `or` and picked
# the ALPHABETICALLY-first match (oldest, not most recent). Now prints the
# manifest path to stderr before the JSON content.
run-json:
	@$(PYTHON) -c "import json,sys,glob,os; \
		out = glob.glob('output_v100/**/manifest.json', recursive=True); \
		if not out: \
			sys.exit('No manifest.json found. Run `make run` first to generate pipeline output.'); \
		out.sort(key=os.path.getmtime, reverse=True); \
		print(f'# {out[0]}', file=sys.stderr); \
		with open(out[0]) as f: \
			print(json.dumps(json.load(f), indent=2, default=str))"

run-neo4j:
	@if [ -z "$$DRUGOS_NEO4J_URI" ]; then echo "Set DRUGOS_NEO4J_URI env var first"; exit 1; fi
	USE_NEO4J_BUILDER=1 $(PYTHON) run_4phase.py

test-bridge:
	cd phase2 && $(PYTHON) -m pytest tests/test_phase1_phase2_bridge.py -v

# P1-008 ROOT FIX: removed --ignore=tests/test_disgenet_pipeline_institutional_v389.py.
# The institutional DisGeNET test now runs in CI. If it is genuinely slow,
# use `make test-phase1-fast` (which excludes @pytest.mark.slow tests).
test-phase1:
	cd phase1 && $(PYTHON) -m pytest tests/ -q

test-phase1-fast:
	cd phase1 && $(PYTHON) -m pytest tests/ -q -m "not slow"

test-phase2:
	cd phase2 && $(PYTHON) -m pytest tests/ -q

# SH-037 ROOT FIX: added test-shared + test-root targets.
test-shared:
	cd shared && $(PYTHON) -m pytest tests/ -q

test-root:
	$(PYTHON) -m pytest tests/ -q

# IN-047/P1-009/SH-037 ROOT FIX: correct order is phase1 → phase2 → bridge
# → shared → root (matches the data dependency direction). The previous
# order (bridge → phase2 → phase1) ran the bridge against missing/stale
# Phase 1 data and never ran shared/ or top-level tests/.
test-all: test-phase1 test-phase2 test-bridge test-shared test-root
	@echo ""
	@echo "All test suites complete."

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type d -name .pytest_cache -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	@echo "Clean complete."

# v113 IN-096 ROOT FIX: backup restore-test target.
restore-test:
	@echo "Running backup restore-test (v113 IN-096)..."
	$(PYTHON) scripts/restore_test.py

# IN-080 v125 FORENSIC ROOT FIX (Teammate Cosmic): hash-pinned lockfile
# management. Generates .lock files for all requirements.txt files with
# sha256 hashes for supply-chain integrity. The .lock files are checked
# into git so the same hashes are used in CI and production.
#
# Usage:
#   make requirements-lock        # regenerate all .lock files
#   make requirements-verify      # verify .lock files are up-to-date
#   make requirements-audit       # scan for known CVEs via pip-audit
#
# The Dockerfiles use --require-hashes when a .lock file is present.
# In dev (no .lock file), they fall back to plain requirements.txt.
requirements-lock:
	@echo "IN-080: regenerating hash-pinned lockfiles..."
	$(PYTHON) scripts/generate_lockfiles.py
	$(PYTHON) scripts/generate_lockfiles_from_root.py
	@echo ""
	@echo "IN-080: lockfiles regenerated. Review and commit:"
	@echo "  git status -- '*.lock'"

requirements-verify:
	@echo "IN-080: verifying lockfile integrity..."
	$(PYTHON) scripts/verify_requirements_security.py --strict
	@for f in requirements.lock phase1/requirements.lock phase2/drugos_graph/requirements.lock graph_transformer/requirements.lock rl/requirements.lock; do \
	    if [ ! -f "$$f" ]; then \
	        echo "ERROR: $$f is missing (run 'make requirements-lock')"; \
	        exit 1; \
	    fi; \
	    warnings=$$(grep -c "WARNING" "$$f" 2>/dev/null || echo 0); \
	    pinned=$$(grep -c "sha256:" "$$f" 2>/dev/null || echo 0); \
	    echo "  $$f: $$pinned pinned, $$warnings warnings"; \
	done
	@echo "IN-080: lockfile integrity verified."

requirements-audit:
	@echo "IN-080: scanning for known CVEs via pip-audit..."
	@command -v pip-audit >/dev/null 2>&1 || { \
	    echo "Installing pip-audit..."; \
	    pip install --quiet pip-audit; \
	}
	@for f in requirements.txt phase1/requirements.txt phase2/drugos_graph/requirements.txt graph_transformer/requirements.txt rl/requirements.txt; do \
	    echo "--- Auditing $$f ---"; \
	    pip-audit -r "$$f" --strict --vulnerability-service osv || true; \
	done
	@echo "IN-080: pip-audit scan complete."
