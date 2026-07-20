/**
 * Task 11.1 — Contract test for /api/predict Zod schema vs GT service.
 *
 * HOSTILE-AUDITOR PASS (v129, TM11): the user explicitly said "comments
 * and tests are fakes — when I manually check code it's 100 percent
 * broken". So this test does NOT trust any existing test. It builds the
 * EXACT response shapes the two Python services actually emit (read
 * directly from graph_transformer/service.py L816-833 and
 * scripts/gt_api.py L221-231, L370-379) and asserts:
 *
 *   1. The frontend Zod schema (GtPredictResponseSchema) ACCEPTS the
 *      canonical graph_transformer/service.py shape.
 *   2. The frontend Zod schema ACCEPTS the scripts/gt_api.py shape
 *      (which docker-compose launches — see docker-compose.yml L579).
 *   3. The schema REJECTS the OLD broken snake_case shape
 *      ({ model_version, n_pairs }) that SH-006 documented — so any
 *      regression is caught immediately.
 *   4. The schema REJECTS a response missing required fields
 *      (predictions / source / generatedAt / count) — so a partial
 *      response surfaces as a contract violation, not a silent undefined.
 *
 * These assertions are the ROOT-LEVEL guarantee that /api/predict will
 * not silently fail Zod validation on every call (the original BLOCKER).
 */
import { GtPredictResponseSchema, GtTopKResponseSchema } from "@/lib/ml-contracts";

