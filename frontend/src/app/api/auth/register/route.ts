import { NextRequest, NextResponse } from "next/server";
import { db } from "@/lib/db";
import { hashPassword, validateEmail, validatePasswordPolicy, signAccessToken, rotateRefreshToken, setAuthCookies } from "@/lib/auth/server";
import { badRequest, internalError, writeAuditLog } from "@/lib/api-helpers";

interface RegisterBody {
  email: string;
  password: string;
  name?: string;
  organizationName?: string;
  role?: string;
  title?: string;
  bio?: string;
}

// FE-006 ROOT FIX: "admin" and "owner" removed from the self-registration
// allowlist. The previous code let ANY unauthenticated user POST
// {"role":"admin"} to /api/auth/register and get a User record with
// role="admin" — which then passed the requireAdmin check (admin || owner).
// That's a textbook privilege-escalation vulnerability: full admin access
// to /api/admin/users (cross-tenant PII), /api/audit-logs, etc.
//
// Self-registration is now restricted to non-privileged roles. The only way
// to become an admin/owner is to be PROMOTED by an existing admin via
// PATCH /api/admin/users (which validates the role against ALLOWED_ROLES_ADMIN).
export const ALLOWED_ROLES_SELF_REG = [
  "researcher",
  "data-scientist",
  "pi",
  "business-dev",
  "developer",
  "viewer",
] as const;
type AllowedSelfRegRole = (typeof ALLOWED_ROLES_SELF_REG)[number];

// Roles that an EXISTING admin/owner can promote a user to. Includes admin
// and owner because that's the promotion path — but this list is ONLY
// consulted from the admin endpoint, never from self-registration.
export const ALLOWED_ROLES_ADMIN = [
  "researcher",
  "data-scientist",
  "pi",
  "business-dev",
  "developer",
  "viewer",
  "billing",
  "admin",
  "owner",
] as const;
type AllowedAdminRole = (typeof ALLOWED_ROLES_ADMIN)[number];

export const ALLOWED_USER_STATUSES = [
  "active",
  "suspended",
  "pending_approval",
] as const;
type AllowedUserStatus = (typeof ALLOWED_USER_STATUSES)[number];

export function isValidAdminRole(role: unknown): role is AllowedAdminRole {
  return typeof role === "string" && (ALLOWED_ROLES_ADMIN as readonly string[]).includes(role);
}

export function isValidUserStatus(status: unknown): status is AllowedUserStatus {
  return typeof status === "string" && (ALLOWED_USER_STATUSES as readonly string[]).includes(status);
}

export async function POST(req: NextRequest) {
  let body: RegisterBody;
  try {
    body = await req.json();
  } catch {
    return badRequest("Invalid JSON body");
  }
  const email = (body.email || "").trim().toLowerCase();
  const password = body.password || "";
  const name = (body.name || "").trim();
  const organizationName = (body.organizationName || "").trim();
  const requestedRole = (body.role || "researcher").trim().toLowerCase();
  const title = (body.title || "").trim() || null;
  const bio = (body.bio || "").trim() || null;

  if (!validateEmail(email)) return badRequest("A valid email is required");
  const passwordCheck = validatePasswordPolicy(password);
  if (!passwordCheck.ok) return badRequest(passwordCheck.reason || "Password does not meet policy");
  if (!name) return badRequest("Name is required");

  // Validate the role — must be one of the self-registration-allowed values.
  // FE-006: admin/owner are NOT in ALLOWED_ROLES_SELF_REG, so an attacker
  // POSTing {"role":"admin"} gets the default "researcher" — NOT admin.
  const role: AllowedSelfRegRole = (ALLOWED_ROLES_SELF_REG as readonly string[]).includes(requestedRole)
    ? (requestedRole as AllowedSelfRegRole)
    : "researcher";

  const existing = await db.user.findUnique({ where: { email } });
  if (existing) {
    return NextResponse.json({ error: "email_taken", message: "An account with this email already exists" }, { status: 409 });
  }

  const passwordHash = await hashPassword(password);

  // Create the user, an organization, and link them as owner.
  // We use a transaction so partial-failures don't leave dangling rows.
  const user = await db.$transaction(async (tx) => {
    const u = await tx.user.create({
      data: {
        email,
        passwordHash,
        name,
        // The user's account-level role controls UI access (admin sees admin
        // pages, researcher does not, etc.). Organizationally they are still
        // the owner of their workspace.
        role,
        title,
        bio,
        emailVerified: false,
      },
    });
    const slugBase = (organizationName || name).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 30);
    // Append a short random suffix to keep the slug unique — otherwise two
    // users registering with the same org name (e.g., "John's Workspace")
    // would collide on the unique slug constraint.
    const slug = `${slugBase}-${Math.random().toString(36).slice(2, 8)}`;
    const org = await tx.organization.create({
      data: {
        name: organizationName || `${name}'s Workspace`,
        slug,
        plan: "free",
        seats: 1,
      },
    });
    await tx.organizationMember.create({
      data: {
        userId: u.id,
        organizationId: org.id,
        role: "owner",
      },
    });
    await tx.subscription.create({
      data: {
        organizationId: org.id,
        plan: "free",
        status: "active",
        seats: 1,
        currentPeriodStart: new Date(),
        currentPeriodEnd: new Date(Date.now() + 365 * 24 * 60 * 60 * 1000),
      },
    });
    return { user: u, orgId: org.id };
  });

  const tokens = await rotateRefreshToken(user.user.id);
  const access = signAccessToken({
    userId: user.user.id,
    email: user.user.email,
    role: user.user.role,
    orgId: user.orgId,
  });
  await setAuthCookies(access, tokens.refresh);
  await writeAuditLog({
    user: { userId: user.user.id, email: user.user.email, role: user.user.role, orgId: user.orgId },
    action: "register",
    resource: `user:${user.user.id}`,
    metadata: { role },
  });

  return NextResponse.json({
    user: {
      id: user.user.id,
      email: user.user.email,
      name: user.user.name,
      role: user.user.role,
      title: user.user.title,
      bio: user.user.bio,
    },
    organizationId: user.orgId,
  }, { status: 201 });
}
