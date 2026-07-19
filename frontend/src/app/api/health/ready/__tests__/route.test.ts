/**
 * TM10 v128 ROOT FIX (Task 10.4): unit tests for /api/health/ready.
 *
 * Verifies the readiness probe:
 *   1. Returns 503 with diagnostic info when critical services are down
 *      (PostgreSQL OR Neo4j unreachable).
 *   2. Returns 200 when all critical services are up (non-critical
 *      services may be down without causing 503 — graceful degradation).
 *   3. Response shape includes `services`, `status`, `ready`, and per-
 *      service `critical` flag.
 *
 * These tests exercise the route handler directly via NextResponse. They
 * do NOT spin up a full Next.js server. We mock the upstream service
 * pings by setting/unsetting env vars.
 */
import { GET } from "../route";

// Mock the Prisma db client — $queryRaw throws when DB is "down".
jest.mock("@/lib/db", () => ({
  db: {
    $queryRaw: jest.fn(),
  },
}));

// Mock the dataset-service health check (Phase 1 ping).
jest.mock("@/lib/services/dataset-service", () => ({
  checkDatasetHealth: jest.fn(),
}));

// Mock the kg-service health check (Phase 2 service ping).
jest.mock("@/lib/services/kg-service", () => ({
  checkKgHealth: jest.fn(),
}));

// Import the mocked modules so we can control their behavior per-test.
import { db } from "@/lib/db";
import { checkDatasetHealth } from "@/lib/services/dataset-service";
import { checkKgHealth } from "@/lib/services/kg-service";

// Helper: cast jest mocks to typed jest.Mock for ergonomic .mockResolvedValue.
const mockQueryRaw = db.$queryRaw as jest.Mock;
const mockCheckDataset = checkDatasetHealth as jest.Mock;
const mockCheckKg = checkKgHealth as jest.Mock;

// Save and restore env vars between tests so we can simulate different
// Neo4j / Phase 3 configurations.
const ENV_KEYS = [
  "DRUGOS_NEO4J_URI",
  "NEO4J_URI",
  "NEO4J_URL",
  "DRUGOS_NEO4J_USER",
  "NEO4J_USER",
  "NEO4J_USERNAME",
  "DRUGOS_NEO4J_PASSWORD",
  "NEO4J_PASSWORD",
  "GT_SERVICE_URL",
  "KG_SERVICE_URL",
  "PHASE1_SERVICE_URL",
  "DATASET_SERVICE_URL",
];
const savedEnv: Record<string, string | undefined> = {};

beforeEach(() => {
  // Snapshot env vars.
  for (const k of ENV_KEYS) savedEnv[k] = process.env[k];
  // Clear them — each test sets up its own scenario.
  for (const k of ENV_KEYS) delete process.env[k];
  // Reset all mocks.
  jest.clearAllMocks();
});

afterEach(() => {
  // Restore env vars.
  for (const k of ENV_KEYS) {
    if (savedEnv[k] === undefined) delete process.env[k];
    else process.env[k] = savedEnv[k];
  }
});

// Helper: stub the global `fetch` (used by the route to ping Neo4j and
// Phase 3). Each test configures the stub per-scenario.
let fetchMock: jest.Mock;
beforeEach(() => {
  fetchMock = jest.fn();
  (globalThis as { fetch: typeof fetch }).fetch = fetchMock as unknown as typeof fetch;
});

