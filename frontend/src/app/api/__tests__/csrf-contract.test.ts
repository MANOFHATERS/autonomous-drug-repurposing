/**
 * Task 11.3 — CSRF protection contract test.
 *
 * HOSTILE-AUDITOR PASS (v129, TM11): verifies the requireCsrfOrSend
 * guard ACTUALLY rejects requests without a CSRF token. The user
 * explicitly said "comments and tests are fakes" — so this test does
 * NOT trust the comment in api-helpers.ts that claims CSRF is
 * enforced. It mocks NextRequest and verifies the actual behavior.
 *
 * Test cases:
 *   1. Request with NO CSRF cookie AND NO CSRF header → 403
 *      (when the user has session cookies).
 *   2. Request with CSRF cookie but NO header → 403.
 *   3. Request with CSRF header but NO cookie → 403.
 *   4. Request with MISMATCHED cookie + header → 403.
 *   5. Request with MATCHING cookie + header → passes (returns { ok: true }).
 *   6. Request with NO session cookies at all → passes (unauthenticated
 *      requests are exempt — CSRF attacks exploit browser cookie-sending).
 *   7. Request with a VALID API key (Bearer drugos_…) → passes
 *      (programmatic clients are not vulnerable to CSRF).
 *   8. Request with an INVALID API key (Bearer drugos_fake) AND session
 *      cookies AND no CSRF token → 403 (BE-078 ROOT FIX: invalid API
 *      keys do NOT bypass CSRF).
 */
import { requireCsrfOrSend, CSRF_COOKIE_NAME, CSRF_HEADER_NAME } from "@/lib/api-helpers";

// Mock the auth server module so authenticateApiKey returns what we
// want without hitting the database.
jest.mock("@/lib/auth/server", () => ({
  authenticateApiKey: jest.fn(),
}));

// next/headers cookies() mock — we control the cookie jar per test.
jest.mock("next/headers", () => ({
  cookies: jest.fn(),
}));

import { authenticateApiKey } from "@/lib/auth/server";
import { cookies as cookiesMock } from "next/headers";

// Build a fake NextRequest with controlled headers + cookie jar.
function buildReq(opts: {
  csrfHeader?: string;
  authHeader?: string;
  cookieHeader?: string;
  cookieJar?: Record<string, string>;
}): any {
  const headers = new Map<string, string>();
  if (opts.csrfHeader !== undefined) {
    headers.set(CSRF_HEADER_NAME, opts.csrfHeader);
  }
  if (opts.authHeader !== undefined) {
    headers.set("authorization", opts.authHeader);
  }
  if (opts.cookieHeader !== undefined) {
    headers.set("cookie", opts.cookieHeader);
  }
  return {
    headers: {
      get: (name: string) => headers.get(name.toLowerCase()) || null,
    },
  };
}

// Mock cookies() to return a synthetic store.
function mockCookieJar(jar: Record<string, string>) {
  const store = {
    get: (name: string) => (jar[name] ? { value: jar[name] } : undefined),
  };
  (cookiesMock as jest.Mock).mockResolvedValue(store);
}

function clearCookieJar() {
  const store = {
    get: (_name: string) => undefined,
  };
  (cookiesMock as jest.Mock).mockResolvedValue(store);
}

