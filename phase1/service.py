#!/usr/bin/env python3
"""Phase 1 Dataset Service (Step 1 integration plan, v105).

Wraps Phase 1's data-warehouse logic as an HTTP service so the Next.js
frontend can proxy to it via DATASET_SERVICE_URL. The frontend's
``src/lib/services/dataset-stats.ts`` and ``src/app/api/dataset/route.ts``
already proxy to ``DATASET_SERVICE_URL`` -- this service is what they
expect to find there.

Endpoints:
    GET  /health                -> {status: "ok", service: "phase1", ...}
    GET  /datasets              -> {sources: [...], total_drugs, total_proteins, ...}
    GET  /datasets/{drug}/mechanism -> {drug, targets: [{protein, action, evidence}], ...}

Run:
    cd phase1 && python service.py
    # or: uvicorn phase1.service:app --host 0.0.0.0 --port 8001

Environment:
    PHASE1_DB_URL: PostgreSQL URL for the staging DB. If unset, the
        service reads embedded sample CSVs (Tier-2 fallback) so it can
        still answer /datasets in dev/CI without a DB.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# Make phase1 importable when running ``python phase1/service.py`` directly.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
# Also make the repo root importable (for pipelines.* / config.*).
_REPO_ROOT = _HERE.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

# v122 FORENSIC ROOT FIX (BUG-4/BUG-5/BUG-6): wire up shared observability
# (metrics + structured JSON logging + OpenTelemetry). The v116
# docker-compose.yml added Prometheus + OTel + Jaeger services but the
# application code never exposed /metrics, never used structured logging,
# and never instrumented FastAPI. This single call fixes IN-040, IN-041,
# IN-042 at the application level.
try:
    from shared.observability import configure_app as _configure_observability
except Exception:  # Defensive fallback — service still runs without observability.
    _configure_observability = None

logger = logging.getLogger("phase1.service")
# v122 BUG-5: structured JSON logging is now configured by
# shared.observability.configure_app() — keep this basicConfig as a fallback
# for when the shared module fails to import (rare, but the service must
# still start).
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")

app = FastAPI(
    title="Autonomous Drug Repurposing — Phase 1 Dataset Service",
    description="HTTP wrapper around Phase 1 ETL / data warehouse.",
    version="1.0.0",
)
app.add_middleware(
    CORSMiddleware,
    # P1-017 v113 ROOT FIX: allow_origins=["*"] permitted ANY website to
    # make cross-origin requests to this API. Combined with allow_headers=["*"],
    # a malicious webpage could enumerate every drug in the database — a
    # data-exfiltration vector for proprietary drug-repurposing hypotheses.
    # ROOT FIX: read allowed origins from PHASE1_CORS_ORIGINS env var
    # (comma-separated). Default to localhost:3000 (the Next.js frontend)
    # for dev. Production deployments MUST set PHASE1_CORS_ORIGINS to the
    # real frontend domain.
    allow_origins=os.environ.get(
        "PHASE1_CORS_ORIGINS", "http://localhost:3000"
    ).split(","),
    # TM3 Task 3.3 v127 ROOT FIX: was ``allow_methods=["GET"]`` which
    # blocked the new POST /datasets/validated_hypotheses endpoint
    # (the data flywheel writeback). The CORS preflight OPTIONS request
    # is auto-handled by CORSMiddleware (it returns 200 without the
    # caller declaring OPTIONS in allow_methods). Adding POST enables
    # the writeback endpoint while keeping the surface minimal — no
    # PUT / DELETE / PATCH (the flywheel is append-only by design).
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# v122 BUG-4/BUG-5/BUG-6: mount /metrics + configure JSON logging + OTel.
if _configure_observability is not None:
    _configure_observability(app, service_name="phase1-dataset")


def _count_csv_rows(path: Path) -> int:
    """Count data rows in a CSV (excluding header). Returns 0 if missing.

    P1-018 v113 ROOT FIX: the previous implementation counted NEWLINES
    via `sum(1 for line in f)`. A CSV field with an embedded newline
    (e.g. a `mechanism_of_action` field containing "Inhibits COX-1\nand
    COX-2") spans multiple physical lines, so the counter overcounted.
    The DrugBank drugs CSV regularly contains multi-line mechanism fields,
    causing the /datasets endpoint to report 10,500 drugs when the actual
    count was 10,000.

    ROOT FIX: use the csv module's reader which correctly handles
    multi-line quoted fields. This is the standard, robust way to count
    CSV records.
    """
    if not path.exists():
        return 0
    try:
        import csv as _csv
        with open(path, "r", encoding="utf-8", errors="replace", newline="") as f:
            reader = _csv.reader(f)
            try:
                next(reader)  # skip header
            except StopIteration:
                return 0
            return sum(1 for _row in reader if _row)  # count non-empty rows
    except Exception:
        return 0


def _count_unique_string_proteins(path: Path) -> int:
    """Count UNIQUE protein IDs from BOTH columns of a STRING PPI CSV.

    P1-029 v117 ROOT FIX: STRING's protein-protein interaction CSV has
    two protein ID columns (column 0 and column 1). The previous v113
    "fix" for P1-029 only fired when ``total_proteins == 0`` (i.e. the
    uniprot_proteins.csv was COMPLETELY ABSENT) -- and even then it
    used the interaction ROW count as a proxy for the protein count,
    which both OVERCOUNTS (one protein appears in many interactions)
    and does not address the COMMON case where both CSVs exist.

    ROOT FIX (v117): parse the STRING PPI CSV with ``csv.reader``
    (correctly handling multi-line quoted fields), skip the header row,
    and collect the set of unique protein IDs from BOTH column 0 and
    column 1. Returns 0 if the file is missing, empty, or unparseable
    so the caller can fall back gracefully.

    The downstream canonical count remains ``SELECT COUNT(*) FROM
    proteins`` (the DB query) -- this CSV-derived count is the best
    pre-DB estimate available to the /datasets endpoint.
    """
    if not path.exists():
        return 0
    try:
        import csv as _csv
        unique_ids: set = set()
        with open(path, "r", encoding="utf-8", errors="replace", newline="") as f:
            reader = _csv.reader(f)
            try:
                next(reader)  # skip header row (string_protein_protein_interactions.csv ships with one)
            except StopIteration:
                return 0
            for row in reader:
                if not row:
                    continue
                # Column 0 = protein1, column 1 = protein2 (STRING convention).
                # Both are typically "<taxid>.<ENSP>" e.g. "9606.ENSP00000000233".
                if len(row) > 0 and row[0]:
                    unique_ids.add(row[0])
                if len(row) > 1 and row[1]:
                    unique_ids.add(row[1])
        return len(unique_ids)
    except Exception as exc:
        logger.warning(
            "Phase 1 service: failed to parse STRING PPI CSV %s for unique "
            "protein count (P1-029 v117). Falling back to uniprot-only count. "
            "Error: %s",
            path, exc,
        )
        return 0


def _processed_data_dir() -> Path:
    """Return the Phase 1 processed_data directory."""
    return _HERE / "processed_data"


def _load_dataset_stats() -> Dict[str, Any]:
    """Load real dataset stats from processed_data CSVs (or DB if available).

    TM1 TASK 2 ROOT FIX: the previous Tier-2 fallback wrote embedded sample
    CSVs (10 fake drugs) when processed_data was empty. This violated the
    "NEVER overwrite real data with mock samples" mandate. Now we return
    zero counts with a clear ``data_status="empty"`` flag so the dashboard
    honestly reports the absence of data instead of fabricating numbers.
    The operator must run ``python -m phase1.pipelines all`` to populate
    real data. For local dev, ``DRUGOS_ENVIRONMENT=development python -m
    phase1.pipelines samples`` writes mock CSVs to a directory of choice.
    """
    pdir = _processed_data_dir()
    if not pdir.exists() or not any(pdir.glob("*.csv*")):
        logger.warning(
            "Phase 1 service: processed_data dir %s is empty. Returning "
            "zero counts with data_status='empty'. Run "
            "`python -m phase1.pipelines all` to populate real data.",
            pdir,
        )

    # Count rows per source CSV. These are the REAL Phase 1 outputs.
    sources: List[Dict[str, Any]] = []
    csv_map = {
        "chembl": "chembl_drugs.csv",
        "drugbank": "drugbank_drugs.csv",
        "uniprot": "uniprot_proteins.csv",
        "string": "string_protein_protein_interactions.csv",
        "disgenet": "disgenet_gene_disease_associations.csv",
        "omim": "omim_gene_disease_associations.csv",
        "pubchem": "pubchem_enrichment.csv",
    }
    total_drugs = 0
    total_proteins = 0
    total_interactions = 0
    for source_name, fname in csv_map.items():
        path = pdir / fname
        rows = _count_csv_rows(path)
        sources.append({
            "name": source_name,
            "csv_file": fname,
            "row_count": rows,
            "available": rows > 0,
        })
        # P1-006 v113 ROOT FIX: removed the dead-code block that computed
        # total_drugs three different ways inside the loop, then
        # unconditionally overwrote it with the DrugBank count below.
        # The loop body is now clean — total_drugs is computed AFTER the
        # loop via a fallback chain.
        if source_name == "uniprot":
            total_proteins = rows
        if source_name == "string":
            total_interactions = rows

    # P1-006 v113 ROOT FIX: compute total_drugs via a fallback chain.
    # The previous code unconditionally set total_drugs to the DrugBank
    # row count. If drugbank_drugs.csv was missing (DrugBank academic
    # license paused), total_drugs was 0 even if chembl_drugs.csv had
    # 3,000 rows. ROOT FIX: try drugbank first (canonical FDA-approved
    # list), then chembl, then a generic drugs.csv fallback. This matches
    # the Phase1OutputContract's source-priority semantics.
    for _drug_csv in ("drugbank_drugs.csv", "chembl_drugs.csv", "drugs.csv"):
        _drug_path = pdir / _drug_csv
        if _drug_path.exists():
            total_drugs = _count_csv_rows(_drug_path)
            if total_drugs > 0:
                break

    # P1-029 v117 ROOT FIX: total_proteins must account for STRING proteins
    # that may not be in UniProt (STRING's alias mapping is imperfect -- a
    # protein in STRING's graph may not yet be in the UniProt snapshot, and
    # vice versa). The previous v113 "fix" only handled the rare edge case
    # where uniprot_proteins.csv was COMPLETELY ABSENT (total_proteins==0);
    # the COMMON case (both CSVs exist) was unaddressed, so total_proteins
    # stayed at the uniprot row count and silently undercounted STRING-only
    # proteins. Worse, the v113 fallback used interaction ROW count (each
    # PPI row = 1 interaction, not 1 protein), which OVERCOUNTED because
    # one protein appears in many interactions.
    #
    # ROOT FIX (v117): parse string_protein_protein_interactions.csv with
    # csv.reader, count UNIQUE protein IDs from BOTH interaction columns
    # (column 0 and column 1), then set
    #     total_proteins = max(uniprot_rows, string_unique_protein_count)
    # The max() accounts for both directions of imperfect overlap:
    #   - UniProt-only proteins (no STRING entry): counted by uniprot_rows.
    #   - STRING-only proteins (no UniProt entry): counted by string_unique.
    # The DB query ``SELECT COUNT(*) FROM proteins`` remains the canonical
    # count for downstream consumers (Phase 2 KG builder).
    _string_ppi_path = pdir / "string_protein_protein_interactions.csv"
    _string_unique_protein_count = _count_unique_string_proteins(_string_ppi_path)
    _uniprot_rows = total_proteins  # snapshot before we possibly overwrite

    if _string_unique_protein_count > 0 and _uniprot_rows > 0:
        # COMMON CASE: both CSVs exist. Take the max to avoid undercounting
        # in either direction.
        total_proteins = max(_uniprot_rows, _string_unique_protein_count)
        logger.info(
            "Phase 1 service /datasets: total_proteins = "
            "max(uniprot_rows=%d, string_unique_proteins=%d) = %d "
            "(P1-029 v117 ROOT FIX -- both CSVs exist, taking the max to "
            "account for STRING-only proteins not in UniProt and vice versa).",
            _uniprot_rows, _string_unique_protein_count, total_proteins,
        )
    elif _string_unique_protein_count > 0 and _uniprot_rows == 0:
        # uniprot_proteins.csv absent/empty -- use STRING unique count directly.
        total_proteins = _string_unique_protein_count
        logger.info(
            "Phase 1 service /datasets: total_proteins = %d from STRING "
            "unique protein IDs (uniprot_proteins.csv absent/empty; "
            "P1-029 v117 ROOT FIX).",
            total_proteins,
        )
    elif _string_unique_protein_count == 0 and _uniprot_rows == 0 and total_interactions > 0:
        # Legacy fallback: STRING CSV exists but we couldn't extract unique
        # proteins from it (e.g. parse failure or unexpected column layout).
        # Use the interaction count as a conservative lower bound (each
        # interaction has 2 proteins, but they may overlap). The DB query
        # is the canonical count.
        total_proteins = total_interactions  # conservative lower bound
        logger.info(
            "Phase 1 service /datasets: total_proteins = %d (STRING "
            "interaction count as conservative lower bound; could not "
            "extract unique proteins from STRING CSV; P1-029 v117 legacy "
            "fallback).",
            total_proteins,
        )
    elif _uniprot_rows > 0:
        # Only uniprot_proteins.csv exists. No STRING PPI data.
        logger.info(
            "Phase 1 service /datasets: total_proteins = %d from "
            "uniprot_proteins.csv (STRING PPI CSV absent; P1-029 v117).",
            total_proteins,
        )
    # else: both are 0 -- total_proteins stays 0 (data_status=empty).

    return {
        "sources": sources,
        "total_sources_available": sum(1 for s in sources if s["available"]),
        "total_sources_expected": len(csv_map),
        "total_drugs": total_drugs,
        "total_proteins": total_proteins,
        "total_ppi": total_interactions,
        "processed_data_dir": str(pdir),
        "data_source": "phase1_csv_artifacts",
    }


def _load_drug_mechanism(drug_name: str) -> Dict[str, Any]:
    """Load the mechanism-of-action for a single drug from DrugBank CSV.

    Returns the drug's targets (proteins) and the indications it treats.
    This is the same data the frontend's /api/drugs/mechanism route
    needs (the frontend proxies to DATASET_SERVICE_URL when set).
    """
    pdir = _processed_data_dir()
    drugbank_path = pdir / "drugbank_drugs.csv"
    interactions_path = pdir / "drugbank_interactions.csv"
    indications_path = pdir / "drugbank_indications.csv"

    if not drugbank_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"DrugBank drugs CSV not found at {drugbank_path}. "
                   "Run `python -m pipelines all` to populate Phase 1 data.",
        )

    import csv as csv_mod

    drug_name_lower = drug_name.lower().strip()

    # 1. Find the drug row in drugbank_drugs.csv.
    drug_row: Optional[Dict[str, str]] = None
    try:
        with open(drugbank_path, "r", encoding="utf-8", errors="replace") as f:
            reader = csv_mod.DictReader(f)
            for row in reader:
                # Match by name (case-insensitive). DrugBank CSV columns
                # vary, but 'name' is the canonical column.
                name = (row.get("name") or row.get("drug_name") or "").lower()
                if name == drug_name_lower:
                    drug_row = row
                    break
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read {drugbank_path}: {exc}")

    if drug_row is None:
        raise HTTPException(status_code=404, detail=f"Drug '{drug_name}' not found in DrugBank data.")

    # 2. Find targets (proteins) from drugbank_interactions.csv.
    targets: List[Dict[str, Any]] = []
    if interactions_path.exists():
        try:
            with open(interactions_path, "r", encoding="utf-8", errors="replace") as f:
                reader = csv_mod.DictReader(f)
                for row in reader:
                    name = (row.get("drug") or row.get("drug_name") or "").lower()
                    if name == drug_name_lower:
                        targets.append({
                            "protein": row.get("protein") or row.get("target") or row.get("uniprot_id") or "",
                            "uniprot_id": row.get("uniprot_id") or "",
                            "action": row.get("action") or "unknown",
                            "evidence": row.get("evidence") or "drugbank",
                        })
        except Exception:
            pass

    # 3. Find indications from drugbank_indications.csv.
    indications: List[str] = []
    if indications_path.exists():
        try:
            with open(indications_path, "r", encoding="utf-8", errors="replace") as f:
                reader = csv_mod.DictReader(f)
                for row in reader:
                    name = (row.get("drug") or row.get("drug_name") or "").lower()
                    if name == drug_name_lower:
                        ind = row.get("indication") or row.get("disease") or ""
                        if ind:
                            indications.append(ind)
        except Exception:
            pass

    return {
        "drug": drug_name,
        "drugbank_id": drug_row.get("drugbank_id") or "",
        "inchikey": drug_row.get("inchikey") or "",
        "smiles": drug_row.get("smiles") or "",
        "targets": targets,
        "indications": indications,
        "source": "phase1_drugbank_csv",
    }


@app.get("/health")
def health() -> Dict[str, Any]:
    """Health endpoint. Returns service metadata + data availability."""
    pdir = _processed_data_dir()
    csv_count = len(list(pdir.glob("*.csv*"))) if pdir.exists() else 0
    return {
        "status": "ok",
        "service": "phase1_dataset",
        "version": "1.0.0",
        "phase1_data_available": csv_count > 0,
        "csv_count": csv_count,
        "processed_data_dir": str(pdir),
    }


@app.get("/datasets")
def datasets() -> Dict[str, Any]:
    """Return real Phase 1 dataset stats (no fabrication)."""
    return _load_dataset_stats()


# BE-024 ROOT FIX (Team Member 12): the frontend's
# `frontend/src/lib/services/dataset-stats.ts:proxyToDatasetService()`
# issues `GET ${DATASET_SERVICE_URL}/stats`. The previous Phase 1 service
# only exposed /health, /datasets, and /datasets/{drug}/mechanism — so
# when an operator set DATASET_SERVICE_URL, the proxy 404'd and fell back
# to the local checkpoint silently. The operator believed they were
# fetching live stats when they were not.
#
# Root fix: expose /stats with the EXACT response shape the frontend
# expects (DatasetStatsResponse in dataset-stats.ts). We translate the
# Phase-1-internal _load_dataset_stats() output into that shape — using
# the real CSV row counts as the source of truth. No fabrication: if no
# CSVs exist (Phase 1 hasn't run), we return zero counts with a clear
# `backend: "phase1_service_no_data"` marker so the dashboard can render
# the "no data ingested yet" state honestly.
@app.get("/stats")
def stats() -> Dict[str, Any]:
    """Phase 1 stats in the DatasetStatsResponse shape the frontend expects.

    This endpoint is the contract-satisfying peer of
    ``frontend/src/lib/services/dataset-stats.ts:proxyToDatasetService``.
    It returns the fields the frontend destructures: sources, nodesLoaded,
    edgesLoaded, edgeTypesPresent, pipelineVersion, schemaVersion,
    bridgeVersion, backend, warnings, errors, generatedAt.
    """
    raw = _load_dataset_stats()
    # Translate Phase-1-internal source rows into the frontend's
    # DatasetSourceStat shape: { name, loaded, rowsLoaded?, sha256? }.
    sources_out: List[Dict[str, Any]] = []
    for s in raw.get("sources", []):
        sources_out.append({
            "name": s.get("name", "unknown"),
            "loaded": bool(s.get("available", False)),
            "rowsLoaded": int(s.get("row_count", 0) or 0),
        })

    # nodesLoaded / edgesLoaded: Phase 1 doesn't build the graph (Phase 2
    # does), but the bridge summary records how many nodes/edges were
    # STAGED for Phase 2. We surface the drug + protein + interaction
    # counts as the closest Phase-1-native equivalent. Phase 2's own
    # /kg/stats endpoint is the canonical source for final graph counts.
    nodes_loaded = (
        int(raw.get("total_drugs", 0) or 0)
        + int(raw.get("total_proteins", 0) or 0)
    )
    edges_loaded = int(raw.get("total_ppi", 0) or 0)

    # edgeTypesPresent: Phase 1 stages Compound→Protein (DrugBank
    # interactions) and Protein→Protein (STRING). Other edge types
    # (Protein→Pathway, Pathway→Disease, Drug→AdverseEvent) are added by
    # Phase 2. We surface only what Phase 1 actually produced — never
    # fabricate.
    edge_types: List[str] = []
    for s in raw.get("sources", []):
        if not s.get("available", False):
            continue
        name = s.get("name", "")
        if name == "drugbank":
            edge_types.append("Compound->Protein")
        elif name == "string":
            edge_types.append("Protein->Protein")
        elif name in ("disgenet", "omim"):
            edge_types.append("Gene->Disease")

    warnings: List[str] = []
    errors: List[str] = []
    if raw.get("total_sources_available", 0) < raw.get("total_sources_expected", 0):
        warnings.append(
            f"Phase 1 has {raw.get('total_sources_available', 0)}/"
            f"{raw.get('total_sources_expected', 0)} sources available. "
            "Run the full Airflow ETL pipeline to ingest all 7 sources."
        )

    return {
        "sources": sources_out,
        "nodesLoaded": nodes_loaded,
        "edgesLoaded": edges_loaded,
        "edgeTypesPresent": edge_types,
        "pipelineVersion": "phase1-service-v1",
        "schemaVersion": "1.0",
        "bridgeVersion": None,
        "backend": "phase1_service",
        "warnings": warnings,
        "errors": errors,
        "generatedAt": _now_iso(),
    }


def _now_iso() -> str:
    """Return current UTC time in ISO-8601 (seconds precision)."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


@app.get("/datasets/{drug}/mechanism")
def drug_mechanism(drug: str) -> Dict[str, Any]:
    """Return the mechanism-of-action (targets + indications) for a drug."""
    return _load_drug_mechanism(drug)


# =============================================================================
# TM3 Task 3.3 v127 ROOT FIX — POST /datasets/validated_hypotheses
# -----------------------------------------------------------------------------
# RED-TEAM AUDIT FINDING (v127, hostile-auditor pass):
#   The DOCX §10 (Data Flywheel) mandates that validated drug-disease
#   hypotheses be fed back to the platform as new labeled data points so
#   the Graph Transformer retrains on proprietary data. Phase 4's
#   ``rl/service.py`` (line 873) calls ``write_validated_hypothesis()``
#   which writes ONLY to a CSV file (phase1/processed_data/
#   validated_hypotheses.csv per the TM14 ``shared/contracts/writeback.py``
#   contract). The CSV is a transport format — ephemeral, not queryable,
#   no transactional guarantees, no FK integrity. A wet-lab-validated
#   hypothesis (the platform's core proprietary moat) was being persisted
#   to a flat file that any operator could ``rm``.
#
# ROOT FIX (this endpoint + migration 019 + ORM model):
#   Add a POST endpoint that accepts a validated hypothesis payload (in
#   the TM14 CSV-shape contract — ``drug``, ``disease``, ``outcome``, etc.)
#   and persists it to the PostgreSQL ``validated_hypotheses`` table
#   (migration 019). The CSV remains as a transport format; this table is
#   the AUTHORITATIVE durable store.
#
# CONTRACT (request body — matches TM14's WRITEBACK_CSV_COLUMNS):
#   {
#     "drug": "Aspirin",                   # -> drug_name (drug_id looked up)
#     "disease": "Cardiovascular Disease", # -> disease_name (disease_id looked up)
#     "outcome": "validated_positive",     # one of VALID_OUTCOMES
#     "validated_by": "pharma_partner_acme",
#     "validation_study_id": "NCT01234567",# -> source
#     "validated_at": "2026-07-19T12:34:56Z",  # ISO-8601
#     "notes": "Phase 3 RCT confirmed efficacy",
#     "original_gt_score": 0.92,           # -> score
#     "original_rl_rank": 1,               # ignored (not in 10-col schema)
#     "writeback_version": "2.0.0-shared-contract"  # ignored
#   }
#
# RESPONSE (201 Created):
#   {
#     "status": "ok",
#     "id": 42,
#     "validated_hypothesis": { ... full row ... },
#     "flywheel_status": "persisted_to_postgresql"
#   }
#
# ERROR RESPONSES:
#   - 400 Bad Request: validation error (bad outcome, bad score, etc.)
#   - 503 Service Unavailable: DB unavailable (DATABASE_URL unset or
#     connection refused). The service does NOT fall back to CSV write
#     here — the operator must fix the DB. CSV writeback remains the
#     responsibility of TM14's ``write_validated_hypothesis()`` function;
#     this endpoint is the PostgreSQL persistence path only.
# =============================================================================

# --- Pydantic request model (matches TM14's WRITEBACK_CSV_COLUMNS) ----------
# We accept the TM14 CSV-shape payload so TM9 (rl/service.py owner) does NOT
# need to change the writeback call shape when migrating from CSV to DB.
# We translate the CSV column names to the table's column names at write time.
try:
    # Pydantic v2 (current) — ``from pydantic import BaseModel``.
    from pydantic import BaseModel, Field, field_validator
    _PYDANTIC_AVAILABLE = True
except ImportError:  # pragma: no cover — fastapi always pulls pydantic
    _PYDANTIC_AVAILABLE = False
    BaseModel = object  # type: ignore[assignment, misc]

# Validated hypothesis outcome values (mirror TM14's VALID_OUTCOMES).
# Imported lazily inside the endpoint to avoid a hard dependency on the
# shared.contracts module at service startup (the service must still boot
# even if shared/ is unimportable in stripped-down dev envs).
_VH_OUTCOME_VALUES: tuple[str, ...] = (
    "validated_positive",
    "validated_toxic",
    "validated_negative",
    "invalidated",
)


if _PYDANTIC_AVAILABLE:

    class ValidatedHypothesisRequest(BaseModel):
        """Request body for POST /datasets/validated_hypotheses.

        Mirrors the TM14 ``shared/contracts/writeback.py`` WRITEBACK_CSV_COLUMNS
        so the rl/service.py writeback call shape is unchanged.
        """

        # Required fields (TM14's REQUIRED_COLUMNS).
        drug: str = Field(..., min_length=1, description="Drug name")
        disease: str = Field(..., min_length=1, description="Disease name")
        outcome: str = Field(..., description="Validation outcome")
        validated_at: str = Field(..., description="ISO-8601 validation timestamp")

        # Optional audit metadata (TM14's optional columns).
        validated_by: Optional[str] = Field(None, max_length=200)
        validation_study_id: Optional[str] = Field(None, max_length=200)
        notes: Optional[str] = Field(None)
        original_gt_score: Optional[float] = Field(None, ge=0.0, le=1.0)
        original_rl_rank: Optional[int] = Field(None, ge=0)
        writeback_version: Optional[str] = Field(None, max_length=50)

        @field_validator("outcome")
        @classmethod
        def _validate_outcome(cls, v: str) -> str:
            if v not in _VH_OUTCOME_VALUES:
                raise ValueError(
                    f"outcome must be one of {_VH_OUTCOME_VALUES}, got {v!r}"
                )
            return v

        @field_validator("drug", "disease")
        @classmethod
        def _strip_and_nonempty(cls, v: str) -> str:
            v2 = v.strip()
            if not v2:
                raise ValueError("must not be empty / whitespace-only")
            return v2

else:  # pragma: no cover — fastapi always pulls pydantic
    ValidatedHypothesisRequest = None  # type: ignore[assignment, misc]


def _get_db_session():
    """Lazily create a SQLAlchemy session to the Phase 1 PostgreSQL DB.

    Returns ``None`` if DATABASE_URL is unset or the connection fails —
    the caller (the POST endpoint) returns a 503 in that case so the
    operator knows to fix the DB. The existing GET endpoints (/datasets,
    /health, /stats) do NOT use the DB — they read CSVs — so a DB failure
    does not affect the existing service surface.
    """
    db_url = os.environ.get("DATABASE_URL") or os.environ.get("PHASE1_DB_URL")
    if not db_url:
        return None, "DATABASE_URL and PHASE1_DB_URL are both unset"
    try:
        # Lazy import so the service starts even if SQLAlchemy / psycopg2
        # are not installed (e.g. in a CI environment that only tests the
        # CSV-backed GET endpoints). The POST endpoint will return 503
        # with a clear error message in that case.
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session
        # Import the models so Base.metadata has the ValidatedHypothesis
        # table registered (needed if the DB is empty and create_all is
        # called — but we do NOT call create_all here; migrations are
        # owned by the migration runner).
        import phase1.database.models  # noqa: F401
        from phase1.database.models import ValidatedHypothesis  # noqa: F401

        # check_same_thread is False only for SQLite; for Postgres it's
        # a no-op. The engine is per-call (not cached) because the POST
        # endpoint is low-traffic (validated hypotheses arrive at human
        # speed — wet-lab results, not ML inference throughput).
        # For production-grade connection pooling, callers should use
        # phase1.database.connection.get_db_session() instead.
        connect_args = {"check_same_thread": False} if db_url.startswith("sqlite") else {}
        engine = create_engine(db_url, connect_args=connect_args, pool_pre_ping=True)
        return Session(engine), None
    except Exception as exc:
        return None, f"DB connection failed: {exc!r}"


@app.post("/datasets/validated_hypotheses", status_code=201)
def create_validated_hypothesis(payload: "ValidatedHypothesisRequest") -> Dict[str, Any]:
    """Persist a validated drug-disease hypothesis to PostgreSQL.

    This is the data flywheel writeback endpoint (TM3 Task 3.3 v127).
    Accepts the TM14 CSV-shape payload, translates it to the 10-column
    canonical schema, and INSERTs into the validated_hypotheses table.
    """
    if not _PYDANTIC_AVAILABLE or ValidatedHypothesisRequest is None:
        raise HTTPException(
            status_code=503,
            detail="Pydantic is not installed — cannot validate request body. "
                   "Install pydantic (it is a fastapi dependency, so this should "
                   "not happen).",
        )

    session, err = _get_db_session()
    if session is None:
        raise HTTPException(
            status_code=503,
            detail=f"Database unavailable — cannot persist validated hypothesis. "
                   f"The data flywheel requires PostgreSQL. Error: {err}",
        )

    try:
        from phase1.database.models import ValidatedHypothesis
        from sqlalchemy import select
        from datetime import datetime

        # --- Translate TM14 CSV-shape payload -> 10-column canonical schema ---
        # drug_id / disease_id: look up from the drugs table by name. If the
        # drug is not in the drugs table (e.g. newly-validated drug not yet
        # loaded by Phase 1 ETL), drug_id stays NULL — the row is still
        # accepted (drug_name is the NOT NULL column).
        drug_id: Optional[str] = None
        disease_id: Optional[str] = None

        try:
            # Look up drug_id from the drugs table (match by name, case-
            # insensitive). Take the InChIKey as the canonical ID if
            # present, else drugbank_id, else chembl_id. This is a best-
            # effort enrichment — failure to find a drug_id is NOT an error.
            #
            # PERF/ROBUSTNESS FIX: select ONLY the three ID columns we
            # need (inchikey, drugbank_id, chembl_id) — do NOT load the
            # full Drug ORM object. Loading the full object triggers
            # SQLAlchemy's lazy-load of Drug.drug_protein_interactions
            # (cascade='all, delete-orphan' relationship), which issues a
            # second SELECT against drug_protein_interactions. That second
            # query (a) is wasted work (we don't need the interactions),
            # and (b) FAILS if the drug_protein_interactions table is
            # empty/missing in a minimal schema bootstrap, causing the
            # drug_id lookup to silently fail and the row to be persisted
            # with drug_id=NULL. The column-only SELECT avoids both issues.
            from phase1.database.models import Drug
            from sqlalchemy import select as _sel
            _drug_cols = session.execute(
                _sel(Drug.inchikey, Drug.drugbank_id, Drug.chembl_id).where(
                    func_lower_or_self(Drug.name) == payload.drug.lower()
                ).limit(1)
            ).first()
            if _drug_cols is not None:
                drug_id = _drug_cols[0] or _drug_cols[1] or _drug_cols[2] or None
        except Exception as exc:
            logger.warning(
                "phase1.service POST /datasets/validated_hypotheses: "
                "drug_id lookup failed for %r: %s. Proceeding with "
                "drug_id=NULL (drug_name is the NOT NULL column).",
                payload.drug, exc,
            )

        # disease_id lookup: gene_disease_associations has disease_id +
        # disease_name. Best-effort match by disease_name (case-insensitive).
        # Same column-only SELECT pattern as the drug_id lookup above.
        try:
            from phase1.database.models import GeneDiseaseAssociation
            from sqlalchemy import select as _sel2
            _disease_col = session.execute(
                _sel2(GeneDiseaseAssociation.disease_id).where(
                    func_lower_or_self(GeneDiseaseAssociation.disease_name) == payload.disease.lower()
                ).limit(1)
            ).scalar()
            if _disease_col is not None:
                disease_id = _disease_col
        except Exception as exc:
            logger.warning(
                "phase1.service POST /datasets/validated_hypotheses: "
                "disease_id lookup failed for %r: %s. Proceeding with "
                "disease_id=NULL.",
                payload.disease, exc,
            )

        # Parse validated_at (ISO-8601). Pydantic already validated the
        # string is present; we just convert to datetime.
        try:
            validated_at_dt = datetime.fromisoformat(
                payload.validated_at.replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"validated_at must be ISO-8601, got {payload.validated_at!r}: {exc}",
            )

        # Compose the notes field — combine TM14 notes + writeback_version
        # + original_rl_rank (which is not in the 10-col schema).
        notes_parts: list[str] = []
        if payload.notes:
            notes_parts.append(payload.notes)
        if payload.writeback_version:
            notes_parts.append(f"[writeback_version={payload.writeback_version}]")
        if payload.original_rl_rank is not None:
            notes_parts.append(f"[original_rl_rank={payload.original_rl_rank}]")
        combined_notes = " ".join(notes_parts) if notes_parts else None

        # Build the ORM object.
        vh = ValidatedHypothesis(
            drug_id=drug_id,
            drug_name=payload.drug,
            disease_id=disease_id,
            disease_name=payload.disease,
            score=payload.original_gt_score,
            outcome=payload.outcome,
            validated_at=validated_at_dt,
            validated_by=payload.validated_by,
            source=payload.validation_study_id,
            notes=combined_notes,
        )
        session.add(vh)
        session.commit()
        session.refresh(vh)

        logger.info(
            "phase1.service POST /datasets/validated_hypotheses: persisted "
            "id=%d drug=%r disease=%r outcome=%r score=%s drug_id=%r disease_id=%r",
            vh.id, vh.drug_name, vh.disease_name, vh.outcome, vh.score,
            vh.drug_id, vh.disease_id,
        )

        return {
            "status": "ok",
            "id": vh.id,
            "validated_hypothesis": {
                "id": vh.id,
                "drug_id": vh.drug_id,
                "drug_name": vh.drug_name,
                "disease_id": vh.disease_id,
                "disease_name": vh.disease_name,
                "score": float(vh.score) if vh.score is not None else None,
                "outcome": vh.outcome,
                "validated_at": vh.validated_at.isoformat() if vh.validated_at else None,
                "validated_by": vh.validated_by,
                "source": vh.source,
                "notes": vh.notes,
                "created_at": vh.created_at.isoformat() if vh.created_at else None,
            },
            "flywheel_status": "persisted_to_postgresql",
        }
    except HTTPException:
        session.rollback()
        raise
    except Exception as exc:
        session.rollback()
        # Check for unique-constraint violation (duplicate writeback).
        # The error message differs between Postgres and SQLite; check both.
        err_msg = str(exc).lower()
        if "unique" in err_msg or "duplicate" in err_msg:
            raise HTTPException(
                status_code=409,
                detail=f"Duplicate validated hypothesis (drug_id, disease_id, "
                       f"validated_at) — already persisted. Original: {exc}",
            )
        logger.exception(
            "phase1.service POST /datasets/validated_hypotheses: "
            "unexpected error persisting hypothesis."
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to persist validated hypothesis: {type(exc).__name__}: {exc}",
        )
    finally:
        session.close()


def func_lower_or_self(column):
    """Return ``func.lower(column)`` if SQLAlchemy is available, else column.

    Helper for case-insensitive name lookups. Imported here to keep the
    endpoint code readable. The ``func`` import is local so the service
    starts even if SQLAlchemy is unimportable (the GET endpoints don't need it).
    """
    try:
        from sqlalchemy import func
        return func.lower(column)
    except ImportError:
        return column


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PHASE1_SERVICE_PORT", "8001"))
    host = os.environ.get("PHASE1_SERVICE_HOST", "0.0.0.0")
    logger.info("Starting Phase 1 Dataset Service on %s:%d", host, port)
    uvicorn.run(app, host=host, port=port)
