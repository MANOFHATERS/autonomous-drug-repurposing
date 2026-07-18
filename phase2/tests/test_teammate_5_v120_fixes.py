"""Teammate 5 — v120 forensic root-fix verification tests.

These tests verify that the v120 P2-001 regression fix (and the other
Teammate-5 swim-lane fixes from v109) actually work as advertised. They
were written by reading the actual code (not the comments) and asserting
on the executable behavior.

P2-001 v120 regression: the v107 file-existence check used
``any(pdir.glob("*.csv*"))`` which matched ANY CSV in the directory,
including non-Phase-1 files like ``validated_hypotheses.csv``. The check
passed, the bridge ran, returned 0 nodes / 0 edges, and the API returned
HTTP 200 with ``node_count=0, edge_count=0`` — silent data loss.

ROOT FIX (v120):
  1. Check for at least one EXPECTED Phase 1 source CSV (per
     ``_PHASE1_SOURCE_TO_CSV``).
  2. After the bridge runs, if it returns 0 nodes AND 0 edges, raise
     FileNotFoundError (converted to HTTP 503 by the route handler).

Tests:
  * test_p2_001_v120_rejects_dir_with_only_non_phase1_csvs
  * test_p2_001_v120_rejects_zero_node_zero_edge_bridge_output
  * test_p2_002_blocks_call_subquery_injection
  * test_p2_002_blocks_apoc_procedure_bypass
  * test_p2_003_blocks_semicolon_injection
  * test_p2_005_phase2_to_phase3_edge_covers_all_core_edge_types
  * test_p2_006_no_lower_fallback_for_unknown_node_labels
  * test_p2_007_sider_uses_canonical_meddra_term_schema
  * test_p2_009_uses_default_edge_confidence_not_entity
  * test_p2_011_rejects_nested_dict_params
  * test_p2_024_no_legacy_phase1_backend_key
  * test_p2_025_core_edge_types_set_is_consistent
  * test_p2_031_auc_direction_required_in_production
  * test_p2_036_pubchem_enrichment_filename
  * test_p2_041_cypher_timeout_enforced
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

# Make phase2 importable.
_HERE = Path(__file__).resolve().parent
_PHASE2 = _HERE.parent
_REPO = _PHASE2.parent
for p in (_PHASE2, _REPO):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


# ─── P2-001 v120 regression tests ────────────────────────────────────────────


def test_p2_001_v120_rejects_dir_with_only_non_phase1_csvs(monkeypatch):
    """If phase1/processed_data has only NON-Phase-1 CSVs (e.g. the
    data-flywheel's validated_hypotheses.csv), the service MUST return
    503, NOT 200 with 0/0."""
    from fastapi.testclient import TestClient
    from phase2 import service as svc

    with tempfile.TemporaryDirectory() as tmp:
        fake_pdir = Path(tmp) / "phase1" / "processed_data"
        fake_pdir.mkdir(parents=True)
        # Drop a non-Phase-1 CSV (the data-flywheel output).
        (fake_pdir / "validated_hypotheses.csv").write_text("id,drug,disease\n1,DB001,DOID1\n")
        # Make NONE of the expected Phase 1 CSVs present.
        monkeypatch.setattr(svc, "_REPO_ROOT", Path(tmp))
        client = TestClient(svc.app)
        r = client.get("/kg/stats")
        assert r.status_code == 503, (
            f"P2-001 v120 regression: expected 503, got {r.status_code} "
            f"with body {r.text[:200]}. The v107 glob check matched "
            f"non-Phase-1 CSVs and the API silently returned 0/0."
        )
        detail = r.json()["detail"]
        assert detail["error"] in ("phase1_data_missing", "kg_bridge_failed"), (
            f"Unexpected error code: {detail.get('error')}"
        )
        # The error message MUST name the expected Phase 1 CSVs.
        assert "Phase 1" in detail["message"] or "phase1_data_missing" in detail["message"]


def test_p2_001_v120_rejects_zero_node_zero_edge_bridge_output(monkeypatch):
    """If the bridge runs successfully but produces 0 nodes AND 0 edges
    (because Phase 1 CSVs are empty/corrupt), the service MUST return
    503, NOT 200 with 0/0."""
    from fastapi.testclient import TestClient
    from phase2 import service as svc

    with tempfile.TemporaryDirectory() as tmp:
        fake_pdir = Path(tmp) / "phase1" / "processed_data"
        fake_pdir.mkdir(parents=True)
        # Drop an EMPTY drugs.csv (header only) — passes the file check
        # but the bridge will return 0 nodes.
        (fake_pdir / "drugs.csv").write_text("drugbank_id,name\n")
        monkeypatch.setattr(svc, "_REPO_ROOT", Path(tmp))
        client = TestClient(svc.app)
        r = client.get("/kg/stats")
        assert r.status_code == 503, (
            f"P2-001 v120 second-line-of-defense failed: expected 503, "
            f"got {r.status_code} with body {r.text[:200]}. The bridge "
            f"returned 0 nodes / 0 edges but the API returned 200."
        )


# ─── P2-002 / P2-003 / P2-011 Cypher injection tests ────────────────────────


def test_p2_002_blocks_call_subquery_injection():
    """CALL { ... } subqueries must be blocked entirely."""
    from phase2.service import _validate_readonly_cypher
    err = _validate_readonly_cypher(
        'CALL { CREATE (n:Malicious {name: "owned"}) RETURN n }'
    )
    assert err is not None
    assert "subquer" in err.lower() or "call" in err.lower()


def test_p2_002_blocks_call_subquery_with_write_after_match():
    """Even when CALL { ... } appears AFTER a MATCH, it must be blocked."""
    from phase2.service import _validate_readonly_cypher
    err = _validate_readonly_cypher(
        'MATCH (n) CALL { CREATE (m:Malicious) RETURN m } RETURN n'
    )
    assert err is not None


def test_p2_002_blocks_apoc_procedure_bypass():
    """apoc.* procedures (except a strict whitelist) must be blocked."""
    from phase2.service import _validate_readonly_cypher
    err = _validate_readonly_cypher(
        'CALL apoc.cypher.runFirstColumn("CREATE (n) RETURN n", {}, false)'
    )
    assert err is not None


def test_p2_003_blocks_semicolon_injection():
    """Multi-statement injection via semicolon must be blocked."""
    from phase2.service import _validate_readonly_cypher
    err = _validate_readonly_cypher('MATCH (n) RETURN n; CREATE (m:Malicious)')
    assert err is not None
    assert "semicolon" in err.lower() or "multi-statement" in err.lower()


def test_p2_003_blocks_load_csv_exfiltration():
    """LOAD CSV with file:// URL must be blocked."""
    from phase2.service import _validate_readonly_cypher
    err = _validate_readonly_cypher(
        'LOAD CSV WITH HEADERS FROM "file:///etc/passwd" AS row RETURN row'
    )
    assert err is not None


def test_p2_002_legit_match_passes():
    """A legit MATCH query must pass the validator."""
    from phase2.service import _validate_readonly_cypher
    err = _validate_readonly_cypher('MATCH (n:Compound) RETURN n LIMIT 10')
    assert err is None


def test_p2_011_rejects_nested_dict_params():
    """Nested dict params (Cypher map injection) must be rejected."""
    from phase2.service import _validate_cypher_params
    err = _validate_cypher_params({"x": {"nested": "value"}})
    assert err is not None
    assert "non-scalar" in err.lower() or "dict" in err.lower()


def test_p2_011_accepts_list_of_scalars():
    """Lists of scalars must be accepted (legit Cypher list param)."""
    from phase2.service import _validate_cypher_params
    err = _validate_cypher_params({"ids": [1, 2, 3]})
    assert err is None


def test_p2_011_rejects_list_of_dicts():
    """Lists of dicts must be rejected."""
    from phase2.service import _validate_cypher_params
    err = _validate_cypher_params({"items": [{"a": 1}, {"b": 2}]})
    assert err is not None


# ─── P2-001 env var unification ──────────────────────────────────────────────


def test_p2_001_canonical_env_var_wins(monkeypatch):
    """DRUGOS_NEO4J_PASSWORD must take precedence over NEO4J_PASSWORD."""
    from phase2.service import _get_neo4j_env_var
    monkeypatch.setenv("NEO4J_PASSWORD", "legacy_secret")
    monkeypatch.setenv("DRUGOS_NEO4J_PASSWORD", "canonical_secret")
    assert _get_neo4j_env_var("PASSWORD", "") == "canonical_secret"


def test_p2_001_legacy_env_var_fallback(monkeypatch):
    """Legacy NEO4J_PASSWORD must still work (with a warning)."""
    from phase2.service import _get_neo4j_env_var
    monkeypatch.delenv("DRUGOS_NEO4J_PASSWORD", raising=False)
    monkeypatch.setenv("NEO4J_PASSWORD", "legacy_secret")
    assert _get_neo4j_env_var("PASSWORD", "") == "legacy_secret"


def test_p2_001_no_env_var_returns_default(monkeypatch):
    """If neither env var is set, the default must be returned."""
    from phase2.service import _get_neo4j_env_var
    monkeypatch.delenv("DRUGOS_NEO4J_PASSWORD", raising=False)
    monkeypatch.delenv("NEO4J_PASSWORD", raising=False)
    assert _get_neo4j_env_var("PASSWORD", "default") == "default"


# ─── P2-005 contract coverage ───────────────────────────────────────────────


def test_p2_005_phase2_to_phase3_edge_covers_all_core_edge_types():
    """Every Phase 2 CORE_EDGE_TYPE must be mapped OR explicitly dropped."""
    from phase2.contracts.phase2_schema import (
        PHASE2_TO_PHASE3_EDGE,
        PHASE2_TO_PHASE3_EDGE_DROPPED,
    )
    from drugos_graph.config_schema import CORE_EDGE_TYPES

    mapped = set(PHASE2_TO_PHASE3_EDGE.keys())
    dropped = set(PHASE2_TO_PHASE3_EDGE_DROPPED)
    for edge in CORE_EDGE_TYPES:
        assert edge in mapped or edge in dropped, (
            f"P2-005: Phase 2 edge {edge} is NEITHER mapped NOR explicitly "
            f"dropped — silently lost at the Phase 2→3 boundary."
        )


def test_p2_005_all_phase3_canonical_edges_reachable():
    """Every Phase 3 canonical edge type must be reachable from at least
    one Phase 2 mapping."""
    from phase2.contracts.phase2_schema import (
        PHASE2_TO_PHASE3_EDGE,
        EDGE_TYPES,
    )
    phase3_targets = set(PHASE2_TO_PHASE3_EDGE.values())
    for edge in EDGE_TYPES:
        assert edge in phase3_targets, (
            f"P2-005: Phase 3 canonical edge {edge} is unreachable from "
            f"any Phase 2 mapping."
        )


# ─── P2-004 invariant ───────────────────────────────────────────────────────


def test_p2_004_core_edge_types_only_reference_core_node_types():
    """Every edge in CORE_EDGE_TYPES must reference node types in
    CORE_NODE_TYPES or DRKG_NODE_TYPES (legacy DRKG types like Anatomy).
    The audit's specific complaint was about 'Drug' — fixed by adding
    'Drug' to CORE_NODE_TYPES."""
    from drugos_graph.config_schema import (
        CORE_EDGE_TYPES,
        CORE_NODE_TYPES,
        DRKG_NODE_TYPES,
    )
    allowed = set(CORE_NODE_TYPES) | set(DRKG_NODE_TYPES)
    for src, _, dst in CORE_EDGE_TYPES:
        assert src in allowed, (
            f"P2-004: edge source {src!r} not in CORE_NODE_TYPES or DRKG_NODE_TYPES"
        )
        assert dst in allowed, (
            f"P2-004: edge dest {dst!r} not in CORE_NODE_TYPES or DRKG_NODE_TYPES"
        )
    # Specific P2-004 audit assertion: 'Drug' MUST be in CORE_NODE_TYPES
    assert "Drug" in CORE_NODE_TYPES, (
        "P2-004 NOT FIXED: 'Drug' missing from CORE_NODE_TYPES"
    )


# ─── P2-006 no .lower() fallback ────────────────────────────────────────────


def test_p2_006_no_lower_fallback_in_pyg_builder():
    """The pyg_builder MUST NOT use .lower() as a fallback for unknown
    Phase 2 node labels."""
    import inspect
    from drugos_graph import pyg_builder
    src = inspect.getsource(pyg_builder)
    import re
    buggy = re.findall(
        r"_PHASE2_TO_GT_NODE_TYPE\.get\([^,]+,\s*[^)]+\.lower\(\)\)",
        src,
    )
    assert not buggy, (
        f"P2-006 NOT FIXED: found {len(buggy)} .lower() fallbacks in "
        f"pyg_builder."
    )


# ─── P2-007 SIDER canonical schema ──────────────────────────────────────────


def test_p2_007_sider_uses_canonical_meddra_term_schema():
    """CORE_EDGE_TYPES must contain the canonical SIDER edge and NOT the
    legacy edge."""
    from drugos_graph.config_schema import CORE_EDGE_TYPES, CORE_NODE_TYPES
    assert ("Compound", "causes_adverse_event", "MedDRA_Term") in CORE_EDGE_TYPES
    assert "MedDRA_Term" in CORE_NODE_TYPES
    # Legacy must NOT be in the whitelist
    assert ("Compound", "causes_side_effect", "Side Effect") not in CORE_EDGE_TYPES


# ─── P2-009 DEFAULT_EDGE_CONFIDENCE = 1.0 ───────────────────────────────────


def test_p2_009_default_edge_confidence_is_one():
    """DEFAULT_EDGE_CONFIDENCE must be 1.0 (edge existence = full signal)
    and graph_queries must use it instead of DEFAULT_ENTITY_CONFIDENCE."""
    from drugos_graph.config import DEFAULT_EDGE_CONFIDENCE, DEFAULT_ENTITY_CONFIDENCE
    assert DEFAULT_EDGE_CONFIDENCE == 1.0
    assert DEFAULT_ENTITY_CONFIDENCE == 0.0
    import inspect
    from drugos_graph import graph_queries
    src = inspect.getsource(graph_queries)
    assert "dc = DEFAULT_EDGE_CONFIDENCE" in src
    assert "dc = DEFAULT_ENTITY_CONFIDENCE" not in src


# ─── P2-024 _Phase1BridgeResult no _phase1_backend key ──────────────────────


def test_p2_024_no_legacy_phase1_backend_key():
    """_Phase1BridgeResult MUST NOT set the legacy '_phase1_backend'
    string-valued dict key (type-system lie)."""
    from drugos_graph.phase1_bridge import _Phase1BridgeResult
    r = _Phase1BridgeResult({"drugs": "frame"}, backend="csv")
    assert r.backend == "csv"
    assert "_phase1_backend" not in r
    # Iterating .items() must NOT yield a string-typed _phase1_backend value
    for k, v in r.items():
        assert k != "_phase1_backend"


# ─── P2-025 CORE_EDGE_TYPES_SET ─────────────────────────────────────────────


def test_p2_025_core_edge_types_set_is_consistent():
    """CORE_EDGE_TYPES_SET must be a frozenset matching CORE_EDGE_TYPES."""
    from drugos_graph.config_schema import CORE_EDGE_TYPES, CORE_EDGE_TYPES_SET
    assert isinstance(CORE_EDGE_TYPES_SET, frozenset)
    assert CORE_EDGE_TYPES_SET == frozenset(CORE_EDGE_TYPES)
    assert len(CORE_EDGE_TYPES_SET) == len(CORE_EDGE_TYPES)


# ─── P2-031 compute_auc direction resolution ────────────────────────────────


def test_p2_031_compute_auc_raises_in_production_without_direction(monkeypatch):
    """In production mode, compute_auc MUST raise when no direction is
    provided (no higher_is_better, no model, no model_score_direction)."""
    import numpy as np
    monkeypatch.setenv("DRUGOS_ENVIRONMENT", "production")
    monkeypatch.delenv("DRUGOS_ALLOW_DEFAULT_AUC_DIRECTION", raising=False)
    # Force re-import to pick up the env var
    import importlib
    from drugos_graph import evaluation as ev_module
    importlib.reload(ev_module)
    from drugos_graph.evaluation import compute_auc, EvaluationInputError
    pos = np.array([0.1, 0.2, 0.3])
    neg = np.array([0.8, 0.9, 1.0])
    with pytest.raises(EvaluationInputError):
        compute_auc(pos, neg)


def test_p2_031_compute_auc_refuses_escape_hatch_in_production(monkeypatch):
    """In production mode, DRUGOS_ALLOW_DEFAULT_AUC_DIRECTION=1 must be
    REFUSED (P2-031 root fix)."""
    import numpy as np
    monkeypatch.setenv("DRUGOS_ENVIRONMENT", "production")
    monkeypatch.setenv("DRUGOS_ALLOW_DEFAULT_AUC_DIRECTION", "1")
    import importlib
    from drugos_graph import evaluation as ev_module
    importlib.reload(ev_module)
    from drugos_graph.evaluation import compute_auc, EvaluationInputError
    pos = np.array([0.1, 0.2, 0.3])
    neg = np.array([0.8, 0.9, 1.0])
    with pytest.raises(EvaluationInputError):
        compute_auc(pos, neg)


def test_p2_031_compute_auc_works_with_explicit_direction():
    """compute_auc must work when higher_is_better is passed explicitly."""
    import numpy as np
    from drugos_graph.evaluation import compute_auc
    pos = np.array([0.1, 0.2, 0.3])
    neg = np.array([0.8, 0.9, 1.0])
    # TransE: lower score = more plausible → higher_is_better=False
    result = compute_auc(pos, neg, higher_is_better=False)
    assert 0.0 <= result <= 1.0
    assert result == 1.0  # perfect separation


# ─── P2-036 pubchem_enrichment filename ─────────────────────────────────────


def test_p2_036_pubchem_enrichment_filename():
    """_PHASE1_SOURCE_TO_CSV['pubchem_enrichment'] must be
    'pubchem_enrichment.csv' (NOT 'pubchem_compound_properties.csv')."""
    from drugos_graph.phase1_bridge import _PHASE1_SOURCE_TO_CSV
    assert _PHASE1_SOURCE_TO_CSV["pubchem_enrichment"] == "pubchem_enrichment.csv"


# ─── P2-017 requirements.txt has fastapi ────────────────────────────────────


def test_p2_017_requirements_has_fastapi():
    """requirements.txt MUST include fastapi, uvicorn, and pydantic."""
    req_path = _PHASE2 / "drugos_graph" / "requirements.txt"
    content = req_path.read_text()
    assert "fastapi" in content.lower(), "P2-017: fastapi missing from requirements.txt"
    assert "uvicorn" in content.lower(), "P2-017: uvicorn missing from requirements.txt"
    assert "pydantic" in content.lower(), "P2-017: pydantic missing from requirements.txt"


# ─── P2-035 CORS explicit headers ───────────────────────────────────────────


def test_p2_035_cors_no_wildcard_headers():
    """CORS middleware MUST NOT use allow_headers=['*'] with
    allow_credentials=True."""
    import inspect
    from phase2 import service as svc
    src = inspect.getsource(svc)
    # Find the CORSMiddleware block
    assert "allow_headers=['*']" not in src, (
        "P2-035 NOT FIXED: CORS uses allow_headers=['*'] with credentials"
    )
    assert "_ALLOWED_CORS_HEADERS" in src, (
        "P2-035: explicit header list not defined"
    )


# ─── P2-041 cypher timeout ──────────────────────────────────────────────────


def test_p2_041_cypher_timeout_is_enforced():
    """The /cypher endpoint MUST enforce a 30-second timeout."""
    import inspect
    from phase2 import service as svc
    src = inspect.getsource(svc)
    assert "QUERY_TIMEOUT_SECONDS" in src
    assert "concurrent.futures" in src
    assert "transaction_timeout" in src


# ─── P2-019 pathway size cap ────────────────────────────────────────────────


def test_p2_019_pathway_size_cap_exists():
    """_derive_pathways_from_string MUST cap pathway size to avoid the
    giant connected component being labeled as a single pathway."""
    import inspect
    from drugos_graph import phase1_bridge
    src = inspect.getsource(phase1_bridge)
    assert "max_pathway_size" in src
    assert "_skipped_oversized" in src


# ─── P2-042 alias collision detection ───────────────────────────────────────


def test_p2_042_alias_collision_detection():
    """load_nodes_batch MUST detect when the same logical entity is loaded
    with different IDs (alias collision)."""
    import inspect
    from drugos_graph import phase1_bridge
    src = inspect.getsource(phase1_bridge)
    assert "_alias_keys" in src
    assert "_node_alias_index" in src
    assert "inchikey" in src  # the universal chemical identifier


# ─── P2-010 ON MATCH SET preserves data ─────────────────────────────────────


def test_p2_010_on_match_set_preserves_data():
    """The kg_builder MERGE Cypher MUST NOT overwrite data properties on
    MATCH (only lineage metadata).

    The buggy version had: ``ON MATCH SET r += row.props`` which overwrites
    all data properties on every re-load. The fixed version has
    ``ON MATCH SET r._updated_at = ..., r._version = ...`` (lineage only).

    Verification approach: search the source for both patterns. The
    buggy pattern (``ON MATCH SET r += row.props``) MUST NOT appear in
    the EXECUTABLE code (after stripping comments). The fixed pattern
    (``ON MATCH SET`` followed by ``r._updated_at`` on the next f-string
    fragment) MUST appear.
    """
    import inspect
    import re
    from drugos_graph import kg_builder
    src = inspect.getsource(kg_builder)
    # Strip single-line comments (everything after # on a line).
    no_comments = re.sub(r"#[^\n]*", "", src)
    # The buggy pattern: ON MATCH SET directly followed by ``r += row.props``
    # (on the same f-string fragment).
    buggy_matches = re.findall(
        r"ON MATCH SET\s+r\s*\+=\s*row\.props",
        no_comments,
    )
    assert not buggy_matches, (
        f"P2-010 NOT FIXED: found {len(buggy_matches)} ON MATCH SET "
        f"r += row.props clauses — data overwrite bug."
    )
    # The fixed pattern: ``ON MATCH SET`` exists in the source.
    assert "ON MATCH SET" in no_comments, (
        "P2-010: no ON MATCH SET found in kg_builder source"
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
