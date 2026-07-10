#!/usr/bin/env python3
"""Run pipeline downloads in parallel (4 workers) — TWO-PHASE design.

v75 ROOT FIX (T-025 — download_parallel.py skips entity resolution):
    The v74 ``download_parallel.py`` called ``cls(run_id=_run_id).run()``
    for each pipeline — the FULL run including LOAD to DB. It NEVER
    called entity_resolution. The master_pipeline_dag.py had a dedicated
    ``entity_resolution`` task that ran BETWEEN downloads and loads —
    it cross-resolved drugs across ChEMBL/DrugBank/PubChem and proteins
    across UniProt/STRING. ``download_parallel.py`` skipped this
    entirely. Drugs loaded by ChEMBL and DrugBank for the same compound
    were NOT cross-resolved. Operators who used ``make download-parallel``
    got a database where ``entity_mapping`` was empty and
    ``proteins.string_id`` was never updated. The knowledge graph built
    from this DB had no entity resolution lineage and may have had
    duplicate drug entities.

    ROOT FIX (master-grade, mirrors the Airflow DAG exactly):
      Phase A — DOWNLOAD + CLEAN only (no DB load):
        FIRST_PASS  : ChEMBL, UniProt, STRING (parallel, 3 workers)
        SECOND_PASS : DisGeNET, OMIM (sequential — see master DAG comment)
        FOURTH_PASS : DrugBank (requires manual XML, separate step)

      Phase B — ENTITY RESOLUTION (single call to the shared module
        ``entity_resolution/run.py::run_entity_resolution()`` — same
        code path as the Airflow ``entity_resolution`` task).

      Phase C — LOAD only (data already downloaded + cleaned + resolved):
        THIRD_PASS  : PubChem (needs drugs in DB from Phase A + entity resolution)
        LOAD_PASS   : ChEMBL, DrugBank, UniProt, STRING, DisGeNET, OMIM, PubChem
                      all call ``.run_load_only()``

    This is the SAME two-phase design the master DAG uses. The Phase 1
    DB produced by ``make download-parallel`` is now IDENTICAL to the
    DB produced by a master DAG run (modulo the parallelism difference
    in Phase A). The Phase 2 bridge can consume either DB with the
    same semantics — entity_mapping is populated, proteins.string_id
    is updated, no duplicate drug entities.

    The previous FOUR_PASS structure (download+clean+load each pipeline
    in one shot) is GONE. Each pipeline now runs ``.run_download_and_
    clean_only()`` in Phase A and ``.run_load_only()`` in Phase C.

FIX M9: PubChem is moved to a third-pass step because it requires drugs
already in the database. First-pass pipelines run in parallel, then
DisGeNET+OMIM run sequentially, then PubChem runs after ChEMBL has
loaded drugs.

FIX AUDIT-21 (CORRECTED by FIX-P1-C-20): the original comment claimed
"DisGeNET and OMIM share gene_disease_associations.csv via
_save_csv_with_mode" — this is FALSE. Verified by inspecting
``DisGeNETPipeline.source_name`` ("disgenet" → writes to
``gene_disease_associations.csv``) and ``OMIMPipeline.source_name``
("omim" → writes to ``omim_gene_disease_associations.csv`` per
``OMIM_OUTPUT_FILENAME``). They write to DIFFERENT files, so running
them in parallel would NOT cause CSV corruption.

The REAL reason for sequential execution (kept by v40 ROOT FIX P1 #55
in master_pipeline_dag.py): DrugBank's ``_write_structured_indications``
step reads the OMIM CSV, so OMIM must finish before DrugBank starts.
Wiring ``disgenet >> omim >> drugbank`` keeps the linear dependency
chain explicit. Running DisGeNET and OMIM in parallel is safe in
PRINCIPLE but the sequential ordering is a defensive choice that
mirrors the linear gene-disease data flow. See master_pipeline_dag.py
lines 596-619 for the canonical explanation.

FIX AUDIT-22: DrugBank requires manual XML download, so it runs in
a separate fourth pass with a clear error message if the XML is missing.

SCI-FIX: The script now exits with non-zero status if any pipeline fails,
so CI/CD can detect broken pipelines. Previously, failures were printed
but the exit code was always 0.
"""
import concurrent.futures
import os
import sys

