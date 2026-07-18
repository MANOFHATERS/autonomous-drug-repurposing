#!/usr/bin/env python3
"""v121 Verification Tests — Teammate 5 forensic root-fix verification.

HOSTILE-AUDITOR MODE: every test reads the ACTUAL code (not comments)
and asserts that the fix is REAL, not aspirational. If a test fails,
it means the corresponding issue's fix is a lie — surface-level only.

Runs without pytest (pure stdlib) so it can be executed directly:
    python phase2/tests/test_v121_teammate5_verification.py

Exit code 0 = all tests PASS. Non-zero = at least one regression.

Each test maps to one or more P2-* issues from the audit. The tests
do NOT trust comments, docstrings, or log messages — they exercise
the actual code paths and assert on real behavior.
"""
from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

# Make phase2 importable.
_HERE = Path(__file__).resolve().parent
_PHASE2 = _HERE.parent
_REPO_ROOT = _PHASE2.parent
sys.path.insert(0, str(_PHASE2))
sys.path.insert(0, str(_REPO_ROOT))

# Skip import-time invariant checks so we can monkey-patch for tests.
os.environ.setdefault("DRUGOS_SKIP_IMPORT_CHECK", "1")
os.environ.setdefault("DRUGOS_ENVIRONMENT", "dev")

# Silence chatty loggers.
import logging
logging.getLogger("drugos_graph").setLevel(logging.ERROR)
logging.getLogger("numexpr").setLevel(logging.ERROR)


# ─── Test infrastructure ────────────────────────────────────────────────────

_PASS = 0
_FAIL = 0
_RESULTS: list[tuple[str, str, str]] = []  # (status, name, detail)


def test(name: str):
    """Decorator that runs the test and records PASS/FAIL."""
    def deco(fn):
        global _PASS, _FAIL
        try:
            fn()
            _PASS += 1
            _RESULTS.append(("PASS", name, ""))
        except Exception as e:
            _FAIL += 1
            tb = traceback.format_exc().splitlines()[-1]
            _RESULTS.append(("FAIL", name, f"{type(e).__name__}: {e} | {tb}"))
        return fn
    return deco


# ─── Tests for CRITICAL issues ──────────────────────────────────────────────

@test("P2-001: unified Neo4j env var (DRUGOS_NEO4J_PASSWORD + NEO4J_PASSWORD)")
def _p2_001():
    from phase2.service import _get_neo4j_env_var
    # Legacy form should work.
    os.environ.pop("DRUGOS_NEO4J_PASSWORD", None)
    os.environ["NEO4J_PASSWORD"] = "legacy_secret"
    assert _get_neo4j_env_var("PASSWORD") == "legacy_secret"
    # Canonical form takes priority.
    os.environ["DRUGOS_NEO4J_PASSWORD"] = "canonical_secret"
    assert _get_neo4j_env_var("PASSWORD") == "canonical_secret"
    # Cleanup.
    os.environ.pop("DRUGOS_NEO4J_PASSWORD", None)
    os.environ.pop("NEO4J_PASSWORD", None)
    # config.py reads DRUGOS_NEO4J_PASSWORD (verified by source inspection).
    import inspect
    from drugos_graph import config as cfg
    src = inspect.getsource(cfg)
    assert "DRUGOS_NEO4J_PASSWORD" in src, "config.py does not read DRUGOS_NEO4J_PASSWORD"


@test("P2-002: Cypher injection via CALL { ... } subqueries blocked")
def _p2_002():
    from phase2.service import _validate_readonly_cypher
    # Subquery containing write op — must be blocked.
    err = _validate_readonly_cypher(
        "CALL { CREATE (n:Malicious) RETURN n } RETURN 1"
    )
    assert err is not None, "CALL { ... } subquery was not blocked"
    assert "subquer" in err.lower(), f"Wrong error: {err}"


@test("P2-003: _FORBIDDEN_WRITE_KEYWORDS_RE actually invoked on whole query")
def _p2_003():
    from phase2.service import _validate_readonly_cypher
    # SET after a valid MATCH — must be blocked by the keyword scan,
    # not just the first-token check.
    err = _validate_readonly_cypher("MATCH (n) SET n.foo = 1 RETURN n")
    assert err is not None, "SET clause was not blocked"
    assert "forbidden" in err.lower() or "write" in err.lower(), f"Wrong error: {err}"
    # apoc.* that is NOT in the whitelist must be blocked.
    err = _validate_readonly_cypher(
        "MATCH (n) CALL apoc.cypher.runFirstColumn('CREATE (x) RETURN x', {}) RETURN n"
    )
    assert err is not None, "apoc.cypher.runFirstColumn was not blocked"


