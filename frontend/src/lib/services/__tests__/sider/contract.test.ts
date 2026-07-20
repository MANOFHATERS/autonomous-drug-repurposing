/**
 * Task 11.4 — SIDER service contract test.
 *
 * HOSTILE-AUDITOR PASS (v129, TM11): verifies the sider.ts service
 * correctly:
 *   1. Normalizes SIDER's 5-tier frequency strings into [lower, upper]
 *      fraction ranges.
 *   2. Computes severity scores from MedDRA SOC names.
 *   3. Builds a valid Cypher query that the Phase 2 service's
 *      _validate_readonly_cypher whitelist will accept.
 *   4. Returns null for invalid drug names (< 2 chars, > 64 chars).
 *
 * The test does NOT call the actual Neo4j (no KG_SERVICE_URL in the
 * test environment) — it tests the pure functions and the Cypher
 * query structure. The end-to-end SIDER query is verified by the
 * safety integration test (which mocks executeCypher).
 */
import {
  SIDER_DISCLAIMER,
} from "@/lib/services/sider";

// We test the INTERNAL helpers by re-importing them. The helpers are
// not exported (they are file-private), so we test them indirectly
// via the public getSiderSafetySummary function — mocking executeCypher
// to return a known shape.
jest.mock("@/lib/services/kg-service", () => ({
  executeCypher: jest.fn(),
}));

import { executeCypher } from "@/lib/services/kg-service";
import { getSiderSafetySummary } from "@/lib/services/sider";