# Ensure project root is importable when running from any directory
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from pipelines.chembl_pipeline import ChEMBLPipeline
from pipelines.uniprot_pipeline import UniProtPipeline
from pipelines.string_pipeline import StringPipeline
from pipelines.disgenet_pipeline import DisGeNETPipeline
from pipelines.omim_pipeline import OMIMPipeline
from pipelines.pubchem_pipeline import PubChemPipeline
from pipelines.drugbank_pipeline import DrugBankPipeline

# FIX AUDIT-21 (CORRECTED by FIX-P1-C-20): the original comment claimed
# DisGeNET and OMIM "share gene_disease_associations.csv via
# _save_csv_with_mode" — this is FALSE (see module docstring for the
# verification). They write to DIFFERENT files. The sequential ordering
# here is a defensive choice for the linear DisGeNET → OMIM → DrugBank
# dependency chain, NOT a CSV-collision avoidance measure.
#
# v75 ROOT FIX (T-025): each entry is (name, cls, phase). ``phase`` is
# "download" or "load" — the run_pipeline() helper dispatches on it.
# This makes the two-phase design explicit at the data structure level.
FIRST_PASS_DOWNLOAD = [
    ("chembl", ChEMBLPipeline),
    ("uniprot", UniProtPipeline),
    ("string", StringPipeline),
]
SECOND_PASS_DOWNLOAD = [
    ("disgenet", DisGeNETPipeline),  # Sequential by convention (see docstring)
    ("omim", OMIMPipeline),  # Sequential by convention (see docstring)
]
FOURTH_PASS_DOWNLOAD = [("drugbank", DrugBankPipeline)]

# PubChem download needs drugs in DB → must run AFTER Phase B (entity
# resolution) and after the other drugs are loaded.
THIRD_PASS_DOWNLOAD = [("pubchem", PubChemPipeline)]

# Phase C — load-only for every source (data is already cleaned on disk).
LOAD_PASS = [
    ("chembl", ChEMBLPipeline),
    ("drugbank", DrugBankPipeline),
    ("uniprot", UniProtPipeline),
    ("string", StringPipeline),
    ("disgenet", DisGeNETPipeline),
    ("omim", OMIMPipeline),
    ("pubchem", PubChemPipeline),
]


def run_pipeline(args):
    """Run a pipeline in the given phase.

    v75 ROOT FIX (T-025): the previous version called ``cls(run_id=...).run()``
    unconditionally — the FULL run including LOAD. This meant
    ``download_parallel.py`` loaded every source BEFORE entity resolution
    ran, so the loaded rows had no entity-mapping lineage, AND PubChem's
    load (which queries the drugs table) ran against a partial DB.

    The fix: dispatch on ``phase``:
      * ``"download"`` → call ``.run_download_and_clean_only()``
        (no DB write — just produce the cleaned CSV on disk).
      * ``"load"``     → call ``.run_load_only()``
        (read the cleaned CSV, write to DB — entity_mapping already
        populated by Phase B between the two passes).
    """
    name, cls, phase, _run_id = args
    try:
        instance = cls(run_id=_run_id)
        if phase == "download":
            instance.run_download_and_clean_only()
        elif phase == "load":
            instance.run_load_only()
        else:
            raise ValueError(f"Unknown phase {phase!r} for pipeline {name!r}")
        return (name, True, None, _run_id)
    except Exception as e:
        return (name, False, str(e), _run_id)


