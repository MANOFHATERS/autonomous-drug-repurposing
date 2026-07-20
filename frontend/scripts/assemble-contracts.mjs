#!/usr/bin/env node
/**
 * frontend/scripts/assemble-contracts.mjs
 * ========================================
 *
 * Assembles frontend/contracts/api_contracts.ts from two parts:
 *   1. The hand-maintained URL constants preamble (_url-constants.ts)
 *   2. The openapi-typescript-generated types (from openapi.json)
 *
 * Task 13.4 (SH-006) v131 ROOT FIX (Teammate 13):
 *   The task required generating api_contracts.ts using `openapi-typescript`.
 *   The previous v129 implementation used a custom Python generator instead,
 *   because openapi-typescript v7.13.0 crashed on TypeScript 7 (the `ts.factory`
 *   API was removed in TS 7). Now that the project uses TypeScript 5.9.3 (the
 *   version the JS/TS ecosystem supports), openapi-typescript works natively.
 *
 *   This script:
 *     1. Runs `npx openapi-typescript contracts/openapi.json -o <tmp>` to
 *        generate the TypeScript types from the combined OpenAPI schema.
 *     2. Reads the preamble (_url-constants.ts) — the hand-maintained URL
 *        constants that the Python contract consistency test reads as text.
 *     3. Reads the openapi-typescript output.
 *     4. Writes the combined file to contracts/api_contracts.ts with a
 *        header explaining the generation process.
 *
 * Usage:
 *   node frontend/scripts/assemble-contracts.mjs
 *
 * Prerequisites:
 *   - frontend/contracts/openapi.json must exist (run extract_openapi.py first)
 *   - openapi-typescript must be installed (npm install --legacy-peer-deps)
 */
import { readFileSync, writeFileSync, existsSync } from "node:fs";
import { join, dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { execSync } from "node:child_process";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const FRONTEND_DIR = join(__dirname, "..");
const CONTRACTS_DIR = join(FRONTEND_DIR, "contracts");

const OPENAPI_JSON = join(CONTRACTS_DIR, "openapi.json");
const PREAMBLE = join(CONTRACTS_DIR, "_url-constants.ts");
const OUTPUT = join(CONTRACTS_DIR, "api_contracts.ts");
const TMP_TYPES = join(CONTRACTS_DIR, "_openapi-types.tmp.ts");

function fail(msg) {
  console.error(`[assemble-contracts] ERROR: ${msg}`);
  process.exit(1);
}

// 1. Verify prerequisites
if (!existsSync(OPENAPI_JSON)) {
  fail(
    `openapi.json not found at ${OPENAPI_JSON}. ` +
      "Run `python3 frontend/scripts/extract_openapi.py` first.",
  );
}
if (!existsSync(PREAMBLE)) {
  fail(`Preamble not found at ${PREAMBLE}.`);
}

// 2. Run openapi-typescript to generate types from openapi.json
console.log("[assemble-contracts] Running openapi-typescript...");
try {
  execSync(
    `npx openapi-typescript "${OPENAPI_JSON}" -o "${TMP_TYPES}"`,
    {
      cwd: FRONTEND_DIR,
      stdio: ["ignore", "pipe", "pipe"],
      env: { ...process.env, NEXT_TELEMETRY_DISABLED: "1" },
    },
  );
} catch (e) {
  fail(
    `openapi-typescript failed: ${e.message}. ` +
      "Ensure openapi-typescript is installed (npm install --legacy-peer-deps).",
  );
}

// 3. Read preamble and generated types
const preamble = readFileSync(PREAMBLE, "utf-8");
const generatedTypes = readFileSync(TMP_TYPES, "utf-8");

// 4. Build the header
const HEADER = `/**
 * frontend/contracts/api_contracts.ts
 * ===================================
 *
 * AUTO-GENERATED — DO NOT EDIT BY HAND.
 *
 * This file is assembled by frontend/scripts/assemble-contracts.mjs from:
 *   1. contracts/_url-constants.ts — hand-maintained URL constants that
 *      mirror shared/contracts/urls.py (the Python contract consistency
 *      test reads this file as text and verifies the URLs match).
 *   2. openapi-typescript output — the TypeScript types generated from
 *      contracts/openapi.json (which is itself built from the four Python
 *      FastAPI services' app.openapi() schemas by extract_openapi.py).
 *
 * Task 13.4 (SH-006) v131 ROOT FIX (Teammate 13):
 *   The previous file was hand-written TypeScript with interfaces that
 *   DIVERGED from what the Python services actually returned. This file
 *   is now generated from the source of truth — the FastAPI apps' own
 *   app.openapi() output, converted to TypeScript by openapi-typescript
 *   (the canonical tool the task required). Any change to a Python
 *   endpoint's URL, request body, or response shape is now a 2-file
 *   change: the Python service + this regenerated file. The CI check
 *   (\`npm run check:contracts\`) fails if the file is out of date.
 *
 * Generation pipeline:
 *   python3 frontend/scripts/extract_openapi.py     # → openapi.json
 *   node frontend/scripts/assemble-contracts.mjs    # → api_contracts.ts
 *     (this script runs openapi-typescript internally)
 *
 * Verification:
 *   npm run check:contracts   # fails if api_contracts.ts is out of date
 */

/* eslint-disable */
// @ts-nocheck — generated file; type errors here indicate an upstream
//               OpenAPI schema issue, not a code issue.

`;

// 5. Write the combined file
//    The preamble already has its own header comment, so we strip it and
//    use our combined header instead. We find the first non-comment, non-blank
//    line and start from there.
const preambleBody = preamble.replace(/^\/\*\*[\s\S]*?\*\/\s*/, "");
const output = HEADER + preambleBody + "\n\n" + generatedTypes;

// Allow an optional output path argument (for check-contracts.sh to generate
// to a temp file without overwriting the working tree). Defaults to the real
// contracts/api_contracts.ts path. Use path.resolve (not join) so an absolute
// temp path (e.g. /tmp/xxx from mktemp) is treated as the root, not appended.
const outputArg = process.argv[2];
const finalOutput = outputArg ? resolve(process.cwd(), outputArg) : OUTPUT;
writeFileSync(finalOutput, output, "utf-8");

// 6. Clean up temp file
try {
  execSync(`rm -f "${TMP_TYPES}"`, { stdio: "ignore" });
} catch {
  // Non-fatal — temp file cleanup is best-effort.
}

console.log(
  `[assemble-contracts] ✓ Wrote ${finalOutput} (${output.length} bytes)`,
);
console.log(
  `[assemble-contracts]   Preamble: ${preamble.length} bytes, types: ${generatedTypes.length} bytes`,
);
