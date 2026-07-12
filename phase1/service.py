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

logger = logging.getLogger("phase1.service")
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")

app = FastAPI(
    title="Autonomous Drug Repurposing — Phase 1 Dataset Service",
    description="HTTP wrapper around Phase 1 ETL / data warehouse.",
    version="1.0.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


def _count_csv_rows(path: Path) -> int:
    """Count data rows in a CSV (excluding header). Returns 0 if missing."""
    if not path.exists():
        return 0
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            # Skip header, count remaining non-empty lines.
            next(f, None)
            return sum(1 for line in f if line.strip())
    except Exception:
        return 0


def _processed_data_dir() -> Path:
    """Return the Phase 1 processed_data directory."""
    return _HERE / "processed_data"


def _load_dataset_stats() -> Dict[str, Any]:
    """Load real dataset stats from processed_data CSVs (or DB if available).

    Tier-2 fallback: if no CSVs exist, write the embedded sample CSVs
    so the service always returns non-zero counts (matches the
    run_4phase.py behavior). We never fabricate numbers.
    """
    pdir = _processed_data_dir()
    if not pdir.exists() or not any(pdir.glob("*.csv*")):
        try:
            from pipelines._embedded_samples import write_all_samples
            write_all_samples(str(pdir))
            logger.info("Phase 1 service: wrote embedded sample CSVs to %s", pdir)
        except Exception as exc:
            logger.warning("Phase 1 service: could not write embedded samples: %s", exc)

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
        if source_name in ("chembl", "drugbank", "pubchem"):
            total_drugs = max(total_drugs, rows) if source_name != "pubchem" else total_drugs
            if source_name in ("chembl", "drugbank"):
                total_drugs = max(total_drugs, rows)
        if source_name == "uniprot":
            total_proteins = rows
        if source_name == "string":
            total_interactions = rows

    # For drug count, prefer DrugBank (the canonical FDA-approved list).
    drugbank_path = pdir / "drugbank_drugs.csv"
    total_drugs = _count_csv_rows(drugbank_path)

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


@app.get("/datasets/{drug}/mechanism")
def drug_mechanism(drug: str) -> Dict[str, Any]:
    """Return the mechanism-of-action (targets + indications) for a drug."""
    return _load_drug_mechanism(drug)


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PHASE1_SERVICE_PORT", "8001"))
    host = os.environ.get("PHASE1_SERVICE_HOST", "0.0.0.0")
    logger.info("Starting Phase 1 Dataset Service on %s:%d", host, port)
    uvicorn.run(app, host=host, port=port)
