/**
 * ML service availability facade — Knowledge Graph, Dataset Pipeline, RL Ranker.
 *
 * FE-009 ROOT FIX (Teammate 14, HIGH) — replaces the previous stub module.
 *
 * ROOT CAUSE (forensic audit):
 *   The previous version of this file was named `ml-stubs.ts` and exported
 *   three synchronous functions: `checkKnowledgeGraphAvailability`,
 *   `checkDatasetAvailability`, `checkRlAvailability`. Each function did
 *   ONE thing: check whether an env var was set. If `KG_SERVICE_URL` was
 *   set, it returned `{ available: true, reason: "Knowledge graph service
 *   is configured and reachable." }` — a LIE. The function never pinged
 *   the service. A misconfigured KG_SERVICE_URL pointing at a dead host
 *   would still show "available: true" in the admin console, hiding the
 *   outage from operators.
 *
 *   Meanwhile, the codebase already had REAL health-check functions that
 *   DO ping the services:
 *     - `checkKgHealth()` in kg-service.ts (HTTP ping to /api/kg/health)
 *     - `checkDatasetHealth()` in dataset-service.ts (HTTP ping to /api/datasets/stats)
 *     - `checkRlHealth()` in rl-ranker.ts (HTTP ping to {RL_SERVICE_URL}/health)
 *
 *   The /api/system/status route was using BOTH — the stubs for the
 *   legacy `services.{knowledgeGraph,dataset,rl}` keys, and the real
 *   `getSystemHealth()` for the `health` object. The two paths could
 *   disagree (stubs say "available", real health says "unreachable"),
 *   leaving operators confused about which to believe.
 *
 * ROOT FIX:
 *   1. Rename the module's purpose from "stubs" to "availability facade".
 *      The file is still named `ml-stubs.ts` for backward-compat with
 *      the existing import in /api/system/status/route.ts — but the
 *      exported functions now delegate to the REAL health checks. The
 *      `ML_SERVICE_STATUS` metadata object is preserved (operators use
 *      it to display service descriptions in the admin console).
 *
 *   2. The three check*Availability functions are now ASYNC and return
 *      `available` based on the REAL health-check result. They never
 *      fabricate "available: true" — if the service is unreachable, they
 *      return `available: false` with a clear reason.
 *
 *   3. The /api/system/status route is updated to await these calls.
 *
 * SCIENTIFIC INTEGRITY: an "available: true" signal in the admin console
 * must mean the service is actually reachable and responding. Anything
 * less is a false positive that hides outages from operators. On a
 * patient-safety platform, a hidden outage can mean a researcher runs a
 * hypothesis query that silently returns stale data — they believe the
 * KG confirmed the drug-disease edge when in fact the KG was down and
 * the request fell back to a cached response.
 */

import { checkKgHealth } from "@/lib/services/kg-service";
import { checkDatasetHealth } from "@/lib/services/dataset-service";
import { checkRlHealth } from "@/lib/services/rl-ranker";

export const ML_SERVICE_STATUS = {
  knowledgeGraph: {
    name: "Knowledge Graph Service",
    description:
      "Neo4j-backed multi-modal biomedical knowledge graph (drugs, proteins, " +
      "pathways, diseases, outcomes). Owned by Phase 2 of the build plan.",
    envVar: "KG_SERVICE_URL",
    deployedAt: null as Date | null,
  },
  dataset: {
    name: "Dataset Pipeline Service",
    description:
      "Apache Airflow ETL pipeline ingesting from ChEMBL, DrugBank, UniProt, " +
      "STRING, DisGeNET, OMIM, and PubChem. Owned by Phase 1 of the build plan.",
    // Issue 233 ROOT FIX: canonical env var is PHASE1_SERVICE_URL (matches
    // the naming convention of GT_SERVICE_URL / RL_SERVICE_URL / KG_SERVICE_URL).
    // DATASET_SERVICE_URL is honored as a legacy alias by dataset-service.ts.
    envVar: "PHASE1_SERVICE_URL",
    deployedAt: null as Date | null,
  },
  rl: {
    name: "RL Hypothesis Ranker",
    description:
      "Reinforcement learning agent (Stable-Baselines3 PPO) that ranks " +
      "drug-disease repurposing hypotheses by plausibility, safety, and " +
      "market opportunity. Owned by Phase 4 of the build plan.",
    envVar: "RL_SERVICE_URL",
    deployedAt: null as Date | null,
  },
} as const;

export interface MlServiceAvailability {
  available: boolean;
  service: string;
  description: string;
  reason: string;
}

/**
 * Check whether the Knowledge Graph (Phase 2) is ACTUALLY reachable.
 *
 * FE-009 ROOT FIX: this function used to return `available: true` if
 * `KG_SERVICE_URL` was set, without pinging the service. Now it delegates
 * to `checkKgHealth()` in kg-service.ts, which HTTP-pings /api/kg/health
 * (a Next.js API route that proxies to the backend FastAPI service, which
 * proxies to the Phase 2 KG service). The `available` flag is true ONLY
 * if the health check returns `reachable: true`.
 *
 * The `configured` flag from the health check is reflected in the reason
 * string — if the env var is not set, the reason says "not configured";
 * if the env var is set but the service is unreachable, the reason says
 * "configured but unreachable". This lets the operator distinguish the
 * two failure modes from the admin console.
 */
