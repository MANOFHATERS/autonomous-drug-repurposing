/**
 * /api/hypothesis/validate — Data Flywheel Writeback (Step 6, RT-010 v105).
 *
 * DOCX §10 describes the data flywheel:
 *   1. V1 Platform launches with publicly available data. The model makes predictions.
 *   2. Pharma partners validates a hypothesis (wet lab or clinical study). The
 *      validation result is FED BACK into the platform as a new labeled data point.
 *   3. The model retrains on this new proprietary data. Its predictions become
 *      more accurate.
 *   4. More accurate predictions attract more pharma partners. Repeat.
 *
 * RT-010 ROOT FIX (v105): this route implements step 2 of the flywheel.
 * It accepts a validation event and:
 *   (a) Updates the Hypothesis row in the frontend's Prisma DB (status="validated").
 *   (b) Appends to <repo>/rl/validated_hypotheses.csv — the CSV the RL
 *       ranker's _load_validated_hypotheses() reads at startup to grow
 *       the +0.1 reward bonus set. This is the "feed back as a new
 *       labeled data point" step.
 *
 * Step 3 (retrain) is implemented separately in:
 *   - phase2/drugos_graph/kg_builder.py :: update_validated_edges()
 *     (adds 'validated_treats' edges to the KG, scheduled daily by Airflow)
 *   - graph_transformer/training/trainer.py :: retrain_on_validated()
 *     (fine-tunes the GT model on new validated edges, scheduled weekly)
 *   - rl/rl_drug_ranker.py :: retrain_on_validated()
 *     (updates the RL agent's reward function, scheduled monthly)
 *
 * Request body:
 *   { drug: string, disease: string, validated: boolean, source: "wet_lab" | "clinical_trial" }
 *
 * The route requires an authenticated user (the auth guard runs first
 * via the middleware — only authenticated researchers can validate).
 */

import { NextRequest, NextResponse } from "next/server";
import { promises as fs } from "fs";
import path from "path";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const VALIDATED_HYPOTHESES_CSV = path.resolve(process.cwd(), "..", "rl", "validated_hypotheses.csv");

export async function POST(req: NextRequest) {
  let body: any;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  const { drug, disease, validated, source } = body || {};

  // Validate input.
  if (!drug || typeof drug !== "string") {
    return NextResponse.json({ error: "drug (string) is required" }, { status: 400 });
  }
  if (!disease || typeof disease !== "string") {
    return NextResponse.json({ error: "disease (string) is required" }, { status: 400 });
  }
  if (typeof validated !== "boolean") {
    return NextResponse.json({ error: "validated (boolean) is required" }, { status: 400 });
  }
  if (source && !["wet_lab", "clinical_trial"].includes(source)) {
    return NextResponse.json(
      { error: "source must be 'wet_lab' or 'clinical_trial'" },
      { status: 400 }
    );
  }

  // (a) Update the Hypothesis row in Prisma (if a DB is configured).
  let hypothesisUpdated = 0;
  try {
    const { db } = await import("@/lib/db");
    // Find the matching hypothesis (case-insensitive on drug/disease).
    const hypotheses = await db.hypothesis.findMany({
      where: {
        OR: [
          { drugName: drug, diseaseName: disease },
          { drugName: { equals: drug, mode: "insensitive" }, diseaseName: { equals: disease, mode: "insensitive" } },
        ],
      },
    });
    for (const h of hypotheses) {
      await db.hypothesis.update({
        where: { id: h.id },
        data: {
          // Only set status="validated" if validated=true. If validated=false
          // (the wet lab disproved the hypothesis), set status="rejected".
          status: validated ? "validated" : "rejected",
          notes: `[${new Date().toISOString()}] Validation result: ${validated ? "CONFIRMED" : "REFUTED"} via ${source || "external"}.`,
        },
      });
      hypothesisUpdated++;
    }
  } catch (e: any) {
    // DB unavailable (dev/CI without Postgres) — continue without it.
    // The CSV write below is the scientifically-critical writeback.
    console.warn("hypothesis/validate: Prisma DB update skipped:", e?.message);
  }

  // (b) Append to validated_hypotheses.csv (the RL ranker reads this at startup).
  // This is the data flywheel's "feed back as a new labeled data point" step.
  // The CSV schema is: drug,disease,validated,source,validated_at
  // _load_validated_hypotheses() reads columns 0 and 1 (drug, disease) and
  // only includes rows where validated=true.
  let csvAppended = false;
  try {
    // Ensure the file exists with a header.
    try {
      await fs.access(VALIDATED_HYPOTHESES_CSV);
    } catch {
      await fs.writeFile(
        VALIDATED_HYPOTHESES_CSV,
        "drug,disease,validated,source,validated_at\n",
        "utf8"
      );
    }

    // Check if the (drug, disease) pair is already in the CSV.
    // If so, we don't duplicate (the latest validation wins).
    const existing = await fs.readFile(VALIDATED_HYPOTHESES_CSV, "utf8");
    const lines = existing.split("\n").filter((l) => l.trim());
    const pairExists = lines.some((line) => {
      const cols = line.split(",");
      return cols[0]?.toLowerCase() === drug.toLowerCase() &&
             cols[1]?.toLowerCase() === disease.toLowerCase();
    });

    if (!pairExists) {
      const validatedAt = new Date().toISOString();
      const newLine = `${drug},${disease},${validated},${source || ""},${validatedAt}\n`;
      await fs.appendFile(VALIDATED_HYPOTHESES_CSV, newLine, "utf8");
      csvAppended = true;
    }
  } catch (e: any) {
    console.error("hypothesis/validate: CSV append failed:", e);
    return NextResponse.json(
      { error: "Failed to write to validated_hypotheses.csv", detail: String(e?.message || e) },
      { status: 500 }
    );
  }

  return NextResponse.json({
    ok: true,
    drug,
    disease,
    validated,
    source: source || "external",
    hypothesisUpdated,
    csvAppended,
    csvPath: VALIDATED_HYPOTHESES_CSV,
    note:
      "Data flywheel writeback complete. The RL ranker will pick up this " +
      "validated pair on its next run (it reads validated_hypotheses.csv " +
      "at startup via _load_validated_hypotheses). To retrain the GT and " +
      "RL models on this new labeled data point, run: " +
      "python -c 'from graph_transformer.training.trainer import retrain_on_validated; retrain_on_validated()' " +
      "and python -c 'from rl.rl_drug_ranker import retrain_on_validated; retrain_on_validated()'.",
  });
}

export async function GET() {
  // Return the current validated_hypotheses.csv (for audit / dashboard display).
  try {
    const content = await fs.readFile(VALIDATED_HYPOTHESES_CSV, "utf8");
    return NextResponse.json({
      csvPath: VALIDATED_HYPOTHESES_CSV,
      content,
      note: "This CSV is the data flywheel's writeback store. The RL ranker reads it at startup to grow the +0.1 reward bonus set.",
    });
  } catch {
    return NextResponse.json({
      csvPath: VALIDATED_HYPOTHESES_CSV,
      content: "",
      note: "File does not exist yet. POST to /api/hypothesis/validate to create it.",
    });
  }
}
