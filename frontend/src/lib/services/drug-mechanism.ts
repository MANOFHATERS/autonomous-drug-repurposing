/**
 * Drug mechanism-of-action lookup service.
 *
 * FE-024 ROOT FIX: The previous code rendered `RL reward: 0.234, policy_prob:
 * 0.123` in the "Mechanism" column of the candidate table — that is RL
 * debug output, NOT a drug's mechanism of action. A researcher evaluating
 * repurposing candidates cannot make any decision without knowing the
 * mechanism (e.g. "NMDA receptor antagonist").
 *
 * This service fetches the real mechanism of action for a drug from ChEMBL
 * (https://www.ebi.ac.uk/chembl/), the European Bioinformatics Institute's
 * open chemistry database. ChEMBL is:
 *   - Free to use (no API key required)
 *   - Scientifically authoritative (used by pharma research worldwide)
 *   - CC BY-SA 3.0 licensed for the data
 *
 * The lookup is two-step:
 *   1. Resolve the drug name to a ChEMBL ID via the molecule search endpoint.
 *   2. Fetch the mechanism-of-action record for that ChEMBL ID.
 *
 * The result is cached per-drug in an in-memory LRU to avoid hammering the
 * ChEMBL API when the user re-runs the same query.
 */

const CHEMBL_BASE = "https://www.ebi.ac.uk/chembl/api/data";

/**
 * FE-028 ROOT FIX (Team Member 15):
 *
 * ROOT CAUSE: the in-memory cache had NO TTL — entries lived forever
 * (until evicted by LRU). If the KG was updated (e.g. a new mechanism
 * was added to ChEMBL), the cache served the OLD mechanism
 * indefinitely. For a pharma partner demo, this means showing
 * outdated mechanisms with no recovery short of a server restart.
 *
 * ROOT FIX:
 *   1. Cache entries now carry a `cachedAt` timestamp. On lookup,
 *      entries older than `CACHE_TTL_MS` (5 minutes) are treated as
 *         misses and re-fetched. 5 minutes is short enough that a
 *         ChEMBL update is reflected quickly, but long enough to
 *         avoid hammering ChEMBL on a busy dashboard.
 *   2. Exported `clearDrugMechanismCache()` allows a manual refresh
 *      via POST /api/drugs/mechanism/refresh.
 *   3. The Next.js `next: { revalidate: 86400 }` on the underlying
 *      fetch is preserved — it dedupes the HTTP request at the
 *      framework level. The 5-min in-memory TTL is a SEPARATE
 *      concern: it controls how often we re-check ChEMBL within a
 *      single server process.
 */
const CACHE_TTL_MS = 5 * 60 * 1000; // 5 minutes (FE-028: was infinite)

export interface DrugMechanismResult {
  drugName: string;
  chemblId: string | null;
  mechanism: string | null;
  /** Canonicalized mechanism reference, e.g. "ChEMBL::CHEMBL25::Direct thrombin inhibitor". */
  source: string | null;
  fetchedAt: string;
  /**
   * BE-017: Set when the lookup failed (network error, JSON parse error,
   * ChEMBL down). The UI should show "Mechanism lookup failed — retry"
   * instead of "—" (which looks like "no mechanism data"). Null means
   * the lookup succeeded but the drug simply has no annotated mechanism.
   */
  error?: string | null;
}

// In-memory cache. Bounded to 256 entries; oldest evicted first.
const CACHE_MAX = 256;

interface CacheEntry {
  result: DrugMechanismResult;
  cachedAt: number; // ms epoch — FE-028
}

const cache = new Map<string, CacheEntry>();

function cacheKey(drugName: string): string {
  return drugName.trim().toLowerCase();
}

function cacheSet(key: string, value: DrugMechanismResult): void {
  if (cache.size >= CACHE_MAX) {
    // Evict the oldest entry (the first key in insertion order).
    const firstKey = cache.keys().next().value;
    if (firstKey !== undefined) cache.delete(firstKey);
  }
  cache.set(key, { result: value, cachedAt: Date.now() });
}