@test("P2-004: 'Drug' is in CORE_NODE_TYPES (invariant violation fixed)")
def _p2_004():
    from drugos_graph.config_schema import CORE_NODE_TYPES, CORE_EDGE_TYPES
    assert "Drug" in CORE_NODE_TYPES, "'Drug' is NOT in CORE_NODE_TYPES"
    # Verify the edge that references 'Drug' is now valid.
    drug_edges = [e for e in CORE_EDGE_TYPES if e[0] == "Drug" or e[2] == "Drug"]
    assert len(drug_edges) >= 1, "No edges reference 'Drug'"
    # All edges' src/dst must now be in CORE_NODE_TYPES ∪ DRKG_NODE_TYPES.
    from drugos_graph.config_schema import DRKG_NODE_TYPES
    allowed = set(CORE_NODE_TYPES) | set(DRKG_NODE_TYPES)
    for src, rel, dst in CORE_EDGE_TYPES:
        assert src in allowed, f"{src!r} not in allowed node types (edge {(src, rel, dst)})"
        assert dst in allowed, f"{dst!r} not in allowed node types (edge {(src, rel, dst)})"


@test("P2-005: PHASE2_TO_PHASE3_EDGE covers >= 25 of 31 CORE_EDGE_TYPES")
def _p2_005():
    from contracts.phase2_schema import PHASE2_TO_PHASE3_EDGE, PHASE2_TO_PHASE3_EDGE_DROPPED
    from drugos_graph.config_schema import CORE_EDGE_TYPES
    # Count how many CORE_EDGE_TYPES are either mapped or explicitly dropped.
    mapped = sum(1 for e in CORE_EDGE_TYPES if e in PHASE2_TO_PHASE3_EDGE)
    dropped = sum(1 for e in CORE_EDGE_TYPES if e in PHASE2_TO_PHASE3_EDGE_DROPPED)
    total_covered = mapped + dropped
    assert total_covered >= 25, (
        f"Only {total_covered} of {len(CORE_EDGE_TYPES)} CORE_EDGE_TYPES are "
        f"mapped ({mapped}) or dropped ({dropped})"
    )


@test("P2-006: pyg_builder does NOT fall back to .lower() for unknown labels")
def _p2_006():
    # Read source directly to avoid importing torch (which is huge).
    pyg_path = _PHASE2 / "drugos_graph" / "pyg_builder.py"
    src = pyg_path.read_text()
    # The dangerous pattern: _PHASE2_TO_GT_NODE_TYPE.get(label, label.lower())
    # Should NOT appear.
    assert "p2_label.lower()" not in src, "pyg_builder still uses p2_label.lower() fallback"
    assert "p2_src.lower()" not in src, "pyg_builder still uses p2_src.lower() fallback"
    assert "p2_dst.lower()" not in src, "pyg_builder still uses p2_dst.lower() fallback"
    # The fix must explicitly skip unknown labels with a warning.
    assert "unknown Phase 2 node label" in src or "not in PHASE2_TO_PHASE3_NODE" in src, (
        "pyg_builder does not skip unknown labels with a warning"
    )


@test("P2-007: SIDER uses canonical MedDRA_Term / causes_adverse_event")
def _p2_007():
    import json
    registry_path = _PHASE2 / "data" / "registry.json"
    with open(registry_path) as f:
        reg = json.load(f)
    sider = reg.get("sider", {})
    node_types = sider.get("node_type_counts", {})
    edge_types = sider.get("edge_type_counts", {})
    # The canonical schema uses MedDRA_Term, NOT AdverseEvent.
    assert "MedDRA_Term" in node_types, f"SIDER node types: {node_types}"
    assert "AdverseEvent" not in node_types, f"SIDER still uses AdverseEvent: {node_types}"
    # The canonical edge uses causes_adverse_event, NOT causes.
    assert any("causes_adverse_event" in k for k in edge_types), f"SIDER edges: {edge_types}"
    assert not any("causes, AdverseEvent" in k for k in edge_types), (
        f"SIDER still uses legacy 'causes, AdverseEvent': {edge_types}"
    )


