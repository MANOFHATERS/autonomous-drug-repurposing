/**
 * v118 Teammate 12 — Real Root-Fix Verification Tests
 *
 * These tests verify that the v118 fixes for the Teammate 12 swim lane
 * (frontend/src/components/drugos/*, frontend/src/components/layout/*,
 * frontend/src/hooks/*) are ACTUAL code-level fixes — not aspirational
 * comments. Each test asserts a property that would have FAILED before
 * the fix and PASSES after.
 *
 * Red-Team mode: assume every comment is a lie. We grep the actual source
 * code for the buggy pattern and assert it is gone, and grep for the
 * fixed pattern and assert it is present.
 */

import * as fs from 'node:fs';
import * as path from 'node:path';

const ROOT = path.resolve(__dirname, '..', '..', '..', '..');

function read(rel: string): string {
  return fs.readFileSync(path.resolve(ROOT, rel), 'utf8');
}

/**
 * Strip JS line comments (`// ...`) and block comments (`/* ... *\/`)
 * so tests can assert against actual CODE, not comments. The Red-Team
 * audit found that previous "ROOT FIX" claims lived only in comments
 * while the actual code still had the bug. This helper prevents that
 * failure mode: we strip comments before checking for buggy patterns.
 */
function stripComments(src: string): string {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, '') // block comments
    .replace(/^\s*\/\/.*$/gm, '')     // line comments (line start)
    .replace(/\s\/\/.*$/gm, '');      // trailing line comments
}

