import { NextRequest, NextResponse } from "next/server";
import { requireAuth, notFound, requireCsrfOrSend } from "@/lib/api-helpers";
import { createHypothesis } from "@/lib/services/projects";
import { db } from "@/lib/db";

/**
 * POST /api/projects/[id]/hypotheses
 * Body: { title: string; drugName: string; diseaseName: string; notes?: string }
 *
 * FE-016 ROOT FIX (Teammate 15, v143 — REST path semantics):
 *
 * The previous code defined the POST handler in
 * `frontend/src/app/api/projects/[id]/route.ts` — meaning addHypothesis
 * POSTed to `/api/projects/${projectId}` (the SAME path as getProject,
 * which is a GET). REST semantics say POST to /projects/{id} should be
 * "create a sub-resource of project {id}", but the URL didn't
 * disambiguate WHICH sub-resource (hypothesis? comment? member?).
 * A reader couldn't tell from the URL whether POST creates a hypothesis,
 * a comment, or some other sub-resource. The backend route handler had
 * to dispatch on body shape, not URL — making future API evolution
 * (e.g., adding POST /api/projects/{id}/members) ambiguous.
 *
 * ROOT FIX (this file):
 *   1. The POST handler is MOVED here from `[id]/route.ts`. The old
 *      file now only has GET (for getProject).
 *   2. The path is `/api/projects/[id]/hypotheses` — explicit, self-
 *      documenting, and consistent with `addComment`'s
 *      `/api/projects/[id]/comments` pattern.
 *   3. The api-client.ts `addHypothesis` method was updated to POST to
 *      this new path.
 *
 * The visibility + OrganizationMember.role checks (FE-017 from Team 13)
 * are preserved EXACTLY as they were in the old file — this is a
 * mechanical move of the POST handler, NOT a re-implementation. The
 * `PROJECT_WRITE_ROLES` constant was also moved here so the role
 * check still works.
 *
 * FE-011: CSRF protection applied to POST (unchanged).
 */

// OrganizationMember roles that are allowed to create hypotheses / mutate
// project content. Viewer and billing are read-only by design.
// FE-016: this constant was MOVED from `[id]/route.ts` when the POST
// handler was relocated here. The role set is unchanged.
const PROJECT_WRITE_ROLES = new Set([
  "owner",
  "admin",
  "member",
  "developer",
  "researcher",
  "data_scientist",
  "pi",
  "business_dev",
]);

export async function POST(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  // FE-011: CSRF protection on every state-changing route.
  const csrf = await requireCsrfOrSend(req);
  if (csrf.response) return csrf.response;

  const auth = await requireAuth();
  if (auth.user === null) return auth.response;
  const { id } = await params;
  const project = await db.project.findUnique({ where: { id } });
  if (!project) return notFound("Project not found");
  if (project.organizationId !== auth.user.orgId) {
    return NextResponse.json({ error: "forbidden" }, { status: 403 });
  }

  // FE-017 (Team 13): visibility check on write too — a non-owner cannot
  // add hypotheses to a private project they can't even see.
  if (project.visibility === "private") {
    const isOwner = project.ownerId === auth.user.userId;
    const isAdminOrOwner = auth.user.role === "admin" || auth.user.role === "owner";
    if (!isOwner && !isAdminOrOwner) {
      return NextResponse.json(
        { error: "forbidden", message: "This project is private to its owner." },
        { status: 403 }
      );
    }
  }

  // FE-017 (Team 13): OrganizationMember.role check — viewer and billing
  // are read-only by design and CANNOT create hypotheses.
  if (auth.user.role !== "owner") {
    const membership = await db.organizationMember.findFirst({
      where: { userId: auth.user.userId, organizationId: project.organizationId },
      select: { role: true },
    });
    if (!membership || !PROJECT_WRITE_ROLES.has(membership.role)) {
      return NextResponse.json(
        {
          error: "forbidden",
          message: `Your organization role (${membership?.role || "none"}) cannot modify project content.`,
        },
        { status: 403 }
      );
    }
  }

  let body: { title: string; drugName: string; diseaseName: string; notes?: string };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "bad_request", message: "Invalid JSON" }, { status: 400 });
  }
  if (!body.title || !body.drugName || !body.diseaseName) {
    return NextResponse.json({ error: "bad_request", message: "title, drugName, diseaseName required" }, { status: 400 });
  }
  const hyp = await createHypothesis({
    projectId: id,
    title: body.title,
    drugName: body.drugName,
    diseaseName: body.diseaseName,
    createdById: auth.user.userId,
    notes: body.notes,
  });
  return NextResponse.json(hyp, { status: 201 });
}
