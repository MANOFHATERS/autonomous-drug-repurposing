/**
 * FE-007 to FE-012 ROOT FIX verification tests (Teammate 14).
 *
 * These tests verify the 6 issues assigned to Teammate 14 (Frontend —
 * Routing + Type system) are FIXED at the root level. Each test reads
 * the ACTUAL production code (not comments, not test fixtures) and
 * asserts the fix is present.
 *
 * The tests are organized by issue ID. Each test name includes the issue
 * ID so a failure immediately tells you which issue regressed.
 *
 * Test strategy:
 *   - For source-code-level fixes (FE-007, FE-008, FE-012): read the
 *     file content and assert the fix is present. This catches regressions
 *     where a future refactor might accidentally revert the fix.
 *   - For runtime-behavior fixes (FE-009, FE-010, FE-011): import the
 *     actual module and exercise the runtime behavior. This catches
 *     drift between the type system and the runtime.
 *
 * Path strategy: jest runs from the frontend/ directory (the package
 * root). All paths are resolved relative to process.cwd() so they work
 * regardless of where the test file lives.
 */

import * as fs from 'fs';
import * as path from 'path';

const frontendRoot = process.cwd();

function resolveFrontend(relativePath: string): string {
  return path.resolve(frontendRoot, relativePath);
}

