# Unified Autonomous Drug Repurposing Platform — Makefile
# ========================================================
# Single entry point for all 4 phases:
#   Phase 1 (Data Ingestion) → Phase 2 (Knowledge Graph) →
#   Phase 3 (Graph Transformer) → Phase 4 (RL Hypothesis Ranker)

SHELL := /bin/bash
PYTHON ?= python3
PIP    ?= pip3

# R-030 root fix: added run-json run-neo4j run-4phase run-full-platform to .PHONY.
# R-019 root fix: run-pipeline target removed (file renamed to run_4phase.py).
# RT-012 root fix: added run-demo for explicit in-memory CI / demo runs.
.PHONY: help install test test-phase1 test-phase2 test-bridge test-all run run-full-platform run-unified run-4phase run-real run-demo dry-run run-json run-neo4j clean

help:
	@echo "Unified Autonomous Drug Repurposing Platform"
	@echo "============================================"
	@echo ""
	@echo "Setup:"
	@echo "  make install         Install Python deps (top-level requirements.txt)"
	@echo ""
	@echo "Run (all 4 phases):"
	@echo "  make run             Full 4-phase run (Phase 1+2+3+4) — DEFAULT"
	@echo "                       (invokes run_4phase.py — the CANONICAL runner per ORCH-003)"
	@echo "  make run-4phase      Explicit alias for make run"
	@echo "  make run-full-platform  DEPRECATED (ORCH-003) — emits warning, same as make run"
	@echo "  make dry-run         Same as make run (alias)"
	@echo ""
	@echo "Run (partial):"
	@echo "  make run-unified     Phase 1+2 (+3+4 via --run-gt-rl flag per ORCH-002)"
	@echo "  make run-real        DEPRECATED (ORCH-003) — emits warning, use make run"
	@echo ""
	@echo "Test:"
	@echo "  make test-all        Run ALL tests across both phases + bridge"
	@echo "  make test-bridge     Run ONLY the Phase1<->Phase2 integration tests"
	@echo "  make test-phase1     Run Phase 1 tests only"
	@echo "  make test-phase2     Run Phase 2 tests only"
	@echo ""
	@echo "Cleanup:"
	@echo "  make clean           Remove __pycache__ and .pytest_cache"

install:
	# v100 ROOT FIX (R-017): install ONLY the top-level requirements.txt,
	# which already merges Phase 1, Phase 2, Phase 3, and Phase 4 dependencies.
	# The sub-requirements files (phase1/, phase2/, graph_transformer/, rl/)
	# are kept for backwards compatibility (cd phase1 && pip install -r
	# requirements.txt) but the Makefile entry point uses the single merged
	# file so there is no version-pinning conflict.
	$(PIP) install -r requirements.txt
	@echo ""
	@echo "Dependencies installed. Run 'make run' for the full 4-phase pipeline."

# v100 ROOT FIX (R-016) + ORCH-003 ROOT FIX (v2): the DEFAULT run
# target now invokes run_4phase.py — the CANONICAL 4-phase runner per
# ORCH-003 (Phase 1 -> 2 -> 3 -> 4 on REAL biomedical data). The previous
# default invoked run_full_platform.py — a DUPLICATE runner with a
# different adapter path and different defaults, which caused
# "works in CI, breaks in prod" situations (ORCH-003 root cause).
# run_full_platform.py and run_real_pipeline.py are now DEPRECATED and
# emit a stderr warning on every invocation; run_4phase.py is the single
# source of truth.
dry-run: run

run: run-4phase

# ORCH-003 ROOT FIX (v2): run-full-platform is kept for backward
# compatibility but emits a deprecation warning. Switch to `make run`
# (which calls run_4phase.py).
run-full-platform:
	$(PYTHON) run_full_platform.py

run-unified:
	$(PYTHON) run_unified.py

# R-019 root fix: run_pipeline.py was renamed to run_4phase.py to avoid
# the two-files-same-name collision with phase2/drugos_graph/run_pipeline.py.
#
# RT-012 ROOT FIX (Team Member 17): `make run` previously invoked
# run_4phase.py, which called run_phase1_to_phase2 with
# prefer_postgres=False and NO builder — so the bridge used the
# RecordingGraphBuilder (in-memory) by default. The KG was never
# persisted to Neo4j. An engineer running `make run` believed they
# built a KG; in fact, they built nothing persistent.
#
# Root fix: when DRUGOS_NEO4J_URI is set, `make run` exports
# USE_NEO4J_BUILDER=1 so run_4phase.py uses the real
# DrugOSGraphBuilder (persists to Neo4j). When the env var is unset
# (dev/CI), it falls back to RecordingGraphBuilder and prints a
# clear warning so the engineer knows the KG is not persisted.
# The new `make run-demo` target explicitly uses the in-memory
# builder for quick demos / CI.
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

# RT-012 ROOT FIX: explicit in-memory demo target. Use this for CI / quick
# demos where Neo4j persistence is not required.
run-demo:
	@echo "RT-012: run-demo — using RecordingGraphBuilder (in-memory, NOT persisted to Neo4j)"
	$(PYTHON) run_4phase.py

run-real:
	$(PYTHON) run_real_pipeline.py

run-json:
	$(PYTHON) run_unified.py --json

run-neo4j:
	@if [ -z "$$DRUGOS_NEO4J_URI" ]; then echo "Set DRUGOS_NEO4J_URI env var first"; exit 1; fi
	USE_NEO4J_BUILDER=1 $(PYTHON) run_4phase.py

test-bridge:
	cd phase2 && $(PYTHON) -m pytest tests/test_phase1_phase2_bridge.py -v

test-phase1:
	cd phase1 && $(PYTHON) -m pytest tests/ -q --ignore=tests/test_disgenet_pipeline_institutional_v389.py

test-phase2:
	cd phase2 && $(PYTHON) -m pytest tests/ -q

test-all: test-bridge test-phase2 test-phase1
	@echo ""
	@echo "All test suites complete."

clean:
	# v28 ROOT FIX (audit TOP-24): use `find -exec rm -rf` WITHOUT
	# `2>/dev/null || true` suppression — real errors (permission,
	# disk-full) surface to the operator. `-type d`/`-type f` skip
	# symlinks automatically (v75 T-034: trimmed 18-line comment).
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type d -name .pytest_cache -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	@echo "Clean complete."
