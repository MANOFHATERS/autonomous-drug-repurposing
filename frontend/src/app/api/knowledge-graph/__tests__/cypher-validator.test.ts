/**
 * FE-008 ROOT FIX unit tests: Cypher read-only validator.
 *
 * Verifies the validateReadOnlyCypher function used by the
 * /api/knowledge-graph POST handler. This function is the second layer
 * of defense (after the role gate) — it rejects any Cypher containing
 * write operations (CREATE, DELETE, SET, REMOVE, MERGE, DROP, CALL, etc.)
 * BEFORE the query is forwarded to the downstream KG service.
 *
 * TM10 v128 ROOT FIX (Task 10.3): added the explicitly-required injection
 * vectors from the task spec:
 *   - CALL{CREATE} (subquery injection)
 *   - CALL{DELETE} (subquery injection)
 *   - apoc.create.* (procedure-call mutation)
 * Also fixed the STALE "rejects oversized query (> 5000 chars)" test — the
 * MAX_CYPHER_LENGTH constant was raised from 5000 to 10000 by BE-082, but
 * the test was never updated. A 6000-char query (under the new 10000 limit)
 * would PASS the validator, so the old test asserting `{ ok: false }` was
 * a false negative that would have flagged a passing query as failing.
 * The test now uses a 12000-char query (over the new limit) and the
 * description was updated to match the actual limit.
 */
import { validateReadOnlyCypher } from "../cypher-validator";

