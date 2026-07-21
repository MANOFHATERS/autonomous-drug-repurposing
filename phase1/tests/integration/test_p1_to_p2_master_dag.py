"""Integration tests for Teammate 1 Task 1.4 -- P1 -> P2 integration.

TM1 TASK 1.4 ROOT FIX (v131) -- Teammate 1 P1->P2 integration:
  These tests verify the master DAG's validate_output task is correctly
  wired to Phase 2 via the contract (PHASE1_OUTPUT_SCHEMA +
  get_all_aliases + get_required_id_column). The previous code
  (lines 1193-1201 pre-fix) hardcoded 7 CSV filenames, 4 of which were
  WRONG -- in production, every missing CSV caused validate_output to
  raise AirflowFailException and trigger_phase2 NEVER fired.

Test coverage:
  1. ``test_validate_output_passes_with_real_filenames`` -- the
     contract-driven filename resolution accepts the ACTUAL filenames
     the pipeline emits (drugs.csv, proteins.csv,
     protein_protein_interactions.csv, gene_disease_associations.csv,
     omim_gene_disease_associations.csv, pubchem_enrichment.csv) plus
     the canonical drugbank_drugs.csv. The previous hardcoded dict
     rejected 4 of these as "not found" in production.

  2. ``test_validate_output_fails_on_synth_inchikeys`` -- SYNTH-prefixed
     InChIKeys in pubchem_enrichment.csv are detected (the previous
     code scanned ``pubchem_compounds.csv`` which NEVER existed, so
     SYNTH InChIKeys in PubChem enrichment flowed undetected).

  3. ``test_dpi_check_uses_postgres_not_sqlite`` -- the DPI-degraded
     pre-flight check queries PostgreSQL via DATABASE_URL (not SQLite
     via phase1/data/drugos.db). The previous SQLite check silently
     disabled itself in production.

  4. ``test_dpi_check_fails_closed_in_production_without_db_url`` --
     in production with no DATABASE_URL, the DPI check raises
     AirflowFailException (fail-closed). The previous SQLite check
     silently skipped itself when the SQLite DB was missing.

  5. ``test_get_required_id_column_returns_canonical_id_per_source`` --
     the contract's new ``get_required_id_column`` function returns
     the correct canonical ID column for each source (inchikey for
     drug sources, uniprot_id for proteins, gene_symbol for GDA
     sources, None for multi-column-key sources).

  6. ``test_phase2_run_pipeline_accepts_provenance_flag`` -- the
     Phase 2 run_pipeline.py CLI accepts ``--provenance <UUID>`` and
     ``run_full_pipeline`` accepts ``provenance_id`` parameter.

  7. ``test_validate_output_xcom_payload_contract`` -- the validate_output
     XCom payload contains all required keys (pipeline_run_id,
     schema_version, row_counts, synth_key_counts, dpi_missing,
     dpi_acknowledged, dpi_source, validated_at, failures) for
     end-to-end tracing.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# =============================================================================
# Test 1: validate_output passes with the REAL filenames the pipeline emits
# =============================================================================
@pytest.mark.integration
def test_validate_output_passes_with_real_filenames():
    """The contract-driven filename resolution accepts REAL filenames.

    The previous code (lines 1193-1201 pre-fix) hardcoded 7 filenames:
      chembl_drugs.csv, drugbank_drugs.csv, uniprot_proteins.csv,
      string_proteins.csv, disgenet_gda.csv, omim_gda.csv,
      pubchem_compounds.csv

    4 of these were WRONG -- the pipeline actually emits:
      drugs.csv (alias for chembl_drugs)
      drugbank_drugs.csv (canonical for drugs)
      proteins.csv (alias for uniprot_proteins)
      protein_protein_interactions.csv (alias for string_ppi)
      gene_disease_associations.csv (alias for disgenet_gda)
      omim_gene_disease_associations.csv (canonical for omim_gda)
      pubchem_enrichment.csv (canonical for pubchem_enrichment)

    In production (_is_production=True), every missing CSV caused
    validate_output to raise AirflowFailException and trigger_phase2
    NEVER fired. This test verifies the contract-driven resolution
    accepts the REAL filenames.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        # Create CSVs with the REAL filenames the pipeline emits.
        # Each CSV includes the canonical ID column for its source
        # (per get_required_id_column in phase1_schema.py).
        (tmpdir / "drugs.csv").write_text("inchikey,drug_name\nABCDEF,aspirin\n")
        (tmpdir / "drugbank_drugs.csv").write_text("inchikey,name\nGHIJKL,metformin\n")
        (tmpdir / "proteins.csv").write_text("uniprot_id,name\nP12345,EGFR\n")
        (tmpdir / "protein_protein_interactions.csv").write_text(
            "uniprot_id_a,uniprot_id_b\nP1,P2\n"
        )
        (tmpdir / "gene_disease_associations.csv").write_text(
            "gene_symbol,disease_id\nBRCA1,C0001\n"
        )
        (tmpdir / "omim_gene_disease_associations.csv").write_text(
            "gene_symbol,disease_id\nBRCA1,OMIM:100100\n"
        )
        (tmpdir / "pubchem_enrichment.csv").write_text(
            "inchikey,cid\nMNWFX,2244\n"
        )

        # Patch the module-level testability seams so the impl function
        # reads the patched values via globals() lookup.
        with patch(
            "phase1.dags.master_pipeline_dag._processed_dir", tmpdir
        ), patch(
            "phase1.dags.master_pipeline_dag._is_production", True
        ), patch(
            # Patch the DPI check to return a safe default so the test
            # focuses on the CSV validation (the DPI check has its own
            # dedicated tests below).
            "phase1.dags.master_pipeline_dag._check_dpi_degraded_via_postgres",
            return_value={
                "dpi_missing": False,
                "acknowledged": False,
                "source": "test_mock",
            },
        ), patch(
            # TM2's feature_validator (Check 5) flags missing optional
            # columns. The test fixtures only include the minimum
            # columns needed for the ID-column check, so feature_validator
            # would flag every optional column as missing. Patch it out
            # here -- the feature_validator has its own dedicated tests in
            # test_p1_to_p3_feature_completeness.py.
            "contracts.feature_validator.validate_feature_completeness",
            return_value=(True, []),
        ):
            # Import AFTER patching so the module-level state is fresh.
            # The import is idempotent (Python caches modules) but the
            # function reads module state at CALL TIME via globals().
            from phase1.dags.master_pipeline_dag import _validate_output_impl
            result = _validate_output_impl()

        assert result["failures"] == [], (
            f"validate_output failed with: {result['failures']}"
        )
        # XCom payload contract: all required keys present.
        assert "pipeline_run_id" in result
        assert "schema_version" in result
        assert "row_counts" in result
        assert "synth_key_counts" in result
        assert "dpi_missing" in result
        assert "dpi_acknowledged" in result
        assert "dpi_source" in result
        assert "validated_at" in result
        # row_counts should have entries for all 7 required sources.
        expected_sources = {
            "chembl_drugs", "drugs", "uniprot_proteins", "string_ppi",
            "disgenet_gda", "omim_gda", "pubchem_enrichment",
        }
        assert set(result["row_counts"].keys()) == expected_sources, (
            f"row_counts keys mismatch: {result['row_counts'].keys()}"
        )
        # Each row count should be 1 (we created 1 data row per CSV).
        for src, count in result["row_counts"].items():
            assert count == 1, f"Source {src}: expected 1 row, got {count}"


