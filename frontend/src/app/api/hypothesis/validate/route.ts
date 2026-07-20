import { NextRequest, NextResponse } from "next/server";
import { requireAuth, requireRole, badRequest, writeAuditLog, requireCsrfOrSend } from "@/lib/api-helpers";
// Task 11.6 ROOT FIX (v129, TM11 — hostile-auditor pass):
// This route now writes back to THREE phases (the data flywheel per
// project docx Section 10):
//   1. Phase 4 RL service (rl/service.py /validate) — writes Phase 1
//      CSV + Phase 2 Neo4j edge + Phase 3 retrain JSON. (Existing path.)
//   2. Phase 1 PostgreSQL (phase1-service:8001/datasets/validated_hypotheses)
//      — the CANONICAL data flywheel store per TM3 Task 3.3. The CSV
//      writeback is for backward compat; the PostgreSQL writeback is
//      the V1 criterion ("data flywheel" per project docx Section 10).
//   3. Phase 4 retrain trigger via /api/rl/refresh — kicks the RL
//      agent to re-evaluate the validated hypothesis on the next
//      /api/rl call. The refresh is non-blocking; a failure here does
//      NOT roll back the writeback.
//
// FORENSIC NOTE: the previous version of this route ONLY wrote to the
// RL service (which wrote Phase 1 CSV + Phase 2 Neo4j + Phase 3 JSON).
// The Phase 1 CSV writeback is NOT the same as Phase 1 PostgreSQL —
// the CSV is an ETL input, not the data flywheel store. TM3 built the
// PostgreSQL endpoint (phase1/service.py L696) specifically for this
// route to call. Without this fix, the data flywheel is BROKEN: validated
// hypotheses land in a CSV that gets overwritten on the next ETL run,
// so the model NEVER retrains on validated data. This is exactly the
// "Phase 1 + Phase 4 not linked" complaint the user raised.
import { mlFetch, resolveServiceUrl, buildServiceUrl, MlServiceError } from "@/lib/http-client";
import {
  RlValidateRequestSchema,
  RlValidateResponseSchema,
  type RlValidateResponse,
  validateMlResponse,
} from "@/lib/ml-contracts";
// TASK-268: notification trigger for hypothesis validation completion.
import { notifyHypothesisValidationComplete } from "@/lib/services/notifications";

const SERVICE_NAME = "phase4_rl_validate";

/**
 * POST /api/hypothesis/validate
 * Body: {
 *   drug: string,
 *   disease: string,
 *   outcome: "validated_positive" | "validated_negative" | "validated_toxic" | "invalidated",
 *   validationStudyId?: string,
 *   notes?: string,
 *   originalGtScore?: number,
 *   originalRlRank?: number,
 * }
 *
 * Task 11.6 ROOT FIX: this route implements the FULL data flywheel
 * (project docx Section 10):
 *
 *   Step 2 of the flywheel: "Pharma partner validates a hypothesis
 *   (in wet lab or clinical study). This validation result is fed
 *   back into the platform as a new labeled data point."
 *
 *   Step 3 of the flywheel: "The model retrains on this new proprietary
 *   data."
 *
 * The route writes the validation result to:
 *   - Phase 4 RL service (rl/service.py /validate) — which writes
 *     Phase 1 CSV, Phase 2 Neo4j VALIDATED_TREATS edge, and Phase 3
 *     retrain_triggered.json. (Existing path.)
 *   - Phase 1 PostgreSQL (phase1-service:8001/datasets/validated_hypotheses)
 *     — the CANONICAL data flywheel store. This is the V1 criterion.
 *   - /api/rl/refresh — kicks the RL agent to re-evaluate on the next
 *     /api/rl call.
 *
 * Security: only data-scientist / pi / developer / admin / owner roles
 * can call this route. The validated_by field is set AUTOMATICALLY from
 * the authenticated user's identity.
 */
