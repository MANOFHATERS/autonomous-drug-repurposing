/**
 * Task 11.6 — /api/hypothesis/validate data-flywheel contract test.
 *
 * HOSTILE-AUDITOR PASS (v129, TM11): verifies the route writes back
 * to ALL THREE phases of the data flywheel:
 *   1. Phase 4 RL service (rl/service.py /validate) — existing path.
 *   2. Phase 1 PostgreSQL (phase1-service:8001/datasets/validated_hypotheses)
 *      — Task 11.6 NEW path.
 *   3. /api/rl/refresh — Task 11.6 NEW path (Phase 4 retrain trigger).
 *
 * The test mocks mlFetch (RL + Phase 1 service calls) and global
 * fetch (the /api/rl/refresh internal call). It then POSTs to the
 * route and verifies all three writebacks were attempted.
 */
import { POST } from "@/app/api/hypothesis/validate/route";

// Mock the auth helpers — the route requires auth + role check.
jest.mock("@/lib/api-helpers", () => ({
  requireAuth: jest.fn().mockResolvedValue({
    user: {
      userId: "u1",
      orgId: "org1",
      email: "ds@drugos.dev",
      role: "data_scientist",
    },
    response: null,
  }),
  requireRole: jest.fn().mockResolvedValue({
    user: {
      userId: "u1",
      orgId: "org1",
      email: "ds@drugos.dev",
      role: "data_scientist",
    },
    response: null,
  }),
  badRequest: jest.fn((msg: string) =>
    new Response(JSON.stringify({ error: "bad_request", message: msg }), {
      status: 400,
      headers: { "Content-Type": "application/json" },
    }),
  ),
  internalError: jest.fn((msg: string) =>
    new Response(JSON.stringify({ error: "internal_error", message: msg }), {
      status: 500,
    }),
  ),
  writeAuditLog: jest.fn().mockResolvedValue(undefined),
  requireCsrfOrSend: jest.fn().mockResolvedValue({ ok: true, response: null }),
}));

// Mock mlFetch — captures both the RL service /validate call AND the
// Phase 1 /datasets/validated_hypotheses call.
jest.mock("@/lib/http-client", () => ({
  mlFetch: jest.fn(),
  resolveServiceUrl: jest.fn(),
  buildServiceUrl: jest.fn((base: string, path: string) => base + path),
  MlServiceError: class MlServiceError extends Error {
    httpStatus: number;
    isTimeout: boolean;
    constructor(msg: string, opts: any = {}) {
      super(msg);
      this.httpStatus = opts.httpStatus ?? 0;
      this.isTimeout = opts.isTimeout ?? false;
    }
    toJSON() {
      return { message: this.message, httpStatus: this.httpStatus };
    }
  },
}));

// Mock the notifications service (non-blocking side-effect).
jest.mock("@/lib/services/notifications", () => ({
  notifyHypothesisValidationComplete: jest.fn().mockResolvedValue(undefined),
}));

// Mock the ml-contracts validateMlResponse so it passes through.
jest.mock("@/lib/ml-contracts", () => {
  const actual = jest.requireActual("@/lib/ml-contracts");
  return {
    ...actual,
    validateMlResponse: jest.fn((_svc, _ep, _schema, body) => body),
  };
});

// Mock global fetch — the /api/rl/refresh internal call uses plain fetch.
const fetchMock = jest.fn().mockResolvedValue(new Response("{}", { status: 200 }));
(global as any).fetch = fetchMock;

import { mlFetch, resolveServiceUrl } from "@/lib/http-client";

function buildReq(body: any): any {
  return {
    json: async () => body,
    headers: new Headers({
      "content-type": "application/json",
      "x-csrf-token": "test_csrf_token",
      cookie: "drugos_access=test; drugos_csrf=test_csrf_token",
    }),
    nextUrl: {
      origin: "http://localhost:3000",
      searchParams: new URLSearchParams(),
    },
  };
}

