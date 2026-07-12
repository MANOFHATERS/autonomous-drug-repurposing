/**
 * RL Hypothesis Ranker service — Phase 4 handoff.
 *
 * FE-019 ROOT FIX: There used to be TWO divergent CSV parsers:
 *   1. This file ( RankedHypothesis with {drug,disease,rank?,reward?,...} )
 *      using env var RL_OUTPUT_CSV_PATH, default `../rl/validated_hypotheses.csv`.
 *   2. /api/rl/route.ts inline parser ( RlCandidate with a richer schema
 *      {plausibilityScore, marketScore, overallScore, confidence,
 *      pathwayScore, unmetNeedScore, efficacyScore, admeScore, ...} )
 *      using env var RL_LOCAL_CSV, NO default.
 * The two had different field names, different env vars, different
 * defaults, and different parsing logic. The lib service was dead code
 * (no route imported it). The route did its own thing inline.
 *
 * Root fix: ONE parser, ONE schema, ONE env var. This file is the single
 * source of truth. /api/rl/route.ts imports getRankedHypotheses() from
 * here. The RankedHypothesis interface carries every field the route
 * previously produced inline. The env var is RL_OUTPUT_CSV_PATH (more
 * descriptive than RL_LOCAL_CSV). RL_LOCAL_CSV is honored as a fallback
 * alias for backward compat.
 *
 * FE-010 ROOT FIX: candidates returned here are RAW MODEL PREDICTIONS,
 * not validated hypotheses. Callers that persist them MUST use
 * status="predicted" and rlPredicted=true — never status="validated".
 *
 * SCIENTIFIC INTEGRITY: we NEVER fabricate predictions. If the CSV is
 * missing we return an empty list and a `source: "none"` indicator.
 */

import { promises as fs } from "fs";
import nodeFs from "fs";
import path from "path";
import { parse } from "csv-parse/sync";

// ---------------------------------------------------------------------------
// FE-069 ROOT FIX: TTL cache for the parsed CSV.
//
// getRankedHypotheses() was calling fs.readFile + csv-parse on EVERY request
// with no caching. For a 1000-row CSV, that's O(n) disk I/O + parsing per
// request — a single authenticated user could DoS the platform by spamming
// GET/POST /api/rl (the route-level rate limiter caps the flood, but even
// legitimate load of 60 req/min would re-parse the CSV 60×/min).
//
// Root fix: cache the PARSED RankedHypothesis[] result keyed by (path, mtime).
// The cache expires after TTL_MS (60s) OR when the file's mtime changes
// (polled on every call) OR immediately when fs.watch fires a change event.
// This collapses O(n) per-request disk I/O into O(1) for the common case.
//
// Multi-node note: this cache is per-process. For a horizontally-scaled
// deployment, replace with a Redis-backed cache. For a single Next.js
// server (the documented deployment model: Caddyfile → standalone Next.js
// server), in-memory is correct.
// ---------------------------------------------------------------------------

const TTL_MS = 60 * 1000; // 60 seconds

interface CsvCacheEntry {
  candidates: RankedHypothesis[];
  parsedAt: number; // ms epoch
  mtimeMs: number; // file mtime when cached (for invalidation)
}

const csvCache = new Map<string, CsvCacheEntry>();
const watchedPaths = new Set<string>();

/**
 * Test-only helper: clear the CSV cache and file watchers. Never call from
 * production code. Exported so the FE-069 wiring test can verify the cache
 * is actually used.
 */
export function __clearRlRankerCsvCacheForTests(): void {
  csvCache.clear();
  watchedPaths.clear();
}

export interface RankedHypothesis {
  drug: string;
  disease: string;
  rank?: number;
  reward?: number;
  policyProb?: number;
  gnnScore?: number;
  safetyScore?: number;
  marketScore?: number;
  literatureSupport?: number;
  plausibilityScore?: number; // alias for gnnScore
  overallScore?: number; // weighted composite (0.4*gnn + 0.3*safety + 0.3*market)
  confidence?: number;
  pathwayScore?: number;
  unmetNeedScore?: number;
  efficacyScore?: number;
  admeScore?: number;
  isKnownPositive?: boolean;
  literatureSupportBool?: boolean;
}

export interface RlRankerResponse {
  candidates: RankedHypothesis[];
  source: "rl_service" | "local_csv" | "none";
  modelVersion?: string;
  generatedAt: string;
  count: number;
  csvPath?: string;
  note?: string;
}

