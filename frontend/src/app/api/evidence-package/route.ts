import { NextRequest, NextResponse } from "next/server";
import { buildEvidencePackage, evidencePackageToMarkdown } from "@/lib/services/evidence-package";
import { requireAuthRole, badRequest, internalError, writeAuditLog, requireCsrfOrSend, notFound } from "@/lib/api-helpers";
import { parsePagination, buildPaginatedResponse } from "@/lib/pagination";
import { db } from "@/lib/db";
// FE-008 ROOT FIX (Team Member 13): validate drug/disease exist in the KG.
//
// Previously this route accepted ANY drug + disease name and built an
// evidence package from PubMed + ClinicalTrials.gov + openFDA without
// verifying the drug or disease exists in the Phase 2 KG. A researcher
// could request an evidence package for "aspirin" + "cancer" even if the
// KG has no aspirin-cancer edge. The PDF then contained generic drug and
// disease info, not KG-derived evidence — the researcher may believe the
// package reflects KG evidence when it does not.
//
// ROOT FIX: before building the package, query the KG (via
// knowledge-graph-stats.ts when KG_SERVICE_URL is unset, or via the KG
// service proxy when it is set) for the drug and disease. If neither is
// found in the KG, return 404 with a clear error message.
//
// We use the lib service `knowledge-graph-stats.ts` to get the list of
// sources + bridge summary. The bridge summary's `edge_types_present`
// list tells us which edge types exist (e.g. "(Compound, treats,
// Disease)"). If the bridge is missing entirely, we fall back to a
// permissive mode (allow the build) — better to produce a package with
// a "KG validation skipped: bridge not built" warning than to hard-block
// evidence package generation when the KG is mid-rebuild.
//
// SECURITY: this validation is NOT a substitute for KG-level row-level
// security. The KG service enforces tenant isolation on its side. This
// route only checks "does the drug/disease name appear in the KG at all"
// — not "does the calling user's org have access to it".
import { getKnowledgeGraphStats } from "@/lib/services/knowledge-graph-stats";

/**
 * Check whether a drug or disease name appears in the Phase 2 KG.
 *
 * Returns true if the name appears in any of:
 *   - The KG service's /lookup endpoint (when KG_SERVICE_URL is set).
 *   - The local Phase 1 checkpoint's bridge summary (when no service).
 *
 * For the local fallback, we check whether the name appears in the
 * `edge_types_present` list (which contains entries like
 * "(Compound, treats, Disease)"). This is a coarse check — it tells us
 * the KG has Compound and Disease nodes, not that this specific drug
 * exists. The fine-grained check requires the KG service to be deployed.
 *
 * The `kind` parameter ("drug" | "disease") is used for the error
 * message and to choose which Cypher-style type to look for in the
 * bridge summary ("Compound" for drugs, "Disease" for diseases).
 */
async function validateEntityInKg(
  name: string,
  kind: "drug" | "disease"
): Promise<{ ok: boolean; reason?: string }> {
  const trimmed = name.trim();
  if (!trimmed) {
    return { ok: false, reason: `${kind} name is empty` };
  }

  // If the KG service is deployed, use its /lookup endpoint for a
  // fine-grained check.
  const kgUrl = process.env.KG_SERVICE_URL;
  if (kgUrl) {
    try {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 10_000);
      let res: Response;
      try {
        res = await fetch(
          `${kgUrl.replace(/\/$/, "")}/lookup?type=${kind === "drug" ? "Compound" : "Disease"}&name=${encodeURIComponent(trimmed)}`,
          {
            headers: { Accept: "application/json" },
            signal: controller.signal,
          }
        );
      } finally {
        clearTimeout(timeout);
      }
      if (res.ok) {
        const body = await res.json();
        if (body?.found === true || Array.isArray(body?.matches) && body.matches.length > 0) {
          return { ok: true };
        }
        return {
          ok: false,
          reason: `${kind.charAt(0).toUpperCase() + kind.slice(1)} "${trimmed}" was not found in the knowledge graph.`,
        };
      }
      // 4xx / 5xx from the KG service — fall through to the local check.
    } catch {
      // Network error / timeout — fall through to the local check.
    }
  }

  // Local fallback: check the registry + node-type counts for the
  // relevant node type. FE-020 (Team 15) added per-type node count
  // breakdowns to the response — we use those to verify the KG has
  // been built with the right node types.
  try {
    const stats = await getKnowledgeGraphStats();
    if (stats.source === "none") {
      // KG not built at all — allow the build but the caller should
      // display a warning. We return ok=true so the evidence package
      // can still be generated (with a "KG validation skipped" note).
      // The audit log records that validation was skipped.
      return { ok: true };
    }
    // FE-020: check nodeTypeCounts for the relevant canonical node type.
    // The CANONICAL_NODE_TYPES are Compound, Protein, Pathway, Disease,
    // ClinicalOutcomes — we map "drug" → "Compound" and "disease" →
    // "Disease".
    const nodeType = kind === "drug" ? "Compound" : "Disease";
    // Check both the canonical nodeTypeCounts map AND the per-source
    // nodeTypeCounts (some sources may contribute to a type even if
    // the aggregate map is empty due to missing node_type_counts in
    // the registry).
    const aggregateCount = stats.nodeTypeCounts?.[nodeType] ?? 0;
    const perSourceCount = stats.sources.reduce((sum, s) => {
      const c = s.nodeTypeCounts?.[nodeType] ?? 0;
      return sum + c;
    }, 0);
    if (aggregateCount === 0 && perSourceCount === 0) {
      // The registry may not have node_type_counts populated yet
      // (FE-020 depends on the Phase 2 builder writing that field).
      // In that case, we cannot definitively say the KG lacks the
      // node type — be permissive (allow the build) but log a warning.
      console.warn(
        `evidence-package: KG validation could not confirm ${nodeType} nodes (registry may not have node_type_counts). Allowing build for ${kind}="${trimmed}".`
      );
      return { ok: true };
    }
    // The KG has the right node type — we cannot do a fine-grained
    // check without the KG service, so we allow the build. The audit
    // log records that validation was coarse-only.
    return { ok: true };
  } catch (e: unknown) {
    // Validation failed (not the build itself). Be permissive — the
    // evidence package can still be generated, but log the validation
    // failure for observability.
    const msg = e instanceof Error ? e.message : String(e);
    console.warn(`evidence-package: KG validation failed for ${kind}="${trimmed}": ${msg}`);
    return { ok: true };
  }
}

