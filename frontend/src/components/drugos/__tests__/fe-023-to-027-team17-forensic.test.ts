/**
 * FE-023 through FE-027 forensic root-fix verification tests.
 *
 * Teammate 17 — hostile-auditor pass.
 *
 * These tests verify the STRUCTURAL fixes (file splits, import paths,
 * deleted shims) — NOT the screen internals. Each test reads the actual
 * source code on disk and asserts that the fix is in place. This is the
 * "verify by reading code" approach the user demanded — we do NOT trust
 * comments or existing test claims.
 *
 * Run with: npx jest src/components/drugos/__tests__/fe-023-to-027-team17-forensic.test.ts
 */

import { describe, it, expect } from "@jest/globals";
import * as fs from "fs";
import * as path from "path";

const DRUGOS_DIR = path.resolve(__dirname, "..");
const SCREENS_DIR = path.resolve(DRUGOS_DIR, "screens");
const FRONTEND_ROOT = path.resolve(DRUGOS_DIR, "..", "..", "..");

function readFile(rel: string): string {
  const abs = path.resolve(FRONTEND_ROOT, rel);
  if (!fs.existsSync(abs)) {
    throw new Error(`File not found: ${rel} (resolved: ${abs})`);
  }
  return fs.readFileSync(abs, "utf8");
}

function readFileLines(rel: string): number {
  return readFile(rel).split("\n").length;
}

function listFiles(dir: string, suffix = ".tsx"): string[] {
  if (!fs.existsSync(dir)) return [];
  return fs
    .readdirSync(dir)
    .filter((f) => f.endsWith(suffix))
    .sort();
}

// ═══════════════════════════════════════════════════════════════════════════
// FE-023: Three monolithic screen files split into per-screen files
// ═══════════════════════════════════════════════════════════════════════════

