/**
 * Graph Transformer (Phase 3) inference service — RT-006 ROOT FIX.
 *
 * Team Member 17: the audit (RT-006) found that
 * `graph_transformer/inference/__init__.py` exports
 * `predict_drug_disease_scores()` and `top_k_novel_predictions()`, but
 * NO API route in `frontend/src/app/api/` invokes them. There was no
 * `/api/predict` or `/api/top-k` route. A researcher asking
 * "what is the GT score for drug X -> disease Y?" could not get an
 * answer — the core ML model was unreachable from the dashboard.
 *
 * Root fix: this service loads the trained GT checkpoint from disk
 * (written by run_4phase.py -> GTRLBridge to <output_dir>/checkpoints/)
 * and exposes two methods that mirror the Python inference module:
 *
 *   1. predictPairs(pairs: [{drug, disease}]): scores for arbitrary pairs
 *   2. topKNovel(topK: number): highest-scoring novel (drug, disease) pairs
 *
 * The service shells out to a small Python helper (`gt_inference.py`)
 * rather than reimplementing the model in JS. This guarantees the JS
 * and Python paths produce IDENTICAL predictions (no drift). The
 * helper is invoked via `python3` with the repo root on sys.path.
 *
 * SCIENTIFIC INTEGRITY: if no checkpoint exists, we return
 * `source: "none"` with an empty list — we NEVER fabricate predictions.
 * A researcher who sees an empty list knows to run `python run_4phase.py`
 * to train the model.
 */

import { promises as fs } from "fs";
import nodeFs from "fs";
import path from "path";
import { spawn } from "child_process";
import { randomUUID } from "crypto";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface DrugDiseasePair {
  drug: string;
  disease: string;
}

export interface GtPrediction {
  drug: string;
  disease: string;
  score: number; // [0, 1]
}

export interface GtInferenceResponse {
  predictions: GtPrediction[];
  source: "gt_checkpoint" | "none";
  modelVersion?: string;
  generatedAt: string;
  count: number;
  checkpointPath?: string | null;
  note?: string;
}

// ---------------------------------------------------------------------------
// Checkpoint resolution
// ---------------------------------------------------------------------------

// BE-008 ROOT FIX: checkpoint search paths must resolve relative to the
// REPO ROOT, not process.cwd(). The previous code used process.cwd()
// directly — when Next.js runs from `frontend/` (the documented
// deployment: `cd frontend && npm run dev`), the resolved paths were
// `frontend/output_v100`, `frontend/graph_transformer/checkpoints`, etc.
// — none of which exist. The actual checkpoints are at the REPO root:
// `<repo>/output_v100`, `<repo>/graph_transformer/checkpoints`. Every
// `/api/predict` and `/api/top-k` request silently fell back to
// `source: "none"` and the GT prediction feature was non-functional.
//
// Root fix: compute the repo root ONCE using the same logic as
// runPythonInference (env var GT_REPO_ROOT wins; else if process.cwd()
// ends with "frontend" we go up one level; else we use process.cwd()
// as-is). Then resolve all candidate dirs relative to that repo root.
// This guarantees the checkpoint search finds files at the SAME path
// the Python helper uses (which also receives `repoRoot` as its CWD).
//
// The array is built lazily inside findLatestGtCheckpoint() rather than
// at module-load time, so changes to process.env.GT_REPO_ROOT at runtime
// (e.g. via dotenv) are picked up. The previous module-load-time
// evaluation froze the paths at first import.
function getRepoRoot(): string {
  const cwd = process.cwd();
  return process.env.GT_REPO_ROOT || (
    cwd.endsWith("frontend") ? path.resolve(cwd, "..") : cwd
  );
}

function getCheckpointCandidateDirs(): string[] {
  const repoRoot = getRepoRoot();
  return [
    process.env.GT_CHECKPOINT_DIR,
    // RT-006: the bridge writes gt_checkpoint.pt directly to <output_dir>
    // (not to <output_dir>/checkpoints/). Check both locations for safety.
    path.resolve(repoRoot, "output_v100"),
    path.resolve(repoRoot, "output_v100", "checkpoints"),
    path.resolve(repoRoot, "output"),
    path.resolve(repoRoot, "output", "checkpoints"),
    path.resolve(repoRoot, "graph_transformer", "checkpoints"),
    // BE-008: also check the frontend-relative paths as a last resort,
    // for dev setups where the user copied a checkpoint into frontend/.
    // This is NOT the documented deployment but provides backwards compat.
    path.resolve(process.cwd(), "output_v100"),
    path.resolve(process.cwd(), "output"),
    path.resolve(process.cwd(), "graph_transformer", "checkpoints"),
  ].filter(Boolean) as string[];
}

/**
 * Find the latest trained GT checkpoint. The bridge writes
 * `gt_checkpoint.pt` to <output_dir>/ (run_4phase.py default output is
 * ./output_v100/). We pick the most-recently-modified `.pt` file across
 * the candidate directories, preferring files named `gt_checkpoint.pt`
 * or `best_model.pt`.
 *
 * Returns null if no checkpoint exists.
 */
