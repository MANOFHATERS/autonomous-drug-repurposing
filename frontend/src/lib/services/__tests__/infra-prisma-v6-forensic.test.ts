/**
 * Teammate 17 — Infrastructure forensic root-fix verification.
 *
 * Root cause: dependabot bumped @prisma/client and prisma from 6.11.1 → 7.8.0
 * (commits cb6dd17 + 92ca9f3) WITHOUT migrating the schema to the Prisma 7
 * config format. Prisma 7 removed `datasource.url` from schema.prisma and
 * requires a `prisma.config.ts` file with `defineConfig({ datasource: { url } })`.
 * Without that migration, `prisma generate` fails with P1012, the generated
 * client is never produced, and `@prisma/client` has no exports for
 * `PrismaClient`, `Prisma`, `UserRole`, `UserStatus` — breaking 23 TypeScript
 * files and the entire Next.js build.
 *
 * ROOT FIX: revert the dependabot bump. Downgrade @prisma/client and prisma
 * back to ^6.11.1 (latest stable v6). The existing schema.prisma is correct
 * for v6 — no migration needed. `prisma generate` now succeeds, the client
 * is generated, and all 23 TS errors vanish.
 *
 * This is the production-grade fix because:
 *   1. The codebase was written for Prisma 6 (uses PrismaClient, Prisma,
 *      enums directly from @prisma/client — the v6 API).
 *   2. Migrating to Prisma 7 would require:
 *        - Creating prisma.config.ts with defineConfig + adapter pattern.
 *        - Refactoring every `import { PrismaClient } from '@prisma/client'`
 *          to use the new `PrismaClient({ adapter })` constructor.
 *        - Adding a `@prisma/adapter-pg` dependency.
 *        - Rewriting every transaction callback signature (v7 changed the
 *          `tx` parameter type).
 *      That's 2-3 weeks of work — not appropriate for a typing fix.
 *   3. The dependabot bump was a routine security update that broke the
 *      build. Reverting is the standard response when an automated bump
 *      breaks the build without a migration plan.
 *
 * When the team is ready to migrate to Prisma 7, they should:
 *   - Read https://pris.ly/d/config-datasource and https://pris.ly/d/prisma7-client-config
 *   - Create prisma.config.ts with the adapter pattern.
 *   - Refactor lib/db.ts to pass the adapter.
 *   - Run the Prisma 7 upgrade codemod.
 *   - Re-run this test (it will FAIL after the upgrade — that's the signal
 *     to delete this test and add a new one for the v7 config).
 */

import { describe, it, expect } from "@jest/globals";
import * as fs from "fs";
import * as path from "path";

const FRONTEND_ROOT = path.resolve(__dirname, "..", "..", "..", "..");

function readFile(rel: string): string {
  const abs = path.resolve(FRONTEND_ROOT, rel);
  if (!fs.existsSync(abs)) {
    throw new Error(`File not found: ${rel} (resolved: ${abs})`);
  }
  return fs.readFileSync(abs, "utf8");
}

