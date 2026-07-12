/**
 * @deprecated This file is a STUB. FE-026 ROOT FIX.
 *
 * Previously this file contained 621 lines of fabricated data: 10 fake
 * diseases, 13 fake drug candidates, 6 fake clinical trials, fake graph
 * nodes/edges, 8 fake users, 5 fake notifications, 6 fake audit logs,
 * 5 fake billing invoices, 3 fake API keys, 3 fake webhooks, fake usage
 * metrics, 10 fake data sources, fake trending diseases, fake recent
 * queries, 4 fake projects, fake deal pipeline, fake organization, fake
 * feature flags, fake system status, fake saved queries, fake blog posts,
 * fake careers, fake patents, fake evidence items, fake ADMET profiles,
 * fake off-target predictions, fake drug interactions, fake knowledge
 * graph nodes/edges, fake pathway data, and more.
 *
 * 11 components imported from this file. Most of the UI was rendering
 * fabricated data — a pharma researcher seeing "Memantine 87 for
 * Huntington's" had no way to know the candidate was invented.
 *
 * ROOT FIX:
 *   - All TYPE definitions have been moved to `@/lib/types`. Import
 *     types from there: `import type { DrugCandidate } from '@/lib/types'`.
 *   - All DATA exports below are EMPTY arrays/objects. Any component
 *     that still imports them will render an empty state. This is the
 *     correct behavior — NO fabricated data is ever shown.
 *   - Components MUST be migrated to use real API calls (see
 *     `use-api-data.tsx` for hooks) or local empty state.
 *
 * DO NOT add new data to this file. If you need a type, add it to
 * `@/lib/types`. If you need data, fetch it from the API.
 */

// Re-export types for backward compatibility. New code MUST import
// directly from `@/lib/types` — these re-exports exist only so the
// 11 components that currently import from `mock-data` keep compiling
// during the migration.
export type {
  Disease,
  DrugCandidate,
  ClinicalTrial,
  GraphNode,
  GraphEdge,
  User,
  AppNotification as Notification,
  AuditLogEntry,
  Patent,
  EvidenceItem,
  ADMETProfile,
  OffTargetPrediction,
  DrugInteraction,
  SafetyTier,
  DashboardStats,
  RecentActivityItem,
  Milestone,
  MonthlyQueryTrend,
  SafetyTierDistribution,
  KnowledgeGraphNode,
  KnowledgeGraphEdge,
  PathwayNode,
  PathwayEdge,
  PathwayData,
} from '@/lib/types';

import type {
  Disease,
  DrugCandidate,
  ClinicalTrial,
  GraphNode,
  GraphEdge,
  User,
  AppNotification,
  AuditLogEntry,
  Patent,
  EvidenceItem,
  ADMETProfile,
  OffTargetPrediction,
  DrugInteraction,
  DashboardStats,
  RecentActivityItem,
  Milestone,
  MonthlyQueryTrend,
  SafetyTierDistribution,
  PathwayData,
} from '@/lib/types';

// ---------------------------------------------------------------------------
// DEPRECATED DATA EXPORTS — all empty. DO NOT use. Migrate to API hooks.
// ---------------------------------------------------------------------------

/** @deprecated Empty. Use useDiseaseSearch() in use-api-data.tsx. */
export const diseases: Disease[] = [];

/** @deprecated Empty. Use useRlCandidates() in use-api-data.tsx. */
export const drugCandidates: DrugCandidate[] = [];

/** @deprecated Empty. Use useClinicalTrialsSearch() in use-api-data.tsx. */
export const clinicalTrials: ClinicalTrial[] = [];

/** @deprecated Empty. Use useKnowledgeGraph() in use-api-data.tsx. */
export const graphNodes: GraphNode[] = [];

/** @deprecated Empty. Use useKnowledgeGraph() in use-api-data.tsx. */
export const graphEdges: GraphEdge[] = [];

/** @deprecated Empty. Use api.listTeamMembers() in api-client.ts. */
export const users: User[] = [];

/** @deprecated Empty. Use api.listNotifications() in api-client.ts. */
export const notifications: AppNotification[] = [];

/** @deprecated Empty. Use api.listAuditLogs() in api-client.ts. */
export const auditLogs: AuditLogEntry[] = [];