describe('v118 TM12 — Real Root-Fix Verification (Red-Team)', () => {
  describe('FE-013: app-shell.tsx category labels (not terse ids)', () => {
    const appShell = read('src/components/layout/app-shell.tsx');

    it('sidebar renders category.label, NOT category.id', () => {
      // The buggy pattern: <span className="flex-1 text-left">{category.id}</span>
      // The fixed pattern: <span className="flex-1 text-left">{category.label}</span>
      const idRender = appShell.match(/{category\.id}/g);
      // The `id` field is still used for navigation lookup, but NOT rendered as the label.
      // Verify the render call uses `label`:
      expect(appShell).toContain('{category.label}');
      // And the buggy direct render of `category.id` is gone (only used in lookup, not rendered):
      const renderIdPattern = /\{category\.id\}/g;
      const matches = appShell.match(renderIdPattern) || [];
      // We expect ZERO direct renders of `{category.id}` in JSX.
      // (Lookup uses `category.id === activeMeta.category` which is JS, not a render.)
      const jsxRenders = appShell
        .split('\n')
        .filter((l) => /\{category\.id\}/.test(l) && !/.+===.+/.test(l) && !/\/\/.*/.test(l));
      expect(jsxRenders.length).toBe(0);
    });

    it('breadcrumb renders categoryLabel, NOT category id', () => {
      expect(appShell).toContain('{activeMeta.categoryLabel}');
      // The buggy pattern `{activeMeta.category}` (rendered as the link text) is gone.
      const buggy = appShell
        .split('\n')
        .filter((l) => /\{activeMeta\.category\}/.test(l) && !/\.categoryLabel/.test(l) && !/===/.test(l) && !/\/\//.test(l));
      expect(buggy.length).toBe(0);
    });
  });

  describe('FE-049: IPPatentsScreen stat cards use real relatedPatents', () => {
    const core = read('src/components/drugos/core-screens.tsx');

    it('the 4 stat cards filter relatedPatents, NOT the empty patents array', () => {
      // Each card should be relatedPatents.filter(p => p.status === '...')
      const relatedPatentsActive = core.match(/relatedPatents\.filter\(p => p\.status === 'active'\)/g);
      const relatedPatentsPending = core.match(/relatedPatents\.filter\(p => p\.status === 'pending'\)/g);
      const relatedPatentsExpired = core.match(/relatedPatents\.filter\(p => p\.status === 'expired'\)/g);
      const relatedPatentsAbandoned = core.match(/relatedPatents\.filter\(p => p\.status === 'abandoned'\)/g);
      expect(relatedPatentsActive?.length ?? 0).toBeGreaterThanOrEqual(1);
      expect(relatedPatentsPending?.length ?? 0).toBeGreaterThanOrEqual(1);
      expect(relatedPatentsExpired?.length ?? 0).toBeGreaterThanOrEqual(1);
      expect(relatedPatentsAbandoned?.length ?? 0).toBeGreaterThanOrEqual(1);
    });

    it('does NOT render stat cards that filter the empty `patents` import', () => {
      // The buggy pattern: <StatCard ... value={patents.filter(p => p.status === 'active').length} .../>
      // After the fix, no StatCard value should reference `patents.filter(...)`
      // (only `relatedPatents.filter(...)` is allowed).
      const buggy = core.match(/value=\{patents\.filter\(/g);
      expect(buggy).toBeNull();
    });
  });

  describe('FE-038: EvidenceBuilderScreen Preview Package button has onClick', () => {
    const core = read('src/components/drugos/core-screens.tsx');

    it('Preview Package button has an onClick handler', () => {
      // The buggy pattern: <Button variant="outline" className="w-full"> with no onClick
      // After the fix: <Button variant="outline" className="w-full" onClick={handlePreview} ...>
      const fixed = core.match(/Preview Package[\s\S]{0,200}/g) || [];
      // Find the JSX block containing "Preview Package"
      const previewButtonBlock = core.match(/<Button[^>]*onClick=\{handlePreview\}[^>]*>\s*<Eye[^>]*\/>\s*Preview Package\s*<\/Button>/);
      expect(previewButtonBlock).not.toBeNull();
    });

    it('handlePreview function is defined', () => {
      expect(core).toContain('const handlePreview = () => {');
      // And it opens a new window with the markdown content.
      // The call signature is window.open('', '_blank') — the regex
      // matches the second argument being '_blank'.
      expect(core).toMatch(/window\.open\([^)]*,\s*['"]_blank['"]\)/);
    });
  });

  describe('FE-049: EvidenceBuilderScreen uses builtPackage evidence, not empty evidenceItems', () => {
    const core = read('src/components/drugos/core-screens.tsx');

    it('allEvidence derives from builtPackage (literature + trials + safety)', () => {
      expect(core).toContain('const allEvidence:');
      expect(core).toMatch(/pkg\?\.literature\?\.items/);
      expect(core).toMatch(/pkg\?\.clinicalTrials\?\.items/);
      expect(core).toMatch(/pkg\?\.safety\?\.topReactions/);
    });

    it('availableEvidence = allEvidence (not the empty evidenceItems.filter())', () => {
      expect(core).toContain('const availableEvidence = allEvidence;');
    });

    it('Selected Evidence panel looks up allEvidence, not evidenceItems', () => {
      expect(core).toContain('allEvidence.find(e => e.id === id)');
      // And the buggy pattern is gone:
      const buggy = core.match(/evidenceItems\.find\(e => e\.id === id\)/g);
      expect(buggy).toBeNull();
    });
  });

  describe('FE-050: ScoreBreakdownScreen uses RL candidates, not empty drugCandidates', () => {
    const core = read('src/components/drugos/core-screens.tsx');

    it('ScoreBreakdownScreen calls useRlCandidates', () => {
      // Find the ScoreBreakdownScreen function and verify it uses useRlCandidates.
      // The function body is now large (3000+ chars), so we allow up to 8000.
      const fnMatch = core.match(/function ScoreBreakdownScreen\(\)[\s\S]{0,8000}?\nfunction /);
      expect(fnMatch).not.toBeNull();
      expect(fnMatch![0]).toContain('useRlCandidates({ limit: 50 })');
    });

    it('ScoreBreakdownScreen does NOT use drugCandidates.find for the candidate lookup', () => {
      const fnMatch = core.match(/function ScoreBreakdownScreen\(\)[\s\S]{0,8000}?\nfunction /);
      expect(fnMatch).not.toBeNull();
      // Strip comments so we only check actual CODE, not the explanatory
      // comments that mention the old buggy pattern.
      const codeOnly = stripComments(fnMatch![0]);
      expect(codeOnly).not.toMatch(/drugCandidates\.find\(c => c\.id === selectedId\)/);
      // The fixed pattern uses rlCandidates
      expect(codeOnly).toMatch(/rlCandidates\.find\(c => c\.id === selectedId\)/);
    });
  });

  describe('FE-050: DiseaseDetailScreen uses real APIs (MeSH + RL + CT.gov)', () => {
    const core = read('src/components/drugos/core-screens.tsx');

    it('DiseaseDetailScreen calls useDiseaseSearch for metadata', () => {
      const fnMatch = core.match(/function DiseaseDetailScreen\(\)[\s\S]{0,8000}?\ninterface Shortlist/);
      expect(fnMatch).not.toBeNull();
      expect(fnMatch![0]).toContain('useDiseaseSearch(diseaseName');
    });

    it('DiseaseDetailScreen calls useRlCandidates for related candidates', () => {
      const fnMatch = core.match(/function DiseaseDetailScreen\(\)[\s\S]{0,8000}?\ninterface Shortlist/);
      expect(fnMatch).not.toBeNull();
      expect(fnMatch![0]).toContain('useRlCandidates({ disease: diseaseName');
    });

    it('DiseaseDetailScreen calls useClinicalTrialsSearch for related trials', () => {
      const fnMatch = core.match(/function DiseaseDetailScreen\(\)[\s\S]{0,8000}?\ninterface Shortlist/);
      expect(fnMatch).not.toBeNull();
      expect(fnMatch![0]).toContain('useClinicalTrialsSearch({ condition: diseaseName');
    });
  });

  describe('FE-050: MechanismOfActionScreen uses real ChEMBL/DrugBank data', () => {
    const core = read('src/components/drugos/core-screens.tsx');

    it('calls useRlCandidates for the drug dropdown', () => {
      const fnMatch = core.match(/function MechanismOfActionScreen\(\)[\s\S]{0,8000}?\nfunction RegulatoryPathwayScreen/);
      expect(fnMatch).not.toBeNull();
      expect(fnMatch![0]).toContain('useRlCandidates({ limit: 50 })');
    });

    it('calls useDrugMechanisms for the mechanism data', () => {
      const fnMatch = core.match(/function MechanismOfActionScreen\(\)[\s\S]{0,8000}?\nfunction RegulatoryPathwayScreen/);
      expect(fnMatch).not.toBeNull();
      expect(fnMatch![0]).toContain('useDrugMechanisms(');
    });

    it('uses mech.proteinTargets (the real field name), not mech.targets', () => {
      const fnMatch = core.match(/function MechanismOfActionScreen\(\)[\s\S]{0,8000}?\nfunction RegulatoryPathwayScreen/);
      expect(fnMatch).not.toBeNull();
      expect(fnMatch![0]).toContain('mech?.proteinTargets');
      // And the buggy mech.targets access is gone
      expect(fnMatch![0]).not.toMatch(/mech\?\.targets\b/);
    });
  });

  describe('FE-050: RegulatoryPathwayScreen uses RL candidates', () => {
    const core = read('src/components/drugos/core-screens.tsx');

    it('calls useRlCandidates for the drug dropdown', () => {
      // RegulatoryPathwayScreen is the LAST function before the EXPORT block.
      // Use a non-greedy match up to the next `// ═══` divider or `export const`.
      const fnMatch = core.match(/function RegulatoryPathwayScreen\(\)[\s\S]{0,8000}?(?:\n\/\/ [=]+|\nexport const coreScreens)/);
      expect(fnMatch).not.toBeNull();
      expect(fnMatch![0]).toContain('useRlCandidates({ limit: 50 })');
    });

    it('does NOT use drugCandidates.find() for candidate lookup', () => {
      const fnMatch = core.match(/function RegulatoryPathwayScreen\(\)[\s\S]{0,8000}?(?:\n\/\/ [=]+|\nexport const coreScreens)/);
      expect(fnMatch).not.toBeNull();
      const codeOnly = stripComments(fnMatch![0]);
      expect(codeOnly).not.toMatch(/drugCandidates\.find\(c => c\.drugName === selectedDrug\)/);
    });
  });

  describe('FE-050: DrugComparisonScreen uses RL candidates', () => {
    const core = read('src/components/drugos/core-screens.tsx');

    it('calls useRlCandidates for the candidate list', () => {
      const fnMatch = core.match(/function DrugComparisonScreen\(\)[\s\S]{0,8000}?\nfunction DrugInteractionScreen/);
      expect(fnMatch).not.toBeNull();
      expect(fnMatch![0]).toContain('useRlCandidates({ limit: 50 })');
    });

    it('does NOT use drugCandidates.find() for the compared lookup', () => {
      const fnMatch = core.match(/function DrugComparisonScreen\(\)[\s\S]{0,8000}?\nfunction DrugInteractionScreen/);
      expect(fnMatch).not.toBeNull();
      const codeOnly = stripComments(fnMatch![0]);
      expect(codeOnly).not.toMatch(/drugCandidates\.find\(c => c\.id === id\)/);
    });

    it('does NOT use the fabricated default IDs DC001/DC002', () => {
      const fnMatch = core.match(/function DrugComparisonScreen\(\)[\s\S]{0,8000}?\nfunction DrugInteractionScreen/);
      expect(fnMatch).not.toBeNull();
      expect(fnMatch![0]).not.toMatch(/useState<string\[\]>\(\['DC001', 'DC002'\]\)/);
    });
  });

  describe('FE-050: DrugInteractionScreen no longer filters the empty drugInteractions array', () => {
    const core = read('src/components/drugos/core-screens.tsx');

    it('does NOT call drugInteractions.filter() (which always returned [])', () => {
      const fnMatch = core.match(/function DrugInteractionScreen\(\)[\s\S]{0,8000}?\nfunction MolecularSimilarityScreen/);
      expect(fnMatch).not.toBeNull();
      const codeOnly = stripComments(fnMatch![0]);
      expect(codeOnly).not.toMatch(/drugInteractions\.filter\(/);
    });

    it('shows an honest EmptyState when no drugs selected', () => {
      const fnMatch = core.match(/function DrugInteractionScreen\(\)[\s\S]{0,8000}?\nfunction MolecularSimilarityScreen/);
      expect(fnMatch).not.toBeNull();
      expect(fnMatch![0]).toContain('Select two drugs to check interactions');
    });
  });

  describe('FE-044: KnowledgeGraphScreen node-type counts use allNodes (real data)', () => {
    const core = read('src/components/drugos/core-screens.tsx');

    it('the sidebar node-type counter uses allNodes.filter(), NOT graphNodes.filter()', () => {
      // The fixed pattern: <span>{allNodes.filter(n => n.type === type).length}</span>
      expect(core).toContain('allNodes.filter(n => n.type === type).length');
      // And the buggy pattern is gone from the JSX render (only in comments):
      const buggyJsxRender = core
        .split('\n')
        .filter((l) => /graphNodes\.filter\(n => n\.type === type\)\.length/.test(l) && !/\/\//.test(l));
      expect(buggyJsxRender.length).toBe(0);
    });
  });
});
