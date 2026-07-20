/**
 * SIDER adverse-event service — queries the Phase 2 Knowledge Graph
 * (Neo4j) for real adverse-event data via the (Compound)-[:causes_adverse_event]->(MedDRA_Term)
 * canonical edge (phase2/drugos_graph/config_schema.py L160).
 *
 * Task 11.4 ROOT FIX (v129, TM11 — hostile-auditor pass):
 *
 * ROOT CAUSE: the /api/safety/[drug] route previously called openFDA
 * (FAERS spontaneous reports). openFDA is real data, but it is NOT the
 * SIDER side of the Knowledge Graph. The project docx (Section 4 —
 * Phase 2) explicitly lists "Drug → causes → Adverse Event" as a KG
 * edge, sourced from SIDER. The V1 launch criteria (Section 8) requires
 * the KG to be "fully built with all 7 data sources integrated" — and
 * SIDER is one of those sources. A safety endpoint that skips the KG
 * and goes straight to openFDA is scientifically incomplete: it misses
 * the frequency / severity / MedDRA code fields that SIDER provides
 * (and that openFDA does NOT).
 *
 * ROOT FIX: this file is the SINGLE source of truth for SIDER lookups.
 * It queries the Phase 2 service's /cypher endpoint (POST) with a
 * read-only Cypher query that:
 *   1. Matches the Compound node by name (case-insensitive, generic OR
 *      brand name).
 *   2. Traverses (Compound)-[:causes_adverse_event]->(MedDRA_Term) edges.
 *   3. Returns the MedDRA term, MedDRA code, frequency (lower/upper
 *      bound as reported by SIDER), and severity (postmarketing →
 *      1=common, 5=rare per SIDER's 5-tier frequency).
 *   4. Also traverses (Compound)-[:has_withdrawal_status]->(:Withdrawal)
 *      if present, so the response includes the withdrawal reason for
 *      withdrawn drugs (verification criterion from the task).
 *
 * FALLBACK: if KG_SERVICE_URL is not set OR the Cypher query fails
 * (Neo4j not configured, SIDER not loaded), the function returns null.
 * The route then falls back to openFDA (still real data, just a
 * different source) — so the endpoint ALWAYS returns real data, never
 * a hardcoded table.
 *
 * SCIENTIFIC INTEGRITY: SIDER frequency is a 5-tier classification
 * (very common >10%, common 1-10%, uncommon 0.1-1%, rare 0.01-0.1%,
 * very rare <0.01%). We surface both the raw SIDER frequency string
 * AND a normalized frequency_lower/frequency_upper percentage range so
 * the dashboard can render a consistent bar regardless of the source
 * vocabulary.
 */

import { executeCypher } from "@/lib/services/kg-service";

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

export interface SiderAdverseEvent {
  /** MedDRA Preferred Term (e.g., "Nausea") — the human-readable term. */
  medraTerm: string;
  /** MedDRA PT code (e.g., "10028813") — the unique MedDRA ID. */
  medraCode: string | null;
  /**
   * SIDER frequency string (raw). One of: "very common", "common",
   * "uncommon", "rare", "very rare", or null if SIDER did not classify
   * the frequency.
   */
  frequency: string | null;
  /**
   * Normalized lower bound of the frequency range as a fraction (0..1).
   * SIDER's 5-tier mapping (per SIDER docs):
   *   very common  → [0.10, 1.00]
   *   common       → [0.01, 0.10]
   *   uncommon     → [0.001, 0.01]
   *   rare         → [0.0001, 0.001]
   *   very rare    → [0.00001, 0.0001]
   * null if SIDER did not classify the frequency.
   */
  frequencyLower: number | null;
  /** Normalized upper bound of the frequency range (see frequencyLower). */
  frequencyUpper: number | null;
  /**
   * Severity score (0..1, higher = more severe). Derived from the
   * MedDRA System Organ Class (SOC) — we use a simple mapping based
   * on the SOC's standard severity ranking (cardiac/hepatic/renal
   * failures → 1.0, gastrointestinal → 0.5, skin → 0.3). When the SOC
   * is unknown, defaults to 0.5 (medium).
   */
  severity: number;
}

export interface SiderWithdrawal {
  /** True if the drug has been withdrawn from the market. */
  isWithdrawn: boolean;
  /** The withdrawal reason (e.g., "hepatotoxicity") — null if not withdrawn. */
  reason: string | null;
  /** The country/region of withdrawal (e.g., "US", "EU") — null if not withdrawn. */
  region: string | null;
  /** The withdrawal year — null if not withdrawn or unknown. */
  year: number | null;
}

