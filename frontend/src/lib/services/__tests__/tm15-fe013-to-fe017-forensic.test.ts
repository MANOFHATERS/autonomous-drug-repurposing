/**
 * Teammate 15 — v143 — FE-013 to FE-017 forensic root-fix verification tests.
 *
 * These tests are SOURCE-LEVEL assertions (they read the actual source files
 * and verify the fix is present). They do NOT mock React/FastAPI/Next.js —
 * they verify the FIX ITSELF is in the code, not that the code "works" under
 * mocked conditions (which is what previous teammates did and which masked
 * the bugs the audit found).
 *
 * Hostile-auditor mode: every assertion is a forensic check that the SPECIFIC
 * broken pattern from the issue is GONE and the SPECIFIC fix is PRESENT.
 *
 * Run with: `cd frontend && npx jest src/lib/services/__tests__/tm15-fe013-to-fe017-forensic.test.ts --runInBand --forceExit`
 */

import { readFileSync, existsSync } from "fs";
import { join, dirname, resolve } from "path";
import { fileURLToPath } from "url";

// Resolve paths relative to THIS test file (not process.cwd()) so the
// tests work regardless of where Jest is invoked from.
// This file is at: frontend/src/lib/services/__tests__/tm15-fe013-to-fe017-forensic.test.ts
// FRONTEND_ROOT = frontend/
// REPO_ROOT     = autonomous-drug-repurposing/ (parent of frontend/)
const __filename_ts = typeof __filename !== "undefined"
  ? __filename
  : fileURLToPath(import.meta.url);
const THIS_DIR = dirname(__filename_ts);
const FRONTEND_ROOT = resolve(THIS_DIR, "../../../..");
const REPO_ROOT = resolve(FRONTEND_ROOT, "..");

function readSrc(relPath: string): string {
  const full = join(FRONTEND_ROOT, "src", relPath);
  if (!existsSync(full)) throw new Error(`file not found: ${full}`);
  return readFileSync(full, "utf8");
}

function readRepoFile(relPath: string): string {
  const full = join(REPO_ROOT, relPath);
  if (!existsSync(full)) throw new Error(`file not found: ${full}`);
  return readFileSync(full, "utf8");
}

// ---------------------------------------------------------------------------
// FE-013: WebSocket demo — onKeyPress removed, useState→useRef for socket.
// ---------------------------------------------------------------------------

