import { NextRequest, NextResponse } from "next/server";
import { db } from "@/lib/db";
import { hashPassword, validateEmail, validatePasswordPolicy, signAccessToken, rotateRefreshToken, setAuthCookies } from "@/lib/auth/server";
import { badRequest, internalError, writeAuditLog } from "@/lib/api-helpers";
import { checkIpRateLimit, recordIpAttempt } from "@/lib/auth/rate-limit";
import { Prisma } from "@prisma/client";
import { createHmac, randomBytes } from "crypto";

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

// ---------------------------------------------------------------------------
// FE-035: Email verification
// ---------------------------------------------------------------------------

/**
 * FE-035 ROOT FIX: Sign a 24-hour email-verification token.
 *
 * The token is a JWT signed with JWT_SECRET, containing the user's ID and
 * email. The user must click a link containing this token to verify their
 * email before they can log in.
 *
 * NOTE: The previous code set emailVerified=false on register but NEVER
 * sent a verification email (nodemailer was in package.json but never
 * imported). This is the root fix — we now generate a real token. The
 * actual email sending is delegated to a configurable EmailService (SES,
 * SendGrid, etc.) — if no email service is configured, we still create
 * the token and return it in the API response (DEV MODE ONLY) so the
 * developer can click the link manually. In production, the token MUST
 * be delivered via email and NEVER returned in the API response.
 */
function signEmailVerificationToken(userId: string, email: string): string {
  const jwt = require("jsonwebtoken");
  const secret = process.env.JWT_SECRET;
  if (!secret || secret.length < 32) {
    if (process.env.NODE_ENV === "production") {
      throw new Error("JWT_SECRET must be set in production.");
    }
    // Dev fallback — same as auth/server.ts.
    return jwt.sign(
      { sub: userId, email, type: "email_verify" },
      "dev-only-insecure-secret-change-me-MINIMUM-32-CHARS-FOR-HS256!!",
      { issuer: "drugos", expiresIn: "24h", algorithm: "HS256" }
    );
  }
  return jwt.sign(
    { sub: userId, email, type: "email_verify" },
    secret,
    { issuer: "drugos", expiresIn: "24h", algorithm: "HS256" }
  );
}

/**
 * Send the verification email. In production, this MUST use a real email
 * service (SES, SendGrid, Postmark). In dev mode, we log the link to
 * stderr so the developer can click it.
 *
 * The email service is configured via EMAIL_SERVICE_URL env var. If not
 * set, we fall back to dev-mode stderr logging (only when NODE_ENV !==
 * 'production').
 */
async function sendVerificationEmail(email: string, token: string, userId: string): Promise<void> {
  const baseUrl = process.env.NEXT_PUBLIC_BASE_URL || "http://localhost:3000";
  const verifyUrl = `${baseUrl}/auth/verify-email?token=${token}`;

  const emailServiceUrl = process.env.EMAIL_SERVICE_URL;
  if (emailServiceUrl) {
    // Production path: POST to the email service (SES, SendGrid, etc.).
    // The service is responsible for delivering the email. We do NOT
    // handle SMTP directly — that's the email service's job.
    try {
      const res = await fetch(`${emailServiceUrl.replace(/\/$/, "")}/send`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          to: email,
          subject: "DrugOS — Verify your email",
          text: `Welcome to DrugOS.\n\nClick the link below to verify your email:\n${verifyUrl}\n\nThis link expires in 24 hours.\n\nIf you did not create an account, ignore this email.`,
          html: `<p>Welcome to DrugOS.</p><p>Click the link below to verify your email:</p><p><a href="${verifyUrl}">${verifyUrl}</a></p><p>This link expires in 24 hours.</p><p>If you did not create an account, ignore this email.</p>`,
          metadata: { userId, type: "email_verification" },
        }),
      });
      if (!res.ok) {
        console.error("[EMAIL-SERVICE] Failed to send verification email:", res.status, await res.text());
      }
    } catch (e) {
      console.error("[EMAIL-SERVICE] Error sending verification email:", e);
    }
    return;
  }

  // Dev mode: log to stderr. NEVER do this in production.
  if (process.env.NODE_ENV !== "production") {
    console.warn("\n[DEV EMAIL VERIFICATION LINK]");
    console.warn(`To: ${email}`);
    console.warn(`URL: ${verifyUrl}`);
    console.warn("[/DEV EMAIL VERIFICATION LINK]\n");
  }
}

