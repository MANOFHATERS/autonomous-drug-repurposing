/**
 * Test setup — runs once before each test suite.
 *
 * The test DATABASE_URL is set by tests/api/env.ts (a setupFile, which runs
 * before any test module is imported). This file ensures the schema is
 * pushed to the test DB and provides per-test cleanup.
 */

import { execSync } from "child_process";
import path from "path";

let prismaClient: any;

beforeAll(async () => {
  // Push schema to the test DB (idempotent — safe to run multiple times)
  try {
    execSync("npx prisma db push --skip-generate --accept-data-loss 2>&1", {
      stdio: "pipe",
      env: { ...process.env, DATABASE_URL: process.env.DATABASE_URL },
    });
  } catch (e) {
    // Schema push may fail silently if DB is already in sync — that's fine
  }
  // Issue 319 (audit 301-320): wrap PrismaClient init in try/catch so
  // filesystem-only e2e tests (like no-mock-data-in-production.e2e.ts)
  // do not fail when the test DB is unavailable. The prismaClient will
  // remain null and beforeEach will skip table cleanup — that's fine
  // because the e2e test does not touch the DB.
  try {
    const { PrismaClient } = await import("@prisma/client");
    prismaClient = new PrismaClient({
      datasources: { db: { url: process.env.DATABASE_URL } },
    });
    (globalThis as any).__testPrisma = prismaClient;
  } catch (e) {
    // Prisma client not available (e.g. not generated yet, or DB
    // unreachable). Non-DB tests can still run.
  }
});

afterAll(async () => {
  if (prismaClient) await prismaClient.$disconnect();
});

beforeEach(async () => {
  if (!prismaClient) return;
  // Clean all tables between tests for hermeticity
  const tablenames = [
    "AuditLog",
    "Notification",
    "MfaChallenge",
    "RefreshToken",
    "ApiKey",
    "EvidencePackage",
    "BillingInvoice",
    "Subscription",
    "Comment",
    "ProjectActivity",
    "Hypothesis",
    "Project",
    "OrganizationMember",
    "Organization",
    "User",
    "WebhookEndpoint",
  ];
  // Delete in dependency order to avoid FK violations
  for (const t of tablenames) {
    try {
      // @ts-ignore — dynamic model name
      await prismaClient[t].deleteMany({});
    } catch (e) {
      // Some tables may not exist yet — ignore
    }
  }
});
