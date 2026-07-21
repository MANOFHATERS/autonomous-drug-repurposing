"""Fold MedDRA_Term nodes into ClinicalOutcome nodes.

Teammate 6 ROOT FIX (P2-001): SIDER emits ``:MedDRA_Term`` nodes with
``(Compound, causes_adverse_event, MedDRA_Term)`` edges. The Phase 3
EDGE_TYPES constant expects ``("drug", "causes", "clinical_outcome")``
edges — Phase 2 NEVER created ``:ClinicalOutcome`` nodes from SIDER
data, so the entire adverse-event signal was structurally absent from
the Phase 3 HeteroData. The Phase 3 GT model trained on a graph with
no safety signal — the RL ranker then ranked dangerous drugs as
"green/safe" because the model had no way to learn from adverse events.

This module folds MedDRA_Term nodes into ClinicalOutcome nodes:

1. For each ``:MedDRA_Term`` node, create a ``:ClinicalOutcome`` node
   with ID ``"CO:<meddra_id>"`` (matches the
   ``ID_PATTERNS["ClinicalOutcome"]`` regex).
   The ClinicalOutcome node preserves the MedDRA_Term's:
   - ``meddra_id``    — the canonical MedDRA code (8-digit LLT/PT)
   - ``meddra_name``  — the human-readable term (e.g. "Headache")
   - ``meddra_type``  — the hierarchy level (PT, LLT, HLT, HLGT, SOC)
   - ``outcome_kind`` — set to ``"adverse_event"`` (the SIDER source
                         only carries adverse-event data; efficacy
                         outcomes are loaded separately from
                         DrugBank indications via the existing
                         ``_load_clinical_outcomes`` bridge step).

2. Re-route every ``(Compound, causes_adverse_event, MedDRA_Term)``
   edge to ``(Compound, causes, ClinicalOutcome)``. The new edge
   carries the SIDER frequency/severity properties PLUS fold audit
   trail (``folded_from_rel``, ``folded_from_dst``). The old
   causes_adverse_event edge is removed.

3. Log a summary of the fold so operators can verify the SIDER
   adverse-event signal is now reachable via the canonical
   ``(drug, causes, clinical_outcome)`` Phase 3 edge type.

The fold is IDEMPOTENT: calling it twice on the same builder is a
no-op the second time (the MedDRA_Term nodes are still present, but
the causes_adverse_event edges have been removed — the second call
finds 0 edges to fold and emits 0 ClinicalOutcome duplicates because
``RecordingGraphBuilder.load_nodes_batch`` dedupes by node ID).

Integration
-----------
This function is called automatically at the end of
``phase1_bridge.load_into_graph`` — after the bridge has loaded
Phase 1 + SIDER data into the builder. Callers that construct a
``RecordingGraphBuilder`` manually (e.g. tests) can call
``fold_meddra_to_clinical_outcome(builder)`` directly.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def fold_meddra_to_clinical_outcome(graph_builder) -> Dict[str, int]:
    """Fold ``:MedDRA_Term`` nodes into ``:ClinicalOutcome`` nodes.

    Parameters
    ----------
    graph_builder : RecordingGraphBuilder
        In-memory graph builder with SIDER data already loaded
        (MedDRA_Term nodes + causes_adverse_event edges). Must
        implement ``get_nodes_by_type``, ``get_edges_by_type``,
        ``add_node``, ``add_edge``, ``remove_edge`` —
        ``RecordingGraphBuilder`` (phase1_bridge.py) does, as of the
        Teammate 6 ROOT FIX.

    Returns
    -------
    dict
        ``{"folded_nodes": int, "folded_edges": int, "skipped": int}``
        where ``skipped`` counts edges whose MedDRA_Term endpoint was
        not in the fold set (defensive — should be 0 in normal
        operation).

    Raises
    ------
    TypeError
        If ``graph_builder`` does not implement the required methods.
    """
    required_methods = (
        "get_nodes_by_type", "get_edges_by_type",
        "add_node", "add_edge", "remove_edge",
        "load_nodes_batch", "load_edges_batch",
    )
    missing = [m for m in required_methods if not hasattr(graph_builder, m)]
    if missing:
        raise TypeError(
            f"fold_meddra_to_clinical_outcome: graph_builder is missing "
            f"required methods {missing}. Expected a RecordingGraphBuilder "
            f"(phase1_bridge.py) or compatible. Got: "
            f"{type(graph_builder).__name__}"
        )

    # ─── Step 1: Collect MedDRA_Term nodes ───────────────────────────────
    meddra_nodes: Dict[str, Dict[str, Any]] = graph_builder.get_nodes_by_type("MedDRA_Term")
    if not meddra_nodes:
        logger.info(
            "fold_meddra_to_clinical_outcome: no MedDRA_Term nodes to fold. "
            "Either SIDER data was not loaded, or the fold has already run."
        )
        return {"folded_nodes": 0, "folded_edges": 0, "skipped": 0}

    # ─── Step 2: Build ClinicalOutcome node records ──────────────────────
    # Map: meddra_node_id -> clinical_outcome_id ("CO:<meddra_id>")
    meddra_to_co: Dict[str, str] = {}
    co_nodes_to_load: List[Dict[str, Any]] = []

    for node_id, props in meddra_nodes.items():
        # Prefer the canonical meddra_id field; fall back to the node ID
        # itself (stripping any "MedDRA:" prefix). This handles both
        # SIDER loader output (meddra_id field populated) and any future
        # loader that uses the MedDRA:C<digits> format as the node ID.
        meddra_id_raw = props.get("meddra_id")
        if not meddra_id_raw:
            # Strip "MedDRA:" prefix if present
            if node_id.startswith("MedDRA:"):
                meddra_id_raw = node_id[len("MedDRA:"):]
            else:
                meddra_id_raw = node_id
        meddra_id = str(meddra_id_raw).strip()

        # ClinicalOutcome IDs use the "CO:" prefix per
        # ID_PATTERNS["ClinicalOutcome"] = ^CO:[A-Za-z0-9_.:-]+$
        co_id = f"CO:{meddra_id}"
        meddra_to_co[node_id] = co_id

        meddra_name = (
            props.get("meddra_name")
            or props.get("name")
            or props.get("side_effect_name")
            or ""
        )
        meddra_type = props.get("meddra_type") or ""

        # FLATTEN all properties into the top-level record dict.
        # The whitelist (_apply_node_whitelist) checks each top-level
        # key against NODE_PROPERTY_WHITELIST["ClinicalOutcome"]
        # individually. If we nested them under 'props', the entire
        # 'props' sub-dict would be stripped (because 'props' is not
        # in the whitelist). The ClinicalOutcome whitelist (Teammate 6
        # ROOT FIX) includes meddra_id, meddra_name, meddra_type,
        # outcome_kind, folded_from — so these survive the whitelist.
        co_nodes_to_load.append({
            "id": co_id,
            "name": meddra_name,
            "entity_type": "ClinicalOutcome",
            "source": "sider_fold",
            "meddra_id": meddra_id,
            "meddra_name": meddra_name,
            "meddra_type": meddra_type,
            "outcome_kind": "adverse_event",
            "folded_from": node_id,
        })

    # Load ClinicalOutcome nodes via the batch API (single call is more
    # efficient than N add_node calls, and dedup is handled by
    # RecordingGraphBuilder.load_nodes_batch).
    if co_nodes_to_load:
        graph_builder.load_nodes_batch(
            label="ClinicalOutcome",
            nodes=co_nodes_to_load,
            source="sider_fold",
        )

    # ─── Step 3: Re-route causes_adverse_event edges ────────────────────
    adverse_edges = graph_builder.get_edges_by_type("causes_adverse_event")
    co_edges_to_load: List[Dict[str, Any]] = []
    removed_count = 0
    skipped_count = 0

    for edge_tuple in adverse_edges:
        src_label, rel_type, dst_label, src_id, dst_id, props = edge_tuple
        # Only fold edges that point to MedDRA_Term nodes
        if dst_label != "MedDRA_Term":
            skipped_count += 1
            continue
        co_id = meddra_to_co.get(dst_id)
        if co_id is None:
            # The MedDRA_Term endpoint was not in our fold set — log
            # and skip. This is defensive: in normal operation, every
            # causes_adverse_event edge should point to a MedDRA_Term
            # node that get_nodes_by_type("MedDRA_Term") returned.
            logger.warning(
                "fold_meddra_to_clinical_outcome: edge "
                "(%s, %s, %s) src=%s dst=%s — dst MedDRA_Term not in "
                "fold set. Skipping (defensive).",
                src_label, rel_type, dst_label, src_id, dst_id,
            )
            skipped_count += 1
            continue

        # Build the new causes edge record. FLATTEN SIDER properties
        # (frequency, severity) and fold audit trail into the top-level
        # record dict (NOT nested under 'props') so they survive
        # _apply_edge_whitelist. The EDGE_PROPERTY_WHITELIST entry for
        # ("Compound", "causes", "ClinicalOutcome") (Teammate 6 ROOT
        # FIX in kg_builder.py) includes frequency, severity,
        # folded_from_rel, folded_from_dst, outcome_kind,
        # evidence_strength, source_count — so all of these survive.
        src_props = props or {}
        new_edge_record: Dict[str, Any] = {
            "src_id": src_id,
            "dst_id": co_id,
            "source": src_props.get("source", "sider_fold"),
            # Fold audit trail
            "folded_from_rel": rel_type,
            "folded_from_dst": dst_id,
            "outcome_kind": "adverse_event",
            # Edge-feature properties consumed by pyg_builder to build
            # edge_attr. Default to 1.0/1.0/1 if not present in the
            # original edge.
            "confidence": float(src_props.get("confidence", 1.0) or 1.0),
            "evidence_strength": float(src_props.get("evidence_strength", 1.0) or 1.0),
            "source_count": float(src_props.get("source_count", 1) or 1),
        }
        # Copy through any SIDER frequency/severity props that the
        # whitelist allows (frequency, frequency_lower_bound,
        # frequency_upper_bound, severity, meddra_name, etc.).
        _passthrough_keys = (
            "frequency", "frequency_description",
            "frequency_lower_bound", "frequency_upper_bound",
            "frequency_source", "meddra_type", "meddra_code",
            "meddra_name", "severity",
        )
        for _pk in _passthrough_keys:
            if _pk in src_props:
                new_edge_record[_pk] = src_props[_pk]
        co_edges_to_load.append(new_edge_record)

        # Remove the old causes_adverse_event edge
        graph_builder.remove_edge(src_label, rel_type, dst_label, src_id, dst_id)
        removed_count += 1

    # Load the new (Compound, causes, ClinicalOutcome) edges via the
    # batch API.
    if co_edges_to_load:
        graph_builder.load_edges_batch(
            src_label="Compound",
            rel_type="causes",
            dst_label="ClinicalOutcome",
            edges=co_edges_to_load,
            source="sider_fold",
        )

    # ─── Step 4: Log summary ────────────────────────────────────────────
    logger.info(
        "fold_meddra_to_clinical_outcome: folded %d MedDRA_Term nodes "
        "into ClinicalOutcome nodes; re-routed %d causes_adverse_event "
        "edges to (Compound, causes, ClinicalOutcome); skipped %d.",
        len(meddra_nodes), removed_count, skipped_count,
    )

    return {
        "folded_nodes": len(meddra_nodes),
        "folded_edges": removed_count,
        "skipped": skipped_count,
    }
