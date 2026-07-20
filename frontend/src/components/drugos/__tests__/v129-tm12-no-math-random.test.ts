/**
 * v129 TM12 Task 12.3 — No Math.random in production code (Red-Team)
 *
 * Hostile-auditor tests for the v129 root fix. Task 12.3 spec:
 *   "Multiple Math.random() calls generate fake safety scores (FE-004),
 *    IP risk scores (FE-005), and molecular similarity scores (FE-006)
 *    on every render. Fix: (1) delete all Math.random() calls; (2) replace
 *    with real API calls; (3) if data is loading, show skeleton; (4) if
 *    data is missing, show EmptyState + DemoDataBanner."
 *
 * Verification: "grep -r 'Math.random' frontend/src/components/  # should
 * return nothing"
 *
 * Red-Team mode: assume every comment is a lie. We strip comments and
 * check the ACTUAL code — not comments that mention Math.random.
 */

import * as fs from 'node:fs';
import * as path from 'node:path';

const ROOT = path.resolve(__dirname, '..', '..', '..', '..');

function read(rel: string): string {
  return fs.readFileSync(path.resolve(ROOT, rel), 'utf8');
}

/**
 * Strip JS line comments and block comments so we only check actual CODE.
 * The Red-Team audit found that previous "ROOT FIX" claims lived only in
 * comments while the actual code still had the bug.
 */
function stripComments(src: string): string {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, '') // block comments
    .replace(/^\s*\/\/.*$/gm, '')     // line comments (line start)
    .replace(/\s\/\/.*$/gm, '');      // trailing line comments
}

const PRODUCTION_FILES = [
  'src/components/drugos/app-router.tsx',
  'src/components/drugos/core-screens.tsx',
  'src/components/drugos/remaining-screens.tsx',
  'src/components/drugos/use-account-data.tsx',
  'src/components/drugos/use-api-data.tsx',
  'src/components/drugos/knowledge-graph-viewer.tsx',
  'src/components/drugos/candidate-table.tsx',
  'src/components/drugos/data-table.tsx',
  'src/components/drugos/score-bar.tsx',
  'src/components/drugos/safety-badge.tsx',
  'src/components/drugos/stat-card.tsx',
  'src/components/drugos/admin-billing-etc-screens.tsx',
  'src/components/drugos/disease-search-bar.tsx',
  'src/components/drugos/pathway-viz.tsx',
  'src/components/drugos/session-provider.tsx',
  'src/components/drugos/next-router-provider.tsx',
  'src/components/ui/sidebar.tsx',
  'src/components/ui/chart.tsx',
  'src/lib/services/billing.ts',
];

describe('v129 TM12 Task 12.3 — No Math.random in production code (Red-Team)', () => {
  describe.each(PRODUCTION_FILES)('%s — no Math.random in actual code', (file) => {
    it('has no Math.random() call in code (comments stripped)', () => {
      const src = read(file);
      const codeOnly = stripComments(src);
      // The buggy pattern: Math.random() anywhere in actual code.
      // We allow the string "Math.random" to appear in COMMENTS (so
      // engineers can document what was replaced), but NOT in code.
      expect(codeOnly).not.toMatch(/Math\.random/);
    });
  });

  describe('use-account-data.tsx — uses crypto.randomUUID for secure IDs', () => {
    const src = read('src/components/drugos/use-account-data.tsx');
    const codeOnly = stripComments(src);

    it('defines a generateSecureId helper', () => {
      expect(codeOnly).toContain('function generateSecureId');
    });

    it('generateSecureId uses crypto.randomUUID (NOT Math.random)', () => {
      expect(codeOnly).toContain('crypto.randomUUID');
    });

    it('addRecentQuery calls generateSecureId (not Math.random)', () => {
      // The ID generation for localStorage entries must use the secure helper.
      expect(codeOnly).toContain('id: generateSecureId()');
    });

    it('has a fallback for non-secure contexts (older browsers)', () => {
      // crypto.randomUUID is unavailable in non-HTTPS contexts in some browsers.
      // The helper MUST have a fallback.
      expect(codeOnly).toContain('__idCounter');
    });
  });

  describe('sidebar.tsx — uses deterministic skeleton widths (NOT Math.random)', () => {
    const src = read('src/components/ui/sidebar.tsx');
    const codeOnly = stripComments(src);

    it('SidebarMenuSkeleton uses a module-level counter (not Math.random)', () => {
      expect(codeOnly).toContain('__skeletonCounter');
    });

    it('SidebarMenuSkeleton width is computed from the counter (not Math.random)', () => {
      // The width formula: `${50 + __skeletonCounter}%` — deterministic.
      expect(codeOnly).toMatch(/50\s*\+\s*__skeletonCounter/);
    });
  });

  describe('billing.ts — uses crypto.randomBytes for invoice numbers (NOT Math.random)', () => {
    const src = read('src/lib/services/billing.ts');
    const codeOnly = stripComments(src);

    it('imports randomBytes from crypto', () => {
      expect(codeOnly).toMatch(/randomBytes/);
      expect(codeOnly).toMatch(/from\s+["']crypto["']/);
    });

    it('invoice number uses randomBytes (not Math.random)', () => {
      expect(codeOnly).toContain('randomBytes(6).toString("hex")');
    });
  });
});
