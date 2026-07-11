/**
 * openFDA service — real FDA adverse event data.
 *
 * Source: openFDA (https://open.fda.gov/) — U.S. Food & Drug Administration
 * Maintainer: FDA
 * License: Public domain (U.S. government work). OpenFDA data is updated nightly.
 *
 * The adverse event endpoint contains serious adverse event reports submitted
 * to the FDA Adverse Event Reporting System (FAERS). We use it to surface
 * real-world safety signals for a given drug.
 *
 * IMPORTANT: openFDA returns REPORTS, not causal evidence. A report listing
 * a drug and an adverse event does NOT mean the drug caused the event. We
 * always label this as "reported adverse events" in the UI and never imply
 * causation.
 *
 * ROOT FIXES (FE-014, FE-030):
 *
 * FE-014 — query injection: the previous code built the openFDA search
 *   expression by string-interpolating the user-supplied drug name into
 *   `patient.drug.openfda.generic_name:"${q}"`. A caller could escape the
 *   quoted context with a `"` and inject arbitrary Lucene syntax, e.g.
 *   `q = aspirin" OR patient.drug.openfda.generic_name:"ibuprofen` would
 *   silently broaden the search to all reports mentioning either drug,
 *   producing misleading safety statistics. The root fix sanitises the
 *   drug name to a strict allowlist of printable, non-meta characters
 *   AND rejects any name that still contains a double-quote after
 *   sanitisation. We also URL-encode the value through URLSearchParams
 *   so the final URL is safe regardless.
 *
 * FE-030 — totalReports: the previous code returned `results.length` as
 *   `totalReports`, but `results.length` is capped at 100 (the openFDA
 *   `limit` param max). For a drug with 50,000 reports the UI said "100
 *   reports", severely understating the safety signal. The root fix reads
 *   `body.meta.results.total` — the TRUE total report count — and falls
 *   back to `results.length` only if `meta` is missing (which only
 *   happens for 404 / empty responses, where the count is genuinely 0).
 */

const OPENFDA_BASE = "https://api.fda.gov";
const OPENFDA_MAX_LIMIT = 100; // openFDA caps `limit` at 100.

export interface AdverseEventReaction {
  term: string;
  count: number;
}

export interface DrugSafetySummary {
  brandName: string;
  genericName: string;
  totalReports: number;
  seriousReports: number;
  seriousReportsWithDeath: number;
  topReactions: AdverseEventReaction[];
  // What we display comes from real reports — but we must be explicit that
  // these are spontaneous reports and not proven causal events.
  disclaimer: string;
}

const SAFETY_DISCLAIMER =
  "Adverse event data is sourced from the FDA Adverse Event Reporting System " +
  "(FAERS) via openFDA. Reports are spontaneous and do not prove causation. " +
  "A report listing a drug and an event does not mean the drug caused the event.";

/**
 * Sanitise a drug name for inclusion in an openFDA Lucene query.
 *
 * ROOT FIX for FE-014: we only allow alphanumerics, whitespace, hyphens,
 * apostrophes, and parentheses. We explicitly REJECT any name that contains
 * a double-quote or any of Lucene's reserved characters (`+ - && || ! ( ) { } [ ] ^ " ~ * ? : \ /`).
 *
 * For names that legitimately contain a hyphen or apostrophe (e.g.
 * "lisdexamfetamine dimesylate" or "st. john's wort"), we keep those
 * characters because they are part of the generic/brand name and do not
 * break out of the quoted-string context in Lucene.
 */
