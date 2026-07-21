/**
 * POST /api/datasets/validated_hypotheses
 *
 * TEAMMATE-4 ROOT FIX (NEW ROUTE): proxies to the FastAPI backend's
 * /datasets/validated_hypotheses endpoint (the data flywheel writeback).
 *
 * The backend enforces org_id scoping: the org_id in the JWT must match
 * any org_id in the payload. Cross-org validation returns 403.
 *
 * See /api/datasets/stats/route.ts for the full architecture comment.
 */

import { NextResponse, type NextRequest } from "next/server";
import { requireAuth, internalError, requireCsrfOrSend, writeAuditLog } from "@/lib/api-helpers";

function getBackendUrl(): string {
  const url = process.env.BACKEND_SERVICE_URL || "http://localhost:8001";
  return url.replace(/\/$/, "");
}

async function getBackendAuthHeaders(req: NextRequest): Promise<Record<string, string>> {
  const sessionCookie =
    req.cookies.get("next-auth.session-token")?.value ||
    req.cookies.get("__Secure-next-auth.session-token")?.value ||
    "";
  const headers: Record<string, string> = {
    "Accept": "application/json",
    "Content-Type": "application/json",
  };
  if (sessionCookie) {
    headers["Authorization"] = `Bearer ${sessionCookie}`;
  }
  return headers;
}

export async function POST(req: NextRequest) {
  // CSRF protection: state-changing routes require the double-submit
  // cookie pattern (see lib/api-helpers.ts).
  const csrf = await requireCsrfOrSend(req);
  if (csrf.response) return csrf.response;

  const auth = await requireAuth();
  if (auth.user === null) return auth.response;

  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json(
      { error: "bad_request", message: "Invalid JSON" },
      { status: 400 }
    );
  }

  try {
    const backendUrl = getBackendUrl();
    const headers = await getBackendAuthHeaders(req);
    const response = await fetch(`${backendUrl}/datasets/validated_hypotheses`, {
      method: "POST",
      headers,
      body: JSON.stringify(body),
      cache: "no-store",
    });

    await writeAuditLog({
      user: auth.user,
      action: "validated_hypothesis_post",
      resource: "dataset:validated_hypotheses",
      metadata: {
        backend_status: response.status,
      },
    });

    if (!response.ok) {
      const text = await response.text();
      return NextResponse.json(
        {
          error: "backend_error",
          message: `Backend returned ${response.status}: ${text.slice(0, 500)}`,
          backend_status: response.status,
        },
        { status: response.status }
      );
    }

    const data = await response.json();
    return NextResponse.json(data, { status: 201 });
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e);
    return internalError(`Validated hypothesis proxy failed: ${msg}`);
  }
}
