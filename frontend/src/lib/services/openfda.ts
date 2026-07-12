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
 */

const OPENFDA_BASE = "https://api.fda.gov";

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

export async function getDrugSafetySummary(drugName: string): Promise<DrugSafetySummary | null> {
  const q = (drugName || "").trim();
  if (q.length < 2) return null;

  // FE-045 ROOT FIX: the previous "best-effort" sanitization (strip a few
  // special chars + boolean operators) was fragile because the openFDA
  // query language reserves far more than quotes/parens/AND/OR/NOT — it
  // also supports field qualifiers (`field:value`), wildcards (`*`),
  // fuzzy (`~`), range queries (`[a TO b]`), and more. An attacker
  // passing `q=patient.drug.openfda.generic_name:ibuprofen` would have
  // their value pass the old sanitizer (no quotes, parens, or boolean
  // words) and end up injected as a field qualifier inside the quoted
  // search string — which openFDA's parser may interpret as a literal
  // OR as a query directive depending on its escape handling.
  //
  // Root fix: instead of trying to escape a syntax we don't fully
  // control, we apply a STRICT WHITELIST. Drug names are alphanumerics,
  // spaces, hyphens, and apostrophes (e.g. "St John's Wort"). Anything
  // else is rejected up-front and we return null — the caller treats
  // null as "no data" and the UI shows "No safety data available".
  //
  // Max 64 chars: FDA generic/brand names are well under this limit
  // (longest generic name ~30 chars; longest brand name ~25 chars), so
  // this is a sane upper bound that also caps any pathological input.
  const WHITELIST = /^[A-Za-z0-9 \-']{2,64}$/;
  if (!WHITELIST.test(q)) return null;
  const sanitized = q.replace(/\s+/g, " ").trim();

  // openFDA uses the generic (non-proprietary) name in the
  // `patient.drug.openfda.generic_name` field. We do an exact
  // (case-insensitive) match on generic_name OR brand_name.
  //
  // FE-045: build the search expression with URLSearchParams so the
  // openFDA query syntax (`"`, `:`, etc.) is properly URL-encoded
  // by the standard library. The `+OR+` separator between the two
  // field clauses is a LITERAL openFDA query operator (not a URL
  // space) — URLSearchParams would encode `+` as `%2B`, which
  // openFDA's parser does NOT accept as the OR separator. So we
  // build the search value with `+OR+` as a literal, URL-encode the
  // value with `encodeURIComponent`, then convert the encoded `%2B`
  // back to literal `+` so the openFDA API sees the operator it
  // expects. This is the same final URL format the openFDA docs
  // publish: `search=field:"value"+OR+field:"value"`.
  const searchValue =
    `patient.drug.openfda.generic_name:"${sanitized}"` +
    `+OR+` +
    `patient.drug.openfda.brand_name:"${sanitized}"`;
  const params = new URLSearchParams({ limit: "100" });
  const encodedSearch = encodeURIComponent(searchValue).replace(/%2B/g, "+");
  const url = `${OPENFDA_BASE}/drug/event.json?search=${encodedSearch}&${params.toString()}`;

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
    totalReports: results.length,
    seriousReports: serious,
    seriousReportsWithDeath: seriousWithDeath,
    topReactions,
    disclaimer: SAFETY_DISCLAIMER,
  };
}
