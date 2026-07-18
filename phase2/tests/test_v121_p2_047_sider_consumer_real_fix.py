#!/usr/bin/env python3
"""v121 P2-047 REAL ROOT FIX verification tests.

Red-team audit (per the user's directive: "comments are fakes"):
    The previous v113 "ROOT FIX" for P2-047 in
    ``phase2/drugos_graph/phase1_bridge.py`` was ASPIRATIONAL. The
    audit found that ``sider_loader`` parses SIDER adverse-event data,
    but the bridge's ``paths`` dict had NO SIDER entry — so SIDER data
    was NEVER consumed by the Phase 1 → Phase 2 bridge. The v113 "fix"
    ONLY added the SIDER entry to the paths dict in
    ``read_phase1_outputs``. The file was READ into the frames dict
    (``out["sider_adverse_events"]``) but NEVER iterated by
    ``stage_phase1_to_phase2``. No
    Compound→causes_adverse_event→MedDRA_Term edges were emitted in
    bridge-only mode. The RL safety ranker (which queries these edges
    for its safety-signal dimension) had ZERO adverse-event signal —
    a patient-safety bug where dangerous drugs (e.g. thalidomide,
    rofecoxib) were ranked "green/safe".

    Exactly the "comments are fakes" pattern the audit warned about:
    the v113 comment claimed the bridge now consumes SIDER, but the
    executable code never iterated ``frames["sider_adverse_events"]``.

REAL ROOT FIX (v121):
    1. Added a new ``_load_sider_adverse_events`` consumer function
       that iterates the SIDER DataFrame and emits:
         • MedDRA_Term nodes (deduped by ``umls_id_meddra``).
         • Compound→causes_adverse_event→MedDRA_Term edges (with
           referential-integrity check — edges whose Compound endpoint
           is NOT in ``staged.compound_nodes`` are skipped).
    2. Added a new ``meddra_term_nodes`` field to ``Phase1StagedData``.
    3. Added ``meddra_term_nodes`` to the ``total_nodes`` property.
    4. Added ``MedDRA_Term`` to the ``load_into_graph`` node-iteration
       list (so the nodes are actually written to Neo4j).
    5. Added ``sider_adverse_events`` to ``_PHASE1_EXPECTED_COLUMNS``
       and ``_PHASE1_ANY_OF_COLUMNS`` for schema validation.
    6. Added ``sider_adverse_events`` to the ``name_map`` in
       ``load_into_graph`` so the lineage checksum reflects the file.

VERIFICATION (these tests):
    1. AST check: ``stage_phase1_to_phase2`` actually iterates
       ``frames.get("sider_adverse_events")`` (the v113 fake fix
       would fail this — the file was read but never iterated).
    2. Behavioral check: with a minimal frames dict containing
       2 Compound nodes + 2 SIDER rows, ``stage_phase1_to_phase2``
       produces 2 MedDRA_Term nodes and 2 causes_adverse_event edges.
       The v113 fake fix would produce 0 of each.
    3. Schema check: MedDRA_Term node IDs match
       ``kg_builder.ID_PATTERNS["MedDRA_Term"]`` =
       ``^(\\d{8}|MedDRA:C\\d{7})$``. Without this, the
       RecordingGraphBuilder's ID_PATTERNS validation would
       dead-letter every edge.
    4. Referential integrity check: SIDER rows whose Compound endpoint
       is NOT in ``staged.compound_nodes`` produce NO edge (the
       RecordingGraphBuilder would dead-letter them anyway).
    5. Dedup check: multiple SIDER rows with the same
       ``umls_id_meddra`` produce ONE MedDRA_Term node (not N).
"""
from __future__ import annotations

import ast
import inspect
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