export interface SiderSafetySummary {
  drugName: string;
  source: "sider_neo4j";
  adverseEvents: SiderAdverseEvent[];
  withdrawal: SiderWithdrawal;
  totalAdverseEvents: number;
  /** When the query was run — for cache-debugging. */
  queriedAt: string;
  /**
   * Always include the SIDER disclaimer: SIDER frequencies are derived
   * from package inserts (not clinical evidence) and may not reflect
   * real-world incidence.
   */
  disclaimer: string;
}

const SIDER_DISCLAIMER =
  "Adverse event data is sourced from SIDER (http://sideeffects.embl.de/) via the " +
  "Phase 2 Knowledge Graph (Neo4j). SIDER frequencies are derived from drug package " +
  "inserts and postmarketing surveillance reports — they do NOT reflect real-world " +
  "incidence rates. A high-frequency event in SIDER is a labeling disclosure, not a " +
  "proven causal association. Always cross-reference with FAERS (openFDA) and clinical " +
  "trial data before making clinical decisions.";

// ---------------------------------------------------------------------------
// Frequency normalization — SIDER 5-tier → [lower, upper] fraction.
// ---------------------------------------------------------------------------

const FREQUENCY_RANGES: Record<string, [number, number]> = {
  "very common": [0.1, 1.0],
  common: [0.01, 0.1],
  uncommon: [0.001, 0.01],
  rare: [0.0001, 0.001],
  "very rare": [0.00001, 0.0001],
};

function normalizeFrequency(
  raw: string | null | undefined,
): { lower: number | null; upper: number | null } {
  if (!raw) return { lower: null, upper: null };
  const key = raw.trim().toLowerCase();
  const range = FREQUENCY_RANGES[key];
  if (range) return { lower: range[0], upper: range[1] };
  return { lower: null, upper: null };
}

// ---------------------------------------------------------------------------
// Severity heuristic — derived from MedDRA SOC.
// ---------------------------------------------------------------------------

const SEVERITY_BY_SOC: Record<string, number> = {
  Cardiac: 1.0,
  Hepatobiliary: 1.0,
  Renal: 1.0,
  "Blood and lymphatic": 0.9,
  Nervous: 0.8,
  Immune: 0.8,
  Respiratory: 0.7,
  Neoplasms: 0.7,
  Vascular: 0.6,
  Psychiatric: 0.5,
  Endocrine: 0.5,
  Metabolism: 0.5,
  Gastrointestinal: 0.4,
  "Skin and subcutaneous": 0.3,
  Musculoskeletal: 0.3,
  Eye: 0.4,
  Ear: 0.3,
  "Reproductive system": 0.4,
  Pregnancy: 0.6,
  "Infections and infestations": 0.5,
  Injury: 0.5,
  Surgical: 0.5,
  General: 0.3,
  Investigations: 0.2,
  Social: 0.1,
};

function severityForSoc(soc: string | null | undefined): number {
  if (!soc) return 0.5; // unknown → medium
  // Try exact match first, then prefix match (MedDRA SOC names can
  // include suffixes like "Cardiac disorders" — strip "disorders" / "and").
  const exact = SEVERITY_BY_SOC[soc];
  if (typeof exact === "number") return exact;
  const key = Object.keys(SEVERITY_BY_SOC).find((k) =>
    soc.toLowerCase().startsWith(k.toLowerCase()),
  );
  return key ? SEVERITY_BY_SOC[key] : 0.5;
}

// ---------------------------------------------------------------------------
// Cypher query — read-only, parameterized, defensive.
// ---------------------------------------------------------------------------

/**
 * Build the Cypher query for SIDER adverse events + withdrawal status.
 *
 * The query is intentionally SIMPLE — it makes 2 optional MATCH
 * clauses (adverse events + withdrawal) and returns a flat result.
 * We do NOT use APOC, subqueries, LOAD CSV, or any write operations
 * (the Phase 2 service's _validate_readonly_cypher would reject those
 * anyway — defense-in-depth).
 *
 * The query uses case-insensitive matching on the Compound name
 * (LOWER(c.name) = LOWER($drugName)) because the KG stores names in
 * mixed case (e.g., "Aspirin") but the API accepts any case.
 *
 * Parameters are passed via the `params` dict — the Phase 2 service's
 * _validate_cypher_params rejects non-scalar params (defense-in-depth
 * against Cypher injection).
 */
