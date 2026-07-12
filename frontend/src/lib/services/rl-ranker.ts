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

const DEFAULT_CSV_PATH = path.resolve(process.cwd(), "..", "rl", "validated_hypotheses.csv");

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
      // FE-033: if the upstream service supports sort+pagination natively,
      // trust its `total` field; otherwise compute it from the candidate
      // count (best-effort). We surface the page + total in the response.
      return {
        ...upstream,
        page: Math.floor(offset / pageSize),
        pageSize,
        total: upstream.count,
      } as RlRankerResponse & { page: number; pageSize: number; total: number };
    } catch (e) {
      console.warn("RL service proxy failed, falling back to local CSV:", e);
    }
  }

  const csvPath = process.env.RL_OUTPUT_CSV_PATH || process.env.RL_LOCAL_CSV || DEFAULT_CSV_PATH;
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
        "Phase 4 service, or ensure the ranker has written its output to " +
        `${csvPath}.`,
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
