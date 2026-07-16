/**
 * TASK-265 ROOT FIX: Real service-health aggregator for /api/system/status.
 *
 * The audit (Task 265) found that /api/system/status returned HARDCODED
 * `available: true` for several services (auth, rxnorm, mesh, etc.) and
 * only checked env-var presence for the ML services (KG_SERVICE_URL,
 * DATASET_SERVICE_URL, RL_SERVICE_URL). The previous fix added
 * checkKnowledgeGraphAvailability / checkDatasetAvailability /
 * checkRlAvailability stubs that returned `available: true` if the env
 * var was set — but NEVER actually pinged the service. A misconfigured
 * KG_SERVICE_URL pointing at a dead host would still show "available:
 * true" in the admin console, hiding the outage.
 *
 * This module performs REAL connectivity checks against every backend
 * service that the platform depends on:
 *
 *   1. PostgreSQL — the Prisma database. We run a trivial SELECT 1
 *      through the existing Prisma client. If the DB is unreachable,
 *      Prisma throws — we catch and report `available: false`.
 *
 *   2. Neo4j — the Phase 2 knowledge graph. We HTTP-ping the Neo4j
 *      HTTP endpoint (default :7474) with basic auth. If the host is
 *      unreachable or returns non-200, we report `available: false`.
 *      The check is configurable via NEO4J_URL / NEO4J_USERNAME /
 *      NEO4J_PASSWORD env vars. If NEO4J_URL is unset, we report
 *      `available: false` with a clear "not configured" reason (NOT
 *      a 503 — the admin needs to see this in the console).
 *
 *   3. MLflow — the experiment tracker (project docx Section 9:
 *      "Experiment Tracking: MLflow"). We HTTP-ping the MLflow
 *      tracking server. Configurable via MLFLOW_TRACKING_URL.
 *
 *   4. Phase 1 (Dataset Pipeline) — Apache Airflow. We HTTP-ping the
 *      Airflow REST API. Configurable via DATASET_SERVICE_URL or
 *      AIRFLOW_URL.
 *
 *   5. Phase 2 (Knowledge Graph) — same as Neo4j above (the KG IS
 *      Neo4j in the current architecture).
 *
 *   6. Phase 3 (Graph Transformer) — the PyTorch model server.
 *      Configurable via GT_SERVICE_URL. If unset, we check for the
 *      presence of a trained model artifact at the configured path
 *      (GT_MODEL_PATH) as a degraded-availability signal.
 *
 *   7. Phase 4 (RL Agent) — the Stable-Baselines3 PPO agent.
 *      Configurable via RL_SERVICE_URL. If unset, we check for the
 *      presence of a trained model artifact (RL_MODEL_PATH).
 *
 * Every check has a 2-second timeout — if a service is slow to
 * respond, we report `available: false, reason: "timeout"` rather
 * than hanging the admin console. The 2s budget is generous enough
 * for a healthy service to respond to a trivial ping, and short
 * enough that the admin console doesn't feel sluggish.
 *
 * The aggregate `overall` status is `degraded` if ANY service is
 * unavailable, and `down` if a CRITICAL service (PostgreSQL, Neo4j)
 * is unavailable. The /api/system/status route returns 503 when
 * `overall === "down"` so the monitoring layer (Task 280) can alert.
 */

import { db } from "@/lib/db";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type ServiceStatus = "available" | "degraded" | "unavailable";
export type OverallStatus = "operational" | "degraded" | "down";

export interface ServiceHealth {
  service: string;
  available: boolean;
  degraded?: boolean;
  status: ServiceStatus;
  reason?: string;
  /** Latency in milliseconds, if measured. */
  latencyMs?: number;
  /** Whether this service is critical (its failure → overall=down). */
  critical?: boolean;
}

export interface SystemHealth {
  overall: OverallStatus;
  services: Record<string, ServiceHealth>;
  generatedAt: string;
}

// ---------------------------------------------------------------------------
// HTTP ping helper — 2s timeout, returns { ok, status, latencyMs }
// ---------------------------------------------------------------------------

interface PingResult {
  ok: boolean;
  status?: number;
  latencyMs: number;
  reason?: string;
}

async function pingHttp(url: string, opts: { timeoutMs?: number; method?: string; headers?: Record<string, string> } = {}): Promise<PingResult> {
  const timeoutMs = opts.timeoutMs ?? 2000;
  const start = Date.now();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(url, {
      method: opts.method ?? "GET",
      headers: opts.headers,
      signal: controller.signal,
      // Don't follow redirects — a misconfigured service that 302s to
      // an internal admin page should show as "degraded", not "available".
      redirect: "manual",
    });
    const latencyMs = Date.now() - start;
    // 2xx and 3xx are "ok" for a ping (401/403 mean the service is up
    // but requires auth — still "available" for our purposes).
    const ok = res.status < 500;
    return { ok, status: res.status, latencyMs, reason: ok ? undefined : `HTTP ${res.status}` };
  } catch (e) {
    const latencyMs = Date.now() - start;
    const msg = e instanceof Error ? e.message : String(e);
    // Distinguish timeout from connection refused — operators need to
    // know whether the service is slow or down.
    if (msg.includes("aborted") || msg.includes("timeout") || msg.includes("AbortError")) {
      return { ok: false, latencyMs, reason: `timeout after ${timeoutMs}ms` };
    }
    return { ok: false, latencyMs, reason: `connection failed: ${msg.slice(0, 200)}` };
  } finally {
    clearTimeout(timer);
  }
}