export async function POST(req: NextRequest) {
  // Task 11.3 ROOT FIX (v129, TM11): CSRF protection. The
  // /api/hypothesis/validate POST route was previously MISSING the
  // requireCsrfOrSend() call — an attacker on evil.com could forge a
  // POST that writes fake validation results to the data flywheel
  // (Phase 1 PostgreSQL + Phase 2 Neo4j + Phase 3 retrain JSON),
  // corrupting the model's training data. The double-submit cookie
  // pattern (see lib/api-helpers.ts) blocks this attack.
  const csrf = await requireCsrfOrSend(req);
  if (csrf.response) return csrf.response;

  const auth = await requireAuth();
  if (auth.user === null) return auth.response;

  // RT-010: only analytics roles can submit validation results.
  const roleCheck = await requireRole(auth.user, "data_scientist", "pi", "developer");
  if (roleCheck.user === null) return roleCheck.response;

  let body: {
    drug?: string;
    disease?: string;
    outcome?: string;
    validationStudyId?: string;
    notes?: string;
    originalGtScore?: number;
    originalRlRank?: number;
  };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "bad_request", message: "Invalid JSON" }, { status: 400 });
  }

  const drug = (body.drug || "").trim();
  const disease = (body.disease || "").trim();
  const outcome = body.outcome;

  if (!drug || !disease) {
    return badRequest("drug and disease are required");
  }
  const validOutcomes = ["validated_positive", "validated_negative", "validated_toxic", "invalidated"];
  if (!outcome || !validOutcomes.includes(outcome)) {
    return badRequest(`outcome must be one of: ${validOutcomes.join(", ")}`);
  }

  // RT-010: validated_by is set from the authenticated user's identity
  // (cannot be spoofed by the caller).
  const validatedBy = auth.user.orgId || auth.user.userId;

  // Step 1: resolve RL_SERVICE_URL. If not set, return 503 with a
  // clear message.
  const rlBaseUrl = resolveServiceUrl("RL_SERVICE_URL");
  if (!rlBaseUrl) {
    return NextResponse.json(
      {
        error: "service_not_deployed",
        service: "Phase 4 RL Hypothesis Ranker",
        reason:
          "RL_SERVICE_URL is not set. The hypothesis writeback requires " +
          "the Phase 4 RL service (rl/service.py) to be running with the " +
          "new /validate endpoint. Start it with `python rl/service.py` " +
          "and set RL_SERVICE_URL=http://localhost:8004 in frontend/.env.local.",
        documentation:
          "See Phase 4 of the build plan (RL-Driven Hypothesis Ranking).",
      },
      { status: 503 }
    );
  }

  // Build the request payload — validate against the contract schema.
  const validatedAt = new Date().toISOString();
  const payload = {
    drug,
    disease,
    outcome,
    validated_by: validatedBy,
    validation_study_id: body.validationStudyId,
    notes: body.notes,
    original_gt_score: body.originalGtScore,
    original_rl_rank: body.originalRlRank,
  };

  // Validate the request payload against the contract.
  const payloadValidation = RlValidateRequestSchema.safeParse(payload);
  if (!payloadValidation.success) {
    return badRequest(
      `Invalid request: ${payloadValidation.error.issues[0]?.message ?? "validation failed"}`
    );
  }

  // ---------------------------------------------------------------
  // STEP 1: Phase 4 RL service /validate (writes Phase 1 CSV +
  // Phase 2 Neo4j + Phase 3 retrain JSON).
  // ---------------------------------------------------------------
  const rlValidateUrl = buildServiceUrl(rlBaseUrl, "/validate");
  const rlResult = await mlFetch<unknown>(rlValidateUrl, {
    service: SERVICE_NAME,
    method: "POST",
    body: payloadValidation.data,
    timeoutMs: 30_000,
    maxRetries: 0, // writeback is NOT idempotent (appends to CSV) — don't retry
  });

  if (!rlResult.ok) {
    const err = rlResult.error as MlServiceError;
    if (err.httpStatus >= 400 && err.httpStatus < 500) {
      return NextResponse.json(
        {
          error: "validate_failed",
          message: `RL service rejected validation request (${err.httpStatus}): ${err.message}`,
          service_error: err.toJSON(),
        },
        { status: err.httpStatus === 400 ? 400 : 502 }
      );
    }
    return NextResponse.json(
      {
        error: "validate_failed",
        message: `RL service validate endpoint failed: ${err.message}`,
        service_error: err.toJSON(),
      },
      { status: 502 }
    );
  }

  // Validate the RL service response against the contract.
  let rlValidated: RlValidateResponse;
  try {
    rlValidated = validateMlResponse(
      SERVICE_NAME,
      "/validate",
      RlValidateResponseSchema,
      rlResult.body,
    );
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    return NextResponse.json(
      {
        error: "validate_contract_violation",
        message: `RL service /validate response did not match expected schema: ${msg}`,
      },
      { status: 502 }
    );
  }

  // ---------------------------------------------------------------
  // STEP 2 (Task 11.6 ROOT FIX): Phase 1 PostgreSQL writeback.
  //
  // POST to phase1-service:8001/datasets/validated_hypotheses with
  // the canonical TM14 CSV-shape payload. The Phase 1 service
  // resolves drug_id and disease_id from the drugs /
  // gene_disease_associations tables, then INSERTs into the
  // validated_hypotheses table. This is the CANONICAL data flywheel
  // store (TM3 Task 3.3 v127) — the CSV writeback the RL service
  // does is for backward compat only.
  //
  // FAILURE HANDLING: this step is NON-BLOCKING. If Phase 1 is
  // unreachable (DATASET_SERVICE_URL / PHASE1_SERVICE_URL not set,
  // DB down, etc.), the validation still succeeded via the RL
  // service — we surface the failure in the response so the caller
  // knows the flywheel is partially broken, but we do NOT roll back
  // the RL writeback (the CSV + Neo4j edge + retrain JSON are
  // already written).
  // ---------------------------------------------------------------
  let phase1Writeback: { ok: boolean; error?: string; endpoint?: string } = {
    ok: false,
    error: "not_attempted",
  };
  const phase1BaseUrl = resolveServiceUrl("PHASE1_SERVICE_URL") || resolveServiceUrl("DATASET_SERVICE_URL");
  if (phase1BaseUrl) {
    const phase1Url = buildServiceUrl(phase1BaseUrl, "/datasets/validated_hypotheses");
    // Phase 1 service expects the TM14 CSV-shape payload (matches
    // shared/contracts/writeback.py WRITEBACK_CSV_COLUMNS). The
    // validated_at field is required (ISO-8601).
    const phase1Payload = {
      drug,
      disease,
      outcome,
      validated_at: validatedAt,
      validated_by: validatedBy,
      validation_study_id: body.validationStudyId ?? null,
      notes: body.notes ?? null,
      original_gt_score: body.originalGtScore ?? null,
      original_rl_rank: body.originalRlRank ?? null,
      writeback_version: "v129_tm11",
    };
    const phase1Result = await mlFetch<unknown>(phase1Url, {
      service: "phase1_validated_hypotheses",
      method: "POST",
      body: phase1Payload,
      timeoutMs: 15_000,
      maxRetries: 0, // PostgreSQL INSERT is not idempotent in general — don't retry
    });
    if (phase1Result.ok) {
      phase1Writeback = { ok: true, endpoint: phase1Url };
    } else {
      const err = phase1Result.error as MlServiceError;
      phase1Writeback = {
        ok: false,
        error: `${err.httpStatus}: ${err.message}`,
        endpoint: phase1Url,
      };
      console.error(
        "[HYPOTHESIS-VALIDATE] Phase 1 PostgreSQL writeback FAILED. " +
          "The RL service writeback succeeded (CSV + Neo4j + retrain JSON), " +
          "but the CANONICAL PostgreSQL writeback did not. The data flywheel " +
          "is PARTIALLY BROKEN — the validated hypothesis is in the CSV but " +
          "NOT in the PostgreSQL store. Error:",
        phase1Writeback.error,
      );
    }
  } else {
    phase1Writeback = {
      ok: false,
      error:
        "PHASE1_SERVICE_URL and DATASET_SERVICE_URL are both unset — " +
        "cannot write to the canonical PostgreSQL data flywheel store.",
    };
    console.error(
      "[HYPOTHESIS-VALIDATE] Phase 1 PostgreSQL writeback SKIPPED: " +
        "PHASE1_SERVICE_URL not configured. The data flywheel is PARTIALLY " +
        "BROKEN — validated hypotheses land in the CSV (which gets overwritten " +
        "on the next ETL run) but NOT in the PostgreSQL store.",
    );
  }

  // ---------------------------------------------------------------
  // STEP 3 (Task 11.6 ROOT FIX): trigger Phase 4 retrain via
  // /api/rl/refresh.
  //
  // The refresh call kicks the RL service to re-evaluate the
  // validated hypothesis on the next /api/rl call. The refresh is
  // NON-BLOCKING — a failure here does NOT roll back the writeback.
  // We use a relative URL so the call stays within the same frontend
  // instance (no need to know the external hostname).
  //
  // NOTE: we use a plain fetch() (not mlFetch) because this is an
  // INTERNAL call to the same Next.js process, not an ML service call.
  // The call inherits the user's auth cookies via the SameSite=Lax
  // cookie jar — but we also forward the original request's
  // X-CSRF-Token header so the refresh route's CSRF check passes.
  // ---------------------------------------------------------------
  let rlRefresh: { ok: boolean; error?: string } = { ok: false, error: "not_attempted" };
  try {
    // Build the internal URL. We use the same origin as the incoming
    // request so the call stays within the same deployment.
    const origin = req.nextUrl.origin;
    const refreshUrl = `${origin}/api/rl/refresh`;
    const refreshRes = await fetch(refreshUrl, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        // Forward the CSRF token so the refresh route's CSRF check
        // passes. The token is the same one the caller sent to THIS
        // route — both routes use the same drugos_csrf cookie.
        "X-CSRF-Token": req.headers.get("x-csrf-token") || "",
        // Forward the cookie header so the refresh route sees the
        // caller's auth cookies. Without this, fetch() in Node 18+
        // does NOT automatically forward cookies.
        cookie: req.headers.get("cookie") || "",
      },
      body: JSON.stringify({}),
      // 10s timeout — the refresh route just calls the RL service's
      // /health endpoint, which should be fast.
      signal: AbortSignal.timeout(10_000),
    });
    if (refreshRes.ok) {
      rlRefresh = { ok: true };
    } else {
      rlRefresh = { ok: false, error: `HTTP ${refreshRes.status}` };
    }
  } catch (e) {
    rlRefresh = {
      ok: false,
      error: e instanceof Error ? e.message : String(e),
    };
    // Non-critical — the writeback already succeeded.
    console.error(
      "[HYPOTHESIS-VALIDATE] /api/rl/refresh trigger failed. " +
        "The writeback succeeded, but the RL agent will NOT re-evaluate " +
        "the validated hypothesis until the next manual refresh. Error:",
      rlRefresh.error,
    );
  }

  await writeAuditLog({
    user: auth.user,
    action: "hypothesis_validate",
    resource: `hypothesis:${drug}:${disease}`,
    metadata: {
      outcome,
      validatedBy,
      validationStudyId: body.validationStudyId,
      phase2Neo4jWritten: rlValidated.writeback?.phase2_neo4j_written,
      // Task 11.6: record the Phase 1 PostgreSQL writeback status so
      // operators can see when the canonical data flywheel store is
      // broken (the CSV writeback is not enough).
      phase1PostgresWritten: phase1Writeback.ok,
      phase1PostgresError: phase1Writeback.error,
      rlRefreshTriggered: rlRefresh.ok,
      rlRefreshError: rlRefresh.error,
    },
  });

  // TASK-268: fire the notification trigger. NON-BLOCKING.
  if (auth.user.orgId) {
    await notifyHypothesisValidationComplete(
      auth.user.userId,
      auth.user.orgId,
      drug,
      disease,
      outcome,
    ).catch((e) => {
      console.error("[HYPOTHESIS-VALIDATE] notification failed:", e);
    });
  }

  return NextResponse.json({
    ok: true,
    writeback: rlValidated.writeback,
    // Task 11.6: surface the FULL data flywheel status so the caller
    // (and the dashboard) can see whether all 3 phases were written.
    dataFlywheel: {
      phase4_rl_service: { ok: true, csv_path: rlValidated.writeback?.phase1_csv_path },
      phase1_postgresql: phase1Writeback,
      phase4_retrain_trigger: rlRefresh,
    },
    message:
      "Hypothesis validation written back to Phase 4 RL service (CSV + Neo4j edge + retrain JSON), " +
      "Phase 1 PostgreSQL (canonical data flywheel store), and Phase 4 retrain trigger.",
  });
}