function findLatestGtCheckpoint(): string | null {
  for (const dir of getCheckpointCandidateDirs()) {
    let entries: string[] = [];
    try {
      entries = nodeFs.readdirSync(dir);
    } catch {
      continue;
    }
    // Prefer canonical names first, then fall back to any .pt file.
    const PREFERRED = ["gt_checkpoint.pt", "best_model.pt"];
    for (const pref of PREFERRED) {
      const full = path.join(dir, pref);
      try {
        if (nodeFs.statSync(full).isFile()) return full;
      } catch {
        // skip
      }
    }
    // Fall back to any .pt file (sorted by mtime desc).
    const candidates = entries
      .filter((name) => /\.pt$/i.test(name) && !/graph_state/i.test(name))
      .map((name) => path.join(dir, name))
      .filter((full) => {
        try {
          return nodeFs.statSync(full).isFile();
        } catch {
          return false;
        }
      });
    if (candidates.length === 0) continue;
    let best = candidates[0];
    let bestMtime = -Infinity;
    for (const c of candidates) {
      try {
        const m = nodeFs.statSync(c).mtimeMs;
        if (m > bestMtime) {
          bestMtime = m;
          best = c;
        }
      } catch {
        // skip
      }
    }
    return best;
  }
  return null;
}

// ---------------------------------------------------------------------------
// Python inference helper invocation
// ---------------------------------------------------------------------------

/**
 * P3-002 ROOT FIX: if GT_SERVICE_URL is set, proxy to the long-running
 * FastAPI service (graph_transformer/service.py) via HTTP instead of
 * spawning a subprocess per request. This is the high-concurrency path
 * (V1 contract: 100 concurrent requests). The HTTP service returns the
 * SAME response shape as the subprocess path, so callers see no
 * difference.
 *
 * If GT_SERVICE_URL is NOT set, fall back to the subprocess path
 * (scripts/gt_inference.py). This is the default for dev/CI.
 */
async function runHttpInference(
  mode: "predict" | "top_k",
  payload: { pairs?: DrugDiseasePair[]; top_k?: number }
): Promise<{ predictions: GtPrediction[]; modelVersion?: string } | null> {
  const serviceUrl = process.env.GT_SERVICE_URL;
  if (!serviceUrl) return null; // fall back to subprocess

  const endpoint = mode === "predict" ? "/predict" : "/top-k";
  const url = serviceUrl.replace(/\/$/, "") + endpoint;

  const resp = await fetch(url, {
    method: mode === "predict" ? "POST" : "GET",
    headers: { "Content-Type": "application/json" },
    body: mode === "predict" ? JSON.stringify({ pairs: payload.pairs }) : undefined,
  });

  if (!resp.ok) {
    const text = await resp.text().catch(() => "");
    throw new Error(`GT service ${url} returned ${resp.status}: ${text.slice(0, 500)}`);
  }

  const data = await resp.json();
  // The HTTP service returns the SAME shape as the subprocess path
  // (predictions, modelVersion, source, etc.) per the P3-002 fix.
  const predictions: GtPrediction[] = (data.predictions || []).map(
    (p: { drug: string; disease: string; score: number }) => ({
      drug: p.drug,
      disease: p.disease,
      score: p.score,
    })
  );
  return { predictions, modelVersion: data.modelVersion };
}

/**
 * Spawn `gt_inference.py` (a small Python helper) to run the actual
 * model inference. The helper loads the checkpoint, runs
 * `predict_drug_disease_scores` or `top_k_novel_predictions`, and
 * writes JSON to stdout.
 *
 * We use a tmp file for the request payload (not stdin) so large pair
 * lists don't overflow the OS pipe buffer.
 */
