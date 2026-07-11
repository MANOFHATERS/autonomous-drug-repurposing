import { NextRequest, NextResponse } from "next/server";
import { db } from "@/lib/db";
import { getAuthenticatedUser } from "@/lib/auth/server";
import { verifyTotp } from "@/lib/auth/totp";
import { badRequest, internalError, writeAuditLog } from "@/lib/api-helpers";

/**
 * POST /api/auth/2fa/verify
 * Body: { secret?: string, code: string }
 *
 * Confirms a 2FA enrollment. If the user is enrolling for the first time,
 * the client must send the `secret` returned by /api/auth/2fa/setup. We
 * verify the code, then persist `mfaSecret` and set `mfaEnabled = true`.
 *
 * If the user already has 2FA enabled and `secret` is omitted, this just
 * verifies the code without changing state (used for re-verification flows).
 */
export async function POST(req: NextRequest) {
  const user = await getAuthenticatedUser();
  if (!user) {
    return NextResponse.json({ error: "unauthorized", message: "Authentication required" }, { status: 401 });
  }

  let body: { secret?: string; code?: string };
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
      return NextResponse.json({ error: "not_found", message: "User not found" }, { status: 404 });
    }

    // Determine which secret to verify against.
    const secret = body.secret || dbUser.mfaSecret;
    if (!secret) {
      return badRequest("No 2FA secret available — call /api/auth/2fa/setup first.");
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