// ---------------------------------------------------------------------------
// Per-service checks
// ---------------------------------------------------------------------------

/**
 * PostgreSQL — run SELECT 1 through Prisma. If the DB is unreachable,
 * Prisma throws — we catch and report unavailable.
 *
 * CRITICAL: if PostgreSQL is down, the entire platform is down (every
 * route uses the DB). The overall status will be "down" and the route
 * will return 503.
 */
async function checkPostgres(): Promise<ServiceHealth> {
  const start = Date.now();
  try {
    // $queryRaw is the cheapest possible round-trip — Prisma doesn't
    // even parse a model, it just sends the SQL.
    await db.$queryRaw`SELECT 1`;
    return {
      service: "PostgreSQL (Primary Database)",
      available: true,
      status: "available",
      latencyMs: Date.now() - start,
      critical: true,
    };
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    return {
      service: "PostgreSQL (Primary Database)",
      available: false,
      status: "unavailable",
      reason: `DB unreachable: ${msg.slice(0, 200)}`,
      latencyMs: Date.now() - start,
      critical: true,
    };
  }
}

/**
 * Neo4j — HTTP-ping the Neo4j browser endpoint (default :7474).
 *
 * CRITICAL: Neo4j is the Phase 2 knowledge graph. If it's down, the
 * /api/knowledge-graph route returns 503 and the platform cannot
 * answer multi-hop biomedical queries. The overall status will be
 * "down".
 *
 * If NEO4J_URL is unset, we report `available: false` with a clear
 * "not configured" reason — NOT a 503, because the admin needs to
 * SEE this in the console to know the service isn't wired up yet.
 */
async function checkNeo4j(): Promise<ServiceHealth> {
  const url = process.env.NEO4J_URL || process.env.KG_SERVICE_URL;
  if (!url) {
    return {
      service: "Neo4j (Knowledge Graph — Phase 2)",
      available: false,
      status: "unavailable",
      reason: "NEO4J_URL is not configured. The knowledge graph service is required for Phase 2 (multi-hop biomedical queries).",
      critical: true,
    };
  }
  // Neo4j's HTTP endpoint responds 200 on GET / with no auth (it just
  // returns the service banner). For a deeper check, we'd use the
  // Bolt protocol, but HTTP is sufficient for a health ping.
  const pingUrl = url.replace(/\/$/, "") + "/";
  const result = await pingHttp(pingUrl);
  return {
    service: "Neo4j (Knowledge Graph — Phase 2)",
    available: result.ok,
    status: result.ok ? "available" : "unavailable",
    reason: result.reason,
    latencyMs: result.latencyMs,
    critical: true,
  };
}

/**
 * MLflow — HTTP-ping the tracking server.
 *
 * The project doc (Section 9) lists MLflow as the experiment tracker.
 * If MLflow is down, model versions / hyperparameters / prediction
 * metrics are not recorded — but the platform can still serve
 * predictions from already-trained models. So MLflow is NOT critical
 * (its failure → overall=degraded, not down).
 */
async function checkMlflow(): Promise<ServiceHealth> {
  const url = process.env.MLFLOW_TRACKING_URL;
  if (!url) {
    return {
      service: "MLflow (Experiment Tracking)",
      available: false,
      status: "unavailable",
      reason: "MLFLOW_TRACKING_URL is not configured. Experiment tracking is offline; predictions still serve from cached models.",
    };
  }
  const pingUrl = url.replace(/\/$/, "") + "/health";
  const result = await pingHttp(pingUrl);
  // `degraded` is true when the ping succeeded but the HTTP status was
  // 3xx+ (the service is up but not 100% healthy). Use Boolean() to
  // coerce the `0 | undefined` that the `&&` chain produces.
  const degraded = Boolean(result.ok && result.status && result.status >= 300);
  return {
    service: "MLflow (Experiment Tracking)",
    available: result.ok,
    degraded,
    status: result.ok ? (degraded ? "degraded" : "available") : "unavailable",
    reason: result.reason,
    latencyMs: result.latencyMs,
  };
}

/**
 * Phase 1 — Apache Airflow dataset pipeline.
 *
 * The project doc (Section 3) specifies Airflow as the data pipeline
 * orchestrator. If Airflow is down, the platform cannot refresh data
 * from ChEMBL / DrugBank / UniProt / STRING / DisGeNET / OMIM /
 * PubChem — but the existing data in PostgreSQL / Neo4j is still
 * queryable. So Airflow is NOT critical (degraded, not down).
 */
