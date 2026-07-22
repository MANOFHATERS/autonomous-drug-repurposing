/**
 * FE-011 ROOT FIX (Teammate 14, HIGH): Zod schemas for non-ML API responses.
 *
 * ROOT CAUSE (forensic audit):
 *   The api-client.ts previously typed `searchPatents`, `buildEvidencePackage`,
 *   and `getEvidencePackage` with `any[]` / `any` for their return values:
 *
 *     searchPatents: (q) => request<{ items: any[] }>(`/api/patents/search?q=...`),
 *     buildEvidencePackage: (body) => request<{ id: string; package: any; markdown: string }>(...),
 *     getEvidencePackage: (id) => request<{ id: string; package: any; markdown: string }>(...),
 *
 *   The audit flagged this as a TypeScript safety hole: future contract drift
 *   between the routes and consumers would not be caught at compile time.
 *   Patient-safety-relevant fields (patent expiration date, assignee, evidence
 *   package literature counts) could disappear silently. The `any` types
 *   disabled type-checking for every consumer of these methods.
 *
 * ROOT FIX:
 *   1. Hand-write Zod schemas for the route response shapes, mirroring the
 *      actual types the routes return (PatentRecord from patentsview.ts;
 *      EvidencePackage from lib/services/evidence-package.ts).
 *   2. Wire the schemas into the request<T>() calls in api-client.ts so
 *      FE-066 runtime validation fires on every response. If the route
 *      drifts from the schema, the caller sees a structured ApiError at
 *      the fetch boundary instead of a cryptic render error.
 *   3. The TypeScript types for these methods are now derived from the
 *      Zod schemas via `z.infer<typeof ...>`, so the compile-time type
 *      and the runtime validation can't drift.
 *
 * Why these live here (not in ml-contracts.ts):
 *   `ml-contracts.ts` is specifically for ML service (Phase 1-4 Python
 *   service) response contracts. Patents and evidence-package responses
 *   are Next.js API route responses (which may internally call external
 *   biomedical APIs, but the route itself shapes the response). Keeping
 *   them in a separate file preserves the separation of concerns.
 *
 * SCIENTIFIC INTEGRITY:
 *   The patent and evidence-package responses carry IP and clinical
 *   decision-support data. A silent field rename (e.g., `patentNumber`
 *   → `patent_number`) could make a pharma partner believe a patent
 *   expired when it didn't, or miss a key literature citation in an
 *   evidence package. The Zod validation catches these at the fetch
 *   boundary — the caller sees "response_shape_mismatch" instead of
 *   rendering incomplete data.
 */

import { z } from "zod";

// ============================================================================
// /api/patents/search — response schema
// ============================================================================
//
// The route at app/api/patents/search/route.ts returns:
//   {
//     items: PatentRecord[],     // real USPTO patents from PatentsView
//     total: number,             // true total_hits reported by PatentsView
//     paginated: boolean,        // whether pagination was applied
//     pagesFetched: number,      // number of PatentsView pages fetched
//     reason?: string            // optional failure reason (e.g., missing API key)
//   }
//
// PatentRecord is defined in lib/services/patentsview.ts. The Zod schema
// below mirrors it EXACTLY — if patentsview.ts adds a field, this schema
// MUST be updated (and vice versa). The runtime Zod validation catches
// any drift between the route's actual response and this schema.

export const PatentRecordSchema = z.object({
  patentNumber: z.string(),
  title: z.string(),
  abstract: z.string(),
  grantDate: z.string(),
  inventors: z.array(z.string()),
  assignees: z.array(z.string()),
  cpcLabels: z.array(z.string()),
  url: z.string(),
});

export const PatentSearchResponseSchema = z.object({
  items: z.array(PatentRecordSchema),
  total: z.number(),
  paginated: z.boolean(),
  pagesFetched: z.number(),
  reason: z.string().optional(),
});

export type PatentRecord = z.infer<typeof PatentRecordSchema>;
export type PatentSearchResponse = z.infer<typeof PatentSearchResponseSchema>;

// ============================================================================
// /api/evidence-package (POST + GET?id=) — response schema
// ============================================================================
//
// The route at app/api/evidence-package/route.ts returns:
//   {
//     id: string,                          // DB row ID (Prisma cuid)
//     package: EvidencePackage,            // the built package (real data)
//     markdown: string                     // PDF-ready markdown rendering
//   }
//
// EvidencePackage is defined in lib/services/evidence-package.ts. The Zod
// schema below mirrors its shape EXACTLY. The nested PubMedArticle,
// ClinicalTrial, and DrugSafetySummary sub-schemas mirror the types from
// pubmed.ts, clinical-trials.ts, and openfda.ts respectively.
//
// NOTE: the schema is permissive about optional fields — every optional
// field in the source type is `.optional()` here. This is intentional:
// the route may omit fields that the upstream service didn't return (e.g.,
// `doi` is only present when PubMed returns it). The Zod validation
// catches MISSING required fields and TYPE mismatches, not missing optional
// fields.

const PubMedArticleSchema = z.object({
  pmid: z.string(),
  title: z.string(),
  journal: z.string(),
  authors: z.array(z.string()),
  pubDate: z.string(),
  abstract: z.string().optional(),
  abstractTruncated: z.string().optional(),
  abstractIsTruncated: z.boolean().optional(),
  abstractFullLength: z.number().optional(),
  doi: z.string().optional(),
  url: z.string(),
});

const ClinicalTrialSchema = z.object({
  nctId: z.string(),
  title: z.string(),
  status: z.string(),
  phase: z.string(),
  enrollment: z.number().optional(),
  startDate: z.string().optional(),
  completionDate: z.string().optional(),
  sponsor: z.string().optional(),
  conditions: z.array(z.string()),
  interventions: z.array(z.string()),
  studyType: z.string(),
  url: z.string(),
  briefSummary: z.string().optional(),
  locations: z.array(z.string()),
});

const AdverseEventReactionSchema = z.object({
  term: z.string(),
  count: z.number(),
});

const DrugSafetySummarySchema = z.object({
  brandName: z.string(),
  genericName: z.string(),
  totalReports: z.number(),
  seriousReports: z.number(),
  seriousReportsWithDeath: z.number(),
  topReactions: z.array(AdverseEventReactionSchema),
  disclaimer: z.string(),
});

const EvidencePackageServiceStatusSchema = z.object({
  literature: z.enum(["ok", "failed"]),
  clinicalTrials: z.enum(["ok", "failed"]),
  safety: z.enum(["ok", "failed"]),
});

export const EvidencePackageSchema = z.object({
  drug: z.string(),
  disease: z.string(),
  generatedAt: z.string(),
  literature: z.object({
    total: z.number(),
    articles: z.array(PubMedArticleSchema),
  }),
  clinicalTrials: z.object({
    total: z.number(),
    trials: z.array(ClinicalTrialSchema),
  }),
  safety: DrugSafetySummarySchema.nullable(),
  notes: z.string(),
  serviceStatus: EvidencePackageServiceStatusSchema,
});

export type EvidencePackageResponse = z.infer<typeof EvidencePackageSchema>;

export const EvidencePackageBuildResponseSchema = z.object({
  id: z.string(),
  package: EvidencePackageSchema,
  markdown: z.string(),
});

export type EvidencePackageBuildResponse = z.infer<typeof EvidencePackageBuildResponseSchema>;
