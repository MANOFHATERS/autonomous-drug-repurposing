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

// BE-027 ROOT FIX (Team Member 12): the previous code had TWO independent
// CSV caches — this one inside rl-ranker.ts AND a separate one inside
// rl-csv-cache.ts. The /api/rl/refresh route cleared only rl-csv-cache.ts's
// cache, leaving THIS cache stale. The operator's "Refresh" click was a
// no-op for the actual /api/rl route that serves RL data (which uses
// rl-ranker.ts's cache via readLocalCsv).
//
// Root fix: delete rl-csv-cache.ts entirely (it was dead code — no
// production route imported its `readRlCsvCached`). Expose PRODUCTION-SAFE
// clear + inspect functions here so /api/rl/refresh can evict the cache
// that the /api/rl route actually uses. The `__clearRlRankerCsvCacheForTests`
// alias above is kept for backward-compat with existing test imports.

/**
 * Production-safe cache clear. Evicts ALL parsed-CSV entries from the
 * rl-ranker cache. Called by POST /api/rl/refresh when an operator clicks
 * the "Refresh" button on the dashboard.
 *
 * Multi-node note: this clears the cache ONLY on the receiving node. For
 * a horizontally-scaled deployment, the refresh endpoint should broadcast
 * a Redis pub/sub message so all nodes clear their caches. For the
 * documented single-instance deployment, in-memory clear is sufficient.
 */
export function clearRlRankerCsvCache(): void {
  csvCache.clear();
}

/**
 * Inspect the cache state for observability / debugging. Returns the list
 * of cached paths and their parsedAt timestamps. Used by /api/rl/refresh
 * to report `clearedEntries` in the audit log.
 */
