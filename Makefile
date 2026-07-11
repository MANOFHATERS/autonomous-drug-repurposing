# Unified Autonomous Drug Repurposing Platform — Makefile
# ========================================================
# Single entry point for all 4 phases:
#   Phase 1 (Data Ingestion) → Phase 2 (Knowledge Graph) →
#   Phase 3 (Graph Transformer) → Phase 4 (RL Hypothesis Ranker)

PYTHON ?= python3
PIP    ?= pip3

.PHONY: help install test test-phase1 test-phase2 test-bridge test-all run run-full-platform run-unified run-pipeline run-real dry-run clean

help:
	@echo "Unified Autonomous Drug Repurposing Platform"
	@echo "============================================"
	@echo ""
	@echo "Setup:"
	@echo "  make install         Install Python deps (top-level requirements.txt)"
	@echo ""
	@echo "Run (all 4 phases):"
	@echo "  make run             Full 4-phase run (Phase 1+2+3+4) — DEFAULT"
	@echo "  make run-full-platform  Same as make run (explicit name)"
	@echo "  make dry-run         Same as make run (alias)"
	@echo ""
	@echo "Run (partial):"
	@echo "  make run-unified     Phase 1+2 (+3+4 via --full-pipeline flag)"
	@echo "  make run-pipeline    v90 4-phase pipeline runner"
	@echo "  make run-real        Real data pipeline (Phase 1+2+3+4 on real KG)"
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
	# which already merges Phase 1 and Phase 2 dependencies. The previous
	# Makefile installed three requirements files (top-level + phase1/ +
	# phase2/), which pinned DIFFERENT versions of the same packages —
	# the last-installed won, silently downgrading previously-installed
	# packages. The sub-requirements files are kept for backwards
	# compatibility (cd phase1 && pip install -r requirements.txt) but
	# the Makefile entry point uses the single merged file.
	$(PIP) install -r requirements.txt
	@echo ""
	@echo "Dependencies installed. Run 'make run' for the full 4-phase pipeline."

# v100 ROOT FIX (R-016): the DEFAULT run target now invokes
# run_full_platform.py (the REAL 4-phase runner: Phase 1 → 2 → 3 → 4
# on REAL biomedical data). The previous default invoked run_unified.py
# which only ran Phase 1+2 (BUG R-007) — Phase 3 (Graph Transformer)
# and Phase 4 (RL ranker) were NEVER invoked by the default entry point.
dry-run: run

run: run-full-platform

run-full-platform:
	$(PYTHON) run_full_platform.py

run-unified:
	$(PYTHON) run_unified.py

run-pipeline:
	$(PYTHON) run_pipeline.py

run-real:
	$(PYTHON) run_real_pipeline.py

run-json:
	$(PYTHON) run_unified.py --json

run-neo4j:
	@if [ -z "$$DRUGOS_NEO4J_URI" ]; then echo "Set DRUGOS_NEO4J_URI env var first"; exit 1; fi
	$(PYTHON) run_unified.py --neo4j-uri $$DRUGOS_NEO4J_URI --neo4j-user $$DRUGOS_NEO4J_USER --neo4j-password $$DRUGOS_NEO4J_PASSWORD

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