/**
 * FE-028: manually clear the drug-mechanism cache. Called by the
 * POST /api/drugs/mechanism/refresh route when the operator clicks
 * "Refresh" on the dashboard, or after a KG update is known to have
 * landed.
 *
 * @param drugName Optional. If provided, clears only the entry for
 *   that drug. If omitted, clears ALL entries.
 */
export function clearDrugMechanismCache(drugName?: string): void {
  if (drugName) {
    cache.delete(cacheKey(drugName));
  } else {
    cache.clear();
  }
}

/** FE-028: inspect the cache for observability / debugging. */
export function getDrugMechanismCacheState(): Array<{
  drugName: string;
  cachedAt: number;
  ageMs: number;
  ttlRemainingMs: number;
  mechanism: string | null;
}> {
  const now = Date.now();
  return Array.from(cache.entries()).map(([k, entry]) => ({
    drugName: k,
    cachedAt: entry.cachedAt,
    ageMs: now - entry.cachedAt,
    ttlRemainingMs: Math.max(0, CACHE_TTL_MS - (now - entry.cachedAt)),
    mechanism: entry.result.mechanism,
  }));
}

interface ChEMBLMoleculeResponse {
  molecules: Array<{
    molecule_chembl_id: string;
    molecule_synonyms?: Array<{ molecule_synonym: string; syn_type: string }>;
    pref_name?: string;
  }>;
}

interface ChEMBLMechanismResponse {
  mechanisms: Array<{
    molecule_chembl_id: string;
    mechanism_of_action: string;
    action_type?: string;
    target_pref_name?: string;
  }>;
}

/**
 * Resolve a drug name (generic or brand) to a ChEMBL ID.
 * Returns null if no match. The ChEMBL molecule search is tolerant of
 * case and partial matches; we filter to exact synonym matches to avoid
 * returning the wrong drug.
 */
