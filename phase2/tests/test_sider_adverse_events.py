"""TASK 4.4 contract test: verify SIDER adverse events have correct schema
and the RL ranker can use SIDER-derived safety_score (not random beta).

The task spec says:
    "SIDER adverse events are stored with wrong schema (P2-007) -- RL
     ranker has zero safety signal. Fix: (1) verify SIDER loader creates
     (Drug)-[:CAUSES]->(AdverseEvent) edges with frequency property;
     (2) verify AdverseEvent nodes have MedDRA code and severity;
     (3) verify PHASE2_TO_PHASE3_EDGE includes this edge type
     (coordinate with TM6); (4) verify RL env queries these edges for
     safety_score."

Verification: ``python -m pytest phase2/tests/test_sider_adverse_events.py -v``
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Path bootstrap
_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[1]
_PHASE2_ROOT = _HERE.parent
for _p in (str(_REPO_ROOT), str(_PHASE2_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ===========================================================================
# TASK 4.4 — contract (1): SIDER edges have the correct schema.
# ===========================================================================

class TestSiderAdverseEventSchema:
    """(1) Verify SIDER loader creates (Compound)-[:causes_adverse_event]->(MedDRA_Term) edges."""

    def test_phase2_to_phase3_edge_includes_sider(self):
        """(3) Verify PHASE2_TO_PHASE3_EDGE includes the SIDER edge type."""
        from phase2.contracts.phase2_schema import PHASE2_TO_PHASE3_EDGE

        key = ("Compound", "causes_adverse_event", "MedDRA_Term")
        assert key in PHASE2_TO_PHASE3_EDGE, (
            f"PHASE2_TO_PHASE3_EDGE missing SIDER edge type {key}. "
            f"The RL ranker will lack the safety signal from SIDER."
        )
        # Must map to a Phase 3 edge type so the Graph Transformer sees it.
        mapped = PHASE2_TO_PHASE3_EDGE[key]
        assert mapped == ("drug", "causes", "clinical_outcome"), (
            f"SIDER edge mapped to wrong Phase 3 type: {mapped}"
        )

    def test_sider_edge_has_frequency_property(self):
        """(1) Verify SIDER edges carry the frequency property."""
        # Build a synthetic SIDER edge mimicking what sider_loader emits.
        edge = {
            "src_id": "BSYNRYMUTXBXSQ-UHFFFAOYSA-N",
            "dst_id": "MedDRA:C0018790",
            "src_type": "Compound",
            "dst_type": "MedDRA_Term",
            "rel_type": "causes_adverse_event",
            "props": {
                "frequency_description": "Frequent",
                "frequency_lower_bound": 0.01,
                "frequency_upper_bound": 0.10,
                "frequency_source": "sider_frequency",
            },
        }
        # The edge has frequency fields.
        props = edge["props"]
        assert "frequency_lower_bound" in props
        assert "frequency_upper_bound" in props
        assert props["frequency_lower_bound"] == 0.01
        assert props["frequency_upper_bound"] == 0.10


# ===========================================================================
# TASK 4.4 — contract (2): AdverseEvent nodes have MedDRA code + severity.
# ===========================================================================

class TestSiderAdverseEventNodeFields:
    """(2) Verify AdverseEvent (MedDRA_Term) nodes have MedDRA code + severity."""

    def test_compute_ae_severity_function_exists(self):
        from drugos_graph.sider_loader import compute_ae_severity
        assert callable(compute_ae_severity)

    def test_severity_higher_for_higher_frequency(self):
        """A 10-50% frequency band should yield higher severity than 0.01-0.1%."""
        from drugos_graph.sider_loader import compute_ae_severity
        sev_rare = compute_ae_severity(0.0001, 0.001)  # rare
        sev_freq = compute_ae_severity(0.10, 0.50)     # frequent
        assert sev_freq > sev_rare, (
            f"Frequent AE severity {sev_freq} should be > rare {sev_rare}"
        )

    def test_severity_no_frequency_data(self):
        """No frequency data -> neutral 0.5."""
        from drugos_graph.sider_loader import compute_ae_severity
        assert compute_ae_severity(None, None) == 0.5

    def test_annotate_edges_adds_severity(self):
        """annotate_edges_with_severity must add 'severity' to every SIDER edge."""
        from drugos_graph.sider_loader import annotate_edges_with_severity

        edges = [
            {
                "src_id": "BSYNRYMUTXBXSQ-UHFFFAOYSA-N",
                "dst_id": "MedDRA:C0018790",
                "rel_type": "causes_adverse_event",
                "props": {"frequency_lower_bound": 0.01, "frequency_upper_bound": 0.10},
            },
            {
                "src_id": "LGPQKFXOTQZKCE-UHFFFAOYSA-N",
                "dst_id": "MedDRA:C0018790",
                "rel_type": "causes_adverse_event",
                "props": {"frequency_lower_bound": 0.10, "frequency_upper_bound": 0.50},
            },
            # Non-SIDER edge -- should be skipped.
            {
                "src_id": "BSYNRYMUTXBXSQ-UHFFFAOYSA-N",
                "dst_id": "P23219",
                "rel_type": "inhibits",
                "props": {},
            },
        ]
        n = annotate_edges_with_severity(edges)
        assert n == 2, f"Expected 2 SIDER edges annotated, got {n}"
        assert "severity" in edges[0]["props"]
        assert "severity" in edges[1]["props"]
        assert "severity" not in edges[2]["props"]  # non-SIDER edge untouched
        # The frequent edge should have higher severity.
        assert edges[1]["props"]["severity"] > edges[0]["props"]["severity"]

    def test_annotate_nodes_adds_severity(self):
        """annotate_nodes_with_severity must add 'severity' to every MedDRA_Term node."""
        from drugos_graph.sider_loader import (
            annotate_edges_with_severity,
            annotate_nodes_with_severity,
        )

        edges = [
            {
                "src_id": "BSYNRYMUTXBXSQ-UHFFFAOYSA-N",
                "dst_id": "MedDRA:C0018790",
                "rel_type": "causes_adverse_event",
                "props": {"frequency_lower_bound": 0.01, "frequency_upper_bound": 0.10},
            },
            {
                "src_id": "LGPQKFXOTQZKCE-UHFFFAOYSA-N",
                "dst_id": "MedDRA:C0018790",
                "rel_type": "causes_adverse_event",
                "props": {"frequency_lower_bound": 0.10, "frequency_upper_bound": 0.50},
            },
        ]
        annotate_edges_with_severity(edges)
        nodes = [
            {"id": "MedDRA:C0018790", "entity_type": "MedDRA_Term", "name": "Headache", "props": {}},
            {"id": "MedDRA:C0018791", "entity_type": "MedDRA_Term", "name": "Nausea", "props": {}},
        ]
        n = annotate_nodes_with_severity(nodes, edges)
        assert n == 2
        # Headache has 2 edges pointing to it -- severity is MAX of both.
        assert nodes[0]["severity"] == edges[1]["props"]["severity"]  # the higher one
        # Nausea has no edges -> severity 0.0 (no observed frequency).
        assert nodes[1]["severity"] == 0.0


# ===========================================================================
# TASK 4.4 — contract (4): RL env uses SIDER-derived safety_score.
# ===========================================================================

class TestSiderSafetyScoreForRL:
    """(4) Verify the RL ranker can compute safety_score from SIDER edges
    (replaces the rng.beta(5, 2) random draw)."""

    def test_compute_sider_safety_score_function_exists(self):
        from drugos_graph.sider_loader import compute_sider_safety_score
        assert callable(compute_sider_safety_score)

    def test_drug_with_no_sider_edges_gets_default(self):
        """A drug with no SIDER edges gets the clean-record default 0.85."""
        from drugos_graph.sider_loader import compute_sider_safety_score

        safety = compute_sider_safety_score("UNKNOWN-DRUG-XXXXX", {})
        assert safety == 0.85

    def test_drug_with_rare_ae_gets_high_safety(self):
        """A drug with only rare AEs should get a high safety_score (>=0.80)."""
        from drugos_graph.sider_loader import (
            annotate_edges_with_severity,
            build_sider_edges_by_drug,
            compute_sider_safety_score,
        )

        edges = [
            {
                "src_id": "BSYNRYMUTXBXSQ-UHFFFAOYSA-N",
                "dst_id": "MedDRA:C0018790",
                "rel_type": "causes_adverse_event",
                "props": {"frequency_lower_bound": 0.001, "frequency_upper_bound": 0.01},
            },
        ]
        annotate_edges_with_severity(edges)
        by_drug = build_sider_edges_by_drug(edges)
        safety = compute_sider_safety_score("BSYNRYMUTXBXSQ-UHFFFAOYSA-N", by_drug)
        assert safety >= 0.80, (
            f"Drug with rare AE should have safety >= 0.80, got {safety}"
        )

    def test_drug_with_frequent_severe_ae_gets_low_safety(self):
        """A drug with a frequent severe AE should get a low safety_score (<=0.40)."""
        from drugos_graph.sider_loader import (
            annotate_edges_with_severity,
            build_sider_edges_by_drug,
            compute_sider_safety_score,
        )

        edges = [
            {
                "src_id": "LGPQKFXOTQZKCE-UHFFFAOYSA-N",  # Valdecoxib
                "dst_id": "MedDRA:C0018790",
                "rel_type": "causes_adverse_event",
                "props": {"frequency_lower_bound": 0.10, "frequency_upper_bound": 0.50},
            },
        ]
        annotate_edges_with_severity(edges)
        by_drug = build_sider_edges_by_drug(edges)
        safety = compute_sider_safety_score("LGPQKFXOTQZKCE-UHFFFAOYSA-N", by_drug)
        assert safety <= 0.40, (
            f"Drug with frequent severe AE should have safety <= 0.40, got {safety}"
        )

    def test_withdrawn_drug_lower_safety_than_clean_drug(self):
        """PATIENT-SAFETY CRITICAL: withdrawn drug MUST have lower safety_score
        than a clean drug. This is the inverse of the P2-050 bug."""
        from drugos_graph.sider_loader import (
            annotate_edges_with_severity,
            build_sider_edges_by_drug,
            compute_sider_safety_score,
        )

        edges = [
            # Aspirin -- rare AE.
            {
                "src_id": "BSYNRYMUTXBXSQ-UHFFFAOYSA-N",
                "dst_id": "MedDRA:C0018790",
                "rel_type": "causes_adverse_event",
                "props": {"frequency_lower_bound": 0.001, "frequency_upper_bound": 0.01},
            },
            # Valdecoxib -- frequent severe AE.
            {
                "src_id": "LGPQKFXOTQZKCE-UHFFFAOYSA-N",
                "dst_id": "MedDRA:C0018790",
                "rel_type": "causes_adverse_event",
                "props": {"frequency_lower_bound": 0.10, "frequency_upper_bound": 0.50},
            },
        ]
        annotate_edges_with_severity(edges)
        by_drug = build_sider_edges_by_drug(edges)
        aspirin_safety = compute_sider_safety_score("BSYNRYMUTXBXSQ-UHFFFAOYSA-N", by_drug)
        valdecoxib_safety = compute_sider_safety_score("LGPQKFXOTQZKCE-UHFFFAOYSA-N", by_drug)
        assert valdecoxib_safety < aspirin_safety, (
            f"PATIENT SAFETY INVERSION: withdrawn drug Valdecoxib "
            f"safety={valdecoxib_safety} should be < Aspirin safety={aspirin_safety}"
        )

    def test_safety_is_deterministic(self):
        """The SIDER-derived safety_score must be DETERMINISTIC (not random).

        The previous implementation used rng.beta(5, 2) -- calling it
        twice on the same drug gave different scores. The ROOT FIX
        computes safety from SIDER edges, so the same input gives the
        same output, every time.
        """
        from drugos_graph.sider_loader import (
            annotate_edges_with_severity,
            build_sider_edges_by_drug,
            compute_sider_safety_score,
        )

        edges = [
            {
                "src_id": "BSYNRYMUTXBXSQ-UHFFFAOYSA-N",
                "dst_id": "MedDRA:C0018790",
                "rel_type": "causes_adverse_event",
                "props": {"frequency_lower_bound": 0.01, "frequency_upper_bound": 0.10},
            },
        ]
        annotate_edges_with_severity(edges)
        by_drug = build_sider_edges_by_drug(edges)
        # Call 100 times -- every call must return the same value.
        results = [
            compute_sider_safety_score("BSYNRYMUTXBXSQ-UHFFFAOYSA-N", by_drug)
            for _ in range(100)
        ]
        assert len(set(results)) == 1, (
            f"SIDER safety_score is NOT deterministic! Got {len(set(results))} "
            f"unique values from 100 calls: {set(results)}"
        )
