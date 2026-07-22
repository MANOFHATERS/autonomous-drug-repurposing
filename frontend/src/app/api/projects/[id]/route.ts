import { NextRequest, NextResponse } from "next/server";
import { requireAuth, notFound } from "@/lib/api-helpers";
import { getProject } from "@/lib/services/projects";

/**
 * GET /api/projects/[id] — fetch a single project with its hypotheses,
 * comments, and activity feed.
 *
 * FE-016 ROOT FIX (Teammate 15, v143 — REST path semantics):
 *
 * The previous version of this file ALSO defined a POST handler for
 * creating hypotheses. That meant addHypothesis POSTed to
 * `/api/projects/${projectId}` — the SAME path as getProject (a GET).
 * REST semantics say POST to /projects/{id} should be "create a
 * sub-resource of project {id}", but the URL didn't disambiguate
 * WHICH sub-resource (hypothesis? comment? member?). A reader couldn't
 * tell from the URL whether POST creates a hypothesis, a comment, or
 * some other sub-resource. The backend route handler had to dispatch
 * on body shape, not URL — making future API evolution (e.g., adding
 * POST /api/projects/{id}/members) ambiguous.
 *
 * ROOT FIX:
 *   - The POST handler was MOVED to
 *     `frontend/src/app/api/projects/[id]/hypotheses/route.ts`.
 *   - addHypothesis now POSTs to `/api/projects/${projectId}/hypotheses`
 *     (consistent with addComment's `/api/projects/${projectId}/comments`).
 *   - This file now ONLY hosts the GET handler — single responsibility.
 *   - The `PROJECT_WRITE_ROLES` constant and the `createHypothesis`
 *     import were ALSO moved to the hypotheses route file (they were
 *     only used by the POST handler).
 *   - The `db` import was removed (only used by the POST handler).
 *
 * The GET handler is UNCHANGED — same visibility check, same auth,
 * same response shape. This is a mechanical relocation of POST, not a
 * re-implementation of GET.
 *
 * FE-017 (Team 13): visibility check on GET is preserved. Private
 * projects are only readable by the owner + admin/owner roles. "org"
 * and "public" projects are readable by any org member. BE-061 closed
 * a cross-org leak — "public" means "visible to any member of the
 * owning organization", NOT "visible to anyone on the internet".
 */

export async function GET(_req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const auth = await requireAuth();
  if (auth.user === null) return auth.response;
  const { id } = await params;
  const project = await getProject(id);
  if (!project) return notFound("Project not found");

  // BE-061 ROOT FIX: The previous "public" visibility check returned the
  // project to ANY authenticated user without verifying org membership.
  // This meant a user in Org A could read any "public" project in Org B,
  // potentially leaking competitor research data if "public" was set by
  // mistake. The "public" visibility now means "visible to any member of
  // the owning organization" — NOT "visible to anyone on the internet".
  // Cross-org visibility must be explicitly enabled via a separate
  // "published" visibility level (future feature). For now, ALL projects
  // require org membership to read.
  if (project.organizationId !== auth.user.orgId) {
    return NextResponse.json({ error: "forbidden", message: "Project belongs to a different organization." }, { status: 403 });
  }
  // BE-061: Additional check — "public" projects are readable by any org
  // member; "private" projects are only readable by the owner + admin/owner
  // roles; "org" projects are readable by any org member (same as public).
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
  return NextResponse.json(project);
}
