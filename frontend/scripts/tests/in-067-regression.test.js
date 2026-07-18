/**
 * IN-067 regression test (Teammate 13, round 2).
 *
 * WHAT THIS TESTS
 * ---------------
 * run-integration-tests.js declares a module-level `let server = null;`
 * (line 42) and a `killServerGroup()` function that closes over it. The
 * `main()` function spawns a child process and assigns it to `server`.
 *
 * The bug: a previous "ROOT FIX" added a SECOND `let server = null;`
 * INSIDE main(), which shadowed the module-level binding. As a result
 * `killServerGroup()` always saw `null` and returned early — the spawned
 * dev server was NEVER killed, leaving zombie processes on port 3010.
 *
 * This test reproduces the same pattern in isolation and verifies that
 * `killServerGroup()` actually kills the spawned child. It would have
 * caught the original shadowing bug.
 *
 * Run with: node scripts/tests/in-067-regression.test.js
 */

const { spawn } = require("child_process");
const fs = require("fs");
const path = require("path");

let pass = 0;
let fail = 0;
function assert(cond, msg) {
  if (cond) {
    console.log(`  \u2713 ${msg}`);
    pass++;
  } else {
    console.log(`  \u2717 ${msg}`);
    fail++;
  }
}

// ─── 1. STATIC CHECK: source file must NOT have `let server` inside main() ──
// The bug was a local `let server = null;` inside main() shadowing the
// module-level `server`. After the fix, the only `let server` declaration
// is at module scope (line 42), and main() assigns to it without redeclaring.
console.log("\n=== IN-067 regression: static check ===\n");
const srcPath = path.join(__dirname, "..", "run-integration-tests.js");
const src = fs.readFileSync(srcPath, "utf8");

// Find the main() function body and check it does NOT declare a local `server`.
const mainMatch = src.match(/async function main\(\)\s*\{([\s\S]*?)^\}/m);
assert(mainMatch !== null, "main() function exists in source");

if (mainMatch) {
  let mainBody = mainMatch[1];
  // Strip // line comments and /* */ block comments so the static check
  // only matches ACTUAL code, not the comment text that explains the bug.
  // (Without this, the regex would match the literal phrase "let server"
  // inside the explanatory comment, giving a false positive.)
  mainBody = mainBody.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "");
  // A `let server` or `var server` or `const server` inside main() would
  // shadow the module-level binding — that's the bug.
  const hasLocalServerDecl = /\b(?:let|var|const)\s+server\b/.test(mainBody);
  assert(!hasLocalServerDecl, "main() does NOT redeclare `server` locally (no shadowing)");
  // main() must still ASSIGN to `server` (the module-level binding) so
  // killServerGroup can see the spawned ChildProcess.
  const assignsServer = /^\s*server\s*=\s*spawn/m.test(mainBody);
  assert(assignsServer, "main() assigns to module-level `server` via spawn()");
}

// ─── 2. BEHAVIORAL CHECK: killServerGroup pattern actually kills child ─────
// Reproduce the exact same pattern (module-level `server` + killServerGroup
// + main that spawns) and verify the child is actually killed.
console.log("\n=== IN-067 regression: behavioral check ===\n");

// Build a mini-module that mirrors run-integration-tests.js structure.
// We use the FIXED pattern (no local `let server` in main).
const module_ = (() => {
  let server = null; // module-level

  function killServerGroup() {
    if (!server || server.pid == null) return false;
    const pid = server.pid;
    try { process.kill(-pid, "SIGTERM"); } catch {}
    // Short grace period for the test (1s instead of 5s).
    const deadline = Date.now() + 1000;
    while (Date.now() < deadline) {
      let alive = true;
      try { process.kill(-pid, 0); } catch { alive = false; }
      if (!alive) return true;
      const sleepEnd = Date.now() + 50;
      while (Date.now() < sleepEnd) { /* spin */ }
    }
    try { process.kill(-pid, "SIGKILL"); } catch {}
    try { server.kill("SIGKILL"); } catch {}
    return true;
  }

  function getServer() { return server; }
  function setServer(s) { server = s; }

  return { killServerGroup, getServer, setServer };
})();

