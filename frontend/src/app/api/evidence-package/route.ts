import { NextRequest, NextResponse } from "next/server";
import { buildEvidencePackage, evidencePackageToMarkdown } from "@/lib/services/evidence-package";
import { requireAuthRole, badRequest, internalError, writeAuditLog } from "@/lib/api-helpers";
import { parsePagination, buildPaginatedResponse } from "@/lib/pagination";
import { db } from "@/lib/db";

/** FE-010 ROOT FIX: Evidence package build requires a research role. */
export async function POST(req: NextRequest) {
  const auth = await requireAuthRole("researcher", "data-scientist", "pi", "business-dev");
  if (auth.user === null) return auth.response;

  let body: { drug: string; disease: string; notes?: string; literatureLimit?: number; trialsLimit?: number };
  try {
    body = await req.json();
  } catch {
    return badRequest("Invalid JSON body");
  }
  if (!body.drug || !body.disease) {
    return badRequest("Both 'drug' and 'disease' fields are required");
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
      metadata: { drug: pkg.drug, disease: pkg.disease, literatureCount: pkg.literature.total, trialsCount: pkg.clinicalTrials.total },
    });
    return NextResponse.json({ id: record.id, package: pkg, markdown });
  } catch (e: any) {
    return internalError(`Evidence package build failed: ${e.message}`);
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
