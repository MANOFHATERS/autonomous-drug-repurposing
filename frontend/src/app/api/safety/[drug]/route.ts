import { NextRequest, NextResponse } from "next/server";
// Task 11.4 ROOT FIX (v129, TM11 — hostile-auditor pass):
// PRIMARY source is now SIDER via the Phase 2 KG (Neo4j). The SIDER
// service returns real adverse-event data with frequency, severity,
// and MedDRA code — exactly what the V1 launch criteria (project
// docx Section 8) requires. openFDA is kept as a SECONDARY source
// for cases where SIDER has no data (e.g., a newly-approved drug
// that SIDER has not yet indexed). The two sources are MERGED —
// the response includes both SIDER's KG-derived data AND openFDA's
// FAERS spontaneous-report counts.
import { getDrugSafetySummary } from "@/lib/services/openfda";
import { getSiderSafetySummary, type SiderSafetySummary } from "@/lib/services/sider";
import { badRequest, internalError, notFound } from "@/lib/api-helpers";
import {
  requireAuthAndRateLimitV2,
  recordApiRequestForUserV2,
} from "@/lib/auth/api-proxy-guard";
// Task 252 ROOT FIX: Zod validation for path + query params.
import { validateDrugPathParam } from "@/lib/zod-schemas";

/**
 * GET /api/safety/[drug]?limit=N
 *
 * Task 11.4 ROOT FIX (v129): the route now returns a MERGED safety
 * summary from TWO real sources:
 *
 *   1. SIDER (via the Phase 2 KG / Neo4j) — adverse-event frequencies,
 *      MedDRA codes, withdrawal reason. This is the CANONICAL source
 *      per the project docx (Section 4 — Phase 2 KG edges include
 *      "Drug → causes → Adverse Event").
 *
 *   2. openFDA (FAERS) — spontaneous-report counts and top reactions.
 *      This is the SECONDARY source — it provides real-world report
 *      counts that SIDER's package-insert frequencies do not.
 *
 * RESPONSE SHAPE:
 *   {
 *     drugName: string,
 *     sider: SiderSafetySummary | null,    // KG-derived
 *     openfda: DrugSafetySummary | null,   // FAERS spontaneous reports
 *     sources: ("sider_neo4j" | "openfda_fae rs")[],  // which sources contributed
 *     disclaimer: string,                  // merged disclaimer
 *   }
 *
 * The route ALWAYS returns real data from at least one source. If both
 * sources return null (drug not in KG AND not in openFDA), it returns
 * a 404 with a clear message — NEVER a hardcoded table.
 *
 * SCIENTIFIC INTEGRITY: the merged disclaimer explains the difference
 * between SIDER frequencies (package-insert disclosures) and openFDA
 * report counts (spontaneous FAERS reports). Both are real data, but
 * they answer different questions — a researcher needs both to make
 * an informed safety assessment.
 *
 * VERIFICATION (per task spec): for a withdrawn drug (e.g.,
 * "rosiglitazone" — withdrawn in the EU for cardiovascular risk),
 * the response MUST include `sider.withdrawal.isWithdrawn: true` and
 * `sider.withdrawal.reason`. The contract test in
 * `__tests__/sider-contract.test.ts` verifies this.
 */
export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ drug: string }> }
) {
  const { drug: rawDrug } = await params;

  // Task 252: validate the drug path param FIRST — invalid input gets
  // a 400 without wasting an auth check.
  const drug = validateDrugPathParam(rawDrug);
  if (!drug) {
    return badRequest(
      "Drug name parameter (2-64 chars, alphanumeric + space/comma/period/apostrophe/hyphen) is required"
    );
  }

  // Task 253: 5 req/sec per-user rate limit (V2 guard).
  const guard = await requireAuthAndRateLimitV2(req);
  if (guard.response !== null) return guard.response;

  try {
    // Fan out BOTH queries in parallel — they hit different services
    // (Phase 2 KG / Neo4j vs api.fda.gov) with no shared dependency.
    // A slow SIDER query does NOT block the openFDA response and vice
    // versa. If one fails, we still return the other (partial result
    // is better than a 500).
    const [siderResult, openfdaResult] = await Promise.allSettled([
      getSiderSafetySummary(drug),
      getDrugSafetySummary(drug),
    ]);
    recordApiRequestForUserV2(guard.user);

    const sider =
      siderResult.status === "fulfilled" ? siderResult.value : null;
    const openfda =
      openfdaResult.status === "fulfilled" ? openfdaResult.value : null;

    // Log any rejected promises so operators can see WHY a source is
    // missing — but do NOT propagate the error to the user. The safety
    // endpoint must ALWAYS return real data from at least one source.
    if (siderResult.status === "rejected") {
      console.error(
        "[SAFETY] SIDER query failed — falling back to openFDA only:",
        siderResult.reason instanceof Error
          ? siderResult.reason.message
          : String(siderResult.reason),
      );
    }
    if (openfdaResult.status === "rejected") {
      console.error(
        "[SAFETY] openFDA query failed — returning SIDER only:",
        openfdaResult.reason instanceof Error
          ? openfdaResult.reason.message
          : String(openfdaResult.reason),
      );
    }

    // If BOTH sources returned null, the drug is unknown — return 404.
    if (!sider && !openfda) {
      return notFound(
        `No safety data available for "${drug}". The drug name does not match ` +
          `any SIDER entry in the Knowledge Graph or any openFDA record.`,
      );
    }

    // Build the merged response. The `sources` array tells the caller
    // which sources contributed (a researcher can see at a glance
    // whether the data is KG-derived, FAERS-derived, or both).
    const sources: string[] = [];
    if (sider) sources.push("sider_neo4j");
    if (openfda) sources.push("openfda_faers");

    const mergedDisclaimer =
      "Safety data is merged from two real sources:\n" +
      "1. SIDER (via the Phase 2 Knowledge Graph / Neo4j) — adverse-event " +
      "frequencies from drug package inserts.\n" +
      "2. openFDA (FAERS) — spontaneous adverse-event report counts from " +
      "the FDA Adverse Event Reporting System.\n" +
      "Neither source proves causation. SIDER frequencies are labeling " +
      "disclosures; FAERS reports are spontaneous and may be incomplete. " +
      "Always cross-reference with clinical trial data before making " +
      "clinical decisions.";

    return NextResponse.json({
      drugName: drug,
      sider: sider as SiderSafetySummary | null,
      openfda,
      sources,
      disclaimer: mergedDisclaimer,
    });
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e);
    return internalError(`Safety lookup failed: ${msg}`);
  }
}
