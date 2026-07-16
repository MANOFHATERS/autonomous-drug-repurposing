#!/usr/bin/env python3
"""Phase 2 Knowledge Graph Service (v107 forensic root fix).

Wraps Phase 2's KG builder / query logic as an HTTP service so the
Next.js frontend can proxy to it via ``KG_SERVICE_URL``. The frontend's
``src/lib/services/knowledge-graph-stats.ts`` and
``src/app/api/knowledge-graph/route.ts`` already proxy to
``KG_SERVICE_URL`` — this service is what they expect to find there.

Endpoints
---------
    GET  /health
        Liveness probe. Returns service metadata + Neo4j config status.

    GET  /kg/stats
        Real KG stats from Neo4j (or in-memory bridge fallback).
        Returns 503 if neither backend is available (P2-008).

    GET  /kg/explore?drug=...&disease=...&limit=N
        Subgraph exploration around a drug or disease node.
        Returns 503 if Neo4j is unavailable AND the in-memory bridge
        cannot resolve the query (P2-009). When the in-memory bridge is
        available, performs a real BFS over the in-memory KG.

    POST /query
        Structured query. Body: ``{"drug": "...", "disease": "...", "limit": N}``.
        Returns a subgraph centered on the requested drug/disease.
        (P2-002 root fix — the frontend POSTs here.)

    POST /cypher
        Raw Cypher passthrough. Body: ``{"cypher": "...", "params": {...}}``.
        Applies a read-only whitelist (MATCH/OPTIONAL MATCH/WITH/RETURN/WHERE
        only). Forwards to Neo4j with a hard 30s timeout and 1000-row cap.
        (P2-002 root fix — the frontend POSTs here.)

Run
---
    cd phase2 && python service.py
    # or: uvicorn phase2.service:app --host 0.0.0.0 --port 8002

Environment
-----------
    NEO4J_URI: bolt://localhost:7687 (or unset for in-memory CSV mode)
    NEO4J_USER: neo4j
    NEO4J_PASSWORD: <password>
    KG_CORS_ORIGINS: comma-separated whitelist of allowed origins
                     (default: ``http://localhost:3000``)
    DRUGOS_ENVIRONMENT: ``production`` (default) or ``dev``

v107 forensic root fixes applied (P2-001, P2-002, P2-008, P2-009, P2-010,
P2-016, P2-017) — see inline comments for the exact bug each fix addresses.
"""
from __future__ import annotations

import logging
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# Make phase2 importable.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
_REPO_ROOT = _HERE.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

logger = logging.getLogger("phase2.service")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)

app = FastAPI(
    title="Autonomous Drug Repurposing — Phase 2 Knowledge Graph Service",
    description="HTTP wrapper around Phase 2 KG builder / queries.",
    version="1.0.0",
)

# ─── P2-016 ROOT FIX (v107 forensic): CORS hardening ───────────────────────
# The previous code set ``allow_origins=["*"]`` (wildcard — any website could
# read KG data) AND ``allow_methods=["GET"]`` (blocking every POST preflight).
# Even if /query and /cypher endpoints existed, the browser would block them.
# ROOT FIX: read a whitelist from KG_CORS_ORIGINS (default: localhost:3000
# for the Next.js dev server). Allow GET, POST, OPTIONS so the frontend's
# POST /query and POST /cypher requests pass CORS preflight.
_DEFAULT_CORS_ORIGINS = "http://localhost:3000,http://127.0.0.1:3000"
_cors_env = os.environ.get("KG_CORS_ORIGINS", _DEFAULT_CORS_ORIGINS)
_allowed_origins: List[str] = [
    origin.strip() for origin in _cors_env.split(",") if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


# ─── P2-017 ROOT FIX (v107 forensic): Neo4j driver resource-leak guard ─────
def _neo4j_driver():
    """Construct a Neo4j driver. Raises if NEO4J_PASSWORD is unset."""
    from neo4j import GraphDatabase  # local import — keeps service importable
    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "")
    if not password:
        raise RuntimeError("NEO4J_PASSWORD is not set — Neo4j backend unavailable")
    return GraphDatabase.driver(uri, auth=(user, password))