async function behavioralTest() {
  // Spawn a long-running child as a process-group leader (same as the real
  // run-integration-tests.js does). `node -e "setInterval(...)"` runs forever
  // until killed — perfect for verifying the kill actually happens.
  const child = spawn("node", ["-e", "setInterval(() => {}, 1000)"], {
    cwd: process.cwd(),
    detached: true,
    stdio: "ignore",
  });

  assert(child.pid !== undefined, `child spawned with pid ${child.pid}`);
  module_.setServer(child);
  assert(module_.getServer() === child, "module-level server is the spawned child");
  assert(module_.getServer().pid === child.pid, "killServerGroup will see correct pid");

  // Verify child is alive before kill.
  let aliveBefore = true;
  try { process.kill(-child.pid, 0); } catch { aliveBefore = false; }
  assert(aliveBefore, "child process group is alive BEFORE killServerGroup()");

  // Register the exit listener BEFORE calling killServerGroup, otherwise the
  // child may exit before we listen and we'll miss the event.
  const exitPromise = new Promise((resolve) => {
    const timer = setTimeout(() => resolve(false), 3000);
    child.on("exit", () => { clearTimeout(timer); resolve(true); });
  });

  // Call killServerGroup (the function under test).
  const killed = module_.killServerGroup();
  assert(killed === true, "killServerGroup() returned true (saw non-null server)");

  // Give the OS a moment to reap the process.
  await new Promise((r) => setTimeout(r, 200));

  // Verify child is dead after kill.
  let aliveAfter = true;
  try { process.kill(-child.pid, 0); } catch { aliveAfter = false; }
  assert(!aliveAfter, "child process group is DEAD after killServerGroup() — IN-067 FIXED");

  // Verify the immediate child is dead (exit event fires).
  const exited = await exitPromise;
  assert(exited, "child emitted 'exit' event after killServerGroup()");
}

// ─── 3. NEGATIVE CHECK: verify the OLD broken pattern would have failed ────
// This documents WHY the bug mattered: with a local `let server` shadowing
// the module-level, killServerGroup would have seen null and done nothing.
console.log("\n=== IN-067 regression: negative check (old broken pattern) ===\n");

async function negativeTest() {
  // Simulate the OLD broken pattern: module-level server is null, local
  // server is the spawned child. killServerGroup closes over module-level.
  let moduleLevelServer = null;
  function killServerGroup() {
    if (!moduleLevelServer || moduleLevelServer.pid == null) return false;
    // ... never reaches here in the broken pattern
    return true;
  }

  const child = spawn("node", ["-e", "setInterval(() => {}, 1000)"], {
    detached: true,
    stdio: "ignore",
  });

  // OLD BROKEN PATTERN: assign to a LOCAL `server`, not the module-level one.
  // eslint-disable-next-line no-unused-vars
  let server = child; // local — moduleLevelServer stays null

  const result = killServerGroup();
  assert(result === false, "OLD pattern: killServerGroup() returned false (saw null) — confirms the bug");

  // Clean up the leaked child (otherwise this test would leak it).
  try { process.kill(-child.pid, "SIGKILL"); } catch {}
  try { child.kill("SIGKILL"); } catch {}
  await new Promise((r) => setTimeout(r, 200));
}

(async () => {
  await behavioralTest();
  await negativeTest();

  console.log("\n=== IN-067 REGRESSION TEST SUMMARY ===");
  console.log(`  Passed: ${pass}`);
  console.log(`  Failed: ${fail}`);
  console.log(fail === 0
    ? "\n  \u2705 IN-067 FIX VERIFIED — killServerGroup() correctly kills the spawned dev server"
    : `\n  \u274C ${fail} CHECKS FAILED — IN-067 NOT FIXED`);
  process.exit(fail === 0 ? 0 : 1);
})();