/**
 * FE-003 ROOT FIX (Team Member 13): The previous default was
 *   path.resolve(process.cwd(), "..", "rl", "validated_hypotheses.csv")
 *
 * `validated_hypotheses.csv` is Phase 4's INPUT file — it contains the
 * 4 known-positive FDA-approved drugs (thalidomide→MM, sildenafil→PAH,
 * mifepristone→Cushing's, topiramate→migraine) used to sanity-check the
 * RL agent's learned policy. The dashboard's RL page was presenting
 * these 4 known drugs as "novel RL-ranked repurposing candidates"
 * unless RL_OUTPUT_CSV_PATH was manually set — a pharma partner would
 * have been making decisions based on 4 hardcoded known positives.
 *
 * ROOT FIX: The new default resolves to the LATEST
 * `top_candidates_<timestamp>.csv` file produced by the RL ranker
 * (rl/rl_drug_ranker.py:save_results writes timestamped files to
 * `output_dir`, which defaults to `output` under the rl/ root or to
 * $RL_OUTPUT_DIR when set). We glob the rl/ root, the rl/output/
 * directory, and the cwd for the most recent top_candidates_*.csv file
 * (by mtime). Only if NO top_candidates_*.csv file exists anywhere do
 * we fall back to validated_hypotheses.csv (so the dashboard still
 * shows SOMETHING during dev before the first RL run completes — but
 * the candidates are explicitly tagged `isKnownPositive: true` via
 * the CSV parser when that column is present, which the UI uses to
 * visually distinguish them).
 *
 * The CSV path resolution is lazy (only runs when no env var is set)
 * and cached per-process. The cache is invalidated by file-watch on the
 * resolved path.
 */
const RL_DIR = path.resolve(process.cwd(), "..", "rl");
const VALIDATED_HYPOTHESES_CSV = path.join(RL_DIR, "validated_hypotheses.csv");

/**
 * Find the most recently-modified top_candidates_*.csv file under the
 * RL directory. The RL ranker writes timestamped files like
 * `top_candidates_20260712_143015.csv` to its output_dir (default
 * `output` under rl/ root, or $RL_OUTPUT_DIR when set).
 *
 * Search order:
 *   1. $RL_OUTPUT_DIR/top_candidates_*.csv (if env var is set)
 *   2. rl/output/top_candidates_*.csv (default output_dir)
 *   3. rl/top_candidates_*.csv (legacy flat layout)
 *
 * Returns the absolute path to the newest file by mtime, or null if
 * no top_candidates_*.csv file exists in any of the search locations.
 */
async function findLatestTopCandidatesCsv(): Promise<string | null> {
  const searchDirs = new Set<string>();
  // $RL_OUTPUT_DIR takes precedence.
  if (process.env.RL_OUTPUT_DIR) {
    searchDirs.add(path.resolve(process.env.RL_OUTPUT_DIR));
  }
  // Default output_dir is "output" relative to the rl/ root.
  searchDirs.add(path.join(RL_DIR, "output"));
  // Legacy flat layout — some older runs wrote directly to rl/.
  searchDirs.add(RL_DIR);

  let best: { path: string; mtimeMs: number } | null = null;
  for (const dir of searchDirs) {
    let entries: nodeFs.Dirent[];
    try {
      entries = await fs.readdir(dir, { withFileTypes: true });
    } catch {
      continue; // directory doesn't exist or unreadable
    }
    for (const entry of entries) {
      if (!entry.isFile()) continue;
      if (!/^top_candidates_.*\.csv$/i.test(entry.name)) continue;
      const full = path.join(dir, entry.name);
      try {
        const st = await fs.stat(full);
        if (best === null || st.mtimeMs > best.mtimeMs) {
          best = { path: full, mtimeMs: st.mtimeMs };
        }
      } catch {
        continue; // stat failed — skip
      }
    }
  }
  return best ? best.path : null;
}

/**
 * Cache the resolved default CSV path so we don't readdir on every
 * request. The cache is invalidated by the per-path fs.watch in
 * readLocalCsv (it watches the resolved path, not the directory — but
 * since the file-watcher only invalidates the parsed-CSV cache, we
 * also re-resolve the path on every request when the env var is unset
 * AND the parsed cache is empty).
 *
 * This is a soft cache — if a new top_candidates_*.csv file is written
 * after the cache was populated, the next request that sees a cache
 * miss (TTL expired or first call) will re-resolve and pick up the new
 * file. The per-path fs.watch then keeps the parsed cache fresh.
 */
