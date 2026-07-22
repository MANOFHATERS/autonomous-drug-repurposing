/**
 * Frontend API client for DruGOS.
 *
 * Wraps `fetch` with credentials included, JSON parsing, error normalization,
 * and small typed helpers for every backend endpoint the UI needs.
 *
 * All cookies are HttpOnly so the browser sends them automatically — we never
 * touch tokens from JavaScript.
 */

import { z, type ZodType } from "zod";

export interface ApiError {
  error: string;
  message?: string;
  status: number;
}

export interface AuthUser {
  id: string;
  email: string;
  name: string | null;
  role: string;
  title?: string | null;
  bio?: string | null;
  status?: string;
  emailVerified?: boolean;
  academicVerified?: boolean;
  mfaEnabled?: boolean;
  lastLoginAt?: string | null;
  createdAt?: string;
}

export interface Organization {
  id: string;
  name: string;
  slug: string;
  plan: string;
  role: string;
}

export interface TeamMember {
  id: string;
  name: string;
  email: string;
  role: string;
  orgRole: string;
  title: string | null;
  bio: string | null;
  status: string;
  lastLoginAt: string | null;
  joinedAt: string;
}

export interface AuthMeResponse {
  user: AuthUser;
  organizations: Organization[];
  activeOrganizationId: string | null;
}

export interface Project {
  id: string;
  name: string;
  description: string | null;
  status: string;
  visibility: string;
  ownerId: string;
  organizationId: string;
  tags: string;
  createdAt: string;
  updatedAt: string;
  _count?: { hypotheses: number; comments: number };
}

