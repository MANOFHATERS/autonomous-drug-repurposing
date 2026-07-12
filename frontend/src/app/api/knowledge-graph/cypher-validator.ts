/**
 * FE-008 ROOT FIX: shared Cypher validator.
 *
 * This module exports validateReadOnlyCypher — the function used by the
 * /api/knowledge-graph POST handler to whitelist read-only Cypher. It is
 * extracted into its own module so unit tests can exercise it without
 * spinning up the Next.js route handler.
 */

// Maximum query length — guards against memory-exhaustion via huge queries.
const MAX_CYPHER_LENGTH = 5000;

// Statements that mutate the graph or call procedures. We reject these
// BEFORE forwarding to the KG service. The check is case-insensitive and
// word-bounded so it doesn't false-positive on legitimate identifiers.
const FORBIDDEN_CYPHER_KEYWORDS =
  /\b(CREATE|MERGE|DELETE|DETACH\s+DELETE|SET|REMOVE|DROP|CALL|YIELD|UNWIND|FOREACH|LOAD\s+CSV|PERIODIC\s+COMMIT)\b/gi;

// The ONLY top-level verbs allowed in a read-only Cypher query.
const ALLOWED_TOP_LEVEL_VERBS =
  /^\s*(MATCH|OPTIONAL\s+MATCH|WITH|RETURN|CALL\s+db\.labels)\b/i;

export function validateReadOnlyCypher(
  cypher: string
): { ok: boolean; reason?: string } {
  const trimmed = cypher.trim();
  if (!trimmed) return { ok: false, reason: "Cypher query is empty." };
  if (trimmed.length > MAX_CYPHER_LENGTH) {
    return {
      ok: false,
      reason: `Cypher query is too long (max ${MAX_CYPHER_LENGTH} chars).`,
    };
  }
  // Reject any forbidden keyword anywhere in the query.
  const forbiddenMatch = trimmed.match(FORBIDDEN_CYPHER_KEYWORDS);
  if (forbiddenMatch) {
    return {
      ok: false,
      reason: `Cypher contains a forbidden keyword: ${forbiddenMatch[0]}. Only read-only MATCH / OPTIONAL MATCH / WITH / RETURN queries are allowed via this endpoint.`,
    };
  }
  // The first non-comment token must be a read verb.
  if (!ALLOWED_TOP_LEVEL_VERBS.test(trimmed)) {
    return {
      ok: false,
      reason:
        "Cypher must start with MATCH, OPTIONAL MATCH, WITH, or RETURN. " +
        "Write operations (CREATE, DELETE, SET, etc.) are not permitted.",
    };
  }
  // Defensive: reject multiple statements (semicolon-separated). Strip
  // string literals first so semicolons inside strings don't trip us.
  const stripped = trimmed
    .replace(/'(?:[^'\\]|\\.)*'/g, "''")
    .replace(/"(?:[^"\\]|\\.)*"/g, '""');
  const statementCount = (stripped.match(/;/g) || []).length;
  if (statementCount > 1 || (statementCount === 1 && !stripped.endsWith(";"))) {
    return {
      ok: false,
      reason: "Multiple Cypher statements are not allowed.",
    };
  }
  return { ok: true };
}
