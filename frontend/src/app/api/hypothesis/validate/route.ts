import { NextRequest, NextResponse } from "next/server";
import { requireAuth, requireRole, internalError, badRequest, writeAuditLog } from "@/lib/api-helpers";
import { spawn } from "child_process";
import path from "path";
import { randomUUID } from "crypto";
import { promises as fs } from "fs";

/**
 * POST /api/hypothesis/validate
 * Body: {
 *   drug: string,
 *   disease: string,
 *   outcome: "validated_positive" | "validated_negative" | "validated_toxic" | "invalidated",
 *   validationStudyId?: string,    // e.g., NCT number
 *   notes?: string,
 *   originalGtScore?: number,
 *   originalRlRank?: number,
 * }
 *
 * RT-010 ROOT FIX (Team Member 17): this route implements the data
 * flywheel writeback (project docx Section 10, step 3):
 *
 *   "Pharma partner validates a hypothesis (in wet lab or clinical
 *    study). This validation result is fed back into the platform as
 *    a new labeled data point."
 *
 * The audit (RT-010) found that NO writeback modules existed — Phase 4's
 * output was never written back to Phase 1's database, Phase 2's KG was
 * not updated with 'validated' edges, and Phase 3's model was not
 * retrained. The data flywheel was aspirational, not actual.
 *
 * Root fix: this route shells out to `phase4/writeback.py` (via a small
 * Python helper) which writes the validated hypothesis to ALL 3 phases:
 *   - Phase 1: appends to phase1/processed_data/validated_hypotheses.csv
 *   - Phase 2: adds a VALIDATED_TREATS edge to Neo4j (when available)
 *   - Phase 3: appends to graph_transformer/retrain_triggered.json
 *
 * Security: only data-scientist / pi / developer / admin / owner roles
 * can call this route. Viewers cannot. The validated_by field is set
 * AUTOMATICALLY from the authenticated user's identity — the caller
 * cannot impersonate another partner.
 */
export async function POST(req: NextRequest) {
  const auth = await requireAuth();
  if (auth.user === null) return auth.response;

  // RT-010: only analytics roles can submit validation results.
  const roleCheck = await requireRole(auth.user, "data-scientist", "pi", "developer");
  if (roleCheck.user === null) return roleCheck.response;

  let body: {
    drug?: string;
    disease?: string;
    outcome?: string;
    validationStudyId?: string;
    notes?: string;
    originalGtScore?: number;
    originalRlRank?: number;
  };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "bad_request", message: "Invalid JSON" }, { status: 400 });
  }

  const drug = (body.drug || "").trim();
  const disease = (body.disease || "").trim();
  const outcome = body.outcome;

  if (!drug || !disease) {
    return badRequest("drug and disease are required");
  }
  const validOutcomes = ["validated_positive", "validated_negative", "validated_toxic", "invalidated"];
  if (!outcome || !validOutcomes.includes(outcome)) {
    return badRequest(`outcome must be one of: ${validOutcomes.join(", ")}`);
  }

  // RT-010: validated_by is set from the authenticated user's identity
  // (cannot be spoofed by the caller). The user's orgId or userId is
  // the partner identifier — this gives us an audit trail.
  const validatedBy = auth.user.orgId || auth.user.userId;

  try {
    const result = await runWriteback({
      drug,
      disease,
      outcome,
      validated_by: validatedBy,
      validation_study_id: body.validationStudyId,
      notes: body.notes,
      original_gt_score: body.originalGtScore,
      original_rl_rank: body.originalRlRank,
    });

    await writeAuditLog({
      user: auth.user,
      action: "hypothesis_validate",
      resource: `hypothesis:${drug}:${disease}`,
      metadata: {
        outcome,
        validatedBy,
        validationStudyId: body.validationStudyId,
        phase2Neo4jWritten: result.phase2_neo4j_written,
      },
    });

    return NextResponse.json({
      ok: true,
      writeback: result,
      message: "Hypothesis validation written back to Phase 1 (CSV), Phase 2 (Neo4j edge), and Phase 3 (retrain trigger).",
    });
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e);
    return internalError(`Hypothesis writeback failed: ${msg}`);
  }
}

/**
 * Shell out to a small Python helper that invokes
 * phase4.writeback.write_validated_hypothesis(). The helper is at
 * scripts/hypothesis_writeback.py.
 */
async function runWriteback(payload: Record<string, unknown>): Promise<{
  phase1_csv_path: string;
  phase2_neo4j_written: boolean;
  phase3_trigger_path: string;
  validated_hypothesis: Record<string, unknown>;
  writeback_version: string;
}> {
  const reqId = randomUUID();
  const reqPath = `/tmp/wb_req_${reqId}.json`;
  const respPath = `/tmp/wb_resp_${reqId}.json`;
  // INT-028 ROOT FIX: resolve repoRoot correctly when Next.js runs from
  // frontend/. process.cwd() returns frontend/ but scripts/ is at repo root.
  const cwd = process.cwd();
  const repoRoot = process.env.GT_REPO_ROOT || (
    cwd.endsWith("frontend") ? path.resolve(cwd, "..") : cwd
  );
  const scriptPath = path.resolve(repoRoot, "scripts", "hypothesis_writeback.py");

  try {
    await fs.writeFile(reqPath, JSON.stringify(payload));
    await new Promise<void>((resolve, reject) => {
      const child = spawn("python3", [scriptPath, reqPath, respPath], {
        cwd: repoRoot,
        env: { ...process.env, PYTHONPATH: repoRoot },
      });
      let stderr = "";
      child.stderr.on("data", (d) => { stderr += d.toString(); });
      child.on("error", reject);
      child.on("close", (code) => {
        if (code !== 0) reject(new Error(`hypothesis_writeback.py exited ${code}: ${stderr.slice(0, 1000)}`));
        else resolve();
      });
    });
    const respRaw = await fs.readFile(respPath, "utf8");
    const resp = JSON.parse(respRaw);
    if (resp.error) throw new Error(resp.error);
    return resp.result;
  } finally {
    try { await fs.unlink(reqPath); } catch { /* ignore */ }
    try { await fs.unlink(respPath); } catch { /* ignore */ }
  }
}