export interface Hypothesis {
  id: string;
  projectId: string;
  title: string;
  drugName: string;
  diseaseName: string;
  status: string;
  plausibilityScore: number | null;
  safetyScore: number | null;
  marketScore: number | null;
  overallScore: number | null;
  notes: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface Comment {
  id: string;
  projectId: string;
  userId: string | null;
  authorName: string;
  body: string;
  createdAt: string;
}

export interface ProjectActivity {
  id: string;
  projectId: string;
  type: string;
  actorName: string;
  summary: string;
  createdAt: string;
}

export interface ProjectDetail extends Project {
  hypotheses: Hypothesis[];
  comments: Comment[];
  activities: ProjectActivity[];
}

/**
 * FE-024 ROOT FIX: Aligned with the actual billing.ts Plan interface.
 * The backend Plan has `priceCents` (NOT `price`), NO `currency`, and
 * NO `interval` field. The previous type had `price: number` which was
 * always undefined because the route returns PLANS directly from
 * billing.ts — causing "$0" and "$NaN" renders in the subscription UI.
 */
export interface Plan {
  id: string;
  name: string;
  priceCents: number;
  seats: number;
  features: string[];
}

export interface Subscription {
  id: string;
  organizationId: string;
  plan: string;
  status: string;
  seats: number;
  currentPeriodStart: string;
  currentPeriodEnd: string;
  cancelAtPeriodEnd: boolean;
}

export interface Invoice {
  id: string;
  number: string;
  amountCents: number;
  currency: string;
  status: string;
  periodStart: string;
  periodEnd: string;
  dueDate: string;
  pdfUrl: string | null;
  createdAt: string;
}

export interface ApiKey {
  id: string;
  name: string;
  prefix: string;
  lastUsedAt: string | null;
  revokedAt: string | null;
  createdAt: string;
  // Only returned once on creation
  rawKey?: string;
}

export interface Notification {
  id: string;
  type: string;
  title: string;
  body: string;
  readAt: string | null;
  createdAt: string;
}

export interface AuditLog {
  id: string;
  userId: string | null;
  actorName: string;
  action: string;
  resource: string | null;
  ip: string | null;
  userAgent: string | null;
  metadata: string;
  createdAt: string;
}

export interface AdminUser {
  id: string;
  email: string;
  name: string | null;
  role: string;
  status: string;
  emailVerified: boolean;
  // FE-009 ROOT FIX: surfaced from /api/admin/users so the admin screen
  // can show the real 2FA state per user instead of a fabricated boolean.
  mfaEnabled?: boolean;
  createdAt: string;
  lastLoginAt: string | null;
}

export interface SystemStatus {
  services: Record<string, { available: boolean; service: string; reason?: string }>;
  generatedAt: string;
}

export interface DrugSearchResult {
  rxcui: string;
  name: string;
  synonym?: string;
  tty?: string;
}

/**
 * FE-023 ROOT FIX: Aligned with the actual MeshDescriptor returned by
 * the MeSH service (mesh.ts). The service returns `descriptorUi`
 * (lowercase 'i') and `name` — NOT `descriptorUI` (uppercase 'I') and
 * NOT `descriptorName`. The previous type caused disease search
 * suggestions to render with id=undefined and name=undefined.
 */
export interface DiseaseSearchResult {
  descriptorUi: string;
  name: string;
  scopeNote?: string;
  treeNumber?: string[];
}

export interface ClinicalTrial {
  nctId: string;
  title: string;
  status: string;
  phase?: string;
  conditions: string[];
  interventions: string[];
  sponsor?: string;
  startDate?: string;
  completionDate?: string;
  enrollment?: number;
  url?: string;
}

export interface PubMedArticle {
  pmid: string;
  title: string;
  authors: string[];
  journal?: string;
  pubDate?: string;
  abstract?: string;
  url?: string;
}

export interface SafetyReport {
  // BE-040 ROOT FIX (Team Member 12): the previous type had `drug: string`
  // — but the actual `DrugSafetySummary` returned by /api/safety/[drug]
  // (from frontend/src/lib/services/openfda.ts) has `brandName` and
  // `genericName`, NOT `drug`. Any consumer that read `safetyData.drug`
  // got `undefined`. We align the api-client type with the actual route
  // response shape so consumers can rely on the typed contract.
  brandName: string;
  genericName: string;
  totalReports: number;
  seriousReports: number;
  seriousReportsWithDeath?: number;
  topReactions: { term: string; count: number }[];
  disclaimer: string;
}

// ---------------------------------------------------------------------------
// ML / Phase 4 handoff types — ROOT FIX for FE-001/FE-002/FE-003
// ---------------------------------------------------------------------------
//
// FE-010 ROOT FIX (Teammate 14, HIGH): the previous version of this
// section hand-wrote DUPLICATE TypeScript interfaces for every ML
// response shape (DatasetStatsResponse, KnowledgeGraphStatsResponse,
// RlRankerResponse, RankedHypothesis, DatasetSourceStat, GraphSourceStat).
//
// These hand-written interfaces DRIFTED from the canonical Zod schemas
// in ml-contracts.ts (the single source of truth). The audit found:
//   - api-client said `source` is REQUIRED with a narrow enum
//     ("dataset_service" | "local_checkpoint" | "none"); ml-contracts
//     said it's an optional string. Type confusion.
//   - api-client had NO `status` field; ml-contracts had it. The
//     dataset-service.ts returns the ml-contracts version, then patches
//     it to add `source="dataset_service"` if missing. The api-client
//     type couldn't see the `status` field.
//   - api-client's `RlRankerResponse.source` was
//     `"rl_service" | "local_csv" | "none"` — but the actual Python
//     service returns `"service"` (per P4-045 fix in rl/service.py).
//     The stale api-client type caused the previous rl-ranker.ts to
//     HARDCODE `source: "rl_service"` to satisfy the type, ignoring
//     the real backend value (see rl-ranker.ts header comment for the
//     forensic history).
//
// ROOT FIX: the api-client no longer hand-writes these types. It re-exports
// the canonical Zod-derived types from ml-contracts.ts (for DatasetStats,
// KgStats) and from rl-ranker.ts (for RlRankerResponse — the rl-ranker.ts
// version is the enriched, consumer-facing type with the narrowed source
// union that includes "service"). Callers that imported these names from
// @/lib/api-client continue to work — the export names are preserved.
//
// The Zod schemas are ALSO wired into the request<T>() calls below so
// FE-066 runtime validation fires on every ML response. Contract drift
// between the Python services and the frontend is now caught at the
// fetch boundary, not 10 layers deep in a React render.

// Re-export canonical ML types (Zod-derived — single source of truth).
// The `export type` syntax ensures these are type-only imports: they
// don't pull the Zod runtime into the client bundle unless the caller
// explicitly imports the schema.
export type {
  DatasetStatsResponse,
  DatasetSourceStat,
  KgStatsResponse as KnowledgeGraphStatsResponse,
  GraphSourceStat,
  RankedHypothesis,
} from "@/lib/ml-contracts";

// Re-export the enriched RL ranker response type (the rl-ranker.ts
// version with the narrowed `source` union and the camelCase aliases).
// The api-client previously hand-wrote a STALE version with
// `source: "rl_service" | "local_csv" | "none"` — that version is
// removed; this re-export is the canonical one.
export type { RlRankerResponse } from "@/lib/services/rl-ranker";

// Import the schemas we need for runtime validation (FE-066 wiring).
import {
  DatasetStatsResponseSchema,
  KgStatsResponseSchema,
  type DatasetStatsResponse,
  type KgStatsResponse,
} from "@/lib/ml-contracts";
// FE-011 ROOT FIX (Teammate 14, HIGH): import response Zod schemas for
// patents and evidence-package. These validate the route responses at
// the fetch boundary so contract drift is caught immediately.
import {
  PatentSearchResponseSchema,
  EvidencePackageBuildResponseSchema,
  type PatentSearchResponse,
  type EvidencePackageBuildResponse,
} from "@/lib/response-schemas";
// FE-011 ROOT FIX: import the canonical EvidencePackage type from
// lib/services/evidence-package (the type the route ACTUALLY returns
// in its `package` field). Renamed locally to `BuiltEvidencePackage`
// to avoid a name collision with the `EvidencePackageSummary` interface
// below (which is the DB row shape returned by listEvidencePackages).
import type { EvidencePackage as BuiltEvidencePackage } from "@/lib/services/evidence-package";

// Re-export the canonical EvidencePackage type so consumers that want
// the real (built) package shape can import it from @/lib/api-client.
// This is the type of the `package` field in buildEvidencePackage /
// getEvidencePackage responses.
export type { BuiltEvidencePackage as EvidencePackage };

/**
 * EvidencePackageSummary — the DB row shape returned by GET /api/evidence-package
 * (the list endpoint).
 *
 * FE-011 ROOT FIX (Teammate 14, HIGH): the previous version of this
 * interface was named `EvidencePackage` and had `summary: string` and
 * `updatedAt: string` fields. Two problems:
 *
 *   1. The route at app/api/evidence-package/route.ts GET handler selects
 *      ONLY `{ id, drugName, diseaseName, title, status, createdAt }`
 *      (line 187 of route.ts) — `summary` and `updatedAt` are NEVER
 *      returned. The previous type lied: it said those fields were
 *      required, but they were always undefined at runtime. Consumers
 *      that read `pkg.summary` got `undefined`.
 *
 *   2. The name `EvidencePackage` collided with the canonical
 *      EvidencePackage type in lib/services/evidence-package.ts (the
 *      shape of the BUILT package — with literature, clinicalTrials,
 *      safety, etc.). The collision made it ambiguous which shape a
 *      consumer was working with.
 *
 * ROOT FIX:
 *   - Rename this interface to `EvidencePackageSummary` (it's the DB
 *     row summary used by the list endpoint — the name now matches
 *     the purpose).
 *   - Remove the `summary` and `updatedAt` fields (the route doesn't
 *     return them — claiming they're required was a lie).
 *   - The canonical `EvidencePackage` name now refers to the BUILT
 *     package type (re-exported above from lib/services/evidence-package).
 *
 * NOTE: this is a breaking type change for any consumer that imported
 * `EvidencePackage` from @/lib/api-client and accessed `.summary` or
 * `.updatedAt`. The grep across src/ shows NO such consumer — the
 * only file that imported `EvidencePackage` from api-client was
 * use-api-data.tsx, and it imported it only as a type for the list
 * response (which never accessed .summary or .updatedAt). The fix
 * is therefore safe.
 */
export interface EvidencePackageSummary {
  id: string;
  drugName: string;
  diseaseName: string;
  title: string;
  status: string;
  createdAt: string;
}

// ---------------------------------------------------------------------------
// Core fetch wrapper
// ---------------------------------------------------------------------------

/**
 * FE-066 ROOT FIX: Runtime response validation.
 *
 * Previously: request<T> did `let body: any` and returned `body as T`. The
 * generic T was a LIE — if the API returned a different shape, the caller
 * got an object TypeScript thought was T but wasn't, and the bug only
 * surfaced at runtime (usually in a React render, far from the cause).
 *
 * Root fix: request<T> now accepts an optional `schema: ZodType<T>`. When
 * provided, the parsed body is run through `schema.parse(body)` before
 * return. Zod either confirms the shape or throws an ApiError with status
 * 0 and a descriptive message — the caller sees the contract violation
 * immediately, at the call site, instead of a cryptic render error.
 *
 * Backward-compat: if no schema is passed, the old `as T` behavior is
 * preserved (so we don't break the ~50 existing call sites in one go).
 * New code SHOULD pass a schema; the lint rule can be tightened later.
 */
async function request<T>(
  url: string,
  init?: RequestInit & { skipAuthRedirect?: boolean; schema?: ZodType<T> }
): Promise<T> {
  const res = await fetch(url, {
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers || {}),
    },
    ...init,
  });

  const text = await res.text();
  let body: any = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = { raw: text };
    }
  }

  if (!res.ok) {
    const err: ApiError = {
      error: body?.error || "request_failed",
      message: body?.message || `Request failed with status ${res.status}`,
      status: res.status,
    };
    // If 401 and not explicitly skipped, dispatch an event so the auth
    // provider can redirect to login.
    if (res.status === 401 && !init?.skipAuthRedirect) {
      // Guard for non-browser environments (jest, SSR).
      if (typeof window !== "undefined" && window.dispatchEvent) {
        window.dispatchEvent(new CustomEvent("drugos:unauthorized"));
      }
    }
    throw err;
  }

  // FE-066: if a zod schema was provided, validate the body. On failure
  // throw a structured ApiError so callers can distinguish contract
  // violations from network errors.
  if (init?.schema) {
    const parsed = init.schema.safeParse(body);
    if (!parsed.success) {
      const err: ApiError = {
        error: "response_shape_mismatch",
        message: `API response from ${url} did not match expected schema: ${parsed.error.message}`,
        status: 0, // 0 = client-side validation failure, not an HTTP status
      };
      throw err;
    }
    return parsed.data;
  }

  return body as T;
}

