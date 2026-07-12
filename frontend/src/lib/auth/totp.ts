/**
 * TOTP (Time-based One-Time Password) helpers — RFC 6238.
 *
 * Uses only Node's built-in `crypto` so we don't need an external dependency.
 * Compatible with Google Authenticator, 1Password, Authy, etc.
 *
 * Also provides `issueMfaTicket` / `verifyMfaTicket` — short-lived JWTs that
 * encode "the user has entered a correct password but has not yet completed
 * 2FA". Used by the FE-004 root fix so that login with 2FA enabled does not
 * issue session tokens until the TOTP code is verified.
 */

import { createHmac, randomBytes, timingSafeEqual } from "crypto";
import jwt from "jsonwebtoken";
// FE-042 ROOT FIX: import the shared JWT secret resolver from auth/server.ts
// so there is a SINGLE source of truth for JWT_SECRET handling. The previous
// code had a divergent `getJwtSecret()` here that returned "" in dev (non-test)
// mode, causing `issueMfaTicket` to throw "FATAL: JWT_SECRET is not set" and
// breaking 2FA enrollment entirely in dev. The shared `resolveJwtSecret`
// always returns a usable secret in dev (loudly-logged dev-only fallback) and
// throws in prod if the env var is missing — which is the correct behavior.
import { resolveJwtSecret, resolvePreviousJwtSecret } from "./server";

const BASE32_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";

/** Encode a Buffer to a base32 string (RFC 4648). */
export function base32Encode(buf: Buffer): string {
  let bits = 0;
  let value = 0;
  let output = "";
  for (const byte of buf) {
    value = (value << 8) | byte;
    bits += 8;
    while (bits >= 5) {
      output += BASE32_ALPHABET[(value >>> (bits - 5)) & 31];
      bits -= 5;
    }
  }
  if (bits > 0) {
    output += BASE32_ALPHABET[(value << (5 - bits)) & 31];
  }
  return output;
}

/** Decode a base32 string to a Buffer. Lowercase and whitespace are tolerated. */
export function base32Decode(s: string): Buffer {
  const cleaned = s.replace(/[\s=]/g, "").toUpperCase();
  let bits = 0;
  let value = 0;
  const bytes: number[] = [];
  for (const ch of cleaned) {
    const idx = BASE32_ALPHABET.indexOf(ch);
    if (idx === -1) throw new Error(`Invalid base32 char: ${ch}`);
    value = (value << 5) | idx;
    bits += 5;
    if (bits >= 8) {
      bytes.push((value >>> (bits - 8)) & 0xff);
      bits -= 8;
    }
  }
  return Buffer.from(bytes);
}

/** Generate a new random 20-byte (160-bit) TOTP secret as base32. */
export function generateTotpSecret(): string {
  return base32Encode(randomBytes(20));
}

/**
 * Compute the current 6-digit TOTP code for a secret.
 * Step = 30s, digits = 6, algorithm = SHA-1, all per RFC 6238 defaults.
 */
export function computeTotp(secretBase32: string, forTime: Date = new Date()): string {
  const counter = Math.floor(forTime.getTime() / 1000 / 30);
  const buf = Buffer.alloc(8);
  // Write counter as big-endian 64-bit
  buf.writeBigUInt64BE(BigInt(counter));
  const key = base32Decode(secretBase32);
  const hmac = createHmac("sha1", key).update(buf).digest();
  const offset = hmac[hmac.length - 1] & 0x0f;
  const truncated = hmac.subarray(offset, offset + 4);
  const num = truncated.readUInt32BE(0) & 0x7fffffff;
  const code = num % 1_000_000;
  return code.toString().padStart(6, "0");
}

/**
 * Verify a TOTP code against the current time window, plus the previous
 * and next windows (±30s) to tolerate clock drift between the user's
 * authenticator app and the server.
 */
export function verifyTotp(secretBase32: string, code: string): boolean {
  if (!/^\d{6}$/.test(code)) return false;
  const now = Date.now();
  for (const offset of [-30000, 0, 30000]) {
    const candidate = computeTotp(secretBase32, new Date(now + offset));
    const a = Buffer.from(candidate);
    const b = Buffer.from(code);
    if (a.length === b.length && timingSafeEqual(a, b)) return true;
  }
  return false;
}

/** Build an `otpauth://` URI for QR-code generators. */
export function buildOtpAuthUri(opts: {
  issuer: string;
  account: string;
  secret: string;
}): string {
  const label = encodeURIComponent(`${opts.issuer}:${opts.account}`);
  const params = new URLSearchParams({
    secret: opts.secret,
    issuer: opts.issuer,
    algorithm: "SHA1",
    digits: "6",
    period: "30",
  });
  return `otpauth://totp/${label}?${params.toString()}`;
}

// ---------------------------------------------------------------------------
// MFA login ticket — short-lived JWT that proves "password verified, 2FA
// pending". Used by FE-004 root fix.
// ---------------------------------------------------------------------------

const MFA_TICKET_TTL_SECONDS = 5 * 60; // 5 minutes

export interface MfaTicketPayload {
  sub: string;
  email: string;
  type: "mfa_pending";
}

export function issueMfaTicket(opts: { userId: string; email: string }): string {
  // FE-042: use the shared resolver. In dev this returns the loudly-logged
  // dev-only fallback (so 2FA enrollment works); in prod it throws if
  // JWT_SECRET is missing or too short — which is the desired fail-closed
  // behavior.
  return jwt.sign(
    { sub: opts.userId, email: opts.email, type: "mfa_pending" } as MfaTicketPayload,
    resolveJwtSecret(),
    { issuer: "drugos", expiresIn: MFA_TICKET_TTL_SECONDS, algorithm: "HS256" }
  );
}

export function verifyMfaTicket(token: string): MfaTicketPayload | null {
  // FE-041/042: support hot-rotation by trying both current + previous secrets.
  const candidates = [resolveJwtSecret(), resolvePreviousJwtSecret()].filter(
    (s): s is string => !!s
  );
  for (const secret of candidates) {
    try {
      const decoded = jwt.verify(token, secret, {
        issuer: "drugos",
        algorithms: ["HS256"],
      }) as MfaTicketPayload;
      if (!decoded || decoded.type !== "mfa_pending" || !decoded.sub) continue;
      return decoded;
    } catch {
      // try next candidate
    }
  }
  return null;
}
