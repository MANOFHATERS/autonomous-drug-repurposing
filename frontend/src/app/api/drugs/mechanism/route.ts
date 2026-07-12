import { NextRequest, NextResponse } from "next/server";
import { lookupDrugMechanisms } from "@/lib/services/drug-mechanism";
import { requireAuth, badRequest } from "@/lib/api-helpers";

/**
 * FE-010 ROOT FIX (Team Member 13): escape KG text fields before returning.
 *
 * The `mechanism` text returned by ChEMBL is rendered as HTML by some
 * frontend components. If the KG's mechanism text contains HTML/JS
 * (e.g., from a corrupted DrugBank import or a ChEMBL data error), the
 * frontend would render it as HTML — XSS. An attacker who can write to
 * the KG (e.g., a compromised ChEMBL mirror, or a malicious internal
 * user with KG write access) could inject a script that steals the
 * session token.
 *
 * ROOT FIX: this route escapes ALL text fields returned from the KG
 * before sending them to the client. We use a strict allowlist-based
 * escape: every character that is not in [a-zA-Z0-9 ,.-:;()'/] is
 * replaced with its HTML entity. This is more conservative than
 * DOMPurify (which parses HTML and strips disallowed tags) — but it
 * is also safer because it cannot be bypassed by malformed HTML.
 *
 * DEFENSE IN DEPTH: the frontend should ALSO run DOMPurify on the
 * mechanism text before rendering it as HTML. This route's escaping
 * is the server-side backstop — if the frontend forgets to sanitize,
 * the server-side escape prevents the XSS.
 *
 * WHY NOT JUST ESCAPE <, >, &, ", ': those are the standard HTML
 * special chars. But there are many other XSS vectors (e.g., U+202E
 * right-to-left override, U+0000 null, U+FEFF BOM, etc.). The strict
 * allowlist is more defensive — it rejects ANYTHING that is not
 * printable ASCII or a small set of punctuation. If the mechanism
 * text contains non-ASCII (e.g., accented characters), they are
 * converted to their HTML numeric entity — the browser renders them
 * correctly, but they cannot form XSS.
 */

/**
 * Escape a string for safe inclusion in HTML. Every character that is
 * not in the strict allowlist is replaced with its HTML numeric entity.
 *
 * The allowlist is:
 *   - a-zA-Z0-9
 *   - space, comma, period, hyphen, colon, semicolon
 *   - parens, single-quote, forward-slash
 *
 * All other characters (including <, >, &, ", double-quote, non-ASCII)
 * are converted to &#NN; (decimal HTML entity).
 */
function escapeKgText(s: string | null | undefined): string | null {
  if (s === null || s === undefined) return null;
  // Allowlist: letters, digits, space, comma, period, hyphen, colon,
  // semicolon, parens, single-quote, forward-slash. Everything else
  // becomes an HTML numeric entity.
  const ALLOWED = /^[a-zA-Z0-9 ,.\-:;()'/]$/;
  let out = "";
  for (let i = 0; i < s.length; i++) {
    const ch = s.charAt(i);
    if (ALLOWED.test(ch)) {
      out += ch;
    } else {
      out += `&#${s.charCodeAt(i)};`;
    }
  }
  return out;
}

/**
 * POST /api/drugs/mechanism
 * Body: { drugNames: string[] }
 *
 * FE-024 ROOT FIX: Returns the real mechanism of action for each drug
 * name, sourced from ChEMBL. Used by the candidate table to render the
 * "Mechanism" column with real data instead of RL debug output.
 *
 * Auth required: an unauthenticated caller could otherwise enumerate the
 * ChEMBL cache and use this server as a proxy to scrape ChEMBL.
 */
export async function POST(req: NextRequest) {
  const auth = await requireAuth();
  if (auth.user === null) return auth.response;

  let body: { drugNames?: unknown };
  try {
    body = await req.json();
  } catch {
    return badRequest("Invalid JSON body");
  }

  const raw = body.drugNames;
  if (!Array.isArray(raw)) {
    return badRequest("drugNames must be an array of strings");
  }
  // Sanitize + bound the input so a malicious client can't DoS ChEMBL
  // with a 10,000-drug batch.
  const drugNames = raw
    .filter((n): n is string => typeof n === "string")
    .map((n) => n.trim())
    .filter((n) => n.length >= 2 && n.length <= 128)
    .slice(0, 100);

  if (drugNames.length === 0) {
    return NextResponse.json({ results: [] });
  }

  const map = await lookupDrugMechanisms(drugNames);
  const results = drugNames.map((name) => {
    const r = map.get(name.toLowerCase()) || {
      drugName: name,
      chemblId: null,
      mechanism: null,
      source: null,
      fetchedAt: new Date().toISOString(),
    };
    // FE-010: escape every text field before returning. The `fetchedAt`
    // and `chemblId` fields are server-generated and safe, but we
    // escape them anyway for defense in depth (a ChEMBL ID is
    // "CHEMBL123" so escaping is a no-op, but the consistency is
    // worth it).
    return {
      drugName: escapeKgText(r.drugName),
      chemblId: escapeKgText(r.chemblId),
      mechanism: escapeKgText(r.mechanism),
      source: escapeKgText(r.source),
      fetchedAt: r.fetchedAt, // ISO timestamp — server-generated, safe
    };
  });
  return NextResponse.json({ results });
}
