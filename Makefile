# Unified Autonomous Drug Repurposing Platform — Makefile
# ========================================================
# Single entry point for all 4 phases:
#   Phase 1 (Data Ingestion) → Phase 2 (Knowledge Graph) →
#   Phase 3 (Graph Transformer) → Phase 4 (RL Hypothesis Ranker)
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

.PHONY: help install test test-phase1 test-phase2 test-bridge test-shared test-root test-all test-phase1-fast run run-full-platform run-unified run-4phase run-real run-demo dry-run run-json run-neo4j clean restore-test

help:
	@echo "Unified Autonomous Drug Repurposing Platform"
	@echo "============================================"
	@echo ""
	@echo "Setup:"
	@echo "  make install         Install Python deps (top-level requirements.txt)"
	@echo ""
	@echo "Run (all 4 phases):"
	@echo "  make run             Full 4-phase run (Phase 1+2+3+4) — DEFAULT"
	@echo "  make run-4phase      Explicit alias for make run"
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
	@if [ -n "$$DRUGOS_NEO4J_URI" ]; then \
		echo "RT-012: DRUGOS_NEO4J_URI is set — using DrugOSGraphBuilder (persists to Neo4j)"; \
		USE_NEO4J_BUILDER=1 $(PYTHON) run_4phase.py; \
	else \
		echo "RT-012: DRUGOS_NEO4J_URI not set — using RecordingGraphBuilder (in-memory, NOT persisted)"; \
		echo "  To persist the KG to Neo4j: export DRUGOS_NEO4J_URI=bolt://localhost:7687"; \
		echo "  and DRUGOS_NEO4J_USER / DRUGOS_NEO4J_PASSWORD, then re-run 'make run'."; \
		$(PYTHON) run_4phase.py; \
	fi

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
