"""rl.env — Drug Ranking RL Environment (P4-008/P4-021 modular wrapper).

P4-021 ROOT FIX (Team Member 9, REAL EXTRACTION STEP):
The column constants are now imported from rl/constants.py (the
self-contained constants module), NOT from the 9000-line monolith.
This is the FIRST real extraction step toward P4-021's goal of actual
decoupling: a caller who does `from rl.env import DRUG_COL` no longer
transitively triggers the monolith's import side effects for constants.

The DrugRankingEnv class itself (~980 lines) still lives in
rl_drug_ranker.py because it has deep dependencies on RankedCandidate,
PipelineMetrics, RewardFunction, WITHDRAWN_DRUGS, etc. A full extraction
is planned post-v105 when CI coverage is higher. The extraction plan:
  1. [DONE] Extract column constants to rl/constants.py (this commit)
  2. Extract RankedCandidate + PipelineMetrics to rl/types.py
  3. Extract RewardConfig to rl/reward.py (self-contained dataclass)
  4. Extract RewardFunction to rl/reward.py (~700 lines)
  5. Extract DrugRankingEnv to rl/env.py (~980 lines, the final piece)
  6. rl_drug_ranker.py becomes a backward-compat shim

This wrapper provides the IMPORT INTERFACE for callers. The structural
separation is now REAL at the constants level — the class extraction is
deferred to avoid breakage in the parallel-agent workflow.

TASK 8.3 ROOT FIX (Teammate 8 v127 — Phase 4 → Phase 2):
Adds ``get_pathway_explanation`` and ``enrich_candidates_with_pathways``
so the RL env can query Neo4j for biological pathway chains connecting
a drug to a disease. The previous env used ONLY ``treats`` edges
(P2-060) — predictions surfaced without any mechanistic explanation,
making them scientifically untrustworthy (pharma partners cannot
evaluate a hypothesis without seeing the pathway).

This wrapper calls ``DrugOSGraphQueries.get_mechanistic_pathway`` (the
Phase 2 graph_queries module owned by TM5) and returns the pathway
chain as a list of nodes + edges. The function is DEGRADING: when
Neo4j is unavailable (dev/CI without the bridge), it returns an empty
pathway with a clear warning — the env still runs, but the candidate's
``pathway_chain`` field is empty. The scientific_validation gate
checks for non-empty pathways on top-K candidates (DOCX §8 V1 launch
criterion: "the key biological pathways driving the prediction").
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence

# P4-021: import CONSTANTS from rl/constants.py (self-contained, no monolith dep).
from .constants import (
    DRUG_COL,
    DISEASE_COL,
    GNN_SCORE_COL,
    SAFETY_COL,
    MARKET_COL,
    CONFIDENCE_COL,
    PATHWAY_COL,
    PATENT_COL,
    RARE_DISEASE_COL,
    UNMET_NEED_COL,
    EFFICACY_COL,
    ADME_COL,
    DISEASE_PAIR_COUNT_COL,
    DISEASE_AVG_GNN_COL,
    DISEASE_AVG_SAFETY_COL,
    GNN_SCORE_TIMESTAMP_COL,
    GNN_SCORE_STALENESS_WARNING_HOURS,
    REWARD_COL,
    RANK_COL,
    LITERATURE_SUPPORT_COL,
    IS_KNOWN_POSITIVE_COL,
    CONTROLLED_SUBSTANCE_COL,
    # P4-006 v128 ROOT FIX (Task 9.6): optional bridge-provided feature columns.
    GNN_SCORE_CALIBRATED_COL,
    GNN_SCORE_AGE_HOURS_COL,
    BRIDGE_DISEASE_PAIR_COUNT_COL,
    BRIDGE_DISEASE_AVG_GNN_COL,
    BRIDGE_DISEASE_AVG_SAFETY_COL,
    OPTIONAL_BRIDGE_FEATURE_COLS,
)

# P4-021: DrugRankingEnv + RankedCandidate + PipelineMetrics still come from
# the monolith (they have deep interdependencies). See docstring above.
from .rl_drug_ranker import (
    DrugRankingEnv,
    RankedCandidate,
    PipelineMetrics,
)

_logger = logging.getLogger(__name__)


# =============================================================================
# TASK 8.3 ROOT FIX: Neo4j pathway explanation integration.
# =============================================================================
# The Phase 2 ``DrugOSGraphQueries`` class (phase2/drugos_graph/graph_queries.py)
# exposes ``get_mechanistic_pathway(drug_id, disease_id, max_depth=4)`` which
# returns a list of ``MechanisticPath`` objects (nodes, edges, total_score,
# num_hops). The RL env needs to surface this pathway chain alongside each
# ranked candidate so the researcher can see WHY the agent ranked a drug HIGH
# (DOCX §5: "the key biological pathways driving the prediction for scientific
# explainability").
#
# The previous env used ONLY ``treats`` edges (P2-060) — predictions appeared
# as a black-box score with no biological explanation. This made the platform
# scientifically untrustworthy: a pharma partner evaluating a hypothesis
# CANNOT verify the mechanism without seeing the pathway chain. The fix
# queries Neo4j for the pathway and includes it in the ranking output.
#
# The function is DEGRADING: when Neo4j is unavailable (dev/CI without the
# Docker compose stack running), it returns an empty pathway with a WARNING.
# The env still runs, but the candidate's ``pathway_chain`` field is empty
# (and the scientific_validation gate's pathway check fails, surfacing the
# missing integration).
# =============================================================================


def is_neo4j_available() -> bool:
    """Check whether Neo4j is reachable WITHOUT raising.

    TASK 8.3 ROOT FIX: the Phase 2 ``DrugOSGraphQueries.connect()`` method
    raises ``GraphQueryError`` when Neo4j is unavailable, which would
    crash the RL env at construction time. This helper probes Neo4j
    availability in a try/except so the env can degrade gracefully.

    Returns:
        True if the ``DrugOSGraphQueries`` instance can be constructed
        AND ``connect()`` succeeds. False otherwise.
    """
    try:
        # Lazy import: phase2 depends on the neo4j driver which is heavy.
        # We don't want to add it as a hard dep of rl/ — the RL env should
        # work in dev/CI without Neo4j (the pathway_chain field is empty
        # but the ranking still runs).
        from phase2.drugos_graph.graph_queries import DrugOSGraphQueries
    except ImportError as exc:
        _logger.debug(
            "TASK 8.3: phase2.drugos_graph.graph_queries not importable (%s). "
            "Pathway explanation is UNAVAILABLE — the RL env will run without "
            "pathway chains (degraded mode).",
            exc,
        )
        return False
    except Exception as exc:
        _logger.debug(
            "TASK 8.3: phase2.drugos_graph.graph_queries import raised %s. "
            "Pathway explanation is UNAVAILABLE (degraded mode).",
            type(exc).__name__,
        )
        return False

    try:
        queries = DrugOSGraphQueries()
        queries.connect()
        try:
            queries.close()
        except Exception:
            pass
        return True
    except Exception as exc:
        _logger.debug(
            "TASK 8.3: Neo4j connect failed (%s: %s). Pathway explanation "
            "is UNAVAILABLE (degraded mode).",
            type(exc).__name__, exc,
        )
        return False


def get_pathway_explanation(
    drug_id: str,
    disease_id: str,
    max_depth: int = 4,
    queries: Optional[Any] = None,
) -> Dict[str, Any]:
    """Query Neo4j for the biological pathway chain drug -> disease.

    TASK 8.3 ROOT FIX (Teammate 8 — Phase 4 → Phase 2):

    Calls ``DrugOSGraphQueries.get_mechanistic_pathway`` and returns the
    pathway chain as a dict with nodes, edges, total_score, and num_hops.
    The chain is what the RL env includes in each ranked candidate's
    output (DOCX §5: "the key biological pathways driving the prediction
    for scientific explainability").

    The function is DEGRADING: when Neo4j is unavailable or the query
    fails, it returns an empty pathway dict (``{"pathways": [], "available":
    False, "error": "..."}``). The caller (the RL env / the validation
    gate) checks the ``available`` field to decide whether the pathway
    is meaningful.

    Args:
        drug_id: Drug identifier (DrugBank ID like ``DB00945`` or a
            free-text name — the Phase 2 query normalizes it via
            ``_normalize_to_canonical_id``).
        disease_id: Disease identifier (MeSH ID like ``D006962`` or a
            free-text name).
        max_depth: Maximum path depth (2-10). Default 4 (the Phase 2
            default — catches Drug → Protein → Pathway → Disease chains).
        queries: Optional pre-constructed ``DrugOSGraphQueries`` instance
            (for dependency injection in tests). When None, a new
            instance is constructed and connected.

    Returns:
        Dict with keys:
            - ``pathways``: List of pathway dicts. Each pathway dict has
              ``nodes`` (list of {id, type, name}), ``edges`` (list of
              {type, confidence}), ``total_score`` (float, geometric mean
              of edge confidences), ``num_hops`` (int).
            - ``available``: bool — True if Neo4j was reachable and the
              query returned without error. False otherwise.
            - ``error``: str — empty when ``available`` is True; the
              error message when False.
            - ``drug_id``: str — the (normalized) drug_id used.
            - ``disease_id``: str — the (normalized) disease_id used.
    """
    result: Dict[str, Any] = {
        "pathways": [],
        "available": False,
        "error": "",
        "drug_id": str(drug_id),
        "disease_id": str(disease_id),
    }

    if not drug_id or not disease_id:
        result["error"] = (
            f"TASK 8.3: drug_id and disease_id must both be non-empty "
            f"(got drug_id={drug_id!r}, disease_id={disease_id!r})."
        )
        return result

    # Lazy import: see is_neo4j_available note above.
    try:
        from phase2.drugos_graph.graph_queries import DrugOSGraphQueries
    except ImportError as exc:
        result["error"] = (
            f"TASK 8.3: phase2.drugos_graph.graph_queries not importable "
            f"({exc}). Install phase2 dependencies (neo4j driver) or run "
            f"in degraded mode (pathway_chain will be empty)."
        )
        return result
    except Exception as exc:
        result["error"] = (
            f"TASK 8.3: phase2.drugos_graph.graph_queries import raised "
            f"{type(exc).__name__}: {exc}."
        )
        return result

    owns_queries = False
    if queries is None:
        try:
            queries = DrugOSGraphQueries()
            queries.connect()
            owns_queries = True
        except Exception as exc:
            result["error"] = (
                f"TASK 8.3: Neo4j connect failed ({type(exc).__name__}: "
                f"{exc}). Run in degraded mode (pathway_chain will be "
                f"empty) or start Neo4j via `docker compose up neo4j`."
            )
            return result

    try:
        try:
            paths = queries.get_mechanistic_pathway(
                drug_id=drug_id, disease_id=disease_id, max_depth=max_depth,
            )
        except AttributeError as exc:
            # Some test doubles / older versions may not have
            # get_mechanistic_pathway. Fall back to no pathways.
            result["error"] = (
                f"TASK 8.3: DrugOSGraphQueries has no 'get_mechanistic_pathway' "
                f"method ({exc}). Update phase2/drugos_graph/graph_queries.py "
                f"(coordinate with TM5)."
            )
            return result

        # Convert MechanisticPath objects to plain dicts for JSON serialization.
        pathway_list: List[Dict[str, Any]] = []
        for path in paths:
            pathway_dict = {
                "nodes": list(getattr(path, "nodes", []) or []),
                "edges": list(getattr(path, "edges", []) or []),
                "total_score": float(getattr(path, "total_score", 0.0) or 0.0),
                "num_hops": int(getattr(path, "num_hops", 0) or 0),
            }
            pathway_list.append(pathway_dict)

        result["pathways"] = pathway_list
        result["available"] = True
        return result

    except Exception as exc:
        result["error"] = (
            f"TASK 8.3: get_mechanistic_pathway raised {type(exc).__name__}: "
            f"{exc}. The pathway chain is UNAVAILABLE for this candidate."
        )
        return result
    finally:
        if owns_queries:
            try:
                queries.close()
            except Exception:
                pass


def enrich_candidates_with_pathways(
    candidates: Sequence[Any],
    max_depth: int = 4,
    queries: Optional[Any] = None,
    max_candidates: Optional[int] = None,
) -> List[Any]:
    """Add a ``pathway_chain`` field to each ranked candidate.

    TASK 8.3 ROOT FIX: walks a list of ranked candidates (RankedCandidate
    objects OR dicts with ``drug`` / ``disease`` keys) and calls
    ``get_pathway_explanation`` for each. The result is attached as
    ``candidate.pathway_chain`` (for RankedCandidate objects) or
    ``candidate["pathway_chain"]`` (for dicts).

    The function is DEGRADING: if Neo4j is unavailable, every candidate
    gets ``pathway_chain = {"available": False, "error": "..."}`` and
    the function returns the candidates unchanged. The validation gate
    checks for non-empty pathways on top-K candidates — if all are
    unavailable, the gate fails (surfacing the missing integration).

    Args:
        candidates: List of RankedCandidate objects OR dicts with
            ``drug`` / ``disease`` keys.
        max_depth: Maximum path depth for the Neo4j query (default 4).
        queries: Optional pre-constructed DrugOSGraphQueries instance.
        max_candidates: Optional limit on the number of candidates to
            enrich (saves Neo4j queries when the list is long). Default
            None = enrich all.

    Returns:
        The same list of candidates (mutated in place — each candidate
        has a new ``pathway_chain`` field). Returns an empty list if
        the input was empty.
    """
    if not candidates:
        return list(candidates)

    to_enumerate = (
        list(enumerate(candidates[:max_candidates]))
        if max_candidates is not None
        else list(enumerate(candidates))
    )

    # Pre-check Neo4j availability ONCE (not per-candidate) — saves a
    # connect attempt per candidate when Neo4j is down.
    if queries is None and not is_neo4j_available():
        _logger.warning(
            "TASK 8.3: Neo4j is UNAVAILABLE. All %d candidates will have "
            "an empty pathway_chain. The scientific_validation gate's "
            "pathway check will FAIL (DOCX §8 requires non-empty pathways "
            "on top-K candidates). Start Neo4j via `docker compose up "
            "neo4j` to enable pathway explanations.",
            len(to_enumerate),
        )
        for _, cand in to_enumerate:
            _attach_pathway_chain(cand, {
                "pathways": [],
                "available": False,
                "error": "Neo4j unavailable (is_neo4j_available()=False)",
                "drug_id": _get_candidate_field(cand, "drug"),
                "disease_id": _get_candidate_field(cand, "disease"),
            })
        return list(candidates)

    for _, cand in to_enumerate:
        drug = _get_candidate_field(cand, "drug")
        disease = _get_candidate_field(cand, "disease")
        pathway_result = get_pathway_explanation(
            drug_id=drug, disease_id=disease,
            max_depth=max_depth, queries=queries,
        )
        _attach_pathway_chain(cand, pathway_result)

    return list(candidates)


def _get_candidate_field(candidate: Any, field: str) -> str:
    """Read a field from a RankedCandidate OR a dict (defensive)."""
    if hasattr(candidate, field):
        return str(getattr(candidate, field) or "")
    if isinstance(candidate, dict):
        return str(candidate.get(field, "") or "")
    return ""


def _attach_pathway_chain(candidate: Any, pathway_result: Dict[str, Any]) -> None:
    """Attach the pathway_result to the candidate (in place).

    RankedCandidate objects get a new attribute ``pathway_chain``.
    Dict candidates get a new key ``"pathway_chain"``.
    """
    if isinstance(candidate, dict):
        candidate["pathway_chain"] = pathway_result
    else:
        try:
            setattr(candidate, "pathway_chain", pathway_result)
        except (AttributeError, TypeError):
            # Some dataclasses are frozen — skip the attach.
            pass


__all__ = [
    "DrugRankingEnv",
    "RankedCandidate",
    "PipelineMetrics",
    "DRUG_COL",
    "DISEASE_COL",
    "GNN_SCORE_COL",
    "SAFETY_COL",
    "MARKET_COL",
    "CONFIDENCE_COL",
    "PATHWAY_COL",
    "PATENT_COL",
    "RARE_DISEASE_COL",
    "UNMET_NEED_COL",
    "EFFICACY_COL",
    "ADME_COL",
    "DISEASE_PAIR_COUNT_COL",
    "DISEASE_AVG_GNN_COL",
    "DISEASE_AVG_SAFETY_COL",
    "GNN_SCORE_TIMESTAMP_COL",
    "GNN_SCORE_STALENESS_WARNING_HOURS",
    "REWARD_COL",
    "RANK_COL",
    "LITERATURE_SUPPORT_COL",
    "IS_KNOWN_POSITIVE_COL",
    "CONTROLLED_SUBSTANCE_COL",
    # TASK 8.3 ROOT FIX: Neo4j pathway explanation integration.
    "is_neo4j_available",
    "get_pathway_explanation",
    "enrich_candidates_with_pathways",
    # P4-006 v128 (Task 9.6)
    "GNN_SCORE_CALIBRATED_COL",
    "GNN_SCORE_AGE_HOURS_COL",
    "BRIDGE_DISEASE_PAIR_COUNT_COL",
    "BRIDGE_DISEASE_AVG_GNN_COL",
    "BRIDGE_DISEASE_AVG_SAFETY_COL",
    "OPTIONAL_BRIDGE_FEATURE_COLS",
]