export function getRlRankerCsvCacheState(): Array<{
  path: string;
  parsedAt: number;
  mtimeMs: number;
  candidateCount: number;
  ageMs: number;
  ttlRemainingMs: number;
}> {
  const now = Date.now();
  return Array.from(csvCache.entries()).map(([p, entry]) => ({
    path: p,
    parsedAt: entry.parsedAt,
    mtimeMs: entry.mtimeMs,
    candidateCount: entry.candidates.length,
    ageMs: now - entry.parsedAt,
    ttlRemainingMs: Math.max(0, TTL_MS - (now - entry.parsedAt)),
  }));
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
  // RT-008 ROOT FIX: csvPath is nullable — when no top_candidates_*.csv
  // exists yet, this is null (we do NOT fall back to validated_hypotheses.csv).
  csvPath?: string | null;
  note?: string;
  /**
   * FE-033: pagination metadata. `total` is the count AFTER filtering but
   * BEFORE pagination. `page` is 0-indexed. `pageSize` is the page size used.
   * Callers use these to render "Showing X–Y of Z" and pagination controls.
   * Optional for backward compat with upstream services that don't return them.
   */
  page?: number;
  pageSize?: number;
  total?: number;
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

async function resolveDefaultCsvPath(): Promise<string | null> {
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

  // RT-008 + FE-003 ROOT FIX: NO top_candidates_*.csv found. Return null.
  // The previous FE-003 code fell back to validated_hypotheses.csv (the
  // INPUT file) — but RT-008 found that this is scientifically wrong: the
  // dashboard would present known drugs (thalidomide, metformin, etc.)
  // as "novel RL-ranked repurposing candidates". The fix: return null
  // and let the caller (getRankedHypotheses) surface a clear
  // "no RL output yet" message. We NEVER fall back to the INPUT file.
  cachedDefaultCsvPath = null;
  cachedDefaultCsvPathAt = now;
  return null;
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
    // BE-072 ROOT FIX: The previous code ALWAYS computed overallScore in
    // TypeScript using fixed weights (0.4*gnn + 0.3*safety + 0.3*market),
    // OVERWRITING the value that the Python RL ranker computed. If the
    // Python ranker used different weights (which it does — the agent's
    // RewardConfig uses gnn=0.04, safety=0.25, market=0.12), the displayed
    // overallScore would NOT match what the ranker actually computed.
    // This causes confusion when debugging: the dashboard shows one score
    // while the RL logs show another.
    //
    // Root fix: If the CSV already contains an overall_score (computed by
    // the Python ranker using its actual reward weights), USE IT. Only
    // fall back to TS computation if the CSV doesn't have it. This ensures
    // the dashboard displays the SAME score the RL agent computed.
    const csvOverall = parseNumber(row["overall_score"]);
    const overallRaw = csvOverall !== undefined
      ? csvOverall
      : computeOverallScore({ gnnScore: gnn, safetyScore: safety, marketScore: market, policyProb });
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
  // INT-022 ROOT FIX: pass through pagination fields from upstream service.
  // The upstream now returns total/page/pageSize for proper pagination.
  const candidates = (body?.candidates || []) as RankedHypothesis[];
  return {
    candidates,
    source: "rl_service",
    modelVersion: body?.modelVersion,
    generatedAt: body?.generatedAt || new Date().toISOString(),
    total: typeof body?.total === "number" ? body.total : candidates.length,
    page: typeof body?.page === "number" ? body.page : 0,
    pageSize: typeof body?.pageSize === "number" ? body.pageSize : 50,
    count: candidates.length,
  };
}

/**
 * FE-033 ROOT FIX: Server-side sort + pagination.
 *
 * The previous version only supported `drug`, `disease`, `limit`. Sorting was
 * done client-side by the candidate-table.tsx component (which actually
 * didn't sort at all — it just rendered whatever order the API returned).
 * For 100K candidates (production scale per the audit), client-side sort
 * freezes the browser for ~5 seconds. Pagination didn't exist either — the
 * table rendered ALL candidates, which is unusable at production scale.
 *
 * Root fix: getRankedHypotheses now accepts `sort`, `sortDir`, `offset`, and
 * `pageSize`. The sort is applied to the filtered candidates array BEFORE
 * slicing, so the caller gets the correctly-sorted page. The response
 * includes `total` (count after filtering, before pagination) so the caller
 * can render "Showing X–Y of Z" and pagination controls.
 *
 * Sort fields map to RankedHypothesis properties:
 *   - 'rank'           (default — the RL agent's native ranking)
 *   - 'overallScore'   (the weighted composite: 0.4*gnn + 0.3*safety + 0.3*market)
 *   - 'gnnScore'       (raw Phase 3 graph transformer score)
 *   - 'safetyScore'    (Phase 4 safety signal)
 *   - 'marketScore'    (Phase 4 market opportunity score)
 *   - 'reward'         (RL reward signal — for ML engineers debugging)
 *   - 'drug'           (alphabetical by drug name)
 *   - 'disease'        (alphabetical by disease name)
 *
 * The `sortDir` is 'asc' or 'desc'. Default: 'asc' for rank, 'desc' for
 * scores.
 */
export type RlSortField =
  | 'rank'
  | 'overallScore'
  | 'gnnScore'
  | 'safetyScore'
  | 'marketScore'
  | 'reward'
  | 'drug'
  | 'disease';

export type RlSortDir = 'asc' | 'desc';

export async function getRankedHypotheses(opts?: {
  drug?: string;
  disease?: string;
  limit?: number;
  /** FE-033: server-side sort field. Default: 'rank'. */
  sort?: RlSortField;
  /** FE-033: sort direction. Default: 'asc' for rank, 'desc' for scores. */
  sortDir?: RlSortDir;
  /** FE-033: offset for pagination. Default: 0. */
  offset?: number;
  /** FE-033: page size for pagination. Capped at 200. Default: 50. */
  pageSize?: number;
}): Promise<RlRankerResponse> {
  // FE-033: `limit` is kept for backward compat (it sets pageSize when
  // pageSize is not provided). New callers should use pageSize + offset.
  const pageSize = Math.min(opts?.pageSize ?? opts?.limit ?? 50, 200);
  const offset = Math.max(0, opts?.offset ?? 0);
  const sortField: RlSortField = opts?.sort ?? 'rank';
  // Default direction: 'asc' for rank (1, 2, 3...), 'desc' for scores
  // (highest first). 'drug' and 'disease' default to 'asc' (alphabetical).
  const defaultDir: RlSortDir =
    sortField === 'rank' || sortField === 'drug' || sortField === 'disease' ? 'asc' : 'desc';
  const sortDir: RlSortDir = opts?.sortDir ?? defaultDir;

  const queryParams = new URLSearchParams();
  if (opts?.drug) queryParams.set("drug", opts.drug);
  if (opts?.disease) queryParams.set("disease", opts.disease);
  // FE-033: pass pagination + sort params to the upstream RL service too.
  queryParams.set("limit", String(pageSize));
  queryParams.set("offset", String(offset));
  queryParams.set("sort", sortField);
  queryParams.set("sortDir", sortDir);

  const serviceUrl = process.env.RL_SERVICE_URL;
  if (serviceUrl) {
    try {
      const upstream = await proxyToRlService(serviceUrl, queryParams);
      // BE-013 + BE-070 + BE-026 ROOT FIX (merged — three teams independently
      // identified the same root cause and converged on the same fix).
      //
      // The prior code at this exact line was:
      //     total: upstream.count,
      // which OVERWROTE the correct `upstream.total` (the count of ALL
      // matching candidates after filtering, BEFORE pagination — see
      // proxyToRlService line 441 and rl/service.py _rank_impl line 428)
      // with `upstream.count` (the count of candidates IN THIS PAGE —
      // see proxyToRlService line 444). The result: the dashboard showed
      // "Showing 1–50 of 50" even when the upstream service had 10,000
      // matching candidates. Users could NEVER navigate beyond page 1
      // because the pagination control thought there was only one page.
      //
      // Root fix: use `upstream.total` (the true filtered count). The
      // proxyToRlService helper guarantees `total` is always a number
      // (it falls back to `candidates.length` only when the upstream
      // service doesn't return one — line 441). The typeof guard is a
      // type-safe assertion that also documents the invariant. If a
      // future change breaks the proxy contract, the fallback to
      // `upstream.count` prevents a NaN from reaching the dashboard.
      //
      // We DO override `page` and `pageSize` here because the caller's
      // `offset` and `pageSize` are the source of truth — the upstream
      // may have echoed back slightly different values due to rounding
      // or defaults. The `total` is the upstream's alone (we cannot
      // compute it without fetching ALL candidates, which would defeat
      // the purpose of pagination).
      return {
        ...upstream,
        page: Math.floor(offset / pageSize),
        pageSize,
        // BE-013/BE-070/BE-026: trust upstream.total — do NOT override with count.
        total: typeof upstream.total === "number" ? upstream.total : upstream.count,
      } as RlRankerResponse & { page: number; pageSize: number; total: number; count: number };
    } catch (e) {
      console.warn("RL service proxy failed, falling back to local CSV:", e);
    }
  }

  // RT-008 + FE-003 ROOT FIX: resolve the RL OUTPUT path (never the
  // INPUT file). If no top_candidates_*.csv exists yet, return an empty
  // list with a clear "no RL output yet" note — do NOT fall back to
  // validated_hypotheses.csv.
  const csvPath =
    process.env.RL_OUTPUT_CSV_PATH ||
    process.env.RL_LOCAL_CSV ||
    (await resolveDefaultCsvPath());
  if (csvPath === null) {
    return {
      candidates: [],
      source: "none",
      generatedAt: new Date().toISOString(),
      count: 0,
      csvPath: null,
      note:
        "No RL-ranked candidates found. The Phase 4 RL ranker has not yet " +
        "written a top_candidates_*.csv output. Run `python run_4phase.py` " +
        "(or set RL_OUTPUT_CSV_PATH to point at an existing output CSV). " +
        "RT-008 + FE-003 ROOT FIX: this route NEVER serves the INPUT file " +
        "(rl/validated_hypotheses.csv) as candidate output.",
    };
  }
  let candidates = await readLocalCsv(csvPath);

  if (opts?.drug) {
    const q = opts.drug.toLowerCase();
    candidates = candidates.filter((c) => c.drug.toLowerCase().includes(q));
  }
  if (opts?.disease) {
    const q = opts.disease.toLowerCase();
    candidates = candidates.filter((c) => c.disease.toLowerCase().includes(q));
  }

  const total = candidates.length;

  // FE-033: Apply server-side sort BEFORE pagination. This is the root fix —
  // the candidate table no longer sorts client-side.
  //
  // CACHE PRESERVATION: readLocalCsv already sorts by rank ascending. When
  // the caller requests the default sort (rank/asc), we skip the sort
  // entirely so the cached array reference is preserved (FE-069 cache-hit
  // test depends on this). When the caller requests a non-default sort, we
  // make a defensive copy first (so we never mutate the cached array or its
  // element objects) and compute overallScore lazily for sorting.
  const needsSort = sortField !== 'rank' || sortDir !== 'asc';
  let sortedCandidates = candidates;
  if (needsSort) {
    // Defensive copy — never mutate the cached array or its element objects.
    sortedCandidates = candidates.map((c) => {
      if (c.overallScore === undefined) {
        const computed = computeOverallScore(c);
        if (computed !== null) return { ...c, overallScore: computed };
      }
      return { ...c };
    });
    const dirMul = sortDir === 'asc' ? 1 : -1;
    sortedCandidates.sort((a, b) => {
      const av = a[sortField];
      const bv = b[sortField];
      if (av === undefined && bv === undefined) return 0;
      if (av === undefined) return 1; // undefined sorts last regardless of dir
      if (bv === undefined) return -1;
      if (typeof av === 'number' && typeof bv === 'number') {
        return (av - bv) * dirMul;
      }
      return String(av).localeCompare(String(bv)) * dirMul;
    });
  }

  // FE-033: Apply pagination AFTER sort. When offset=0 and pageSize >= total,
  // slice returns the full array — but still a new array reference. To
  // preserve the FE-069 cache-hit invariant (same array reference on
  // repeated calls with default params), we skip slice when it would be a
  // no-op AND no sort was applied.
  const needsSlice = offset > 0 || pageSize < total;
  let paged: RankedHypothesis[];
  if (needsSort || needsSlice) {
    paged = sortedCandidates.slice(offset, offset + pageSize);
  } else {
    // Default params, no filtering — return the cached array reference.
    paged = sortedCandidates;
  }

  if (total === 0) {
    return {
      candidates: [],
      source: "none",
      generatedAt: new Date().toISOString(),
      count: 0,
      csvPath,
      page: Math.floor(offset / pageSize),
      pageSize,
      total: 0,
      note:
        "No RL-ranked candidates found. Set RL_SERVICE_URL to proxy to the " +
        "Phase 4 service, OR run `python run_4phase.py` to generate " +
        "top_candidates_*.csv output. FE-003 v105: the default scan looks " +
        `for top_candidates_*.csv in ${RL_DIR} (NOT the INPUT ` +
        "validated_hypotheses.csv file — that was the previous bug).",
    } as RlRankerResponse & { page: number; pageSize: number; total: number };
  }

  return {
    candidates: paged,
    source: "local_csv",
    generatedAt: new Date().toISOString(),
    count: paged.length,
    csvPath,
    page: Math.floor(offset / pageSize),
    pageSize,
    total,
    note:
      "Served from local CSV artifact. These are REAL model predictions from " +
      "the Phase 4 RL ranker output — they are NOT validated hypotheses. " +
      "Persistence callers must use status='predicted' and rlPredicted=true.",
  } as RlRankerResponse & { page: number; pageSize: number; total: number };
}

/**
 * BE-071 ROOT FIX: syncRlOutputToHypotheses was DEAD CODE — exported but
 * NEVER called by any route. It performed 200 findMany queries (one per
 * candidate), each with a non-indexed OR clause on drugName + diseaseName
 * (lowercase variants). For 200 candidates, that's 200 full table scans
 * of the Hypothesis table — a performance disaster if ever called.
 *
 * The OR clause with `drugName: c.drug.toLowerCase()` would never match
 * because the DB stores the original case (unless the DB collation is
 * case-insensitive), making the entire function produce false negatives.
 *
 * Root fix: DELETE the function. The actual RL-to-hypothesis sync is
 * performed by `persistRlCandidates` in `/api/rl/route.ts`, which:
 *   - Uses a proper indexed lookup (findFirst with exact drugName + diseaseName)
 *   - Has correct org scoping and ownership checks
 *   - Is actually called from the route handler
 *
 * If a cron-based sync is needed in the future, it should call the
 * existing `persistRlCandidates` function rather than duplicating the logic.
 *
 * NOTE TO FUTURE DEVELOPERS: Do NOT re-implement this function. Use
 * `persistRlCandidates` from `/api/rl/route.ts` instead. It is the
 * single source of truth for RL hypothesis persistence.
 */

/**
 * INT-024 ROOT FIX: weights MUST match the RL agent's reward function.
 * The previous code used 0.4/0.3/0.3 (gnn/safety/market) which produced
 * DIFFERENT rankings than the agent learned. The agent uses the weights
 * from reward_weights.yaml: gnn=0.04, safety=0.25, market=0.12 (capped).
 *
 * The overallScore is a HUMAN-READABLE composite for the dashboard.
 * The ACTUAL ranking is by policyProb (the agent's policy probability).
 * When policyProb is available, sorting uses that directly.
 */
export function computeOverallScore(c: {
  gnnScore?: number;
  safetyScore?: number;
  marketScore?: number;
  policyProb?: number;
}): number | null {
  // INT-024: if policyProb is available, use it directly — this is what
  // the RL agent actually uses for ranking. No synthetic score needed.
  if (c.policyProb !== undefined && c.policyProb !== null) {
    return c.policyProb;
  }
  // Fallback: compute weighted composite matching reward_weights.yaml.
  // Weights: gnn=0.04, safety=0.25, market=0.12 (default profile).
  const signals: { value: number; weight: number }[] = [];
  if (c.gnnScore !== undefined) signals.push({ value: c.gnnScore, weight: 0.04 });
  if (c.safetyScore !== undefined) signals.push({ value: c.safetyScore, weight: 0.25 });
  if (c.marketScore !== undefined) signals.push({ value: c.marketScore, weight: 0.12 });
  if (signals.length === 0) return null;
  const totalWeight = signals.reduce((s, x) => s + x.weight, 0);
  return signals.reduce((s, x) => s + (x.value * x.weight) / totalWeight, 0);
}