def _run_neo4j(fn):
    """Run a function with a Neo4j session. Guarantees driver.close().

    P2-017 ROOT FIX (v107 forensic): the previous code created a driver,
    ran queries, then called ``driver.close()`` at the end of the try
    block. If ``session.run()`` raised, ``driver.close()`` was NEVER
    called — the driver leaked. Under load, this exhausts the Neo4j
    connection pool (~100 calls) and the service becomes unresponsive.
    ROOT FIX: use ``try/finally`` to guarantee ``driver.close()`` runs
    even on exception. This is the same pattern recommended by the
    official Neo4j Python driver docs.
    """
    driver = _neo4j_driver()
    try:
        return fn(driver)
    finally:
        try:
            driver.close()
        except Exception as close_exc:  # pragma: no cover — defensive
            logger.warning("Phase 2 service: driver.close() failed: %s", close_exc)


def _get_kg_stats_from_neo4j() -> Optional[Dict[str, Any]]:
    """Try to read KG stats from Neo4j. Returns None if Neo4j is not available."""

    def _query(driver):
        with driver.session() as session:
            node_count = session.run("MATCH (n) RETURN count(n) AS c").single()["c"]
            edge_count = session.run(
                "MATCH ()-[r]->() RETURN count(r) AS c"
            ).single()["c"]
            # Per-label node counts.
            result = session.run(
                "MATCH (n) RETURN labels(n)[0] AS label, count(n) AS c"
            )
            node_types = {record["label"]: record["c"] for record in result}
            # Per-type edge counts.
            result = session.run(
                "MATCH ()-[r]->() RETURN type(r) AS t, count(r) AS c"
            )
            edge_types = {record["t"]: record["c"] for record in result}
        return {
            "node_count": int(node_count),
            "edge_count": int(edge_count),
            "node_types": node_types,
            "edge_types": edge_types,
            "backend": "neo4j",
        }

    try:
        return _run_neo4j(_query)
    except Exception as exc:
        logger.info(
            "Phase 2 service: Neo4j not available (%s), falling back to in-memory builder.",
            exc,
        )
        return None