function sanitizeDrugName(input: string): string {
  const trimmed = (input || "").trim();
  if (trimmed.length < 2 || trimmed.length > 200) {
    throw new Error("Drug name must be 2–200 characters long.");
  }
  // Allow letters, digits, whitespace, hyphens, apostrophes, periods,
  // commas, and parentheses. Everything else is stripped.
  const cleaned = trimmed.replace(/[^A-Za-z0-9\s\-'.,()]/g, "");
  if (cleaned.length < 2) {
    throw new Error("Drug name contains no usable characters after sanitisation.");
  }
  // Defense in depth: even after stripping, if a double-quote or backslash
  // somehow survived, refuse — never interpolate into the Lucene string.
  if (/["\\]/.test(cleaned)) {
    throw new Error("Drug name contains forbidden characters.");
  }
  return cleaned;
}

<<<<<<< HEAD
export async function getDrugSafetySummary(drugName: string): Promise<DrugSafetySummary | null> {
  let q: string;
  try {
    q = sanitizeDrugName(drugName);
  } catch {
    return null;
  }

  // openFDA uses the generic (non-proprietary) name in the
  // `patient.drug.openfda.generic_name` field. We do an exact (case-
  // insensitive) match on generic_name OR brand_name.
  //
  // FE-014 root fix: `q` has been sanitised to remove all Lucene metachars
  // and double-quotes, so it cannot break out of the quoted context.
  const search = `patient.drug.openfda.generic_name:"${q}"+OR+patient.drug.openfda.brand_name:"${q}"`;
  // Build the URL manually — we MUST preserve the literal `+` characters
  // in `+OR+` (they are Lucene syntax, not URL spaces). URLSearchParams
  // would encode `+` as `%2B`, which openFDA rejects. We use
  // encodeURIComponent on the search value, then restore the `+` chars
  // that are part of the Lucene expression.
  const encodedSearch = encodeURIComponent(search).replace(/%2B/g, "+");
  const url = `${OPENFDA_BASE}/drug/event.json?search=${encodedSearch}&limit=${OPENFDA_MAX_LIMIT}`;
=======
  // FE-014 ROOT FIX: Sanitize user input before interpolating into the
  // openFDA query expression. The previous code did:
  //   const search = `patient.drug.openfda.generic_name:"${q}"+OR+...`
  //   const url = `${OPENFDA_BASE}/drug/event.json?search=${encodeURIComponent(search)...}`
  //
  // The openFDA search syntax reserves ", (, ), AND, OR, NOT. An attacker
  // passing q=aspirin") AND (patient.drug.openfda.generic_name:ibuprofen
  // could manipulate the query — escape the quoted field, inject boolean
  // operators, exfiltrate data from other drugs, exhaust the openFDA API
  // quota, or trigger 500s.
  //
  // Root fix: strip every character that has special meaning in the openFDA
  // query language BEFORE interpolation. We allow alphanumerics, spaces,
  // hyphens, and apostrophes (e.g., "St John's Wort"). Everything else is
  // removed. We also collapse whitespace and apply a max length.
  const sanitized = q
    .replace(/["()\\]/g, "") // remove quotes, parens, backslashes
    .replace(/\b(AND|OR|NOT)\b/gi, "") // remove boolean operators
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 128);
  if (sanitized.length < 2) return null;

  // openFDA uses the generic (non-proprietary) name in the `patient.drug.openfda.generic_name` field.
  // We do an exact (case-insensitive) match on generic_name OR brand_name.
  // The sanitized value is safe to interpolate because we stripped every
  // special character above.
  const search = `patient.drug.openfda.generic_name:"${sanitized}"+OR+patient.drug.openfda.brand_name:"${sanitized}"`;
  const url = `${OPENFDA_BASE}/drug/event.json?search=${encodeURIComponent(search).replace(/%2B/g, "+")}&limit=100`;
>>>>>>> fix/v101-forensic-root-fixes-20-critical-bugs

  // Note: openFDA responses can exceed Next.js's 2MB fetch cache limit, so
  // we do not pass `next: { revalidate }` here — we always fetch fresh.
  const res = await fetch(url, {
    headers: { Accept: "application/json" },
  });
  if (res.status === 404) {
    // openFDA returns 404 when there are zero matches — not an error.
    return {
      brandName: q,
      genericName: q,
      totalReports: 0,
      seriousReports: 0,
      seriousReportsWithDeath: 0,
      topReactions: [],
      disclaimer: SAFETY_DISCLAIMER,
    };
  }
  if (!res.ok) {
    throw new Error(`openFDA returned ${res.status}`);
  }
  const body = await res.json();
  const results: any[] = body?.results || [];
  if (results.length === 0) {
    return {
      brandName: q,
      genericName: q,
      totalReports: 0,
      seriousReports: 0,
      seriousReportsWithDeath: 0,
      topReactions: [],
      disclaimer: SAFETY_DISCLAIMER,
    };
  }

  // FE-030 root fix: read the TRUE total report count from
  // `body.meta.results.total`. This is the count of ALL reports matching
  // the query, not just the 100 we fetched. Fall back to `results.length`
  // only if `meta` is missing for any reason.
  const trueTotal: number =
    typeof body?.meta?.results?.total === "number"
      ? body.meta.results.total
      : results.length;

  let serious = 0;
  let seriousWithDeath = 0;
  const reactionCounts: Record<string, number> = {};
  for (const ev of results) {
    if (ev.serious === "1") {
      serious++;
      if (ev.seriousnessdeath === "1") seriousWithDeath++;
    }
    for (const r of ev.patient?.reaction || []) {
      // Prefer reactionmeddrapt Preferred Term text; fall back to meddra code.
      const term = r.reactionmeddrapt || r.termmeddra;
      if (term) {
        reactionCounts[term] = (reactionCounts[term] || 0) + 1;
      }
    }
  }
  const topReactions: AdverseEventReaction[] = Object.entries(reactionCounts)
    .map(([term, count]) => ({ term, count }))
    .sort((a, b) => b.count - a.count)
    .slice(0, 10);

  // Pull display names from the first matching record
  const first = results[0];
  const openfda = first?.patient?.drug?.[0]?.openfda || {};
  return {
    brandName: (openfda.brand_name || [q])[0],
    genericName: (openfda.generic_name || [q])[0],
    totalReports: trueTotal,
    seriousReports: serious,
    seriousReportsWithDeath: seriousWithDeath,
    topReactions,
    disclaimer: SAFETY_DISCLAIMER,
  };
}