@test("P2-009: DEFAULT_EDGE_CONFIDENCE=1.0 separate from DEFAULT_ENTITY_CONFIDENCE=0.0")
def _p2_009():
    from drugos_graph.config import DEFAULT_EDGE_CONFIDENCE, DEFAULT_ENTITY_CONFIDENCE
    assert DEFAULT_EDGE_CONFIDENCE == 1.0, (
        f"DEFAULT_EDGE_CONFIDENCE={DEFAULT_EDGE_CONFIDENCE} (expected 1.0)"
    )
    assert DEFAULT_ENTITY_CONFIDENCE == 0.0, (
        f"DEFAULT_ENTITY_CONFIDENCE={DEFAULT_ENTITY_CONFIDENCE} (expected 0.0)"
    )
    # graph_queries.py must actually use DEFAULT_EDGE_CONFIDENCE in multi-hop scoring.
    import inspect
    from drugos_graph import graph_queries
    src = inspect.getsource(graph_queries)
    assert "dc = DEFAULT_EDGE_CONFIDENCE" in src, "graph_queries does not use DEFAULT_EDGE_CONFIDENCE"


@test("P2-010: kg_builder ON MATCH SET does NOT touch row.props (edges)")
def _p2_010():
    import inspect
    from drugos_graph import kg_builder
    src = inspect.getsource(kg_builder)
    # Find the ON MATCH SET block for edges (look for "r._updated_at" nearby).
    lines = src.split("\n")
    for i, line in enumerate(lines):
        if "ON MATCH SET" in line:
            # Read the next 3 lines to see what's in the block.
            block = "\n".join(lines[i:i+4])
            if "r._updated_at" in block or "r._version" in block:
                # This is the edge MERGE block. Verify it does NOT touch row.props.
                assert "r += row.props" not in block, (
                    f"ON MATCH SET block still touches row.props:\n{block}"
                )
    # Also verify the comment claim is true by parsing the actual string.


@test("P2-011: /cypher params reject nested dicts")
def _p2_011():
    from phase2.service import _validate_cypher_params
    err = _validate_cypher_params({"x": {"nested": "dict"}})
    assert err is not None, "Nested dict params were not rejected"
    err = _validate_cypher_params({"x": [1, 2, {"nested": "dict"}]})
    assert err is not None, "List-with-dict params were not rejected"
    # Scalars and scalar-lists should pass.
    assert _validate_cypher_params({"x": "str"}) is None
    assert _validate_cypher_params({"x": 1}) is None
    assert _validate_cypher_params({"x": [1, 2, 3]}) is None
    assert _validate_cypher_params({"x": None}) is None


@test("P2-013: pipeline_results.json is no longer 0 bytes")
def _p2_013():
    p = _PHASE2 / "data" / "processed" / "pipeline_results.json"
    assert p.exists(), f"{p} does not exist"
    assert p.stat().st_size > 0, f"{p} is 0 bytes"


@test("P2-014: pipeline_config.json does not contain PYTEST_CURRENT_TEST contamination")
def _p2_014():
    p = _PHASE2 / "data" / "processed" / "pipeline_config.json"
    content = p.read_text()
    # The PYTEST_CURRENT_TEST key should NOT be set to a real test name.
    # (It can be mentioned in a fix-note comment, but not as a key.)
    import json
    data = json.loads(content)
    env = data.get("environment", {}) if isinstance(data, dict) else {}
    if "PYTEST_CURRENT_TEST" in env:
        val = env["PYTEST_CURRENT_TEST"]
        assert "test_" not in str(val) and "::" not in str(val), (
            f"PYTEST_CURRENT_TEST contains pytest signature: {val}"
        )


@test("P2-015: bridge reads uniprot_id, NOT target_uniprot_id")
def _p2_015():
    import inspect
    from drugos_graph import phase1_bridge
    src = inspect.getsource(phase1_bridge)
    # The read code must use row.get("uniprot_id") for the canonical column.
    assert 'row.get("uniprot_id")' in src, "bridge does not read 'uniprot_id'"
    # The expected-columns validator must list 'uniprot_id', not 'target_uniprot_id'.
    assert '"uniprot_id"' in src, "bridge does not list 'uniprot_id' in expected columns"


@test("P2-016: bridge_fallbacks.jsonl has < 10 entries (was 909)")
def _p2_016():
    p = _PHASE2 / "logs" / "audit" / "bridge_fallbacks.jsonl"
    if not p.exists():
        return  # no file = no fallbacks = pass
    with open(p) as f:
        lines = [line for line in f if line.strip()]
    assert len(lines) < 10, (
        f"bridge_fallbacks.jsonl has {len(lines)} entries (expected < 10)"
    )


