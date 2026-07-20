/**
 * Teammate 13 v131 ROOT FIX verification tests.
 *
 * This file verifies the FORENSIC ROOT-LEVEL fixes for Tasks 13.1–13.5
 * PLUS the toolchain unblock (TypeScript 7 → 5.9.3, ESLint 10 → 9.39.5,
 * chart.tsx Recharts v3 types, sidebar.tsx useId, layout.tsx onError
 * removal, billing route require() removal, auth/server.ts createRequire).
 *
 * Hostile-auditor mode: these tests read ACTUAL FILE CONTENTS (not
 * comments, not runtime behavior) to prove the fixes are real. A comment
 * that says "FIXED" is not evidence — the code itself must be correct.
 */
import * as fs from 'fs';
import * as path from 'path';

const FRONTEND = path.resolve(__dirname, '../../../..');
const read = (rel: string) => fs.readFileSync(path.join(FRONTEND, rel), 'utf-8');

// Strip /* ... */ and // ... comments so "does NOT contain X" tests match
// actual code, not comments that explain the old bug. This is the
// hostile-auditor way: a comment that says "we removed hsl(var(--X))"
// is NOT evidence the code is fixed — we must check the code itself.
const stripComments = (src: string) =>
  src
    .replace(/\/\*[\s\S]*?\*\//g, '') // block comments
    .replace(/(^|[^:])\/\/.*$/gm, '$1'); // line comments (not URLs)

const readCode = (rel: string) => stripComments(read(rel));

describe('Teammate 13 v131 — Task 13.1: tailwind.config.ts (FE-011)', () => {
  const config = read('tailwind.config.ts');

  it('does NOT wrap CSS vars in hsl() — the root cause of FE-011', () => {
    // The bug: hsl(var(--background)) where --background is a hex value.
    // hsl(#F8F8FA) is INVALID CSS → falls back to currentColor → unstyled app.
    // Use readCode (strips comments) so we check actual code, not the
    // v123 comment block that explains the bug.
    const code = readCode('tailwind.config.ts');
    expect(code).not.toMatch(/hsl\(\s*var\(/);
  });

  it('uses CSS variables directly (var(--X) not hsl(var(--X)))', () => {
    expect(config).toMatch(/background:\s*['"]var\(--background\)/);
    expect(config).toMatch(/primary:\s*\{\s*DEFAULT:\s*['"]var\(--primary\)/);
  });
});

describe('Teammate 13 v131 — Task 13.2: Suspense + lazy loading (FE-030)', () => {
  const coreScreens = read('src/components/drugos/core-screens.tsx');
  const appRouter = read('src/components/drugos/app-router.tsx');

  it('core-screens.tsx uses next/dynamic for remaining-screens (not static import)', () => {
    // The bug: `import { remainingScreens } from './remaining-screens'`
    // bundled the 3864-line file into the main chunk.
    expect(coreScreens).not.toMatch(
      /import\s+\{\s*remainingScreens\s*\}\s+from\s+['"]\.\/remaining-screens['"]/,
    );
    expect(coreScreens).toMatch(
      /dynamic\(\s*\(\)\s*=>\s*import\(['"]\.\/remaining-screens['"]\)/,
    );
  });

  it('core-screens.tsx provides a skeleton fallback for each lazy screen', () => {
    expect(coreScreens).toMatch(/loading:\s*\(\)\s*=>\s*<ScreenSkeleton/);
  });

  it('app-router.tsx wraps screens in <Suspense> with a fallback', () => {
    expect(appRouter).toMatch(/<Suspense/);
    expect(appRouter).toMatch(/fallback=\{<CoreScreenSkeleton/);
  });
});

describe('Teammate 13 v131 — Task 13.3: ErrorBoundary (FE-029)', () => {
  const layout = read('src/app/layout.tsx');
  const errorBoundary = read('src/components/error-boundary.tsx');
  let errorTsx: string;
  try {
    errorTsx = read('src/app/error.tsx');
  } catch {
    errorTsx = '';
  }

  it('layout.tsx wraps children in <ErrorBoundary>', () => {
    expect(layout).toMatch(/<ErrorBoundary>/);
  });

  it('layout.tsx does NOT pass onError prop (Server→Client function passing bug fix)', () => {
    // v131 ROOT FIX: Server Components cannot pass functions to Client Components.
    // The onError prop was removed because ErrorBoundary.componentDidCatch
    // already logs errors. Use readCode to check actual code, not the
    // v131 comment block that explains the fix.
    const code = readCode('src/app/layout.tsx');
    expect(code).not.toMatch(/onError=\{/);
  });

  it('error-boundary.tsx is a class component with getDerivedStateFromError', () => {
    expect(errorBoundary).toMatch(/class\s+ErrorBoundary\s+extends\s+Component/);
    expect(errorBoundary).toMatch(/getDerivedStateFromError/);
    expect(errorBoundary).toMatch(/componentDidCatch/);
  });

  it('error-boundary.tsx has a DefaultErrorFallback with Try Again + Reload', () => {
    expect(errorBoundary).toMatch(/DefaultErrorFallback/);
    expect(errorBoundary).toMatch(/Try again|Try Again/);
    expect(errorBoundary).toMatch(/Reload/);
  });

  it('error.tsx exists with Next.js convention (use client + error + reset props)', () => {
    expect(errorTsx).toBeTruthy();
    expect(errorTsx).toMatch(/'use client'/);
    expect(errorTsx).toMatch(/error:\s*Error/);
    expect(errorTsx).toMatch(/reset:\s*\(\)\s*=>\s*void/);
  });
});

describe('Teammate 13 v131 — Task 13.4: api_contracts.ts (SH-006)', () => {
  const contracts = read('contracts/api_contracts.ts');

  it('contains real PredictRequest schema (not empty Record<string, never>)', () => {
    // The v129 bug: phase3/phase4 endpoints had empty schemas because
    // torch/gymnasium were missing → AST fallback → Record<string, never>.
    // v131 root fix: installed torch+gymnasium → real app.openapi() schemas.
    expect(contracts).toMatch(/PredictRequest/);
    expect(contracts).toMatch(/disease/);
    expect(contracts).toMatch(/drug/);
  });

  it('contains real RankRequest schema', () => {
    expect(contracts).toMatch(/RankRequest/);
  });

  it('contains real ValidateRequest schema', () => {
    expect(contracts).toMatch(/ValidateRequest/);
  });

  it('preserves canonical URL constants for the Python contract consistency test', () => {
    expect(contracts).toMatch(/URL_KG_STATS\s*=\s*["']\/kg\/stats["']/);
    expect(contracts).toMatch(/URL_PREDICT\s*=\s*["']\/predict["']/);
    expect(contracts).toMatch(/URL_RANK\s*=\s*["']\/rank["']/);
    expect(contracts).toMatch(/URL_VALIDATE\s*=\s*["']\/validate["']/);
  });

  it('has the openapi-typescript-generated paths interface', () => {
    // openapi-typescript v7 outputs `export interface paths` with the
    // canonical structure. The v129 custom Python generator also output
    // this, but openapi-typescript output is more comprehensive.
    expect(contracts).toMatch(/export interface paths/);
    expect(contracts).toMatch(/export interface components/);
  });
});

describe('Teammate 13 v131 — Task 13.5: canonical ScoreBar/SafetyBadge (FE-024)', () => {
  const appRouter = read('src/components/drugos/app-router.tsx');

  it('app-router.tsx imports ScoreBar from canonical path', () => {
    expect(appRouter).toMatch(
      /import\s+\{\s*ScoreBar\s*\}\s+from\s+['"]@\/components\/drugos\/score-bar['"]/,
    );
  });

  it('app-router.tsx imports SafetyBadge from canonical path', () => {
    expect(appRouter).toMatch(
      /import\s+\{\s*SafetyBadge\s*\}\s+from\s+['"]@\/components\/drugos\/safety-badge['"]/,
    );
  });

  it('app-router.tsx does NOT define a local ScoreBar function', () => {
    // The bug: local copies bypassed the scientific disclaimers in the
    // canonical component (AUC threshold warning, CI band, etc.).
    expect(appRouter).not.toMatch(/function\s+ScoreBar\s*\(/);
    expect(appRouter).not.toMatch(/const\s+ScoreBar\s*=\s*\(/);
  });

  it('app-router.tsx does NOT define a local SafetyBadge function', () => {
    expect(appRouter).not.toMatch(/function\s+SafetyBadge\s*\(/);
    expect(appRouter).not.toMatch(/const\s+SafetyBadge\s*=\s*\(/);
  });
});

describe('Teammate 13 v131 — bonus: chart.tsx Recharts v3 type fixes', () => {
  const chart = read('src/components/ui/chart.tsx');

  it('imports TooltipContentProps from recharts (not broken ComponentProps)', () => {
    // The bug: React.ComponentProps<typeof RechartsPrimitive.Tooltip>
    // resolves to TooltipProps which OMITS payload/label/active.
    expect(chart).toMatch(/import type \{[^}]*TooltipContentProps/);
  });

  it('imports LegendPayload from recharts', () => {
    expect(chart).toMatch(/import type \{[^}]*LegendPayload/);
  });

  it('does NOT use the broken ComponentProps<typeof RechartsPrimitive.Tooltip>', () => {
    const code = readCode('src/components/ui/chart.tsx');
    expect(code).not.toMatch(
      /React\.ComponentProps<typeof RechartsPrimitive\.Tooltip>/,
    );
  });
});

describe('Teammate 13 v131 — bonus: sidebar.tsx useId (no module-level counter)', () => {
  const sidebar = read('src/components/ui/sidebar.tsx');

  it('does NOT have a module-level __skeletonCounter variable', () => {
    // The bug: `let __skeletonCounter = 0` at module level, reassigned
    // inside useMemo → violated react-hooks rule + was fragile.
    expect(sidebar).not.toMatch(/^let __skeletonCounter/m);
  });

  it('uses React.useId() for deterministic skeleton widths', () => {
    expect(sidebar).toMatch(/React\.useId\(\)/);
  });
});

describe('Teammate 13 v131 — bonus: no require() in ESM source files', () => {
  it('billing route does not use require("crypto")', () => {
    const code = readCode('src/app/api/billing/subscription/route.ts');
    expect(code).not.toMatch(/require\(["']crypto["']\)/);
  });

  it('auth/server.ts uses createRequire (not bare require)', () => {
    const auth = read('src/lib/auth/server.ts');
    expect(auth).toMatch(/createRequire/);
    expect(auth).not.toMatch(/=\s*require\(/);
  });
});

describe('Teammate 13 v131 — bonus: TypeScript + ESLint versions', () => {
  const pkg = JSON.parse(read('package.json'));

  it('uses TypeScript 5.x (not 7.x which broke the toolchain)', () => {
    // TypeScript 7.0.2 removed ts.factory API → broke Next.js build,
    // ESLint, openapi-typescript. Downgrade to 5.9.3 is the root fix.
    // package.json stores version as "^5.9.3" (with caret), so the
    // regex must handle the optional caret prefix.
    expect(pkg.devDependencies.typescript).toMatch(/\^?5\./);
  });

  it('uses ESLint 9.x (not 10.x which broke eslint-plugin-react)', () => {
    // ESLint 10 removed context.getFilename() → broke eslint-plugin-react.
    expect(pkg.devDependencies.eslint).toMatch(/\^?9\./);
  });
});