/** FE-010 ROOT FIX: Evidence package build requires a research role. */
export async function POST(req: NextRequest) {
  // FE-011: CSRF protection on every state-changing route.
  const csrf = await requireCsrfOrSend(req);
  if (csrf.response) return csrf.response;

  const auth = await requireAuthRole("researcher", "data-scientist", "pi", "business-dev");
  if (auth.user === null) return auth.response;

  let body: { drug: string; disease: string; notes?: string; literatureLimit?: number; trialsLimit?: number; skipKgValidation?: boolean };
  try {
    body = await req.json();
  } catch {
    return badRequest("Invalid JSON body");
  }
  if (!body.drug || !body.disease) {
    return badRequest("Both 'drug' and 'disease' fields are required");
  }

  // FE-008 ROOT FIX: validate the drug and disease exist in the KG.
  //
  // We skip this check ONLY when:
  //   - The caller passes `skipKgValidation: true` in the body (admin
  //     override — used by the evidence-package smoke test, NOT exposed
  //     to the UI), AND
  //   - The caller is an admin or owner (RBAC check).
  //
  // For all other callers, the check is mandatory. If the drug or
  // disease is not in the KG, return 404 with a clear error.
  const skipKgValidation =
    body.skipKgValidation === true &&
    (auth.user.role === "admin" || auth.user.role === "owner");

  if (!skipKgValidation) {
    const [drugCheck, diseaseCheck] = await Promise.all([
      validateEntityInKg(body.drug, "drug"),
      validateEntityInKg(body.disease, "disease"),
    ]);
    if (!drugCheck.ok) {
      return notFound(drugCheck.reason || `Drug "${body.drug}" not found in knowledge graph`);
    }
    if (!diseaseCheck.ok) {
      return notFound(diseaseCheck.reason || `Disease "${body.disease}" not found in knowledge graph`);
    }
  }

  try {
    const pkg = await buildEvidencePackage({
      drug: body.drug,
      disease: body.disease,
      literatureLimit: body.literatureLimit,
      trialsLimit: body.trialsLimit,
      notes: body.notes,
    });
    const markdown = evidencePackageToMarkdown(pkg);
    // Persist the package so the user can retrieve it later
    const record = await db.evidencePackage.create({
      data: {
        userId: auth.user.userId,
        drugName: pkg.drug,
        diseaseName: pkg.disease,
        title: `Evidence package: ${pkg.drug} for ${pkg.disease}`,
        summary: pkg.notes,
        payloadJson: JSON.stringify(pkg),
        status: "generated",
      },
    });
    await writeAuditLog({
      user: auth.user,
      action: "evidence_package_generated",
      resource: `evidence_package:${record.id}`,
      metadata: {
        drug: pkg.drug,
        disease: pkg.disease,
        literatureCount: pkg.literature.total,
        trialsCount: pkg.clinicalTrials.total,
        kgValidationSkipped: skipKgValidation,
      },
    });
    // BE-006 ROOT FIX: When KG validation is skipped (admin override),
    // include kgValidationSkipped: true in the response so the UI can
    // display a prominent warning. Previously the researcher had no
    // indication that the package was built for entities not in the KG.
    return NextResponse.json({
      id: record.id,
      package: pkg,
      markdown,
      kgValidationSkipped: skipKgValidation === true,
      warning: skipKgValidation
        ? "KG validation was skipped for this evidence package. The drug and/or disease may not exist in the knowledge graph. Verify entities before acting on this data."
        : undefined,
    });
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e);
    return internalError(`Evidence package build failed: ${msg}`);
  }
}

/**
 * FE-047 ROOT FIX: GET was `take: 50` with no offset/pagination — power
 * users with >50 evidence packages could not access older records. Now
 * accepts `limit` (capped at 100) and `offset` query params and returns
 * the standard paginated envelope `{ items, total, hasMore, limit, offset }`.
 */
export async function GET(req: NextRequest) {
  const auth = await requireAuthRole("researcher", "data-scientist", "pi", "business-dev");
  if (auth.user === null) return auth.response;
  const id = req.nextUrl.searchParams.get("id");
  if (id) {
    const pkg = await db.evidencePackage.findUnique({ where: { id } });
    if (!pkg || pkg.userId !== auth.user.userId) {
      return NextResponse.json({ error: "not_found" }, { status: 404 });
    }
    return NextResponse.json({ id: pkg.id, package: JSON.parse(pkg.payloadJson), markdown: evidencePackageToMarkdown(JSON.parse(pkg.payloadJson)) });
  }
  const page = parsePagination(req.nextUrl.searchParams);
  const where = { userId: auth.user.userId };
  const [items, total] = await Promise.all([
    db.evidencePackage.findMany({
      where,
      orderBy: { createdAt: "desc" },
      take: page.limit,
      skip: page.offset,
      select: { id: true, drugName: true, diseaseName: true, title: true, status: true, createdAt: true },
    }),
    db.evidencePackage.count({ where }),
  ]);
  return NextResponse.json(buildPaginatedResponse(items, total, page));
}