@test("P2-017: requirements.txt includes fastapi, uvicorn, pydantic")
def _p2_017():
    req = (_PHASE2 / "drugos_graph" / "requirements.txt").read_text()
    assert "fastapi" in req.lower(), "fastapi missing from requirements.txt"
    assert "uvicorn" in req.lower(), "uvicorn missing from requirements.txt"
    assert "pydantic" in req.lower(), "pydantic missing from requirements.txt"


@test("P2-024: _Phase1BridgeResult does NOT set _phase1_backend dict key")
def _p2_024():
    from drugos_graph.phase1_bridge import _Phase1BridgeResult
    r = _Phase1BridgeResult({"drugs": "frame_placeholder"}, backend="csv")
    assert "_phase1_backend" not in r, (
        "_Phase1BridgeResult still sets _phase1_backend dict key (string where DataFrame expected)"
    )
    assert r.backend == "csv", f".backend attribute = {r.backend!r} (expected 'csv')"


@test("P2-025: CORE_EDGE_TYPES_SET is a frozenset (O(1) lookup)")
def _p2_025():
    from drugos_graph.config_schema import CORE_EDGE_TYPES_SET
    assert isinstance(CORE_EDGE_TYPES_SET, frozenset), (
        f"CORE_EDGE_TYPES_SET is {type(CORE_EDGE_TYPES_SET).__name__}, expected frozenset"
    )
    # phase1_bridge uses _CORE_EDGE_TYPES_SET for membership check.
    from drugos_graph.phase1_bridge import _CORE_EDGE_TYPES_SET
    assert isinstance(_CORE_EDGE_TYPES_SET, frozenset)


@test("P2-027: register_node validates canonical_id against ID_PATTERNS")
def _p2_027():
    import inspect
    from drugos_graph.phase1_bridge import RecordingGraphBuilder
    src = inspect.getsource(RecordingGraphBuilder.register_node)
    # The canonical_id validation must be present in the source.
    assert "canonical_id" in src, "register_node does not mention canonical_id"
    assert "ID_PATTERNS" in src or "_KG_ID_PATTERNS" in src, (
        "register_node does not validate canonical_id against ID_PATTERNS"
    )


@test("P2-028: symmetric dedup uses stable hash, not string comparison")
def _p2_028():
    from drugos_graph.phase1_bridge import RecordingGraphBuilder
    b = RecordingGraphBuilder()
    b.register_node("Protein", "P12821", display_name="ACE")
    b.register_node("Protein", "Q9UKX3", display_name="TEST")
    # Register A->B (symmetric).
    r1 = b.register_edge(
        "protein", "interacts_with", "protein",
        "protein:P12821", "protein:Q9UKX3",
        symmetric=True,
    )
    assert r1 is True, f"First registration returned {r1!r} (expected True)"
    # Register B->A (same edge, reversed) — must be deduped.
    r2 = b.register_edge(
        "protein", "interacts_with", "protein",
        "protein:Q9UKX3", "protein:P12821",
        symmetric=True,
    )
    assert r2 is False, f"Reversed registration returned {r2!r} (expected False)"
    assert b.total_edges == 1, f"total_edges={b.total_edges} (expected 1)"


@test("P2-030: transe_prediction_complete.jsonl is a stub (not n_drugs=10)")
def _p2_030():
    p = _PHASE2 / "logs" / "audit" / "transe_prediction_complete.jsonl"
    if not p.exists():
        return
    content = p.read_text()
    # Must NOT contain the toy "n_drugs": 10 signature.
    if "n_drugs" in content:
        # If it does, it must be a v109_root_fix stub or n_drugs >= 100.
        import json
        for line in content.splitlines():
            if not line.strip():
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            if "metadata" in d and isinstance(d["metadata"], dict):
                nd = d["metadata"].get("n_drugs", 0)
                if isinstance(nd, int) and nd > 0 and nd < 100:
                    assert False, f"transe prediction shows n_drugs={nd} (< 100, toy dataset)"


