import { NextRequest, NextResponse } from "next/server";
// Task 11.5 ROOT FIX (v129, TM11 — hostile-auditor pass):
// - Accepts BOTH ?q=<free-text> AND ?drug=<name>&disease=<name>.
// - When drug+disease are provided, builds a structured PubMed query
//   that searches for both terms in Title/Abstract — the standard
//   "is there published literature supporting this drug-disease pair?"
//   query that the V1 launch criteria (project docx Section 8) requires:
//   "At least 5 top predictions are supported by published literature."
// - Returns count + top N PMIDs (default N=5) per drug-disease pair,
//   per the task spec. The `pmids` field is the structured list; the
//   `items` field is the full article metadata (kept for the existing
//   UI that renders the literature-search results list).
// - Uses ESearch + ESummary (already implemented in pubmed.ts). The
//   task spec mentions EFetch, but EFetch returns FULL abstract text
//   (potentially 5KB+ per article × 100 articles = 500KB+ per query).
//   ESummary returns title + journal + authors + pubDate without the
//   abstract — the abstract is fetched on-demand via getAbstract(pmid)
//   when the user expands an article in the UI. This is the standard
//   PubMed UX pattern and keeps the search response under 50KB.
//   For the drug-disease case where the task wants "abstracts", we
//   additionally call efetch for the TOP 5 PMIDs to populate the
//   `abstracts` field — so the V1 criterion "5+ literature-supported
//   predictions" can be verified by checking that each prediction's
//   `literature.count >= 5` AND `literature.abstracts.length >= 1`.
import { searchPubMed, getAbstract } from "@/lib/services/pubmed";
import { badRequest, internalError } from "@/lib/api-helpers";
import {
  requireAuthAndRateLimit,
  recordApiRequestForUser,
} from "@/lib/auth/api-proxy-guard";

/**
 * Build a PubMed query string from a drug name and/or disease name.
 *
 * The query uses [Title/Abstract] field qualifiers to search the
 * title and abstract of every indexed article. This is the standard
 * PubMed query for "has anyone published on this drug-disease pair?".
 *
 * Examples:
 *   buildDrugDiseaseQuery("aspirin", "cancer")
 *     → '"aspirin"[Title/Abstract] AND "cancer"[Title/Abstract]'
 *
 *   buildDrugDiseaseQuery("aspirin", undefined)
 *     → '"aspirin"[Title/Abstract]'
 *
 *   buildDrugDiseaseQuery(undefined, "cancer")
 *     → '"cancer"[Title/Abstract]'
 *
 * The drug/disease names are wrapped in double quotes so PubMed
 * treats them as phrase queries (avoids "aspirin" matching
 * "aspirations" or "baspirin").
 *
 * SECURITY: drug and disease names are sanitized to remove PubMed
 * query syntax (quotes, parentheses, boolean operators, field
 * qualifiers). An attacker passing drug='aspirin" OR "ibuprofen'
 * would otherwise craft a query that returns aspirin OR ibuprofen
 * articles — mixing two drugs' literature. The sanitizer strips
 * the double-quote character before re-quoting.
 */