describe("Task 11.3: requireCsrfOrSend — CSRF protection contract", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    (authenticateApiKey as jest.Mock).mockResolvedValue(null);
  });

  test("REJECTS request with session cookies but NO CSRF token (403)", async () => {
    // The user has a valid session (drugos_access cookie is set) but
    // did not send a CSRF token. This is the attack scenario: an
    // attacker on evil.com forges a POST that the browser sends with
    // the victim's session cookie auto-attached, but the attacker
    // cannot read the CSRF cookie to set the matching X-CSRF-Token
    // header (SameSite=Lax blocks cross-origin reads).
    mockCookieJar({
      drugos_access: "valid_access_token",
      drugos_refresh: "valid_refresh_token",
      // NO drugos_csrf cookie
    });
    const req = buildReq({}); // no CSRF header
    const result = await requireCsrfOrSend(req);
    expect(result.ok).toBe(false);
    expect(result.response).not.toBeNull();
    if (!result.ok && result.response) {
      // The response is a NextResponse — its status should be 403.
      expect(result.response.status).toBe(403);
      const body = await result.response.json();
      expect(body.error).toBe("csrf_missing");
    }
  });

  test("REJECTS request with CSRF cookie but NO CSRF header (403)", async () => {
    // The user has the drugos_csrf cookie set (from login) but forgot
    // to copy its value into the X-CSRF-Token header. This is a
    // misconfigured client, not an attack — but we reject anyway
    // (the cookie alone is not proof of intent; the header proves
    // the client intentionally sent the token).
    mockCookieJar({
      drugos_access: "valid_access_token",
      drugos_csrf: "abc123token",
    });
    const req = buildReq({}); // no CSRF header
    const result = await requireCsrfOrSend(req);
    expect(result.ok).toBe(false);
    if (!result.ok && result.response) {
      expect(result.response.status).toBe(403);
    }
  });

  test("REJECTS request with CSRF header but NO CSRF cookie (403)", async () => {
    // The client sent a CSRF header but no cookie. This could be a
    // misconfigured client OR an attacker who somehow got the token
    // value but the cookie was not set (e.g., they extracted the
    // token from a leaked log). Reject — both pieces must be present.
    mockCookieJar({
      drugos_access: "valid_access_token",
      // NO drugos_csrf cookie
    });
    const req = buildReq({ csrfHeader: "abc123token" });
    const result = await requireCsrfOrSend(req);
    expect(result.ok).toBe(false);
  });

  test("REJECTS request with MISMATCHED cookie + header (403)", async () => {
    mockCookieJar({
      drugos_access: "valid_access_token",
      drugos_csrf: "cookie_token_value",
    });
    const req = buildReq({ csrfHeader: "different_header_value" });
    const result = await requireCsrfOrSend(req);
    expect(result.ok).toBe(false);
    if (!result.ok && result.response) {
      expect(result.response.status).toBe(403);
      const body = await result.response.json();
      expect(body.error).toBe("csrf_mismatch");
    }
  });

  test("ACCEPTS request with MATCHING cookie + header", async () => {
    const token = "matching_token_value_abc123";
    mockCookieJar({
      drugos_access: "valid_access_token",
      drugos_csrf: token,
    });
    const req = buildReq({ csrfHeader: token });
    const result = await requireCsrfOrSend(req);
    expect(result.ok).toBe(true);
    expect(result.response).toBeNull();
  });

  test("ACCEPTS request with NO session cookies at all (unauthenticated)", async () => {
    // CSRF protection is irrelevant when there are no session cookies
    // to exploit. This lets /api/auth/login and /api/auth/register
    // work without a pre-issued CSRF token.
    clearCookieJar();
    const req = buildReq({}); // no headers, no cookies
    const result = await requireCsrfOrSend(req);
    expect(result.ok).toBe(true);
  });

  test("ACCEPTS request with a VALID API key (Bearer drugos_…)", async () => {
    // Programmatic clients with API keys are not vulnerable to CSRF
    // (the attacker cannot make the victim's browser send the
    // attacker's API key). Exempt VALID API keys.
    (authenticateApiKey as jest.Mock).mockResolvedValue({
      userId: "user_123",
      orgId: "org_123",
    });
    clearCookieJar(); // API-key clients don't have session cookies
    const req = buildReq({ authHeader: "Bearer drugos_real_key_abc123" });
    const result = await requireCsrfOrSend(req);
    expect(result.ok).toBe(true);
    expect(authenticateApiKey).toHaveBeenCalledWith("drugos_real_key_abc123");
  });

  test("REJECTS request with INVALID API key + session cookies + no CSRF (BE-078 ROOT FIX)", async () => {
    // BE-078 ROOT FIX: an attacker with the victim's session cookie
    // could send `Authorization: Bearer drugos_fake_key` to bypass
    // the CSRF check (the previous code exempted ANY drugos_-prefixed
    // key without verifying it). The fix: only VALID API keys are
    // exempt. An invalid key + session cookies + no CSRF → 403.
    (authenticateApiKey as jest.Mock).mockResolvedValue(null); // invalid key
    mockCookieJar({
      drugos_access: "victim_access_token",
      drugos_csrf: "victim_csrf_token",
    });
    const req = buildReq({
      authHeader: "Bearer drugos_fake_key",
      // NO CSRF header — the attacker is trying to bypass CSRF
    });
    const result = await requireCsrfOrSend(req);
    expect(result.ok).toBe(false);
    if (!result.ok && result.response) {
      expect(result.response.status).toBe(403);
    }
  });
});