async function runPythonInference(
  checkpointPath: string,
  mode: "predict" | "top_k",
  payload: { pairs?: DrugDiseasePair[]; top_k?: number }
): Promise<{ predictions: GtPrediction[]; modelVersion?: string }> {
  const tmpDir = "/tmp";
  const reqId = randomUUID();
  const reqPath = path.join(tmpDir, `gt_inference_req_${reqId}.json`);
  const respPath = path.join(tmpDir, `gt_inference_resp_${reqId}.json`);

  try {
    await fs.writeFile(reqPath, JSON.stringify({ checkpoint: checkpointPath, mode, ...payload }));

    // INT-027 ROOT FIX: resolve repoRoot correctly when Next.js runs from
    // frontend/. process.cwd() returns frontend/ but scripts/ is at repo root.
    const cwd = process.cwd();
    const repoRoot = process.env.GT_REPO_ROOT || (
      cwd.endsWith("frontend") ? path.resolve(cwd, "..") : cwd
    );
    const scriptPath = path.resolve(repoRoot, "scripts", "gt_inference.py");

    // If the helper doesn't exist, fail gracefully — caller surfaces a
    // clear message. (The script is shipped with the repo per RT-006 fix.)
    if (!nodeFs.existsSync(scriptPath)) {
      throw new Error(`GT inference helper not found at ${scriptPath}. Run 'python run_4phase.py' first to train the model and ensure scripts/gt_inference.py is present.`);
    }

    await new Promise<void>((resolve, reject) => {
      const child = spawn("python3", [scriptPath, reqPath, respPath], {
        cwd: repoRoot,
        env: { ...process.env, PYTHONPATH: repoRoot },
      });
      let stderr = "";
      child.stderr.on("data", (d) => { stderr += d.toString(); });
      child.on("error", reject);
      child.on("close", (code) => {
        if (code !== 0) reject(new Error(`gt_inference.py exited ${code}: ${stderr.slice(0, 1000)}`));
        else resolve();
      });
    });

    const respRaw = await fs.readFile(respPath, "utf8");
    const resp = JSON.parse(respRaw);
    if (resp.error) throw new Error(resp.error);
    return { predictions: resp.predictions || [], modelVersion: resp.model_version };
  } finally {
    // Best-effort cleanup
    try { await fs.unlink(reqPath); } catch { /* ignore */ }
    try { await fs.unlink(respPath); } catch { /* ignore */ }
  }
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Score arbitrary (drug, disease) pairs with the trained GT model.
 *
 * Returns `{source: "none", predictions: [], ...}` if no checkpoint
 * exists — we NEVER fabricate scores.
 */
export async function predictPairs(pairs: DrugDiseasePair[]): Promise<GtInferenceResponse> {
  if (pairs.length === 0) {
    return {
      predictions: [],
      source: "none",
      generatedAt: new Date().toISOString(),
      count: 0,
      checkpointPath: null,
      note: "No pairs supplied.",
    };
  }

  // P3-002 ROOT FIX: try HTTP service first (if GT_SERVICE_URL is set),
  // then fall back to subprocess. The HTTP service is the high-concurrency
  // path for production (V1 contract: 100 concurrent requests).
  try {
    const httpResult = await runHttpInference("predict", { pairs });
    if (httpResult !== null) {
      return {
        predictions: httpResult.predictions,
        source: "gt_checkpoint",
        modelVersion: httpResult.modelVersion,
        generatedAt: new Date().toISOString(),
        count: httpResult.predictions.length,
        checkpointPath: null,
      };
    }
  } catch (e: unknown) {
    // HTTP service is configured but failed — log and fall back to subprocess
    const msg = e instanceof Error ? e.message : String(e);
    console.warn(`[gt-inference] GT_SERVICE_URL call failed, falling back to subprocess: ${msg}`);
  }

  const checkpointPath = findLatestGtCheckpoint();
  if (checkpointPath === null) {
    return {
      predictions: [],
      source: "none",
      generatedAt: new Date().toISOString(),
      count: 0,
      checkpointPath: null,
      note:
        "No trained Graph Transformer checkpoint found. Run " +
        "`python run_4phase.py` to train the model first. RT-006 ROOT FIX: " +
        "this endpoint NEVER fabricates GT scores.",
    };
  }

  try {
    const { predictions, modelVersion } = await runPythonInference(checkpointPath, "predict", { pairs });
    return {
      predictions,
      source: "gt_checkpoint",
      modelVersion,
      generatedAt: new Date().toISOString(),
      count: predictions.length,
      checkpointPath,
    };
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e);
    return {
      predictions: [],
      source: "none",
      generatedAt: new Date().toISOString(),
      count: 0,
      checkpointPath,
      note: `GT inference failed: ${msg}`,
    };
  }
}

/**
 * Return the top-K highest-scoring NOVEL (drug, disease) pairs from the
 * trained GT model. "Novel" = not in the known_pairs list.
 */
export async function topKNovel(topK: number = 50): Promise<GtInferenceResponse> {
  // P3-002 ROOT FIX: try HTTP service first, then fall back to subprocess.
  try {
    const httpResult = await runHttpInference("top_k", { top_k: topK });
    if (httpResult !== null) {
      return {
        predictions: httpResult.predictions,
        source: "gt_checkpoint",
        modelVersion: httpResult.modelVersion,
        generatedAt: new Date().toISOString(),
        count: httpResult.predictions.length,
        checkpointPath: null,
      };
    }
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e);
    console.warn(`[gt-inference] GT_SERVICE_URL top_k call failed, falling back to subprocess: ${msg}`);
  }

  const checkpointPath = findLatestGtCheckpoint();
  if (checkpointPath === null) {
    return {
      predictions: [],
      source: "none",
      generatedAt: new Date().toISOString(),
      count: 0,
      checkpointPath: null,
      note:
        "No trained Graph Transformer checkpoint found. Run " +
        "`python run_4phase.py` to train the model first. RT-006 ROOT FIX.",
    };
  }

  try {
    const { predictions, modelVersion } = await runPythonInference(checkpointPath, "top_k", { top_k: topK });
    return {
      predictions,
      source: "gt_checkpoint",
      modelVersion,
      generatedAt: new Date().toISOString(),
      count: predictions.length,
      checkpointPath,
    };
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e);
    return {
      predictions: [],
      source: "none",
      generatedAt: new Date().toISOString(),
      count: 0,
      checkpointPath,
      note: `GT inference failed: ${msg}`,
    };
  }
}
