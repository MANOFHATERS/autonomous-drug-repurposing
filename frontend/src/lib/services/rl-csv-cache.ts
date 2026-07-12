/**
 * TTL-based in-memory cache for the RL ranker's output CSV.
 *
 * FE-069 ROOT FIX: /api/rl was calling `fs.readFile` + `csv-parse` on EVERY
 * request. For a 1000-row CSV, that's O(n) disk I/O + parsing per request —
 * a single authenticated user could DoS the platform by spamming
 * GET /api/rl. The POST handler had the same issue.
 *
 * Root fix: parse the CSV once, cache the parsed result in memory with a
 * 60-second TTL. A file-watcher (fs.watch) invalidates the cache the moment
 * the underlying CSV changes — so re-running the RL agent is reflected
 * immediately, without sacrificing DoS protection.
 *
 * Multi-node note: this cache is per-process. For a horizontally-scaled
 * deployment, replace with a Redis-backed cache (the function signatures
 * stay the same). For a single Next.js server (the documented deployment
 * model: Caddyfile → standalone Next.js server), in-memory is correct.
 */

import { parse } from "csv-parse/sync";

export interface RlCandidate {
  drug: string;
  disease: string;
  reward: number;
  rank: number;
  policyProb: number;
  plausibilityScore: number;
  safetyScore: number;
  marketScore: number;
  overallScore: number;
  literatureSupport: boolean;
  isKnownPositive: boolean;
  confidence: number;
  pathwayScore: number;
  unmetNeedScore: number;
  efficacyScore: number;
  admeScore: number;
}

interface CacheEntry {
  candidates: RlCandidate[];
  parsedAt: number; // ms epoch
  mtimeMs: number; // file mtime when cached (for invalidation)
}

const TTL_MS = 60 * 1000; // 60 seconds

const cache = new Map<string, CacheEntry>();
// Track which paths have an active fs.watch listener so we don't double-watch.
const watchedPaths = new Set<string>();

function num(r: Record<string, string>, key: string, fallback: number): number {
  const v = r[key];
  if (v === undefined || v === null || v === "") return fallback;
  const n = Number(v);
  return Number.isFinite(n) ? n : fallback;
}

function bool(r: Record<string, string>, key: string): boolean {
  const v = (r[key] || "").toLowerCase();
  return v === "1" || v === "true" || v === "yes";
}

/**
 * Parse the raw CSV text into the RlCandidate shape. Pure function — no I/O.
 * Exported so unit tests can verify the parsing logic without touching disk.
 */
export function parseRlCsvContent(content: string): RlCandidate[] {
  const records = parse(content, {
    columns: true,
    skip_empty_lines: true,
    trim: true,
  }) as Record<string, string>[];

  return records.map((r, idx) => {
    const gnn = num(r, "gnn_score", 0);
    const safety = num(r, "safety_score", 0);
    const market = num(r, "market_score", 0);
    const reward = num(r, "reward", 0);
    const rank = num(r, "rank", idx + 1);
    const policyProb = num(r, "policy_prob", 0);
    const confidence = num(r, "confidence", 0);
    const pathwayScore = num(r, "pathway_score", 0);
    const unmetNeedScore = num(r, "unmet_need_score", 0);
    const efficacyScore = num(r, "efficacy_score", 0);
    const admeScore = num(r, "adme_score", 0);
    // Composite overall score — weighted sum of the three dimensions the
    // project docx specifies (plausibility, safety, market). Weights match
    // the RL reward function's default weights in rl_drug_ranker.py.
    const overall = 0.4 * gnn + 0.3 * safety + 0.3 * market;
    return {
      drug: r.drug || "",
      disease: r.disease || "",
      reward,
      rank,
      policyProb,
      plausibilityScore: gnn,
      safetyScore: safety,
      marketScore: market,
      overallScore: overall,
      literatureSupport: bool(r, "literature_support"),
      isKnownPositive: bool(r, "is_known_positive"),
      confidence,
      pathwayScore,
      unmetNeedScore,
      efficacyScore,
      admeScore,
    };
  });
}

/**
 * Apply the optional drug/disease filter, sort by overall score (the RL
 * agent's ranking), reassign ranks, and slice to `limit`. Pure function.
 */
export function filterAndRankCandidates(
  candidates: RlCandidate[],
  filter: { drug?: string; disease?: string; limit?: number }
): RlCandidate[] {
  let out = candidates;
  if (filter.drug) {
    const q = filter.drug.toLowerCase();
    out = out.filter((c) => c.drug.toLowerCase().includes(q));
  }
  if (filter.disease) {
    const q = filter.disease.toLowerCase();
    out = out.filter((c) => c.disease.toLowerCase().includes(q));
  }
  out.sort((a, b) => b.overallScore - a.overallScore);
  out = out.map((c, i) => ({ ...c, rank: i + 1 }));
  if (filter.limit) {
    out = out.slice(0, filter.limit);
  }
  return out;
}

/**
 * Read & parse the CSV at `path`, returning ALL candidates (unfiltered).
 * Results are cached for TTL_MS (60s). A file watcher invalidates the cache
 * immediately when the CSV changes on disk.
 */
export async function readRlCsvCached(path: string): Promise<RlCandidate[]> {
  const fs = await import("fs/promises");
  const nodeFs = await import("fs");

  // Stat the file first — its mtime is the cache key.
  const stat = await fs.stat(path);
  const now = Date.now();
  const cached = cache.get(path);

  // Cache hit: same mtime AND within TTL.
  if (
    cached &&
    cached.mtimeMs === stat.mtimeMs &&
    now - cached.parsedAt < TTL_MS
  ) {
    return cached.candidates;
  }

  // Cache miss: read + parse.
  const content = await fs.readFile(path, "utf8");
  const candidates = parseRlCsvContent(content);
  cache.set(path, {
    candidates,
    parsedAt: now,
    mtimeMs: stat.mtimeMs,
  });

  // Register a file watcher (once per path) so cache invalidates immediately
  // when the RL agent writes a new CSV. This is the "file watcher to
  // invalidate cache when the CSV changes" called out in the FE-069 fix.
  if (!watchedPaths.has(path)) {
    try {
      nodeFs.watch(path, () => {
        cache.delete(path);
      });
      watchedPaths.add(path);
    } catch {
      // Watching is best-effort. If the file doesn't exist or the platform
      // doesn't support watching, the TTL will still expire the cache.
    }
  }

  return candidates;
}

/**
 * Test-only helper: clear the cache and file watchers. Never call from
 * production code.
 */
export function __clearRlCsvCacheForTests(): void {
  cache.clear();
  watchedPaths.clear();
}