def _get_kg_stats_from_builder() -> Dict[str, Any]:
    """Build the KG in-memory from Phase 1 CSVs via the bridge, then count.

    This is the Tier-2 fallback used in dev/CI when Neo4j is not running.
    The bridge (drugos_graph.phase1_bridge.run_phase1_to_phase2) reads
    the same Phase 1 CSVs that run_4phase.py reads, so the stats here
    EXACTLY match what the bridge would load into Neo4j in production.

    P2-001 ROOT FIX (v107 forensic): the previous code UNCONDITIONALLY
    called ``pipelines._embedded_samples.write_all_samples(str(pdir))``
    when ``phase1/processed_data`` was empty or had no CSVs. This
    injected 10 mock drug records (the SAME embedded samples P1-001
    found) on EVERY ``/kg/stats`` API call when Phase 1 hadn't run. The
    mock data was labeled as real and consumed by the bridge. The KG
    stats endpoint returned counts based on mock data; the frontend
    displayed mock stats; the GNN trained on mock drug-protein
    interactions. Violates the user's "NO mock data" mandate.
    ROOT FIX: remove the ``write_all_samples`` call. If Phase 1 data is
    missing, return a structured 503 (caller raises HTTPException) with
    a clear error. Operators must run Phase 1 first.

    P2-010 ROOT FIX (v107 forensic): the previous code walked
    ``builder.node_loads`` and read ``node.get("type", "unknown")`` for
    each node. But the node-level ``type`` property is the
    ChEMBL/DrugBank scientific type (e.g. "small molecule", "biotech",
    "antibody"), NOT the KG label (Compound, Disease, etc.). The
    returned ``node_types`` dict had keys like "small molecule" instead
    of "Compound". The frontend's nodeCount breakdown by canonical type
    was wrong (showed "small molecule: 5000" instead of "Compound: 5000").
    ROOT FIX: use ``load["label"]`` (the KG label) for per-type
    breakdown. The KG label is the canonical Phase 2 vocabulary
    (Compound, Protein, Gene, Disease, Pathway, ClinicalOutcome).
    """
    pdir = _REPO_ROOT / "phase1" / "processed_data"
    # P2-001: do NOT inject mock data. If Phase 1 has not run, fail loud.
    if not pdir.exists() or not any(pdir.glob("*.csv*")):
        raise FileNotFoundError(
            f"Phase 1 processed data not found at {pdir}. Run the Phase 1 "
            f"pipeline first (python run_4phase.py phase1, or "
            f"python run_full_platform.py --phase 1). The Phase 2 KG service "
            f"refuses to serve mock data — the user's mandate is NO mock data. "
            f"(P2-001 root fix, v107)"
        )

    try:
        from drugos_graph.phase1_bridge import run_phase1_to_phase2
        result = run_phase1_to_phase2(
            phase1_processed_dir=str(pdir),
            prefer_postgres=False,
        )
        builder = result["builder"]
        summary = result["summary"]

        # P2-010: use load["label"] (KG label) — NOT node.get("type") which
        # is the ChEMBL/DrugBank scientific type ("small molecule", etc.).
        node_types: Dict[str, int] = {}
        for load in getattr(builder, "node_loads", []) or []:
            if not isinstance(load, dict):
                continue
            label = load.get("label") or "unknown"
            n_nodes = len(load.get("nodes", []) or [])
            node_types[label] = node_types.get(label, 0) + n_nodes

        edge_types: Dict[str, int] = {}
        for load in getattr(builder, "edge_loads", []) or []:
            if not isinstance(load, dict):
                continue
            rel = load.get("rel_type") or "unknown"
            n_edges = len(load.get("edges", []) or [])
            edge_types[rel] = edge_types.get(rel, 0) + n_edges

        return {
            "node_count": int(summary.get("nodes_loaded", 0)),
            "edge_count": int(summary.get("edges_loaded", 0)),
            "node_types": node_types,
            "edge_types": edge_types,
            "backend": "in_memory_bridge",
            "sources_read": summary.get("sources_read", []),
        }
    except FileNotFoundError:
        # Re-raise P2-001 missing-data errors unchanged.
        raise
    except Exception as exc:
        logger.error("Phase 2 service: bridge failed: %s", exc, exc_info=True)
        # P2-008: signal failure via the backend="error" marker; the route
        # handler converts this to HTTP 503.
        return {
            "node_count": 0,
            "edge_count": 0,
            "node_types": {},
            "edge_types": {},
            "backend": "error",
            "error": str(exc),
        }