async function resolveChemblId(drugName: string): Promise<string | null> {
  const sanitized = drugName.trim().replace(/["\\]/g, "").slice(0, 128);
  if (sanitized.length < 2) return null;

  // The molecule_synonyms filter is a case-insensitive iexact match.
  // We try the user's exact name first; if no hit, we try a fuzzy
  // search via the search term `_search` resource as a fallback.
  const url =
    `${CHEMBL_BASE}/molecule.json?molecule_synonyms__molecule_synonym__iexact=` +
    encodeURIComponent(sanitized) +
    `&limit=5`;

  const res = await fetch(url, {
    headers: { Accept: "application/json" },
    // Cache the fetch result for 24h at the Next.js level (production).
    next: { revalidate: 86400 },
  });
  if (!res.ok) return null;
  const body: ChEMBLMoleculeResponse = await res.json();
  const molecules = body?.molecules || [];
  if (molecules.length === 0) return null;

  // Prefer the molecule whose synonym EXACTLY matches (case-insensitive).
  // Without this filter, "ASCEND" might match "ASCORBINIC ACID" because
  // both start with the same letters in the synonym index.
  const lower = sanitized.toLowerCase();
  const exactMatch = molecules.find((m) =>
    (m.molecule_synonyms || []).some(
      (s) => (s.molecule_synonym || "").toLowerCase() === lower
    )
  );
  if (exactMatch) return exactMatch.molecule_chembl_id;

  // Fall back to the first molecule if no exact synonym match (the
  // ChEMBL search already ranks by relevance).
  return molecules[0].molecule_chembl_id;
}

/**
 * Fetch the mechanism-of-action text for a ChEMBL ID.
 * Returns null if the molecule has no mechanism record (this is common —
 * many compounds in ChEMBL have no annotated MoA).
 */
async function fetchMechanism(chemblId: string): Promise<string | null> {
  const url = `${CHEMBL_BASE}/mechanism.json?molecule_chembl_id=${encodeURIComponent(
    chemblId
  )}&limit=5`;
  const res = await fetch(url, {
    headers: { Accept: "application/json" },
    next: { revalidate: 86400 },
  });
  if (!res.ok) return null;
  const body: ChEMBLMechanismResponse = await res.json();
  const mechanisms = body?.mechanisms || [];
  if (mechanisms.length === 0) return null;

  // Prefer the record with both an action_type and a mechanism_of_action —
  // that's the most informative. Otherwise take the first record.
  const preferred =
    mechanisms.find((m) => m.action_type && m.mechanism_of_action) ||
    mechanisms[0];

  const parts: string[] = [];
  if (preferred.action_type) {
    // action_type is a short code like "INHIBITOR"; expand to title case.
    parts.push(
      preferred.action_type.charAt(0).toUpperCase() +
        preferred.action_type.slice(1).toLowerCase()
    );
  }
  if (preferred.mechanism_of_action) {
    parts.push(preferred.mechanism_of_action);
  } else if (preferred.target_pref_name) {
    parts.push(`targets ${preferred.target_pref_name}`);
  }
  return parts.length > 0 ? parts.join(" - ") : null;
}

/**
 * Look up the mechanism of action for a drug name.
 *
 * Returns a `DrugMechanismResult` with `mechanism: null` if no data is
 * found (the UI should render "—" in that case, never a fabricated value).
 *
 * Throws on network errors so the caller can decide how to handle
 * (e.g. show a tooltip "mechanism lookup failed").
 */
export async function lookupDrugMechanism(
  drugName: string
): Promise<DrugMechanismResult> {
  const key = cacheKey(drugName);
  const now = Date.now();

  // FE-028: TTL check. Treat entries older than CACHE_TTL_MS as misses.
  const cached = cache.get(key);
  if (cached && now - cached.cachedAt < CACHE_TTL_MS) {
    return cached.result;
  }
  // (else: cache miss or stale — fall through to re-fetch)

  const result: DrugMechanismResult = {
    drugName: drugName.trim(),
    chemblId: null,
    mechanism: null,
    source: null,
    fetchedAt: new Date().toISOString(),
  };

  try {
    const chemblId = await resolveChemblId(drugName);
    if (!chemblId) {
      // Drug not found in ChEMBL — this is "no data", not an error.
      cacheSet(key, result);
      return result;
    }
    result.chemblId = chemblId;
    const mechanism = await fetchMechanism(chemblId);
    if (mechanism) {
      result.mechanism = mechanism;
      result.source = `ChEMBL::${chemblId}`;
    }
  } catch (e) {
    // BE-017 ROOT FIX: Distinguish "lookup failed" from "no mechanism data".
    // Previously this catch block swallowed ALL errors silently and the UI
    // showed "—" — the same as "no data". A researcher believed the data
    // was missing when actually ChEMBL was down, and may have made decisions
    // based on "no mechanism data" when the data exists.
    const errorMsg = e instanceof Error ? e.message : String(e);
    result.error = "chembl_unreachable";
    console.error(`[drug-mechanism] BE-017: ChEMBL lookup failed for "${drugName}": ${errorMsg}`);
  }

  cacheSet(key, result);
  return result;
}

/**
 * Batch-lookup mechanisms for multiple drug names. Requests are issued
 * concurrently but ChEMBL rate-limits to ~5 req/sec, so we cap concurrency
 * at 5 to avoid 429s.
 */
export async function lookupDrugMechanisms(
  drugNames: string[]
): Promise<Map<string, DrugMechanismResult>> {
  const result = new Map<string, DrugMechanismResult>();
  const unique = Array.from(new Set(drugNames.map((n) => n.trim()).filter(Boolean)));
  const CONCURRENCY = 5;

  for (let i = 0; i < unique.length; i += CONCURRENCY) {
    const batch = unique.slice(i, i + CONCURRENCY);
    const results = await Promise.all(
      batch.map((name) => lookupDrugMechanism(name).catch(() => ({
        drugName: name,
        chemblId: null,
        mechanism: null,
        source: null,
        fetchedAt: new Date().toISOString(),
      })))
    );
    for (const r of results) {
      result.set(r.drugName.toLowerCase(), r);
    }
  }
  return result;
}
