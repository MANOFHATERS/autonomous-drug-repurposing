/**
 * GET /api/datasets/[drug]/mechanism
 *
 * TEAMMATE-4 ROOT FIX (NEW ROUTE): proxies to the FastAPI backend's
 * /datasets/{drug}/mechanism endpoint (which proxies to Phase 1).
 *
 * Returns the drug's mechanism-of-action (targets + indications) from
 * DrugBank data.
 *
 * See /api/datasets/stats/route.ts for the full architecture comment.
 */

import { NextResponse, type NextRequest } from "next/server";
import { requireAuth, internalError } from "@/lib/api-helpers";

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
  };
  if (sessionCookie) {
    headers["Authorization"] = `Bearer ${sessionCookie}`;
  }
  return headers;
}

interface RouteContext {
  params: Promise<{ drug: string }>;
}

export async function GET(req: NextRequest, ctx: RouteContext) {
  const auth = await requireAuth();
  if (auth.user === null) return auth.response;

  try {
    const { drug } = await ctx.params;
    if (!drug) {
      return NextResponse.json(
        { error: "bad_request", message: "Drug name is required" },
        { status: 400 }
      );
    }
    const backendUrl = getBackendUrl();
    const headers = await getBackendAuthHeaders(req);
    const response = await fetch(
      `${backendUrl}/datasets/${encodeURIComponent(drug)}/mechanism`,
      {
        method: "GET",
        headers,
        cache: "no-store",
      }
    );

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
    return NextResponse.json(data);
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e);
    return internalError(`Drug mechanism proxy failed: ${msg}`);
  }
}