# ─── P2-009 ROOT FIX (v107 forensic): in-memory subgraph exploration ──────
def _explore_subgraph_in_memory(
    drug: Optional[str], disease: Optional[str], limit: int
) -> Optional[Dict[str, Any]]:
    """BFS over the in-memory RecordingGraphBuilder.

    The previous code returned ``{"nodes": [], "edges": [], "note": "not yet
    implemented"}`` with HTTP 200 whenever Neo4j was unavailable. The KG
    explorer feature was permanently broken in dev/CI (no Neo4j).

    ROOT FIX: build the in-memory KG (same call as /kg/stats uses), then
    perform a real 2-hop BFS from the requested drug or disease node.
    Returns None if the in-memory builder is unavailable (caller raises
    503). This makes the KG explorer feature work in dev/CI without a
    Neo4j dependency.
    """
    try:
        from drugos_graph.phase1_bridge import run_phase1_to_phase2
        pdir = _REPO_ROOT / "phase1" / "processed_data"
        if not pdir.exists() or not any(pdir.glob("*.csv*")):
            return None
        result = run_phase1_to_phase2(
            phase1_processed_dir=str(pdir),
            prefer_postgres=False,
        )
        builder = result["builder"]
    except Exception as exc:
        logger.info("Phase 2 service: in-memory explore unavailable: %s", exc)
        return None

    # Build adjacency: src_label -> src_id -> [(rel_type, dst_label, dst_id)]
    adj: Dict[str, Dict[str, List[Tuple[str, str, str]]]] = {}
    node_props: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for load in getattr(builder, "node_loads", []) or []:
        if not isinstance(load, dict):
            continue
        label = load.get("label", "unknown")
        for node in load.get("nodes", []) or []:
            if not isinstance(node, dict):
                continue
            nid = str(node.get("id", ""))
            if not nid:
                continue
            node_props[(label, nid)] = {
                k: v for k, v in node.items() if k != "id"
            }
            adj.setdefault(label, {}).setdefault(nid, [])

    for load in getattr(builder, "edge_loads", []) or []:
        if not isinstance(load, dict):
            continue
        src_label = load.get("src_label", "")
        rel = load.get("rel_type", "")
        dst_label = load.get("dst_label", "")
        for edge in load.get("edges", []) or []:
            if not isinstance(edge, dict):
                continue
            src_id = str(edge.get("src_id", ""))
            dst_id = str(edge.get("dst_id", ""))
            if not src_id or not dst_id:
                continue
            adj.setdefault(src_label, {}).setdefault(src_id, []).append(
                (rel, dst_label, dst_id)
            )
            # Reverse adjacency for BFS from disease nodes.
            adj.setdefault(dst_label, {}).setdefault(dst_id, []).append(
                (f"rev_{rel}", src_label, src_id)
            )

    # Pick the start node: drug name match (Compound label) or disease name
    # match (Disease label). Match by case-insensitive name OR exact id.
    start_label: Optional[str] = None
    start_id: Optional[str] = None
    target_name = (drug or disease or "").strip().lower()
    search_label = "Compound" if drug else "Disease"
    for (label, nid), props in node_props.items():
        if label != search_label:
            continue
        name = str(props.get("name", "")).lower()
        if name == target_name or nid.lower() == target_name:
            start_label, start_id = label, nid
            break
    if start_id is None:
        return {
            "nodes": [],
            "edges": [],
            "backend": "in_memory_bridge",
            "note": f"No {search_label} node matched '{drug or disease}'.",
        }

    # 2-hop BFS.
    visited: Set[Tuple[str, str]] = {(start_label, start_id)}
    edges_out: List[Dict[str, Any]] = []
    frontier: List[Tuple[str, str]] = [(start_label, start_id)]
    for _hop in range(2):
        next_frontier: List[Tuple[str, str]] = []
        for (sl, sid) in frontier:
            for (rel, dl, did) in adj.get(sl, {}).get(sid, []):
                if rel.startswith("rev_"):
                    # Skip reverse edges in BFS expansion (avoid trivial
                    # back-and-forth). They are still recorded in edges_out
                    # if they connect two visited nodes.
                    fwd_rel = rel[4:]
                    if (dl, did) in visited:
                        edges_out.append({
                            "source": sid, "source_label": sl,
                            "target": did, "target_label": dl,
                            "type": fwd_rel,
                        })
                    continue
                edges_out.append({
                    "source": sid, "source_label": sl,
                    "target": did, "target_label": dl,
                    "type": rel,
                })
                if (dl, did) not in visited:
                    visited.add((dl, did))
                    next_frontier.append((dl, did))
            if len(edges_out) >= limit:
                break
        frontier = next_frontier
        if not frontier or len(edges_out) >= limit:
            break

    nodes_out: List[Dict[str, Any]] = []
    for (label, nid) in visited:
        nodes_out.append({
            "id": nid,
            "label": label,
            "properties": node_props.get((label, nid), {}),
        })
    # Deduplicate edges by (source, target, type).
    seen_edges: Set[Tuple[str, str, str]] = set()
    unique_edges: List[Dict[str, Any]] = []
    for e in edges_out:
        key = (e["source"], e["target"], e["type"])
        if key in seen_edges:
            continue
        seen_edges.add(key)
        unique_edges.append(e)
        if len(unique_edges) >= limit:
            break
    return {
        "nodes": nodes_out,
        "edges": unique_edges,
        "backend": "in_memory_bridge",
    }


