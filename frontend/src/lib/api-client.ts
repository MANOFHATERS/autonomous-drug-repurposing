/**
 * Frontend API client for DruGOS.
 *
 * Wraps `fetch` with credentials included, JSON parsing, error normalization,
 * and small typed helpers for every backend endpoint the UI needs.
 *
 * All cookies are HttpOnly so the browser sends them automatically — we never
 * touch tokens from JavaScript.
 */

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

export interface Plan {
  id: string;
  name: string;
  price: number;
  priceCents?: number; // FE-027 unblock: some components read priceCents
  currency: string;
  interval: string;
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

export interface DiseaseSearchResult {
  descriptorUI: string;
  descriptorName: string;
  scopeNote?: string;
  synonyms?: string[];
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
  drug: string;
  totalReports: number;
  seriousReports: number;
  seriousReportsWithDeath?: number;
  topReactions: { term: string; count: number }[];
  disclaimer: string;
}

// ---------------------------------------------------------------------------
// ML / Phase 4 handoff types — ROOT FIX for FE-001/FE-002/FE-003
// ---------------------------------------------------------------------------

export interface RankedHypothesis {
  drug: string;
  disease: string;
  rank?: number;
  reward?: number;
  policyProb?: number;
  gnnScore?: number;
  safetyScore?: number;
  literatureSupport?: number;
}

export interface RlRankerResponse {
  candidates: RankedHypothesis[];
  source: "rl_service" | "local_csv" | "none";
  modelVersion?: string;
  generatedAt: string;
  count: number;
  note?: string;
  syncedHypotheses?: number;
}

export interface DatasetSourceStat {
  name: string;
  loaded: boolean;
  rowsLoaded?: number;
  sha256?: string;
}

export interface DatasetStatsResponse {
  sources: DatasetSourceStat[];
  nodesLoaded: number;
  edgesLoaded: number;
  edgeTypesPresent: string[];
  pipelineVersion?: string;
  schemaVersion?: string;
  bridgeVersion?: string;
  backend?: string;
  warnings: string[];
  errors: string[];
  source: "dataset_service" | "local_checkpoint" | "none";
  generatedAt: string;
  note?: string;
}

export interface GraphSourceStat {
  name: string;
  loaded: boolean;
  loadedReason?: string;
  version?: string;
  rows?: number;
  edgeCount?: number;
  sha256?: string;
  producedAt?: string;
  producedBy?: string;
  loadId?: string;
}

export interface KnowledgeGraphStatsResponse {
  sources: GraphSourceStat[];
  nodeCount: number;
  edgeCount: number;
  source: "kg_service" | "local_registry" | "none";
  generatedAt: string;
  note?: string;
}

export interface EvidencePackage {
  id: string;
  drugName: string;
  diseaseName: string;
  title: string;
  summary: string;
  status: string;
  createdAt: string;
  updatedAt: string;
}

// ---------------------------------------------------------------------------
// Core fetch wrapper
// ---------------------------------------------------------------------------

async function request<T>(
  url: string,
  init?: RequestInit & { skipAuthRedirect?: boolean }
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
      window.dispatchEvent(new CustomEvent("drugos:unauthorized"));
    }
    throw err;
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

  me: () => request<AuthMeResponse>("/api/auth/me", { skipAuthRedirect: true }),
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
  addComment: (projectId: string, body: { authorName: string; body: string }) =>
    request<Comment>(`/api/projects/${projectId}/comments`, { method: "POST", body: JSON.stringify(body) }),

  // BILLING
  listPlans: () => request<{ plans: Plan[] }>("/api/billing/plans"),
  getSubscription: () => request<{ subscription: Subscription | null; plans: Plan[] }>("/api/billing/subscription"),
  changePlan: (planId: string) =>
    request<{ ok: true }>("/api/billing/subscription", { method: "POST", body: JSON.stringify({ planId }) }),
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
  searchClinicalTrials: (q: string) =>
    request<{ items: ClinicalTrial[] }>(`/api/clinical-trials/search?q=${encodeURIComponent(q)}`),
  searchLiterature: (q: string) =>
    request<{ items: PubMedArticle[] }>(`/api/literature/search?q=${encodeURIComponent(q)}`),
  getSafety: (drug: string) =>
    request<SafetyReport>(`/api/safety/${encodeURIComponent(drug)}`),
  searchPatents: (q: string) =>
    request<{ items: any[] }>(`/api/patents/search?q=${encodeURIComponent(q)}`),

  // EVIDENCE PACKAGES
  listEvidencePackages: () => request<{ items: EvidencePackage[] }>("/api/evidence-package"),
  buildEvidencePackage: (body: { drug: string; disease: string; notes?: string; literatureLimit?: number; trialsLimit?: number }) =>
    request<{ id: string; package: any; markdown: string }>("/api/evidence-package", { method: "POST", body: JSON.stringify(body) }),
  getEvidencePackage: (id: string) =>
    request<{ id: string; package: any; markdown: string }>(`/api/evidence-package?id=${encodeURIComponent(id)}`),

  // ML — Phase 4 RL ranker, Phase 1 dataset, Phase 2 knowledge graph
  // ROOT FIX for FE-001/FE-002/FE-003: the UI now calls these real endpoints
  // instead of rendering mock data. The endpoints serve real data from the
  // Phase 1/2/4 Python pipeline artifacts.
  getRankedHypotheses: (params?: { drug?: string; disease?: string; limit?: number }) => {
    const qs = new URLSearchParams();
    if (params?.drug) qs.set("drug", params.drug);
    if (params?.disease) qs.set("disease", params.disease);
    if (params?.limit) qs.set("limit", String(params.limit));
    return request<RlRankerResponse>(`/api/rl?${qs.toString()}`);
  },
  syncRlOutput: () =>
    request<RlRankerResponse>("/api/rl", { method: "POST", body: JSON.stringify({ sync: true, limit: 200 }) }),
  getDatasetStats: () => request<DatasetStatsResponse>("/api/dataset"),
  getKnowledgeGraphStats: () => request<KnowledgeGraphStatsResponse>("/api/knowledge-graph"),
};