# Make the phase2 package importable.
REPO_ROOT = Path(__file__).resolve().parents[2]
PHASE2_DIR = REPO_ROOT / "phase2"
for _p in (str(REPO_ROOT), str(PHASE2_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pandas as pd  # noqa: E402

from drugos_graph.phase1_bridge import (  # noqa: E402
    Phase1StagedData,
    _PHASE1_EXPECTED_COLUMNS,
    _PHASE1_ANY_OF_COLUMNS,
    _load_sider_adverse_events,
    stage_phase1_to_phase2,
)


# ──────────────────────────────────────────────────────────────────────────
# Test 1: AST check — stage_phase1_to_phase2 actually iterates the
# sider_adverse_events frame (the v113 fake fix would fail this).
# ──────────────────────────────────────────────────────────────────────────
def test_ast_stage_phase1_to_phase2_iterates_sider_adverse_events():
    """The function body MUST call ``frames.get("sider_adverse_events")``.

    The v113 fake fix added the SIDER entry to ``read_phase1_outputs``'s
    paths dict but NEVER iterated the frame in ``stage_phase1_to_phase2``.
    This AST check catches that exact regression: if a future refactor
    removes the consumer call, this test fails.
    """
    src = inspect.getsource(stage_phase1_to_phase2)
    tree = ast.parse(src)
    # Walk the AST and look for frames.get("sider_adverse_events") OR
    # frames["sider_adverse_events"].
    found_get = False
    found_subscript = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            # frames.get("sider_adverse_events")
            if (
                node.func.attr == "get"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "frames"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == "sider_adverse_events"
            ):
                found_get = True
        if isinstance(node, ast.Subscript):
            # frames["sider_adverse_events"]
            if (
                isinstance(node.value, ast.Name)
                and node.value.id == "frames"
                and isinstance(node.slice, ast.Constant)
                and node.slice.value == "sider_adverse_events"
            ):
                found_subscript = True
    assert found_get or found_subscript, (
        "v121 P2-047 REAL FIX REGRESSION: stage_phase1_to_phase2 does NOT "
        "iterate frames.get('sider_adverse_events') or frames['sider_adverse_events']. "
        "This is the exact v113 fake-fix pattern — the file is read into "
        "the frames dict but NEVER consumed. The RL safety ranker would "
        "have ZERO adverse-event signal in bridge-only mode (patient-safety bug)."
    )


# ──────────────────────────────────────────────────────────────────────────
# Test 2: Behavioral check — 2 Compound + 2 SIDER rows → 2 MedDRA_Term
# nodes + 2 causes_adverse_event edges.
# ──────────────────────────────────────────────────────────────────────────
def test_behavioral_sider_consumption_emits_nodes_and_edges():
    """End-to-end: minimal frames dict → MedDRA_Term nodes + edges."""
    drugs_df = pd.DataFrame({
        "drugbank_id": ["DB00001", "DB00002"],
        "name": ["Drug A", "Drug B"],
        "inchikey": ["RZBJZVVOYQRSNH-UHFFFAOYSA-N", "ALQOKHWFYFVFCD-UHFFFAOYSA-N"],
        "pubchem_cid": [1234, 5678],
    })
    sider_df = pd.DataFrame({
        "pubchem_cid": [1234, 5678],
        "umls_id_meddra": ["C0018790", "C0234123"],
        "side_effect_name": ["Pain", "Hepatotoxicity"],
        "meddra_type": ["PT", "PT"],
    })
    frames = {"drugs": drugs_df, "sider_adverse_events": sider_df}

    staged = stage_phase1_to_phase2(frames, run_id="v121-test")

    assert len(staged.compound_nodes) == 2
    assert len(staged.meddra_term_nodes) == 2, (
        "v121 P2-047 REAL FIX FAILED: expected 2 MedDRA_Term nodes, "
        f"got {len(staged.meddra_term_nodes)}. The v113 fake fix would "
        "produce 0 (the file was read but never iterated)."
    )
    edge_key = ("Compound", "causes_adverse_event", "MedDRA_Term")
    assert edge_key in staged.edges, (
        f"v121 P2-047 REAL FIX FAILED: edge key {edge_key} not in staged.edges. "
        f"Available: {list(staged.edges.keys())}"
    )
    assert len(staged.edges[edge_key]) == 2, (
        "v121 P2-047 REAL FIX FAILED: expected 2 "
        f"Compound→causes_adverse_event→MedDRA_Term edges, "
        f"got {len(staged.edges[edge_key])}."
    )


# ──────────────────────────────────────────────────────────────────────────
# Test 3: Schema check — MedDRA_Term node IDs match ID_PATTERNS.
# ──────────────────────────────────────────────────────────────────────────
def test_meddra_term_node_ids_match_id_patterns():
    """MedDRA_Term IDs must match kg_builder.ID_PATTERNS['MedDRA_Term'].

    Pattern: ^(\\d{8}|MedDRA:C\\d{7})$ — either an 8-digit LLT/PT code
    OR a MedDRA-prefixed UMLS CUI (C + 7 digits). The bridge emits the
    second form (MedDRA:C0018790). Without this, the
    RecordingGraphBuilder's ID_PATTERNS validation would dead-letter
    every edge.
    """
    drugs_df = pd.DataFrame({
        "drugbank_id": ["DB00001"],
        "name": ["Drug A"],
        "inchikey": ["RZBJZVVOYQRSNH-UHFFFAOYSA-N"],
        "pubchem_cid": [1234],
    })
    sider_df = pd.DataFrame({
        "pubchem_cid": [1234],
        "umls_id_meddra": ["C0018790"],
        "side_effect_name": ["Pain"],
        "meddra_type": ["PT"],
    })
    staged = stage_phase1_to_phase2(
        {"drugs": drugs_df, "sider_adverse_events": sider_df},
        run_id="v121-test-id-pattern",
    )
    pattern = r"^(\d{8}|MedDRA:C\d{7})$"
    for n in staged.meddra_term_nodes:
        assert re.match(pattern, n["id"]), (
            f"MedDRA_Term node id {n['id']!r} does not match "
            f"ID_PATTERNS['MedDRA_Term'] = {pattern!r}. "
            f"The RecordingGraphBuilder would dead-letter this node."
        )


# ──────────────────────────────────────────────────────────────────────────
# Test 4: Referential integrity — SIDER rows whose Compound is NOT in
# the graph produce NO edge (but the MedDRA_Term node IS still emitted).
# ──────────────────────────────────────────────────────────────────────────
def test_referential_integrity_unknown_compound_skipped():
    """SIDER row whose pubchem_cid is NOT in any Compound node → skip edge.

    The RecordingGraphBuilder would dead-letter the edge anyway (missing
    endpoint). The bridge's consumer skips it proactively to avoid
    polluting the dead-letter queue and to make the referential-
    integrity guarantee explicit.
    """
    drugs_df = pd.DataFrame({
        "drugbank_id": ["DB00001"],
        "name": ["Drug A"],
        "inchikey": ["RZBJZVVOYQRSNH-UHFFFAOYSA-N"],
        "pubchem_cid": [1234],
    })
    # SIDER has TWO rows: one for Drug A (pubchem_cid=1234 — known),
    # one for Drug Z (pubchem_cid=9999 — NOT in the graph).
    sider_df = pd.DataFrame({
        "pubchem_cid": [1234, 9999],
        "umls_id_meddra": ["C0018790", "C0999999"],
        "side_effect_name": ["Pain", "Unknown AE"],
        "meddra_type": ["PT", "PT"],
    })
    staged = stage_phase1_to_phase2(
        {"drugs": drugs_df, "sider_adverse_events": sider_df},
        run_id="v121-test-refint",
    )
    # MedDRA_Term nodes: BOTH should be emitted (the node is independent
    # of whether the Compound endpoint exists).
    assert len(staged.meddra_term_nodes) == 2, (
        f"Expected 2 MedDRA_Term nodes (both CUIs), "
        f"got {len(staged.meddra_term_nodes)}."
    )
    # Edges: ONLY ONE (the one for the known Compound).
    edge_key = ("Compound", "causes_adverse_event", "MedDRA_Term")
    n_edges = len(staged.edges.get(edge_key, []))
    assert n_edges == 1, (
        f"Expected 1 edge (Compound DB00001 → MedDRA:C0018790), "
        f"got {n_edges}. The edge for pubchem_cid=9999 should have been "
        f"skipped (Compound not in graph — referential integrity)."
    )


# ──────────────────────────────────────────────────────────────────────────
# Test 5: Dedup — multiple SIDER rows with the same umls_id_meddra
# produce ONE MedDRA_Term node (not N).
# ──────────────────────────────────────────────────────────────────────────
def test_meddra_term_node_dedup_by_cui():
    """Multiple SIDER rows for the same CUI → ONE MedDRA_Term node.

    SIDER's meddra_all_se file lists the SAME adverse event under
    multiple rows (e.g., the same drug-CUI pair across PT/LLT types).
    The bridge's consumer MUST dedup by CUI — otherwise the KG would
    have N duplicate MedDRA_Term nodes for the same condition, and the
    RL safety ranker would count them as N distinct adverse events
    (inflating the safety score for the drug).
    """
    drugs_df = pd.DataFrame({
        "drugbank_id": ["DB00001"],
        "name": ["Drug A"],
        "inchikey": ["RZBJZVVOYQRSNH-UHFFFAOYSA-N"],
        "pubchem_cid": [1234],
    })
    # THREE SIDER rows: TWO with the same CUI (C0018790), one with a
    # different CUI (C0234123).
    sider_df = pd.DataFrame({
        "pubchem_cid": [1234, 1234, 1234],
        "umls_id_meddra": ["C0018790", "C0018790", "C0234123"],
        "side_effect_name": ["Pain", "Pain", "Hepatotoxicity"],
        "meddra_type": ["PT", "LLT", "PT"],
    })
    staged = stage_phase1_to_phase2(
        {"drugs": drugs_df, "sider_adverse_events": sider_df},
        run_id="v121-test-dedup",
    )
    # TWO unique MedDRA_Term nodes (C0018790 dedup'd, C0234123).
    assert len(staged.meddra_term_nodes) == 2, (
        f"Expected 2 MedDRA_Term nodes (C0018790 dedup'd + C0234123), "
        f"got {len(staged.meddra_term_nodes)}. Dedup by CUI is broken."
    )
    # THREE edges — one per SIDER row (the edge is per drug-CUI pair,
    # NOT dedup'd; if the same drug-CUI pair appears twice, the KG
    # builder's MERGE handles the dedup at load time).
    edge_key = ("Compound", "causes_adverse_event", "MedDRA_Term")
    n_edges = len(staged.edges.get(edge_key, []))
    # The bridge may or may not dedup edges — both are acceptable.
    # The CRITICAL assertion is the node dedup above.
    assert n_edges >= 2, (
        f"Expected at least 2 edges (one per unique drug-CUI pair), "
        f"got {n_edges}."
    )


# ──────────────────────────────────────────────────────────────────────────
# Test 6: total_nodes property includes meddra_term_nodes.
# ──────────────────────────────────────────────────────────────────────────
def test_total_nodes_includes_meddra_term_nodes():
    """``Phase1StagedData.total_nodes`` MUST include ``meddra_term_nodes``.

    Without this, operators checking ``staged.total_nodes`` against
    Phase 1's reported row counts would under-report by N_meddra —
    making it look like the bridge dropped data.
    """
    drugs_df = pd.DataFrame({
        "drugbank_id": ["DB00001"],
        "name": ["Drug A"],
        "inchikey": ["RZBJZVVOYQRSNH-UHFFFAOYSA-N"],
        "pubchem_cid": [1234],
    })
    sider_df = pd.DataFrame({
        "pubchem_cid": [1234],
        "umls_id_meddra": ["C0018790"],
        "side_effect_name": ["Pain"],
        "meddra_type": ["PT"],
    })
    staged = stage_phase1_to_phase2(
        {"drugs": drugs_df, "sider_adverse_events": sider_df},
        run_id="v121-test-total",
    )
    expected = (
        len(staged.compound_nodes)
        + len(staged.protein_nodes)
        + len(staged.gene_nodes)
        + len(staged.disease_nodes)
        + len(staged.clinical_outcome_nodes)
        + len(staged.pathway_nodes)
        + len(staged.meddra_term_nodes)
    )
    assert staged.total_nodes == expected, (
        f"total_nodes={staged.total_nodes} but expected {expected} — "
        f"meddra_term_nodes is NOT included in the total_nodes property."
    )


# ──────────────────────────────────────────────────────────────────────────
# Test 7: _PHASE1_EXPECTED_COLUMNS has the sider_adverse_events schema.
# ──────────────────────────────────────────────────────────────────────────
def test_phase1_expected_columns_includes_sider_adverse_events():
    """Schema validation MUST require the minimum SIDER columns."""
    assert "sider_adverse_events" in _PHASE1_EXPECTED_COLUMNS, (
        "sider_adverse_events not in _PHASE1_EXPECTED_COLUMNS — the "
        "bridge's schema validator would accept any CSV (even an empty "
        "one) as valid SIDER data, hiding schema regressions."
    )
    required = _PHASE1_EXPECTED_COLUMNS["sider_adverse_events"]
    assert "umls_id_meddra" in required, (
        "umls_id_meddra not required for sider_adverse_events — the "
        "bridge cannot build MedDRA_Term node IDs without it."
    )
    assert "side_effect_name" in required, (
        "side_effect_name not required for sider_adverse_events — the "
        "bridge cannot build MedDRA_Term node names without it."
    )


# ──────────────────────────────────────────────────────────────────────────
# Test 8: _PHASE1_ANY_OF_COLUMNS accepts pubchem_cid OR stitch_id_flat.
# ──────────────────────────────────────────────────────────────────────────
def test_phase1_any_of_columns_accepts_pubchem_or_stitch():
    """The Compound endpoint can be identified by EITHER pubchem_cid
    OR stitch_id_flat. The validator MUST accept either.
    """
    assert "sider_adverse_events" in _PHASE1_ANY_OF_COLUMNS, (
        "sider_adverse_events not in _PHASE1_ANY_OF_COLUMNS — the "
        "validator would reject SIDER CSVs that use stitch_id_flat "
        "instead of pubchem_cid (a real SIDER export format)."
    )
    groups = _PHASE1_ANY_OF_COLUMNS["sider_adverse_events"]
    # Flatten the groups and check that pubchem_cid and stitch_id_flat
    # are both accepted (in the same group, since they're alternatives).
    all_cols = [c for g in groups for c in g]
    assert "pubchem_cid" in all_cols, (
        "pubchem_cid not in any ANY_OF group for sider_adverse_events."
    )
    assert "stitch_id_flat" in all_cols, (
        "stitch_id_flat not in any ANY_OF group for sider_adverse_events — "
        "the validator would reject SIDER CSVs that use STITCH IDs only."
    )


# ──────────────────────────────────────────────────────────────────────────
# Test 9: Empty SIDER DataFrame → empty MedDRA_Term nodes + no edges
# (graceful degradation, no crash).
# ──────────────────────────────────────────────────────────────────────────
def test_empty_sider_df_graceful_degradation():
    """An empty SIDER DataFrame MUST NOT crash the bridge."""
    drugs_df = pd.DataFrame({
        "drugbank_id": ["DB00001"],
        "name": ["Drug A"],
        "inchikey": ["RZBJZVVOYQRSNH-UHFFFAOYSA-N"],
        "pubchem_cid": [1234],
    })
    empty_sider = pd.DataFrame(columns=["pubchem_cid", "umls_id_meddra", "side_effect_name"])
    staged = stage_phase1_to_phase2(
        {"drugs": drugs_df, "sider_adverse_events": empty_sider},
        run_id="v121-test-empty",
    )
    assert len(staged.meddra_term_nodes) == 0
    edge_key = ("Compound", "causes_adverse_event", "MedDRA_Term")
    assert edge_key not in staged.edges or len(staged.edges[edge_key]) == 0


# ──────────────────────────────────────────────────────────────────────────
# Test 10: Missing sider_adverse_events key in frames → no crash,
# no MedDRA_Term nodes (the bridge tolerates the absence — SIDER is
# optional in bridge-only mode).
# ──────────────────────────────────────────────────────────────────────────
def test_missing_sider_key_in_frames_tolerated():
    """The bridge MUST tolerate a missing sider_adverse_events key.

    SIDER is a Phase-2-only source per the project docx. The bridge
    MUST NOT crash when SIDER data is absent — it should produce an
    empty MedDRA_Term node list and continue. This is the graceful-
    degradation contract.
    """
    drugs_df = pd.DataFrame({
        "drugbank_id": ["DB00001"],
        "name": ["Drug A"],
        "inchikey": ["RZBJZVVOYQRSNH-UHFFFAOYSA-N"],
    })
    # NO sider_adverse_events key in frames.
    staged = stage_phase1_to_phase2({"drugs": drugs_df}, run_id="v121-test-missing")
    assert len(staged.meddra_term_nodes) == 0
    edge_key = ("Compound", "causes_adverse_event", "MedDRA_Term")
    assert edge_key not in staged.edges or len(staged.edges[edge_key]) == 0


if __name__ == "__main__":
    # Allow running this file directly (without pytest) for quick
    # verification during development.
    sys.exit(pytest.main([__file__, "-v"]))