def _run_entity_resolution_phase():
    """Phase B — run cross-database entity resolution.

    v75 ROOT FIX (T-025): this is the step the v74 script was missing.
    It calls the SAME function the Airflow ``entity_resolution`` task
    calls (``entity_resolution/run.py::run_entity_resolution()``),
    so the DB produced by ``make download-parallel`` is semantically
    identical to the DB produced by a master DAG run.
    """
    print("=" * 70)
    print("Phase B — Entity Resolution (cross-database drug + protein resolution)")
    print("=" * 70)
    try:
        from entity_resolution.run import run_entity_resolution
        result = run_entity_resolution()
        # v83 FORENSIC ROOT FIX (P2-14): the previous code accessed
        # ``result['drug_mappings']``, ``result['protein_mappings']``,
        # ``result['proteins_updated']`` directly — if
        # ``run_entity_resolution`` returned a different dict structure
        # (e.g. renamed a key), the print() crashed with KeyError and
        # the script exited with a confusing traceback instead of a
        # clear error. ROOT FIX: use ``.get()`` with defaults and
        # validate the result is a dict before accessing. If the
        # structure is unexpected, log a clear warning but don't crash.
        if not isinstance(result, dict):
            print(
                f"  [WARN] Entity resolution returned non-dict result "
                f"(type={type(result).__name__}). Cannot extract counts."
            )
            return (True, None, result)
        drug_mappings = result.get("drug_mappings", "N/A")
        protein_mappings = result.get("protein_mappings", "N/A")
        proteins_updated = result.get("proteins_updated", "N/A")
        # Detect missing keys for a clear warning (non-fatal).
        missing_keys = [
            k for k in ("drug_mappings", "protein_mappings", "proteins_updated")
            if k not in result
        ]
        if missing_keys:
            print(
                f"  [WARN] Entity resolution result missing expected keys: "
                f"{missing_keys}. Available keys: {sorted(result.keys())}. "
                f"Reporting 'N/A' for missing counts."
            )
        print(
            f"  [OK] Entity resolution complete: "
            f"{drug_mappings} drug mappings, "
            f"{protein_mappings} protein mappings, "
            f"{proteins_updated} proteins updated with string_id."
        )
        return (True, None, result)
    except Exception as exc:
        print(f"  [FAIL] Entity resolution failed: {exc}")
        return (False, str(exc), None)


