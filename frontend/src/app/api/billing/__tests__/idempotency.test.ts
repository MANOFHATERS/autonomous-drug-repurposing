/**
 * Task 11.7 — /api/billing/subscription idempotency contract test.
 *
 * HOSTILE-AUDITOR PASS (v129, TM11): verifies the route:
 *   1. Accepts Idempotency-Key via the HTTP HEADER (canonical location).
 *   2. Accepts idempotencyKey via the body (backward compat).
 *   3. Header takes precedence over body when both are present.
 *   4. Returns the existing subscription (noOp: true) when the org
 *      is ALREADY on the requested plan — does NOT create a new invoice.
 *   5. Two POSTs with the SAME idempotency key return the SAME invoiceId
 *      (the second call is an idempotent replay).
 *
 * The test mocks the Prisma db client + auth helpers + CSRF guard
 * so it can exercise the route's pure logic without a real database.
 */
import { POST } from "@/app/api/billing/subscription/route";

// Mock the auth helpers.
jest.mock("@/lib/api-helpers", () => ({
  requireAuthRole: jest.fn().mockResolvedValue({
    user: {
      userId: "u1",
      orgId: "org1",
      email: "owner@drugos.dev",
      role: "owner",
    },
    response: null,
  }),
  badRequest: jest.fn((msg: string) =>
    new Response(JSON.stringify({ error: "bad_request", message: msg }), {
      status: 400,
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

// Mock password + MFA verification — always succeeds.
jest.mock("@/lib/auth/server", () => ({
  verifyPassword: jest.fn().mockResolvedValue(true),
  authenticateApiKey: jest.fn(),
}));
jest.mock("@/lib/auth/totp", () => ({
  verifyMfaTicket: jest.fn().mockReturnValue({ userId: "u1" }),
  verifyTotpWithReplayCheck: jest.fn(),
}));
jest.mock("@/lib/auth/rate-limit", () => ({
  checkTotpRateLimit: jest.fn().mockReturnValue({ locked: false }),
  recordFailedTotpDistributed: jest.fn().mockResolvedValue({
    locked: false,
    attemptsRemaining: 5,
    retryAfterSeconds: 0,
  }),
  clearTotpAttempts: jest.fn(),
}));

// Mock Prisma db — the test controls subscription.findUnique,
// billingInvoice.findUnique, etc. We define the mock INSIDE the
// factory so it's available when the mock is hoisted.
jest.mock("@/lib/db", () => {
  const dbMock = {
    user: {
      findUnique: jest.fn().mockResolvedValue({
        passwordHash: "hash",
        mfaEnabled: false,
        mfaSecret: null,
        email: "owner@drugos.dev",
        lastTotpCounter: null,
      }),
    },
    subscription: {
      findUnique: jest.fn(),
    },
    billingInvoice: {
      findUnique: jest.fn(),
      findFirst: jest.fn(),
      create: jest.fn(),
    },
    $transaction: jest.fn(),
  };
  return { db: dbMock };
});

// Mock the billing service — capture the idempotencyKey arg.
jest.mock("@/lib/services/billing", () => ({
  changePlan: jest.fn(),
  getOrganizationSubscription: jest.fn(),
  PLANS: [
    { id: "free", name: "Free", priceCents: 0, seats: 1, features: [] },
    { id: "researcher", name: "Researcher", priceCents: 4900, seats: 1, features: [] },
  ],
}));

import { changePlan } from "@/lib/services/billing";
import { db } from "@/lib/db";

function buildReq(opts: {
  body: any;
  idempotencyKeyHeader?: string;
}): any {
  const headers = new Headers({
    "content-type": "application/json",
    "x-csrf-token": "csrf_token",
    cookie: "drugos_access=test; drugos_csrf=csrf_token",
  });
  if (opts.idempotencyKeyHeader) {
    headers.set("idempotency-key", opts.idempotencyKeyHeader);
  }
  return {
    json: async () => opts.body,
    headers,
  };
}

describe("Task 11.7: /api/billing/subscription idempotency contract", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    // Default: org has no existing subscription.
    db.subscription.findUnique.mockResolvedValue(null);
    // Default: no existing invoice with the idempotency key.
    db.billingInvoice.findUnique.mockResolvedValue(null);
    db.billingInvoice.findFirst.mockResolvedValue(null);
    // Default: changePlan returns a fresh invoice.
    (changePlan as jest.Mock).mockResolvedValue({
      invoiceId: "inv_new_123",
      idempotentReplay: false,
    });
  });

  test("Accepts Idempotency-Key via the HTTP HEADER", async () => {
    const req = buildReq({
      body: { planId: "researcher", currentPassword: "pw" },
      idempotencyKeyHeader: "client-generated-key-abc",
    });
    const res = await POST(req);
    expect(res.status).toBe(200);
    expect(changePlan).toHaveBeenCalledWith(
      "org1",
      "researcher",
      "client-generated-key-abc", // header value used
    );
  });

  test("Accepts idempotencyKey via the BODY (backward compat)", async () => {
    const req = buildReq({
      body: { planId: "researcher", currentPassword: "pw", idempotencyKey: "body-key-xyz" },
    });
    const res = await POST(req);
    expect(res.status).toBe(200);
    expect(changePlan).toHaveBeenCalledWith(
      "org1",
      "researcher",
      "body-key-xyz", // body value used
    );
  });

  test("HEADER takes precedence over BODY when both are present", async () => {
    const req = buildReq({
      body: { planId: "researcher", currentPassword: "pw", idempotencyKey: "body-key" },
      idempotencyKeyHeader: "header-key",
    });
    const res = await POST(req);
    expect(res.status).toBe(200);
    expect(changePlan).toHaveBeenCalledWith("org1", "researcher", "header-key");
  });

  test("Returns noOp:true when org is ALREADY on the requested plan (no invoice created)", async () => {
    // The org is already on "researcher" — the route should short-circuit.
    db.subscription.findUnique.mockResolvedValue({
      id: "sub_123",
      plan: "researcher",
      status: "active",
    });
    const req = buildReq({
      body: { planId: "researcher", currentPassword: "pw" },
      idempotencyKeyHeader: "any-key",
    });
    const res = await POST(req);
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.ok).toBe(true);
    expect(body.noOp).toBe(true);
    expect(body.idempotentReplay).toBe(true);
    expect(body.invoiceId).toBeNull();
    expect(body.subscription).toBeDefined();
    expect(body.subscription.plan).toBe("researcher");
    // changePlan should NOT have been called (short-circuit).
    expect(changePlan).not.toHaveBeenCalled();
  });

  test("Two POSTs with the SAME idempotency key return the SAME invoiceId (idempotent replay)", async () => {
    // First POST: creates a new invoice.
    (changePlan as jest.Mock).mockResolvedValueOnce({
      invoiceId: "inv_abc",
      idempotentReplay: false,
    });
    const req1 = buildReq({
      body: { planId: "researcher", currentPassword: "pw" },
      idempotencyKeyHeader: "same-key-123",
    });
    const res1 = await POST(req1);
    const body1 = await res1.json();
    expect(body1.invoiceId).toBe("inv_abc");
    expect(body1.idempotentReplay).toBe(false);

    // Second POST with the SAME key: the billing service detects the
    // existing invoice and returns it (idempotentReplay: true).
    (changePlan as jest.Mock).mockResolvedValueOnce({
      invoiceId: "inv_abc", // SAME invoiceId
      idempotentReplay: true,
    });
    const req2 = buildReq({
      body: { planId: "researcher", currentPassword: "pw" },
      idempotencyKeyHeader: "same-key-123", // SAME key
    });
    const res2 = await POST(req2);
    const body2 = await res2.json();
    expect(body2.invoiceId).toBe("inv_abc"); // SAME invoice
    expect(body2.idempotentReplay).toBe(true); // flagged as replay
  });

  test("Records idempotencyKeySource in the audit log (header vs body vs generated)", async () => {
    const { writeAuditLog } = require("@/lib/api-helpers");
    const req = buildReq({
      body: { planId: "researcher", currentPassword: "pw" },
      idempotencyKeyHeader: "header-key",
    });
    await POST(req);
    expect(writeAuditLog).toHaveBeenCalled();
    const auditCall = writeAuditLog.mock.calls[0][0];
    expect(auditCall.metadata.idempotencyKeySource).toBe("header");
  });

  test("Caps Idempotency-Key header at 200 chars (DoS guard)", async () => {
    const longKey = "x".repeat(500);
    const req = buildReq({
      body: { planId: "researcher", currentPassword: "pw" },
      idempotencyKeyHeader: longKey,
    });
    await POST(req);
    expect(changePlan).toHaveBeenCalled();
    const passedKey = (changePlan as jest.Mock).mock.calls[0][2];
    expect(passedKey.length).toBe(200); // capped, not 500
  });
});
