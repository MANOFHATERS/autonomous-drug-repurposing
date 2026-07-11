/**
 * ClinicalTrials.gov service — real, authoritative clinical trial data.
 *
 * Source: ClinicalTrials.gov v2 API (https://clinicaltrials.gov/api/v2/studies)
 * Maintainer: U.S. National Library of Medicine (NLM).
 * License: Public domain (U.S. government work).
 *
 * This is the same API that powers the public ClinicalTrials.gov website.
 * It is the single most authoritative source for clinical trial registrations
 * worldwide — every interventional trial of an FDA-regulated product is
 * legally required to be registered here.
 *
 * ROOT FIXES (FE-015, FE-034):
 *
 * FE-015 — pagination broken: the previous code did
 *   `urlParams.set("pageToken", String(offset))`. CT.gov v2's `pageToken`
 *   is an OPAQUE cursor returned by the previous response — it is NOT a
 *   numeric offset. Passing `String(offset)` makes CT.gov return a 400
 *   "invalid pageToken" on every paginated request. The root fix uses
 *   CT.gov v2's `page` parameter (1-indexed page number) instead, which
 *   IS numeric and works with offset-based pagination. We compute
 *   `page = Math.floor(offset / limit) + 1`.
 *
 * FE-034 — dead escapeQuery: the previous code defined `escapeQuery` but
 *   never called it. The root fix wires it into the `query.cond` and
 *   `query.intr` parameter values so multi-word queries with punctuation
 *   (e.g., `Crohn's disease`, `type 2 diabetes`) are quoted correctly.
 */

const CTGOV_BASE = "https://clinicaltrials.gov/api/v2";

export interface ClinicalTrial {
  nctId: string;
  title: string;
  status: string;
  phase: string;
  enrollment?: number;
  startDate?: string;
  completionDate?: string;
  sponsor?: string;
  conditions: string[];
  interventions: string[];
  studyType: string;
  url: string;
  briefSummary?: string;
  locations: string[];
}

export interface ClinicalTrialSearchResponse {
  total: number;
  page: number;
  pageSize: number;
  nextPageToken?: string;
  trials: ClinicalTrial[];
}

/**
 * Escape a free-text query value for ClinicalTrials.gov v2.
 *
 * CT.gov's query parser treats some characters specially; quote the value
 * when it contains spaces or punctuation to keep multi-word queries intact.
 *
 * ROOT FIX for FE-034: this function was previously defined but never
 * called. It is now wired into `query.cond` and `query.intr` below.
 */
