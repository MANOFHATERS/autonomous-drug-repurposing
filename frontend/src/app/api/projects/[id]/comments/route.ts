import { NextRequest, NextResponse } from "next/server";
import { requireAuth, notFound, badRequest } from "@/lib/api-helpers";
import { addComment } from "@/lib/services/projects";
import { db } from "@/lib/db";

/**
 * POST /api/projects/[id]/comments
 * Body: { body: string }   (authorName is intentionally NOT accepted)
 *
 * FE-073 ROOT FIX: Comment impersonation.
 *
 * Previously the route accepted `body.authorName` from the client and
 * passed it through to addComment(). A user could post a comment
 * attributed to "Dr. Smith (PI)" by simply sending that string in the
 * request body — the actual commenter's identity was ignored.
 *
 * Root fix: the route now IGNORES any client-supplied authorName and
 * passes only auth.user.userId to addComment(). The service derives
 * authorName from the User table (User.name || User.email) so attribution
 * is always truthful and audit-traceable.
 */
export async function POST(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const auth = await requireAuth();
  if (auth.user === null) return auth.response;
  const { id } = await params;
  const project = await db.project.findUnique({ where: { id } });
  if (!project) return notFound("Project not found");
  if (project.organizationId !== auth.user.orgId) {
    return NextResponse.json({ error: "forbidden" }, { status: 403 });
  }

  let body: { body?: string; authorName?: string };
  try {
    body = await req.json();
  } catch {
    return badRequest("Invalid JSON body");
  }

  // FE-073: `authorName` from the client is intentionally ignored. We do
  // NOT echo it back even if the client insists — the service derives it
  // from the User table.
  if (!body.body || body.body.trim().length < 1) {
    return badRequest("Comment body required");
  }
  // Hard cap comment length to prevent storage abuse.
  const commentBody = body.body.trim().slice(0, 10000);

  try {
    const comment = await addComment(id, auth.user.userId, commentBody);
    return NextResponse.json(comment, { status: 201 });
  } catch (e: any) {
    // addComment throws if the user lookup fails — surface as 401 since the
    // auth token resolved but the user no longer exists in the DB.
    if (/not found/i.test(e.message)) {
      return NextResponse.json(
        { error: "unauthorized", message: "Authenticated user no longer exists." },
        { status: 401 }
      );
    }
    console.error("addComment failed:", e);
    return NextResponse.json(
      { error: "internal_error", message: "Failed to add comment." },
      { status: 500 }
    );
  }
}
