#!/usr/bin/env python3
"""Phase 2 Knowledge Graph Service (Step 1 integration plan, v105).

Wraps Phase 2's KG builder / query logic as an HTTP service so the
Next.js frontend can proxy to it via KG_SERVICE_URL. The frontend's
``src/lib/services/knowledge-graph-stats.ts`` and
``src/app/api/knowledge-graph/route.ts`` already proxy to
``KG_SERVICE_URL`` -- this service is what they expect to find there.

Endpoints:
    GET  /health                -> {status: "ok", service: "phase2", ...}
    GET  /kg/stats              -> {node_count, edge_count, node_types: {...}, ...}
    GET  /kg/explore?drug=...&disease=...  -> subgraph around the query

Run:
    cd phase2 && python service.py
    # or: uvicorn phase2.service:app --host 0.0.0.0 --port 8002

Environment:
    NEO4J_URI: bolt://localhost:7687 (or unset for in-memory CSV mode)
    NEO4J_USER: neo4j
    NEO4J_PASSWORD: <password>
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# Make phase2 importable.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
_REPO_ROOT = _HERE.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

logger = logging.getLogger("phase2.service")
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")

app = FastAPI(
    title="Autonomous Drug Repurposing — Phase 2 Knowledge Graph Service",
    description="HTTP wrapper around Phase 2 KG builder / queries.",
    version="1.0.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


def _get_kg_stats_from_neo4j() -> Optional[Dict[str, Any]]:
    """Try to read KG stats from Neo4j. Returns None if Neo4j is not available."""
    try:
        from neo4j import GraphDatabase
        uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
        user = os.environ.get("NEO4J_USER", "neo4j")
        password = os.environ.get("NEO4J_PASSWORD", "")
        if not password:
            return None
        driver = GraphDatabase.driver(uri, auth=(user, password))
        with driver.session() as session:
            node_count = session.run("MATCH (n) RETURN count(n) AS c").single()["c"]
            edge_count = session.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]
            # Per-label node counts.
            result = session.run("MATCH (n) RETURN labels(n)[0] AS label, count(n) AS c")
            node_types = {record["label"]: record["c"] for record in result}
            # Per-type edge counts.
            result = session.run("MATCH ()-[r]->() RETURN type(r) AS t, count(r) AS c")
            edge_types = {record["t"]: record["c"] for record in result}
        driver.close()
        return {
            "node_count": int(node_count),
            "edge_count": int(edge_count),
            "node_types": node_types,
            "edge_types": edge_types,
            "backend": "neo4j",
        }
    except Exception as exc:
        logger.info("Phase 2 service: Neo4j not available (%s), falling back to in-memory builder.", exc)
        return None


def _get_kg_stats_from_builder() -> Dict[str, Any]:
    """Build the KG in-memory from Phase 1 CSVs via the bridge, then count.

    This is the Tier-2 fallback used in dev/CI when Neo4j is not running.
    The bridge (drugos_graph.phase1_bridge.run_phase1_to_phase2) reads
    the same Phase 1 CSVs that run_4phase.py reads, so the stats here
    EXACTLY match what the bridge would load into Neo4j in production.
    """
    pdir = _REPO_ROOT / "phase1" / "processed_data"
    if not pdir.exists() or not any(pdir.glob("*.csv*")):
        try:
            sys.path.insert(0, str(_REPO_ROOT / "phase1"))
            from pipelines._embedded_samples import write_all_samples
            write_all_samples(str(pdir))
        except Exception as exc:
            logger.warning("Phase 2 service: could not write embedded samples: %s", exc)

    try:
        from drugos_graph.phase1_bridge import run_phase1_to_phase2
        result = run_phase1_to_phase2(
            phase1_processed_dir=str(pdir),
            prefer_postgres=False,
        )
        builder = result["builder"]
        summary = result["summary"]
        # The builder records per-type node counts in node_loads.
        node_types: Dict[str, int] = {}
        for load in getattr(builder, "node_loads", []) or []:
            for node in (load.get("nodes", []) if isinstance(load, dict) else []):
                ntype = node.get("type", "unknown") if isinstance(node, dict) else getattr(node, "type", "unknown")
                node_types[ntype] = node_types.get(ntype, 0) + 1
        edge_types: Dict[str, int] = {}
        for load in getattr(builder, "edge_loads", []) or []:
            for edge in (load.get("edges", []) if isinstance(load, dict) else []):
                etype = edge.get("type", "unknown") if isinstance(edge, dict) else getattr(edge, "type", "unknown")
                edge_types[etype] = edge_types.get(etype, 0) + 1
        return {
            "node_count": int(summary.get("nodes_loaded", 0)),
            "edge_count": int(summary.get("edges_loaded", 0)),
            "node_types": node_types,
            "edge_types": edge_types,
            "backend": "in_memory_bridge",
            "sources_read": summary.get("sources_read", []),
        }
    except Exception as exc:
        logger.error("Phase 2 service: bridge failed: %s", exc, exc_info=True)
        return {
            "node_count": 0,
            "edge_count": 0,
            "node_types": {},
            "edge_types": {},
            "backend": "error",
            "error": str(exc),
        }


def _explore_subgraph_neo4j(drug: Optional[str], disease: Optional[str], limit: int) -> Optional[Dict[str, Any]]:
    """Try Neo4j first for subgraph exploration."""
    try:
        from neo4j import GraphDatabase
        uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
        user = os.environ.get("NEO4J_USER", "neo4j")
        password = os.environ.get("NEO4J_PASSWORD", "")
        if not password:
            return None
        driver = GraphDatabase.driver(uri, auth=(user, password))
        nodes: List[Dict[str, Any]] = []
        edges: List[Dict[str, Any]] = []
        with driver.session() as session:
            if drug:
                # Find the drug + its 2-hop neighbors.
                q = """
                MATCH p=(d:Drug {name: $drug})-[*1..2]-(n)
                RETURN p LIMIT $limit
                """
                result = session.run(q, drug=drug, limit=limit)
                for record in result:
                    path = record["p"]
                    for node in path.nodes:
                        nodes.append({"id": node.id, "labels": list(node.labels), "properties": dict(node)})
                    for rel in path.relationships:
                        edges.append({
                            "source": rel.start_node.id,
                            "target": rel.end_node.id,
                            "type": rel.type,
                        })
            elif disease:
                q = """
                MATCH p=(d:Disease {name: $disease})-[*1..2]-(n)
                RETURN p LIMIT $limit
                """
                result = session.run(q, disease=disease, limit=limit)
                for record in result:
                    path = record["p"]
                    for node in path.nodes:
                        nodes.append({"id": node.id, "labels": list(node.labels), "properties": dict(node)})
                    for rel in path.relationships:
                        edges.append({
                            "source": rel.start_node.id,
                            "target": rel.end_node.id,
                            "type": rel.type,
                        })
        driver.close()
        # Deduplicate nodes/edges by id.
        seen_node_ids = set()
        unique_nodes = []
        for n in nodes:
            if n["id"] not in seen_node_ids:
                seen_node_ids.add(n["id"])
                unique_nodes.append(n)
        return {"nodes": unique_nodes, "edges": edges, "backend": "neo4j"}
    except Exception as exc:
        logger.info("Phase 2 service: Neo4j explore failed: %s", exc)
        return None


@app.get("/health")
def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "service": "phase2_kg",
        "version": "1.0.0",
        "neo4j_configured": bool(os.environ.get("NEO4J_PASSWORD")),
    }


@app.get("/kg/stats")
def kg_stats() -> Dict[str, Any]:
    """Return real KG stats from Neo4j (or in-memory bridge fallback)."""
    neo4j_stats = _get_kg_stats_from_neo4j()
    if neo4j_stats is not None:
        return neo4j_stats
    return _get_kg_stats_from_builder()


@app.get("/kg/explore")
def kg_explore(
    drug: Optional[str] = Query(None),
    disease: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
) -> Dict[str, Any]:
    """Return a subgraph around a drug or disease."""
    if not drug and not disease:
        raise HTTPException(status_code=400, detail="Provide ?drug= or ?disease= query param.")
    neo4j_result = _explore_subgraph_neo4j(drug, disease, limit)
    if neo4j_result is not None:
        return neo4j_result
    # Fallback: build in-memory and search.
    stats = _get_kg_stats_from_builder()
    return {
        "nodes": [],
        "edges": [],
        "backend": stats.get("backend", "unknown"),
        "note": "Neo4j not available; in-memory subgraph exploration not yet implemented for this path. Set NEO4J_URI/USER/PASSWORD to enable.",
        "kg_stats": stats,
    }


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PHASE2_SERVICE_PORT", "8002"))
    host = os.environ.get("PHASE2_SERVICE_HOST", "0.0.0.0")
    logger.info("Starting Phase 2 KG Service on %s:%d", host, port)
    uvicorn.run(app, host=host, port=port)