function sanitizePubMedTerm(term: string): string {
  // Strip characters that have meaning in PubMed's query syntax:
  //   " ( ) [ ] : * ? ~ AND OR NOT
  // This is a whitelist approach: keep alphanumerics, spaces, hyphens,
  // apostrophes, commas, periods. Anything else is removed.
  return term.replace(/[^A-Za-z0-9 \-',.]/g, " ").replace(/\s+/g, " ").trim();
}

function buildDrugDiseaseQuery(drug?: string, disease?: string): string | null {
  const parts: string[] = [];
  const cleanDrug = drug ? sanitizePubMedTerm(drug) : "";
  const cleanDisease = disease ? sanitizePubMedTerm(disease) : "";
  if (cleanDrug) parts.push(`"${cleanDrug}"[Title/Abstract]`);
  if (cleanDisease) parts.push(`"${cleanDisease}"[Title/Abstract]`);
  if (parts.length === 0) return null;
  return parts.join(" AND ");
}

export async function GET(req: NextRequest) {
  const guard = await requireAuthAndRateLimit(req);
  if (guard.response !== null) return guard.response;

  // Task 11.5: accept BOTH query contracts.
  //   1. ?q=<free-text>            — existing contract (kept for backwards compat)
  //   2. ?drug=<name>&disease=<name>  — task spec contract (drug-disease pair lookup)
  const q = req.nextUrl.searchParams.get("q") || "";
  const drug = req.nextUrl.searchParams.get("drug") || undefined;
  const disease = req.nextUrl.searchParams.get("disease") || undefined;
  const limit = parseInt(req.nextUrl.searchParams.get("limit") || "15", 10);
  const offset = parseInt(req.nextUrl.searchParams.get("offset") || "0", 10);
  const sort = (req.nextUrl.searchParams.get("sort") || "relevance") as any;
  const yearFrom = req.nextUrl.searchParams.get("yearFrom")
    ? parseInt(req.nextUrl.searchParams.get("yearFrom")!, 10)
    : undefined;
  const yearTo = req.nextUrl.searchParams.get("yearTo")
    ? parseInt(req.nextUrl.searchParams.get("yearTo")!, 10)
    : undefined;

  // Build the query. If both `q` and `drug`/`disease` are provided,
  // `q` takes precedence (backwards compat). If only `drug`/`disease`
  // are provided, build a structured query.
  let query = q.trim();
  if (!query) {
    const built = buildDrugDiseaseQuery(drug, disease);
    if (built) query = built;
  }

  if (!query || query.trim().length < 2) {
    return badRequest(
      "Either ?q=<query> (min 2 chars) or ?drug=<name>&disease=<name> is required",
    );
  }

  try {
    const result = await searchPubMed({ query, limit, offset, sort, yearFrom, yearTo });
    recordApiRequestForUser(guard.user);

    // Task 11.5: when the query was built from drug+disease, also fetch
    // abstracts for the TOP 5 PMIDs so the V1 criterion ("5+ literature-
    // supported predictions") can be verified. We use getAbstract()
    // which calls NCBI EFetch internally. The abstracts are returned
    // in a separate `abstracts` field so the existing `items` field
    // (which contains ESummary metadata) is unchanged.
    let abstracts: Array<{ pmid: string; abstract?: string }> = [];
    const wasDrugDiseaseQuery = !q && (!!drug || !!disease);
    if (wasDrugDiseaseQuery && result.articles.length > 0) {
      const top5Pmids = result.articles.slice(0, 5).map((a) => a.pmid);
      // Fan out 5 parallel EFetch calls — each is independent. We use
      // Promise.allSettled so a single EFetch failure (429, network
      // error) does NOT fail the whole request. Failed abstracts are
      // omitted from the response (the `abstracts` array only contains
      // PMIDs that successfully fetched).
      const abstractResults = await Promise.allSettled(
        top5Pmids.map(async (pmid): Promise<{ pmid: string; abstract?: string }> => {
          const abstract = await getAbstract(pmid, 500); // truncate to 500 chars
          return { pmid, abstract };
        }),
      );
      abstracts = abstractResults
        .filter(
          (r): r is PromiseFulfilledResult<{ pmid: string; abstract?: string }> =>
            r.status === "fulfilled",
        )
        .map((r) => r.value);
    }

    // FE-006: map `articles` → `items`. Keep `total`, `limit`, `offset`.
    return NextResponse.json({
      items: result.articles,
      total: result.total,
      limit,
      offset,
      // Task 11.5: structured fields for the V1 literature-support check.
      // The dashboard's "literature-supported hypothesis" badge checks
      // `pmids.length >= 5` per the V1 criterion (project docx Section 8).
      pmids: result.articles.map((a) => a.pmid),
      count: result.articles.length,
      // Task 11.5: when the query was drug+disease, include the
      // structured query so the caller can verify what was searched.
      query,
      querySource: wasDrugDiseaseQuery ? "drug_disease" : "free_text",
      // Task 11.5: top-5 abstracts (only populated for drug-disease queries).
      abstracts,
    });
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e);
    return internalError(`PubMed search failed: ${msg}`);
  }
}
