import { NextRequest, NextResponse } from "next/server";
import { db } from "@/lib/db";
import {
  getAuthenticatedUser,
  hashPassword,
  verifyPassword,
  validatePasswordPolicy,
} from "@/lib/auth/server";
import { badRequest, internalError, writeAuditLog } from "@/lib/api-helpers";

/**
 * POST /api/auth/password
 * Body: { currentPassword: string, newPassword: string }
 *
 * Verifies the current password, validates the new password against the
 * policy, and updates the user's passwordHash. Returns 200 on success.
 */
export async function POST(req: NextRequest) {
  const user = await getAuthenticatedUser();
  if (!user) {
    return NextResponse.json({ error: "unauthorized", message: "Authentication required" }, { status: 401 });
  }

  let body: { currentPassword?: string; newPassword?: string };
  try {
    body = await req.json();
  } catch {
    return badRequest("Invalid JSON body");
  }

  const currentPassword = body.currentPassword || "";
  const newPassword = body.newPassword || "";

  if (!currentPassword || !newPassword) {
    return badRequest("Both currentPassword and newPassword are required.");
  }

  const policy = validatePasswordPolicy(newPassword);
  if (!policy.ok) {
    return badRequest(policy.reason || "New password does not meet policy.");
  }

  const dbUser = await db.user.findUnique({ where: { id: user.userId } });
  if (!dbUser) {
    return NextResponse.json({ error: "not_found", message: "User not found" }, { status: 404 });
  }

  const ok = await verifyPassword(currentPassword, dbUser.passwordHash);
  if (!ok) {
    return NextResponse.json(
      { error: "invalid_credentials", message: "Current password is incorrect." },
      { status: 403 }
    );
  }

  try {
    const newHash = await hashPassword(newPassword);
    await db.user.update({
      where: { id: user.userId },
      data: { passwordHash: newHash },
    });
    await writeAuditLog({
      user,
      action: "password_change",
      resource: user.userId,
    });
    return NextResponse.json({ ok: true, message: "Password updated." });
  } catch (e) {
    console.error("Password update failed:", e);
    return internalError("Failed to update password.");
  }
}
