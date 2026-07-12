import { NextRequest, NextResponse } from "next/server";
import { db } from "@/lib/db";
import { getAuthenticatedUser } from "@/lib/auth/server";
import { verifyTotp } from "@/lib/auth/totp";
import { badRequest, internalError, writeAuditLog } from "@/lib/api-helpers";
import { verify2faSetupToken } from "@/lib/auth/two-factor-setup-token";

/**
 * POST /api/auth/2fa/verify
 * Body: { secret?: string, code: string, setupToken?: string }
 *
 * Confirms a 2FA enrollment. If the user is enrolling for the first time,
 * the client must send the `secret` AND `setupToken` returned by
 * /api/auth/2fa/setup. We verify the code, validate the one-time setup
 * token (FE-071), then persist `mfaSecret` and set `mfaEnabled = true`.
 *
 * FE-071 ROOT FIX: The setup token is now MANDATORY for first-time
 * enrollment. If `secret` is provided (first-time enrollment) but
 * `setupToken` is missing or invalid, the request is rejected with 403.
 * This closes the XSS-driven "steal the secret, enroll 2FA yourself"
 * attack vector.
 *
 * If the user already has 2FA enabled and `secret` is omitted, this just
 * verifies the code against the persisted secret without changing state
 * (used for re-verification flows). No setup token is needed in that case
 * because no new secret is being persisted.
 */
export async function POST(req: NextRequest) {
  const user = await getAuthenticatedUser();
  if (!user) {
    return NextResponse.json({ error: "unauthorized", message: "Authentication required" }, { status: 401 });
  }

  let body: { secret?: string; code?: string; setupToken?: string };
  try {
    body = await req.json();
  } catch {
    return badRequest("Invalid JSON body");
  }

  const code = (body.code || "").trim();
  if (!/^\d{6}$/.test(code)) {
    return badRequest("A 6-digit code is required.");
  }

  try {
    const dbUser = await db.user.findUnique({ where: { id: user.userId } });
    if (!dbUser) {
      // FE-068 (consistency): treat deleted user as 401, not 404.
      return NextResponse.json({ error: "unauthorized", message: "User not found" }, { status: 401 });
    }

    // Determine which secret to verify against.
    const secret = body.secret || dbUser.mfaSecret;
    if (!secret) {
      return badRequest("No 2FA secret available — call /api/auth/2fa/setup first.");
    }

    // FE-071 ROOT FIX: For first-time enrollment (secret provided by the
    // client, user does NOT yet have mfaEnabled), require a valid one-time
    // setup token. This proves the secret came from a legitimate /setup
    // call bound to this user's session, not from an XSS-driven secret
    // theft + replay.
    const isInitialEnrollment = !dbUser.mfaEnabled && !!body.secret;
    if (isInitialEnrollment) {
      // TS narrowing: body.secret is guaranteed truthy here (the
      // isInitialEnrollment check above ensures it), but TS can't follow
      // the boolean variable — extract to a const so the type narrows.
      const enrollmentSecret: string = body.secret!;
      const enrollmentSetupToken: string | undefined = body.setupToken;
      if (!enrollmentSetupToken) {
        return NextResponse.json(
          {
            error: "setup_token_required",
            message:
              "First-time 2FA enrollment requires the setupToken returned by /api/auth/2fa/setup.",
          },
          { status: 403 }
        );
      }
      const verifyResult = verify2faSetupToken(
        user.userId,
        enrollmentSecret,
        enrollmentSetupToken
      );
      if (!verifyResult.ok) {
        return NextResponse.json(
          {
            error: "invalid_setup_token",
            message: `Setup token rejected: ${verifyResult.reason}. Re-run /api/auth/2fa/setup.`,
          },
          { status: 403 }
        );
      }
    }

    if (!verifyTotp(secret, code)) {
      return NextResponse.json(
        { error: "invalid_code", message: "Invalid 6-digit code. Try again." },
        { status: 400 }
      );
    }

    // If enrolling for the first time, persist the secret.
    if (!dbUser.mfaEnabled) {
      await db.user.update({
        where: { id: user.userId },
        data: { mfaSecret: secret, mfaEnabled: true },
      });
      await writeAuditLog({
        user,
        action: "2fa_enable",
        resource: user.userId,
      });
    }

    return NextResponse.json({ ok: true, enabled: true });
  } catch (e) {
    console.error("2FA verify failed:", e);
    return internalError("Failed to verify 2FA code.");
  }
}
