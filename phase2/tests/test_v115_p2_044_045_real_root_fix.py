#!/usr/bin/env python3
"""v115 P2-044/045 REAL ROOT FIX verification tests.

Red-team audit (per the user's directive: "comments are fakes"):
    The previous v113 "ROOT FIX" for P2-044 + P2-045 in
    ``phase2/service.py::_explore_subgraph_neo4j`` was ASPIRATIONAL.
    The long comment block at the top of the function explained the fix
    in detail, and the ``elif drug:`` + ``elif disease:`` branches were
    correctly fixed to use ``_business_id()`` / ``_node_record()``. But
    the ``if drug and disease:`` branch (the most scientifically useful
    query -- it finds shortestPath BETWEEN a drug and a disease) was
    MISSED. It STILL used:
        ``"id": d_node.id``            (Neo4j INTERNAL ID -- unstable)
        ``"id": dis_node.id``          (Neo4j INTERNAL ID -- unstable)
        ``"source": r.start_node.id``  (arbitrary for undirected MATCH)
        ``"target": r.end_node.id``    (arbitrary for undirected MATCH)
        ``"id": sn.id``                (Neo4j INTERNAL ID -- unstable)
        ``"id": en.id``                (Neo4j INTERNAL ID -- unstable)
    Exactly the "comments are fakes" pattern the audit warned about.

REAL ROOT FIX (v115):
    All three branches (``if drug and disease:``, ``elif drug:``,
    ``elif disease:``) now use ``_node_record()`` for every node and
    ``_business_id()`` for every edge source/target. The fix is
    VERIFIED by these tests:
        1. AST check: no ``.id`` attribute access inside any
           ``nodes.append({...})`` or ``edges.append({...})`` call.
        2. Behavioral check: with a mock Neo4j driver that returns
           nodes whose business ``id`` property DIFFERS from the Neo4j
           internal ``.id``, the response uses the BUSINESS ID (not
           the internal ID).
        3. Stability check: the same query run twice with the same
           inputs produces IDENTICAL node/edge IDs (the internal-ID
           bug caused IDs to change across KG rebuilds).
"""
from __future__ import annotations

import ast
import inspect
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest

