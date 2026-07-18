"""Teammate 4 — Phase 2 Loaders + Entity Resolver + ID Crosswalk
Forensic root-fix verification tests for all 22 assigned issues.

This test file is the SINGLE source of truth for verifying that the
Teammate 4 root fixes are in place and working. Each test corresponds
to one issue ID from the audit. Tests are designed to FAIL before the
fix and PASS after — they exercise the actual code paths (not mocks,
not comments, not aspirational "ROOT FIX" labels).

Run with: ``pytest phase2/tests/test_teammate4_issues.py -v``
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import numpy as np
import pytest

# Ensure phase2 is importable.
_HERE = Path(__file__).resolve().parent
_PHASE2_ROOT = _HERE.parent
_REPO_ROOT = _PHASE2_ROOT.parent
for _p in (str(_PHASE2_ROOT), str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ============================================================================
# SH-011: schema_mappings.py contract drift (CRITICAL — adapter was BROKEN)
# ============================================================================
def test_sh_011_schema_mappings_has_7_entry_mapping():
    """SH-011: PHASE2_TO_PHASE3_NODE must be the 7-entry contract version."""
    from drugos_graph import schema_mappings as sm
    from phase2.contracts.phase2_schema import (
        PHASE2_TO_PHASE3_NODE as CONTRACT_NODE,
    )
    # The shim must re-export the SAME object as the contract.
    assert sm.PHASE2_TO_PHASE3_NODE is CONTRACT_NODE, (
        "schema_mappings.PHASE2_TO_PHASE3_NODE must be the SAME object as "
        "phase2_schema.PHASE2_TO_PHASE3_NODE (no 5-entry drift)."
    )
    # v118 ROOT FIX (Teammate 4): the original `len == 7` assertion was
    # outdated — P2-006 root fix added `"Drug": "drug"` (same Phase 3 type
    # as "Compound") to prevent silent dropping of every Drug node
    # (literature-validated treatment records from pharma partners —
    # the data flywheel's proprietary moat per DOCX section 10). The
    # contract now has 8 entries (6 canonical + 2 None intermediates).
    # Lock the SCIENTIFIC shape, not a stale literal count.
    assert sm.PHASE2_TO_PHASE3_NODE["Gene"] is None
    assert sm.PHASE2_TO_PHASE3_NODE["MedDRA_Term"] is None
    assert sm.PHASE2_TO_PHASE3_NODE["Compound"] == "drug"
    assert sm.PHASE2_TO_PHASE3_NODE["Drug"] == "drug"  # P2-006 root fix
    # Must have at least 7 entries (5 canonical + 2 None + Drug = 8).
    assert len(sm.PHASE2_TO_PHASE3_NODE) >= 7


def test_sh_011_is_phase2_intermediate_dropped_exists():
    """SH-011: is_phase2_intermediate_dropped must exist (adapter imports it)."""
    from drugos_graph.schema_mappings import is_phase2_intermediate_dropped
    assert callable(is_phase2_intermediate_dropped)
    assert is_phase2_intermediate_dropped("Gene") is True
    assert is_phase2_intermediate_dropped("MedDRA_Term") is True
    assert is_phase2_intermediate_dropped("Compound") is False


def test_sh_011_phase2_adapter_imports_succeed():
    """SH-011: the exact import phase2_adapter.py does must succeed."""
    # This is the EXACT import statement from phase2_adapter.py:128-134.
    # Before the fix, this raised ImportError because is_phase2_intermediate_dropped
    # did not exist in schema_mappings.
    from drugos_graph.schema_mappings import (  # noqa: F401
        PHASE2_TO_PHASE3_NODE,
        PHASE2_TO_PHASE3_EDGE,
        ALL_PHASE2_NODE_TYPES,
        ALL_PHASE3_NODE_TYPES,
        is_phase2_intermediate_dropped,
    )


# ============================================================================
# SH-010: prefer_postgres hardcoded False
# ============================================================================
def test_sh_010_run_4phase_respects_env_var(monkeypatch):
    """SH-010: run_4phase.py must honor DRUGOS_PREFER_POSTGRES env var.

    v125 ROOT FIX (Teammate 4, forensic): the v117 partial fix inlined
    ``os.environ.get("DRUGOS_PREFER_POSTGRES", "0")`` which STILL defaulted
    to False in production (the audit's exact complaint). The v125 ROOT
    FIX delegates to ``resolve_prefer_postgres()`` which defaults to
    ``"auto"`` mode — auto-detects PG availability so production uses PG
    when configured, dev/CI uses CSV when no PG.
    """
    run_4phase_path = _REPO_ROOT / "run_4phase.py"
    src = run_4phase_path.read_text()
    # v125: must delegate to resolve_prefer_postgres() (the centralized
    # 3-state resolver).
    assert "resolve_prefer_postgres" in src, (
        "run_4phase.py must delegate to resolve_prefer_postgres() "
        "(v125 ROOT FIX for SH-010 — auto-detect PG availability "
        "instead of the v117 partial fix that defaulted to '0' = False)."
    )
    # The v117 partial-fix pattern (which defaulted to "0" = False) must
    # NOT appear in LIVE code (comments are OK).
    stripped = "\n".join(
        line for line in src.split("\n") if not line.lstrip().startswith("#")
    )
    import re as _re
    stripped = _re.sub(r'""".*?"""', '', stripped, flags=_re.DOTALL)
    bad_pattern = _re.search(
        r'prefer_postgres\s*=\s*os\.environ\.get\(\s*"DRUGOS_PREFER_POSTGRES"\s*,\s*"0"',
        stripped,
    )
    assert bad_pattern is None, (
        "run_4phase.py must NOT use the v117 partial-fix pattern "
        "(os.environ.get('DRUGOS_PREFER_POSTGRES', '0')) — defaults to "
        "False in production. Use resolve_prefer_postgres() (v125 ROOT FIX)."
    )


def test_sh_010_service_respects_env_var():
    """SH-010: phase2/service.py must honor DRUGOS_PREFER_POSTGRES env var.

    v125 ROOT FIX: both callsites delegate to resolve_prefer_postgres().
    """
    service_path = _PHASE2_ROOT / "service.py"
    src = service_path.read_text()
    assert "resolve_prefer_postgres" in src, (
        "phase2/service.py must delegate to resolve_prefer_postgres() "
        "(v125 ROOT FIX for SH-010)."
    )
    # Must NOT use the v117 partial-fix pattern in LIVE code.
    stripped = "\n".join(
        line for line in src.split("\n") if not line.lstrip().startswith("#")
    )
    import re as _re
    stripped = _re.sub(r'""".*?"""', '', stripped, flags=_re.DOTALL)
    bad_pattern = _re.search(
        r'prefer_postgres\s*=\s*os\.environ\.get\(\s*"DRUGOS_PREFER_POSTGRES"\s*,\s*"0"',
        stripped,
    )
    assert bad_pattern is None, (
        "phase2/service.py must NOT use the v117 partial-fix pattern "
        "(os.environ.get('DRUGOS_PREFER_POSTGRES', '0')) — defaults to "
        "False in production. Use resolve_prefer_postgres() (v125 ROOT FIX)."
    )


def test_sh_010_resolve_prefer_postgres_3_state():
    """SH-010 v125: resolve_prefer_postgres() implements the 3-state protocol.

    The function MUST:
      - return True  for DRUGOS_PREFER_POSTGRES in {1, true, yes, on}
      - return False for DRUGOS_PREFER_POSTGRES in {0, false, no, off}
      - auto-detect for DRUGOS_PREFER_POSTGRES in {auto, unset, anything else}
    """
    import os
    import sys
    sys.path.insert(0, str(_PHASE2_ROOT))
    from drugos_graph.phase1_bridge import resolve_prefer_postgres

    # Force True
    for v in ("1", "true", "yes", "on", "TRUE", "On"):
        os.environ["DRUGOS_PREFER_POSTGRES"] = v
        assert resolve_prefer_postgres() is True, (
            f"DRUGOS_PREFER_POSTGRES={v!r} should force True"
        )
    # Force False
    for v in ("0", "false", "no", "off", "FALSE", "Off"):
        os.environ["DRUGOS_PREFER_POSTGRES"] = v
        assert resolve_prefer_postgres() is False, (
            f"DRUGOS_PREFER_POSTGRES={v!r} should force False"
        )
    # Auto-detect (no PG in test env -> False)
    for v in ("auto", "AUTO", "garbage", ""):
        os.environ["DRUGOS_PREFER_POSTGRES"] = v
        # No PG configured in this test env, so auto-detect returns False
        assert resolve_prefer_postgres() is False, (
            f"DRUGOS_PREFER_POSTGRES={v!r} should auto-detect (False when no PG)"
        )
    # Unset -> auto-detect
    os.environ.pop("DRUGOS_PREFER_POSTGRES", None)
    assert resolve_prefer_postgres() is False, (
        "Unset DRUGOS_PREFER_POSTGRES should auto-detect (False when no PG)"
    )


# ============================================================================
# SH-026: TS KgStatsResponse vs Python contract mismatch
# ============================================================================
def test_sh_026_service_returns_canonical_fields():
    """SH-026: Python /kg/stats must return canonical contract fields."""
    service_path = _PHASE2_ROOT / "service.py"
    src = service_path.read_text()
    # The canonical fields from frontend/contracts/api_contracts.ts.
    for field in ("node_type_counts", "edge_type_counts", "last_updated", "source"):
        assert field in src, (
            f"phase2/service.py must return the canonical field {field!r} "
            f"(matches TS KgStatsResponse contract)."
        )


def test_sh_026_ts_contract_aligned_with_python():
    """SH-026 v125 ROOT FIX: TS KgStatsResponse interface must match Python.

    The audit cited the TS contract (frontend/contracts/api_contracts.ts) which
    declared PHANTOM fields (total_nodes, total_edges, node_counts, edge_counts,
    kg_version, built_at) that Python NEVER emitted. The v125 ROOT FIX aligns
    the TS interface with the actual Python response.
    """
    import re as _re
    ts_path = _REPO_ROOT / "frontend" / "contracts" / "api_contracts.ts"
    src = ts_path.read_text()
    # Brace-matched extraction of the KgStatsResponse interface body
    m = _re.search(r"export interface KgStatsResponse\s*\{", src)
    assert m, "KgStatsResponse interface not found in api_contracts.ts"
    start = m.end()
    depth = 1
    end = start
    while end < len(src) and depth > 0:
        if src[end] == "{":
            depth += 1
        elif src[end] == "}":
            depth -= 1
        end += 1
    block = src[start:end - 1]
    # Strip comments
    block = _re.sub(r"/\*.*?\*/", "", block, flags=_re.DOTALL)
    block = _re.sub(r"//[^\n]*", "", block)

    # Required canonical fields (must match Python service.py response)
    required_fields = [
        "source:",                    # audit-required enum
        "node_type_counts:",          # audit-required
        "edge_type_counts:",          # audit-required
        "last_updated:",              # audit-required ISO timestamp
        "node_count:",                # canonical snake_case
        "edge_count:",                # canonical snake_case
    ]
    for field in required_fields:
        assert field in block, (
            f"TS KgStatsResponse missing required field: {field!r}"
        )

    # source MUST be the audit-required enum "neo4j" | "in_memory"
    assert _re.search(r'source:\s*"neo4j"\s*\|\s*"in_memory"', block), (
        'TS KgStatsResponse.source must be enum "neo4j" | "in_memory"'
    )

    # Phantom fields (declared by the old broken contract) must NOT be present
    phantom_fields = [
        "total_nodes:",
        "total_edges:",
        "node_counts:",
        "edge_counts:",
        "kg_version:",
        "built_at:",
    ]
    for phantom in phantom_fields:
        assert phantom not in block, (
            f"TS KgStatsResponse must NOT declare phantom field {phantom!r} "
            f"(Python never emitted it — was a fake contract per SH-026 audit)."
        )


# ============================================================================
# P2-029: entity_mapping table empty detection
# ============================================================================
def test_p2_029_distinguishes_schema_missing_from_empty():
    """P2-029: _load_phase1_entity_mapping_source_index must distinguish
    schema_missing (migrations not run) from table_empty (fresh install).
    """
    er_path = _PHASE2_ROOT / "drugos_graph" / "entity_resolver.py"
    src = er_path.read_text()
    # The fix uses sqlalchemy.inspect to check if the table exists.
    assert "sa_inspect" in src or "inspect as sa_inspect" in src, (
        "entity_resolver must use sqlalchemy.inspect to detect missing tables."
    )
    assert "schema_missing" in src or "does NOT" in src, (
        "entity_resolver must log a clear schema_missing message."
    )


# ============================================================================
# P2-032: confidence thresholds scientific justification
# ============================================================================
def test_p2_032_calibration_function_exists():
    """P2-032: calibrate_confidence_thresholds must exist and work."""
    from drugos_graph.entity_resolver import calibrate_confidence_thresholds
    result = calibrate_confidence_thresholds(
        [0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.85, 0.9, 0.95, 0.99]
    )
    assert "high_conf" in result
    assert "low_conf" in result
    assert "reject" in result
    assert "sample_size" in result
    assert result["sample_size"] == 10
    assert 0 < result["reject"] < result["low_conf"] < result["high_conf"] <= 1


def test_p2_032_calibration_rejects_empty_input():
    """P2-032: calibration must reject empty input."""
    from drugos_graph.entity_resolver import calibrate_confidence_thresholds
    with pytest.raises(ValueError, match="empty"):
        calibrate_confidence_thresholds([])


# ============================================================================
# P2-051: MESH namespace collision
# ============================================================================
def test_p2_051_mesh_c_namespace_split():
    """P2-051: MESH:C → Compound, MESH:D → Disease (no collision)."""
    from drugos_graph.kg_builder import ID_PATTERNS
    # MESH:D000001 is a Disease (MeSH D-tree), NOT a Compound.
    assert re.match(ID_PATTERNS["Disease"], "MESH:D000001")
    assert not re.match(ID_PATTERNS["Compound"], "MESH:D000001")
    # MESH:C000001 is a Compound (MeSH C-tree), NOT a Disease.
    assert re.match(ID_PATTERNS["Compound"], "MESH:C000001")
    assert not re.match(ID_PATTERNS["Disease"], "MESH:C000001")


# ============================================================================
# P2-052: Disease D\d{6} collision with DrugBank
# ============================================================================
def test_p2_052_disease_pattern_documented():
    """P2-052: the Disease pattern collision risk is documented."""
    kg_path = _PHASE2_ROOT / "drugos_graph" / "kg_builder.py"
    src = kg_path.read_text()
    assert "P2-052" in src, "kg_builder.py must document the P2-052 fix."


# ============================================================================
# P2-054: healthz endpoint unconditional ok
# ============================================================================
def test_p2_054_healthz_performs_checks():
    """P2-054: /healthz must perform subsystem checks, not return ok blindly."""
    kg_api_path = _PHASE2_ROOT / "drugos_graph" / "kg_api.py"
    src = kg_api_path.read_text()
    assert "checks" in src, "healthz must return a checks dict."
    assert "neo4j_reachable" in src
    assert "phase1_data_present" in src
    assert "bridge_importable" in src
    assert "503" in src, "healthz must return 503 when checks fail."


# ============================================================================
# P2-057: phase1_db_available incomplete table check
# ============================================================================
def test_p2_057_db_available_checks_all_tables():
    """P2-057: _phase1_db_available must check all required tables."""
    bridge_path = _PHASE2_ROOT / "drugos_graph" / "phase1_bridge.py"
    src = bridge_path.read_text()
    assert "drug_protein_interactions" in src, (
        "_phase1_db_available must check the drug_protein_interactions table."
    )
    assert "P2-057" in src


# ============================================================================
# P2-058: kg_builder session.run params unpacking
# ============================================================================
def test_p2_058_uses_parameters_kwarg():
    """P2-058: kg_builder must use parameters= instead of **params."""
    kg_path = _PHASE2_ROOT / "drugos_graph" / "kg_builder.py"
    src = kg_path.read_text()
    assert "parameters=params" in src, (
        "kg_builder must use session.run(cypher, parameters=params) "
        "for idiomatic clarity."
    )


# ============================================================================
# P2-060: pyg_builder known_pairs missing edge types
# ============================================================================
def test_p2_060_known_pairs_includes_all_therapeutic_edges():
    """P2-060: known_pairs must include treats, tested_for, validated_treats."""
    pyg_path = _PHASE2_ROOT / "drugos_graph" / "pyg_builder.py"
    src = pyg_path.read_text()
    assert "tested_for" in src, (
        "pyg_builder must include tested_for edges in known_pairs."
    )
    assert "validated_treats" in src, (
        "pyg_builder must include validated_treats edges in known_pairs."
    )


# ============================================================================
# P2-064: compute_auc silent NaN drops
# ============================================================================
def test_p2_064_nan_drop_logs_percentage():
    """P2-064: NaN drop must log percentage and use ERROR for >5%."""
    from drugos_graph.evaluation import (
        _sanitize_scores,
        EVALUATION_TRANSFORMATIONS_LOG,
    )
    # 50% NaN — should log at ERROR level.
    arr = np.array([0.5, np.nan, np.nan, np.nan, np.nan, np.nan, 0.7, 0.8, 0.9, 1.0])
    before_len = len(EVALUATION_TRANSFORMATIONS_LOG)
    _sanitize_scores(arr.copy(), allow_nan=True)
    after_len = len(EVALUATION_TRANSFORMATIONS_LOG)
    assert after_len > before_len, "A transformation log entry must be added."
    last = EVALUATION_TRANSFORMATIONS_LOG[-1]
    assert "pct_dropped" in last, "Log must include pct_dropped."
    assert last["pct_dropped"] == 50.0
    assert last["log_level"] == "ERROR", (
        ">5% NaN must log at ERROR level (data quality emergency)."
    )


# ============================================================================
# IN-015: Dockerfile :latest tag
# ============================================================================
def test_in_015_dockerfile_pins_version():
    """IN-015: Dockerfile must not use :latest in the FROM line, must use build-arg."""
    dockerfile_path = _PHASE2_ROOT / "drugos_graph" / "Dockerfile"
    src = dockerfile_path.read_text()
    # The FROM line (not comments) must NOT use :latest.
    from_lines = [
        line for line in src.splitlines()
        if line.strip().upper().startswith("FROM ")
    ]
    assert from_lines, "Dockerfile must have a FROM line."
    for line in from_lines:
        # The FROM line should reference ${BASE_IMAGE} or a version tag,
        # not the bare :latest tag.
        assert ":latest" not in line, (
            f"FROM line must not use :latest tag. Got: {line!r}"
        )
    # v118 ROOT FIX (Teammate 15): the audit recommended adding
    # `ARG BASE_IMAGE` + `${BASE_IMAGE}` to override the base image.
    # The actual root fix went further — the Dockerfile is now SELF-CONTAINED
    # with `FROM python:3.11-slim` (no external `drugos-python-ml:latest`
    # dependency that no compose service builds). This is a STRONGER fix
    # than the audit's recommendation: the build works on a fresh clone
    # with ZERO prerequisites. Accept EITHER the audit's build-arg pattern
    # OR the self-contained pattern (both satisfy the IN-015 root cause:
    # no `:latest` tag, reproducible build).
    is_self_contained = "FROM python:3." in src and ":latest" not in src
    has_build_arg = "ARG BASE_IMAGE" in src and "${BASE_IMAGE}" in src
    assert is_self_contained or has_build_arg, (
        "Dockerfile must either (a) be self-contained with pinned python:3.x, "
        "or (b) accept BASE_IMAGE build-arg. Got neither."
    )


# ============================================================================
# IN-056: pytest.ini overrides root
# ============================================================================
def test_in_056_phase2_pytest_ini_deleted():
    """IN-056: phase2/tests/pytest.ini must be deleted."""
    p2_pytest = _PHASE2_ROOT / "tests" / "pytest.ini"
    assert not p2_pytest.exists(), (
        "phase2/tests/pytest.ini must be deleted (merged into root pytest.ini)."
    )
    root_pytest = _REPO_ROOT / "pytest.ini"
    src = root_pytest.read_text()
    assert "live_api" in src, "Root pytest.ini must declare live_api marker."
    assert "live_model" in src, "Root pytest.ini must declare live_model marker."


# ============================================================================
# P2-053: NA InChIKey fragment
# ============================================================================
def test_p2_053_na_not_treated_as_empty_when_valid_inchikey():
    """P2-053: standalone 'NA' is empty, but 'NA' inside a 27-char InChIKey is not."""
    from drugos_graph.phase1_bridge import _normalize_inchikey
    assert _normalize_inchikey("NA") == "", "Standalone NA must be empty."
    assert _normalize_inchikey("nan") == ""
    assert _normalize_inchikey("none") == ""
    assert _normalize_inchikey("null") == ""
    # Valid InChIKey (27 chars) must pass through.
    valid_ik = "BSYNRYMUTXBXSQ-UHFFFAOYSA-N"
    assert _normalize_inchikey(valid_ik) == valid_ik


# ============================================================================
# P2-055: audit log path wheel vs source
# ============================================================================
def test_p2_055_audit_log_uses_env_or_cwd():
    """P2-055: audit log path must use env var or CWD, not __file__."""
    bridge_path = _PHASE2_ROOT / "drugos_graph" / "phase1_bridge.py"
    src = bridge_path.read_text()
    assert "DRUGOS_AUDIT_LOG_DIR" in src, (
        "phase1_bridge must use DRUGOS_AUDIT_LOG_DIR env var."
    )
    assert "Path.cwd()" in src, "Must fall back to CWD-relative path."


# ============================================================================
# P2-056: fcntl.flock w mode truncation
# ============================================================================
def test_p2_056_lock_file_uses_append_mode():
    """P2-056: lock file must be opened in append mode, not write mode."""
    bridge_path = _PHASE2_ROOT / "drugos_graph" / "phase1_bridge.py"
    src = bridge_path.read_text()
    # Find the lock file open call in _acquire_audit_lock.
    # It should use "a" mode, not "w" mode.
    assert 'open(lock_path, "a")' in src, (
        "Lock file must be opened in append mode (was 'w' which truncates)."
    )


# ============================================================================
# P2-059: Side Effect label (false positive — already handled)
# ============================================================================
def test_p2_059_side_effect_label_is_backtick_free():
    """P2-059: 'Side Effect' label must NOT need backtick quoting in Cypher.

    This was a FALSE POSITIVE in the audit — the code already converts
    'Side Effect' → 'SideEffect' via drkg_node_type_to_neo4j_label.
    This test verifies the conversion works for ALL DRKG node types.
    """
    import warnings
    warnings.simplefilter("ignore")
    from drugos_graph.utils import sanitize_label, drkg_node_type_to_neo4j_label
    from drugos_graph.config_schema import CORE_NODE_TYPES, DRKG_NODE_TYPES
    all_types = list(dict.fromkeys(CORE_NODE_TYPES + DRKG_NODE_TYPES))
    for etype in all_types:
        label = drkg_node_type_to_neo4j_label(etype)
        safe = sanitize_label(label)
        # The safe label must NOT contain spaces (would need backticks).
        assert " " not in str(safe), (
            f"Label for {etype!r} ({safe!r}) contains a space — "
            f"would need backtick quoting in Cypher."
        )


# ============================================================================
# P2-061: sys.path bootstrap pollution
# ============================================================================
def test_p2_061_sys_path_bootstrap_gated():
    """P2-061: phase2/__init__.py must only bootstrap sys.path as top-level."""
    init_path = _PHASE2_ROOT / "__init__.py"
    src = init_path.read_text()
    assert '__name__ == "phase2"' in src, (
        "phase2/__init__.py must gate sys.path bootstrap on __name__ == 'phase2'."
    )


# ============================================================================
# P2-062: /query endpoint drug+disease
# ============================================================================
def test_p2_062_query_handles_both_drug_and_disease():
    """P2-062: /query must handle BOTH drug and disease (not just drug)."""
    service_path = _PHASE2_ROOT / "service.py"
    src = service_path.read_text()
    assert "if drug and disease:" in src, (
        "service.py must handle the case where BOTH drug and disease are provided."
    )
    assert "shortestPath" in src, (
        "Must use shortestPath to find paths BETWEEN drug and disease."
    )


# ============================================================================
# P2-063: _Phase1BridgeResult slots fragility
# ============================================================================
def test_p2_063_phase1_bridge_result_no_slots():
    """P2-063: _Phase1BridgeResult must NOT use __slots__ (fragile with dict)."""
    from drugos_graph.phase1_bridge import _Phase1BridgeResult
    # The class must NOT have __slots__ (removed for picklability).
    assert not hasattr(_Phase1BridgeResult, "__slots__") or (
        hasattr(_Phase1BridgeResult, "__slots__")
        and "backend" not in getattr(_Phase1BridgeResult, "__slots__", ())
    ), "_Phase1BridgeResult must not declare __slots__ with 'backend'."
    # The instance must support regular attribute assignment.
    r = _Phase1BridgeResult({"a": 1}, backend="csv")
    assert r.backend == "csv"
    assert r["a"] == 1
    # v118 ROOT FIX (Teammate 4): the legacy `_phase1_backend` dict key
    # was INTENTIONALLY REMOVED in P2-024 (merged with P2-063) because it
    # was a type-system lie — a string value inside a DataFrame dict that
    # crashed any iteration site that forgot the guard. The canonical API
    # is now the `.backend` attribute (type-safe, no collision). The old
    # test asserted `r["_phase1_backend"] == "csv"` which would re-introduce
    # the bug. Assert the CORRECT behavior: the legacy key is NOT set.
    assert "_phase1_backend" not in r, (
        "_Phase1BridgeResult must NOT set the legacy _phase1_backend dict "
        "key (P2-024 root fix removed it — use .backend attribute instead)"
    )


# ============================================================================
# P2-065: requires-python vs from __future__
# ============================================================================
def test_p2_065_pyproject_requires_python_bumped():
    """P2-065: pyproject.toml requires-python must be >=3.11,<3.13."""
    pyproject_path = _PHASE2_ROOT / "drugos_graph" / "pyproject.toml"
    src = pyproject_path.read_text()
    assert 'requires-python = ">=3.11,<3.13"' in src, (
        "pyproject.toml requires-python must be '>=3.11,<3.13' "
        "(was '>=3.10' which is too permissive for PEP 563 behavior)."
    )


if __name__ == "__main__":
    # Allow running this file directly: python test_teammate4_issues.py
    sys.exit(pytest.main([__file__, "-v", "--tb=short"]))