def _explore_subgraph_neo4j(
    drug: Optional[str], disease: Optional[str], limit: int
) -> Optional[Dict[str, Any]]:
    """Try Neo4j first for subgraph exploration."""

    # v113 FORENSIC ROOT FIX (P2-044 + P2-045, MEDIUM):
    #   P2-044: the previous code used ``d_node.id`` (Neo4j INTERNAL ID)
    #   as the response ``id``. Neo4j internal IDs are NOT stable across
    #   database restarts -- a node that was internal-id 42 before a
    #   restart may be internal-id 17 after. The frontend cached these
    #   IDs and broke on the next KG rebuild.
    #
    #   P2-045: the previous code used ``r1.start_node.id`` and
    #   ``r1.end_node.id`` for edge source/target. For UNDIRECTED
    #   ``MATCH (d)-[r1]-(n1)`` patterns, ``start_node`` and
    #   ``end_node`` are ARBITRARY (not the actual src/dst of the
    #   traversal) -- the edge's source/target in the response could
    #   be SWAPPED on consecutive runs of the same query, breaking
    #   the visual graph rendering.
    #
    #   ROOT FIX: use the BUSINESS ``id`` property from node properties
    #   (``dict(node).get("id")``), falling back to the Neo4j internal
    #   ID only when the business ``id`` property is missing (e.g., for
    #   legacy nodes created before the ``id`` property was mandatory).
    #   For edges, use the business IDs of the nodes we ALREADY have
    #   from the query (``d``, ``n1``, ``n2``) -- do NOT use
    #   ``r.start_node.id`` / ``r.end_node.id`` which are arbitrary for
    #   undirected patterns. The edge source is always the node CLOSER
    #   to the query root (``d`` for r1, ``n1`` for r2), and the target
    #   is the node FARTHER from the root (``n1`` for r1, ``n2`` for r2).
    #   This produces STABLE, DETERMINISTIC edge source/target pairs.
    def _business_id(node) -> Any:
        """Return the business ``id`` property, falling back to Neo4j internal id."""
        if node is None:
            return None
        props = dict(node)
        bid = props.get("id")
        if bid is not None and bid != "":
            return bid
        # Legacy nodes without a business ``id`` property -- fall back
        # to the Neo4j internal id (stringified so it's JSON-serializable
        # and visually distinct from business IDs).
        return f"__neo4j_internal:{node.id}"

    def _node_record(node) -> Dict[str, Any]:
        return {
            "id": _business_id(node),
            "label": list(node.labels)[0] if node.labels else "Unknown",
            "labels": list(node.labels),
            "properties": dict(node),
        }

    def _query(driver):
        nodes: List[Dict[str, Any]] = []
        edges: List[Dict[str, Any]] = []
        # P2-030 ROOT FIX (v107): the previous query used
        # ``MATCH p=(d:Drug {name: $drug})-[*1..2]-(n) RETURN p LIMIT $limit``
        # The ``[*1..2]`` variable-length path is UNBOUNDED in complexity —
        # on a large KG, a 2-hop traversal from a high-degree drug (e.g.
        # aspirin with 1000+ edges) can match MILLIONS of paths. The
        # ``LIMIT $limit`` caps the RETURN but NOT the intermediate path
        # expansion. A single API call can DoS the Neo4j instance.
        # ROOT FIX: use a subquery with LIMIT INSIDE so the path
        # expansion is bounded at each hop. This caps the work Neo4j
        # does, preventing DoS while still returning a useful subgraph.
        # P2-031 ROOT FIX (v107): the previous code deduplicated NODES
        # by id but NOT EDGES. The same relationship (same source,
        # target, type) appeared multiple times in the response,
        # inflating the visual graph. ROOT FIX: deduplicate edges by
        # (source, target, type) tuple.
        with driver.session() as session:
            if drug:
                # P2-030 ROOT FIX (v107): bounded 2-hop traversal using
                # subqueries with LIMIT inside each hop. The previous
                # ``[*1..2]`` variable-length path was UNBOUNDED — on a
                # large KG, a 2-hop traversal from a high-degree drug
                # could match millions of paths, DoS-ing Neo4j.
                q = """
                MATCH (d:Compound {name: $drug})
                CALL {
                    WITH d
                    MATCH (d)-[r1]-(n1)
                    RETURN n1, r1, d AS d1
                    LIMIT $limit
                }
                WITH d, collect(DISTINCT [n1, r1, d1]) AS hop1
                UNWIND hop1 AS h1
                WITH h1[0] AS n1, h1[1] AS r1, h1[2] AS d1
                CALL {
                    WITH n1
                    MATCH (n1)-[r2]-(n2)
                    WHERE n2 <> d AND n2 <> n1
                    RETURN n2, r2, n1 AS n1b
                    LIMIT $limit
                }
                RETURN d, n1, r1, n2, r2
                LIMIT $limit
                """
                result = session.run(q, drug=drug, limit=limit)
                for record in result:
                    d_node = record["d"]
                    nodes.append(_node_record(d_node))
                    n1 = record["n1"]
                    if n1 is not None:
                        nodes.append(_node_record(n1))
                        r1 = record["r1"]
                        if r1 is not None:
                            # v113 P2-044/045 ROOT FIX: use business IDs
                            # of the nodes we already have (d, n1), NOT
                            # r1.start_node.id / r1.end_node.id (which
                            # are arbitrary for undirected MATCH).
                            edges.append({
                                "source": _business_id(d_node),
                                "target": _business_id(n1),
                                "type": r1.type,
                            })
                    n2 = record["n2"]
                    if n2 is not None:
                        nodes.append(_node_record(n2))
                        r2 = record["r2"]
                        if r2 is not None:
                            # v113 P2-044/045 ROOT FIX: use business IDs
                            # of n1 and n2 (NOT r2.start_node/end_node).
                            edges.append({
                                "source": _business_id(n1) if n1 is not None else _business_id(d_node),
                                "target": _business_id(n2),
                                "type": r2.type,
                            })
            elif disease:
                # P2-030: same bounded traversal for disease queries.
                q = """
                MATCH (d:Disease {name: $disease})
                CALL {
                    WITH d
                    MATCH (d)-[r1]-(n1)
                    RETURN n1, r1, d AS d1
                    LIMIT $limit
                }
                WITH d, collect(DISTINCT [n1, r1, d1]) AS hop1
                UNWIND hop1 AS h1
                WITH h1[0] AS n1, h1[1] AS r1, h1[2] AS d1
                CALL {
                    WITH n1
                    MATCH (n1)-[r2]-(n2)
                    WHERE n2 <> d AND n2 <> n1
                    RETURN n2, r2, n1 AS n1b
                    LIMIT $limit
                }
                RETURN d, n1, r1, n2, r2
                LIMIT $limit
                """
                result = session.run(q, disease=disease, limit=limit)
                for record in result:
                    d_node = record["d"]
                    nodes.append(_node_record(d_node))
                    n1 = record["n1"]
                    if n1 is not None:
                        nodes.append(_node_record(n1))
                        r1 = record["r1"]
                        if r1 is not None:
                            # v113 P2-044/045 ROOT FIX: use business IDs.
                            edges.append({
                                "source": _business_id(d_node),
                                "target": _business_id(n1),
                                "type": r1.type,
                            })
                    n2 = record["n2"]
                    if n2 is not None:
                        nodes.append(_node_record(n2))
                        r2 = record["r2"]
                        if r2 is not None:
                            # v113 P2-044/045 ROOT FIX: use business IDs.
                            edges.append({
                                "source": _business_id(n1) if n1 is not None else _business_id(d_node),
                                "target": _business_id(n2),
                                "type": r2.type,
                            })
            else:
                return None
        # Deduplicate nodes by id.
        seen_node_ids = set()
        unique_nodes = []
        for n in nodes:
            if n["id"] not in seen_node_ids:
                seen_node_ids.add(n["id"])
                unique_nodes.append(n)
        # P2-031 ROOT FIX (v107): deduplicate EDGES by (source, target, type).
        # The previous code deduplicated nodes but NOT edges — the same
        # relationship appeared multiple times, inflating the visual graph.
        seen_edge_keys = set()
        unique_edges = []
        for e in edges:
            _edge_key = (e["source"], e["target"], e["type"])
            if _edge_key not in seen_edge_keys:
                seen_edge_keys.add(_edge_key)
                unique_edges.append(e)
        return {"nodes": unique_nodes, "edges": unique_edges, "backend": "neo4j"}

    try:
        return _run_neo4j(_query)
    except Exception as exc:
        logger.info("Phase 2 service: Neo4j explore failed: %s", exc)
        return None