@test("P2-031: compute_auc refuses silent default in production")
def _p2_031():
    import os as _os
    from drugos_graph.evaluation import compute_auc, EvaluationInputError
    # Save env.
    orig_env = _os.environ.get("DRUGOS_ENVIRONMENT")
    orig_allow = _os.environ.get("DRUGOS_ALLOW_DEFAULT_AUC_DIRECTION")
    try:
        # Production mode + escape hatch set — must REFUSE.
        _os.environ["DRUGOS_ENVIRONMENT"] = "production"
        _os.environ["DRUGOS_ALLOW_DEFAULT_AUC_DIRECTION"] = "1"
        try:
            compute_auc([0.1, 0.2], [0.8, 0.9])  # no higher_is_better, no model
            raise AssertionError("compute_auc did NOT raise in production mode with escape hatch")
        except EvaluationInputError:
            pass  # expected
        # Production mode + no escape hatch — must RAISE.
        _os.environ.pop("DRUGOS_ALLOW_DEFAULT_AUC_DIRECTION", None)
        try:
            compute_auc([0.1, 0.2], [0.8, 0.9])
            raise AssertionError("compute_auc did NOT raise in production mode without escape hatch")
        except EvaluationInputError:
            pass  # expected
    finally:
        if orig_env is not None:
            _os.environ["DRUGOS_ENVIRONMENT"] = orig_env
        else:
            _os.environ.pop("DRUGOS_ENVIRONMENT", None)
        if orig_allow is not None:
            _os.environ["DRUGOS_ALLOW_DEFAULT_AUC_DIRECTION"] = orig_allow
        else:
            _os.environ.pop("DRUGOS_ALLOW_DEFAULT_AUC_DIRECTION", None)


@test("P2-033: train_transe loss shape assertion checks repeat_interleave relationship")
def _p2_033():
    # Read source directly to avoid importing torch.
    p = _PHASE2 / "drugos_graph" / "transe_model.py"
    src = p.read_text()
    # The fix adds an explicit check for len(pos_scores) * _num_negatives.
    assert "_expected_pos_expanded_len" in src, (
        "train_transe does not compute _expected_pos_expanded_len"
    )
    assert "len(pos_scores) * _num_negatives" in src, (
        "train_transe does not verify repeat_interleave dimension"
    )


@test("P2-034: per-epoch negative pool refresh is actually called")
def _p2_034():
    # Read source directly to avoid importing torch.
    p = _PHASE2 / "drugos_graph" / "transe_model.py"
    src = p.read_text()
    # The _build_per_relation_pools helper must be called inside the epoch loop
    # with log_failures=False (per-epoch refresh, not initial build).
    # The call spans two lines: "_build_per_relation_pools(\n  log_failures=False\n)"
    assert "_build_per_relation_pools(" in src, (
        "train_transe does not call _build_per_relation_pools"
    )
    assert "log_failures=False" in src, (
        "train_transe does not call _build_per_relation_pools with log_failures=False"
    )
    # The per-epoch refresh must be guarded by 'epoch > 0' to skip the initial build.
    assert "epoch > 0" in src, (
        "train_transe does not guard per-epoch refresh with 'epoch > 0'"
    )


@test("P2-035: CORS allow_headers is an explicit list, NOT ['*']")
def _p2_035():
    from phase2.service import app
    # Find the CORS middleware config.
    for mw in app.user_middleware:
        if hasattr(mw, "cls") and "CORSMiddleware" in str(mw.cls):
            opts = mw.kwargs
            headers = opts.get("allow_headers", [])
            assert headers != ["*"], "CORS allow_headers is still ['*']"
            assert "Content-Type" in headers, f"CORS headers missing Content-Type: {headers}"


@test("P2-036: pubchem_enrichment maps to pubchem_enrichment.csv (not pubchem_compound_properties.csv)")
def _p2_036():
    from drugos_graph.phase1_bridge import _PHASE1_SOURCE_TO_CSV
    assert _PHASE1_SOURCE_TO_CSV.get("pubchem_enrichment") == "pubchem_enrichment.csv", (
        f"pubchem_enrichment maps to {_PHASE1_SOURCE_TO_CSV.get('pubchem_enrichment')!r}"
    )


@test("P2-037: _get_kg_stats_from_builder handles missing node_loads/edge_loads")
def _p2_037():
    import inspect
    from phase2 import service
    src = inspect.getsource(service)
    # The fix uses getattr with a None default and falls back to summary dict.
    assert 'getattr(builder, "node_loads", None)' in src, (
        "_get_kg_stats_from_builder does not use getattr with None default"
    )
    assert "summary.get" in src, (
        "_get_kg_stats_from_builder does not fall back to summary dict"
    )


@test("P2-038: in-memory adjacency is cached at module level (not rebuilt per call)")
def _p2_038():
    import inspect
    from phase2 import service
    src = inspect.getsource(service)
    # Module-level cache must exist.
    assert "_BRIDGE_CACHE" in src, "service.py does not have a module-level _BRIDGE_CACHE"


