# Unified Autonomous Drug Repurposing Platform — Makefile
# ========================================================
# Single entry point for both Phase 1 (data ingestion) and Phase 2 (knowledge graph).
# The bridge between the two phases lives in phase2/drugos_graph/phase1_bridge.py.

SHELL := /bin/bash

PYTHON ?= python3
PIP    ?= pip3

.PHONY: help install test test-phase1 test-phase2 test-bridge test-all run dry-run clean

help:
        @echo "Unified Autonomous Drug Repurposing Platform"
        @echo "============================================"
        @echo ""
        @echo "Setup:"
        @echo "  make install         Install Python deps for both phases"
        @echo ""
        @echo "Run:"
        @echo "  make dry-run         Phase1 → Bridge → Phase2 (in-memory, no Neo4j)"
        @echo "  make run             Same as dry-run (alias)"
        @echo ""
        @echo "Test:"
        @echo "  make test-all        Run ALL tests across both phases + bridge"
        @echo "  make test-bridge     Run ONLY the Phase1↔Phase2 integration tests"
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
        @echo "Dependencies installed. Run 'make dry-run' to test the unified pipeline."

dry-run: run

run:
        $(PYTHON) run_unified.py

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