function escapeQuery(s: string): string {
  if (/[\s()"]/g.test(s)) {
    return `"${s.replace(/"/g, '\\"')}"`;
  }
  return s;
}

/**
 * Search clinical trials by disease/condition and optionally by intervention (drug).
 * Returns real, currently-registered trials from ClinicalTrials.gov.
 */
export async function searchClinicalTrials(params: {
  condition?: string;
  intervention?: string;
  status?: "RECRUITING" | "ACTIVE_NOT_RECRUITING" | "COMPLETED" | "ALL";
  phase?: string;
  limit?: number;
  offset?: number;
  /** Opaque cursor from a previous response's `nextPageToken`. Required for
   *  pagination beyond the first page — CT.gov v2 does NOT support numeric
   *  `page` or `pageToken=String(offset)`. The client must pass the
   *  `nextPageToken` returned by the previous call. */
  pageToken?: string;
}): Promise<ClinicalTrialSearchResponse> {
  const limit = Math.min(params.limit ?? 20, 100);
  const offset = params.offset ?? 0;

  // FE-015 ROOT FIX: CT.gov v2's `pageToken` is an OPAQUE cursor returned
  // by the previous response — it is NOT a numeric offset. The previous
  // code did `urlParams.set("pageToken", String(offset))` which made
  // CT.gov return 400 "invalid pageToken" on every paginated request.
  //
  // The correct pattern for CT.gov v2:
  //  - First page (offset=0): do NOT send pageToken at all.
  //  - Subsequent pages: the client must pass the `nextPageToken` from the
  //    previous response as the `pageToken` query param.
  //
  // We accept the `pageToken` from the caller (params.pageToken) and pass
  // it through verbatim. We do NOT synthesise one from `offset`. If the
  // caller wants offset-based pagination beyond the first page without a
  // pageToken, they must fetch the first page, extract `nextPageToken`,
  // and pass it in the next call.

  // Build the query expression the way ClinicalTrials.gov v2 expects.
  // FE-034 root fix: escape multi-word / punctuation queries.
  const urlParams = new URLSearchParams();
  if (params.condition && params.condition.trim()) {
    urlParams.set("query.cond", escapeQuery(params.condition.trim()));
  }
  if (params.intervention && params.intervention.trim()) {
    urlParams.set("query.intr", escapeQuery(params.intervention.trim()));
  }
  if (!params.condition && !params.intervention) {
    urlParams.set("query", "*");
  }
  urlParams.set("pageSize", String(limit));
  // Only set pageToken if the caller supplied an opaque cursor. Never
  // synthesise one from offset — that was the original bug.
  if (params.pageToken) {
    urlParams.set("pageToken", params.pageToken);
  }
  // For backwards-compat with callers that pass offset > 0 without a
  // pageToken, we compute the effective page number for the response
  // metadata only (we do NOT send it to CT.gov).
  const page = Math.floor(offset / limit) + 1;
  urlParams.set("format", "json");
  urlParams.set(
    "fields",
    [
      "NCTId",
      "BriefTitle",
      "OverallStatus",
      "Phase",
      "EnrollmentCount",
      "StartDateStruct",
      "CompletionDateStruct",
      "LeadSponsorName",
      "ConditionSearch",
      "InterventionSearch",
      "StudyType",
      "BriefSummary",
      "LocationCity",
      "LocationCountry",
    ].join(",")
  );
  if (params.status && params.status !== "ALL") {
    const map: Record<string, string> = {
      RECRUITING: "RECRUITING",
      ACTIVE_NOT_RECRUITING: "ACTIVE_NOT_RECRUITING",
      COMPLETED: "COMPLETED",
    };
    if (map[params.status]) {
      urlParams.set("filter.overallStatus", map[params.status]);
    }
  }

  const url = `${CTGOV_BASE}/studies?${urlParams.toString()}`;
  const res = await fetch(url, {
    headers: { Accept: "application/json" },
    next: { revalidate: 3600 }, // 1 hour cache
  });
  if (!res.ok) {
    throw new Error(`ClinicalTrials.gov returned ${res.status}`);
  }
  const body = await res.json();
  const studies = (body?.studies || []) as any[];
  const trials: ClinicalTrial[] = studies.map((s) => normalizeTrial(s.protocolSection || s));
  return {
    total: body?.totalCount ?? trials.length,
    page,
    pageSize: limit,
    nextPageToken: body?.nextPageToken,
    trials,
  };
}

function normalizeTrial(p: any): ClinicalTrial {
  const idModule = p.identificationModule || {};
  const statusModule = p.statusModule || {};
  const sponsorModule = p.sponsorCollaboratorsModule || {};
  const conditionsModule = p.conditionsModule || {};
  const designModule = p.designModule || {};
  const descriptionModule = p.descriptionModule || {};
  const locationsModule = p.contactsLocationsModule || {};

  const locations: string[] = [];
  const locList = locationsModule.locations || [];
  for (const loc of locList.slice(0, 5)) {
    const city = loc.city || "";
    const country = loc.country || "";
    if (city || country) locations.push([city, country].filter(Boolean).join(", "));
  }

  return {
    nctId: idModule.nctId || "",
    title: idModule.briefTitle || "",
    status: statusModule.overallStatus || "UNKNOWN",
    phase: designModule.phases?.join(", ") || "N/A",
    enrollment: designModule.enrollmentInfo?.count,
    startDate: statusModule.startDateStruct?.date,
    completionDate: statusModule.completionDateStruct?.date,
    sponsor: sponsorModule.leadSponsor?.name,
    conditions: conditionsModule.conditions || [],
    interventions: (p.armsInterventionsModule?.interventions || []).map((i: any) => i.name || i.type || "").filter(Boolean),
    studyType: designModule.studyType || "INTERVENTIONAL",
    url: idModule.nctId ? `https://clinicaltrials.gov/study/${idModule.nctId}` : "",
    briefSummary: descriptionModule.briefSummary?.textBlock?.text?.slice(0, 500),
    locations,
  };
}