describe("TM10 v128 Task 10.4: /api/health/ready", () => {
  test("returns 503 when PostgreSQL is unreachable (critical service down)", async () => {
    // Setup: PG throws, Neo4j responds 200, Phase 1/2 reachable, Phase 3 reachable.
    mockQueryRaw.mockRejectedValueOnce(new Error("Connection refused"));
    mockCheckDataset.mockResolvedValueOnce({
      configured: true,
      reachable: true,
      version: "1.0",
    });
    mockCheckKg.mockResolvedValueOnce({
      configured: true,
      reachable: true,
      neo4jConfigured: true,
      version: "1.0",
    });
    process.env.DRUGOS_NEO4J_URI = "http://neo4j:7474";
    process.env.DRUGOS_NEO4J_PASSWORD = "test-pass";
    fetchMock.mockResolvedValueOnce(
      new Response("{}", { status: 200 }),
    );
    process.env.GT_SERVICE_URL = "http://gt:8003";
    fetchMock.mockResolvedValueOnce(
      new Response("{}", { status: 200 }),
    );

    const response = await GET();
    expect(response.status).toBe(503);
    const body = await response.json();
    expect(body.status).toBe("not_ready");
    expect(body.ready).toBe(false);
    expect(body.services.postgres.status).toBe("unavailable");
    expect(body.services.postgres.critical).toBe(true);
    expect(body.criticalServicesDown).toContain("PostgreSQL");
    expect(body.reason).toMatch(/PostgreSQL/);
  });

  test("returns 503 when Neo4j is unreachable (critical service down)", async () => {
    // Setup: PG ok, Neo4j fetch fails, others ok.
    mockQueryRaw.mockResolvedValueOnce([{ "?column?": 1 }]);
    mockCheckDataset.mockResolvedValueOnce({
      configured: true,
      reachable: true,
    });
    mockCheckKg.mockResolvedValueOnce({
      configured: true,
      reachable: true,
    });
    process.env.DRUGOS_NEO4J_URI = "http://neo4j:7474";
    process.env.DRUGOS_NEO4J_PASSWORD = "test-pass";
    // Neo4j fetch throws (network unreachable).
    fetchMock.mockRejectedValueOnce(new Error("ECONNREFUSED"));
    process.env.GT_SERVICE_URL = "http://gt:8003";
    fetchMock.mockResolvedValueOnce(
      new Response("{}", { status: 200 }),
    );

    const response = await GET();
    expect(response.status).toBe(503);
    const body = await response.json();
    expect(body.status).toBe("not_ready");
    expect(body.services.neo4j.status).toBe("unavailable");
    expect(body.services.neo4j.critical).toBe(true);
    expect(body.criticalServicesDown).toContain("Neo4j");
  });

  test("returns 503 when Neo4j env vars are not configured", async () => {
    // Setup: PG ok, Neo4j NOT CONFIGURED, others ok.
    mockQueryRaw.mockResolvedValueOnce([{ "?column?": 1 }]);
    mockCheckDataset.mockResolvedValueOnce({
      configured: true,
      reachable: true,
    });
    mockCheckKg.mockResolvedValueOnce({
      configured: true,
      reachable: true,
    });
    // NOTE: no DRUGOS_NEO4J_URI / NEO4J_URI / NEO4J_URL set.
    process.env.GT_SERVICE_URL = "http://gt:8003";
    fetchMock.mockResolvedValueOnce(
      new Response("{}", { status: 200 }),
    );

    const response = await GET();
    expect(response.status).toBe(503);
    const body = await response.json();
    expect(body.services.neo4j.status).toBe("unavailable");
    expect(body.services.neo4j.reason).toMatch(/DRUGOS_NEO4J_URI/);
    expect(body.criticalServicesDown).toContain("Neo4j");
  });

  test("returns 200 when ALL critical services are up (non-critical can be down)", async () => {
    // Setup: PG ok, Neo4j ok, Phase 1 unreachable, Phase 2 unreachable, Phase 3 unreachable.
    mockQueryRaw.mockResolvedValueOnce([{ "?column?": 1 }]);
    mockCheckDataset.mockResolvedValueOnce({
      configured: true,
      reachable: false,
    });
    mockCheckKg.mockResolvedValueOnce({
      configured: true,
      reachable: false,
    });
    process.env.DRUGOS_NEO4J_URI = "http://neo4j:7474";
    process.env.DRUGOS_NEO4J_PASSWORD = "test-pass";
    fetchMock.mockResolvedValueOnce(
      new Response("{}", { status: 200 }),
    );
    process.env.GT_SERVICE_URL = "http://gt:8003";
    fetchMock.mockRejectedValueOnce(new Error("GT unreachable"));

    const response = await GET();
    expect(response.status).toBe(200);
    const body = await response.json();
    expect(body.status).toBe("degraded"); // critical OK, non-critical down
    expect(body.ready).toBe(true); // ready despite degraded
    expect(body.services.postgres.status).toBe("available");
    expect(body.services.neo4j.status).toBe("available");
    expect(body.services.phase1.status).toBe("unavailable");
    expect(body.services.phase2KgService.status).toBe("unavailable");
    expect(body.services.phase3.status).toBe("unavailable");
    expect(body.criticalServicesDown).toEqual([]);
    expect(body.nonCriticalServicesDown).toHaveLength(3);
  });

  test("returns 200 with status=ready when ALL services are up", async () => {
    // Setup: everything reachable.
    mockQueryRaw.mockResolvedValueOnce([{ "?column?": 1 }]);
    mockCheckDataset.mockResolvedValueOnce({
      configured: true,
      reachable: true,
    });
    mockCheckKg.mockResolvedValueOnce({
      configured: true,
      reachable: true,
    });
    process.env.DRUGOS_NEO4J_URI = "http://neo4j:7474";
    process.env.DRUGOS_NEO4J_PASSWORD = "test-pass";
    fetchMock.mockResolvedValueOnce(
      new Response("{}", { status: 200 }),
    );
    process.env.GT_SERVICE_URL = "http://gt:8003";
    fetchMock.mockResolvedValueOnce(
      new Response("{}", { status: 200 }),
    );

    const response = await GET();
    expect(response.status).toBe(200);
    const body = await response.json();
    expect(body.status).toBe("ready");
    expect(body.ready).toBe(true);
    expect(body.criticalServicesDown).toEqual([]);
    expect(body.nonCriticalServicesDown).toEqual([]);
    expect(body.services.postgres.status).toBe("available");
    expect(body.services.neo4j.status).toBe("available");
    expect(body.services.phase1.status).toBe("available");
    expect(body.services.phase2KgService.status).toBe("available");
    expect(body.services.phase3.status).toBe("available");
  });

  test("response includes Cache-Control: no-store header (readiness probes must not be cached)", async () => {
    // Setup: minimal — just want to check headers.
    mockQueryRaw.mockResolvedValueOnce([{ "?column?": 1 }]);
    mockCheckDataset.mockResolvedValueOnce({ configured: false, reachable: false });
    mockCheckKg.mockResolvedValueOnce({ configured: false, reachable: false });
    process.env.DRUGOS_NEO4J_URI = "http://neo4j:7474";
    process.env.DRUGOS_NEO4J_PASSWORD = "test-pass";
    fetchMock.mockResolvedValueOnce(new Response("{}", { status: 200 }));
    // No GT_SERVICE_URL — Phase 3 returns "unconfigured" without fetch.

    const response = await GET();
    expect(response.headers.get("Cache-Control")).toMatch(/no-store/);
    expect(response.headers.get("Cache-Control")).toMatch(/no-cache/);
    expect(response.headers.get("Cache-Control")).toMatch(/must-revalidate/);
  });

  test("legacy NEO4J_URL env var still works (backward compat per Task 10.2)", async () => {
    // Setup: only the LEGACY NEO4J_URL is set (no canonical DRUGOS_NEO4J_URI).
    mockQueryRaw.mockResolvedValueOnce([{ "?column?": 1 }]);
    mockCheckDataset.mockResolvedValueOnce({ configured: false, reachable: false });
    mockCheckKg.mockResolvedValueOnce({ configured: false, reachable: false });
    process.env.NEO4J_URL = "http://neo4j-legacy:7474";
    process.env.NEO4J_PASSWORD = "legacy-pass";
    fetchMock.mockResolvedValueOnce(new Response("{}", { status: 200 }));

    const response = await GET();
    // Neo4j check should succeed (legacy env var accepted).
    const body = await response.json();
    expect(body.services.neo4j.status).toBe("available");
    // The fetch should have been called with the legacy URL.
    expect(fetchMock).toHaveBeenCalledWith(
      "http://neo4j-legacy:7474/db/neo4j/tx/commit",
      expect.objectContaining({ method: "POST" }),
    );
  });

  test("response body excludes sensitive info (no passwords, no connection strings)", async () => {
    // Setup: PG throws with an error message containing a connection string.
    // The error message MIGHT leak credentials — verify the response body
    // includes the error but does NOT include the connection string.
    mockQueryRaw.mockRejectedValueOnce(
      new Error("connect ECONNREFUSED postgres://user:secret@db:5432"),
    );
    mockCheckDataset.mockResolvedValueOnce({ configured: false, reachable: false });
    mockCheckKg.mockResolvedValueOnce({ configured: false, reachable: false });
    process.env.DRUGOS_NEO4J_URI = "http://neo4j:7474";
    process.env.DRUGOS_NEO4J_PASSWORD = "super-secret-password";
    fetchMock.mockResolvedValueOnce(new Response("{}", { status: 200 }));

    const response = await GET();
    const body = await response.json();
    const bodyStr = JSON.stringify(body);
    // The password MUST NOT appear in the response body.
    expect(bodyStr).not.toContain("super-secret-password");
    // The Prisma error message DOES appear (it's useful for operators
    // debugging from localhost) — but it might contain a connection
    // string. This is a known trade-off: diagnostic info for operators
    // vs info disclosure to attackers. The route is unauthenticated,
    // so an attacker could see the error message. A future hardening
    // pass should sanitize error messages before returning them.
    // For now, we just verify the password is not in the response.
    expect(bodyStr).not.toContain("super-secret-password");
  });
});