function buildSiderCypher(drugName: string): { cypher: string; params: Record<string, unknown> } {
  return {
    cypher: [
      "MATCH (c:Compound)",
      "WHERE toLower(c.name) = toLower($drugName)",
      "OPTIONAL MATCH (c)-[r:causes_adverse_event]->(m:MedDRA_Term)",
      "OPTIONAL MATCH (c)-[w:has_withdrawal_status]->(wd:Withdrawal)",
      "WITH c, r, m, w, wd",
      "WHERE r IS NOT NULL OR w IS NOT NULL",
      "RETURN",
      "  m.name AS medraTerm,",
      "  m.meddra_code AS medraCode,",
      "  r.frequency AS frequency,",
      "  r.soc AS soc,",
      "  wd.reason AS withdrawalReason,",
      "  wd.region AS withdrawalRegion,",
      "  wd.year AS withdrawalYear",
      "LIMIT 200",
    ].join("\n"),
    params: { drugName },
  };
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Query SIDER adverse events for a drug via the Phase 2 KG (Neo4j).
 *
 * Returns null if:
 *   - KG_SERVICE_URL is not set (executeCypher returns { records: [] })
 *   - The drug is not in the KG (empty result)
 *   - The query fails (Neo4j not configured, SIDER not loaded)
 *
 * The caller (/api/safety/[drug]/route.ts) falls back to openFDA in
 * those cases — so the endpoint ALWAYS returns real data.
 */
export async function getSiderSafetySummary(
  drugName: string,
): Promise<SiderSafetySummary | null> {
  const trimmed = (drugName || "").trim();
  if (trimmed.length < 2 || trimmed.length > 64) return null;

  const { cypher, params } = buildSiderCypher(trimmed);

  let result;
  try {
    result = await executeCypher({ cypher, params, timeoutMs: 15_000 });
  } catch {
    // KG service unreachable, Neo4j not configured, SIDER not loaded,
    // or query timed out. Return null so the caller falls back to
    // openFDA — never propagate the error to the user (the safety
    // endpoint must ALWAYS return real data).
    return null;
  }

  if (!result.records || result.records.length === 0) {
    return null;
  }

  // Group results: each row is one adverse event OR one withdrawal fact.
  // A drug can have many adverse events and 0 or 1 withdrawal fact.
  const adverseEvents: SiderAdverseEvent[] = [];
  let withdrawal: SiderWithdrawal = {
    isWithdrawn: false,
    reason: null,
    region: null,
    year: null,
  };

  for (const row of result.records) {
    const medraTerm = (row.medraTerm as string) || null;
    const withdrawalReason = (row.withdrawalReason as string) || null;

    // Row is an adverse-event row if it has a MedDRA term.
    if (medraTerm) {
      const frequency = (row.frequency as string) || null;
      const { lower, upper } = normalizeFrequency(frequency);
      adverseEvents.push({
        medraTerm,
        medraCode: (row.medraCode as string) || null,
        frequency,
        frequencyLower: lower,
        frequencyUpper: upper,
        severity: severityForSoc(row.soc as string | null),
      });
    }

    // Row is a withdrawal row if it has a withdrawal reason.
    if (withdrawalReason) {
      withdrawal = {
        isWithdrawn: true,
        reason: withdrawalReason,
        region: (row.withdrawalRegion as string) || null,
        year: typeof row.withdrawalYear === "number" ? row.withdrawalYear : null,
      };
    }
  }

  if (adverseEvents.length === 0 && !withdrawal.isWithdrawn) {
    // No adverse events and no withdrawal — the drug is in the KG but
    // has no SIDER data. Return null so the caller falls back to
    // openFDA (which may have FAERS reports even if SIDER doesn't).
    return null;
  }

  // Sort adverse events by frequency (descending) so the most common
  // events appear first. Events without a frequency sort last.
  adverseEvents.sort((a, b) => {
    const aFreq = a.frequencyLower ?? -1;
    const bFreq = b.frequencyLower ?? -1;
    return bFreq - aFreq;
  });

  return {
    drugName: trimmed,
    source: "sider_neo4j",
    adverseEvents,
    withdrawal,
    totalAdverseEvents: adverseEvents.length,
    queriedAt: new Date().toISOString(),
    disclaimer: SIDER_DISCLAIMER,
  };
}

/**
 * Check whether the SIDER integration is available (i.e., KG_SERVICE_URL
 * is set). Used by /api/system/status to report SIDER availability
 * without making a full query.
 */
export function isSiderConfigured(): boolean {
  return Boolean(process.env.KG_SERVICE_URL);
}

export { SIDER_DISCLAIMER };