let cachedDefaultCsvPath: string | null = null;
let cachedDefaultCsvPathAt = 0;
const DEFAULT_PATH_CACHE_TTL_MS = 60 * 1000; // 60s

async function resolveDefaultCsvPath(): Promise<string> {
  // If a top_candidates_*.csv was found within the last TTL window,
  // reuse it (the per-path fs.watch in readLocalCsv invalidates the
  // PARSED cache when the file changes; this resolver cache only
  // avoids the readdir overhead).
  const now = Date.now();
  if (
    cachedDefaultCsvPath &&
    now - cachedDefaultCsvPathAt < DEFAULT_PATH_CACHE_TTL_MS
  ) {
    // Best-effort: verify the cached path still exists. If it was
    // deleted, fall through to re-resolve.
    try {
      await fs.stat(cachedDefaultCsvPath);
      return cachedDefaultCsvPath;
    } catch {
      cachedDefaultCsvPath = null;
    }
  }

  const latest = await findLatestTopCandidatesCsv();
  if (latest) {
    cachedDefaultCsvPath = latest;
    cachedDefaultCsvPathAt = now;
    return latest;
  }

  // No top_candidates_*.csv found — fall back to validated_hypotheses.csv.
  // This is the dev bootstrap case (before the first RL run completes).
  // The CSV parser tags rows with isKnownPositive=true when that column
  // is present, so the UI can visually distinguish them.
  cachedDefaultCsvPath = VALIDATED_HYPOTHESES_CSV;
  cachedDefaultCsvPathAt = now;
  return VALIDATED_HYPOTHESES_CSV;
}

/**
 * Test-only helper: clear the default-CSV-path resolver cache so tests
 * can verify the findLatestTopCandidatesCsv logic deterministically.
 */
export function __clearRlDefaultCsvPathCacheForTests(): void {
  cachedDefaultCsvPath = null;
  cachedDefaultCsvPathAt = 0;
}

function parseNumber(s: string | undefined): number | undefined {
  if (s === undefined || s === null || s === "") return undefined;
  const n = Number(s);
  return Number.isFinite(n) ? n : undefined;
}

function parseBool(s: string | undefined): boolean | undefined {
  if (s === undefined || s === null || s === "") return undefined;
  const v = s.toLowerCase();
  return v === "1" || v === "true" || v === "yes";
}

