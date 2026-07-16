/**
 * USPTO PatentsView service — real patent data.
 *
 * Source: PatentsView API (https://search.patentsview.org/api/v1/patent/)
 * Maintainer: U.S. Patent and Trademark Office, via a research partnership.
 * License: Public domain (U.S. government work).
 *
 * This endpoint returns real patent grants from the USPTO. We use it to
 * surface patents that mention a drug or disease name in their title,
 * abstract, or claims — useful for IP due diligence.
 *
 * NOTE: PatentsView requires an API key. We read it from PATENTSVIEW_API_KEY
 * env var. If the key is missing we degrade gracefully with an empty result
 * set and a clear reason, rather than returning fake data.
 *
 * FE-026 ROOT FIX (Team Member 15):
 *
 * ROOT CAUSE: PatentsView returns at most 100 patents per page. The
 * previous code set `o: { size: limit }` and never followed
 * pagination. For a drug like "aspirin" with 500+ patents, only the
 * first 100 were returned. The RL ranker's `patent_score` dimension
 * (which uses patent count) was then based on 100/500 = 20% of the
 * data — silently corrupting the ranking.
 *
 * ROOT FIX: when the caller does not specify a limit (or specifies a
 * limit > 100), we loop pages using `o.offset` until we've collected
 * all results OR hit the safety cap (1000 patents / 10 pages). The
 * returned `total` field is the true total_hits from PatentsView
 * (which may exceed the number of patents we actually fetch — we
 * expose both so the UI can show "showing 100 of 523").
 *
 * When the caller specifies a `limit` ≤ 100, we make a single request
 * (no pagination needed) — preserving the original behavior for
 * callers that explicitly want a small page.
 */

import { monitoredFetch } from "@/lib/external-api-monitor";

const PATENTSVIEW_BASE = "https://search.patentsview.org/api/v1/patent";

/**
 * Safety cap: never fetch more than this many patents in a single
 * `searchPatents` call, even if PatentsView reports a higher total.
 * This prevents a runaway loop from exhausting the API quota. 1000
 * patents is more than enough for any drug's IP due diligence
 * (aspirin, the most-patented drug, has ~500).
 */
const MAX_PATENTS_PER_SEARCH = 1000;
const PATENTSVIEW_PAGE_SIZE = 100;

export interface PatentRecord {
  patentNumber: string;
  title: string;
  abstract: string;
  grantDate: string;
  inventors: string[];
  assignees: string[];
  cpcLabels: string[];
  url: string;
}

export interface PatentSearchResponse {
  /** True total matching patents reported by PatentsView (may exceed patents.length). */
  total: number;
  /** Patents actually fetched (capped at MAX_PATENTS_PER_SEARCH). */
  patents: PatentRecord[];
  /** FE-026: whether pagination was applied. */
  paginated: boolean;
  /** FE-026: number of pages fetched. */
  pagesFetched: number;
  reason?: string;
}

interface PatentsViewRawPatent {
  patent_number?: string;
  patent_title?: string;
  patent_abstract?: string;
  patent_date?: string;
  inventors?: Array<{ inventor_name?: string }>;
  assignees?: Array<{ assignee_organization?: string }>;
  cpc_current?: Array<{ cpc_subsection_id?: string }>;
}

interface PatentsViewResponse {
  patents?: PatentsViewRawPatent[];
  total_hits?: number;
}

function mapRawPatent(p: PatentsViewRawPatent): PatentRecord {
  return {
    patentNumber: p.patent_number || "",
    title: p.patent_title || "",
    abstract: (p.patent_abstract || "").slice(0, 500),
    grantDate: p.patent_date || "",
    inventors: (p.inventors || [])
      .map((i) => i.inventor_name)
      .filter((x): x is string => Boolean(x)),
    assignees: (p.assignees || [])
      .map((a) => a.assignee_organization)
      .filter((x): x is string => Boolean(x)),
    cpcLabels: (p.cpc_current || [])
      .map((c) => c.cpc_subsection_id)
      .filter((x): x is string => Boolean(x)),
    url: p.patent_number
      ? `https://patents.google.com/patent/US${p.patent_number}`
      : "",
  };
}

function buildRequestBody(query: string, size: number, offset: number) {
  return {
    q: {
      _text: {
        _in: [
          { _text_phrase: { patent_title: query } },
          { _text_phrase: { patent_abstract: query } },
        ],
      },
    },
    f: [
      "patent_number",
      "patent_title",
      "patent_abstract",
      "patent_date",
      "inventors.inventor_name",
      "assignees.assignee_organization",
      "cpc_current.cpc_subsection_id",
    ],
    o: { size, offset, perms: {} },
    s: [{ patent_date: "desc" }],
  };
}

