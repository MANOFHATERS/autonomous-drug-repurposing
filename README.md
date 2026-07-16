# Autonomous Drug Repurposing Platform

> **Team Cosmic · VentureLab** — Manoj · Rohan · Aseem
>
> A pure machine-learning system that systematically mines all 10,000
> FDA-approved drugs against every known disease. Rather than spending
> 12 years and $2.6 billion developing a new molecule, this platform
> finds hidden therapeutic value in drugs that already passed safety
> trials.

[![CI](https://github.com/MANOFHATERS/autonomous-drug-repurposing/actions/workflows/ci.yml/badge.svg)](.github/workflows/ci.yml)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-proprietary-red.svg)](LICENSE)

---

## 📋 Project Status

This README is the **canonical entry point** for the repository.
For the full project specification (architecture, phases, build
process, team responsibilities, risks), see
[`README_V31.md`](README_V31.md) (kept for historical reference —
the v31 build process documentation).

## 🏗 Architecture at a Glance

The platform is built as **four sequential phases** that feed each
other:

| Phase | Timeline | Focus | Deliverable |
|-------|----------|-------|-------------|
| **Phase 1** | Weeks 1–2 | Data Ingestion | ChEMBL, DrugBank, UniProt, STRING, DisGeNET, OMIM, PubChem pipelines live; cleaned datasets |
| **Phase 2** | Weeks 2–3 | Knowledge Graph | Neo4j graph with 10K drugs, proteins, pathways, diseases, clinical outcomes |
| **Phase 3** | Weeks 3–5 | Graph Transformer | Trained GNN model with drug-disease interaction scores |
| **Phase 4** | Weeks 5–6 | RL Agent | RL agent ranking hypotheses by plausibility, safety, market opportunity |
| **Phase 5+6** | Week 7 | API, Dashboard, Launch | REST API + interactive web dashboard; validated V1 platform |

The four phases are **100% connected** through a single authoritative
data wire: Phase 1 produces cleaned CSVs → Phase 2's `phase1_bridge`
consumes them and builds the KG → Phase 3's GT model trains on the KG
→ Phase 4's RL ranker consumes the GT predictions.

## 🚀 Quickstart

### Prerequisites

- Python 3.12+
- Docker + Docker Compose (for full-stack local dev)
- Neo4j 5.x (optional, for KG persistence; the RecordingGraphBuilder
  works in-memory without it)

### Install

```bash
# Clone the repo
git clone https://github.com/MANOFHATERS/autonomous-drug-repurposing.git
cd autonomous-drug-repurposing

# Install Python dependencies
make install   # or: pip install -r requirements.txt
```

### Run the full 4-phase pipeline

```bash
# DEFAULT: in-memory RecordingGraphBuilder (no Neo4j required)
make run

# With Neo4j persistence:
export DRUGOS_NEO4J_URI=bolt://localhost:7687
export DRUGOS_NEO4J_USER=neo4j
export DRUGOS_NEO4J_PASSWORD=your_password
make run-neo4j

# Demo mode (small embedded sample data, no downloads)
make run-demo
```

The canonical runner is **`run_4phase.py`** (per ORCH-003). The
deprecated targets `make run-full-platform`, `make run-unified`, and
`make run-real` are kept as aliases for backward compat (v113 IN-072).

### Run tests

```bash
# Default: skips network/gpu/slow tests (per v113 IN-055)
make test-all

# Phase-specific:
make test-phase1
make test-phase2
make test-bridge   # Phase 1 ↔ Phase 2 integration tests

# Run EVERYTHING (including network/slow/gpu tests):
pytest --override-ini="addopts=" -m "network or slow or gpu"
```

## 🔬 Phase 1 — Data Ingestion

Seven free public biomedical databases are downloaded, cleaned, and
loaded into a staging PostgreSQL database:

1. **ChEMBL** — 2M+ chemical compounds with biological activity data
2. **DrugBank** — Detailed profiles of FDA-approved drugs
3. **UniProt** — Protein sequences and functions
4. **STRING** — Protein-protein interaction networks
5. **DisGeNET** — Curated gene-disease associations
6. **OMIM** — Genetic basis of diseases
7. **PubChem** — Structural and property data for chemical compounds

**Note (v113 P1-024):** DrugBank academic downloads are paused since
May 2026. In FULL mode, the pipeline raises `RuntimeError` unless
`DRUGOS_ALLOW_NO_DRUGBANK=1` is set, forcing operators to explicitly
acknowledge the ChEMBL-only degraded mode. This is a patient-safety
guard — without it, the RL ranker's withdrawn-drug safety filter would
see NULL for every drug, and a withdrawn drug like thalidomide could
be recommended as a repurposing candidate.

## 🧠 Phase 2 — Knowledge Graph

A Neo4j graph with five node types (Compound, Protein, Pathway,
Disease, ClinicalOutcome) and 30+ edge types. The `phase1_bridge`
module is the single authoritative wire from Phase 1 to Phase 2 —
it reads Phase 1's cleaned CSVs and emits node/edge dicts for the
KG builder.

**Phase 1 ↔ Phase 2 connectivity (v113):**
- All 7 Phase 1 sources + SIDER adverse events are wired through the
  bridge's `paths` dict (v113 P2-047 root fix).
- ClinicalOutcome node IDs are deterministic across runs:
  `CO:{disease_key}:{indication_type}` (v113 P2-046/048 root fix).
- Withdrawn drugs get 0.0 confidence on `treats` edges (v113 P2-050
  root fix — patient-safety guard).
- Legacy `causes_side_effect` edge type is REMOVED from the whitelist
  (v113 P2-049) — all SIDER edges must use the canonical
  `causes_adverse_event` form.

## 🎯 Phase 3 — Graph Transformer

A PyTorch + PyTorch Geometric Graph Transformer that reads the KG and
predicts drug-disease interaction scores. Trained on known
drug-disease pairs (positives) and random pairs (negatives), with
graph-level cross-validation splits to prevent data leakage.

The GT service is exposed via FastAPI at `scripts/gt_api.py`:
- `POST /predict` — predict scores for explicit drug-disease pairs
- `POST /top-k` — rank top-K novel drug-disease pairs
- `GET /healthz` — Docker healthcheck

**Note (v113 IN-038/039):** The GT API uses the modern `lifespan`
context manager (no deprecated `@app.on_event`). CORS is hardened:
no credentials, explicit header list, wildcard origins rejected.

## 🤖 Phase 4 — RL Hypothesis Ranker

A Stable-Baselines3 PPO agent that ranks the GT's top predictions by
three dimensions:
1. **Scientific plausibility** — graph evidence strength
2. **Safety signal** — adverse event history (from SIDER via Phase 2)
3. **Market opportunity** — disease under-served, drug on/off patent

The data flywheel: validated hypotheses from pharma partners are
written back to `validated_hypotheses.csv`, which feeds the RL
ranker's reward bonus (DOCX §10).

## 🛡 Production Hardening (v113)

This codebase is **institutional-grade production-ready**, not a
college project. Key production guards:

- **Patient safety:** withdrawn drugs get 0.0 treats confidence;
  toxic validated pairs are excluded from the RL reward bonus.
- **Scientific correctness:** ClinicalOutcome IDs are deterministic;
  legacy edge types are dead-lettered; SIDER edges are canonical.
- **Security:** CORS hardened; ownership guards fail CLOSED; no
  wildcard origins.
- **Reproducibility:** per-instance RNG (no global seed mutation);
  markers enforced in pytest; no production file mutation in tests.
- **Audit trail:** tamper-evident JSONL audit logs for every KG
  mutation; lineage properties on every node/edge.

## 📚 Documentation

- [Full build process & architecture](README_V31.md) — Team Cosmic
  build spec (phases, technology stack, team responsibilities, risks)
- [Forensic audit fix summaries](V113_ROOT_FIX_SUMMARY.md) — v113
  root-cause fix log (this release)
- [Phase 1 ↔ Phase 2 bridge](phase2/drugos_graph/phase1_bridge.py) —
  the single authoritative wire from cleaned data to KG
- [Graph Transformer service](scripts/gt_api.py) — FastAPI inference
  service
- [RL ranker](rl/rl_drug_ranker.py) — PPO agent + reward shaping

## 🧪 Testing

```bash
# Default (skips network/gpu/slow):
pytest

# Forensic root-cause verification (v113):
pytest tests/forensic_root_v113/

# Phase 1 ↔ Phase 2 bridge:
pytest phase2/tests/test_phase1_phase2_bridge.py

# Full suite (includes network tests):
pytest --override-ini="addopts=" -m "network or slow or gpu"
```

## 🔧 Configuration

Environment variables (see `.env.example` for the full list):

| Variable | Default | Description |
|----------|---------|-------------|
| `DRUGOS_DOWNLOAD_MODE` | `sample` | `sample` / `full` / `skip` |
| `DRUGOS_ALLOW_NO_DRUGBANK` | `0` | Set to `1` to allow ChEMBL-only degraded mode (v113 P1-024) |
| `DRUGOS_NEO4J_URI` | (empty) | Neo4j bolt URI; empty = in-memory KG |
| `GT_CORS_ORIGINS` | `http://localhost:3000` | Comma-separated origins; `*` is REJECTED (v113 IN-039) |
| `VALIDATED_HYPOTHESES_CSV` | (auto) | Override path to validated hypotheses CSV |
| `DRUGOS_ENVIRONMENT` | `production` | `production` / `development` (controls escape hatches) |

## 📄 License

Proprietary — Team Cosmic / VentureLab. All rights reserved.

---

**Team Cosmic** · Manoj · Rohan · Aseem