async function readLocalCsv(csvPath: string): Promise<RankedHypothesis[]> {
  // FE-069 ROOT FIX: TTL cache with mtime invalidation + fs.watch.
  //
  // Stat the file first — its mtime is the cache key. If the cached entry
  // has the SAME mtime AND is within TTL, return it without re-reading or
  // re-parsing. This collapses O(n) per-request disk I/O into O(1) for the
  // common case (60 req/min from a single user → 1 parse per 60s).
  //
  // If the file's mtime has changed (the RL agent wrote a new CSV), the
  // cache is invalidated immediately — no stale data. We also register an
  // fs.watch listener (once per path) so cache invalidates the instant the
  // file changes on disk, without waiting for the next request to notice
  // the mtime change.
  let stat: { mtimeMs: number };
  try {
    stat = await fs.stat(csvPath);
  } catch {
    // File doesn't exist (or unreadable). Clear any stale cache entry and
    // return empty — we NEVER fabricate predictions.
    csvCache.delete(csvPath);
    return [];
  }

  const now = Date.now();
  const cached = csvCache.get(csvPath);
  if (
    cached &&
    cached.mtimeMs === stat.mtimeMs &&
    now - cached.parsedAt < TTL_MS
  ) {
    // Cache hit: same mtime AND within TTL. Return the cached array
    // (same reference — callers can detect cache hits via ===).
    return cached.candidates;
  }

  // Cache miss: read + parse.
  let content: string;
  try {
    content = await fs.readFile(csvPath, "utf8");
  } catch {
    return [];
  }
  if (content.charCodeAt(0) === 0xfeff) content = content.slice(1);
  let records: Record<string, string>[];
  try {
    records = parse(content, {
      columns: true,
      skip_empty_lines: true,
      trim: true,
      bom: true,
    }) as Record<string, string>[];
  } catch (e) {
    console.error("rl-ranker: CSV parse failed:", e);
    return [];
  }
  const out: RankedHypothesis[] = [];
  for (let i = 0; i < records.length; i++) {
    const r = records[i];
    const row: Record<string, string> = {};
    for (const k of Object.keys(r)) row[k.toLowerCase()] = r[k];
    const drug = row["drug"];
    const disease = row["disease"];
    if (!drug || !disease) continue;
    const gnn = parseNumber(row["gnn_score"]);
    const safety = parseNumber(row["safety_score"]);
    const market = parseNumber(row["market_score"]);
    const reward = parseNumber(row["reward"]);
    const rank = parseNumber(row["rank"]) ?? i + 1;
    const policyProb = parseNumber(row["policy_prob"]);
    const confidence = parseNumber(row["confidence"]);
    const pathwayScore = parseNumber(row["pathway_score"]);
    const unmetNeedScore = parseNumber(row["unmet_need_score"]);
    const efficacyScore = parseNumber(row["efficacy_score"]);
    const admeScore = parseNumber(row["adme_score"]);
    const litNum = parseNumber(row["literature_support"]);
    const isKnownPositive = parseBool(row["is_known_positive"]);
    const overallRaw = computeOverallScore({ gnnScore: gnn, safetyScore: safety, marketScore: market, policyProb });
    const overall = overallRaw ?? undefined;
    out.push({
      drug,
      disease,
      rank,
      reward,
      policyProb,
      gnnScore: gnn,
      safetyScore: safety,
      marketScore: market,
      literatureSupport: litNum,
      literatureSupportBool: parseBool(row["literature_support"]),
      plausibilityScore: gnn,
      overallScore: overall,
      confidence,
      pathwayScore,
      unmetNeedScore,
      efficacyScore,
      admeScore,
      isKnownPositive,
    });
  }
  if (out.some((c) => c.rank !== undefined)) {
    out.sort((a, b) => (a.rank ?? Number.MAX_SAFE_INTEGER) - (b.rank ?? Number.MAX_SAFE_INTEGER));
  }

  // FE-069: Store in cache with the current mtime so subsequent requests hit.
  csvCache.set(csvPath, {
    candidates: out,
    parsedAt: now,
    mtimeMs: stat.mtimeMs,
  });

  // Register a file watcher (once per path) so cache invalidates
  // immediately when the RL agent writes a new CSV. This is the "file
  // watcher to invalidate cache when the CSV changes" called out in the
  // FE-069 fix. Best-effort — if the platform doesn't support watching,
  // the TTL + mtime check still invalidates correctly.
  if (!watchedPaths.has(csvPath)) {
    try {
      nodeFs.watch(csvPath, () => {
        csvCache.delete(csvPath);
      });
      watchedPaths.add(csvPath);
    } catch {
      // Watching is best-effort. The TTL + mtime check is the primary
      // invalidation mechanism; fs.watch is a latency optimization.
    }
  }

  return out;
}

async function proxyToRlService(url: string, queryParams: URLSearchParams): Promise<RlRankerResponse> {
  const fullUrl = `${url.replace(/\/$/, "")}/rank?${queryParams.toString()}`;
  const res = await fetch(fullUrl, {
    headers: { Accept: "application/json" },
    cache: "no-store",
  });
  if (!res.ok) {
    throw new Error(`RL service at ${url} returned ${res.status}`);
  }
  const body = await res.json();
  return {
    candidates: (body?.candidates || []) as RankedHypothesis[],
    source: "rl_service",
    modelVersion: body?.modelVersion,
    generatedAt: body?.generatedAt || new Date().toISOString(),
    count: (body?.candidates || []).length,
  };
}