describe("FE-013: WebSocket demo — onKeyDown + useRef socket", () => {
  const wsExample = readRepoFile("frontend/examples/websocket/frontend.tsx");

  test("imports useRef from react (NOT just useEffect/useState)", () => {
    expect(wsExample).toMatch(/import\s*\{[^}]*\buseRef\b[^}]*\}\s*from\s*['"]react['"]/);
  });

  test("imports the Socket TYPE from socket.io-client (for the ref type annotation)", () => {
    expect(wsExample).toMatch(/import\s*\{[^}]*\bio\b[^}]*,\s*type\s+Socket\s*[^}]*\}\s*from\s*['"]socket\.io-client['"]/);
  });

  test("socket is stored in useRef<Socket | null>(null) — NOT useState<any>(null)", () => {
    // The bug: `const [socket, setSocket] = useState<any>(null);`
    // The fix: `const socketRef = useRef<Socket | null>(null);`
    expect(wsExample).not.toMatch(/useState<any>\(\s*null\s*\)/);
    expect(wsExample).toMatch(/useRef<Socket\s*\|\s*null>\(\s*null\s*\)/);
  });

  test("no onKeyPress anywhere in the file (removed completely)", () => {
    // The bug: `onKeyPress={handleKeyPress}` and `onKeyPress={(e) => ...}`
    // The fix: all replaced with onKeyDown.
    // NOTE: the fix's explanatory comment block MENTIONS "onKeyPress" (to
    // explain what was removed) — that's intentional. The test must check
    // for ACTIVE JSX usage (onKeyPress=) not the comment. We strip
    // comments before checking.
    const stripped = wsExample
      .replace(/\/\*[\s\S]*?\*\//g, '')   // block comments
      .replace(/\/\/.*$/gm, '');             // line comments
    expect(stripped).not.toMatch(/onKeyPress\s*=/);
  });

  test("onKeyDown is used for BOTH the username input and the message input", () => {
    // Count onKeyDown occurrences — should be at least 2 (one per input).
    const matches = wsExample.match(/onKeyDown=/g);
    expect(matches).not.toBeNull();
    expect(matches!.length).toBeGreaterThanOrEqual(2);
  });

  test("handleKeyPress is gone — replaced with handleKeyDown (and handleJoinKeyDown)", () => {
    // NOTE: the fix's comment mentions "handleKeyPress" to explain the
    // rename. The test checks for ACTIVE usage (function def or call),
    // not the comment. We strip comments first.
    const stripped = wsExample
      .replace(/\/\*[\s\S]*?\*\//g, '')
      .replace(/\/\/.*$/gm, '');
    // The old pattern `const handleKeyPress =` or `handleKeyPress(` must be gone.
    expect(stripped).not.toMatch(/\bhandleKeyPress\b/);
    expect(stripped).toMatch(/handleKeyDown/);
    expect(stripped).toMatch(/handleJoinKeyDown/);
  });

  test("socket is accessed via socketRef.current — NOT via a state variable", () => {
    expect(wsExample).toMatch(/socketRef\.current/);
    // The old pattern `if (socket && ...)` (where socket was from useState)
    // must NOT be present. The new pattern reads from the ref.
    expect(wsExample).not.toMatch(/const\s*\[socket,/);
  });

  test("useEffect cleanup clears the ref (socketRef.current = null)", () => {
    // The fix explicitly nulls the ref on cleanup to avoid stale references.
    expect(wsExample).toMatch(/socketRef\.current\s*=\s*null/);
  });

  test("file is marked as illustrative-only (not part of production app)", () => {
    expect(wsExample).toMatch(/illustrative-only|ILLUSTRATIVE-ONLY|illustrative only/i);
  });
});

// ---------------------------------------------------------------------------
// FE-014: Graceful SIGTERM/SIGINT shutdown in backend/api/main.py.
// ---------------------------------------------------------------------------

describe("FE-014: Graceful SIGTERM/SIGINT shutdown in backend/api/main.py", () => {
  const mainPy = readRepoFile("backend/api/main.py");

  test("uvicorn.run is called with timeout_graceful_shutdown parameter", () => {
    // The bug: no timeout_graceful_shutdown — uvicorn killed in-flight
    // requests immediately on SIGTERM.
    expect(mainPy).toMatch(/timeout_graceful_shutdown\s*=/);
  });

  test("default graceful shutdown timeout is 30s (matches k8s terminationGracePeriodSeconds)", () => {
    expect(mainPy).toMatch(/DRUGOS_API_GRACEFUL_SHUTDOWN_SECONDS['"]?,\s*['"]30['"]/);
  });

  test("in-flight ML tasks are tracked in a module-level set", () => {
    expect(mainPy).toMatch(/_inflight_ml_tasks\s*:\s*["']?set\[?.*Task\]?["']?\s*=\s*set\(\)/);
  });

  test("_track_ml_call helper is defined and used to wrap httpx calls", () => {
    expect(mainPy).toMatch(/def\s+_track_ml_call\s*\(/);
    // Verify it's actually used in /predict (the GT service call).
    expect(mainPy).toMatch(/_track_ml_call\(/);
  });

  test("shutdown handler is registered via @app.on_event('shutdown')", () => {
    expect(mainPy).toMatch(/@app\.on_event\(["']shutdown["']\)/);
    expect(mainPy).toMatch(/async\s+def\s+_drain_inflight_ml_calls\s*\(/);
  });

  test("shutdown handler drains in-flight tasks with a 25s timeout (5s < uvicorn's 30s)", () => {
    expect(mainPy).toMatch(/_INFLIGHT_ML_DRAIN_TIMEOUT_SECONDS\s*=\s*25\.0/);
    expect(mainPy).toMatch(/asyncio\.wait_for/);
  });

  test("shutdown handler logs CRITICAL on timeout (so operators detect hung GT/RL)", () => {
    expect(mainPy).toMatch(/logger\.critical/);
    expect(mainPy).toMatch(/FE-014 shutdown.*did NOT complete within/i);
  });

  test("/predict wraps the httpx GT call with _track_ml_call", () => {
    // Find the /predict function body and verify _track_ml_call is inside.
    // The predict function is VERY long (huge docstring + GT call +
    // response mapping), so we search from the def to the next `@app.`
    // decorator (which marks the start of the next route handler).
    const predictStart = mainPy.indexOf('async def predict(');
    expect(predictStart).toBeGreaterThan(-1);
    const nextDecorator = mainPy.indexOf('@app.', predictStart + 1);
    const window = nextDecorator === -1
      ? mainPy.slice(predictStart)
      : mainPy.slice(predictStart, nextDecorator);
    expect(window).toMatch(/_track_ml_call\(/);
  });

  test("/top-k wraps the httpx RL call with _track_ml_call", () => {
    const topkStart = mainPy.indexOf('async def top_k(');
    expect(topkStart).toBeGreaterThan(-1);
    const nextDecorator = mainPy.indexOf('@app.', topkStart + 1);
    const window = nextDecorator === -1
      ? mainPy.slice(topkStart)
      : mainPy.slice(topkStart, nextDecorator);
    expect(window).toMatch(/_track_ml_call\(/);
  });

  test("all /kg/* proxy routes wrap httpx calls with _track_ml_call", () => {
    // There are 3 /kg/* routes: /kg/stats, /kg/explore, /cypher.
    // Verify each one's handler body contains _track_ml_call.
    const kgStatsStart = mainPy.indexOf('async def kg_stats(');
    const kgExploreStart = mainPy.indexOf('async def kg_explore(');
    const cypherStart = mainPy.indexOf('async def cypher_proxy(');
    // Each handler should have _track_ml_call in its first 3000 chars.
    if (kgStatsStart > -1) {
      expect(mainPy.slice(kgStatsStart, kgStatsStart + 3000)).toMatch(/_track_ml_call\(/);
    }
    if (kgExploreStart > -1) {
      expect(mainPy.slice(kgExploreStart, kgExploreStart + 3000)).toMatch(/_track_ml_call\(/);
    }
    if (cypherStart > -1) {
      expect(mainPy.slice(cypherStart, cypherStart + 3000)).toMatch(/_track_ml_call\(/);
    }
  });
});

// ---------------------------------------------------------------------------
// FE-015: FastAPI import graceful degradation (app = no-op stub when missing).
// ---------------------------------------------------------------------------

describe("FE-015: FastAPI import graceful degradation", () => {
  const mainPy = readRepoFile("backend/api/main.py");
  const extractOpenapi = readRepoFile("frontend/scripts/extract_openapi.py");

  test("main.py does NOT re-raise ImportError when FastAPI is missing", () => {
    // The bug: `raise ImportError("BE-001 v123: FastAPI is required...")`
    // The fix: catch ImportError and define no-op stubs.
    expect(mainPy).not.toMatch(/raise ImportError\(\s*["']BE-001 v123/);
  });

  test("main.py defines _HAS_FASTAPI flag (true when fastapi imports OK)", () => {
    expect(mainPy).toMatch(/_HAS_FASTAPI\s*=\s*True/);
    expect(mainPy).toMatch(/_HAS_FASTAPI\s*=\s*False/);
  });

  test("main.py defines a no-op FastAPI stub class with .get/.post/.middleware decorators", () => {
    expect(mainPy).toMatch(/class\s+_NoOpApp/);
    expect(mainPy).toMatch(/def\s+get\(self/);
    expect(mainPy).toMatch(/def\s+post\(self/);
    expect(mainPy).toMatch(/def\s+middleware\(self/);
    expect(mainPy).toMatch(/def\s+on_event\(self/);
  });

  test("main.py defines no-op stubs for Pydantic BaseModel, Field, ConfigDict", () => {
    expect(mainPy).toMatch(/class\s+BaseModel:\s*[^]*Stub Pydantic BaseModel/);
    expect(mainPy).toMatch(/def\s+Field\(/);
    expect(mainPy).toMatch(/def\s+ConfigDict\(/);
  });

  test("main.py defines a stub `status` class with HTTP status constants", () => {
    expect(mainPy).toMatch(/class\s+_StatusStub:/);
    expect(mainPy).toMatch(/HTTP_503_SERVICE_UNAVAILABLE\s*=\s*503/);
  });

  test("main.py sets app = FastAPI(...) when fastapi IS installed, else app = _NoOpApp()", () => {
    expect(mainPy).toMatch(/if\s+_HAS_FASTAPI:/);
    expect(mainPy).toMatch(/app\s*=\s*FastAPI\(/);
    expect(mainPy).toMatch(/app\s*=\s*_NoOpApp\(\)/);
  });

  test("main.py tags the no-op stub with _is_noop_stub=True for extract_openapi.py detection", () => {
    expect(mainPy).toMatch(/setattr\(app,\s*["']_is_noop_stub["'],\s*True\)/);
  });

  test("httpx is imported OPTIONALLY with a no-op stub when missing", () => {
    expect(mainPy).toMatch(/_HAS_HTTPX\s*=\s*True/);
    expect(mainPy).toMatch(/_HAS_HTTPX\s*=\s*False/);
    expect(mainPy).toMatch(/class\s+_NoOpHttpxModule/);
  });

  test("extract_openapi.py includes backend.api.main in the SERVICES list", () => {
    expect(extractOpenapi).toMatch(/["']backend\.api\.main["']/);
  });

  test("extract_openapi.py detects _is_noop_stub and skips with a WARNING", () => {
    expect(extractOpenapi).toMatch(/getattr\(app_obj,\s*["']_is_noop_stub["'],\s*False\)/);
    expect(extractOpenapi).toMatch(/FE-015.*no-op stub.*FastAPI not installed/i);
  });

  test("extract_openapi.py documents that FastAPI is required ONLY for backend dev", () => {
    expect(extractOpenapi).toMatch(/FastAPI is required ONLY for backend dev, NOT for frontend dev/i);
  });
});

// ---------------------------------------------------------------------------
// FE-016: addHypothesis POSTs to /api/projects/{id}/hypotheses (not /api/projects/{id}).
// ---------------------------------------------------------------------------

describe("FE-016: addHypothesis path → /api/projects/{id}/hypotheses", () => {
  const apiClient = readSrc("lib/api-client.ts");
  const projectRoute = readSrc("app/api/projects/[id]/route.ts");
  const hypothesesRoute = readSrc("app/api/projects/[id]/hypotheses/route.ts");

  test("api-client addHypothesis POSTs to /api/projects/${projectId}/hypotheses", () => {
    // The bug: `request<Hypothesis>('/api/projects/${projectId}', { method: "POST", ... })`
    // The fix: `request<Hypothesis>('/api/projects/${projectId}/hypotheses', { method: "POST", ... })`
    expect(apiClient).toMatch(/addHypothesis.*request<Hypothesis>\(`\/api\/projects\/\$\{projectId\}\/hypotheses`/s);
  });

  test("api-client addHypothesis does NOT POST to bare /api/projects/${projectId}", () => {
    // Extract just the addHypothesis method body and verify the old path is gone.
    const start = apiClient.indexOf("addHypothesis:");
    expect(start).toBeGreaterThan(-1);
    const end = apiClient.indexOf("addComment:", start);
    const addHypBody = apiClient.slice(start, end);
    // The old path was `/api/projects/${projectId}` (no /hypotheses suffix).
    // After the fix, only `/api/projects/${projectId}/hypotheses` should appear.
    expect(addHypBody).not.toMatch(/`\/api\/projects\/\$\{projectId\}`/);
    expect(addHypBody).toMatch(/`\/api\/projects\/\$\{projectId\}\/hypotheses`/);
  });

  test("[id]/route.ts no longer defines a POST handler (only GET)", () => {
    // The bug: POST handler was in [id]/route.ts.
    // The fix: POST handler moved to [id]/hypotheses/route.ts.
    expect(projectRoute).not.toMatch(/export\s+async\s+function\s+POST\s*\(/);
    expect(projectRoute).toMatch(/export\s+async\s+function\s+GET\s*\(/);
  });

  test("[id]/hypotheses/route.ts exists and defines the POST handler", () => {
    expect(hypothesesRoute).toMatch(/export\s+async\s+function\s+POST\s*\(/);
  });

  test("hypotheses/route.ts preserves the PROJECT_WRITE_ROLES check (FE-017 from Team 13)", () => {
    expect(hypothesesRoute).toMatch(/PROJECT_WRITE_ROLES/);
    expect(hypothesesRoute).toMatch(/organizationMember\.findFirst/);
  });

  test("hypotheses/route.ts preserves the visibility check on private projects", () => {
    expect(hypothesesRoute).toMatch(/project\.visibility\s*===\s*["']private["']/);
  });

  test("hypotheses/route.ts preserves CSRF protection (FE-011)", () => {
    expect(hypothesesRoute).toMatch(/requireCsrfOrSend/);
  });

  test("hypotheses/route.ts calls createHypothesis with the same signature as before", () => {
    expect(hypothesesRoute).toMatch(/createHypothesis\(/);
    expect(hypothesesRoute).toMatch(/projectId:\s*id/);
    expect(hypothesesRoute).toMatch(/createdById:\s*auth\.user\.userId/);
  });
});

// ---------------------------------------------------------------------------
// FE-017: listUsers / listAuditLogs use URLSearchParams (not template literals).
// ---------------------------------------------------------------------------

describe("FE-017: URLSearchParams for listUsers / listAuditLogs", () => {
  const apiClient = readSrc("lib/api-client.ts");

  test("listUsers uses URLSearchParams — NOT a template literal query string", () => {
    // The bug: `/api/admin/users?limit=${limit}&offset=${offset}`
    // The fix: `new URLSearchParams()` + `qs.set(...)` + `qs.toString()`.
    // Find the listUsers method body.
    const start = apiClient.indexOf("listUsers:");
    expect(start).toBeGreaterThan(-1);
    const end = apiClient.indexOf("updateUser:", start);
    const listUsersBody = apiClient.slice(start, end);
    expect(listUsersBody).not.toMatch(/`\/api\/admin\/users\?\$\{limit\}/);
    expect(listUsersBody).toMatch(/new URLSearchParams/);
    expect(listUsersBody).toMatch(/qs\.set\(\s*["']limit["']/);
    expect(listUsersBody).toMatch(/qs\.set\(\s*["']offset["']/);
    expect(listUsersBody).toMatch(/qs\.toString\(\)/);
  });

  test("listAuditLogs uses URLSearchParams — NOT a template literal query string", () => {
    const start = apiClient.indexOf("listAuditLogs:");
    expect(start).toBeGreaterThan(-1);
    const end = apiClient.indexOf("getSystemStatus:", start);
    const listAuditBody = apiClient.slice(start, end);
    expect(listAuditBody).not.toMatch(/`\/api\/audit-logs\?\$\{limit\}/);
    expect(listAuditBody).toMatch(/new URLSearchParams/);
    expect(listAuditBody).toMatch(/qs\.set\(\s*["']limit["']/);
    expect(listAuditBody).toMatch(/qs\.set\(\s*["']offset["']/);
    expect(listAuditBody).toMatch(/qs\.toString\(\)/);
  });

  test("ESLint config bans template-literal URL query string patterns (no-restricted-syntax)", () => {
    const eslintConfig = readRepoFile("frontend/eslint.config.mjs");
    expect(eslintConfig).toMatch(/no-restricted-syntax/);
    expect(eslintConfig).toMatch(/FE-017.*Use URLSearchParams/i);
  });
});