export const api = {
  // AUTH
  register: (body: {
    email: string;
    password: string;
    name: string;
    organizationName?: string;
    role?: string;
    title?: string;
    bio?: string;
  }) =>
    request<{ user: AuthUser; organizationId: string }>("/api/auth/register", {
      method: "POST",
      body: JSON.stringify(body),
      skipAuthRedirect: true,
    }),

  login: (body: { email: string; password: string }) =>
    request<{ user: AuthUser; organizationId: string }>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify(body),
      skipAuthRedirect: true,
    }),

  logout: () => request<{ ok: true }>("/api/auth/logout", { method: "POST" }),

  refresh: () => request<{ ok: true }>("/api/auth/refresh", { method: "POST" }),

  me: () =>
    request<AuthMeResponse>("/api/auth/me", {
      skipAuthRedirect: true,
      // FE-066: validate the /me response shape. If the server contract
      // drifts (e.g. `user` becomes `profile`), this throws immediately
      // instead of producing undefined-user render errors downstream.
      schema: z.object({
        user: z.object({
          id: z.string(),
          email: z.string(),
          name: z.string().nullable(),
          role: z.string(),
          title: z.string().nullable().optional(),
          bio: z.string().nullable().optional(),
          status: z.string().optional(),
          emailVerified: z.boolean().optional(),
          academicVerified: z.boolean().optional(),
          mfaEnabled: z.boolean().optional(),
          lastLoginAt: z.string().nullable().optional(),
          createdAt: z.string().optional(),
        }),
        organizations: z.array(
          z.object({
            id: z.string(),
            name: z.string(),
            slug: z.string(),
            plan: z.string(),
            role: z.string(),
          })
        ),
        activeOrganizationId: z.string().nullable(),
      }) as ZodType<AuthMeResponse>,
    }),
  updateMe: (body: { name?: string; title?: string; bio?: string }) =>
    request<{ user: AuthUser }>("/api/auth/me", { method: "PATCH", body: JSON.stringify(body) }),

  // TEAM
  listTeamMembers: () => request<{ items: TeamMember[]; total: number }>("/api/team"),

  // PROJECTS
  listProjects: () => request<{ items: Project[] }>("/api/projects"),
  createProject: (body: { name: string; description?: string; visibility?: "private" | "org" | "public"; tags?: string[] }) =>
    request<Project>("/api/projects", { method: "POST", body: JSON.stringify(body) }),
  getProject: (id: string) => request<ProjectDetail>(`/api/projects/${id}`),
  addHypothesis: (projectId: string, body: { title: string; drugName: string; diseaseName: string; notes?: string }) =>
    request<Hypothesis>(`/api/projects/${projectId}`, { method: "POST", body: JSON.stringify(body) }),
  // FE-073 ROOT FIX: authorName is intentionally NOT accepted. The server
  // derives it from the authenticated user's User.name || User.email.
  // Sending authorName in the body is a no-op (server ignores it).
  addComment: (projectId: string, body: { body: string }) =>
    request<Comment>(`/api/projects/${projectId}/comments`, { method: "POST", body: JSON.stringify(body) }),

  // BILLING
  listPlans: () => request<{ plans: Plan[] }>("/api/billing/plans"),
  getSubscription: () => request<{ subscription: Subscription | null; plans: Plan[] }>("/api/billing/subscription"),
  // FE-021 ROOT FIX: The billing/subscription route requires currentPassword
  // (re-authentication) and optionally totpCode or mfaTicket when MFA is
  // enabled. The previous signature only sent { planId } — every plan change
  // got 400 "currentPassword is required". Updated signature accepts the
  // full required parameter object.
  changePlan: (body: { planId: string; currentPassword: string; totpCode?: string; mfaTicket?: string }) =>
    request<{ ok: true }>("/api/billing/subscription", { method: "POST", body: JSON.stringify(body) }),
  listInvoices: () => request<{ items: Invoice[] }>("/api/billing/invoices"),

  // API KEYS
  listApiKeys: () => request<{ items: ApiKey[] }>("/api/api-keys"),
  createApiKey: (name: string) =>
    request<ApiKey>("/api/api-keys", { method: "POST", body: JSON.stringify({ name }) }),
  revokeApiKey: (id: string) =>
    request<{ ok: true }>(`/api/api-keys/${id}/revoke`, { method: "POST" }),

  // NOTIFICATIONS
  listNotifications: () => request<{ items: Notification[] }>("/api/notifications"),
  markNotificationRead: (id: string) =>
    request<{ ok: true }>(`/api/notifications/${id}/read`, { method: "POST" }),

  // ADMIN
  listUsers: (limit = 50, offset = 0) =>
    request<{ items: AdminUser[]; total: number }>(`/api/admin/users?limit=${limit}&offset=${offset}`),
  updateUser: (body: { userId: string; role?: string; status?: string }) =>
    request<AdminUser>("/api/admin/users", { method: "PATCH", body: JSON.stringify(body) }),

  // AUDIT LOGS
  listAuditLogs: (limit = 100, offset = 0) =>
    request<{ items: AuditLog[]; total: number }>(`/api/audit-logs?limit=${limit}&offset=${offset}`),

  // SYSTEM STATUS
  getSystemStatus: () => request<SystemStatus>("/api/system/status"),

  // BIOMEDICAL DATA (live public APIs)
  searchDrugs: (q: string) =>
    request<{ items: DrugSearchResult[] }>(`/api/drugs/search?q=${encodeURIComponent(q)}`),
  searchDiseases: (q: string) =>
    request<{ items: DiseaseSearchResult[] }>(`/api/diseases/search?q=${encodeURIComponent(q)}`),
  // FE-022 ROOT FIX: The clinical-trials/search route requires `condition`
  // OR `intervention` — it does NOT accept a `q` param. The previous
  // signature always caused 400 errors. Updated to accept the correct
  // params object matching the route's expected query parameters.
  searchClinicalTrials: (params: { condition?: string; intervention?: string; limit?: number; pageToken?: string }) => {
    const qs = new URLSearchParams();
    if (params.condition) qs.set("condition", params.condition);
    if (params.intervention) qs.set("intervention", params.intervention);
    if (params.limit) qs.set("limit", String(params.limit));
    if (params.pageToken) qs.set("pageToken", params.pageToken);
    return request<{ items: ClinicalTrial[] }>(`/api/clinical-trials/search?${qs.toString()}`);
  },
  searchLiterature: (q: string) =>
    request<{ items: PubMedArticle[] }>(`/api/literature/search?q=${encodeURIComponent(q)}`),
  getSafety: (drug: string) =>
    request<SafetyReport>(`/api/safety/${encodeURIComponent(drug)}`),
  searchPatents: (q: string) =>
    request<PatentSearchResponse>(`/api/patents/search?q=${encodeURIComponent(q)}`, {
      // FE-011 ROOT FIX: wire the PatentSearchResponseSchema so FE-066
      // runtime validation fires. If the /api/patents/search route's
      // response shape drifts from the schema (e.g., a field is renamed
      // or removed), the caller sees a structured ApiError at the fetch
      // boundary instead of a cryptic render error 10 layers deep.
      schema: PatentSearchResponseSchema as unknown as ZodType<PatentSearchResponse>,
    }),

  // EVIDENCE PACKAGES
  // FE-011 ROOT FIX: listEvidencePackages returns EvidencePackageSummary[]
  // (the DB row shape — NOT the built package shape). The previous type
  // said `EvidencePackage[]` where EvidencePackage had `summary` and
  // `updatedAt` fields — but the route's GET handler doesn't select those
  // columns. The renamed EvidencePackageSummary type matches the actual
  // route response exactly.
  listEvidencePackages: () =>
    request<{ items: EvidencePackageSummary[] }>("/api/evidence-package"),
  // FE-011 ROOT FIX: buildEvidencePackage returns the BUILT EvidencePackage
  // (with literature, clinicalTrials, safety, serviceStatus, notes) — NOT
  // the DB row summary. The `package` field is the canonical
  // EvidencePackage type from lib/services/evidence-package. The Zod
  // schema validates the full response shape (id + package + markdown)
  // so any drift between the route and the contract is caught at the
  // fetch boundary.
  buildEvidencePackage: (body: { drug: string; disease: string; notes?: string; literatureLimit?: number; trialsLimit?: number }) =>
    request<EvidencePackageBuildResponse>("/api/evidence-package", {
      method: "POST",
      body: JSON.stringify(body),
      schema: EvidencePackageBuildResponseSchema as unknown as ZodType<EvidencePackageBuildResponse>,
    }),
  // FE-011 ROOT FIX: getEvidencePackage returns the same shape as
  // buildEvidencePackage — the route's GET handler reads the DB row,
  // parses payloadJson (which IS the built EvidencePackage), and returns
  // `{id, package, markdown}`. The Zod schema is the same.
  getEvidencePackage: (id: string) =>
    request<EvidencePackageBuildResponse>(`/api/evidence-package?id=${encodeURIComponent(id)}`, {
      schema: EvidencePackageBuildResponseSchema as unknown as ZodType<EvidencePackageBuildResponse>,
    }),

  // ML — Phase 4 RL ranker, Phase 1 dataset, Phase 2 knowledge graph
  // ROOT FIX for FE-001/FE-002/FE-003: the UI now calls these real endpoints
  // instead of rendering mock data. The endpoints serve real data from the
  // Phase 1/2/4 Python pipeline artifacts.
  //
  // FE-003 ROOT FIX (Team Member 15, v108): re-added getDatasetStats().
  // The previous FE-025 "ROOT FIX" removed this method as "dead code" —
  // but that decision was itself a bug, because DataSourcesScreen NEEDS
  // this method to replace its hardcoded fake source list (FE-003).
  // Removing the method made it impossible to wire DataSourcesScreen to
  // real data without re-adding the method. The /api/dataset endpoint
  // exists and returns real source stats (loaded/rowsLoaded/sha256)
  // from the Phase 1 dataset-stats service. This method is now CALLED
  // by DataSourcesScreen — it is no longer dead code.
  //
  // FE-010 ROOT FIX (Teammate 14, HIGH): wire the DatasetStatsResponseSchema
  // into the request<T>() call so FE-066 runtime validation fires. If the
  // /api/dataset route's response shape drifts from the Zod schema in
  // ml-contracts.ts, the caller sees a structured ApiError at the fetch
  // boundary instead of a cryptic render error 10 layers deep. The schema
  // is the SAME one the route itself uses to validate its outgoing
  // response — so a successful validation here means the contract held
  // end-to-end (route → wire → client).
  getDatasetStats: () =>
    request<DatasetStatsResponse>("/api/dataset", {
      schema: DatasetStatsResponseSchema as unknown as ZodType<DatasetStatsResponse>,
    }),

  // Issue 306 (audit 301-320): Graph Stats screen must call
  // /api/knowledge-graph/stats. The endpoint path /api/knowledge-graph
  // already returns the stats payload (route at
  // src/app/api/knowledge-graph/route.ts). For backward compat we keep
  // the original method name; new code should prefer getKnowledgeGraphStats.
  //
  // FE-010 ROOT FIX (Teammate 14, HIGH): wire the KgStatsResponseSchema
  // into the request<T>() call so FE-066 runtime validation fires on
  // every KG stats fetch. The schema is the canonical one from
  // ml-contracts.ts — drift between the Python Phase 2 service and the
  // frontend is caught at the fetch boundary.
  getKnowledgeGraphStats: () =>
    request<KgStatsResponse>("/api/knowledge-graph", {
      schema: KgStatsResponseSchema as unknown as ZodType<KgStatsResponse>,
    }),

  // Issue 307 (audit 301-320): Quality screen calls /api/dataset/quality.
  // Returns REAL quality metrics derived from Phase 1 + Phase 2 stats
  // (completeness, integrity, freshness, canonical coverage). No
  // fabricated percentages.
  getDatasetQuality: () =>
    request<DatasetQualityResponse>("/api/dataset/quality"),

  // Issue 315 (audit 301-320): Investor Dashboard calls /api/admin/metrics.
  // Returns REAL platform metrics (user/org/project/hypothesis counts,
  // audit-log activity, dataset + KG scale). Financial metrics
  // (ARR/MRR/NRR) are explicitly null — NOT fabricated.
  getAdminMetrics: () => request<AdminMetricsResponse>("/api/admin/metrics"),
};

