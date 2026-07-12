import { NextRequest, NextResponse } from "next/server";
import { lookupDrugMechanisms } from "@/lib/services/drug-mechanism";
import { requireAuth, badRequest } from "@/lib/api-helpers";

/**
 * POST /api/drugs/mechanism
 * Body: { drugNames: string[] }
 *
 * FE-024 ROOT FIX: Returns the real mechanism of action for each drug
 * name, sourced from ChEMBL. Used by the candidate table to render the
 * "Mechanism" column with real data instead of RL debug output.
 *
 * Auth required: an unauthenticated caller could otherwise enumerate the
 * ChEMBL cache and use this server as a proxy to scrape ChEMBL.
 */
export async function POST(req: NextRequest) {
  const auth = await requireAuth();
  if (auth.user === null) return auth.response;

  let body: { drugNames?: unknown };
  try {
    body = await req.json();
  } catch {
    return badRequest("Invalid JSON body");
  }

  const raw = body.drugNames;
  if (!Array.isArray(raw)) {
    return badRequest("drugNames must be an array of strings");
  }
  // Sanitize + bound the input so a malicious client can't DoS ChEMBL
  // with a 10,000-drug batch.
  const drugNames = raw
    .filter((n): n is string => typeof n === "string")
    .map((n) => n.trim())
    .filter((n) => n.length >= 2 && n.length <= 128)
    .slice(0, 100);

  if (drugNames.length === 0) {
    return NextResponse.json({ results: [] });
  }

  const map = await lookupDrugMechanisms(drugNames);
  const results = drugNames.map((name) => map.get(name.toLowerCase()) || {
    drugName: name,
    chemblId: null,
    mechanism: null,
    source: null,
    fetchedAt: new Date().toISOString(),
  });
  return NextResponse.json({ results });
}