# Make the phase2 package importable.
REPO_ROOT = Path(__file__).resolve().parents[2]
PHASE2_DIR = REPO_ROOT / "phase2"
for _p in (str(REPO_ROOT), str(PHASE2_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import phase2.service as svc  # type: ignore


# ============================================================================
# Test 1: AST check — no bare .id access in nodes.append/edges.append dicts
# ============================================================================
class TestAstNoBareIdAccess:
    """Verify the source code does not use bare ``.id`` (Neo4j internal ID)
    in any ``nodes.append({...})`` or ``edges.append({...})`` call site
    inside ``_explore_subgraph_neo4j``."""

    @pytest.fixture(scope="class")
    def function_source(self) -> str:
        return inspect.getsource(svc._explore_subgraph_neo4j)

    @pytest.fixture(scope="class")
    def function_ast(self, function_source: str) -> ast.AST:
        return ast.parse(function_source)

    def test_no_bare_id_in_append_dicts(self, function_ast: ast.AST) -> None:
        """AST walk: for every ``nodes.append({...})`` or ``edges.append({...})``,
        the ``id``, ``source``, and ``target`` keys must NOT be a bare
        ``<expr>.id`` attribute access (Neo4j internal ID)."""
        violations: List[str] = []

        class AppendChecker(ast.NodeVisitor):
            def visit_Call(self, node: ast.Call) -> None:
                if (isinstance(node.func, ast.Attribute)
                        and node.func.attr == "append"
                        and isinstance(node.func.value, ast.Name)
                        and node.func.value.id in ("nodes", "edges")
                        and node.args
                        and isinstance(node.args[0], ast.Dict)):
                    d = node.args[0]
                    for key, value in zip(d.keys, d.values):
                        if isinstance(key, ast.Constant) and key.value in ("id", "source", "target"):
                            # BARE .id access is the broken pattern.
                            if isinstance(value, ast.Attribute) and value.attr == "id":
                                violations.append(
                                    f"line {node.lineno}: {node.func.value.id}.append "
                                    f"key={key.value!r} uses bare .id access "
                                    f"(Neo4j internal ID) -- should use _business_id() "
                                    f"or _node_record()"
                                )
                self.generic_visit(node)

        AppendChecker().visit(function_ast)
        assert not violations, (
            "P2-044/045 violations found in _explore_subgraph_neo4j:\n  "
            + "\n  ".join(violations)
        )

    def test_business_id_helper_defined(self, function_source: str) -> None:
        """The ``_business_id`` helper must be defined inside the function."""
        assert "def _business_id" in function_source, (
            "_business_id helper is missing from _explore_subgraph_neo4j -- "
            "without it, the fix is impossible."
        )

    def test_node_record_helper_defined(self, function_source: str) -> None:
        """The ``_node_record`` helper must be defined inside the function."""
        assert "def _node_record" in function_source, (
            "_node_record helper is missing from _explore_subgraph_neo4j -- "
            "without it, the fix is impossible."
        )

    def test_no_start_node_end_node_access(self, function_source: str) -> None:
        """No ``r.start_node.id`` / ``r.end_node.id`` / ``r1.start_node.id``
        / ``r1.end_node.id`` / ``r2.start_node.id`` / ``r2.end_node.id``
        patterns in executable code (comments are stripped)."""
        # Parse AST, strip docstrings, unparse.
        tree = ast.parse(function_source)

        class DocstringStripper(ast.NodeTransformer):
            def _strip(self, node):
                self.generic_visit(node)
                if (node.body and isinstance(node.body[0], ast.Expr)
                        and isinstance(node.body[0].value, ast.Constant)
                        and isinstance(node.body[0].value.value, str)):
                    node.body = node.body[1:] or [ast.Pass()]
                return node

            visit_FunctionDef = _strip
            visit_AsyncFunctionDef = _strip
            visit_ClassDef = _strip
            visit_Module = _strip

        tree = DocstringStripper().visit(tree)
        clean = ast.unparse(tree)
        forbidden = [
            "r.start_node.id", "r.end_node.id",
            "r1.start_node.id", "r1.end_node.id",
            "r2.start_node.id", "r2.end_node.id",
            "d_node.id", "dis_node.id", "sn.id", "en.id",
            "n1.id", "n2.id",
        ]
        found = [p for p in forbidden if p in clean]
        assert not found, (
            f"P2-044/045: forbidden Neo4j-internal-ID patterns still in "
            f"executable code: {found}"
        )


class _MockNeo4jNode:
    """A mock Neo4j Node that behaves like the real one for our purposes.

    The real neo4j.graph.Node supports:
        - ``dict(node)`` → properties dict (via Mapping protocol)
        - ``node.id`` → internal Neo4j ID (int)
        - ``node.labels`` → set of labels
        - ``node[k]`` → property value
        - ``node.keys()``, ``node.items()``, ``node.get(k, default)``
    We implement the same interface with a plain Python class (NOT MagicMock)
    because MagicMock intercepts dunder methods (``__iter__``, ``__getitem__``)
    in surprising ways.
    """

    def __init__(self, properties: Dict[str, Any], internal_id: int, labels: List[str]):
        self._properties = dict(properties)
        self.id = internal_id
        self.labels = list(labels)

    # Mapping protocol — enables dict(node)
    def __iter__(self):
        return iter(self._properties)

    def __getitem__(self, key):
        return self._properties[key]

    def __len__(self):
        return len(self._properties)

    def __contains__(self, key):
        return key in self._properties

    def keys(self):
        return self._properties.keys()

    def items(self):
        return self._properties.items()

    def values(self):
        return self._properties.values()

    def get(self, key, default=None):
        return self._properties.get(key, default)

    def __repr__(self) -> str:
        return f"_MockNeo4jNode(id={self.id!r}, labels={self.labels!r}, props={self._properties!r})"


class _MockNeo4jRelationship:
    """A mock Neo4j Relationship."""

    def __init__(self, rel_type: str, start_node: Any, end_node: Any):
        self.type = rel_type
        self.start_node = start_node
        self.end_node = end_node


# ============================================================================
# Test 2: Behavioral check — _business_id returns the BUSINESS id, not internal
# ============================================================================
class TestBusinessIdHelper:
    """Verify the ``_business_id`` helper returns the BUSINESS ``id`` property
    when present, and falls back to a clearly-marked internal ID only when
    the business ``id`` is missing."""

    @pytest.fixture(scope="class")
    def business_id_fn(self):
        """Extract the ``_business_id`` closure from _explore_subgraph_neo4j."""
        src = inspect.getsource(svc._explore_subgraph_neo4j)
        tree = ast.parse(src)
        business_id_fn_src = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_business_id":
                business_id_fn_src = ast.get_source_segment(src, node)
                break
        assert business_id_fn_src is not None, "_business_id not found in source"
        ns: Dict[str, Any] = {}
        exec(  # noqa: S102 -- controlled exec of extracted source
            business_id_fn_src, ns
        )
        return ns["_business_id"]

    def test_returns_business_id_when_present(self, business_id_fn) -> None:
        """When a node has a business ``id`` property, ``_business_id`` returns it."""
        node = _MockNeo4jNode(
            properties={"id": "DB00001", "name": "aspirin"},
            internal_id=42,
            labels=["Compound"],
        )
        result = business_id_fn(node)
        assert result == "DB00001", f"expected 'DB00001', got {result!r}"

    def test_falls_back_to_internal_id_when_business_id_missing(self, business_id_fn) -> None:
        """When a node has NO business ``id`` property, ``_business_id`` falls back
        to a clearly-marked internal ID (prefixed with ``__neo4j_internal:``)."""
        node = _MockNeo4jNode(
            properties={"name": "old legacy node"},
            internal_id=99,
            labels=["Compound"],
        )
        result = business_id_fn(node)
        assert result == "__neo4j_internal:99", (
            f"expected '__neo4j_internal:99', got {result!r} -- the fallback "
            f"must be clearly marked so it's visually distinct from business IDs."
        )

    def test_falls_back_to_internal_id_when_business_id_empty_string(self, business_id_fn) -> None:
        """When a node has business ``id=""`` (empty string), ``_business_id``
        treats it as missing and falls back to the internal ID."""
        node = _MockNeo4jNode(
            properties={"id": ""},
            internal_id=7,
            labels=["Compound"],
        )
        result = business_id_fn(node)
        assert result == "__neo4j_internal:7", (
            f"expected '__neo4j_internal:7', got {result!r} -- empty-string "
            f"business id must be treated as missing."
        )

    def test_returns_none_for_none_node(self, business_id_fn) -> None:
        """When the node is None, ``_business_id`` returns None (no crash)."""
        result = business_id_fn(None)
        assert result is None, f"expected None, got {result!r}"


# ============================================================================
# Test 3: Stability check — same query produces same IDs across "restarts"
# ============================================================================
class TestResponseIdStability:
    """Verify that the response from ``_explore_subgraph_neo4j`` uses
    BUSINESS IDs that are stable across Neo4j restarts (where internal IDs
    may change). This is the SCIENTIFIC reason for P2-044: a frontend that
    caches node IDs by Neo4j internal ID breaks when the DB restarts."""

    def test_response_uses_business_id_not_internal_id(self) -> None:
        """Mock a Neo4j driver whose nodes have:
            - business ``id`` property = "DB00001" / "MESH:D001" / etc.
            - Neo4j internal ``.id`` = 17 / 42 / etc. (different numbers)
        The response MUST use the business ID, not the internal ID."""
        drug_node = _MockNeo4jNode({"id": "DB00001", "name": "aspirin"}, 17, ["Compound"])
        disease_node = _MockNeo4jNode({"id": "MESH:D001", "name": "migraine"}, 42, ["Disease"])
        protein_node = _MockNeo4jNode({"id": "P12345", "name": "PTGS1"}, 99, ["Protein"])

        rel1 = _MockNeo4jRelationship("targets", drug_node, protein_node)
        rel2 = _MockNeo4jRelationship("associated_with", protein_node, disease_node)

        records = [
            {"d": drug_node, "dis": disease_node, "r": rel1,
             "sn": drug_node, "en": protein_node},
            {"d": drug_node, "dis": disease_node, "r": rel2,
             "sn": protein_node, "en": disease_node},
        ]

        mock_session = MagicMock()
        mock_session.run.return_value = records
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_driver = MagicMock()
        mock_driver.session.return_value = mock_session

        with patch.object(svc, "_neo4j_driver", return_value=mock_driver):
            result = svc._explore_subgraph_neo4j("aspirin", "migraine", limit=10)

        assert result is not None, "result should not be None with mock driver"
        assert result["backend"] == "neo4j"

        node_ids = {n["id"] for n in result["nodes"]}
        assert "DB00001" in node_ids, (
            f"business ID 'DB00001' missing from response node IDs: {node_ids}. "
            f"P2-044 regression: the response is using Neo4j internal IDs."
        )
        assert "MESH:D001" in node_ids, (
            f"business ID 'MESH:D001' missing from response node IDs: {node_ids}. "
            f"P2-044 regression: the response is using Neo4j internal IDs."
        )
        assert "P12345" in node_ids, (
            f"business ID 'P12345' missing from response node IDs: {node_ids}. "
            f"P2-044 regression: the response is using Neo4j internal IDs."
        )
        for internal_id in (17, 42, 99):
            assert internal_id not in node_ids, (
                f"Neo4j internal ID {internal_id} found in response node IDs: "
                f"{node_ids}. P2-044 is REGRESSED -- business IDs must be used."
            )

        for edge in result["edges"]:
            assert edge["source"] in {"DB00001", "MESH:D001", "P12345"}, (
                f"edge source {edge['source']!r} is not a business ID "
                f"(P2-045 regression)"
            )
            assert edge["target"] in {"DB00001", "MESH:D001", "P12345"}, (
                f"edge target {edge['target']!r} is not a business ID "
                f"(P2-045 regression)"
            )

    def test_response_ids_stable_across_simulated_restart(self) -> None:
        """Simulate a Neo4j restart: the SAME nodes get DIFFERENT internal IDs
        but the SAME business IDs. The response MUST be identical across the
        restart (the frontend caches by business ID)."""

        def make_query_response(internal_id_offset: int):
            drug_node = _MockNeo4jNode(
                {"id": "DB00001", "name": "aspirin"},
                17 + internal_id_offset, ["Compound"],
            )
            disease_node = _MockNeo4jNode(
                {"id": "MESH:D001", "name": "migraine"},
                42 + internal_id_offset, ["Disease"],
            )
            rel = _MockNeo4jRelationship("treats", drug_node, disease_node)
            return [{"d": drug_node, "dis": disease_node, "r": rel,
                     "sn": drug_node, "en": disease_node}]

        def run_query(internal_id_offset: int) -> Optional[Dict[str, Any]]:
            records = make_query_response(internal_id_offset)
            mock_session = MagicMock()
            mock_session.run.return_value = records
            mock_session.__enter__ = MagicMock(return_value=mock_session)
            mock_session.__exit__ = MagicMock(return_value=False)
            mock_driver = MagicMock()
            mock_driver.session.return_value = mock_session
            with patch.object(svc, "_neo4j_driver", return_value=mock_driver):
                return svc._explore_subgraph_neo4j("aspirin", "migraine", limit=10)

        result_before = run_query(internal_id_offset=0)
        result_after = run_query(internal_id_offset=1000)

        assert result_before is not None and result_after is not None, (
            f"result_before={result_before}, result_after={result_after} -- "
            f"both should be non-None with the mock driver"
        )

        ids_before = sorted(n["id"] for n in result_before["nodes"])
        ids_after = sorted(n["id"] for n in result_after["nodes"])
        assert ids_before == ids_after, (
            f"P2-044 REGRESSION: node IDs changed across simulated restart.\n"
            f"  before: {ids_before}\n"
            f"  after:  {ids_after}\n"
            f"The frontend caches by these IDs -- if they change across "
            f"restarts, the cache breaks and the user sees stale / broken "
            f"graph visualizations."
        )

        edges_before = sorted((e["source"], e["target"], e["type"]) for e in result_before["edges"])
        edges_after = sorted((e["source"], e["target"], e["type"]) for e in result_after["edges"])
        assert edges_before == edges_after, (
            f"P2-045 REGRESSION: edge endpoints changed across simulated restart.\n"
            f"  before: {edges_before}\n"
            f"  after:  {edges_after}"
        )
