import { NextResponse } from "next/server";
import { db } from "@/lib/db";
import { getAuthenticatedUser } from "@/lib/auth/server";
import { generateTotpSecret, buildOtpAuthUri } from "@/lib/auth/totp";
import { internalError } from "@/lib/api-helpers";

/**
 * POST /api/auth/2fa/setup
 *
 * Generates a brand-new TOTP secret for the authenticated user and returns
 * it (along with an otpauth:// URI) so the client can display a QR code.
 *
 * The secret is NOT persisted yet — the user must call /api/auth/2fa/verify
 * with a valid 6-digit code from their authenticator app to confirm they
 * have stored it. Only then do we mark `mfaEnabled = true` and persist
 * `mfaSecret`.
 *
 * To avoid persisting half-finished enrollments, we return the secret in
 * the response and let the client send it back in the /verify call.
 */
export async function POST() {
  const user = await getAuthenticatedUser();
  if (!user) {
    return NextResponse.json({ error: "unauthorized", message: "Authentication required" }, { status: 401 });
  }

  try {
    const secret = generateTotpSecret();
    const uri = buildOtpAuthUri({
      issuer: "DrugOS",
      account: user.email,
      secret,
    });
    return NextResponse.json({ secret, otpauthUri: uri });
  } catch (e) {
    console.error("2FA setup failed:", e);
    return internalError("Failed to start 2FA enrollment.");
  }
}
