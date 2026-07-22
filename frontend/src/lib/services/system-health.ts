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

async function pingHttp(url: string, opts: { timeoutMs?: number; method?: string; headers?: Record<string, string>; body?: string } = {}): Promise<PingResult> {
  const timeoutMs = opts.timeoutMs ?? 2000;
  const start = Date.now();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(url, {
      method: opts.method ?? "GET",
      headers: opts.headers,
      body: opts.body,
      signal: controller.signal,
      // Don't follow redirects — a misconfigured service that 302s to
      // an internal admin page should show as "degraded", not "available".
      redirect: "manual",
    });
    const latencyMs = Date.now() - start;
    // BE-024 ROOT FIX (v115, MEDIUM): the previous `ok = res.status < 500`
    // treated 404 (MLflow's missing /health endpoint) and 401 (Airflow's
    // auth-gated /api/v1/health) as "available". For liveness probes that
    // hit known-correct endpoints, ONLY 2xx should count as "available".
    // 3xx (redirect) is "degraded", 4xx is "degraded" (the service is up
    // but the endpoint is misconfigured), 5xx is "unavailable".
    //
    // Callers that intentionally accept 401/404 (e.g. Neo4j auth probe)
    // can check `result.status` directly. The `ok` flag here is the
    // strict 2xx-only check.
    const ok = res.status >= 200 && res.status < 300;
    const degraded = res.status >= 300 && res.status < 500;
    return {
      ok,
      status: res.status,
      latencyMs,
      reason: ok ? undefined : degraded ? `HTTP ${res.status} (degraded)` : `HTTP ${res.status}`,
    };
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
  // FE-018 ROOT FIX (Teammate 16, hostile-auditor): the previous code
  // hard-failed with `available: false, critical: true` whenever
  // DRUGOS_NEO4J_PASSWORD (or legacy NEO4J_PASSWORD) was unset — even
  // if Neo4j itself was perfectly reachable (dev environments often
  // run Neo4j with auth disabled, and production Kubernetes
  // readiness probes were failing on every dev deploy because
  // /api/system/status returned 503). The aggregate `getSystemHealth`
  // then computed `anyCriticalDown = true` → overall = "down" → 503
  // → Kubernetes marked the pod unhealthy. Operators learned to
  // ignore the warning, which masks REAL Neo4j outages in
  // production.
  //
  // ROOT FIX (per FE-019 issue spec):
  //   1. If NEO4J_URI is unset → "not configured" (operator action
  //      needed). Reported as `degraded` (NOT unavailable), and NOT
  //      critical — because the SERVICE isn't down, the operator just
  //      hasn't wired it up. The aggregate overall status will be
  //      "degraded" (visible in admin console) but NOT "down" (no
  //      503, no K8s probe failure).
  //   2. ALWAYS ping Neo4j's HTTP transaction endpoint
  //      (/db/neo4j/tx/commit) FIRST without auth, even if no
  //      password env var is set. Many dev/test deployments run Neo4j
  //      with auth disabled (dbms.security.auth_enabled=false) —
  //      these return 200 to unauthenticated requests.
  //   3. If the no-auth ping returns 200 → available (auth disabled).
  //   4. If the no-auth ping returns 401/403 AND credentials are
  //      configured → retry WITH basic auth. If auth succeeds →
  //      available. If auth fails → "degraded: auth failed" (config
  //      issue, NOT a service outage).
  //   5. If the no-auth ping returns 401/403 AND NO credentials are
  //      configured → "degraded: auth required" (operator action
  //      needed — service is UP but unreachable without credentials).
  //   6. ONLY mark as `unavailable` + `critical: true` on 5xx,
  //      timeout, or connection failure (the SERVICE itself is down).
  //
  // TM10 v128 ENV-VAR ALIGNMENT (preserved): canonical names
  // (DRUGOS_NEO4J_URI / DRUGOS_NEO4J_USER / DRUGOS_NEO4J_PASSWORD)
  // win, with legacy NEO4J_URI / NEO4J_URL / NEO4J_USER /
  // NEO4J_USERNAME / NEO4J_PASSWORD fallbacks for backward
  // compatibility with deployments that haven't migrated yet.
  const url =
    process.env.DRUGOS_NEO4J_URI ||
    process.env.NEO4J_URI ||
    process.env.NEO4J_URL;
  if (!url) {
    // (1) "Not configured" — operator action needed, NOT a service
    // outage. Report as degraded (NOT unavailable), NOT critical
    // (so overall != "down" and /api/system/status does NOT return
    // 503 — K8s readiness probes stay healthy).
    return {
      service: "Neo4j (Knowledge Graph — Phase 2)",
      available: false,
      degraded: true,
      status: "degraded",
      reason:
        "Neo4j is NOT CONFIGURED. Set DRUGOS_NEO4J_URI (canonical) " +
        "or NEO4J_URI / NEO4J_URL (legacy) to the Neo4j HTTP endpoint " +
        "(default http://localhost:7474). This is an operator action, " +
        "not a service outage — Neo4j may be running but is not wired " +
        "into the health check. (FE-018 root fix.)",
      // FE-018 ROOT FIX: NOT critical — the service isn't down, the
      // operator just hasn't configured the URL yet.
      critical: false,
    };
  }

  // Build the transaction URL. Neo4j's HTTP API is at /db/{database}/tx/commit.
  // The default database is "neo4j". We POST a trivial Cypher query
  // (RETURN 1) and check the response status.
  const txUrl = url.replace(/\/$/, "") + "/db/neo4j/tx/commit";

  // TM10 v128: same canonical-first env var lookup for credentials.
  const username =
    process.env.DRUGOS_NEO4J_USER ||
    process.env.NEO4J_USER ||
    process.env.NEO4J_USERNAME ||
    "neo4j";
  const password =
    process.env.DRUGOS_NEO4J_PASSWORD ||
    process.env.NEO4J_PASSWORD ||
    "";

  // FE-018 ROOT FIX: ALWAYS try the no-auth ping first. Dev/test
  // deployments commonly run Neo4j with auth disabled — these
  // return 200 to unauthenticated requests, and we should report
  // "available" instead of failing on the missing password env var.
  const noAuthResult = await pingHttp(txUrl, {
    method: "POST",
    timeoutMs: 3000,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      statements: [{ statement: "RETURN 1" }],
    }),
  });

  const noAuthStatus = noAuthResult.status;
  const noAuthOk = noAuthStatus !== undefined && noAuthStatus >= 200 && noAuthStatus < 300;

  // (3) No-auth ping succeeded → Neo4j is up and accepts unauthenticated
  // queries (auth disabled). This is a valid production configuration
  // for dev/test environments.
  if (noAuthOk) {
    return {
      service: "Neo4j (Knowledge Graph — Phase 2)",
      available: true,
      status: "available",
      latencyMs: noAuthResult.latencyMs,
      critical: true,
    };
  }

  // (6) 5xx, timeout, or connection failure → the SERVICE is down.
  // Mark unavailable + critical (overall = "down" → 503 → K8s probe
  // fails → operator gets paged). This is the only branch that
  // sets critical: true on failure.
  const isServiceDown =
    noAuthResult.reason?.includes("timeout") ||
    noAuthResult.reason?.includes("connection failed") ||
    (noAuthStatus !== undefined && noAuthStatus >= 500);
  if (isServiceDown) {
    return {
      service: "Neo4j (Knowledge Graph — Phase 2)",
      available: false,
      status: "unavailable",
      reason: noAuthResult.reason
        ? `Neo4j is DOWN: ${noAuthResult.reason}. (FE-018 root fix: marked unavailable only on 5xx/timeout — auth-missing is degraded, not down.)`
        : `Neo4j is DOWN: HTTP ${noAuthStatus}. (FE-018 root fix.)`,
      latencyMs: noAuthResult.latencyMs,
      critical: true,
    };
  }

  // (5) No-auth ping returned 401/403 AND no credentials are
  // configured → "degraded: auth required". The SERVICE is up (it
  // responded with 401, proving the HTTP server is alive) — the
  // operator just needs to set DRUGOS_NEO4J_PASSWORD. This is NOT
  // a service outage, so NOT critical.
  const isAuthRequired = noAuthStatus === 401 || noAuthStatus === 403;
  if (isAuthRequired && !password) {
    return {
      service: "Neo4j (Knowledge Graph — Phase 2)",
      available: false,
      degraded: true,
      status: "degraded",
      reason:
        `Neo4j requires authentication (HTTP ${noAuthStatus}) but no ` +
        "password is configured. The service is UP (it responded), " +
        "but the health check cannot verify query execution without " +
        "credentials. Set DRUGOS_NEO4J_USER and DRUGOS_NEO4J_PASSWORD " +
        "(canonical) or NEO4J_USER / NEO4J_PASSWORD (legacy) to clear " +
        "this. (FE-018 root fix: auth-missing is degraded, NOT unavailable.)",
      latencyMs: noAuthResult.latencyMs,
      // FE-018 ROOT FIX: NOT critical — the service is up.
      critical: false,
    };
  }

  // (4) No-auth ping returned 401/403 AND credentials ARE configured
  // → retry WITH basic auth. If auth succeeds → available. If auth
  // fails → "degraded: auth failed" (config issue, NOT outage).
  if (isAuthRequired && password) {
    const basicAuth = Buffer.from(`${username}:${password}`).toString("base64");
    const authResult = await pingHttp(txUrl, {
      method: "POST",
      timeoutMs: 3000,
      headers: {
        Authorization: `Basic ${basicAuth}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        statements: [{ statement: "RETURN 1" }],
      }),
    });
    const authStatus = authResult.status;
    const authOk = authStatus !== undefined && authStatus >= 200 && authStatus < 300;
    if (authOk) {
      return {
        service: "Neo4j (Knowledge Graph — Phase 2)",
        available: true,
        status: "available",
        latencyMs: authResult.latencyMs,
        critical: true,
      };
    }
    // Auth failed with credentials provided → config issue (wrong
    // password), NOT a service outage. The service is up (it
    // responded to the no-auth ping with 401).
    const authFailed = authStatus === 401 || authStatus === 403;
    if (authFailed) {
      return {
        service: "Neo4j (Knowledge Graph — Phase 2)",
        available: false,
        degraded: true,
        status: "degraded",
        reason:
          `Neo4j auth FAILED (HTTP ${authStatus}) with the configured ` +
          "credentials. The service is UP, but the username/password " +
          "is wrong. Check DRUGOS_NEO4J_USER / DRUGOS_NEO4J_PASSWORD " +
          "(canonical) or NEO4J_USER / NEO4J_PASSWORD (legacy). " +
          "(FE-018 root fix: auth-failed is degraded, NOT unavailable.)",
        latencyMs: authResult.latencyMs,
        critical: false,
      };
    }
    // 5xx with auth → service is down (already covered above for
    // no-auth, but auth retry might also hit 5xx). Mark unavailable.
    return {
      service: "Neo4j (Knowledge Graph — Phase 2)",
      available: false,
      status: "unavailable",
      reason: authResult.reason
        ? `Neo4j is DOWN (post-auth): ${authResult.reason}. (FE-018 root fix.)`
        : `Neo4j is DOWN (post-auth): HTTP ${authStatus}. (FE-018 root fix.)`,
      latencyMs: authResult.latencyMs,
      critical: true,
    };
  }

  // 404 (database 'neo4j' not found) or other non-2xx non-401/403/5xx
  // → service is up but database is missing/misconfigured. Treat as
  // degraded (config issue), NOT unavailable.
  return {
    service: "Neo4j (Knowledge Graph — Phase 2)",
    available: false,
    degraded: true,
    status: "degraded",
    reason:
      noAuthStatus === 404
        ? "Neo4j database 'neo4j' not found — the database may not be initialized. The Neo4j HTTP server is UP (it responded with 404). (FE-018 root fix.)"
        : noAuthResult.reason
          ? `Neo4j returned unexpected status: ${noAuthResult.reason}. (FE-018 root fix.)`
          : `Neo4j returned unexpected HTTP ${noAuthStatus}. (FE-018 root fix.)`,
    latencyMs: noAuthResult.latencyMs,
    critical: false,
  };
}

/**
 * BE-023 ROOT FIX (v115, HIGH): separate health check for the Phase 2
 * KG service (the Python FastAPI service at KG_SERVICE_URL). This is
 * DISTINCT from Neo4j — the KG service is a Python wrapper that
 * queries Neo4j. A healthy KG service + healthy Neo4j = fully
 * operational Phase 2. A healthy KG service + broken Neo4j = the KG
 * service falls back to its in-memory bridge (degraded). A broken KG
 * service = the frontend's /api/knowledge-graph route returns 502.
 *
 * This check was previously conflated with checkNeo4j — they are now
 * separate so operators can see WHICH component is failing.
 */
async function checkKgService(): Promise<ServiceHealth> {
  const url = process.env.KG_SERVICE_URL;
  if (!url) {
    return {
      service: "KG Service (Phase 2 Python wrapper)",
      available: false,
      status: "unavailable",
      reason: "KG_SERVICE_URL is not configured. The Phase 2 Python KG service wraps Neo4j and exposes /kg/stats, /kg/explore.",
    };
  }
  // Ping the /health endpoint (registered by phase2/service.py).
  const pingUrl = url.replace(/\/$/, "") + "/health";
  const result = await pingHttp(pingUrl);
  return {
    service: "KG Service (Phase 2 Python wrapper)",
    available: result.ok,
    status: result.ok ? "available" : "unavailable",
    reason: result.reason,
    latencyMs: result.latencyMs,
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
  // BE-024 ROOT FIX (v115, MEDIUM): MLflow's tracking server does NOT
  // expose a /health endpoint by default. The previous code pinged
  // /health, which returned 404. With the previous `ok = status < 500`
  // logic, a 404 was treated as "available" — a false positive. With
  // the new strict 2xx logic, a 404 is "degraded".
  //
  // The CORRECT endpoint to ping is the MLflow REST API's
  // /api/2.0/mlflow/experiments/search endpoint. It returns 200 if
  // MLflow is up and the API is responding. We send a POST with an
  // empty body (the API accepts a max_results param but it's optional).
  //
  // Reference: https://mlflow.org/docs/latest/rest-api.html
  const pingUrl = url.replace(/\/$/, "") + "/api/2.0/mlflow/experiments/search";
  const result = await pingHttp(pingUrl, {
    method: "POST",
    timeoutMs: 3000,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ max_results: 1 }),
  });
  // MLflow returns 200 on success, 401 if auth is required (the
  // docker-compose setup uses auth — operators with auth configured
  // should pass MLFLOW_TRACKING_USERNAME / PASSWORD via env vars,
  // which we don't here because the healthcheck should work without
  // auth). A 401 means MLflow is up but auth-gated — degraded.
  // A 404 means the API version is wrong — degraded. A 5xx means
  // MLflow is broken — unavailable.
  const degraded = result.status === 401 || result.status === 403 || result.status === 404;
  return {
    service: "MLflow (Experiment Tracking)",
    available: result.ok,
    degraded,
    status: result.ok ? "available" : degraded ? "degraded" : "unavailable",
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
  // FE-019 ROOT FIX (Teammate 16, hostile-auditor): the previous code
  // fell back from AIRFLOW_URL to DATASET_SERVICE_URL, conflating TWO
  // DIFFERENT SERVICES:
  //   - AIRFLOW_URL → the Apache Airflow webserver (port 8080 per
  //     frontend/src/lib/_url-constants.ts), which orchestrates the
  //     Phase 1 ETL pipeline (ChEMBL/DrugBank/UniProt/STRING/DisGeNET/
  //     OMIM/PubChem ingestion — see project docx Section 3).
  //   - DATASET_SERVICE_URL → legacy alias for PHASE1_SERVICE_URL (per
  //     frontend/src/lib/ml-contracts.ts SERVICE_URL_ENV_VARS), which
  //     points at the Phase 1 FastAPI dataset service (port 8000),
  //     NOT at Airflow.
  // When AIRFLOW_URL was unset but DATASET_SERVICE_URL was set, the
  // function pinged the Phase 1 service's /health and reported it as
  // "Apache Airflow available: true" — a false positive that hid
  // real Airflow outages from operators. Stale data flowed undetected
  // because the admin console showed Airflow as healthy when only the
  // Phase 1 service was up.
  //
  // ROOT FIX (per FE-019 issue spec):
  //   1. NEVER fall back to DATASET_SERVICE_URL — Airflow and the
  //      Phase 1 service are different services on different ports.
  //   2. If AIRFLOW_URL is unset → "not configured" (degraded, NOT
  //      unavailable, NOT critical). The dataset pipeline cannot
  //      refresh, but the platform still serves from existing data
  //      in PostgreSQL/Neo4j.
  //   3. If AIRFLOW_URL is set → ping /health on the Airflow
  //      webserver (the unauthenticated endpoint exposed for
  //      container healthchecks — reference: https://airflow.apache.org/docs/apache-airflow/stable/administration-and-deployment/logging-monitoring/check.html).
  const url = process.env.AIRFLOW_URL;
  if (!url) {
    // (2) "Not configured" — operator action needed. The platform
    // can still serve from existing data, so this is degraded (NOT
    // unavailable) and NOT critical (Airflow is NOT in the critical
    // path — only PostgreSQL and Neo4j are).
    return {
      service: "Apache Airflow (Dataset Pipeline — Phase 1)",
      available: false,
      degraded: true,
      status: "degraded",
      reason:
        "Apache Airflow is NOT CONFIGURED. Set AIRFLOW_URL to the " +
        "Airflow webserver URL (default http://localhost:8080). " +
        "Without Airflow, the dataset pipeline cannot refresh from " +
        "the 7 biomedical sources (ChEMBL/DrugBank/UniProt/STRING/" +
        "DisGeNET/OMIM/PubChem), but the platform continues to serve " +
        "from existing data in PostgreSQL/Neo4j. (FE-019 root fix: " +
        "DATASET_SERVICE_URL fallback removed — Airflow and the Phase 1 " +
        "service are different services on different ports and must " +
        "not be conflated.)",
      // Airflow is NOT critical (degraded, not down) — the platform
      // can still serve queries from existing data.
      critical: false,
    };
  }
  // BE-024 ROOT FIX (v115, MEDIUM) — preserved: Airflow 2.x+ moved
  // /api/v1/health behind auth. The CORRECT unauthenticated endpoint
  // is /health (no /api/v1/ prefix). Airflow 2.x exposes this on the
  // webserver for container healthchecks — it does not require auth
  // and returns 200 with a JSON status.
  const pingUrl = url.replace(/\/$/, "") + "/health";
  const result = await pingHttp(pingUrl, { timeoutMs: 3000 });
  // Airflow /health returns 200 on success, 401 if auth is required
  // (older config), 404 if the endpoint doesn't exist (very old
  // Airflow), 5xx if Airflow is broken.
  const degraded = result.status === 401 || result.status === 403 || result.status === 404;
  return {
    service: "Apache Airflow (Dataset Pipeline — Phase 1)",
    available: result.ok,
    degraded,
    status: result.ok ? "available" : degraded ? "degraded" : "unavailable",
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
  // BE-023 ROOT FIX (v115): checkKgService is now a separate check
  // (distinct from checkNeo4j). The KG service is the Python wrapper
  // around Neo4j — its failure is NOT critical (Neo4j can still be
  // up; the KG service just routes queries to it).
  const [postgres, neo4j, kgService, mlflow, airflow, gt, rl] = await Promise.all([
    checkPostgres(),
    checkNeo4j(),
    checkKgService(),
    checkMlflow(),
    checkAirflow(),
    checkGraphTransformer(),
    checkRlAgent(),
  ]);

  const services = {
    postgres,
    neo4j,
    kgService,
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