async function fetchPatentsPage(
  query: string,
  size: number,
  offset: number
): Promise<{ patents: PatentRecord[]; totalHits: number; ok: boolean; status: number }> {
  // Task 260: monitored for observability — every PatentsView call is
  // logged with URL, duration, and status so operators can detect slow
  // or degraded upstream responses (and 401s from an expired API key).
  const res = await monitoredFetch("patentsview", PATENTSVIEW_BASE, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Api-Key": process.env.PATENTSVIEW_API_KEY || "",
    },
    body: JSON.stringify(buildRequestBody(query, size, offset)),
    next: { revalidate: 86400 },
  });
  if (!res.ok) {
    return { patents: [], totalHits: 0, ok: false, status: res.status };
  }
  const data: PatentsViewResponse = await res.json();
  const patents = (data.patents || []).map(mapRawPatent);
  return {
    patents,
    totalHits: typeof data.total_hits === "number" ? data.total_hits : patents.length,
    ok: true,
    status: res.status,
  };
}

export async function searchPatents(params: {
  query: string;
  limit?: number;
}): Promise<PatentSearchResponse> {
  const q = (params.query || "").trim();
  if (q.length < 2) {
    return {
      total: 0,
      patents: [],
      paginated: false,
      pagesFetched: 0,
    };
  }

  if (!process.env.PATENTSVIEW_API_KEY) {
    return {
      total: 0,
      patents: [],
      paginated: false,
      pagesFetched: 0,
      reason:
        "PATENTSVIEW_API_KEY not configured. Patent search requires a free PatentsView API key " +
        "(see https://patentsview.org/apis/keyrequest). No mock data is returned.",
    };
  }

  const requestedLimit = params.limit;
  const wantsAll = requestedLimit === undefined || requestedLimit > PATENTSVIEW_PAGE_SIZE;

  // Single-page fast path: caller asked for ≤ 100 patents.
  if (!wantsAll) {
    const limit = Math.min(requestedLimit!, PATENTSVIEW_PAGE_SIZE);
    const page = await fetchPatentsPage(q, limit, 0);
    if (!page.ok) {
      return {
        total: 0,
        patents: [],
        paginated: false,
        pagesFetched: 0,
        reason: `PatentsView returned ${page.status}. The patent search service may be temporarily unavailable.`,
      };
    }
    return {
      total: page.totalHits,
      patents: page.patents,
      paginated: false,
      pagesFetched: 1,
    };
  }

  // FE-026 ROOT FIX: paginated path. Loop pages until we've collected
  // all results OR hit the safety cap. PatentsView's `total_hits`
  // tells us the true total; we keep paging until our accumulated
  // count reaches it (or we hit MAX_PATENTS_PER_SEARCH).
  const allPatents: PatentRecord[] = [];
  let totalHits = 0;
  let pagesFetched = 0;
  let offset = 0;

  while (allPatents.length < MAX_PATENTS_PER_SEARCH) {
    const remaining = MAX_PATENTS_PER_SEARCH - allPatents.length;
    const size = Math.min(PATENTSVIEW_PAGE_SIZE, remaining);
    const page = await fetchPatentsPage(q, size, offset);
    pagesFetched++;

    if (!page.ok) {
      // If the first page fails, return the error. If a later page
      // fails, return what we have so far (partial result is better
      // than none) with a reason.
      if (pagesFetched === 1) {
        return {
          total: 0,
          patents: [],
          paginated: false,
          pagesFetched,
          reason: `PatentsView returned ${page.status}. The patent search service may be temporarily unavailable.`,
        };
      }
      return {
        total: totalHits,
        patents: allPatents,
        paginated: true,
        pagesFetched,
        reason: `PatentsView returned ${page.status} on page ${pagesFetched}. Returning partial results (${allPatents.length} of ${totalHits}).`,
      };
    }

    totalHits = page.totalHits;
    allPatents.push(...page.patents);
    offset += page.patents.length;

    // Stop if we've collected everything OR this page returned fewer
    // than `size` patents (meaning we've hit the last page).
    if (page.patents.length < size) break;
    if (allPatents.length >= totalHits) break;
  }

  return {
    total: totalHits,
    patents: allPatents,
    paginated: pagesFetched > 1,
    pagesFetched,
  };
}