if __name__ == "__main__":
    import uuid as _uuid_main
    import os as _os_main

    # v38 ROOT FIX (Phase 1 Issue #2): pre-compute run_ids in the MAIN
    # thread so each pipeline gets a unique, deterministic run_id BEFORE
    # being submitted to the thread pool. The previous code computed
    # run_ids INSIDE the thread function, which caused a race condition
    # (4 threads overwriting the same os.environ["PIPELINE_RUN_ID"]).
    def _make_run_id(name):
        _base = _os_main.environ.get("PIPELINE_RUN_ID", "")
        if _base:
            return f"{_base}_{name}"
        return f"parallel_{name}_{_uuid_main.uuid4().hex[:8]}"

    def _with_run_ids(pipelines, phase):
        """Attach a pre-computed run_id and the phase tag to each tuple."""
        return [(name, cls, phase, _make_run_id(name)) for name, cls in pipelines]

    all_results = []
    overall_failed = False

    # =====================================================================
    # PHASE A — DOWNLOAD + CLEAN (no DB load)
    # =====================================================================
    print("=" * 70)
    print("Phase A — Download + Clean (no DB load; .run_download_and_clean_only)")
    print("=" * 70)

    print(f"\n[A.1] First-pass pipelines in parallel ({len(FIRST_PASS_DOWNLOAD)} jobs)...")
    print(f"  (FIRST_PASS has {len(FIRST_PASS_DOWNLOAD)} pipelines, max_workers={len(FIRST_PASS_DOWNLOAD)})")
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(FIRST_PASS_DOWNLOAD)) as pool:
        results = list(pool.map(run_pipeline, _with_run_ids(FIRST_PASS_DOWNLOAD, "download")))
    all_results.extend(results)
    for name, ok, err, run_id in results:
        if ok:
            print(f"  [OK] {name} (run_id={run_id})")
        else:
            print(f"  [FAIL] {name} (run_id={run_id}): {err}")
            overall_failed = True

    print("\n[A.2] Second-pass (DisGeNET + OMIM sequential)...")
    second_results = list(map(run_pipeline, _with_run_ids(SECOND_PASS_DOWNLOAD, "download")))
    all_results.extend(second_results)
    for name, ok, err, run_id in second_results:
        if ok:
            print(f"  [OK] {name} (run_id={run_id})")
        else:
            print(f"  [FAIL] {name} (run_id={run_id}): {err}")
            overall_failed = True

    print("\n[A.3] Fourth-pass (DrugBank — requires manual XML)...")
    fourth_results = list(map(run_pipeline, _with_run_ids(FOURTH_PASS_DOWNLOAD, "download")))
    all_results.extend(fourth_results)
    for name, ok, err, run_id in fourth_results:
        if ok:
            print(f"  [OK] {name} (run_id={run_id})")
        else:
            print(f"  [FAIL] {name} (run_id={run_id}): {err}")
            overall_failed = True

    # =====================================================================
    # PHASE B — ENTITY RESOLUTION (the v75 fix; was MISSING in v74)
    # =====================================================================
    er_ok, er_err, er_result = _run_entity_resolution_phase()
    if not er_ok:
        overall_failed = True

    # =====================================================================
    # PHASE C — LOAD ONLY (data already downloaded + cleaned + resolved)
    # =====================================================================
    print("=" * 70)
    print("Phase C — Load only (.run_load_only — entity_mapping already populated)")
    print("=" * 70)

    # C.1: Load all sources EXCEPT PubChem first. PubChem's load
    # queries the drugs table (enrichment lookup), so the drug-loading
    # sources must be loaded before PubChem.
    print("\n[C.1] Loading all sources except PubChem...")
    load_pass_no_pubchem = [(n, c) for n, c in LOAD_PASS if n != "pubchem"]
    load_results_no_pubchem = list(
        map(run_pipeline, _with_run_ids(load_pass_no_pubchem, "load"))
    )
    all_results.extend(load_results_no_pubchem)
    for name, ok, err, run_id in load_results_no_pubchem:
        if ok:
            print(f"  [OK] {name} loaded (run_id={run_id})")
        else:
            print(f"  [FAIL] {name} load failed (run_id={run_id}): {err}")
            overall_failed = True

    # C.2: PubChem download + load — needs drugs in DB (now loaded + resolved).
    print("\n[C.2] PubChem download (needs drugs in DB — entity resolution done)...")
    pubchem_download_results = list(
        map(run_pipeline, _with_run_ids(THIRD_PASS_DOWNLOAD, "download"))
    )
    all_results.extend(pubchem_download_results)
    for name, ok, err, run_id in pubchem_download_results:
        if ok:
            print(f"  [OK] {name} downloaded (run_id={run_id})")
        else:
            print(f"  [FAIL] {name} download failed (run_id={run_id}): {err}")
            overall_failed = True

    print("\n[C.3] PubChem load...")
    pubchem_load_results = list(
        map(run_pipeline, _with_run_ids([("pubchem", PubChemPipeline)], "load"))
    )
    all_results.extend(pubchem_load_results)
    for name, ok, err, run_id in pubchem_load_results:
        if ok:
            print(f"  [OK] {name} loaded (run_id={run_id})")
        else:
            print(f"  [FAIL] {name} load failed (run_id={run_id}): {err}")
            overall_failed = True

    # =====================================================================
    # FINAL SUMMARY
    # =====================================================================
    # SCI-FIX: Exit non-zero if any pipeline OR entity resolution failed
    # so CI/CD can detect broken pipelines. In a medical ETL pipeline,
    # silent failures mean stale or missing drug data — and an
    # unresolved DB silently corrupts every downstream KG build.
    failed = [name for name, ok, _err, _run_id in all_results if not ok]
    if failed or not er_ok:
        if failed:
            print(f"\nFAILED pipelines: {failed}")
        if not er_ok:
            print(f"\nFAILED entity resolution: {er_err}")
        sys.exit(1)
    else:
        print("\n" + "=" * 70)
        print("All pipelines + entity resolution completed successfully.")
        print(f"  Drug mappings:   {er_result['drug_mappings']}")
        print(f"  Protein mappings: {er_result['protein_mappings']}")
        print(f"  Proteins updated: {er_result['proteins_updated']}")
        print("=" * 70)