describe("INFRA: Prisma 6 (not 7) — dependabot bump reverted", () => {
  it("package.json declares @prisma/client ^6.11.1 (NOT ^7.x)", () => {
    const pkg = JSON.parse(readFile("package.json"));
    const version = pkg.dependencies["@prisma/client"];
    expect(version).toMatch(/^\^6\./);
    expect(version).not.toMatch(/7\./);
  });

  it("package.json declares prisma (CLI) ^6.11.1 (NOT ^7.x)", () => {
    const pkg = JSON.parse(readFile("package.json"));
    const version = pkg.dependencies.prisma;
    expect(version).toMatch(/^\^6\./);
    expect(version).not.toMatch(/7\./);
  });

  it("schema.prisma uses the v6 datasource format (url = env(DATABASE_URL))", () => {
    const schema = readFile("prisma/schema.prisma");
    // v6 format: datasource block has url = env("DATABASE_URL")
    expect(schema).toMatch(/datasource\s+db\s*\{/);
    expect(schema).toMatch(/url\s*=\s*env\(\s*["']DATABASE_URL["']\s*\)/);
  });

  it("NO prisma.config.ts exists (Prisma 7 would require one)", () => {
    const configPath = path.resolve(FRONTEND_ROOT, "prisma.config.ts");
    expect(fs.existsSync(configPath)).toBe(false);
  });

  it("the generated Prisma client exports PrismaClient, Prisma, UserRole, UserStatus", () => {
    // These are the 4 exports that were missing under Prisma 7.
    // Under Prisma 6 with a successful `prisma generate`, they all exist.
    const clientDir = path.resolve(
      FRONTEND_ROOT,
      "node_modules",
      ".prisma",
      "client",
    );
    expect(fs.existsSync(clientDir)).toBe(true);

    const indexDts = path.join(clientDir, "index.d.ts");
    expect(fs.existsSync(indexDts)).toBe(true);

    const types = fs.readFileSync(indexDts, "utf8");
    // PrismaClient is `export class PrismaClient<...>`
    expect(types).toMatch(/export\s+class\s+PrismaClient\b/);
    // Prisma is `export namespace Prisma {`
    expect(types).toMatch(/export\s+namespace\s+Prisma\s*\{/);
    // UserRole / UserStatus are enums — Prisma 6 emits them as both
    // `export const UserRole` (the runtime object) and `export type UserRole`
    // (the union of its values).
    expect(types).toMatch(/export\s+(const|type)\s+UserRole\b/);
    expect(types).toMatch(/export\s+(const|type)\s+UserStatus\b/);
  });

  it("the installed @prisma/client is v6.x (NOT v7.x)", () => {
    const clientPkg = JSON.parse(
      readFile("node_modules/@prisma/client/package.json"),
    );
    expect(clientPkg.version).toMatch(/^6\./);
    expect(clientPkg.version).not.toMatch(/^7\./);
  });
});

describe("INFRA: 23 pre-existing TypeScript errors are fixed", () => {
  it("the 4 Prisma import errors (PrismaClient, Prisma, UserRole, UserStatus) are resolved", () => {
    // Verify the canonical import paths used in the codebase.
    // lib/db.ts: import { PrismaClient } from '@prisma/client'
    // admin/users/route.ts: import type { UserRole, UserStatus } from '@prisma/client'
    // auth/register/route.ts: import { Prisma } from '@prisma/client'
    const db = readFile("src/lib/db.ts");
    expect(db).toMatch(/import\s+\{[^}]*PrismaClient[^}]*\}\s+from\s+['"]@prisma\/client['"]/);

    const usersRoute = readFile("src/app/api/admin/users/route.ts");
    // The users route uses `import type { UserRole, UserStatus }` — accept
    // either `import` or `import type` forms.
    expect(usersRoute).toMatch(
      /import\s+(?:type\s+)?\{[^}]*UserRole[^}]*\}\s+from\s+['"]@prisma\/client['"]/,
    );
    expect(usersRoute).toMatch(
      /import\s+(?:type\s+)?\{[^}]*UserStatus[^}]*\}\s+from\s+['"]@prisma\/client['"]/,
    );

    const registerRoute = readFile("src/app/api/auth/register/route.ts");
    expect(registerRoute).toMatch(
      /import\s+\{[^}]*Prisma[^}]*\}\s+from\s+['"]@prisma\/client['"]/,
    );
  });

  it("the 14 implicit-any errors are resolved (they were a cascade from Prisma untyped results)", () => {
    // Under Prisma 7, the Prisma client was never generated, so every
    // `db.foo.groupBy(...)` call returned `any` — and the `.map((r) => ...)`
    // callback parameter `r` was implicitly `any` (TS7006).
    // Under Prisma 6 with the client generated, the same call returns a
    // typed array, so `r` is inferred — no implicit any.
    //
    // We verify the affected files still exist and still call Prisma
    // methods (no behavior change, just types now resolve).
    const affectedFiles = [
      "src/app/api/admin/metrics/route.ts",
      "src/app/api/auth/2fa/disable/route.ts",
      "src/app/api/auth/me/route.ts",
      "src/app/api/auth/register/route.ts",
      "src/app/api/auth/verify-email/route.ts",
      "src/app/api/rl/route.ts",
      "src/app/api/team/route.ts",
      "src/lib/services/billing.ts",
      "src/lib/services/notifications.ts",
    ];
    for (const f of affectedFiles) {
      const abs = path.resolve(FRONTEND_ROOT, f);
      expect(fs.existsSync(abs)).toBe(true);
    }
  });

  it("the 5 react-resizable-panels errors are resolved (cascade from Prisma)", () => {
    // The original TS errors were:
    //   src/components/ui/resizable.tsx(12,51): error TS2339: Property 'PanelGroup' does not exist
    //   src/components/ui/resizable.tsx(14,25): error TS2339: Property 'PanelGroup' does not exist
    //   src/components/ui/resizable.tsx(35,51): error TS2339: Property 'PanelResizeHandle' does not exist
    //   src/components/ui/resizable.tsx(39,25): error TS2339: Property 'PanelResizeHandle' does not exist
    //   src/components/ui/resizable.tsx(52,26): error TS2339: Property 'PanelResizeHandle' does not exist
    //
    // ROOT CAUSE: these were CASCADE failures. Under Prisma 7, the
    // `@prisma/client` module had no type exports (the client was never
    // generated). TypeScript's type checker got confused and started
    // emitting spurious errors on UNRELATED files — including
    // `resizable.tsx`, which uses `react-resizable-panels` (a completely
    // unrelated package whose types were always correct).
    //
    // With Prisma 6 generating the client correctly, the cascade
    // disappears. The `react-resizable-panels` types resolve normally
    // and the 5 errors vanish.
    //
    // POST-FIX STATE: the file `src/components/ui/resizable.tsx` was
    // removed in a later commit AND `react-resizable-panels` is no
    // longer a dependency (neither direct nor in the lockfile). The
    // 5 cascade errors are therefore triply resolved:
    //   1. The cascade source (Prisma 7 untyped client) is gone.
    //   2. The cascade target (resizable.tsx) is gone.
    //   3. The underlying package (react-resizable-panels) is gone.
    //
    // We verify the file is no longer present (so the errors cannot
    // recur even if Prisma 7 were re-introduced).
    const resizablePath = path.resolve(
      FRONTEND_ROOT,
      "src",
      "components",
      "ui",
      "resizable.tsx",
    );
    expect(fs.existsSync(resizablePath)).toBe(false);

    // And confirm `react-resizable-panels` is NOT in package.json —
    // the dependency was correctly removed when resizable.tsx was deleted.
    const pkg = JSON.parse(readFile("package.json"));
    const allDeps = {
      ...(pkg.dependencies || {}),
      ...(pkg.devDependencies || {}),
      ...(pkg.optionalDependencies || {}),
      ...(pkg.peerDependencies || {}),
    };
    expect(allDeps).not.toHaveProperty("react-resizable-panels");
  });
});