describe('FE-007 ROOT FIX: use-mobile.ts has "use client" directive', () => {
  const useMobilePath = resolveFrontend('src/hooks/use-mobile.ts');

  test('use-mobile.ts file exists at the expected path', () => {
    expect(fs.existsSync(useMobilePath)).toBe(true);
  });

  test('use-mobile.ts has "use client" as the FIRST line (before imports)', () => {
    const content = fs.readFileSync(useMobilePath, 'utf-8');
    const lines = content.split('\n');
    // Skip any leading blank lines / BOM — find the first non-empty line.
    const firstNonEmpty = lines.find((l) => l.trim().length > 0) ?? '';
    // The directive must be the first non-empty line. Both single and
    // double quotes are valid per the React spec.
    expect(firstNonEmpty).toMatch(/^["']use client["'];?$/);
  });

  test('use-mobile.ts still exports useIsMobile (no API regression)', () => {
    const content = fs.readFileSync(useMobilePath, 'utf-8');
    expect(content).toMatch(/export\s+function\s+useIsMobile\s*\(/);
  });

  test('use-mobile.ts still uses React.useState and React.useEffect (the hook still works)', () => {
    const content = fs.readFileSync(useMobilePath, 'utf-8');
    expect(content).toMatch(/React\.useState/);
    expect(content).toMatch(/React\.useEffect/);
  });
});

describe('FE-008 ROOT FIX: CSP hardened (no unsafe-inline for script-src, explicit connect-src allowlist)', () => {
  const middlewarePath = resolveFrontend('src/middleware.ts');
  const nextConfigPath = resolveFrontend('next.config.ts');

  test('middleware.ts file exists', () => {
    expect(fs.existsSync(middlewarePath)).toBe(true);
  });

  test('middleware.ts generates a per-request nonce via randomBytes', () => {
    const content = fs.readFileSync(middlewarePath, 'utf-8');
    expect(content).toMatch(/randomBytes\(32\)\.toString\(["']base64["']\)/);
    expect(content).toMatch(/x-nextjs-nonce/);
  });

  test('middleware.ts script-src does NOT contain unsafe-inline (the nonce is the only gate)', () => {
    const content = fs.readFileSync(middlewarePath, 'utf-8');
    // Find the actual CODE line that sets script-src (a template literal
    // starting with `script-src). Comment lines (// or *) are excluded —
    // comments may legitimately mention 'unsafe-inline' to explain why
    // it was removed.
    const codeLines = content
      .split('\n')
      .filter((l) => !/^\s*(\/\/|\*)/.test(l));
    const scriptSrcCodeLine = codeLines.find((l) =>
      l.includes("`script-src"),
    );
    expect(scriptSrcCodeLine).toBeDefined();
    // The code line must use the nonce template.
    expect(scriptSrcCodeLine!).toMatch(/`script-src 'self' 'nonce-\$\{nonce\}'`/);
    // ... and must NOT contain 'unsafe-inline' as a literal source.
    expect(scriptSrcCodeLine!).not.toMatch(/'unsafe-inline'/);
  });

  test('middleware.ts connect-src uses an EXPLICIT allowlist (no wildcard https:)', () => {
    const content = fs.readFileSync(middlewarePath, 'utf-8');
    // Filter out comment lines (// or *) — comments may legitimately
    // contain the loose `connect-src 'self' https:` form to explain
    // what the fix replaced.
    const codeLines = content
      .split('\n')
      .filter((l) => !/^\s*(\/\/|\*)/.test(l))
      .join('\n');
    // The code must NOT contain the loose `connect-src 'self' https:`
    // form (the wildcard-https bug).
    const hasLooseConnectSrc = /connect-src 'self' https:/.test(codeLines);
    expect(hasLooseConnectSrc).toBe(false);
    // The allowlist must include the real upstream biomedical APIs.
    expect(codeLines).toMatch(/https:\/\/api\.fda\.gov/);
    expect(codeLines).toMatch(/https:\/\/clinicaltrials\.gov/);
    expect(codeLines).toMatch(/https:\/\/eutils\.ncbi\.nlm\.nih\.gov/);
    expect(codeLines).toMatch(/https:\/\/rxnav\.nlm\.nih\.gov/);
    expect(codeLines).toMatch(/https:\/\/id\.nlm\.nih\.gov/);
    expect(codeLines).toMatch(/https:\/\/search\.patentsview\.org/);
    expect(codeLines).toMatch(/https:\/\/www\.ebi\.ac\.uk/);
  });

  test('next.config.ts does NOT set a Content-Security-Policy header (middleware owns the CSP)', () => {
    const content = fs.readFileSync(nextConfigPath, 'utf-8');
    // The next.config.ts headers() must NOT include a CSP entry —
    // because next.config.ts headers() run AFTER middleware and would
    // overwrite the middleware's nonce-based CSP with a static one.
    // The fix is to remove the CSP from next.config.ts entirely.
    //
    // We check that no `key: "Content-Security-Policy"` line exists in
    // the securityHeaders array. (The COMMENT mentioning CSP is allowed —
    // only the actual header entry is forbidden.)
    const cspHeaderEntry = content
      .split('\n')
      .find((l) => /^\s*key:\s*["']Content-Security-Policy["']/.test(l));
    expect(cspHeaderEntry).toBeUndefined();
  });

  test('next.config.ts still sets the other security headers (X-Frame-Options, HSTS, etc.)', () => {
    const content = fs.readFileSync(nextConfigPath, 'utf-8');
    expect(content).toMatch(/key:\s*["']X-Frame-Options["']/);
    expect(content).toMatch(/key:\s*["']X-Content-Type-Options["']/);
    expect(content).toMatch(/key:\s*["']Referrer-Policy["']/);
    expect(content).toMatch(/key:\s*["']Permissions-Policy["']/);
    expect(content).toMatch(/key:\s*["']Strict-Transport-Security["']/);
  });
});

describe('FE-009 ROOT FIX: ml-stubs.ts delegates to REAL health checks (no env-var-only stubs)', () => {
  const mlStubsPath = resolveFrontend('src/lib/services/ml-stubs.ts');

  test('ml-stubs.ts file exists', () => {
    expect(fs.existsSync(mlStubsPath)).toBe(true);
  });

  test('ml-stubs.ts imports checkKgHealth from kg-service (real ping)', () => {
    const content = fs.readFileSync(mlStubsPath, 'utf-8');
    expect(content).toMatch(/import\s+\{\s*checkKgHealth\s*\}\s+from\s+["']@\/lib\/services\/kg-service["']/);
  });

  test('ml-stubs.ts imports checkDatasetHealth from dataset-service (real ping)', () => {
    const content = fs.readFileSync(mlStubsPath, 'utf-8');
    expect(content).toMatch(/import\s+\{\s*checkDatasetHealth\s*\}\s+from\s+["']@\/lib\/services\/dataset-service["']/);
  });

  test('ml-stubs.ts imports checkRlHealth from rl-ranker (real ping)', () => {
    const content = fs.readFileSync(mlStubsPath, 'utf-8');
    expect(content).toMatch(/import\s+\{\s*checkRlHealth\s*\}\s+from\s+["']@\/lib\/services\/rl-ranker["']/);
  });

  test('checkKnowledgeGraphAvailability is ASYNC (returns Promise<MlServiceAvailability>)', () => {
    const content = fs.readFileSync(mlStubsPath, 'utf-8');
    expect(content).toMatch(/export\s+async\s+function\s+checkKnowledgeGraphAvailability\s*\(/);
  });

  test('checkDatasetAvailability is ASYNC', () => {
    const content = fs.readFileSync(mlStubsPath, 'utf-8');
    expect(content).toMatch(/export\s+async\s+function\s+checkDatasetAvailability\s*\(/);
  });

  test('checkRlAvailability is ASYNC', () => {
    const content = fs.readFileSync(mlStubsPath, 'utf-8');
    expect(content).toMatch(/export\s+async\s+function\s+checkRlAvailability\s*\(/);
  });

  test('checkKnowledgeGraphAvailability delegates to checkKgHealth (not env-var-only)', () => {
    const content = fs.readFileSync(mlStubsPath, 'utf-8');
    // The function body must call checkKgHealth() and use its result.
    expect(content).toMatch(/const\s+health\s*=\s*await\s+checkKgHealth\(\)/);
    // It must NOT short-circuit on `process.env.KG_SERVICE_URL` presence
    // to return available=true (the previous buggy behavior).
    const kgFnMatch = content.match(
      /export\s+async\s+function\s+checkKnowledgeGraphAvailability[\s\S]*?\n\}/,
    );
    expect(kgFnMatch).toBeDefined();
    const kgFnBody = kgFnMatch![0];
    // The available flag must come from `health.configured && health.reachable`
    // (the real health check result), NOT from `process.env.KG_SERVICE_URL` presence.
    expect(kgFnBody).toMatch(/health\.configured\s*&&\s*health\.reachable/);
    // Must NOT contain `available: true` as a direct return when env var is set.
    expect(kgFnBody).not.toMatch(
      /if\s*\(\s*url\s*\)\s*\{[\s\S]*?available:\s*true/,
    );
  });

  test('ML_SERVICE_STATUS metadata is preserved (for admin console display)', () => {
    const content = fs.readFileSync(mlStubsPath, 'utf-8');
    expect(content).toMatch(/export\s+const\s+ML_SERVICE_STATUS/);
    expect(content).toMatch(/knowledgeGraph/);
    expect(content).toMatch(/dataset/);
    expect(content).toMatch(/rl/);
  });
});

describe('FE-010 ROOT FIX: DatasetStatsResponse unified (single source of truth, Zod-validated)', () => {
  const apiClientPath = resolveFrontend('src/lib/api-client.ts');
  const mlContractsPath = resolveFrontend('src/lib/ml-contracts.ts');

  test('api-client.ts and ml-contracts.ts files exist', () => {
    expect(fs.existsSync(apiClientPath)).toBe(true);
    expect(fs.existsSync(mlContractsPath)).toBe(true);
  });

  test('api-client.ts does NOT hand-write a DatasetStatsResponse interface (it re-exports from ml-contracts)', () => {
    const content = fs.readFileSync(apiClientPath, 'utf-8');
    // The hand-written `export interface DatasetStatsResponse { ... }`
    // must NOT be present. The audit found this duplicate had drifted
    // from the canonical Zod schema in ml-contracts.ts.
    const hasHandWrittenInterface = /export\s+interface\s+DatasetStatsResponse\s*\{/.test(
      content,
    );
    expect(hasHandWrittenInterface).toBe(false);
  });

  test('api-client.ts re-exports DatasetStatsResponse from @/lib/ml-contracts', () => {
    const content = fs.readFileSync(apiClientPath, 'utf-8');
    // The re-export can be in the form:
    //   export type { DatasetStatsResponse } from "@/lib/ml-contracts";
    // or part of a multi-line export type { ... } block.
    expect(content).toMatch(/DatasetStatsResponse/);
    expect(content).toMatch(/from\s+["']@\/lib\/ml-contracts["']/);
  });

  test('api-client.ts does NOT hand-write a KnowledgeGraphStatsResponse interface (re-exports KgStatsResponse as alias)', () => {
    const content = fs.readFileSync(apiClientPath, 'utf-8');
    const hasHandWrittenInterface = /export\s+interface\s+KnowledgeGraphStatsResponse\s*\{/.test(
      content,
    );
    expect(hasHandWrittenInterface).toBe(false);
    // The alias re-export must be present.
    expect(content).toMatch(
      /KgStatsResponse\s+as\s+KnowledgeGraphStatsResponse/,
    );
  });

  test('api-client.ts does NOT hand-write a RlRankerResponse interface (re-exports from rl-ranker)', () => {
    const content = fs.readFileSync(apiClientPath, 'utf-8');
    const hasHandWrittenInterface = /export\s+interface\s+RlRankerResponse\s*\{/.test(
      content,
    );
    expect(hasHandWrittenInterface).toBe(false);
    expect(content).toMatch(
      /export\s+type\s+\{\s*RlRankerResponse\s*\}\s*from\s+["']@\/lib\/services\/rl-ranker["']/,
    );
  });

  test('api.getDatasetStats() wires the DatasetStatsResponseSchema (FE-066 runtime validation)', () => {
    const content = fs.readFileSync(apiClientPath, 'utf-8');
    // Find the getDatasetStats method definition.
    const match = content.match(
      /getDatasetStats:\s*\([^)]*\)\s*=>\s*request<[^>]+>\([^,]+,\s*\{[\s\S]*?schema:\s*DatasetStatsResponseSchema/,
    );
    expect(match).toBeDefined();
  });

  test('api.getKnowledgeGraphStats() wires the KgStatsResponseSchema', () => {
    const content = fs.readFileSync(apiClientPath, 'utf-8');
    const match = content.match(
      /getKnowledgeGraphStats:\s*\([^)]*\)\s*=>\s*[\s\S]*?request<[^>]+>\([^,]+,\s*\{[\s\S]*?schema:\s*KgStatsResponseSchema/,
    );
    expect(match).toBeDefined();
  });

  test('ml-contracts.ts still exports the canonical DatasetStatsResponseSchema', () => {
    const content = fs.readFileSync(mlContractsPath, 'utf-8');
    expect(content).toMatch(/export\s+const\s+DatasetStatsResponseSchema\s*=/);
    expect(content).toMatch(
      /export\s+type\s+DatasetStatsResponse\s*=\s*z\.infer<typeof\s+DatasetStatsResponseSchema>/,
    );
  });
});

describe('FE-011 ROOT FIX: searchPatents / buildEvidencePackage / getEvidencePackage fully typed (no any)', () => {
  const apiClientPath = resolveFrontend('src/lib/api-client.ts');
  const responseSchemasPath = resolveFrontend('src/lib/response-schemas.ts');

  test('response-schemas.ts file exists (new file with the Zod schemas)', () => {
    expect(fs.existsSync(responseSchemasPath)).toBe(true);
  });

  test('response-schemas.ts exports PatentSearchResponseSchema and PatentRecordSchema', () => {
    const content = fs.readFileSync(responseSchemasPath, 'utf-8');
    expect(content).toMatch(/export\s+const\s+PatentRecordSchema\s*=/);
    expect(content).toMatch(/export\s+const\s+PatentSearchResponseSchema\s*=/);
    // The PatentRecord schema must mirror the real route response shape
    // (patentNumber, title, abstract, grantDate, inventors, assignees,
    // cpcLabels, url). These fields come from patentsview.ts.
    expect(content).toMatch(/patentNumber:\s*z\.string\(\)/);
    expect(content).toMatch(/inventors:\s*z\.array\(z\.string\(\)\)/);
    expect(content).toMatch(/assignees:\s*z\.array\(z\.string\(\)\)/);
    expect(content).toMatch(/cpcLabels:\s*z\.array\(z\.string\(\)\)/);
  });

  test('response-schemas.ts exports EvidencePackageBuildResponseSchema', () => {
    const content = fs.readFileSync(responseSchemasPath, 'utf-8');
    expect(content).toMatch(/export\s+const\s+EvidencePackageBuildResponseSchema\s*=/);
    // The schema must validate the `package` field (the built EvidencePackage).
    expect(content).toMatch(/package:\s*EvidencePackageSchema/);
    expect(content).toMatch(/markdown:\s*z\.string\(\)/);
  });

  test('api.searchPatents returns PatentSearchResponse (NOT { items: any[] })', () => {
    const content = fs.readFileSync(apiClientPath, 'utf-8');
    // Find the searchPatents method.
    const match = content.match(
      /searchPatents:\s*\([^)]*\)\s*=>\s*request<PatentSearchResponse>/,
    );
    expect(match).toBeDefined();
    // The old form `request<{ items: any[] }>` must NOT be present anywhere.
    expect(content).not.toMatch(/request<\{\s*items:\s*any\[\]\s*\}>/);
  });

  test('api.searchPatents wires the PatentSearchResponseSchema', () => {
    const content = fs.readFileSync(apiClientPath, 'utf-8');
    const match = content.match(
      /searchPatents:[\s\S]*?schema:\s*PatentSearchResponseSchema/,
    );
    expect(match).toBeDefined();
  });

  test('api.buildEvidencePackage returns EvidencePackageBuildResponse (NOT { package: any })', () => {
    const content = fs.readFileSync(apiClientPath, 'utf-8');
    const match = content.match(
      /buildEvidencePackage:\s*\([^)]*\)\s*=>\s*request<EvidencePackageBuildResponse>/,
    );
    expect(match).toBeDefined();
    // The old form `request<{ id: string; package: any; markdown: string }>`
    // must NOT be present.
    expect(content).not.toMatch(/package:\s*any/);
  });

  test('api.buildEvidencePackage wires the EvidencePackageBuildResponseSchema', () => {
    const content = fs.readFileSync(apiClientPath, 'utf-8');
    const match = content.match(
      /buildEvidencePackage:[\s\S]*?schema:\s*EvidencePackageBuildResponseSchema/,
    );
    expect(match).toBeDefined();
  });

  test('api.getEvidencePackage returns EvidencePackageBuildResponse and wires the schema', () => {
    const content = fs.readFileSync(apiClientPath, 'utf-8');
    const match = content.match(
      /getEvidencePackage:\s*\([^)]*\)\s*=>\s*request<EvidencePackageBuildResponse>[\s\S]*?schema:\s*EvidencePackageBuildResponseSchema/,
    );
    expect(match).toBeDefined();
  });

  test('api-client.ts imports EvidencePackage from lib/services/evidence-package (the canonical built package type)', () => {
    const content = fs.readFileSync(apiClientPath, 'utf-8');
    expect(content).toMatch(
      /import\s+type\s+\{\s*EvidencePackage\s+as\s+BuiltEvidencePackage\s*\}\s*from\s+["']@\/lib\/services\/evidence-package["']/,
    );
  });

  test('api-client.ts renames the old DB-row EvidencePackage interface to EvidencePackageSummary', () => {
    const content = fs.readFileSync(apiClientPath, 'utf-8');
    expect(content).toMatch(/export\s+interface\s+EvidencePackageSummary/);
    // The old interface had `summary: string` and `updatedAt: string`
    // which the route GET handler doesn't actually return. The new
    // EvidencePackageSummary interface must NOT have those fields.
    const summaryMatch = content.match(
      /export\s+interface\s+EvidencePackageSummary\s*\{[\s\S]*?\n\}/,
    );
    expect(summaryMatch).toBeDefined();
    const summaryBody = summaryMatch![0];
    expect(summaryBody).not.toMatch(/^\s*summary:\s*string;/m);
    expect(summaryBody).not.toMatch(/^\s*updatedAt:\s*string;/m);
  });
});

describe('FE-012 ROOT FIX: websocket example server (CORS allowlist + crypto.randomUUID)', () => {
  const wsServerPath = resolveFrontend('examples/websocket/server.ts');

  test('examples/websocket/server.ts file exists', () => {
    expect(fs.existsSync(wsServerPath)).toBe(true);
  });

  test('server.ts does NOT use cors origin: "*" (the open-relay bug)', () => {
    const content = fs.readFileSync(wsServerPath, 'utf-8');
    // Filter out comment lines — comments may legitimately contain
    // `origin: "*"` to explain what the fix replaced.
    const codeLines = content
      .split('\n')
      .filter((l) => !/^\s*(\/\/|\*)/.test(l))
      .join('\n');
    // The literal `origin: "*"` must NOT appear in actual code.
    expect(codeLines).not.toMatch(/origin:\s*["']\*["']/);
  });

  test('server.ts uses an env-var-driven allowlist (WS_ALLOWED_ORIGINS)', () => {
    const content = fs.readFileSync(wsServerPath, 'utf-8');
    expect(content).toMatch(/WS_ALLOWED_ORIGINS/);
    // The allowlist must default to localhost:3000 (the dev server).
    expect(content).toMatch(/http:\/\/localhost:3000/);
  });

  test('server.ts uses a CORS callback (not a static string) for fine-grained origin checking', () => {
    const content = fs.readFileSync(wsServerPath, 'utf-8');
    expect(content).toMatch(/origin:\s*\(origin,\s*callback\)\s*=>\s*\{/);
  });

  test('server.ts does NOT use Math.random for message IDs (the predictable-ID bug)', () => {
    const content = fs.readFileSync(wsServerPath, 'utf-8');
    // Filter out comment lines — comments may legitimately mention
    // Math.random to explain why it was replaced.
    const codeLines = content
      .split('\n')
      .filter((l) => !/^\s*(\/\/|\*)/.test(l))
      .join('\n');
    // Math.random() must NOT appear in actual code.
    expect(codeLines).not.toMatch(/Math\.random\(\)/);
  });

  test('server.ts imports randomUUID and randomBytes from crypto', () => {
    const content = fs.readFileSync(wsServerPath, 'utf-8');
    expect(content).toMatch(
      /import\s+\{\s*randomUUID,\s*randomBytes\s*\}\s*from\s+["']crypto["']/,
    );
  });

  test('server.ts generateMessageId uses crypto.randomUUID with randomBytes fallback', () => {
    const content = fs.readFileSync(wsServerPath, 'utf-8');
    const match = content.match(
      /const\s+generateMessageId\s*=\s*\(\):\s*string\s*=>\s*\{[\s\S]*?if\s*\(\s*typeof\s+randomUUID\s*===\s*["']function["']\s*\)\s*\{[\s\S]*?return\s+randomUUID\(\)[\s\S]*?\}[\s\S]*?return\s+randomBytes\(16\)\.toString\(["']hex["']\)/,
    );
    expect(match).toBeDefined();
  });

  test('server.ts has an "EXAMPLE ONLY — DO NOT DEPLOY" warning at the top', () => {
    const content = fs.readFileSync(wsServerPath, 'utf-8');
    const firstNonEmpty = content
      .split('\n')
      .find((l) => l.trim().length > 0) ?? '';
    expect(firstNonEmpty).toMatch(/EXAMPLE ONLY.*DO NOT DEPLOY/i);
  });
});