describe("FE-023: split monolithic screen files", () => {
  it("core-screens.tsx is now a thin barrel (<= 250 lines)", () => {
    const lines = readFileLines("src/components/drugos/core-screens.tsx");
    expect(lines).toBeLessThanOrEqual(250);
  });

  it("remaining-screens.tsx is now a thin barrel (<= 200 lines)", () => {
    const lines = readFileLines("src/components/drugos/remaining-screens.tsx");
    expect(lines).toBeLessThanOrEqual(200);
  });

  it("app-router.tsx is now a slim router (<= 350 lines)", () => {
    const lines = readFileLines("src/components/drugos/app-router.tsx");
    expect(lines).toBeLessThanOrEqual(350);
  });

  it("screens/ directory exists with the 3 shared helper files", () => {
    expect(fs.existsSync(SCREENS_DIR)).toBe(true);
    expect(fs.existsSync(path.join(SCREENS_DIR, "_core-shared.tsx"))).toBe(true);
    expect(fs.existsSync(path.join(SCREENS_DIR, "_remaining-shared.tsx"))).toBe(true);
    expect(fs.existsSync(path.join(SCREENS_DIR, "_app-layout.tsx"))).toBe(true);
  });

  it("every core screen file exists under screens/", () => {
    const expected = [
      "disease-search.tsx",
      "search-results.tsx",
      "candidate-detail.tsx",
      "charts.tsx",
      "knowledge-graph.tsx",
      "clinical-trials.tsx",
      "safety-profile.tsx",
      "ip-patents.tsx",
      "evidence-builder.tsx",
      "report-generation.tsx",
      "advanced-search.tsx",
      "saved-queries.tsx",
      "drug-comparison.tsx",
      "drug-interaction.tsx",
      "molecular-similarity.tsx",
      "score-breakdown.tsx",
      "disease-detail.tsx",
      "shortlists.tsx",
      "query-history.tsx",
      "batch-query.tsx",
      "prediction-explorer.tsx",
      "evidence-timeline.tsx",
      "mechanism-of-action.tsx",
      "regulatory-pathway.tsx",
      "screen-skeleton.tsx",
    ];
    for (const f of expected) {
      const p = path.join(SCREENS_DIR, f);
      expect(fs.existsSync(p)).toBe(true);
    }
  });

  it("every remaining screen exists under the right category subdirectory", () => {
    const expected: Record<string, string[]> = {
      admin: [
        "pipeline.tsx", "analytics.tsx", "team-members.tsx", "projects.tsx",
        "shared-queries.tsx", "annotations.tsx", "data-sources.tsx",
        "graph-statistics.tsx", "quality.tsx", "deals.tsx", "invoices.tsx",
        "users-admin.tsx", "roles.tsx", "sso.tsx", "audit-logs.tsx",
        "feature-flags.tsx", "api-docs.tsx", "api-keys.tsx", "playground.tsx",
        "webhooks.tsx",
      ],
      account: [
        "subscription.tsx", "usage.tsx", "profile.tsx", "security-settings.tsx",
        "notifications.tsx", "preferences.tsx",
      ],
      legal: ["privacy-policy.tsx", "terms.tsx", "compliance.tsx"],
      support: ["help-center.tsx", "ticket.tsx", "system-status.tsx"],
      investor: ["investor-dashboard.tsx", "cap-table.tsx"],
      misc: ["changelog.tsx", "roadmap.tsx", "feedback.tsx"],
    };
    for (const [category, files] of Object.entries(expected)) {
      for (const f of files) {
        const p = path.join(SCREENS_DIR, category, f);
        expect(fs.existsSync(p)).toBe(true);
      }
    }
  });

  it("every public/auth/app screen file exists under the right subdirectory", () => {
    const expected: Record<string, string[]> = {
      public: [
        "landing.tsx", "pricing.tsx", "about.tsx", "security.tsx", "status.tsx",
        "blog.tsx", "contact.tsx", "careers.tsx", "case-studies.tsx", "feature.tsx",
      ],
      auth: [
        "_auth-layout.tsx", "login.tsx", "register.tsx", "forgot-password.tsx",
        "reset-password.tsx", "mfa-challenge.tsx", "email-verification.tsx",
        "academic-verification.tsx", "org-selection.tsx", "onboarding-welcome.tsx",
        "onboarding-role.tsx", "onboarding-workspace.tsx", "onboarding-invite.tsx",
        "admin-approval.tsx", "account-locked.tsx",
      ],
      app: [
        "app-dashboard.tsx", "app-search.tsx", "app-search-results.tsx",
        "app-placeholder.tsx", "core-screen-bridge.tsx",
        "core-screen-skeleton.tsx", "app-section-renderer.tsx",
      ],
    };
    for (const [category, files] of Object.entries(expected)) {
      for (const f of files) {
        const p = path.join(SCREENS_DIR, category, f);
        expect(fs.existsSync(p)).toBe(true);
      }
    }
  });

  it("app-shell.tsx exists at the drugos/ root (alongside app-router.tsx)", () => {
    expect(fs.existsSync(path.join(DRUGOS_DIR, "app-shell.tsx"))).toBe(true);
  });

  it("every new screen file is <= 500 code lines (FE-023 lint rule)", () => {
    // Walk screens/ recursively and check each .tsx file.
    function walk(dir: string): string[] {
      const out: string[] = [];
      for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        const full = path.join(dir, entry.name);
        if (entry.isDirectory()) out.push(...walk(full));
        else if (entry.name.endsWith(".tsx")) out.push(full);
      }
      return out;
    }
    const files = walk(SCREENS_DIR);
    expect(files.length).toBeGreaterThan(80); // 97 expected
    for (const f of files) {
      const content = fs.readFileSync(f, "utf8");
      // Count code lines (non-blank, non-comment).
      const codeLines = content.split("\n").filter((line) => {
        const trimmed = line.trim();
        if (trimmed === "") return false;
        if (trimmed.startsWith("//")) return false;
        if (trimmed.startsWith("/*") || trimmed.startsWith("*")) return false;
        return true;
      }).length;
      expect(codeLines).toBeLessThanOrEqual(500);
    }
  });

  it("every new screen file starts with 'use client'", () => {
    function walk(dir: string): string[] {
      const out: string[] = [];
      for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        const full = path.join(dir, entry.name);
        if (entry.isDirectory()) out.push(...walk(full));
        else if (entry.name.endsWith(".tsx")) out.push(full);
      }
      return out;
    }
    const files = walk(SCREENS_DIR);
    for (const f of files) {
      const content = fs.readFileSync(f, "utf8");
      expect(content.startsWith("'use client'") || content.startsWith('"use client"')).toBe(true);
    }
  });

  it("core-screens.tsx barrel re-exports the coreScreens map with all 60 keys", () => {
    const content = readFile("src/components/drugos/core-screens.tsx");
    // 23 core screens + 37 remaining screens = 60 entries in coreScreens map.
    // Count the number of `'key':` patterns inside the coreScreens export.
    const coreScreensBlock = content.split("export const coreScreens")[1] || "";
    const keyMatches = coreScreensBlock.match(/'[\w-]+':/g) || [];
    expect(keyMatches.length).toBe(60);
  });

  it("remaining-screens.tsx barrel re-exports the remainingScreens map with 37 keys", () => {
    const content = readFile("src/components/drugos/remaining-screens.tsx");
    const block = content.split("export const remainingScreens")[1] || "";
    const keyMatches = block.match(/'[\w-]+':/g) || [];
    expect(keyMatches.length).toBe(37);
  });

  it("app-router.tsx imports AppShell from ./app-shell (not defined inline)", () => {
    const content = readFile("src/components/drugos/app-router.tsx");
    expect(content).toMatch(/import\s+\{[^}]*AppShell[^}]*\}\s+from\s+['"]\.\/app-shell['"]/);
    // The AppShell function should NOT be defined inside app-router.tsx.
    expect(content).not.toMatch(/^function\s+AppShell/m);
  });

  it("FE-023 lint rule (max-lines: 500) is present in eslint.config.mjs", () => {
    const content = readFile("eslint.config.mjs");
    expect(content).toMatch(/max-lines/);
    expect(content).toMatch(/max:\s*500/);
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// FE-024: lucide-react icon imports audited and dead icons removed
// ═══════════════════════════════════════════════════════════════════════════

describe("FE-024: audit and remove unused lucide-react icons", () => {
  it("app-router.tsx NO LONGER has the 90+ icon barrel import", () => {
    const content = readFile("src/components/drugos/app-router.tsx");
    // The original had a 12-line `import { ... } from 'lucide-react'` block
    // with 90+ icons. The slim router imports ZERO lucide icons.
    const lucideImports = content.match(/from\s+['"]lucide-react['"]/g) || [];
    expect(lucideImports.length).toBe(0);
  });

  it("no screen file imports more than 30 lucide-react icons", () => {
    // Walk screens/ and check each file's lucide-react import size.
    function walk(dir: string): string[] {
      const out: string[] = [];
      for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        const full = path.join(dir, entry.name);
        if (entry.isDirectory()) out.push(...walk(full));
        else if (entry.name.endsWith(".tsx")) out.push(full);
      }
      return out;
    }
    const files = walk(SCREENS_DIR);
    for (const f of files) {
      const content = fs.readFileSync(f, "utf8");
      const importMatch = content.match(/import\s*\{([^}]*)\}\s*from\s*['"]lucide-react['"]/);
      if (importMatch) {
        const icons = importMatch[1].split(",").map((s) => s.trim()).filter(Boolean);
        // Some icons have aliases (`X as Y`). Count each name once.
        expect(icons.length).toBeLessThanOrEqual(30);
      }
    }
  });

  it("specific dead icons flagged in the audit (Atom, GitFork, Columns3, FolderKanban, Flag) are only imported where actually used", () => {
    // The audit specifically called out these icons as "appear unused in a quick grep".
    // After the split, they should only appear in files that actually render them.
    // We verify they are NOT in the slim app-router.tsx (which renders no icons).
    const appRouter = readFile("src/components/drugos/app-router.tsx");
    expect(appRouter).not.toMatch(/\bAtom\b/);
    expect(appRouter).not.toMatch(/\bGitFork\b/);
    expect(appRouter).not.toMatch(/\bColumns3\b/);
    // FolderKanban and Flag may legitimately be used by AppShell (sidebar nav).
    // Verify they are imported there if used.
    const appShell = readFile("src/components/drugos/app-shell.tsx");
    if (appShell.match(/\bFolderKanban\b/)) {
      expect(appShell).toMatch(/import\s*\{[^}]*FolderKanban[^}]*\}\s*from\s*['"]lucide-react['"]/);
    }
    if (appShell.match(/\bFlag\b/)) {
      expect(appShell).toMatch(/import\s*\{[^}]*Flag[^}]*\}\s*from\s*['"]lucide-react['"]/);
    }
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// FE-025: version.ts uses NEXT_PUBLIC_APP_VERSION env var (not package.json import)
// ═══════════════════════════════════════════════════════════════════════════

describe("FE-025: version.ts env-var pattern", () => {
  it("version.ts does NOT statically import package.json", () => {
    const content = readFile("src/lib/version.ts");
    // Check the actual code lines (not comments). A real import statement
    // starts with `import` at the beginning of a line (possibly indented),
    // not inside a `//` comment block.
    const codeLines = content
      .split("\n")
      .map((line) => line.trim())
      .filter((line) => line && !line.startsWith("//") && !line.startsWith("*") && !line.startsWith("/*"));
    const codeOnly = codeLines.join("\n");
    expect(codeOnly).not.toMatch(/^import\s+packageJson/m);
    expect(codeOnly).not.toMatch(/require\(.*package\.json/);
    // Also assert no `import ... from "<anything>package.json"` at the start of a line.
    expect(codeOnly).not.toMatch(/^import\s+.*from\s+["'][^"']*package\.json["']/m);
  });

  it("version.ts reads NEXT_PUBLIC_APP_VERSION from process.env", () => {
    const content = readFile("src/lib/version.ts");
    expect(content).toMatch(/process\.env\.NEXT_PUBLIC_APP_VERSION/);
  });

  it("version.ts falls back to '0.0.0-unknown' when env var is unset", () => {
    const content = readFile("src/lib/version.ts");
    expect(content).toMatch(/0\.0\.0-unknown/);
  });

  it("next.config.ts inlines NEXT_PUBLIC_APP_VERSION at build time via env field", () => {
    const content = readFile("next.config.ts");
    expect(content).toMatch(/import\s+packageJson\s+from\s+["']\.\/package\.json["']/);
    expect(content).toMatch(/NEXT_PUBLIC_APP_VERSION:\s*packageJson\.version/);
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// FE-026: RefreshCw, AlertCircle import moved to top of use-api-data.tsx
// ═══════════════════════════════════════════════════════════════════════════

describe("FE-026: late import moved to top of use-api-data.tsx", () => {
  it("the lucide-react import for RefreshCw + AlertCircle appears ONLY in the top import block", () => {
    const content = readFile("src/components/drugos/use-api-data.tsx");
    const allLucideImports = content.match(/from\s+['"]lucide-react['"]/g) || [];
    // Should be exactly ONE lucide-react import statement (at the top).
    expect(allLucideImports.length).toBe(1);

    // That one import must include RefreshCw and AlertCircle.
    const importMatch = content.match(/import\s*\{([^}]*)\}\s*from\s*['"]lucide-react['"]/);
    expect(importMatch).not.toBeNull();
    const importBlock = importMatch![1];
    expect(importBlock).toMatch(/\bRefreshCw\b/);
    expect(importBlock).toMatch(/\bAlertCircle\b/);
  });

  it("the late `import { RefreshCw, AlertCircle }` statement is GONE from below line 600", () => {
    const content = readFile("src/components/drugos/use-api-data.tsx");
    const lines = content.split("\n");
    // The original late import was at line ~639. Verify no `import` statement
    // appears after line 100 (top imports end by then).
    for (let i = 100; i < lines.length; i++) {
      const line = lines[i];
      // Allow imports inside JSX strings (rare, but possible). Only flag
      // real top-level import statements.
      if (/^\s*import\s/.test(line)) {
        // Found a late import — this is a FE-026 regression.
        throw new Error(`Late import found at line ${i + 1}: ${line.trim()}`);
      }
    }
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// FE-027: 5 facade service shim files deleted, callers updated
// ═══════════════════════════════════════════════════════════════════════════

describe("FE-027: delete 5 facade service shim files", () => {
  const shims = [
    "src/lib/services/dataset-stats.ts",
    "src/lib/services/knowledge-graph-stats.ts",
    "src/lib/services/clinical-trials-service.ts",
    "src/lib/services/patents-service.ts",
    "src/lib/services/safety-service.ts",
  ];

  for (const shim of shims) {
    it(`${shim} has been deleted`, () => {
      const abs = path.resolve(FRONTEND_ROOT, shim);
      expect(fs.existsSync(abs)).toBe(false);
    });
  }

  it("no source file imports from any of the 5 deleted shim paths", () => {
    // Walk src/ and check no file imports from a shim path.
    function walk(dir: string): string[] {
      const out: string[] = [];
      for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        const full = path.join(dir, entry.name);
        if (entry.isDirectory()) out.push(...walk(full));
        else if (entry.name.endsWith(".ts") || entry.name.endsWith(".tsx")) out.push(full);
      }
      return out;
    }
    const srcDir = path.resolve(FRONTEND_ROOT, "src");
    const files = walk(srcDir);
    const shimNames = [
      "dataset-stats",
      "knowledge-graph-stats",
      "clinical-trials-service",
      "patents-service",
      "safety-service",
    ];
    for (const f of files) {
      const content = fs.readFileSync(f, "utf8");
      for (const shim of shimNames) {
        // Allow the shim NAME to appear in a COMMENT, but not in an import.
        // Match: from '.../<shim>' or from "<shim>" or from "./<shim>"
        const importRegex = new RegExp(
          `from\\s+['"][^'"]*\\b${shim}\\b['"]`,
        );
        if (importRegex.test(content)) {
          throw new Error(
            `${f} still imports from deleted shim "${shim}". Update to canonical path.`,
          );
        }
      }
    }
  });

  it("admin/metrics route imports getDatasetStats from @/lib/services/dataset-service (canonical)", () => {
    const content = readFile("src/app/api/admin/metrics/route.ts");
    expect(content).toMatch(
      /from\s+['"]@\/lib\/services\/dataset-service['"]/,
    );
    expect(content).not.toMatch(/from\s+['"]@\/lib\/services\/dataset-stats['"]/);
  });

  it("admin/metrics route imports getKnowledgeGraphStats from @/lib/services/kg-service (canonical)", () => {
    const content = readFile("src/app/api/admin/metrics/route.ts");
    expect(content).toMatch(/from\s+['"]@\/lib\/services\/kg-service['"]/);
    expect(content).not.toMatch(/from\s+['"]@\/lib\/services\/knowledge-graph-stats['"]/);
  });

  it("dataset/quality route imports getKnowledgeGraphStats from @/lib/services/kg-service (canonical)", () => {
    const content = readFile("src/app/api/dataset/quality/route.ts");
    expect(content).toMatch(/from\s+['"]@\/lib\/services\/kg-service['"]/);
    expect(content).not.toMatch(/from\s+['"]@\/lib\/services\/knowledge-graph-stats['"]/);
  });

  it("the canonical service files still export the expected functions", () => {
    const datasetService = readFile("src/lib/services/dataset-service.ts");
    expect(datasetService).toMatch(/export\s+async\s+function\s+getDatasetStats/);
    expect(datasetService).toMatch(/export\s+async\s+function\s+getDrugMechanism/);
    expect(datasetService).toMatch(/export\s+async\s+function\s+checkDatasetHealth/);

    const kgService = readFile("src/lib/services/kg-service.ts");
    expect(kgService).toMatch(/export\s+async\s+function\s+getKnowledgeGraphStats/);
    expect(kgService).toMatch(/export\s+async\s+function\s+exploreKnowledgeGraph/);
    expect(kgService).toMatch(/export\s+async\s+function\s+executeCypher/);
    expect(kgService).toMatch(/export\s+async\s+function\s+checkKgHealth/);

    const openfda = readFile("src/lib/services/openfda.ts");
    expect(openfda).toMatch(/export\s+async\s+function\s+getDrugSafetySummary/);
    expect(openfda).toMatch(/export\s+function\s+isOpenfdaApiKeyConfigured/);

    const patentsview = readFile("src/lib/services/patentsview.ts");
    expect(patentsview).toMatch(/export\s+async\s+function\s+searchPatents/);

    const clinicalTrials = readFile("src/lib/services/clinical-trials.ts");
    expect(clinicalTrials).toMatch(/export\s+async\s+function\s+searchClinicalTrials/);
  });
});
