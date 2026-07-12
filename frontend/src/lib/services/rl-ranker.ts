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
import path from "path";
import { parse } from "csv-parse/sync";

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
        "Phase 4 service, or ensure the ranker has written its output to " +
        `${csvPath}.`,
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
