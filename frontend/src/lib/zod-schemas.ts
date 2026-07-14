/**
 * BE-029 ROOT FIX (Team Member 12): shared Zod schemas for API request bodies.
 *
 * ROOT CAUSE: NONE of the 43 API routes used Zod to validate request bodies.
 * Validation was manual and inconsistent — some routes checked
 * `typeof body.field === "string"`, others used `!body.field`, others
 * trusted the body entirely. Type-confusion bugs were exploitable:
 *   - `body.limit = "abc"` → `Math.min("abc", 5000)` → NaN →
 *     `pairs.slice(0, NaN)` → empty array returned silently.
 *   - `body.drug` as object → `.trim()` throws unhandled TypeError → 500.
 *   - `body.tags` as string → `.join(",")` joins characters.
 *
 * ROOT FIX: this module exports one Zod schema per route body shape,
 * plus a `validateBody()` helper that returns a typed result. Routes
 * call `const parsed = validateBody(MySchema, body); if (!parsed.ok)
 * return parsed.response;` and get a 400 with a structured error
 * message on validation failure.
 *
 * We use Zod 4 (already a dependency ^4.0.2). Zod 4's `safeParse`
 * returns `{ success: true, data } | { success: false, error }` — we
 * translate that into the discriminated union below so routes can
 * narrow with `parsed.ok`.
 *
 * SECURITY: every state-changing route (POST/PATCH/PUT/DELETE) on the
 * 7 routes owned by Team Member 12 (BE-021 to BE-040) now uses these
 * schemas. Other teams can adopt the same helper for their routes.
 */
import { z } from "zod";
import { NextResponse } from "next/server";

// ---------------------------------------------------------------------------
// Generic validation helper
// ---------------------------------------------------------------------------

export type ValidationResult<T> =
  | { ok: true; data: T }
  | { ok: false; response: NextResponse };

/**
 * Validate an unknown request body against a Zod schema.
 *
 * On success: returns `{ ok: true, data }` with the parsed (and
 * type-narrowed) body.
 *
 * On failure: returns `{ ok: false, response }` with a 400 NextResponse
 * whose JSON body lists every field error. Routes return the response
 * directly: `if (!parsed.ok) return parsed.response;`.
 *
 * The error response shape is:
 *   {
 *     error: "bad_request",
 *     message: "Request body failed validation",
 *     issues: [{ path: "pairs.0.drug", message: "Required" }, ...]
 *   }
 */
export function validateBody<T>(
  schema: z.ZodType<T>,
  body: unknown
): ValidationResult<T> {
  const result = schema.safeParse(body);
  if (result.success) {
    return { ok: true, data: result.data };
  }
  // Zod 4: result.error.issues is an array of { path, message, code }.
  const issues = result.error.issues.map((iss) => ({
    path: iss.path.join("."),
    message: iss.message,
  }));
  return {
    ok: false,
    response: NextResponse.json(
      {
        error: "bad_request",
        message: "Request body failed validation.",
        issues,
      },
      { status: 400 }
    ),
  };
}

// ---------------------------------------------------------------------------
// Per-route schemas
// ---------------------------------------------------------------------------

// /api/predict — POST body (BE-029, BE-030)
export const PredictBody = z.object({
  pairs: z
    .array(
      z.object({
        drug: z.string().min(1).max(200),
        disease: z.string().min(1).max(200),
      })
    )
    .min(1)
    .max(5000),
  limit: z.number().int().positive().max(5000).optional(),
});
export type PredictBodyT = z.infer<typeof PredictBody>;

// /api/top-k — no body (GET only). Query param validated inline.

// /api/rl — POST body (BE-029)
export const RlBody = z.object({
  drug: z.string().min(1).max(200).optional(),
  disease: z.string().min(1).max(200).optional(),
  limit: z.number().int().positive().max(200).optional(),
  sort: z
    .enum(["rank", "overallScore", "gnnScore", "safetyScore", "marketScore", "reward", "drug", "disease"])
    .optional(),
  sortDir: z.enum(["asc", "desc"]).optional(),
  page: z.number().int().min(0).optional(),
  pageSize: z.number().int().positive().max(200).optional(),
  orgId: z.string().min(1).max(100).optional(),
});
export type RlBodyT = z.infer<typeof RlBody>;

// /api/knowledge-graph — POST body (BE-029)
export const KnowledgeGraphBody = z.object({
  cypher: z.string().min(1).max(10_000),
  params: z.record(z.string(), z.unknown()).optional(),
});
export type KnowledgeGraphBodyT = z.infer<typeof KnowledgeGraphBody>;

// /api/auth/2fa/disable — POST body (BE-029, BE-031)
export const TwoFaDisableBody = z.object({
  currentPassword: z.string().min(1).max(1024),
  totpCode: z.string().regex(/^\d{6}$/).optional(),
});
export type TwoFaDisableBodyT = z.infer<typeof TwoFaDisableBody>;

// /api/auth/2fa/login-verify — POST body (BE-029, BE-034)
export const TwoFaLoginVerifyBody = z.object({
  mfaToken: z.string().min(1).max(4096).optional(),
  code: z.string().regex(/^\d{6}$/),
});
export type TwoFaLoginVerifyBodyT = z.infer<typeof TwoFaLoginVerifyBody>;

// /api/auth/password — POST body (BE-029, BE-032)
export const PasswordChangeBody = z.object({
  currentPassword: z.string().min(1).max(1024),
  newPassword: z.string().min(8).max(1024),
});
export type PasswordChangeBodyT = z.infer<typeof PasswordChangeBody>;

// /api/auth/verify-email — POST body (BE-029)
export const VerifyEmailBody = z.object({
  token: z.string().min(10).max(8192),
});
export type VerifyEmailBodyT = z.infer<typeof VerifyEmailBody>;

// /api/auth/me — PATCH body (BE-029)
export const AuthMePatchBody = z.object({
  name: z.string().min(1).max(200).optional(),
  title: z.string().max(200).optional(),
  bio: z.string().max(2000).optional(),
  activeOrganizationId: z.string().min(1).max(100).nullable().optional(),
});
export type AuthMePatchBodyT = z.infer<typeof AuthMePatchBody>;

// /api/billing/subscription — POST body (BE-029, BE-033)
export const BillingSubscriptionBody = z.object({
  planId: z.string().min(1).max(100),
  currentPassword: z.string().min(1).max(1024),
  totpCode: z.string().regex(/^\d{6}$/).optional(),
  mfaTicket: z.string().min(1).max(4096).optional(),
}).refine(
  (data) => !(data.totpCode && data.mfaTicket),
  { message: "Provide either totpCode or mfaTicket, not both." }
);
export type BillingSubscriptionBodyT = z.infer<typeof BillingSubscriptionBody>;