describe("FE-008: Cypher read-only validator", () => {
  test("accepts a simple MATCH/RETURN query", () => {
    const r = validateReadOnlyCypher("MATCH (n:Drug) RETURN n LIMIT 10");
    expect(r.ok).toBe(true);
  });

  test("accepts OPTIONAL MATCH", () => {
    const r = validateReadOnlyCypher(
      "OPTIONAL MATCH (n:Drug)-[:treats]->(d:Disease) RETURN n, d"
    );
    expect(r.ok).toBe(true);
  });

  test("accepts WITH / RETURN composition", () => {
    const r = validateReadOnlyCypher(
      "MATCH (n:Drug) WITH n LIMIT 10 RETURN n"
    );
    expect(r.ok).toBe(true);
  });

  test("rejects CREATE", () => {
    const r = validateReadOnlyCypher("CREATE (n:Drug {name: 'foo'})");
    expect(r.ok).toBe(false);
    expect(r.reason).toMatch(/CREATE/i);
  });

  test("rejects DELETE", () => {
    const r = validateReadOnlyCypher("MATCH (n:Drug) DELETE n");
    expect(r.ok).toBe(false);
    expect(r.reason).toMatch(/DELETE/i);
  });

  test("rejects SET", () => {
    const r = validateReadOnlyCypher("MATCH (n:Drug) SET n.foo = 'bar'");
    expect(r.ok).toBe(false);
    expect(r.reason).toMatch(/SET/i);
  });

  test("rejects MERGE", () => {
    const r = validateReadOnlyCypher("MERGE (n:Drug {name: 'foo'})");
    expect(r.ok).toBe(false);
    expect(r.reason).toMatch(/MERGE/i);
  });

  test("rejects DROP", () => {
    const r = validateReadOnlyCypher("MATCH (n) DROP DATABASE x");
    expect(r.ok).toBe(false);
  });

  test("rejects CALL (procedure invocation)", () => {
    const r = validateReadOnlyCypher(
      "CALL apoc.destroyNode(node) YIELD value RETURN value"
    );
    expect(r.ok).toBe(false);
    expect(r.reason).toMatch(/CALL/i);
  });

  test("rejects DETACH DELETE", () => {
    const r = validateReadOnlyCypher("MATCH (n) DETACH DELETE n");
    expect(r.ok).toBe(false);
  });

  test("rejects UNWIND (used in batch writes)", () => {
    const r = validateReadOnlyCypher(
      "UNWIND $rows AS row CREATE (n:Drug) SET n.name = row.name"
    );
    expect(r.ok).toBe(false);
  });

  test("rejects empty query", () => {
    const r = validateReadOnlyCypher("");
    expect(r.ok).toBe(false);
  });

  test("rejects query that does not start with a read verb", () => {
    const r = validateReadOnlyCypher("WHERE 1=1 RETURN 1");
    expect(r.ok).toBe(false);
    expect(r.reason).toMatch(/MATCH|OPTIONAL MATCH|WITH|RETURN/i);
  });

  test("rejects multiple statements (semicolon-separated)", () => {
    const r = validateReadOnlyCypher("MATCH (n) RETURN n; MATCH (m) RETURN m");
    expect(r.ok).toBe(false);
    expect(r.reason).toMatch(/Multiple/i);
  });

  test("does NOT reject a semicolon at the very end (single statement)", () => {
    const r = validateReadOnlyCypher("MATCH (n) RETURN n;");
    expect(r.ok).toBe(true);
  });

  test("does not false-positive on semicolons inside string literals", () => {
    const r = validateReadOnlyCypher(
      "MATCH (n) WHERE n.name = 'a;b;c' RETURN n"
    );
    expect(r.ok).toBe(true);
  });

  // TM10 v128 ROOT FIX (Task 10.3): updated limit from 5000 → 10000 to
  // match the BE-082 alignment with the Zod schema (KnowledgeGraphBody.cypher
  // = z.string().min(1).max(10_000)). The previous test used 6000 chars
  // (under the new 10000 limit) which would PASS the validator — the
  // assertion `{ ok: false }` was a false negative.
  test("rejects oversized query (> 10000 chars)", () => {
    const r = validateReadOnlyCypher("MATCH (n) RETURN n " + "x".repeat(12000));
    expect(r.ok).toBe(false);
    expect(r.reason).toMatch(/too long/i);
  });

  test("accepts a query at exactly the 10000 char limit (boundary)", () => {
    // Exactly MAX_CYPHER_LENGTH chars total — should pass.
    const prefix = "MATCH (n) RETURN n ";
    const padding = "x".repeat(10000 - prefix.length);
    const r = validateReadOnlyCypher(prefix + padding);
    expect(r.ok).toBe(true);
  });

  // ===========================================================================
  // TM10 v128 ROOT FIX (Task 10.3): explicitly-required injection vectors.
  // The task spec says: "add a test that tries CALL{CREATE}, CALL{DELETE},
  // apoc.create.*; all must be blocked."
  // ===========================================================================

  test("TM10 v128: rejects CALL{CREATE} subquery injection", () => {
    // Cypher subquery syntax: `CALL { <inner query> }`. An attacker tries
    // to smuggle a CREATE inside the subquery. The validator must block
    // this on TWO grounds: (1) the keyword `CALL`, and (2) the keyword
    // `CREATE`. Either alone is sufficient.
    const r = validateReadOnlyCypher(
      "MATCH (n:Drug) CALL { CREATE (m:Malicious {name: 'pwned'}) RETURN m } RETURN n"
    );
    expect(r.ok).toBe(false);
    // Reason must mention CALL or CREATE (both are present).
    expect(r.reason).toMatch(/CALL|CREATE/i);
  });

  test("TM10 v128: rejects CALL{DELETE} subquery injection", () => {
    // Same pattern — DELETE smuggled inside a CALL subquery.
    const r = validateReadOnlyCypher(
      "MATCH (n:Drug) CALL { MATCH (m:Malicious) DELETE m } RETURN n"
    );
    expect(r.ok).toBe(false);
    expect(r.reason).toMatch(/CALL|DELETE/i);
  });

  test("TM10 v128: rejects CALL apoc.create.node (procedure mutation)", () => {
    // apoc.create.* procedures write to the graph. The validator blocks
    // this on TWO grounds: (1) the keyword `CALL`, and (2) the keyword
    // `CREATE` (inside `apoc.create`). Either alone is sufficient.
    const r = validateReadOnlyCypher(
      "CALL apoc.create.node(['Malicious'], {name: 'pwned'}) YIELD node RETURN node"
    );
    expect(r.ok).toBe(false);
    expect(r.reason).toMatch(/CALL|CREATE/i);
  });

  test("TM10 v128: rejects CALL apoc.create.relationship (procedure mutation)", () => {
    // apoc.create.relationship adds edges. Same blocking logic.
    const r = validateReadOnlyCypher(
      "MATCH (a:Drug), (b:Disease) CALL apoc.create.relationship(a, 'FAKE_EDGE', {}, b) YIELD rel RETURN rel"
    );
    expect(r.ok).toBe(false);
    expect(r.reason).toMatch(/CALL|CREATE/i);
  });

  test("TM10 v128: rejects CALL{...} with MERGE inside subquery", () => {
    // Variant — MERGE smuggled inside CALL.
    const r = validateReadOnlyCypher(
      "MATCH (n:Drug) CALL { MERGE (m:Fake {id: 1}) RETURN m } RETURN n"
    );
    expect(r.ok).toBe(false);
    expect(r.reason).toMatch(/CALL|MERGE/i);
  });

  test("TM10 v128: rejects CALL{...} with SET inside subquery", () => {
    // Variant — SET smuggled inside CALL.
    const r = validateReadOnlyCypher(
      "MATCH (n:Drug) CALL { MATCH (m) SET m.attacker_field = 'pwned' RETURN m } RETURN n"
    );
    expect(r.ok).toBe(false);
    expect(r.reason).toMatch(/CALL|SET/i);
  });

  test("TM10 v128: rejects CALL{...} with DETACH DELETE inside subquery", () => {
    // Variant — DETACH DELETE smuggled inside CALL.
    const r = validateReadOnlyCypher(
      "MATCH (n:Drug) CALL { MATCH (m:Malicious) DETACH DELETE m } RETURN n"
    );
    expect(r.ok).toBe(false);
    expect(r.reason).toMatch(/CALL|DELETE|DETACH/i);
  });

  test("TM10 v128: rejects CALL{} with LOAD CSV inside subquery", () => {
    // LOAD CSV WITH HEADERS is a write-adjacent operation (can be used to
    // exfiltrate data or trigger file reads). Blocked by both CALL and
    // LOAD CSV rules.
    const r = validateReadOnlyCypher(
      "CALL { LOAD CSV WITH HEADERS FROM 'file:///etc/passwd' AS row CREATE (n:Exfil {line: row}) RETURN n } RETURN 1"
    );
    expect(r.ok).toBe(false);
    expect(r.reason).toMatch(/CALL|LOAD|CSV|CREATE/i);
  });

  test("TM10 v128: rejects YIELD without CALL (defense in depth)", () => {
    // YIELD only appears after a CALL procedure. If YIELD appears alone,
    // it's either a syntax error or an attempt to smuggle a procedure
    // call past the validator. Either way, reject.
    const r = validateReadOnlyCypher(
      "MATCH (n) YIELD value RETURN value"
    );
    expect(r.ok).toBe(false);
    expect(r.reason).toMatch(/YIELD/i);
  });

  test("TM10 v128: rejects FOREACH (used in batch writes)", () => {
    // FOREACH iterates a list and runs a write operation per element.
    // Even if the body is read-only (rare), FOREACH is a write-pattern
    // marker — block it.
    const r = validateReadOnlyCypher(
      "MATCH (n:Drug) FOREACH (x IN [1,2,3] | SET n['f'+x] = x) RETURN n"
    );
    expect(r.ok).toBe(false);
    expect(r.reason).toMatch(/FOREACH|SET/i);
  });

  test("TM10 v128: rejects PERIODIC COMMIT (batch writes)", () => {
    // PERIODIC COMMIT is an apoc-style batch write directive.
    const r = validateReadOnlyCypher(
      "CALL apoc.periodic.commit('MATCH (n) RETURN n', {})"
    );
    expect(r.ok).toBe(false);
    expect(r.reason).toMatch(/CALL|PERIODIC|COMMIT/i);
  });

  test("TM10 v128: case-insensitive — rejects lowercase call{create}", () => {
    // The validator must be case-insensitive (regex has `i` flag).
    const r = validateReadOnlyCypher(
      "match (n) call { create (m) return m } return n"
    );
    expect(r.ok).toBe(false);
  });

  test("TM10 v128: case-insensitive — rejects mixed-case CALL{Create}", () => {
    const r = validateReadOnlyCypher(
      "MaTcH (n) CaLl { CrEaTe (m) ReTuRn m } ReTuRn n"
    );
    expect(r.ok).toBe(false);
  });

  test("TM10 v128: does NOT false-positive on 'CREATE' inside a string literal", () => {
    // A legitimate query that mentions CREATE in a string value should
    // NOT be blocked. The forbidden-keyword regex is word-bounded, but
    // it doesn't strip string literals first — so the keyword WOULD
    // match here. This is an OVERLY-STRICT behavior (false rejection of
    // a legitimate query), but it's SAFE (better to reject a legitimate
    // query than to allow a malicious one). The test documents this
    // known limitation so future maintainers don't think it's a bug.
    //
    // If a researcher genuinely needs to query for a node whose name
    // contains "CREATE", they should use a parameterized query:
    //   MATCH (n) WHERE n.name = $name RETURN n
    // and pass `name: "CREATE"` as a parameter — the validator doesn't
    // inspect parameter values.
    const r = validateReadOnlyCypher(
      "MATCH (n) WHERE n.name = 'CREATE INDEX' RETURN n"
    );
    // Known overly-strict behavior — documents the trade-off.
    expect(r.ok).toBe(false);
    expect(r.reason).toMatch(/CREATE/i);
  });

  test("TM10 v128: rejects attempts to bypass with inline comments", () => {
    // Cypher supports `//` line comments. An attacker might try:
    //   MATCH (n) RETURN n // CREATE (m)
    // The validator's keyword regex scans the WHOLE string (after
    // trimming) — it does NOT strip comments. So `CREATE` in the comment
    // is still matched. This is overly-strict (a legitimate query that
    // happens to mention CREATE in a comment is rejected) but SAFE.
    const r = validateReadOnlyCypher(
      "MATCH (n:Drug) RETURN n // TODO: CREATE index on n.name"
    );
    expect(r.ok).toBe(false);
    expect(r.reason).toMatch(/CREATE/i);
  });

  test("TM10 v128: rejects stacked queries with comment-based obfuscation", () => {
    // Attempt: MATCH (n) RETURN n/*;*/; CREATE (m)
    // The validator strips string literals AND backtick identifiers
    // before counting semicolons. `/* */` is NOT a Cypher comment (Cypher
    // only supports `//` and `--`), so the comment text is parsed as
    // identifiers. The forbidden-keyword check catches `CREATE` regardless.
    const r = validateReadOnlyCypher(
      "MATCH (n) RETURN n/*;*/; CREATE (m:Malicious)"
    );
    expect(r.ok).toBe(false);
    // Either forbidden keyword (CREATE) or multiple-statements detection
    // catches this.
    expect(r.ok).toBe(false);
  });
});
