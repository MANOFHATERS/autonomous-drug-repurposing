/**
 * /api/predict — Phase 3 Graph Transformer proxy route.
 *
 * RT-006 ROOT FIX (v105, Step 2 of integration plan):
 * The frontend previously had NO route that proxied to the Phase 3 GT
 * service. The /api/dataset route proxies to Phase 1, /api/knowledge-graph
 * proxies to Phase 2, /api/rl proxies to Phase 4 — but Phase 3 (the GT
 * model that produces the actual drug-disease interaction scores) was
 * UNREACHABLE from the frontend. This is the "missing Phase 3 → Frontend
 * connection" called out in the integration plan's Step 2.
 *
 * Root fix: this route proxies POST/GET requests to GT_SERVICE_URL
 * (http://gt-service:8003 in docker-compose, http://localhost:8003 in
 * dev). When GT_SERVICE_URL is not set, returns 503 with a clear
 * message directing the operator to set it.
 *
 * Schema mirrors the GT service's /predict endpoint:
 *   Request:  { pairs?: [{drug, disease}], drug?: string, disease?: string, limit?: number }
 *   Response: { scores: [{drug, disease, score, confidence}], backend, count }
 */

import { NextRequest, NextResponse } from "next/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const DEFAULT_GT_SERVICE_URL = "http://localhost:8003";

export async function POST(req: NextRequest) {
  const serviceUrl = process.env.GT_SERVICE_URL || DEFAULT_GT_SERVICE_URL;
  let body: any;
  try {
    body = await req.json();
  } catch {
    // Empty body is fine — the service will score all pairs.
    body = {};
  }

  try {
    const res = await fetch(`${serviceUrl.replace(/\/$/, "")}/predict`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(body),
      cache: "no-store",
    });

    if (!res.ok) {
      const text = await res.text();
      return NextResponse.json(
        {
          error: `GT service returned ${res.status}`,
          detail: text,
          serviceUrl,
        },
        { status: res.status }
      );
    }

    const data = await res.json();
    return NextResponse.json(data);
  } catch (e: any) {
    // The GT service is down / unreachable. Return 503 with guidance.
    return NextResponse.json(
      {
        error: "GT service unavailable",
        detail: String(e?.message || e),
        serviceUrl,
        help: "Set GT_SERVICE_URL to the Phase 3 GT service URL (e.g. http://localhost:8003) and start it via `python graph_transformer/service.py`.",
      },
      { status: 503 }
    );
  }
}

export async function GET(req: NextRequest) {
  const serviceUrl = process.env.GT_SERVICE_URL || DEFAULT_GT_SERVICE_URL;
  const url = new URL(req.url);
  const drug = url.searchParams.get("drug") || undefined;
  const disease = url.searchParams.get("disease") || undefined;
  const limit = url.searchParams.get("limit") || "50";

  // If neither drug nor disease is provided, return service health info.
  if (!drug && !disease) {
    try {
      const res = await fetch(`${serviceUrl.replace(/\/$/, "")}/health`, { cache: "no-store" });
      const data = await res.json();
      return NextResponse.json({
        ...data,
        help: "POST to /api/predict with {pairs: [{drug, disease}]} or {drug: 'metformin'} to get GT scores. GET /api/predict?drug=metformin also works.",
      });
    } catch (e: any) {
      return NextResponse.json(
        {
          error: "GT service unavailable",
          detail: String(e?.message || e),
          serviceUrl,
          help: "Set GT_SERVICE_URL and start the GT service via `python graph_transformer/service.py`.",
        },
        { status: 503 }
      );
    }
  }

  // Proxy to /predict with the drug/disease query.
  try {
    const res = await fetch(`${serviceUrl.replace(/\/$/, "")}/predict`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ drug, disease, limit: parseInt(limit, 10) }),
      cache: "no-store",
    });
    if (!res.ok) {
      return NextResponse.json(
        { error: `GT service returned ${res.status}` },
        { status: res.status }
      );
    }
    const data = await res.json();
    return NextResponse.json(data);
  } catch (e: any) {
    return NextResponse.json(
      {
        error: "GT service unavailable",
        detail: String(e?.message || e),
        serviceUrl,
      },
      { status: 503 }
    );
  }
}