export async function checkKnowledgeGraphAvailability(): Promise<MlServiceAvailability> {
  const health = await checkKgHealth();
  if (health.configured && health.reachable) {
    return {
      available: true,
      service: ML_SERVICE_STATUS.knowledgeGraph.name,
      description: ML_SERVICE_STATUS.knowledgeGraph.description,
      // FE-007 ROOT FIX: do NOT echo the URL back — it may contain internal
      // hostnames or ports that aid reconnaissance. The version is safe to
      // surface (it's a public, non-sensitive identifier).
      reason: health.version
        ? `Knowledge graph service is reachable (version ${health.version}).`
        : "Knowledge graph service is configured and reachable.",
    };
  }
  if (health.configured && !health.reachable) {
    return {
      available: false,
      service: ML_SERVICE_STATUS.knowledgeGraph.name,
      description: ML_SERVICE_STATUS.knowledgeGraph.description,
      reason:
        "Knowledge graph service is configured (KG_SERVICE_URL is set on " +
        "the backend) but unreachable. The backend's /api/kg/health " +
        "endpoint did not return a healthy response within the timeout. " +
        "Check that the Phase 2 KG service (phase2/service.py) is running " +
        "and that Neo4j is reachable from it.",
    };
  }
  return {
    available: false,
    service: ML_SERVICE_STATUS.knowledgeGraph.name,
    description: ML_SERVICE_STATUS.knowledgeGraph.description,
    // FE-007 ROOT FIX: do NOT leak the env var name in the reason string.
    // The status endpoint is admin-only now, but defense in depth — we
    // also redact env var names at the route layer. Generic "service not
    // configured" is all an operator needs to know.
    reason:
      "The standalone Neo4j knowledge graph service has not been deployed " +
      "yet. This endpoint refuses to return fabricated graph data.",
  };
}

/**
 * Check whether the Dataset Pipeline (Phase 1) is ACTUALLY reachable.
 *
 * FE-009 ROOT FIX: this function used to return `available: true` if
 * `PHASE1_SERVICE_URL` (or the legacy `DATASET_SERVICE_URL`) was set,
 * without pinging the service. Now it delegates to `checkDatasetHealth()`
 * in dataset-service.ts, which HTTP-pings /api/datasets/stats (a Next.js
 * API route that proxies to the backend FastAPI service, which proxies
 * to the Phase 1 dataset service).
 *
 * Note: `checkDatasetHealth()` returns `configured: true` even when only
 * the Next.js /api/datasets/stats route is reachable (because the route
 * is always present in the frontend). The `reachable` flag is the
 * authoritative signal — it's true only if the route returns a 2xx
 * response with a parseable JSON body.
 */
export async function checkDatasetAvailability(): Promise<MlServiceAvailability> {
  const health = await checkDatasetHealth();
  if (health.configured && health.reachable) {
    return {
      available: true,
      service: ML_SERVICE_STATUS.dataset.name,
      description: ML_SERVICE_STATUS.dataset.description,
      reason: health.version
        ? `Dataset service is reachable (schema version ${health.version}).`
        : "Dataset service is configured and reachable.",
    };
  }
  if (health.configured && !health.reachable) {
    return {
      available: false,
      service: ML_SERVICE_STATUS.dataset.name,
      description: ML_SERVICE_STATUS.dataset.description,
      reason:
        "Dataset service is configured (PHASE1_SERVICE_URL or " +
        "DATASET_SERVICE_URL is set on the backend) but unreachable. " +
        "The backend's /api/datasets/stats endpoint did not return a " +
        "healthy response within the timeout. Check that the Phase 1 " +
        "dataset service (phase1/service.py) is running and that the " +
        "Airflow DAGs have populated the processed_data/ directory.",
    };
  }
  return {
    available: false,
    service: ML_SERVICE_STATUS.dataset.name,
    description: ML_SERVICE_STATUS.dataset.description,
    // FE-007 ROOT FIX: do NOT leak the env var name.
    reason:
      "The standalone Apache Airflow dataset pipeline has not been deployed " +
      "yet. This endpoint refuses to return fabricated dataset statistics.",
  };
}

/**
 * Check whether the RL Hypothesis Ranker (Phase 4) is ACTUALLY reachable.
 *
 * FE-009 ROOT FIX: this function used to return `available: true` if
 * `RL_SERVICE_URL` was set, without pinging the service. Now it delegates
 * to `checkRlHealth()` in rl-ranker.ts, which HTTP-pings
 * `{RL_SERVICE_URL}/health` via the shared mlFetch HTTP client (with
 * retry and timeout). The `available` flag is true ONLY if the health
 * check returns `reachable: true`.
 */
export async function checkRlAvailability(): Promise<MlServiceAvailability> {
  const health = await checkRlHealth();
  if (health.configured && health.reachable) {
    return {
      available: true,
      service: ML_SERVICE_STATUS.rl.name,
      description: ML_SERVICE_STATUS.rl.description,
      reason: health.version
        ? `RL service is reachable (version ${health.version}).`
        : "RL service is configured and reachable.",
    };
  }
  if (health.configured && !health.reachable) {
    return {
      available: false,
      service: ML_SERVICE_STATUS.rl.name,
      description: ML_SERVICE_STATUS.rl.description,
      reason:
        "RL service is configured (RL_SERVICE_URL is set) but unreachable. " +
        "The service did not return a healthy response from /health within " +
        "the timeout. Check that the Phase 4 RL service (rl/service.py) " +
        "is running on the canonical port 8003 and that the trained PPO " +
        "checkpoint is loaded.",
    };
  }
  return {
    available: false,
    service: ML_SERVICE_STATUS.rl.name,
    description: ML_SERVICE_STATUS.rl.description,
    // FE-007 ROOT FIX: do NOT leak the env var name.
    reason:
      "The standalone Stable-Baselines3 RL hypothesis ranker has not been " +
      "deployed yet. This endpoint refuses to return fabricated " +
      "repurposing predictions.",
  };
}
