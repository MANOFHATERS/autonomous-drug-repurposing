"""Team 6 (Phase 3) regression tests — P3-009 edge-mapping unification
and SH-006 service version-string consistency.

These tests LOCK IN the root fixes applied in this branch so that future
changes cannot silently re-introduce the divergences:

  P3-009 (regression caught live): BiomedicalGraphBuilder defined its OWN
    local _PHASE2_TO_PHASE3_EDGE_TYPE (11 entries) instead of importing the
    shared PHASE2_TO_PHASE3_EDGE (30 entries) from drugos_graph.schema_mappings.
    The two adapter paths (adapt_phase2_to_phase3 via phase2_adapter, and
    from_phase1_staged_data via graph_builder) produced DIFFERENT Phase 3
    graphs from the same Phase 2 data. Root fix: graph_builder now imports
    and references the shared mapping (the INT-004 consolidation that
    phase2_adapter already did but graph_builder had missed).

  SH-006 (residual): service.py /top-k returned modelVersion "gt_v110" while
    /predict returned "gt_v113" — version-string drift between two endpoints
    in the same service. Root fix: both now return "gt_v113".

Run: pytest tests/test_tm6_p3_009_unification_regression.py -v
"""
import os
import sys

# Ensure phase2 is importable (graph_builder inserts it, but be explicit for tests).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PHASE2 = os.path.join(_REPO_ROOT, "phase2")
for _p in (_REPO_ROOT, _PHASE2):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def test_p3_009_builder_uses_shared_edge_mapping():
    """BiomedicalGraphBuilder._PHASE2_TO_PHASE3_EDGE_TYPE must be IDENTICAL
    (same keys + same values) to the shared PHASE2_TO_PHASE3_EDGE from
    drugos_graph.schema_mappings. The local copy was previously a stale
    11-entry subset that silently dropped 19 edge types."""
    from drugos_graph.schema_mappings import PHASE2_TO_PHASE3_EDGE
    from graph_transformer.data.graph_builder import BiomedicalGraphBuilder

    builder_mapping = BiomedicalGraphBuilder._PHASE2_TO_PHASE3_EDGE_TYPE
    assert set(builder_mapping.keys()) == set(PHASE2_TO_PHASE3_EDGE.keys()), (
        "P3-009 REGRESSION: builder edge mapping diverged from shared mapping. "
        f"Only in builder: {set(builder_mapping) - set(PHASE2_TO_PHASE3_EDGE)}. "
        f"Only in shared: {set(PHASE2_TO_PHASE3_EDGE) - set(builder_mapping)}."
    )
    for key, expected in PHASE2_TO_PHASE3_EDGE.items():
        actual = builder_mapping[key]
        assert actual == expected, (
            f"P3-009 REGRESSION: key {key} maps differently: "
            f"builder={actual} shared={expected}"
        )


def test_p3_009_builder_uses_shared_node_mapping():
    """BiomedicalGraphBuilder._PHASE2_TO_PHASE3_NODE_TYPE must be IDENTICAL
    to the shared PHASE2_TO_PHASE3_NODE (includes Gene/MedDRA_Term -> None)."""
    from drugos_graph.schema_mappings import PHASE2_TO_PHASE3_NODE
    from graph_transformer.data.graph_builder import BiomedicalGraphBuilder

    builder_mapping = BiomedicalGraphBuilder._PHASE2_TO_PHASE3_NODE_TYPE
    assert set(builder_mapping.keys()) == set(PHASE2_TO_PHASE3_NODE.keys()), (
        "P3-009 REGRESSION: builder node mapping diverged from shared mapping."
    )
    for key, expected in PHASE2_TO_PHASE3_NODE.items():
        assert builder_mapping[key] == expected, (
            f"P3-009 REGRESSION: node {key} maps differently: "
            f"builder={builder_mapping[key]} shared={expected}"
        )


def test_p3_009_builder_includes_sider_and_metabolism_edges():
    """The previously-dropped edge types (SIDER adverse events, drug-metabolism,
    Gene edges) must now be present in the builder mapping (they were silently
    dropped before the unification, starving the GT model of safety/PPI signal
    in the from_phase1_staged_data path)."""
    from graph_transformer.data.graph_builder import BiomedicalGraphBuilder

    m = BiomedicalGraphBuilder._PHASE2_TO_PHASE3_EDGE_TYPE
    # SIDER adverse-event edges -> ('drug','causes','clinical_outcome')
    assert ("Compound", "causes_side_effect", "Side Effect") in m
    assert ("Compound", "causes_adverse_event", "MedDRA_Term") in m
    assert m[("Compound", "causes_adverse_event", "MedDRA_Term")] == (
        "drug", "causes", "clinical_outcome",
    )
    # Drug-metabolism edge
    assert ("Compound", "metabolized_by", "Protein") in m
    assert m[("Compound", "metabolized_by", "Protein")] == (
        "drug", "modulates", "protein",
    )
    # Gene -> pathway/disease derived edges
    assert ("Gene", "associated_with", "Disease") in m
    assert m[("Gene", "associated_with", "Disease")] == (
        "pathway", "disrupted_in", "disease",
    )


def test_p3_009_unknown_maps_to_neutral_binds_not_inhibits():
    """('Compound','unknown','Protein') must map to neutral ('drug','binds',
    'protein') — NOT to inhibits/activates/modulates (which would fabricate a
    specific mechanism). 'binds' is the honest direction-unknown binding edge
    and preserves drug-protein connectivity for multi-hop reasoning."""
    from graph_transformer.data.graph_builder import BiomedicalGraphBuilder

    m = BiomedicalGraphBuilder._PHASE2_TO_PHASE3_EDGE_TYPE
    val = m.get(("Compound", "unknown", "Protein"))
    assert val == ("drug", "binds", "protein"), (
        f"expected unknown -> ('drug','binds','protein'), got {val}"
    )


def test_sh006_topk_and_predict_share_model_version():
    """SH-006 residual fix: /top-k and /predict must return the SAME
    modelVersion string (previously /top-k was 'gt_v110', /predict 'gt_v113')."""
    import re

    with open(os.path.join(_REPO_ROOT, "graph_transformer", "service.py")) as f:
        src = f.read()
    versions = re.findall(r'"modelVersion":\s*"([^"]+)"', src)
    assert len(versions) >= 2, f"expected >=2 modelVersion literals, found {len(versions)}"
    assert len(set(versions)) == 1, (
        "SH-006 REGRESSION: service.py has inconsistent modelVersion strings: "
        f"{versions}. All endpoints must report the same version."
    )