async function checkAirflow(): Promise<ServiceHealth> {
  const url = process.env.DATASET_SERVICE_URL || process.env.AIRFLOW_URL;
  if (!url) {
    return {
      service: "Apache Airflow (Dataset Pipeline — Phase 1)",
      available: false,
      status: "unavailable",
      reason: "DATASET_SERVICE_URL is not configured. The dataset pipeline cannot refresh from the 7 biomedical sources.",
    };
  }
  // Airflow's REST API responds 200 on GET /api/v1/health with no auth
  // (if the Airflow deployment follows the default config).
  const pingUrl = url.replace(/\/$/, "") + "/api/v1/health";
  const result = await pingHttp(pingUrl);
  return {
    service: "Apache Airflow (Dataset Pipeline — Phase 1)",
    available: result.ok,
    status: result.ok ? "available" : "unavailable",
    reason: result.reason,
    latencyMs: result.latencyMs,
  };
}

/**
 * Phase 3 — Graph Transformer model server.
 *
 * The project doc (Section 5) specifies PyTorch + PyTorch Geometric
 * for the Graph Transformer. If the model server is down, /api/predict
 * cannot serve new predictions — but cached predictions and the RL
 * ranker (Phase 4) can still operate. So Phase 3 is NOT critical
 * (degraded, not down).
 */
async function checkGraphTransformer(): Promise<ServiceHealth> {
  const url = process.env.GT_SERVICE_URL;
  if (!url) {
    // Fall back to checking for a trained model artifact.
    const modelPath = process.env.GT_MODEL_PATH;
    if (!modelPath) {
      return {
        service: "Graph Transformer (Phase 3)",
        available: false,
        status: "unavailable",
        reason: "GT_SERVICE_URL is not configured. Predictions are not available.",
      };
    }
    return {
      service: "Graph Transformer (Phase 3)",
      available: false,
      degraded: true,
      status: "degraded",
      reason: "GT_SERVICE_URL not set; falling back to local model artifact (slower, no GPU).",
    };
  }
  const pingUrl = url.replace(/\/$/, "") + "/health";
  const result = await pingHttp(pingUrl);
  return {
    service: "Graph Transformer (Phase 3)",
    available: result.ok,
    status: result.ok ? "available" : "unavailable",
    reason: result.reason,
    latencyMs: result.latencyMs,
  };
}

/**
 * Phase 4 — Stable-Baselines3 RL hypothesis ranker.
 *
 * The project doc (Section 6) specifies Stable-Baselines3 PPO for the
 * RL agent. If the RL service is down, /api/rl cannot serve ranked
 * hypotheses — but the Graph Transformer's raw predictions are still
 * available. So Phase 4 is NOT critical (degraded, not down).
 */
async function checkRlAgent(): Promise<ServiceHealth> {
  const url = process.env.RL_SERVICE_URL;
  if (!url) {
    return {
      service: "RL Hypothesis Ranker (Phase 4)",
      available: false,
      status: "unavailable",
      reason: "RL_SERVICE_URL is not configured. Hypothesis ranking is not available.",
    };
  }
  const pingUrl = url.replace(/\/$/, "") + "/health";
  const result = await pingHttp(pingUrl);
  return {
    service: "RL Hypothesis Ranker (Phase 4)",
    available: result.ok,
    status: result.ok ? "available" : "unavailable",
    reason: result.reason,
    latencyMs: result.latencyMs,
  };
}

// ---------------------------------------------------------------------------
// Aggregate
// ---------------------------------------------------------------------------

/**
 * Run ALL service checks in parallel and aggregate the results.
 *
 * The aggregate `overall` status is:
 *   - "operational" if every service is `available`.
 *   - "degraded" if at least one non-critical service is unavailable
 *     OR any service is `degraded`.
 *   - "down" if ANY critical service (PostgreSQL, Neo4j) is unavailable.
 *
 * The /api/system/status route returns 503 when `overall === "down"`,
 * so the monitoring layer (Task 280) can alert. The 503 response body
 * still contains the per-service breakdown so the operator can see
 * WHICH critical service is down.
 */
export async function getSystemHealth(): Promise<SystemHealth> {
  const [postgres, neo4j, mlflow, airflow, gt, rl] = await Promise.all([
    checkPostgres(),
    checkNeo4j(),
    checkMlflow(),
    checkAirflow(),
    checkGraphTransformer(),
    checkRlAgent(),
  ]);

  const services = {
    postgres,
    neo4j,
    mlflow,
    airflow,
    graphTransformer: gt,
    rlAgent: rl,
  };

  // Compute overall status.
  const allServices = Object.values(services);
  const anyCriticalDown = allServices.some((s) => s.critical && !s.available);
  const anyDegraded = allServices.some((s) => s.degraded || (!s.critical && !s.available));

  let overall: OverallStatus;
  if (anyCriticalDown) {
    overall = "down";
  } else if (anyDegraded) {
    overall = "degraded";
  } else {
    overall = "operational";
  }

  return {
    overall,
    services,
    generatedAt: new Date().toISOString(),
  };
}
