/**
 * Teammate 13 v129 regression guards.
 *
 * These tests verify that the forensic root fixes for Tasks 13.1, 13.3,
 * and 13.5 do not regress. They read source files as TEXT (not import them)
 * so a regression that breaks the import would still be caught.
 *
 * The user explicitly required regression tests that verify the actual
 * code state — not tests that pass by mocking. Each test reads the real
 * file from disk and asserts on its contents.
 */

import { readFileSync, existsSync, readdirSync, statSync } from 'node:fs';
import { resolve } from 'node:path';

// The test lives at frontend/src/lib/services/__tests__/<this>.test.ts
// FRONTEND_ROOT must point to frontend/ — 4 levels up.
const FRONTEND_ROOT = resolve(__dirname, '../../../..');

// ─────────────────────────────────────────────────────────────────────────────
// Task 13.1 (FE-011): tailwind.config.ts must NOT wrap hex CSS vars in hsl()
// ─────────────────────────────────────────────────────────────────────────────
// The bug: tailwind.config.ts had `background: 'hsl(var(--background))'` etc.
// But globals.css defines `--background: #F8F8FA` (HEX). `hsl(#F8F8FA)` is
// INVALID CSS — every Tailwind color utility resolved to a broken value.
//
// The fix: use the CSS variable directly: `background: 'var(--background)'`.
// This test reads tailwind.config.ts as text and verifies NO `hsl(var(`
// pattern appears in the color definitions. It also verifies globals.css
// defines the variables as HEX (so `var(--background)` is a valid color).
//
// This is a TEXT-based test (not an import) because:
//   1. We want to catch the exact regression pattern (hsl(var(...))).
//   2. Importing tailwind.config.ts triggers Tailwind's full config loader.
//   3. We can verify BOTH files in one test.