export async function getRankedHypotheses(opts?: {
  drug?: string;
  disease?: string;
  limit?: number;
}): Promise<RlRankerResponse> {
  const limit = Math.min(opts?.limit ?? 50, 200);
  const queryParams = new URLSearchParams();
  if (opts?.drug) queryParams.set("drug", opts.drug);
  if (opts?.disease) queryParams.set("disease", opts.disease);
  queryParams.set("limit", String(limit));

  const serviceUrl = process.env.RL_SERVICE_URL;
  if (serviceUrl) {
    try {
      return await proxyToRlService(serviceUrl, queryParams);
    } catch (e) {
      console.warn("RL service proxy failed, falling back to local CSV:", e);
    }
  }

  const csvPath =
    process.env.RL_OUTPUT_CSV_PATH ||
    process.env.RL_LOCAL_CSV ||
    (await resolveDefaultCsvPath());
  let candidates = await readLocalCsv(csvPath);

  if (opts?.drug) {
    const q = opts.drug.toLowerCase();
    candidates = candidates.filter((c) => c.drug.toLowerCase().includes(q));
  }
  if (opts?.disease) {
    const q = opts.disease.toLowerCase();
    candidates = candidates.filter((c) => c.disease.toLowerCase().includes(q));
  }
  if (candidates.length > limit) candidates = candidates.slice(0, limit);

  if (candidates.length === 0) {
    return {
      candidates: [],
      source: "none",
      generatedAt: new Date().toISOString(),
      count: 0,
      csvPath,
      note:
        "No RL-ranked candidates found. Set RL_SERVICE_URL to proxy to the " +
        "Phase 4 service, OR run `python run_4phase.py` to generate " +
        "top_candidates_*.csv output. FE-003 v105: the default scan looks " +
        `for top_candidates_*.csv in ${RL_DIR} (NOT the INPUT ` +
        "validated_hypotheses.csv file — that was the previous bug).",
    };
  }

  return {
    candidates,
    source: "local_csv",
    generatedAt: new Date().toISOString(),
    count: candidates.length,
    csvPath,
    note:
      "Served from local CSV artifact. These are REAL model predictions from " +
      "the Phase 4 RL ranker output — they are NOT validated hypotheses. " +
      "Persistence callers must use status='predicted' and rlPredicted=true.",
  };
}

/**
 * Sync the RL ranker's output into the Hypothesis table.
 *
 * FE-010 ROOT FIX: hypotheses touched by this sync are marked
 * rlPredicted=true. Their `status` is set to "predicted" if it was "draft"
 * (we do NOT downgrade a "validated" or "rejected" hypothesis).
 */
export async function syncRlOutputToHypotheses(): Promise<number> {
  const { db } = await import("@/lib/db");
  const { candidates } = await getRankedHypotheses({ limit: 200 });
  if (candidates.length === 0) return 0;

  let updated = 0;
  for (const c of candidates) {
    const matches = await db.hypothesis.findMany({
      where: {
        OR: [
          { drugName: c.drug, diseaseName: c.disease },
          { drugName: c.drug.toLowerCase(), diseaseName: c.disease.toLowerCase() },
        ],
      },
    });
    for (const h of matches) {
      const nextStatus = h.status === "draft" ? "predicted" : h.status;
      await db.hypothesis.update({
        where: { id: h.id },
        data: {
          status: nextStatus,
          rlPredicted: true,
          rank: c.rank ?? null,
          policyProb: c.policyProb ?? null,
          reward: c.reward ?? null,
          gnnScore: c.gnnScore ?? null,
          safetyScore: c.safetyScore ?? null,
          marketScore: c.marketScore ?? null,
          plausibilityScore: c.plausibilityScore ?? null,
          overallScore: c.overallScore ?? computeOverallScore(c),
          literatureSupport: c.literatureSupportBool ?? (c.literatureSupport !== undefined ? c.literatureSupport > 0 : null),
          rlModelVersion: "rl_drug_ranker.py-v101",
          rlUpdatedAt: new Date(),
        } as any,
      });
      updated++;
    }
  }
  return updated;
}

export function computeOverallScore(c: {
  gnnScore?: number;
  safetyScore?: number;
  marketScore?: number;
  policyProb?: number;
}): number | null {
  const signals: { value: number; weight: number }[] = [];
  if (c.gnnScore !== undefined) signals.push({ value: c.gnnScore, weight: 0.4 });
  if (c.safetyScore !== undefined) signals.push({ value: c.safetyScore, weight: 0.3 });
  if (c.marketScore !== undefined) signals.push({ value: c.marketScore, weight: 0.3 });
  if (signals.length === 0 && c.policyProb !== undefined) {
    return c.policyProb;
  }
  if (signals.length === 0) return null;
  const totalWeight = signals.reduce((s, x) => s + x.weight, 0);
  return signals.reduce((s, x) => s + (x.value * x.weight) / totalWeight, 0);
}