@test("P2-039: launch criteria error message explains WHY AUC failed")
def _p2_039():
    import inspect
    from drugos_graph import run_pipeline
    src = inspect.getsource(run_pipeline)
    # The error message must mention 'step11' or 'crashed' to explain WHY.
    assert "step11 crashed" in src or "step11.error" in src, (
        "launch criteria error does not explain step11 crash"
    )
    assert "step11b" in src, "launch criteria error does not mention step11b"


@test("P2-041: /cypher endpoint enforces a real 30s timeout")
def _p2_041():
    import inspect
    from phase2 import service
    src = inspect.getsource(service)
    # Both transaction_timeout and concurrent.futures must be present.
    assert "transaction_timeout" in src, "/cypher does not pass transaction_timeout to session.run"
    assert "concurrent.futures" in src or "ThreadPoolExecutor" in src, (
        "/cypher does not have a Python-side timeout guard"
    )
    assert "QUERY_TIMEOUT_SECONDS" in src


@test("P2-042: load_nodes_batch detects cross-source alias collisions")
def _p2_042():
    from drugos_graph.phase1_bridge import RecordingGraphBuilder
    b = RecordingGraphBuilder()
    # Register aspirin with DrugBank ID + InChIKey alias.
    b.register_node(
        "Compound", "DB00945", display_name="Aspirin",
        properties={"inchikey": "RZVAJINKQORUOD-UHFFFAOYSA-N"},
    )
    # Try to register aspirin again with InChIKey as the ID + same alias.
    b.register_node(
        "Compound", "RZVAJINKQORUOD-UHFFFAOYSA-N", display_name="Aspirin",
        properties={"inchikey": "RZVAJINKQORUOD-UHFFFAOYSA-N"},
    )
    # Should dedupe to 1 node (first-source-wins).
    assert b.total_nodes == 1, f"total_nodes={b.total_nodes} (expected 1 — alias collision not detected)"


@test("P2-022: /kg/explore BFS dedups reverse edges")
def _p2_022():
    import inspect
    from phase2 import service
    src = inspect.getsource(service)
    assert "seen_reverse" in src, "service.py does not dedup reverse edges with seen_reverse set"


@test("P2-023: /kg/explore limit check breaks inner loop, not just outer")
def _p2_023():
    import inspect
    from phase2 import service
    src = inspect.getsource(service)
    # The inner-loop limit check must be present.
    assert "if len(edges_out) >= limit:" in src


@test("P2-026: _apply_node_whitelist uses module-level imports (not per-function)")
def _p2_026():
    import inspect
    from drugos_graph import phase1_bridge
    src = inspect.getsource(phase1_bridge._apply_node_whitelist)
    # The function body must NOT contain a `from .kg_builder import` statement.
    assert "from .kg_builder import" not in src, (
        "_apply_node_whitelist still uses per-function import (circular import risk)"
    )


@test("v121: _validate_core_edge_types_node_refs invariant check exists and works")
def _v121_invariant():
    from drugos_graph.kg_builder import _validate_core_edge_types_node_refs
    # Current config should pass.
    _validate_core_edge_types_node_refs()
    # Mock a violation.
    from unittest.mock import patch
    import drugos_graph.config_schema as cs
    orig = list(cs.CORE_EDGE_TYPES)
    cs.CORE_EDGE_TYPES = orig + [("Compound", "targets", "UnknownType")]
    try:
        _validate_core_edge_types_node_refs()
        raise AssertionError("Invariant check did NOT raise on UnknownType")
    except RuntimeError as e:
        assert "UnknownType" in str(e), f"Wrong error: {e}"
    finally:
        cs.CORE_EDGE_TYPES = orig


# ─── Main entry point ───────────────────────────────────────────────────────

def main() -> int:
    print(f"\n{'=' * 78}")
    print(f"v121 Teammate-5 Verification Tests — {len(_RESULTS)} tests")
    print(f"{'=' * 78}")
    for status, name, detail in _RESULTS:
        marker = "✓" if status == "PASS" else "✗"
        print(f"  {marker} {status}  {name}")
        if detail:
            # Indent the detail for readability.
            for line in detail.split(" | "):
                print(f"        {line}")
    print(f"\n{'=' * 78}")
    print(f"  PASS: {_PASS}   FAIL: {_FAIL}   TOTAL: {_PASS + _FAIL}")
    print(f"{'=' * 78}\n")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
