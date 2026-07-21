/**
 * Teammate 8 — Frontend KG service contract tests.
 *
 * Verifies that:
 *   1. CANONICAL_NODE_TYPES uses "ClinicalOutcome" (SINGULAR), not
 *      "ClinicalOutcomes" (PLURAL).
 *   2. KgStatsResponseSchema includes the canonicalNodeCount field.
 *
 * Run with: npx jest frontend/__tests__/teammate-8-kg-contract.test.ts
 */

import {
  CANONICAL_NODE_TYPES,
  CANONICAL_NODE_TYPE_SET,
  KgStatsResponseSchema,
} from "@/lib/ml-contracts";

describe("Teammate 8 — Frontend KG contract", () => {
  describe("CANONICAL_NODE_TYPES", () => {
    it("uses 'ClinicalOutcome' (SINGULAR), not 'ClinicalOutcomes' (PLURAL)", () => {
      // The Phase 2 KG label vocabulary uses the SINGULAR form
      // "ClinicalOutcome" — see phase2/service.py:CANONICAL_NODE_TYPES.
      // The previous frontend used the PLURAL form "ClinicalOutcomes",
      // which silently dropped all ClinicalOutcome nodes from the
      // canonical nodeCount (the kg-service.ts transform layer
      // classified them as non-canonical).
      expect(CANONICAL_NODE_TYPES).toContain("ClinicalOutcome");
      expect(CANONICAL_NODE_TYPES).not.toContain("ClinicalOutcomes");
    });

    it("contains exactly the 5 project docx Phase 2 node types", () => {
      // Per project docx Phase 2 "Graph Structure" section:
      //   Drugs, Proteins, Biological Pathways, Diseases, Clinical Outcomes.
      // The KG label vocabulary uses: Compound, Protein, Pathway,
      // Disease, ClinicalOutcome (SINGULAR).
      expect(CANONICAL_NODE_TYPES).toEqual([
        "Compound",
        "Protein",
        "Pathway",
        "Disease",
        "ClinicalOutcome",
      ]);
    });

    it("CANONICAL_NODE_TYPE_SET is a ReadonlySet matching the array", () => {
      expect(CANONICAL_NODE_TYPE_SET).toBeInstanceOf(Set);
      expect(CANONICAL_NODE_TYPE_SET.size).toBe(5);
      for (const t of CANONICAL_NODE_TYPES) {
        expect(CANONICAL_NODE_TYPE_SET.has(t)).toBe(true);
      }
    });
  });

  describe("KgStatsResponseSchema", () => {
    it("includes the canonicalNodeCount field (optional)", () => {
      // The schema must accept canonicalNodeCount as an optional number.
      // It's optional for backward compat with older Phase 2 deployments
      // that don't emit it yet (the kg-service.ts transform derives it
      // client-side in that case).
      const valid = {
        sources: [],
        nodeCount: 100,
        canonicalNodeCount: 80,
        edgeCount: 50,
        nodeTypeCounts: { Compound: 50, Protein: 30 },
        edgeTypeCounts: { treats: 50 },
        source: "neo4j",
        generatedAt: "2026-07-21T09:15:32Z",
      };
      const parsed = KgStatsResponseSchema.safeParse(valid);
      expect(parsed.success).toBe(true);
      if (parsed.success) {
        expect(parsed.data.canonicalNodeCount).toBe(80);
      }
    });

    it("accepts a response without canonicalNodeCount (backward compat)", () => {
      const valid = {
        sources: [],
        nodeCount: 100,
        edgeCount: 50,
        nodeTypeCounts: {},
        edgeTypeCounts: {},
        source: "in_memory",
        generatedAt: "2026-07-21T09:15:32Z",
      };
      const parsed = KgStatsResponseSchema.safeParse(valid);
      expect(parsed.success).toBe(true);
    });
  });
});