export async function POST(req: NextRequest) {
  // FE-035 ROOT FIX: IP-based rate limiting on registration. Without this,
  // an attacker could spam account creation indefinitely, filling the User
  // table with garbage accounts. We reuse the same checkIpRateLimit /
  // recordIpAttempt helpers from the login rate-limiter.
  const ipRate = checkIpRateLimit(req);
  if (ipRate.blocked) {
    return NextResponse.json(
      {
        error: "rate_limited",
        message: `Too many registration attempts from this IP. Try again in ${Math.ceil(ipRate.retryAfterSeconds / 60)} minute(s).`,
        retryAfter: ipRate.retryAfterSeconds,
      },
      {
        status: 429,
        headers: { "Retry-After": String(ipRate.retryAfterSeconds) },
      }
    );
  }
  recordIpAttempt(req);

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
  const role: AllowedSelfRegRole = (ALLOWED_ROLES_SELF_REG as readonly string[]).includes(requestedRole)
    ? (requestedRole as AllowedSelfRegRole)
    : "researcher";

  const passwordHash = await hashPassword(password);

  // FE-036 ROOT FIX: TOCTOU race on email uniqueness.
  //
  // The previous code did:
  //   const existing = await db.user.findUnique({ where: { email } });
  //   if (existing) return 409;
  //   // ... create user ...
  //
  // Between the findUnique and the create, another request could insert a
  // user with the same email. The create would then fail with a Prisma
  // unique-constraint error (P2002), thrown as an unhandled exception →
  // 500 error. The user saw "internal_error" instead of "email_taken".
  //
  // Root fix: wrap the create in try/catch. Catch Prisma P2002
  // (unique constraint violation) and return 409 email_taken. The
  // pre-check (findUnique) is kept as a fast-path to avoid the overhead
  // of a transaction on the common case, but the catch is the actual
  // race-safety mechanism.
  const existing = await db.user.findUnique({ where: { email } });
  if (existing) {
    return NextResponse.json(
      { error: "email_taken", message: "An account with this email already exists" },
      { status: 409 }
    );
  }

  // Create the user, an organization, and link them as owner.
  // We use a transaction so partial-failures don't leave dangling rows.
  // FE-036: the transaction is wrapped in try/catch to handle the race
  // where another request inserts the same email between our findUnique
  // and the create. In that case, Prisma throws P2002 — we catch it
  // and return 409.
  let user: { user: { id: string; email: string; name: string | null; role: string; title: string | null; bio: string | null }; orgId: string };
  try {
    user = await db.$transaction(async (tx) => {
      const u = await tx.user.create({
        data: {
          email,
          passwordHash,
          name,
          role,
          title,
          bio,
          // FE-035: emailVerified starts false. The user must click the
          // verification link before they can log in. See /api/auth/verify-email.
          emailVerified: false,
        },
      });
      const slugBase = (organizationName || name || "workspace").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 30);
      const slug = `${slugBase}-${randomBytes(3).toString("hex")}`;
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
  } catch (e) {
    // FE-036: Catch Prisma unique-constraint violation (P2002) on the
    // email field. This is the race we couldn't prevent with the
    // findUnique pre-check.
    if (e instanceof Prisma.PrismaClientKnownRequestError && e.code === "P2002") {
      const target = (e.meta as { target?: string[] } | undefined)?.target?.join(", ") || "email";
      if (target.includes("email")) {
        return NextResponse.json(
          { error: "email_taken", message: "An account with this email already exists" },
          { status: 409 }
        );
      }
      // Some other unique constraint (e.g. org slug collision). The slug
      // has a random suffix so this is extremely unlikely, but handle it.
      return NextResponse.json(
        { error: "conflict", message: `A record with this ${target} already exists.` },
        { status: 409 }
      );
    }
    // Unexpected error — rethrow to be caught by the outer catch.
    throw e;
  }

  // FE-035: Issue a verification token and "send" the email (in dev mode
  // this logs to stderr; in production it POSTs to EMAIL_SERVICE_URL).
  const verifyToken = signEmailVerificationToken(user.user.id, user.user.email);
  await sendVerificationEmail(user.user.email, verifyToken, user.user.id);

  // FE-035: We NO LONGER issue access+refresh tokens on registration.
  // The user must verify their email first. The previous code issued
  // tokens immediately — an attacker could register with someone else's
  // email and immediately use the platform as that person.
  //
  // We DO write an audit log (critical — registration is a security event).
  const audit = await writeAuditLog({
    user: { userId: user.user.id, email: user.user.email, role: user.user.role, orgId: user.orgId },
    action: "register",
    resource: `user:${user.user.id}`,
    metadata: { role, emailVerificationRequired: true },
    critical: true,
  });
  if (!audit.ok) {
    // Registration succeeded but audit failed. We can't undo the
    // registration (the user + org + subscription were created in a
    // transaction). We mark the account as pending_approval so an
    // admin can investigate, and return an error.
    await db.user.update({
      where: { id: user.user.id },
      data: { status: "pending_approval" },
    });
    return internalError("Account created but audit log failed. Account is pending admin approval.");
  }

  return NextResponse.json(
    {
      user: {
        id: user.user.id,
        email: user.user.email,
        name: user.user.name,
        role: user.user.role,
        title: user.user.title,
        bio: user.user.bio,
        emailVerified: false,
      },
      organizationId: user.orgId,
      // FE-035: Tell the client to redirect to a "check your email" page.
      // The user MUST verify their email before they can log in.
      verificationRequired: true,
      message: "Account created. Check your email for a verification link to activate your account.",
      // In dev mode (no EMAIL_SERVICE_URL), include the token in the
      // response so the developer can click the link from the console.
      // In production, this is NEVER included.
      ...(process.env.NODE_ENV !== "production" && !process.env.EMAIL_SERVICE_URL
        ? { devVerifyToken: verifyToken }
        : {}),
    },
    { status: 201 }
  );
}