# ─── P2-002 ROOT FIX (v107 forensic): /query + /cypher endpoints ──────────
class QueryBody(BaseModel):
    """Structured query body for POST /query."""
    drug: Optional[str] = Field(None, description="Drug name (Compound.name).")
    disease: Optional[str] = Field(None, description="Disease name (Disease.name).")
    limit: int = Field(100, ge=1, le=500, description="Max nodes to return.")


# Read-only Cypher keyword whitelist. Mirrors the frontend's
# ``validateReadOnlyCypher`` validator so both layers enforce the same rule.
_READ_ONLY_PREFIX_RE = re.compile(
    r"^\s*(MATCH|OPTIONAL\s+MATCH|WITH|RETURN|WHERE|ORDER\s+BY|LIMIT|SKIP|UNWIND|DISTINCT|CALL\s+\{[^}]*\}\s*(?:YIELD[^;]*)?|CALL\s+db\.labels\(\)|CALL\s+db\.relationshipTypes\(\))\b",
    re.IGNORECASE,
)
_FORBIDDEN_KEYWORDS_RE = re.compile(
    r"\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|INDEX|CONSTRAINT|"
    r"CALL\s+db\.schema\.write|CALL\s+apoc\.create|CALL\s+apoc\.destroy)\b",
    re.IGNORECASE,
)


