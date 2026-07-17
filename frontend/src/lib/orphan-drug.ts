/**
 * FE-051 ROOT FIX (Teammate 13, MEDIUM) — FDA Orphan-Drug eligibility helper.
 *
 * Extracted from core-screens.tsx so it can be unit-tested directly (the
 * component file pulls in recharts + framer-motion, which makes importing it
 * in Jest expensive and brittle). This module is pure, dependency-free, and
 * scientifically defensible.
 *
 * The previous UI used `prevalence?.includes('per 100,000')` — a fragile
 * heuristic that mis-classified any prevalence NOT phrased exactly as
 * "N per 100,000" and said "may qualify" for ANY string containing that
 * literal regardless of the actual number (so "5,000 per 100,000" — half
 * the population — would falsely qualify).
 *
 * Root fix: parse the numeric value + unit out of common biomedical
 * prevalence phrasings, convert to an estimated US-population count, and
 * compare against the FDA statutory orphan-drug threshold
 * (< 200,000 people in the US — 21 U.S.C. §360ee). Returns a structured
 * result so the UI can show a transparent rationale, not a binary guess.
 */

/** Approximate US population used to convert rates to counts. */
export const US_POPULATION = 331_000_000;

/** FDA orphan-drug designation threshold (21 U.S.C. §360ee). */
export const FDA_ORPHAN_THRESHOLD = 200_000;

export interface OrphanEligibility {
  /** null = cannot determine (unknown / unparseable prevalence). */
  eligible: boolean | null;
  /** Estimated US prevalence count (null = unknown). */
  estimate: number | null;
  /** Human-readable rationale for the UI. */
  note: string;
}

/**
 * Parse a free-form disease prevalence string and evaluate FDA orphan-drug
 * eligibility. Handles: "N per 100,000", "1 in N", "N per million", and
 * bare counts like "150,000" / "<200,000 cases". Returns eligible=null when
 * the string cannot be parsed — we never guess.
 */
export function parsePrevalence(prevalence: string | undefined | null): OrphanEligibility {
  if (!prevalence || typeof prevalence !== 'string') {
    return { eligible: null, estimate: null, note: 'Prevalence data not available.' };
  }
  const p = prevalence.trim();
  if (!p) {
    return { eligible: null, estimate: null, note: 'Prevalence data not available.' };
  }

  // "N per 100,000" / "N per 100000" / "N/100,000"
  let m = p.match(/([\d,.]+)\s*(?:per|\/)\s*100,?000/i);
  if (m) {
    const rate = Number(m[1].replace(/,/g, ''));
    if (Number.isFinite(rate) && rate >= 0) {
      const estimate = Math.round((rate / 100_000) * US_POPULATION);
      return {
        eligible: estimate < FDA_ORPHAN_THRESHOLD,
        estimate,
        note: `~${estimate.toLocaleString()} US cases (rate ${rate} per 100,000). FDA orphan threshold: < ${FDA_ORPHAN_THRESHOLD.toLocaleString()}.`,
      };
    }
  }

  // "1 in N" (ratio)
  m = p.match(/1\s*in\s*([\d,.]+)/i);
  if (m) {
    const denom = Number(m[1].replace(/,/g, ''));
    if (Number.isFinite(denom) && denom > 0) {
      const estimate = Math.round(US_POPULATION / denom);
      return {
        eligible: estimate < FDA_ORPHAN_THRESHOLD,
        estimate,
        note: `~${estimate.toLocaleString()} US cases (1 in ${denom.toLocaleString()}). FDA orphan threshold: < ${FDA_ORPHAN_THRESHOLD.toLocaleString()}.`,
      };
    }
  }

  // "N per million" / "N/million"
  m = p.match(/([\d,.]+)\s*(?:per|\/)\s*million/i);
  if (m) {
    const perMillion = Number(m[1].replace(/,/g, ''));
    if (Number.isFinite(perMillion) && perMillion >= 0) {
      const estimate = Math.round(perMillion * (US_POPULATION / 1_000_000));
      return {
        eligible: estimate < FDA_ORPHAN_THRESHOLD,
        estimate,
        note: `~${estimate.toLocaleString()} US cases (${perMillion} per million). FDA orphan threshold: < ${FDA_ORPHAN_THRESHOLD.toLocaleString()}.`,
      };
    }
  }

  // Bare count: "150,000" / "<200,000" / "200000 cases"
  m = p.match(/<?\s*([\d,]+)\s*(?:cases?|people|patients|individuals)?/i);
  if (m) {
    const count = Number(m[1].replace(/,/g, ''));
    if (Number.isFinite(count) && count > 0 && count < US_POPULATION) {
      return {
        eligible: count < FDA_ORPHAN_THRESHOLD,
        estimate: count,
        note: `~${count.toLocaleString()} US cases. FDA orphan threshold: < ${FDA_ORPHAN_THRESHOLD.toLocaleString()}.`,
      };
    }
  }

  // Unparseable — do NOT guess.
  return {
    eligible: null,
    estimate: null,
    note: `Prevalence "${p}" could not be parsed. Manual review required for orphan-drug eligibility.`,
  };
}