describe("Task 11.6: /api/hypothesis/validate data-flywheel contract", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    // Default: both RL_SERVICE_URL and PHASE1_SERVICE_URL are configured.
    (resolveServiceUrl as jest.Mock).mockImplementation((envVar: string) => {
      if (envVar === "RL_SERVICE_URL") return "http://localhost:8004";
      if (envVar === "PHASE1_SERVICE_URL" || envVar === "DATASET_SERVICE_URL")
        return "http://localhost:8001";
      return null;
    });
    (mlFetch as jest.Mock).mockImplementation(async (url: string) => {
      // IMPORTANT: check "/datasets/validated_hypotheses" BEFORE "/validate"
      // because "/validated_hypotheses" CONTAINS "/validate" as a substring.
      // Phase 1 /datasets/validated_hypotheses
      if (url.includes("/datasets/validated_hypotheses")) {
        return { ok: true, body: { ok: true, id: "vh_123" } };
      }
      // RL service /validate (exact path — not "/validated_*")
      if (url.includes("/validate")) {
        return {
          ok: true,
          body: {
            ok: true,
            writeback: {
              phase1_csv_path: "/data/validated_hypotheses.csv",
              phase2_neo4j_written: true,
              phase3_trigger_path: "/data/retrain_triggered.json",
              validated_hypothesis: { drug: "aspirin", disease: "cancer" },
              writeback_version: "v129_tm11",
            },
          },
        };
      }
      return { ok: false, error: { httpStatus: 404, message: "unknown url" } };
    });
  });

  test("Writes back to ALL THREE phases (RL service + Phase 1 PostgreSQL + /api/rl/refresh)", async () => {
    const req = buildReq({
      drug: "aspirin",
      disease: "cancer",
      outcome: "validated_positive",
      validationStudyId: "NCT12345",
      notes: "Wet-lab confirmed",
      originalGtScore: 0.87,
      originalRlRank: 3,
    });
    const res = await POST(req);
    expect(res.status).toBe(200);
    const body = await res.json();

    // 1. RL service /validate was called.
    expect(mlFetch).toHaveBeenCalledWith(
      "http://localhost:8004/validate",
      expect.objectContaining({ method: "POST" }),
    );

    // 2. Phase 1 PostgreSQL /datasets/validated_hypotheses was called.
    expect(mlFetch).toHaveBeenCalledWith(
      "http://localhost:8001/datasets/validated_hypotheses",
      expect.objectContaining({ method: "POST" }),
    );

    // 3. /api/rl/refresh was triggered via internal fetch.
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:3000/api/rl/refresh",
      expect.objectContaining({ method: "POST" }),
    );

    // 4. The response includes the data flywheel status.
    expect(body.ok).toBe(true);
    expect(body.dataFlywheel).toBeDefined();
    expect(body.dataFlywheel.phase4_rl_service.ok).toBe(true);
    expect(body.dataFlywheel.phase1_postgresql.ok).toBe(true);
    expect(body.dataFlywheel.phase4_retrain_trigger.ok).toBe(true);
  });

  test("Phase 1 PostgreSQL payload includes required fields (drug, disease, outcome, validated_at, validated_by)", async () => {
    const req = buildReq({
      drug: "aspirin",
      disease: "cancer",
      outcome: "validated_positive",
    });
    await POST(req);
    const phase1Call = (mlFetch as jest.Mock).mock.calls.find((call) =>
      call[0].includes("/datasets/validated_hypotheses"),
    );
    expect(phase1Call).toBeDefined();
    const payload = phase1Call[1].body;
    expect(payload.drug).toBe("aspirin");
    expect(payload.disease).toBe("cancer");
    expect(payload.outcome).toBe("validated_positive");
    expect(payload.validated_at).toBeDefined();
    expect(typeof payload.validated_at).toBe("string");
    // validated_at must be ISO-8601 parseable.
    expect(() => new Date(payload.validated_at).toISOString()).not.toThrow();
    expect(payload.validated_by).toBe("org1"); // orgId from auth.user
    expect(payload.writeback_version).toBe("v129_tm11");
  });

  test("Returns 503 when RL_SERVICE_URL is not set (BLOCKER — flywheel cannot start)", async () => {
    const { resolveServiceUrl } = require("@/lib/http-client");
    (resolveServiceUrl as jest.Mock).mockImplementation((envVar: string) => {
      if (envVar === "RL_SERVICE_URL") return null;
      return "http://localhost:8001";
    });
    const req = buildReq({
      drug: "aspirin",
      disease: "cancer",
      outcome: "validated_positive",
    });
    const res = await POST(req);
    expect(res.status).toBe(503);
    const body = await res.json();
    expect(body.error).toBe("service_not_deployed");
  });

  test("Phase 1 writeback failure is NON-BLOCKING (RL writeback still succeeds)", async () => {
    (mlFetch as jest.Mock).mockImplementation(async (url: string) => {
      // IMPORTANT: check "/datasets/validated_hypotheses" BEFORE "/validate"
      // (see beforeEach comment about the substring trap).
      if (url.includes("/datasets/validated_hypotheses")) {
        return {
          ok: false,
          error: {
            httpStatus: 503,
            message: "PostgreSQL unavailable",
            toJSON: () => ({ message: "PostgreSQL unavailable" }),
          },
        };
      }
      if (url.includes("/validate")) {
        return {
          ok: true,
          body: {
            ok: true,
            writeback: {
              phase1_csv_path: "/data/validated_hypotheses.csv",
              phase2_neo4j_written: true,
              phase3_trigger_path: "/data/retrain_triggered.json",
              validated_hypothesis: {},
              writeback_version: "v129_tm11",
            },
          },
        };
      }
      return { ok: false, error: { httpStatus: 404 } };
    });
    const req = buildReq({
      drug: "aspirin",
      disease: "cancer",
      outcome: "validated_positive",
    });
    const res = await POST(req);
    // The route still returns 200 — the RL writeback succeeded.
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.ok).toBe(true);
    // But the data flywheel status shows Phase 1 failed.
    expect(body.dataFlywheel.phase1_postgresql.ok).toBe(false);
    expect(body.dataFlywheel.phase1_postgresql.error).toContain("PostgreSQL unavailable");
  });

  test("/api/rl/refresh trigger failure is NON-BLOCKING", async () => {
    fetchMock.mockRejectedValueOnce(new Error("refresh timed out"));
    const req = buildReq({
      drug: "aspirin",
      disease: "cancer",
      outcome: "validated_positive",
    });
    const res = await POST(req);
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.ok).toBe(true);
    expect(body.dataFlywheel.phase4_retrain_trigger.ok).toBe(false);
  });

  test("Forwards CSRF token to the /api/rl/refresh internal call", async () => {
    const req = buildReq({
      drug: "aspirin",
      disease: "cancer",
      outcome: "validated_positive",
    });
    await POST(req);
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:3000/api/rl/refresh",
      expect.objectContaining({
        headers: expect.objectContaining({
          "X-CSRF-Token": "test_csrf_token",
        }),
      }),
    );
  });

  test("Rejects invalid outcome values (400)", async () => {
    const req = buildReq({
      drug: "aspirin",
      disease: "cancer",
      outcome: "invalid_outcome",
    });
    const res = await POST(req);
    expect(res.status).toBe(400);
  });

  test("Rejects missing drug or disease (400)", async () => {
    const req = buildReq({
      drug: "",
      disease: "cancer",
      outcome: "validated_positive",
    });
    const res = await POST(req);
    expect(res.status).toBe(400);
  });
});