describe("Task 11.4: SIDER service contract", () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  test("Returns null for drug names < 2 chars (input validation)", async () => {
    const result = await getSiderSafetySummary("a");
    expect(result).toBeNull();
    expect(executeCypher).not.toHaveBeenCalled();
  });

  test("Returns null for drug names > 64 chars (input validation)", async () => {
    const longName = "a".repeat(65);
    const result = await getSiderSafetySummary(longName);
    expect(result).toBeNull();
    expect(executeCypher).not.toHaveBeenCalled();
  });

  test("Returns null when executeCypher returns empty records (drug not in KG)", async () => {
    (executeCypher as jest.Mock).mockResolvedValue({ records: [] });
    const result = await getSiderSafetySummary("unknown_drug_xyz");
    expect(result).toBeNull();
  });

  test("Returns null when executeCypher throws (Neo4j not configured)", async () => {
    (executeCypher as jest.Mock).mockRejectedValue(new Error("KG_SERVICE_URL not set"));
    const result = await getSiderSafetySummary("aspirin");
    expect(result).toBeNull();
  });

  test("Returns a valid SiderSafetySummary when executeCypher returns SIDER data", async () => {
    // Simulate a successful SIDER query — 2 adverse events, no withdrawal.
    (executeCypher as jest.Mock).mockResolvedValue({
      records: [
        {
          medraTerm: "Nausea",
          medraCode: "10028813",
          frequency: "common",
          soc: "Gastrointestinal disorders",
          withdrawalReason: null,
          withdrawalRegion: null,
          withdrawalYear: null,
        },
        {
          medraTerm: "Headache",
          medraCode: "10019211",
          frequency: "very common",
          soc: "Nervous system disorders",
          withdrawalReason: null,
          withdrawalRegion: null,
          withdrawalYear: null,
        },
      ],
    });
    const result = await getSiderSafetySummary("aspirin");
    expect(result).not.toBeNull();
    if (result) {
      expect(result.drugName).toBe("aspirin");
      expect(result.source).toBe("sider_neo4j");
      expect(result.totalAdverseEvents).toBe(2);
      expect(result.adverseEvents).toHaveLength(2);
      // Sorted by frequency descending — "very common" (lower=0.10)
      // should come before "common" (lower=0.01).
      expect(result.adverseEvents[0].medraTerm).toBe("Headache");
      expect(result.adverseEvents[0].frequencyLower).toBeCloseTo(0.1);
      expect(result.adverseEvents[0].frequencyUpper).toBeCloseTo(1.0);
      expect(result.adverseEvents[1].medraTerm).toBe("Nausea");
      expect(result.adverseEvents[1].frequencyLower).toBeCloseTo(0.01);
      expect(result.adverseEvents[1].frequencyUpper).toBeCloseTo(0.1);
      // Severity: Gastrointestinal → 0.4, Nervous → 0.8
      expect(result.adverseEvents[0].severity).toBeCloseTo(0.8); // Headache → Nervous
      expect(result.adverseEvents[1].severity).toBeCloseTo(0.4); // Nausea → Gastrointestinal
      // Withdrawal: not withdrawn
      expect(result.withdrawal.isWithdrawn).toBe(false);
      expect(result.withdrawal.reason).toBeNull();
      // Disclaimer must be present
      expect(result.disclaimer).toBe(SIDER_DISCLAIMER);
    }
  });

  test("Includes withdrawal reason for withdrawn drugs (verification criterion)", async () => {
    // The task spec requires: "verify response for a withdrawn drug
    // includes the withdrawal reason." This test simulates a
    // withdrawn drug (e.g., rosiglitazone — withdrawn in the EU for
    // cardiovascular risk) and verifies the withdrawal fields are
    // populated.
    (executeCypher as jest.Mock).mockResolvedValue({
      records: [
        {
          medraTerm: "Myocardial infarction",
          medraCode: "10027533",
          frequency: "uncommon",
          soc: "Cardiac disorders",
          withdrawalReason: "cardiovascular toxicity",
          withdrawalRegion: "EU",
          withdrawalYear: 2010,
        },
      ],
    });
    const result = await getSiderSafetySummary("rosiglitazone");
    expect(result).not.toBeNull();
    if (result) {
      expect(result.withdrawal.isWithdrawn).toBe(true);
      expect(result.withdrawal.reason).toBe("cardiovascular toxicity");
      expect(result.withdrawal.region).toBe("EU");
      expect(result.withdrawal.year).toBe(2010);
      // Severity for Cardiac → 1.0 (highest)
      expect(result.adverseEvents[0].severity).toBeCloseTo(1.0);
    }
  });

  test("The Cypher query passed to executeCypher is READ-ONLY (no writes, no APOC)", async () => {
    // Verify the query structure: must use MATCH + OPTIONAL MATCH + RETURN,
    // must NOT use CREATE/MERGE/DELETE/SET/REMOVE/APOC/LOAD CSV/CALL.
    (executeCypher as jest.Mock).mockResolvedValue({ records: [] });
    await getSiderSafetySummary("aspirin");
    expect(executeCypher).toHaveBeenCalledTimes(1);
    const callArgs = (executeCypher as jest.Mock).mock.calls[0][0];
    const cypher = callArgs.cypher as string;
    expect(cypher).toMatch(/\bMATCH\b/);
    expect(cypher).toMatch(/\bOPTIONAL MATCH\b/);
    expect(cypher).toMatch(/\bRETURN\b/);
    expect(cypher).toMatch(/\bLIMIT\b/);
    // Forbidden clauses:
    expect(cypher).not.toMatch(/\bCREATE\b/i);
    expect(cypher).not.toMatch(/\bMERGE\b/i);
    expect(cypher).not.toMatch(/\bDELETE\b/i);
    expect(cypher).not.toMatch(/\bSET\b/i);
    expect(cypher).not.toMatch(/\bREMOVE\b/i);
    expect(cypher).not.toMatch(/\bCALL\b/i);
    expect(cypher).not.toMatch(/\bAPOC\b/i);
    expect(cypher).not.toMatch(/LOAD\s+CSV/i);
    // Must use parameterized query (not string concatenation).
    expect(cypher).toContain("$drugName");
    expect(callArgs.params.drugName).toBe("aspirin");
  });
});
