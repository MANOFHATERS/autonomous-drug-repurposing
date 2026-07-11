/**
 * AES-256-GCM encryption-at-rest utility.
 *
 * Used for encrypting sensitive fields persisted to the database (currently
 * WebhookEndpoint.secretEncrypted). The encryption key is sourced from the
 * WEBHOOK_SECRET_KEY environment variable (must be a 32-byte base64 or hex
 * string). If the env var is missing in production we fail-fast — we do NOT
 * fall back to a hardcoded key, because that would defeat the entire point
 * of encryption-at-rest.
 *
 * Ciphertext format: "v1:<base64 iv>:<base64 ciphertext>:<base64 tag>"
 * The "v1" prefix lets us evolve the format later without breaking old rows.
 */

import { createCipheriv, createDecipheriv, randomBytes, createHash } from "crypto";

const KEY_ENV_VAR = "WEBHOOK_SECRET_KEY";
const PREFIX = "v1";

function getKey(): Buffer {
  const raw = process.env[KEY_ENV_VAR];
  if (!raw) {
    if (process.env.NODE_ENV === "production") {
      throw new Error(
        `${KEY_ENV_VAR} must be set in production. Generate one with: ` +
        `openssl rand -base64 32`
      );
    }
    // Dev-only deterministic key so the app doesn't crash during local dev.
    // NEVER used in production — the throw above gates this.
    return createHash("sha256").update("drugos-dev-only-webhook-key-not-for-production").digest();
  }
  // Accept either base64 or hex.
  let buf: Buffer;
  if (/^[0-9a-fA-F]{64}$/.test(raw)) {
    buf = Buffer.from(raw, "hex");
  } else {
    try {
      buf = Buffer.from(raw, "base64");
    } catch {
      throw new Error(`${KEY_ENV_VAR} must be valid base64 or hex`);
    }
  }
  if (buf.length !== 32) {
    throw new Error(
      `${KEY_ENV_VAR} must decode to exactly 32 bytes (got ${buf.length}). ` +
      `Generate with: openssl rand -base64 32`
    );
  }
  return buf;
}

/**
 * Encrypt a plaintext string using AES-256-GCM.
 * Returns "v1:<base64 iv>:<base64 ciphertext>:<base64 tag>".
 */
export function encryptSecret(plaintext: string): string {
  if (typeof plaintext !== "string" || plaintext.length === 0) {
    throw new Error("plaintext must be a non-empty string");
  }
  const key = getKey();
  const iv = randomBytes(12); // 96-bit IV is recommended for GCM
  const cipher = createCipheriv("aes-256-gcm", key, iv);
  const ciphertext = Buffer.concat([
    cipher.update(plaintext, "utf8"),
    cipher.final(),
  ]);
  const tag = cipher.getAuthTag();
  return [
    PREFIX,
    iv.toString("base64"),
    ciphertext.toString("base64"),
    tag.toString("base64"),
  ].join(":");
}

/**
 * Decrypt a ciphertext produced by encryptSecret().
 * Returns the original plaintext, or throws if the value is tampered/invalid.
 */
export function decryptSecret(stored: string): string {
  if (typeof stored !== "string" || stored.length === 0) {
    throw new Error("stored ciphertext is empty");
  }
  const parts = stored.split(":");
  if (parts.length !== 4 || parts[0] !== PREFIX) {
    throw new Error(
      `Unrecognized ciphertext format (expected "${PREFIX}:iv:ct:tag")`
    );
  }
  const [, ivB64, ctB64, tagB64] = parts;
  const key = getKey();
  const iv = Buffer.from(ivB64, "base64");
  const ct = Buffer.from(ctB64, "base64");
  const tag = Buffer.from(tagB64, "base64");
  const decipher = createDecipheriv("aes-256-gcm", key, iv);
  decipher.setAuthTag(tag);
  const plaintext = Buffer.concat([decipher.update(ct), decipher.final()]);
  return plaintext.toString("utf8");
}

/**
 * Returns true if a stored value looks like an encrypted secret (i.e. starts
 * with "v1:"). Used by migrations to detect rows that still hold plaintext.
 */
export function isEncryptedSecret(stored: string): boolean {
  return typeof stored === "string" && stored.startsWith(`${PREFIX}:`);
}