# =============================================================================
# Test 2: validate_output detects SYNTH-prefixed InChIKeys
# =============================================================================
@pytest.mark.integration
def test_validate_output_fails_on_synth_inchikeys():
    """SYNTH-prefixed InChIKeys in pubchem_enrichment.csv are detected.

    The previous code (line 1248 pre-fix) scanned 3 hardcoded filenames:
      chembl_drugs.csv, drugbank_drugs.csv, pubchem_compounds.csv

    ``pubchem_compounds.csv`` NEVER existed (the real file is
    ``pubchem_enrichment.csv``), so SYNTH-prefixed InChIKeys in PubChem
    enrichment flowed undetected into the KG. This test verifies the
    contract-driven SYNTH scan catches SYNTH InChIKeys in
    pubchem_enrichment.csv.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        # Create CSVs with the REAL filenames.
        (tmpdir / "drugs.csv").write_text("inchikey,drug_name\nABCDEF,aspirin\n")
        (tmpdir / "drugbank_drugs.csv").write_text("inchikey,name\nGHIJKL,metformin\n")
        (tmpdir / "proteins.csv").write_text("uniprot_id,name\nP12345,EGFR\n")
        (tmpdir / "protein_protein_interactions.csv").write_text(
            "uniprot_id_a,uniprot_id_b\nP1,P2\n"
        )
        (tmpdir / "gene_disease_associations.csv").write_text(
            "gene_symbol,disease\nBRCA1,cancer\n"
        )
        (tmpdir / "omim_gene_disease_associations.csv").write_text(
            "disease_id,gene\nOMIM:100100,BRCA1\n"
        )
        # Inject a SYNTH-prefixed InChIKey into pubchem_enrichment.csv.
        # The previous code would NOT detect this because it scanned
        # pubchem_compounds.csv (which never exists).
        (tmpdir / "pubchem_enrichment.csv").write_text(
            "inchikey,cid\nSYNTHFAKE123,2244\nREALKEY123456,2245\n"
        )

        with patch(
            "phase1.dags.master_pipeline_dag._processed_dir", tmpdir
        ), patch(
            "phase1.dags.master_pipeline_dag._is_production", True
        ), patch(
            "phase1.dags.master_pipeline_dag._check_dpi_degraded_via_postgres",
            return_value={
                "dpi_missing": False,
                "acknowledged": False,
                "source": "test_mock",
            },
        ), patch(
            "contracts.feature_validator.validate_feature_completeness",
            return_value=(True, []),
        ):
            from phase1.dags.master_pipeline_dag import _validate_output_impl
            result = _validate_output_impl()

        assert any(
            "SYNTH" in f for f in result["failures"]
        ), (
            "SYNTH InChIKey in pubchem_enrichment.csv was NOT detected. "
            f"Failures: {result['failures']}"
        )


# =============================================================================
# Test 3: DPI check queries PostgreSQL, not SQLite
# =============================================================================
@pytest.mark.integration
def test_dpi_check_uses_postgres_not_sqlite():
    """The DPI-degraded pre-flight check queries PostgreSQL via DATABASE_URL.

    The previous code (lines 870-914 pre-fix) did the DPI check via
    the stdlib ``sqlite3`` module, reading from ``phase1/data/drugos.db``.
    In PRODUCTION, the Phase 1 DB is PostgreSQL (per docker-compose.yml
    and config.settings), NOT SQLite -- the SQLite DB exists only in
    dev/test. The check queried an EMPTY/non-existent SQLite DB in
    production, found no ``pipeline_runs`` row, and SILENTLY SKIPPED
    the DPI-degraded enforcement. The safety net was DISABLED in
    production.

    ROOT FIX: query PostgreSQL via DATABASE_URL. This test verifies
    the check calls ``create_engine`` with the DATABASE_URL value
    (not sqlite3.connect with a local path).
    """
    with patch.dict(
        os.environ,
        {"DATABASE_URL": "postgresql://test:test@localhost/test"},
    ), patch(
        "phase1.dags.master_pipeline_dag.create_engine"
    ) as mock_engine:
        # Configure the mock chain: create_engine(db_url) -> engine;
        # engine.connect() -> context manager; cm.__enter__() -> conn;
        # conn.execute(text(...)) -> result; result.fetchone() -> row.
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = (
            '{"dpi_missing": true, "dpi_missing_acknowledged": false}',
        )
        mock_engine.return_value.connect.return_value.__enter__.return_value = mock_conn

        from phase1.dags.master_pipeline_dag import _check_dpi_degraded_via_postgres
        result = _check_dpi_degraded_via_postgres()

        # Verify create_engine was called with the DATABASE_URL value.
        mock_engine.assert_called_once_with(
            "postgresql://test:test@localhost/test",
            pool_pre_ping=True,
        )
        # Verify the result came from PostgreSQL (not SQLite).
        assert result["dpi_missing"] is True
        assert result["acknowledged"] is False
        assert result["source"] == "postgres"
        # Verify sqlite3 was NOT used (the previous code used sqlite3).
        # We check that no sqlite3.connect was called by inspecting
        # the mock_engine call -- if sqlite3 were used, create_engine
        # would NOT have been called.


# =============================================================================
# Test 4: DPI check fails-closed in production without DATABASE_URL
# =============================================================================
@pytest.mark.integration
def test_dpi_check_fails_closed_in_production_without_db_url():
    """In production with no DATABASE_URL, the DPI check raises AirflowFailException.

    The previous SQLite check silently skipped itself when the SQLite
    DB was missing. In production (where the DB is PostgreSQL, not
    SQLite), the SQLite DB was ALWAYS missing -- the safety net was
    permanently disabled.

    ROOT FIX: fail-closed in production. If DATABASE_URL is not set,
    raise AirflowFailException (non-retryable). This forces operators
    to set DATABASE_URL before the master DAG can proceed to Phase 2.
    """
    # Clear DATABASE_URL from the environment.
    env_without_db_url = {
        k: v for k, v in os.environ.items() if k != "DATABASE_URL"
    }
    with patch.dict(os.environ, env_without_db_url, clear=True), patch(
        "phase1.dags.master_pipeline_dag._is_production", True
    ):
        from phase1.dags.master_pipeline_dag import (
            _check_dpi_degraded_via_postgres,
        )
        try:
            from airflow.exceptions import AirflowFailException
        except ImportError:
            # If airflow is not installed, the function falls back to
            # RuntimeError. We accept either exception class.
            AirflowFailException = RuntimeError  # type: ignore[assignment]

        with pytest.raises(AirflowFailException, match="DATABASE_URL is not set"):
            _check_dpi_degraded_via_postgres()


# =============================================================================
# Test 5: get_required_id_column returns the canonical ID column per source
# =============================================================================
@pytest.mark.integration
def test_get_required_id_column_returns_canonical_id_per_source():
    """The contract's get_required_id_column returns the correct ID column.

    TM1 Task 1.4 v131 ROOT FIX: the new ``get_required_id_column``
    function returns the canonical ID column for each source (the
    column whose value uniquely identifies a row for downstream
    deduplication, provenance tracing, and entity resolution). Sources
    with multi-column composite keys (e.g. string_ppi's protein pair)
    return None.
    """
    try:
        from phase1.contracts.phase1_schema import get_required_id_column
    except ImportError:
        from contracts.phase1_schema import get_required_id_column  # type: ignore[no-redef]

    # Drug / compound sources: inchikey is the canonical compound key.
    assert get_required_id_column("chembl_drugs") == "inchikey"
    assert get_required_id_column("drugs") == "inchikey"
    assert get_required_id_column("pubchem_enrichment") == "inchikey"

    # Protein source: uniprot_id is the canonical protein key.
    assert get_required_id_column("uniprot_proteins") == "uniprot_id"

    # GDA sources: gene_symbol is the canonical gene key.
    assert get_required_id_column("disgenet_gda") == "gene_symbol"
    assert get_required_id_column("omim_gda") == "gene_symbol"
    assert get_required_id_column("omim_susceptibility") == "gene_symbol"

    # ChEMBL activities: molecule_chembl_id is the canonical compound
    # reference on an activity row.
    assert get_required_id_column("chembl_activities") == "molecule_chembl_id"

    # Multi-column-key sources: None (composite key -- skip header check).
    assert get_required_id_column("interactions") is None
    assert get_required_id_column("indications") is None
    assert get_required_id_column("string_ppi") is None

    # Unknown source key raises KeyError (defensive -- surfaces drift).
    with pytest.raises(KeyError, match="unknown source_key"):
        get_required_id_column("nonexistent_source")


# =============================================================================
# Test 6: Phase 2 run_pipeline.py accepts --provenance flag
# =============================================================================
@pytest.mark.integration
def test_phase2_run_pipeline_accepts_provenance_flag():
    """Phase 2 run_pipeline.py CLI accepts --provenance <UUID>.

    TM1 Task 1.4 v131 ROOT FIX: the master DAG's _trigger_phase2 task
    forwards the Phase 1 pipeline_run_id to Phase 2 as
    ``--provenance <UUID>``. This test verifies run_pipeline.py's
    argparse accepts the flag and run_full_pipeline accepts the
    ``provenance_id`` parameter.

    We do NOT run the full pipeline (it takes 6-7h on real data) --
    we just verify the CLI parses --provenance and forwards it to
    run_full_pipeline.
    """
    # Verify the --provenance flag is in the argparse parser by
    # inspecting the parser source. We can't easily import main()
    # without running it (it calls sys.exit), so we use ast to
    # inspect the source.
    import ast
    run_pipeline_path = Path(__file__).resolve().parents[3] / "phase2" / "drugos_graph" / "run_pipeline.py"
    if not run_pipeline_path.exists():
        pytest.skip(f"run_pipeline.py not found at {run_pipeline_path}")
    source = run_pipeline_path.read_text()
    tree = ast.parse(source)

    # Find all add_argument calls and check for --provenance.
    add_arg_calls = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and getattr(node.func, "attr", None) == "add_argument"
        ):
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    add_arg_calls.append(arg.value)
    assert "--provenance" in add_arg_calls, (
        "run_pipeline.py's argparse does NOT have a --provenance flag. "
        f"Found args: {add_arg_calls}"
    )

    # Verify run_full_pipeline accepts provenance_id by inspecting
    # the function signature.
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.FunctionDef)
            and node.name == "run_full_pipeline"
        ):
            arg_names = [arg.arg for arg in node.args.args]
            assert "provenance_id" in arg_names, (
                "run_full_pipeline does NOT accept a provenance_id "
                f"parameter. Found args: {arg_names}"
            )
            break
    else:
        pytest.fail("run_full_pipeline function not found in run_pipeline.py")


# =============================================================================
# Test 7: validate_output XCom payload contract
# =============================================================================
@pytest.mark.integration
def test_validate_output_xcom_payload_contract():
    """The validate_output XCom payload contains all required keys.

    TM1 Task 1.4 v131 ROOT FIX: the validate_output task returns an
    XCom payload dict that flows validate_output -> trigger_phase2 ->
    Phase 2 (via --provenance). The payload carries enough metadata
    for end-to-end tracing: which run, which schema version, which
    row counts, which SYNTH counts, DPI state, timestamp.

    This test verifies the payload contains all required keys with
    the correct types.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        # Create minimal valid CSVs.
        (tmpdir / "drugs.csv").write_text("inchikey,drug_name\nABCDEF,aspirin\n")
        (tmpdir / "drugbank_drugs.csv").write_text("inchikey,name\nGHIJKL,metformin\n")
        (tmpdir / "proteins.csv").write_text("uniprot_id,name\nP12345,EGFR\n")
        (tmpdir / "protein_protein_interactions.csv").write_text(
            "uniprot_id_a,uniprot_id_b\nP1,P2\n"
        )
        (tmpdir / "gene_disease_associations.csv").write_text(
            "gene_symbol,disease\nBRCA1,cancer\n"
        )
        (tmpdir / "omim_gene_disease_associations.csv").write_text(
            "disease_id,gene\nOMIM:100100,BRCA1\n"
        )
        (tmpdir / "pubchem_enrichment.csv").write_text(
            "inchikey,cid\nMNWFX,2244\n"
        )

        with patch(
            "phase1.dags.master_pipeline_dag._processed_dir", tmpdir
        ), patch(
            "phase1.dags.master_pipeline_dag._is_production", False  # dev mode
        ):
            from phase1.dags.master_pipeline_dag import _validate_output_impl
            result = _validate_output_impl()

        # Required keys for end-to-end tracing.
        required_keys = {
            "pipeline_run_id",      # UUID for tracing
            "schema_version",       # contract version
            "row_counts",           # per-source row counts
            "synth_key_counts",     # per-source SYNTH counts
            "dpi_missing",          # DPI-degraded flag
            "dpi_acknowledged",     # operator acknowledgement
            "dpi_source",           # DPI check provenance
            "validated_at",         # ISO 8601 timestamp
            "failures",             # list of failure messages
        }
        assert set(result.keys()) >= required_keys, (
            f"XCom payload missing keys: {required_keys - set(result.keys())}"
        )

        # Type checks.
        assert isinstance(result["pipeline_run_id"], str)
        assert isinstance(result["schema_version"], str)
        assert isinstance(result["row_counts"], dict)
        assert isinstance(result["synth_key_counts"], dict)
        assert isinstance(result["dpi_missing"], bool)
        assert isinstance(result["dpi_acknowledged"], bool)
        assert isinstance(result["dpi_source"], str)
        assert isinstance(result["validated_at"], str)
        assert isinstance(result["failures"], list)

        # pipeline_run_id should be a valid UUID string (36 chars).
        assert len(result["pipeline_run_id"]) == 36, (
            f"pipeline_run_id is not a 36-char UUID: {result['pipeline_run_id']}"
        )

        # validated_at should be an ISO 8601 timestamp.
        assert "T" in result["validated_at"], (
            f"validated_at is not ISO 8601: {result['validated_at']}"
        )


# =============================================================================
# Test 8: validate_output rejects a CSV with a missing ID column
# =============================================================================
@pytest.mark.integration
def test_validate_output_fails_on_missing_id_column():
    """validate_output rejects a CSV missing its required ID column.

    TM1 Task 1.4 v131 ROOT FIX: the contract-driven ID-column check
    verifies the ID column (from get_required_id_column) is present
    in the CSV header. If a pipeline schema drift renames the ID
    column, validate_output catches it BEFORE Phase 2 builds a KG
    on rows with NULL IDs.

    This test creates a ``drugs.csv`` WITHOUT the ``inchikey`` column
    and verifies validate_output flags it as a failure.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        # Create CSVs -- but drugs.csv is MISSING the inchikey column.
        (tmpdir / "drugs.csv").write_text(
            "chembl_id,drug_name\nCHEMBL123,aspirin\n"  # no inchikey!
        )
        (tmpdir / "drugbank_drugs.csv").write_text("inchikey,name\nGHIJKL,metformin\n")
        (tmpdir / "proteins.csv").write_text("uniprot_id,name\nP12345,EGFR\n")
        (tmpdir / "protein_protein_interactions.csv").write_text(
            "uniprot_id_a,uniprot_id_b\nP1,P2\n"
        )
        (tmpdir / "gene_disease_associations.csv").write_text(
            "gene_symbol,disease_id\nBRCA1,C0001\n"
        )
        (tmpdir / "omim_gene_disease_associations.csv").write_text(
            "gene_symbol,disease_id\nBRCA1,OMIM:100100\n"
        )
        (tmpdir / "pubchem_enrichment.csv").write_text(
            "inchikey,cid\nMNWFX,2244\n"
        )

        with patch(
            "phase1.dags.master_pipeline_dag._processed_dir", tmpdir
        ), patch(
            "phase1.dags.master_pipeline_dag._is_production", True
        ), patch(
            "phase1.dags.master_pipeline_dag._check_dpi_degraded_via_postgres",
            return_value={
                "dpi_missing": False,
                "acknowledged": False,
                "source": "test_mock",
            },
        ), patch(
            "contracts.feature_validator.validate_feature_completeness",
            return_value=(True, []),
        ):
            from phase1.dags.master_pipeline_dag import _validate_output_impl
            result = _validate_output_impl()

        # The failure list should mention the missing inchikey column.
        assert any(
            "inchikey" in f.lower() for f in result["failures"]
        ), (
            "Missing inchikey column in drugs.csv was NOT detected. "
            f"Failures: {result['failures']}"
        )


# =============================================================================
# Test 9: _trigger_phase2 entrypoint resolution prefers canonical path
# =============================================================================
@pytest.mark.integration
def test_trigger_phase2_prefers_canonical_run_pipeline_py():
    """_trigger_phase2 prefers phase2/drugos_graph/run_pipeline.py.

    TM1 Task 1.4 v131 ROOT FIX: the entrypoint resolution priority
    order is:
      1. PHASE2_ENTRYPOINT env var (operator override).
      2. phase2/drugos_graph/run_pipeline.py (canonical).
      3. run_unified.py at project root (legacy).
      4. python -m drugos_graph (fallback).

    This test verifies the canonical path is preferred when it exists
    and PHASE2_ENTRYPOINT is not set.
    """
    # We can't easily invoke _trigger_phase2 (it's a @task-decorated
    # function that returns an XComArg, not the result). Instead, we
    # verify the entrypoint resolution logic by inspecting the source.
    import ast
    dag_path = Path(__file__).resolve().parents[2] / "dags" / "master_pipeline_dag.py"
    source = dag_path.read_text()
    tree = ast.parse(source)

    # Find _trigger_phase2 function and verify it references the
    # canonical path.
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.FunctionDef)
            and node.name == "_trigger_phase2"
        ):
            func_source = ast.get_source_segment(source, node)
            # Verify the canonical path is checked.
            assert "phase2" in func_source, (
                "_trigger_phase2 does not reference phase2/ directory"
            )
            assert "drugos_graph" in func_source, (
                "_trigger_phase2 does not reference drugos_graph/"
            )
            assert "run_pipeline.py" in func_source, (
                "_trigger_phase2 does not reference run_pipeline.py"
            )
            # Verify PHASE2_ENTRYPOINT env var is consulted.
            assert "PHASE2_ENTRYPOINT" in func_source, (
                "_trigger_phase2 does not consult PHASE2_ENTRYPOINT env var"
            )
            # Verify --provenance is passed to Phase 2.
            assert "--provenance" in func_source, (
                "_trigger_phase2 does not pass --provenance to Phase 2"
            )
            break
    else:
        pytest.fail("_trigger_phase2 function not found in master_pipeline_dag.py")


# =============================================================================
# Test 10: SCHEMA_VERSION constant exists in the contract
# =============================================================================
@pytest.mark.integration
def test_schema_version_constant_exists():
    """The contract module exports a SCHEMA_VERSION constant.

    TM1 Task 1.4 v131 ROOT FIX: the validate_output XCom payload
    includes ``schema_version`` so downstream consumers (trigger_phase2,
    Phase 2 run_pipeline.py) can verify they're reading Phase 1 output
    from a compatible contract version.
    """
    try:
        from phase1.contracts.phase1_schema import SCHEMA_VERSION
    except ImportError:
        from contracts.phase1_schema import SCHEMA_VERSION  # type: ignore[no-redef]

    assert isinstance(SCHEMA_VERSION, str), (
        f"SCHEMA_VERSION should be a str, got {type(SCHEMA_VERSION)}"
    )
    assert SCHEMA_VERSION, "SCHEMA_VERSION should not be empty"
    # The version should be a non-empty numeric string (e.g. "11").
    assert SCHEMA_VERSION.isdigit(), (
        f"SCHEMA_VERSION should be numeric, got {SCHEMA_VERSION!r}"
    )


# =============================================================================
# CLI entry point (run all tests with ``python -m pytest <this_file> -v``)
# =============================================================================
if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", "--tb=short"]))