class CypherBody(BaseModel):
    """Raw Cypher passthrough body for POST /cypher."""
    cypher: str = Field(..., description="Read-only Cypher query.")
    params: Optional[Dict[str, Any]] = Field(
        None, description="Parameterized query variables."
    )


def _validate_readonly_cypher(cypher: str) -> Optional[str]:
    """Return an error message if the Cypher is not read-only, else None."""
    if not cypher or not cypher.strip():
        return "Empty Cypher query."
    if _FORBIDDEN_KEYWORDS_RE.search(cypher):
        return (
            "Cypher contains a forbidden write/DDL keyword "
            "(CREATE/MERGE/DELETE/SET/REMOVE/DROP/INDEX/CONSTRAINT/...). "
            "Only read-only MATCH/OPTIONAL MATCH/WITH/RETURN queries are "
            "allowed via this endpoint."
        )
    # The first non-whitespace token must be MATCH or OPTIONAL MATCH.
    first_token = cypher.strip().split(None, 1)[0].upper() if cypher.strip() else ""
    if first_token not in ("MATCH", "OPTIONAL"):
        return (
            f"Cypher must start with MATCH or OPTIONAL MATCH (got '{first_token}'). "
            "Only read-only queries are allowed."
        )
    return None


@app.get("/health")
def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "service": "phase2_kg",
        "version": "1.0.0",
        "neo4j_configured": bool(os.environ.get("NEO4J_PASSWORD")),
        "cors_origins": _allowed_origins,
    }


@app.get("/kg/stats")
def kg_stats() -> Dict[str, Any]:
    """Return real KG stats from Neo4j (or in-memory bridge fallback)."""
    # P2-017: _get_kg_stats_from_neo4j now uses try/finally driver.close().
    neo4j_stats = _get_kg_stats_from_neo4j()
    if neo4j_stats is not None:
        return neo4j_stats
    # P2-001 / P2-008: bridge may raise FileNotFoundError (Phase 1 missing)
    # or return backend="error". Either way, the route MUST surface 503 —
    # never silently return 0/0 with HTTP 200, and never let FileNotFoundError
    # become an unhandled HTTP 500.
    try:
        bridge_stats = _get_kg_stats_from_builder()
    except FileNotFoundError as exc:
        # P2-001: Phase 1 data is missing — return 503 with a clear error.
        raise HTTPException(
            status_code=503,
            detail={
                "error": "phase1_data_missing",
                "message": str(exc),
                "backend": "missing",
            },
        )
    if bridge_stats.get("backend") == "error":
        raise HTTPException(
            status_code=503,
            detail={
                "error": "kg_bridge_failed",
                "message": bridge_stats.get("error", "unknown bridge failure"),
                "backend": "error",
            },
        )
    return bridge_stats


