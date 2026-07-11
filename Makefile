# Unified Autonomous Drug Repurposing Platform — Makefile
# ========================================================
# V100 ROOT FIX (BUG #15, P0 CRITICAL): the previous default `make run`
# target invoked `run_unified.py` which ONLY runs Phase 1 + Phase 2 and
# NEVER invokes the Graph Transformer (Phase 3) or RL Ranker (Phase 4).
# The default Makefile entry point produced ZERO repurposing candidates.
# Root fix: `make run` now invokes `run_full_platform.py` — the ONLY
# runner that wires all 4 phases (Phase 1 data → Phase 2 KG → Phase 3 GT
# → Phase 4 RL → candidates CSV). The old `run-unified` target is kept
# as an alias for backward compatibility.

PYTHON ?= python3
PIP    ?= pip3

.PHONY: help install test test-phase1 test-phase2 test-bridge test-all run run-full-platform run-unified dry-run clean

help:
	@echo "Unified Autonomous Drug Repurposing Platform"
	@echo "============================================"
	@echo ""
	@echo "Setup:"
	@echo "  make install         Install Python deps for both phases"
	@echo ""
	@echo "Run:"
	@echo "  make run             Run ALL 4 phases (Phase 1+2+3+4) → candidates CSV"
	@echo "  make run-full-platform  Same as make run (explicit name)"
	@echo "  make run-unified     Legacy: Phase 1 + Phase 2 only (NO candidates)"
	@echo "  make dry-run         Same as run-unified (legacy alias)"
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
	$(PIP) install -r requirements.txt
	$(PIP) install -r phase1/requirements.txt
	$(PIP) install -r phase2/drugos_graph/requirements.txt
	@echo ""
	@echo "Dependencies installed. Run 'make run' to run all 4 phases."

# V100 BUG #15: `make run` now runs ALL 4 phases via run_full_platform.py.
run: run-full-platform

run-full-platform:
	$(PYTHON) run_full_platform.py

# Legacy target — Phase 1 + Phase 2 only (produces NO candidates).
run-unified:
	$(PYTHON) run_unified.py

dry-run: run-unified

run-json:
	$(PYTHON) run_full_platform.py --json

run-neo4j:
	@if [ -z "$$DRUGOS_NEO4J_URI" ]; then echo "Set DRUGOS_NEO4J_URI env var first"; exit 1; fi
	$(PYTHON) run_full_platform.py --neo4j-uri $$DRUGOS_NEO4J_URI --neo4j-user $$DRUGOS_NEO4J_USER --neo4j-password $$DRUGOS_NEO4J_PASSWORD

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
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type d -name .pytest_cache -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	@echo "Clean complete."