describe("Task 11.1: /api/predict Zod schema vs GT service contract", () => {
  // ---------------------------------------------------------------------------
  // Canonical response shape emitted by graph_transformer/service.py
  // _predict_inner() — read directly from the Python source (L816-833).
  // ---------------------------------------------------------------------------
  const canonicalGraphTransformerShape = {
    predictions: [
      { drug: "aspirin", disease: "cancer", score: 0.87, confidence: 0.62 },
    ],
    source: "gt_checkpoint",
    modelVersion: "gt_v113",
    generatedAt: "2026-07-20T03:00:00.000Z",
    count: 1,
    checkpointPath: "/opt/ml_artifacts/best_model.pt",
    error_count: 0,
    error_rate: 0.0,
    // TM7-v127 added an optional neo4j_writeback field. Zod's default
    // object schema is non-strict (strips unknown keys), so this should
    // pass cleanly.
    neo4j_writeback: { neo4j_configured: true, edges_written: 1 },
  };

  // ---------------------------------------------------------------------------
  // Response shape emitted by scripts/gt_api.py PredictResponse — read
  // directly from the Python source (L221-231, L370-379). This is the
  // service that docker-compose.yml L579 actually launches.
  // ---------------------------------------------------------------------------
  const scriptsGtApiShape = {
    predictions: [
      { drug: "aspirin", disease: "cancer", score: 0.87 },
    ],
    source: "gt_checkpoint",
    modelVersion: "gt_v113",
    generatedAt: "2026-07-20T03:00:00.000Z",
    count: 1,
    checkpointPath: "/opt/ml_artifacts/best_model.pt",
    error_count: 0,
    error_rate: 0.0,
  };

  test("ACCEPTS canonical graph_transformer/service.py response shape", () => {
    const result = GtPredictResponseSchema.safeParse(canonicalGraphTransformerShape);
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.predictions).toHaveLength(1);
      expect(result.data.predictions[0].drug).toBe("aspirin");
      expect(result.data.predictions[0].score).toBeCloseTo(0.87);
      expect(result.data.modelVersion).toBe("gt_v113");
      expect(result.data.generatedAt).toBe("2026-07-20T03:00:00.000Z");
      expect(result.data.count).toBe(1);
      expect(result.data.checkpointPath).toBe("/opt/ml_artifacts/best_model.pt");
      expect(result.data.error_count).toBe(0);
      expect(result.data.error_rate).toBe(0.0);
    }
  });

  test("ACCEPTS scripts/gt_api.py response shape (the service docker-compose launches)", () => {
    const result = GtPredictResponseSchema.safeParse(scriptsGtApiShape);
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.predictions).toHaveLength(1);
      expect(result.data.source).toBe("gt_checkpoint");
      expect(result.data.modelVersion).toBe("gt_v113");
      expect(result.data.count).toBe(1);
    }
  });

  test("REJECTS the OLD broken snake_case shape ({ model_version, n_pairs }) — SH-006 regression guard", () => {
    // This is the shape that SH-006 documented as broken. If a future
    // refactor accidentally reverts scripts/gt_api.py to this shape, the
    // /api/predict route would silently fail Zod validation on every
    // call (BLOCKER). This test catches that regression.
    const oldBrokenShape = {
      predictions: [{ drug: "aspirin", disease: "cancer", score: 0.87 }],
      // OLD: snake_case + missing required fields
      model_version: "gt_v1",
      n_pairs: 1,
      // missing: source, generatedAt, count, checkpointPath
    };
    const result = GtPredictResponseSchema.safeParse(oldBrokenShape);
    expect(result.success).toBe(false);
    if (!result.success) {
      // The error must mention the missing required fields
      const missingFields = result.error.issues.map((i) => i.path.join("."));
      expect(missingFields).toEqual(
        expect.arrayContaining(["source", "generatedAt", "count"])
      );
    }
  });

  test("REJECTS response missing required `predictions` field", () => {
    const missingPredictions = {
      source: "gt_checkpoint",
      generatedAt: "2026-07-20T03:00:00.000Z",
      count: 0,
    };
    const result = GtPredictResponseSchema.safeParse(missingPredictions);
    expect(result.success).toBe(false);
  });

  test("REJECTS response missing required `generatedAt` field — guards against Pydantic Optional=None default", () => {
    // scripts/gt_api.py L225 declares `generatedAt: Optional[str] = None`.
    // The predict() function at L374 always sets it to a real ISO timestamp,
    // but if a future refactor forgets to set it, the response would have
    // generatedAt=null. The Zod schema declares generatedAt as required
    // (z.string()), so this would fail validation — surfacing the bug
    // immediately instead of letting a null propagate to the dashboard.
    const nullGeneratedAt = {
      predictions: [],
      source: "gt_checkpoint",
      generatedAt: null, // <-- the bug
      count: 0,
    };
    const result = GtPredictResponseSchema.safeParse(nullGeneratedAt);
    expect(result.success).toBe(false);
  });

  test("ACCEPTS response with `note` field on prediction (drug not in graph case)", () => {
    // graph_transformer/service.py L741-745 appends a `note` field to
    // predictions when the drug or disease is not in the graph. The Zod
    // schema declares `note` as optional on GtPredictionSchema — verify.
    const shapeWithNote = {
      predictions: [
        {
          drug: "unknown_drug",
          disease: "cancer",
          score: 0.0,
          confidence: 0.0,
          note: "drug or disease not in graph",
        },
      ],
      source: "gt_checkpoint",
      modelVersion: "gt_v113",
      generatedAt: "2026-07-20T03:00:00.000Z",
      count: 1,
      checkpointPath: null,
      error_count: 1,
      error_rate: 1.0,
    };
    const result = GtPredictResponseSchema.safeParse(shapeWithNote);
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.predictions[0].note).toBe("drug or disease not in graph");
    }
  });

  test("GtTopKResponseSchema ACCEPTS the canonical /top-k response shape", () => {
    // graph_transformer/service.py top_k() returns the same shape but
    // without error_count/error_rate. Verify the top-k schema accepts it.
    const topKShape = {
      predictions: [
        { drug: "metformin", disease: "cancer", score: 0.91 },
        { drug: "aspirin", disease: "cancer", score: 0.85 },
      ],
      source: "gt_checkpoint",
      modelVersion: "gt_v113",
      generatedAt: "2026-07-20T03:00:00.000Z",
      count: 2,
      checkpointPath: "/opt/ml_artifacts/best_model.pt",
    };
    const result = GtTopKResponseSchema.safeParse(topKShape);
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.predictions).toHaveLength(2);
      expect(result.data.count).toBe(2);
    }
  });

  test("ACCEPTS response when GT service is unreachable (source: 'none' graceful degrade)", () => {
    // gt-inference.ts predictPairs() returns this shape when
    // GT_SERVICE_URL is not set OR the service is unreachable. The route
    // returns this directly (not a 500) so the dashboard shows a clear
    // "model not trained yet" state. Verify the schema accepts it.
    const gracefulDegradeShape = {
      predictions: [],
      source: "none",
      generatedAt: "2026-07-20T03:00:00.000Z",
      count: 0,
      checkpointPath: null,
      note: "GT_SERVICE_URL is not set.",
    };
    const result = GtPredictResponseSchema.safeParse(gracefulDegradeShape);
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.source).toBe("none");
      expect(result.data.count).toBe(0);
      expect(result.data.predictions).toEqual([]);
    }
  });
});