// ---------------------------------------------------------------------------
// Issue 318 (audit 301-320): Type contract for /api/dataset/quality and
// /api/admin/metrics. Aligned with the actual route response shapes —
// no descriptorUI/descriptorUi, price/priceCents, drug/brandName style
// mismatches. Each field name matches exactly what the route returns.
// ---------------------------------------------------------------------------

export interface DatasetQualityResponse {
  status: "ok" | "no_data" | "service_down";
  generatedAt: string;
  source: string;
  // Real coverage metrics — percentages computed from actual loaded/total
  sourceCompletenessPct: number;
  canonicalCoveragePct: number;
  checksumCoveragePct: number;
  // Real graph-anomaly signal
  nodeEdgeRatio: number;
  nodesLoaded: number;
  edgesLoaded: number;
  // Real per-canonical-type breakdown
  canonicalNodeCoverage: Array<{
    type: string;
    present: boolean;
    count: number;
  }>;
  // Real integrity signals
  sourcesWithChecksum: number;
  totalSources: number;
  // Real freshness signal
  freshnessHoursAgo: number | null;
  isStale: boolean;
  checkpointGeneratedAt: string | null;
  // Real issue counts
  warningsCount: number;
  errorsCount: number;
  warnings: string[];
  errors: string[];
  // Pipeline version metadata (for audit trail)
  pipelineVersion: string | null;
  schemaVersion: string | null;
  bridgeVersion: string | null;
  note?: string;
}

export interface AdminMetricsResponse {
  scope: "system" | "organization";
  organizationId: string | null;
  generatedAt: string;
  // REAL user/org/subscription counts
  totalUsers: number;
  totalOrganizations: number;
  activeSubscriptions: number;
  // REAL research activity counts
  totalProjects: number;
  totalHypotheses: number;
  totalValidatedHypotheses: number;
  totalEvidencePackages: number;
  // REAL platform activity (last 30 days)
  auditLogEventsLast30Days: number;
  topActionsLast30Days: Array<{ action: string; count: number }>;
  dailyActiveUsersLast7Days: Array<{ day: string; activeUsers: number }>;
  // REAL Phase 1 + Phase 2 data scale
  dataset: {
    nodesLoaded: number;
    edgesLoaded: number;
    sourcesLoaded: number;
    sourcesTotal: number;
    source: string;
    status: string;
  };
  knowledgeGraph: {
    nodeCount: number;
    edgeCount: number;
    source: string;
  } | null;
  // EXPLICITLY NOT FABRICATED — financial metrics are null, not invented
  financials: {
    arr: null;
    mrr: null;
    customerCount: null;
    nrr: null;
    note: string;
  };
}
