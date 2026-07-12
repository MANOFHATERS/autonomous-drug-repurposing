import { NextResponse } from "next/server";
import { requireAdmin } from "@/lib/api-helpers";
import {
  checkKnowledgeGraphAvailability,
  checkDatasetAvailability,
  checkRlAvailability,
  type MlServiceAvailability,
} from "@/lib/services/ml-stubs";
import { isOpenfdaApiKeyConfigured } from "@/lib/services/openfda";

/**
 * GET /api/system/status
 *
 * FE-007 ROOT FIX: This endpoint previously required NO authentication and
 * leaked internal service configuration. It returned `available: true/false`
 * for every backend service with `reason` strings that often included env
 * var names ("PATENTSVIEW_API_KEY not configured"). An attacker could probe
 * this endpoint to map the platform's attack surface and read env var names
 * to know what to phish for.
 *
 * Fix:
 *   1. requireAdmin() — only admin / owner roles can read service status.
 *   2. Defensive env-var-name scrubbing at the route layer — even if a
 *      future service stub forgets the rule, the route redacts anything
 *      matching `[A-Z_]{3,}_API_KEY` or `_SERVICE_URL` or `_URL` etc.
 *   3. No URL values are echoed back.
 */

// Matches common env-var name patterns: ALL_CAPS_WITH_UNDERSCORES, at least
// 4 chars, ending in _KEY, _URL, _TOKEN, _SECRET, or _ID. We redact the
// match so an attacker cannot infer which env vars to phish for.
const ENV_VAR_PATTERN = /\b[A-Z][A-Z0-9_]{3,}(?:_API_KEY|_KEY|_URL|_TOKEN|_SECRET|_ID|_PASSWORD)\b/g;

function scrubReason(reason: string | undefined): string | undefined {
  if (!reason) return reason;
  return reason.replace(ENV_VAR_PATTERN, "[REDACTED]");
}

function scrubService(svc: MlServiceAvailability): MlServiceAvailability {
  return {
    ...svc,
    reason: scrubReason(svc.reason) || "",
  };
}

export async function GET() {
  // FE-007 ROOT FIX: require admin (or owner) auth. The status endpoint is
  // an operational surface — it should not be world-readable.
  const auth = await requireAdmin();
  if (auth.user === null) return auth.response;

  const patentsviewAvailable = !!process.env.PATENTSVIEW_API_KEY;
  // FE-024: openFDA is technically reachable without a key, but the
  // shared public rate limit (240 req/min) is too slow for a pharma
  // partner demo. Report `degraded` when the key is missing so the
  // admin dashboard surfaces the misconfiguration.
  const openfdaKeyPresent = isOpenfdaApiKeyConfigured();

  return NextResponse.json({
    services: {
      auth: { available: true, service: "Authentication" },
      rxnorm: { available: true, service: "RxNorm Drug Search" },
      mesh: { available: true, service: "MeSH Disease Search" },
      clinicalTrials: { available: true, service: "ClinicalTrials.gov Search" },
      pubmed: { available: true, service: "PubMed Literature Search" },
      openfda: {
        // FE-024 ROOT FIX: report `degraded` (not `available: true`)
        // when the API key is missing. The endpoint still works, but
        // at 240 req/min shared — too slow for demos.
        available: openfdaKeyPresent,
        degraded: !openfdaKeyPresent,
        service: "openFDA Adverse Events",
        reason: openfdaKeyPresent
          ? undefined
          : "API key not configured. Service is reachable but rate-limited to 240 req/min shared across all unauthenticated callers. Set OPENFDA_API_KEY to raise to 120,000 req/min.",
      },
      patentsview: {
        available: patentsviewAvailable,
        service: "USPTO Patent Search",
        // FE-007 ROOT FIX: do NOT echo the env var name back. Generic
        // "service not configured" is all an operator needs.
        reason: patentsviewAvailable
          ? undefined
          : "Service not configured.",
      },
      projects: { available: true, service: "Projects & Collaboration" },
      billing: { available: true, service: "Billing & Subscriptions" },
      admin: { available: true, service: "Admin Console" },
      apiKeys: { available: true, service: "Developer API Keys" },
      evidence: { available: true, service: "Evidence Packages" },
      // FE-007 ROOT FIX: defensive scrubbing — even though ml-stubs no longer
      // emits env var names, this is belt-and-suspenders for future additions.
      knowledgeGraph: scrubService(checkKnowledgeGraphAvailability()),
      dataset: scrubService(checkDatasetAvailability()),
      rl: scrubService(checkRlAvailability()),
    },
    generatedAt: new Date().toISOString(),
  });
}