/** @deprecated Empty. Use api.listPlans() in api-client.ts. */
export const subscriptionPlans: never[] = [];

/** @deprecated Empty. Use api.listInvoices() in api-client.ts. */
export const billingHistory: never[] = [];

/** @deprecated Empty. Use api.listApiKeys() in api-client.ts. */
export const apiKeys: never[] = [];

/** @deprecated Empty. Webhooks are managed via /api/webhooks (not yet implemented). */
export const webhooks: never[] = [];

/** @deprecated Empty. Usage metrics are fetched from /api/usage (not yet implemented). */
export const usageMetrics: Record<string, never> = {};

/** @deprecated Empty. Use api.getDatasetStats() in api-client.ts. */
export const dataSources: never[] = [];

/** @deprecated Empty. Use api.getRankedHypotheses() for live trends. */
export const trendingDiseases: never[] = [];

/** @deprecated Empty. Persist queries via /api/projects (saved queries). */
export const recentQueries: never[] = [];

/** @deprecated Empty. Use api.listTeamMembers(). */
export const teamMembers: User[] = [];

/** @deprecated Empty. Use api.listProjects(). */
export const projects: never[] = [];

/** @deprecated Empty. Deal pipeline is managed via /api/projects (not yet implemented). */
export const dealPipeline: never[] = [];

/** @deprecated Empty. Use api.me() to fetch the user's organization. */
export const organization: Record<string, never> = {};

/** @deprecated Empty. Feature flags are managed via /api/admin/feature-flags (not yet implemented). */
export const featureFlags: never[] = [];

/** @deprecated Empty. Use api.getSystemStatus(). */
export const systemStatus: never[] = [];

/** @deprecated Empty. Saved queries are managed via /api/projects. */
export const savedQueries: never[] = [];

/** @deprecated Empty. Blog posts are managed via a CMS (not yet integrated). */
export const blogPosts: never[] = [];

/** @deprecated Empty. Careers are managed via a CMS (not yet integrated). */
export const careers: never[] = [];

/** @deprecated Empty. Use api.searchPatents(). */
export const patents: Patent[] = [];

/** @deprecated Empty. Use api.buildEvidencePackage(). */
export const evidenceItems: EvidenceItem[] = [];

/** @deprecated Empty. ADMET profiles are served by /api/admet (not yet implemented). */
export const admetProfiles: ADMETProfile[] = [];

/** @deprecated Empty. Off-target predictions are served by /api/off-targets (not yet implemented). */
export const offTargetPredictions: OffTargetPrediction[] = [];

/** @deprecated Empty. Drug interactions are served by /api/interactions (not yet implemented). */
export const drugInteractions: DrugInteraction[] = [];

/** @deprecated Empty. Use api.getDatasetStats() / api.getKnowledgeGraphStats(). */
export const dashboardStats: DashboardStats = {
  totalCandidates: 0,
  totalDrugs: 0,
  totalDiseases: 0,
  knowledgeGraphNodes: 0,
  knowledgeGraphEdges: 0,
  literatureSupported: 0,
  novelCandidates: 0,
  avgConfidence: 0,
};

/** @deprecated Empty. Recent activity is fetched from /api/projects/[id]/activities. */
export const recentActivity: RecentActivityItem[] = [];

/** @deprecated Empty. Milestones are managed via /api/projects (not yet implemented). */
export const milestones: Milestone[] = [];

/** @deprecated Empty. Monthly trends are derived from /api/audit-logs. */
export const monthlyQueryTrend: MonthlyQueryTrend[] = [];

/** @deprecated Empty. Safety tier distribution is derived from /api/rl. */
export const safetyTierDistribution: SafetyTierDistribution[] = [];

/** @deprecated Empty. Alias for graphNodes — use useKnowledgeGraph(). */
export const knowledgeGraphNodes: GraphNode[] = graphNodes;

/** @deprecated Empty. Alias for graphEdges — use useKnowledgeGraph(). */
export const knowledgeGraphEdges: GraphEdge[] = graphEdges;

/** @deprecated Empty. Pathway data is fetched from /api/knowledge-graph. */
export const pathwayData: PathwayData = { nodes: [], edges: [] };
