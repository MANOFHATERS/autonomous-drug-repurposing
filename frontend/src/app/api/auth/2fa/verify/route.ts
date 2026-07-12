import { NextRequest, NextResponse } from "next/server";
import { db } from "@/lib/db";
import { getAuthenticatedUser } from "@/lib/auth/server";
import { verifyTotpWithReplayCheck } from "@/lib/auth/totp";
import { badRequest, internalError, writeAuditLog, requireCsrfOrSend } from "@/lib/api-helpers";

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
 *
 * FE-033 ROOT FIX: TOTP replay protection. The previous code used
 * `verifyTotp` which accepts any matching code in the ±30s window with
 * NO tracking of used codes — an attacker who phished a code could
 * replay it within 60s. Now we use `verifyTotpWithReplayCheck` which
 * rejects any code whose counter is <= the user's lastTotpCounter.
 */
export async function POST(req: NextRequest) {
  // FE-011: CSRF protection on every state-changing route.
  const csrf = await requireCsrfOrSend(req);
  if (csrf.response) return csrf.response;

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

    // FE-033: Replay-protected TOTP verification.
    const result = verifyTotpWithReplayCheck(secret, code, dbUser.lastTotpCounter);
    if (!result.ok) {
      const message =
        result.reason === "replayed"
          ? "This code has already been used. Wait for the next 30-second window and try again."
          : "Invalid 6-digit code. Try again.";
      return NextResponse.json(
        { error: result.reason === "replayed" ? "code_replayed" : "invalid_code", message },
        { status: 400 }
      );
    }

    // Atomically update lastTotpCounter ONLY if no concurrent verification
    // has already advanced it past our counter. This prevents a race where
    // two concurrent requests both verify the same code and both succeed.
    // The `updateMany` with `where: { lastTotpCounter: { lt: result.counter } }`
    // ensures only one of the two requests actually persists the update.
    // For first-time enrollment (lastTotpCounter is null), we use a direct
    // update since there's no race window to worry about (the user just
    // set up 2FA and hasn't verified anything yet).
    if (dbUser.lastTotpCounter === null) {
      await db.user.update({
        where: { id: user.userId },
        data: { lastTotpCounter: result.counter },
      });
    } else {
      await db.user.updateMany({
        where: { id: user.userId, lastTotpCounter: { lt: result.counter } },
        data: { lastTotpCounter: result.counter },
      });
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
