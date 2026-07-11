/**
 * API key management for the developer platform.
 *
 * Keys are issued in the format `drugos_<32 hex chars>`. We store only the
 * SHA-256 hash of the key, never the raw key. The user sees the raw key
 * exactly once at creation time and is responsible for storing it.
 *
 * ROOT FIX for FE-022 (API key revocation not scoped to owning user):
 *
 * Previously: `revokeApiKey(organizationId, keyId)` matched only on
 * `(id, organizationId)`. Any user in the org could revoke ANY OTHER user's
 * API key. The ApiKey model has a `userId` field — keys are per-user within
 * an org — but `revokeApiKey` did not check it.
 *
 * ROOT FIX: `revokeApiKey` and `listApiKeys` now accept an optional
 * `userId` filter. When the caller is not admin/owner, the route handler
 * passes their userId, which constrains both list and revoke to keys they
 * own. Admin/owner bypass the userId filter for org-wide oversight.
 *
 * Rate limiting: each key inherits the rate limit of the organization's
 * subscription plan. We do not implement rate limiting inside this service
 * — it lives in the API gateway / middleware layer.
 */

import { db } from "@/lib/db";
import { createHash, randomBytes } from "crypto";

export interface CreatedApiKey {
  id: string;
  name: string;
  prefix: string;
  rawKey: string; // shown once
  createdAt: Date;
}

export async function issueApiKey(
  organizationId: string,
  userId: string,
  name: string
): Promise<CreatedApiKey> {
  const rawKey = `drugos_${randomBytes(16).toString("hex")}`;
  const hash = createHash("sha256").update(rawKey).digest("hex");
  const prefix = rawKey.slice(0, 12);
  const record = await db.apiKey.create({
    data: {
      organizationId,
      userId,
      name,
      hashedKey: hash,
      prefix,
    },
  });
  return {
    id: record.id,
    name: record.name,
    prefix,
    rawKey,
    createdAt: record.createdAt,
  };
}

/**
 * List API keys for the org. If `userId` is supplied, only that user's keys
 * are returned (FE-022 root fix — non-admin callers pass their own userId).
 */
export async function listApiKeys(organizationId: string, userId?: string) {
  return db.apiKey.findMany({
    where: {
      organizationId,
      revokedAt: null,
      ...(userId ? { userId } : {}),
    },
    orderBy: { createdAt: "desc" },
    select: {
      id: true,
      name: true,
      prefix: true,
      lastUsedAt: true,
      createdAt: true,
    },
  });
}

/**
 * Revoke an API key. ROOT FIX for FE-022: the caller's `userId` is required
 * for non-admin/owner callers. If `userId` is supplied, the key must match
 * `(id, organizationId, userId)` — otherwise 0 rows are updated and we
 * return false (404 to the caller). Admin/owner callers pass `userId =
 * undefined` to bypass the per-user filter.
 */
export async function revokeApiKey(
  organizationId: string,
  keyId: string,
  userId?: string
): Promise<boolean> {
  const result = await db.apiKey.updateMany({
    where: {
      id: keyId,
      organizationId,
      revokedAt: null,
      ...(userId ? { userId } : {}),
    },
    data: { revokedAt: new Date() },
  });
  return result.count > 0;
}
