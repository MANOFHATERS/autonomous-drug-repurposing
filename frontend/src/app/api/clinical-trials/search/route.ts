import { NextRequest, NextResponse } from "next/server";
import { searchClinicalTrials } from "@/lib/services/clinical-trials";
import { badRequest, internalError } from "@/lib/api-helpers";

/**
 * ROOT FIXES (FE-023, FE-032).
 *
 * FE-023 — NaN limit/offset: previously `parseInt(req.nextUrl.searchParams.get("limit") || "20", 10)`
 *   returned NaN when the caller passed `?limit=abc`. NaN then propagated
 *   through `Math.min(NaN, 100) = NaN` and `String(NaN) = "NaN"`, which was
 *   sent to upstream as `pageSize=NaN` → 400 error. The root fix clamps
 *   with `Math.max(1, Math.min(100, parsed || 20))` so any non-numeric
 *   input falls back to the default.
 *
 * FE-032 — status cast to any: previously
 *   `const status = (req.nextUrl.searchParams.get("status") || "ALL") as any;`
 *   bypassed the TypeScript union type. A user passing `?status=FOO` would
 *   produce `map[params.status] === undefined`, then
 *   `urlParams.set("filter.overallStatus", undefined)` silently set the
 *   param to the literal string "undefined", and CT.gov returned 400.
 *   The root fix validates against the explicit union type and returns
 *   `badRequest("Invalid status")` on mismatch.
 */

const ALLOWED_STATUSES = new Set(["ALL", "RECRUITING", "ACTIVE_NOT_RECRUITING", "COMPLETED"]);

function clampLimit(raw: string | null): number {
  const def = 20;
  if (!raw) return def;
  const n = Number.parseInt(raw, 10);
  if (!Number.isFinite(n)) return def;
  return Math.max(1, Math.min(100, n));
}

function clampOffset(raw: string | null): number {
  if (!raw) return 0;
  const n = Number.parseInt(raw, 10);
  if (!Number.isFinite(n)) return 0;
  return Math.max(0, Math.min(10_000, n));
}

export async function GET(req: NextRequest) {
  const condition = req.nextUrl.searchParams.get("condition") || "";
  const intervention = req.nextUrl.searchParams.get("intervention") || "";
  const rawStatus = (req.nextUrl.searchParams.get("status") || "ALL").toUpperCase();
  if (!ALLOWED_STATUSES.has(rawStatus)) {
    return badRequest(`Invalid status. Allowed: ${[...ALLOWED_STATUSES].join(", ")}`);
  }
  const status = rawStatus as "RECRUITING" | "ACTIVE_NOT_RECRUITING" | "COMPLETED" | "ALL";
  const limit = clampLimit(req.nextUrl.searchParams.get("limit"));
  const offset = clampOffset(req.nextUrl.searchParams.get("offset"));

  if (!condition && !intervention) {
    return badRequest("At least one of 'condition' or 'intervention' is required");
  }
  try {
    const result = await searchClinicalTrials({
      condition: condition || undefined,
      intervention: intervention || undefined,
      status,
      limit,
      offset,
    });
    return NextResponse.json(result);
  } catch (e: any) {
    return internalError(`ClinicalTrials.gov search failed: ${e.message}`);
  }
}
