import { NextResponse } from "next/server";
import { db } from "@/lib/db";
import { getAuthenticatedUser } from "@/lib/auth/server";
import { internalError, writeAuditLog } from "@/lib/api-helpers";

/**
 * POST /api/auth/2fa/disable
 *
 * Disables 2FA on the authenticated user's account. Clears `mfaSecret`
 * and sets `mfaEnabled = false`.
 *
 * In a production deployment you would re-require the user's password or
 * a current TOTP code before disabling; for this development build we
 * trust the authenticated session.
 */
export async function POST() {
  const user = await getAuthenticatedUser();
  if (!user) {
    return NextResponse.json({ error: "unauthorized", message: "Authentication required" }, { status: 401 });
  }

  try {
    await db.user.update({
      where: { id: user.userId },
      data: { mfaSecret: null, mfaEnabled: false },
    });
    await writeAuditLog({
      user,
      action: "2fa_disable",
      resource: user.userId,
    });
    return NextResponse.json({ ok: true, enabled: false });
  } catch (e) {
    console.error("2FA disable failed:", e);
    return internalError("Failed to disable 2FA.");
  }
}