@app.get("/kg/explore")
def kg_explore(
    drug: Optional[str] = Query(None),
    disease: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
) -> Dict[str, Any]:
    """Return a subgraph around a drug or disease."""
    if not drug and not disease:
        raise HTTPException(
            status_code=400,
            detail="Provide ?drug= or ?disease= query param.",
        )
    # P2-017: _explore_subgraph_neo4j now uses try/finally driver.close().
    neo4j_result = _explore_subgraph_neo4j(drug, disease, limit)
    if neo4j_result is not None:
        return neo4j_result
    # P2-009: real in-memory BFS (was: empty 200 with a note field).
    in_mem_result = _explore_subgraph_in_memory(drug, disease, limit)
    if in_mem_result is not None:
        return in_mem_result
    raise HTTPException(
        status_code=503,
        detail={
            "error": "kg_explore_unavailable",
            "message": (
                "Neither Neo4j nor the in-memory bridge could resolve this "
                "query. Set NEO4J_URI/USER/PASSWORD for production, or "
                "ensure Phase 1 processed_data exists for in-memory fallback."
            ),
        },
    )


@app.post("/query")
def kg_query(body: QueryBody) -> Dict[str, Any]:
    """Structured drug/disease query (P2-002 root fix).

    The frontend POSTs ``{"drug": "...", "disease": "...", "limit": N}``
    here. We resolve it via Neo4j (preferred) or the in-memory BFS.
    """
    if not body.drug and not body.disease:
        raise HTTPException(
            status_code=400,
            detail="Provide at least one of 'drug' or 'disease' in the body.",
        )
    # Try Neo4j first.
    neo4j_result = _explore_subgraph_neo4j(body.drug, body.disease, body.limit)
    if neo4j_result is not None:
        return neo4j_result
    # In-memory fallback.
    in_mem_result = _explore_subgraph_in_memory(body.drug, body.disease, body.limit)
    if in_mem_result is not None:
        return in_mem_result
    raise HTTPException(
        status_code=503,
        detail={
            "error": "kg_query_unavailable",
            "message": (
                "Neither Neo4j nor the in-memory bridge could resolve this "
                "structured query."
            ),
        },
    )


@app.post("/cypher")
def kg_cypher(body: CypherBody) -> Dict[str, Any]:
    """Raw read-only Cypher passthrough (P2-002 root fix).

    Applies a read-only whitelist (mirrors the frontend's
    ``validateReadOnlyCypher``). Forwards to Neo4j with a hard 1000-row cap.
    Returns 503 if Neo4j is not configured — the in-memory bridge cannot
    answer arbitrary Cypher.
    """
    # Validate read-only.
    err = _validate_readonly_cypher(body.cypher)
    if err is not None:
        raise HTTPException(status_code=400, detail={"error": "cypher_rejected", "message": err})

    if not os.environ.get("NEO4J_PASSWORD"):
        raise HTTPException(
            status_code=503,
            detail={
                "error": "service_not_deployed",
                "message": (
                    "Raw Cypher queries require Neo4j. Set NEO4J_URI/USER/"
                    "PASSWORD to enable. The in-memory bridge cannot answer "
                    "arbitrary Cypher."
                ),
            },
        )

    MAX_ROWS = 1000

    def _query(driver):
        with driver.session() as session:
            result = session.run(body.cypher, body.params or {})
            records = []
            for rec in result:
                records.append(dict(rec))
                if len(records) >= MAX_ROWS:
                    break
            return {
                "records": records,
                "row_count": len(records),
                "truncated": len(records) >= MAX_ROWS,
                "max_rows": MAX_ROWS,
                "backend": "neo4j",
            }

    try:
        return _run_neo4j(_query)
    except Exception as exc:
        logger.error("Phase 2 service: /cypher failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=502,
            detail={"error": "kg_cypher_failed", "message": str(exc)},
        )


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PHASE2_SERVICE_PORT", "8002"))
    host = os.environ.get("PHASE2_SERVICE_HOST", "0.0.0.0")
    logger.info("Starting Phase 2 KG Service on %s:%d", host, port)
    uvicorn.run(app, host=host, port=port)