describe('Task 13.1 (FE-011): tailwind.config.ts hex CSS vars not in hsl()', () => {
  const tailwindConfigPath = resolve(FRONTEND_ROOT, 'tailwind.config.ts');
  const globalsCssPath = resolve(FRONTEND_ROOT, 'src/app/globals.css');

  it('tailwind.config.ts exists', () => {
    expect(existsSync(tailwindConfigPath)).toBe(true);
  });

  it('tailwind.config.ts does NOT contain `hsl(var(` wrappers around CSS vars', () => {
    const content = readFileSync(tailwindConfigPath, 'utf-8');
    // Strip comments so the test doesn't false-positive on a comment that
    // says "we removed the hsl(var()) wrapper".
    const activeLines = content
      .split('\n')
      .filter(line => {
        const trimmed = line.trim();
        // Skip single-line // comments.
        if (trimmed.startsWith('//')) return false;
        // Skip lines inside /* */ blocks (naive — assumes one block per line).
        if (trimmed.startsWith('*') || trimmed.startsWith('/*')) return false;
        return true;
      })
      .join('\n');
    expect(activeLines).not.toMatch(/hsl\s*\(\s*var\s*\(/);
  });

  it('tailwind.config.ts uses bare var(--xxx) for color values', () => {
    const content = readFileSync(tailwindConfigPath, 'utf-8');
    // Verify at least the canonical Tailwind color slots reference CSS vars
    // directly (not through hsl() or rgb() wrappers).
    expect(content).toMatch(/background:\s*['"]var\(--background\)['"]/);
    expect(content).toMatch(/foreground:\s*['"]var\(--foreground\)['"]/);
    expect(content).toMatch(/primary:\s*\{[^}]*DEFAULT:\s*['"]var\(--primary\)['"]/);
  });

  it('globals.css defines --background as a HEX value (not HSL components)', () => {
    const content = readFileSync(globalsCssPath, 'utf-8');
    // The :root block must define --background as a hex color.
    // This is the value tailwind.config.ts references via var(--background).
    const rootMatch = content.match(/:root\s*\{([^}]+)\}/);
    expect(rootMatch).not.toBeNull();
    const rootBlock = rootMatch![1];
    expect(rootBlock).toMatch(/--background:\s*#[0-9A-Fa-f]{3,8}/);
    expect(rootBlock).toMatch(/--primary:\s*#[0-9A-Fa-f]{3,8}/);
  });

  it('globals.css defines --destructive-foreground (FE-039 fix)', () => {
    // FE-039: destructive.foreground referenced --destructive-foreground
    // which was NEVER defined in globals.css. The fix defines it as white.
    const content = readFileSync(globalsCssPath, 'utf-8');
    const rootMatch = content.match(/:root\s*\{([^}]+)\}/);
    expect(rootMatch).not.toBeNull();
    expect(rootMatch![1]).toMatch(/--destructive-foreground:\s*#[0-9A-Fa-f]{3,8}/);
    // Dark mode must also define it.
    const darkMatch = content.match(/\.dark\s*\{([^}]+)\}/);
    expect(darkMatch).not.toBeNull();
    expect(darkMatch![1]).toMatch(/--destructive-foreground:\s*#[0-9A-Fa-f]{3,8}/);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// Task 13.3 (FE-029): error.tsx + global-error.tsx must exist (Next.js convention)
// ─────────────────────────────────────────────────────────────────────────────
// The bug: NO error.tsx or global-error.tsx existed. Any render crash
// white-screened the entire app — only the class-based <ErrorBoundary> in
// layout.tsx caught sync render errors, but Next.js per-route + root
// error boundaries were missing.
//
// The fix: create src/app/error.tsx (per-route) + src/app/global-error.tsx
// (root-level, catches errors even in the root layout itself).
//
// This test verifies both files exist, are client components, export a
// default function, and accept (error, reset) props.

describe('Task 13.3 (FE-029): Next.js error boundaries', () => {
  const errorTsxPath = resolve(FRONTEND_ROOT, 'src/app/error.tsx');
  const globalErrorTsxPath = resolve(FRONTEND_ROOT, 'src/app/global-error.tsx');

  it('src/app/error.tsx exists (per-route error boundary)', () => {
    expect(existsSync(errorTsxPath)).toBe(true);
  });

  it('src/app/global-error.tsx exists (root error boundary)', () => {
    expect(existsSync(globalErrorTsxPath)).toBe(true);
  });

  it('error.tsx is a client component with default export accepting (error, reset)', () => {
    const content = readFileSync(errorTsxPath, 'utf-8');
    expect(content).toMatch(/^['"]use client['"]/m);
    expect(content).toMatch(/export\s+default\s+function\s+\w+\s*\(\s*\{[^}]*error[^}]*reset[^}]*\}/);
    // Must call reset() somewhere (the "Try again" button).
    expect(content).toMatch(/reset\(\)/);
  });

  it('global-error.tsx is a client component that renders <html> and <body>', () => {
    // Next.js requires global-error.tsx to render its own <html> + <body>
    // because it REPLACES the root layout when activated.
    const content = readFileSync(globalErrorTsxPath, 'utf-8');
    expect(content).toMatch(/^['"]use client['"]/m);
    expect(content).toMatch(/export\s+default\s+function\s+\w+\s*\(\s*\{[^}]*error[^}]*reset[^}]*\}/);
    expect(content).toMatch(/<html/);
    expect(content).toMatch(/<body/);
    expect(content).toMatch(/reset\(\)/);
  });

  it('layout.tsx still wires the class-based <ErrorBoundary> (defense in depth)', () => {
    const layoutPath = resolve(FRONTEND_ROOT, 'src/app/layout.tsx');
    const content = readFileSync(layoutPath, 'utf-8');
    expect(content).toMatch(/import\s+\{\s*ErrorBoundary\s*\}\s+from\s+['"]@\/components\/error-boundary['"]/);
    expect(content).toMatch(/<ErrorBoundary/);
    // Suspense at root level (FE-030 fix from previous pass).
    expect(content).toMatch(/<Suspense/);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// Task 13.5 (FE-024): no local ScoreBar/SafetyBadge definitions outside canonical files
// ─────────────────────────────────────────────────────────────────────────────
// The bug: app-router.tsx had LOCAL `function ScoreBar(` and
// `function SafetyBadge(` definitions that bypassed the scientific
// disclaimers in the canonical score-bar.tsx and safety-badge.tsx.
//
// The fix: delete local copies, import from canonical paths.
//
// This test scans the entire frontend/src tree for local definitions and
// verifies NONE exist outside the canonical files. It also verifies the
// canonical files export the components.

function walkDir(dir: string, files: string[] = []): string[] {
  const entries = readdirSync(dir);
  for (const entry of entries) {
    const fullPath = resolve(dir, entry);
    const stat = statSync(fullPath);
    if (stat.isDirectory()) {
      // Skip node_modules, __tests__, .next, etc.
      if (entry === 'node_modules' || entry === '.next' || entry === '__tests__') continue;
      walkDir(fullPath, files);
    } else if (entry.endsWith('.tsx') || entry.endsWith('.ts')) {
      files.push(fullPath);
    }
  }
  return files;
}

describe('Task 13.5 (FE-024): canonical ScoreBar/SafetyBadge only', () => {
  const canonicalScoreBar = resolve(FRONTEND_ROOT, 'src/components/drugos/score-bar.tsx');
  const canonicalSafetyBadge = resolve(FRONTEND_ROOT, 'src/components/drugos/safety-badge.tsx');

  it('canonical score-bar.tsx exists and exports ScoreBar', () => {
    expect(existsSync(canonicalScoreBar)).toBe(true);
    const content = readFileSync(canonicalScoreBar, 'utf-8');
    expect(content).toMatch(/export\s+function\s+ScoreBar\s*\(/);
  });

  it('canonical safety-badge.tsx exists and exports SafetyBadge', () => {
    expect(existsSync(canonicalSafetyBadge)).toBe(true);
    const content = readFileSync(canonicalSafetyBadge, 'utf-8');
    expect(content).toMatch(/export\s+function\s+SafetyBadge\s*\(/);
  });

  it('NO local `function ScoreBar(` definitions exist outside canonical file', () => {
    const srcDir = resolve(FRONTEND_ROOT, 'src');
    const allFiles = walkDir(srcDir);
    const offenders: string[] = [];
    for (const file of allFiles) {
      if (file === canonicalScoreBar) continue;
      const content = readFileSync(file, 'utf-8');
      // Strip comments to avoid false positives.
      const activeLines = content
        .split('\n')
        .filter(line => {
          const trimmed = line.trim();
          return !trimmed.startsWith('//') && !trimmed.startsWith('*') && !trimmed.startsWith('/*');
        })
        .join('\n');
      if (/\bfunction\s+ScoreBar\s*\(/.test(activeLines)) {
        offenders.push(file);
      }
    }
    expect(offenders).toEqual([]);
  });

  it('NO local `function SafetyBadge(` definitions exist outside canonical file', () => {
    const srcDir = resolve(FRONTEND_ROOT, 'src');
    const allFiles = walkDir(srcDir);
    const offenders: string[] = [];
    for (const file of allFiles) {
      if (file === canonicalSafetyBadge) continue;
      const content = readFileSync(file, 'utf-8');
      const activeLines = content
        .split('\n')
        .filter(line => {
          const trimmed = line.trim();
          return !trimmed.startsWith('//') && !trimmed.startsWith('*') && !trimmed.startsWith('/*');
        })
        .join('\n');
      if (/\bfunction\s+SafetyBadge\s*\(/.test(activeLines)) {
        offenders.push(file);
      }
    }
    expect(offenders).toEqual([]);
  });

  it('consumers import ScoreBar from canonical path', () => {
    const consumers = [
      'src/components/drugos/core-screens.tsx',
      'src/components/drugos/candidate-table.tsx',
      'src/components/drugos/app-router.tsx',
    ];
    for (const rel of consumers) {
      const path = resolve(FRONTEND_ROOT, rel);
      if (!existsSync(path)) continue;
      const content = readFileSync(path, 'utf-8');
      const importsCanonical = /from\s+['"]@\/components\/drugos\/score-bar['"]/.test(content)
        || /from\s+['"]\.\/score-bar['"]/.test(content);
      expect(importsCanonical).toBe(true);
    }
  });

  it('consumers import SafetyBadge from canonical path', () => {
    const consumers = [
      'src/components/drugos/core-screens.tsx',
      'src/components/drugos/candidate-table.tsx',
      'src/components/drugos/app-router.tsx',
    ];
    for (const rel of consumers) {
      const path = resolve(FRONTEND_ROOT, rel);
      if (!existsSync(path)) continue;
      const content = readFileSync(path, 'utf-8');
      const importsCanonical = /from\s+['"]@\/components\/drugos\/safety-badge['"]/.test(content)
        || /from\s+['"]\.\/safety-badge['"]/.test(content);
      expect(importsCanonical).toBe(true);
    }
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// Task 13.2 (FE-030): Suspense + lazy loading for remaining-screens
// ─────────────────────────────────────────────────────────────────────────────
// The bug: remaining-screens.tsx (3864 lines) was statically imported by
// core-screens.tsx, bundling all 37 screens into the main JS payload.
//
// The fix: core-screens.tsx now uses `next/dynamic` to lazy-load each
// screen. CoreScreenBridge in app-router.tsx wraps <ScreenComponent /> in
// <Suspense> with a skeleton fallback.

describe('Task 13.2 (FE-030): Suspense + lazy loading for heavy screens', () => {
  const coreScreensPath = resolve(FRONTEND_ROOT, 'src/components/drugos/core-screens.tsx');
  const appRouterPath = resolve(FRONTEND_ROOT, 'src/components/drugos/app-router.tsx');

  it('core-screens.tsx imports `next/dynamic`', () => {
    const content = readFileSync(coreScreensPath, 'utf-8');
    expect(content).toMatch(/import\s+dynamic\s+from\s+['"]next\/dynamic['"]/);
  });

  it('core-screens.tsx no longer statically imports remainingScreens', () => {
    const content = readFileSync(coreScreensPath, 'utf-8');
    // The static `import { remainingScreens } from './remaining-screens'`
    // line must be GONE — replaced by `dynamic()` calls.
    const staticImport = /import\s+\{\s*remainingScreens\s*\}\s+from\s+['"]\.\/remaining-screens['"]/;
    expect(staticImport.test(content)).toBe(false);
  });

  it('core-screens.tsx uses dynamic() to lazy-load remaining screens', () => {
    const content = readFileSync(coreScreensPath, 'utf-8');
    // Verify at least a handful of screens are lazy-loaded.
    const dynamicCount = (content.match(/dynamic\(\s*\(\)\s*=>\s*import\(['"]\.\/remaining-screens['"]\)/g) || []).length;
    expect(dynamicCount).toBeGreaterThanOrEqual(20); // 37 total; allow some slack
  });

  it('core-screens.tsx does NOT spread ...remainingScreens into coreScreens', () => {
    const content = readFileSync(coreScreensPath, 'utf-8');
    // Strip comments so a comment that says "we removed ...remainingScreens"
    // doesn't false-positive. We only care about ACTIVE code.
    const activeLines = content
      .split('\n')
      .filter(line => {
        const trimmed = line.trim();
        return !trimmed.startsWith('//') && !trimmed.startsWith('*') && !trimmed.startsWith('/*');
      })
      .join('\n');
    // The spread was the OLD pattern that bundled everything statically.
    expect(activeLines).not.toMatch(/\.\.\.remainingScreens/);
  });

  it('app-router.tsx wraps <ScreenComponent /> in <Suspense>', () => {
    const content = readFileSync(appRouterPath, 'utf-8');
    expect(content).toMatch(/import\s+.*Suspense.*from\s+['"]react['"]/);
    expect(content).toMatch(/<Suspense/);
    expect(content).toMatch(/<ScreenComponent\s*\/>/);
  });

  it('app-router.tsx CoreScreenBridge has a skeleton fallback', () => {
    const content = readFileSync(appRouterPath, 'utf-8');
    // The fallback prop on <Suspense> must be set (not just <Suspense>).
    expect(content).toMatch(/<Suspense[^>]*fallback=\{/);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// Task 13.4 (SH-006): api_contracts.ts auto-generated from Python OpenAPI
// ─────────────────────────────────────────────────────────────────────────────
// The bug: api_contracts.ts was 606 lines of hand-written TS with phantom
// fields that diverged from Python.
//
// The fix: api_contracts.ts is now generated by
// frontend/scripts/extract_openapi.py + generate_api_contracts.py from
// the Python services' OpenAPI schemas. The hand-written URL constants
// at the top are kept for backwards compat with the Python contract test.

describe('Task 13.4 (SH-006): api_contracts.ts auto-generated', () => {
  const contractsPath = resolve(FRONTEND_ROOT, 'contracts/api_contracts.ts');
  const openapiJsonPath = resolve(FRONTEND_ROOT, 'contracts/openapi.json');
  const extractScriptPath = resolve(FRONTEND_ROOT, 'scripts/extract_openapi.py');
  const generateScriptPath = resolve(FRONTEND_ROOT, 'scripts/generate_api_contracts.py');
  const checkScriptPath = resolve(FRONTEND_ROOT, 'scripts/check-contracts.sh');

  it('all generator scripts exist', () => {
    expect(existsSync(extractScriptPath)).toBe(true);
    expect(existsSync(generateScriptPath)).toBe(true);
    expect(existsSync(checkScriptPath)).toBe(true);
  });

  it('openapi.json exists (extracted from Python services)', () => {
    expect(existsSync(openapiJsonPath)).toBe(true);
  });

  it('api_contracts.ts has AUTO-GENERATED header', () => {
    const content = readFileSync(contractsPath, 'utf-8');
    expect(content).toMatch(/AUTO-GENERATED.*DO NOT EDIT BY HAND/i);
  });

  it('api_contracts.ts exports URL constants (backwards compat with Python contract test)', () => {
    const content = readFileSync(contractsPath, 'utf-8');
    // These mirror shared/contracts/urls.py exactly. The Python contract
    // consistency test reads this file as text and verifies these literals.
    expect(content).toMatch(/URL_KG_STATS\s*=\s*["']\/kg\/stats["']/);
    expect(content).toMatch(/URL_KG_EXPLORE\s*=\s*["']\/kg\/explore["']/);
    expect(content).toMatch(/URL_PREDICT\s*=\s*["']\/predict["']/);
    expect(content).toMatch(/URL_TOP_K\s*=\s*["']\/top-k["']/);
    expect(content).toMatch(/URL_RANK\s*=\s*["']\/rank["']/);
    expect(content).toMatch(/URL_RANK_BY_DRUG\s*=\s*["']\/rank\/\{drug\}["']/);
    expect(content).toMatch(/URL_VALIDATE\s*=\s*["']\/validate["']/);
    expect(content).toMatch(/URL_HEALTH\s*=\s*["']\/health["']/);
  });

  it('api_contracts.ts exports `paths` interface with all 8+ canonical paths', () => {
    const content = readFileSync(contractsPath, 'utf-8');
    expect(content).toMatch(/export\s+interface\s+paths\s*\{/);
    // Each canonical URL must appear as a path key.
    expect(content).toMatch(/"\/kg\/stats"/);
    expect(content).toMatch(/"\/kg\/explore"/);
    expect(content).toMatch(/"\/predict"/);
    expect(content).toMatch(/"\/top-k"/);
    expect(content).toMatch(/"\/rank"/);
    expect(content).toMatch(/"\/rank\/\{drug\}"/);
    expect(content).toMatch(/"\/validate"/);
    expect(content).toMatch(/"\/health"/);
  });

  it('api_contracts.ts exports `components` and `operations` interfaces', () => {
    const content = readFileSync(contractsPath, 'utf-8');
    expect(content).toMatch(/export\s+interface\s+components\s*\{/);
    expect(content).toMatch(/export\s+interface\s+operations\s*\{/);
  });

  it('package.json has gen:contracts and check:contracts scripts', () => {
    const pkgPath = resolve(FRONTEND_ROOT, 'package.json');
    const content = readFileSync(pkgPath, 'utf-8');
    expect(content).toMatch(/"gen:contracts"\s*:/);
    expect(content).toMatch(/"check:contracts"\s*:/);
  });
});
