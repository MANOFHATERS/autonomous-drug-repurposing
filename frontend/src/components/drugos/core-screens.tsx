'use client';

/**
 * FE-023 ROOT FIX (Teammate 17): core-screens.tsx is now a thin barrel.
 *
 * Previously this file was 3646 lines containing 23 screen components, 4
 * chart helpers, shared utilities, and the lazy `dynamic()` wrappers for
 * the 37 "remaining" screens. The monolithic file caused slow HMR, slow
 * type-checking, bundle bloat, and merge conflicts.
 *
 * ROOT FIX: each screen now lives in its own file under `screens/`. This
 * file re-exports every core screen as a named export AND maintains the
 * `coreScreens` map (the routing key → component lookup used by
 * `AppSectionRenderer`).
 *
 * The 23 core research screens live under `screens/` (flat — no
 * subdirectory) because they form a cohesive "researcher workspace" UI.
 * The 37 "remaining" admin/account/legal/support/investor/misc screens
 * live under `screens/<category>/` and are lazy-loaded via `next/dynamic`
 * so they're code-split into separate chunks. Each `dynamic()` call below
 * points at the new per-screen file (NOT at `remaining-screens.tsx`), so
 * Next.js emits ONE chunk per screen — the smallest possible initial JS
 * payload.
 *
 * No behavior changes. Every screen's source code is preserved verbatim
 * in its new file.
 */

import type { ComponentType } from 'react';
import dynamic from 'next/dynamic';

// Core research screens (eager — they're in the researcher's primary workflow)
import { DiseaseSearchScreen } from './screens/disease-search';
import { SearchResultsScreen } from './screens/search-results';
import { CandidateDetailScreen } from './screens/candidate-detail';
import { KnowledgeGraphScreen } from './screens/knowledge-graph';
import { ClinicalTrialsScreen } from './screens/clinical-trials';
import { SafetyProfileScreen } from './screens/safety-profile';
import { IPPatentsScreen } from './screens/ip-patents';
import { EvidenceBuilderScreen } from './screens/evidence-builder';
import { ReportGenerationScreen } from './screens/report-generation';
import { AdvancedSearchScreen } from './screens/advanced-search';
import { SavedQueriesScreen } from './screens/saved-queries';
import { DrugComparisonScreen } from './screens/drug-comparison';
import { DrugInteractionScreen } from './screens/drug-interaction';
import { MolecularSimilarityScreen } from './screens/molecular-similarity';
import { ScoreBreakdownScreen } from './screens/score-breakdown';
import { DiseaseDetailScreen } from './screens/disease-detail';
import { ShortlistsScreen } from './screens/shortlists';
import { QueryHistoryScreen } from './screens/query-history';
import { BatchQueryScreen } from './screens/batch-query';
import { PredictionExplorerScreen } from './screens/prediction-explorer';
import { EvidenceTimelineScreen } from './screens/evidence-timeline';
import { MechanismOfActionScreen } from './screens/mechanism-of-action';
import { RegulatoryPathwayScreen } from './screens/regulatory-pathway';

// Chart helpers (shared by candidate-detail, safety-profile, ip-patents, mechanism)
export { PathwayDiagram, ADMETRadarChart, PhaseDistributionChart, PatentTimeline } from './screens/charts';

// ScreenSkeleton (used by CoreScreenSkeleton in screens/app/core-screen-skeleton.tsx)
export { ScreenSkeleton } from './screens/screen-skeleton';

// Re-export every core screen as a named export for callers that want a
// specific screen by name.
export {
  DiseaseSearchScreen,
  SearchResultsScreen,
  CandidateDetailScreen,
  KnowledgeGraphScreen,
  ClinicalTrialsScreen,
  SafetyProfileScreen,
  IPPatentsScreen,
  EvidenceBuilderScreen,
  ReportGenerationScreen,
  AdvancedSearchScreen,
  SavedQueriesScreen,
  DrugComparisonScreen,
  DrugInteractionScreen,
  MolecularSimilarityScreen,
  ScoreBreakdownScreen,
  DiseaseDetailScreen,
  ShortlistsScreen,
  QueryHistoryScreen,
  BatchQueryScreen,
  PredictionExplorerScreen,
  EvidenceTimelineScreen,
  MechanismOfActionScreen,
  RegulatoryPathwayScreen,
};

// ───────────────────────────────────────────────────────────────────────────
// FE-023 ROOT FIX (continued): Lazy `dynamic()` wrappers for the 37 remaining
// screens. Each wrapper points at the new per-screen file so Next.js emits
// ONE chunk per screen — the smallest possible initial JS payload. The
// previous code pointed at `./remaining-screens` (the 3864-line monolith),
// which produced ONE shared chunk for all 37 screens. Now each screen is
// its own chunk loaded on-demand only when the user navigates to it.
//
// `ssr: false` is intentionally omitted — these screens are safe to render
// on the server (they call client-side hooks inside `useEffect`, not during
// render). Keeping SSR enabled improves first-paint for users whose first
// navigation is to one of these screens.
// ───────────────────────────────────────────────────────────────────────────

const PipelineScreenLazy: ComponentType = dynamic(() => import('./screens/admin/pipeline').then(m => m.PipelineScreen));
const AnalyticsScreenLazy: ComponentType = dynamic(() => import('./screens/admin/analytics').then(m => m.AnalyticsScreen));
const TeamMembersScreenLazy: ComponentType = dynamic(() => import('./screens/admin/team-members').then(m => m.TeamMembersScreen));
const ProjectsScreenLazy: ComponentType = dynamic(() => import('./screens/admin/projects').then(m => m.ProjectsScreen));
const SharedQueriesScreenLazy: ComponentType = dynamic(() => import('./screens/admin/shared-queries').then(m => m.SharedQueriesScreen));
const AnnotationsScreenLazy: ComponentType = dynamic(() => import('./screens/admin/annotations').then(m => m.AnnotationsScreen));
const DataSourcesScreenLazy: ComponentType = dynamic(() => import('./screens/admin/data-sources').then(m => m.DataSourcesScreen));
const GraphStatisticsScreenLazy: ComponentType = dynamic(() => import('./screens/admin/graph-statistics').then(m => m.GraphStatisticsScreen));
const QualityScreenLazy: ComponentType = dynamic(() => import('./screens/admin/quality').then(m => m.QualityScreen));
const SubscriptionScreenLazy: ComponentType = dynamic(() => import('./screens/account/subscription').then(m => m.SubscriptionScreen));
const UsageScreenLazy: ComponentType = dynamic(() => import('./screens/account/usage').then(m => m.UsageScreen));
const DealsScreenLazy: ComponentType = dynamic(() => import('./screens/admin/deals').then(m => m.DealsScreen));
const InvoicesScreenLazy: ComponentType = dynamic(() => import('./screens/admin/invoices').then(m => m.InvoicesScreen));
const UsersAdminScreenLazy: ComponentType = dynamic(() => import('./screens/admin/users-admin').then(m => m.UsersAdminScreen));
const RolesScreenLazy: ComponentType = dynamic(() => import('./screens/admin/roles').then(m => m.RolesScreen));
const SSOScreenLazy: ComponentType = dynamic(() => import('./screens/admin/sso').then(m => m.SSOScreen));
const AuditLogsScreenLazy: ComponentType = dynamic(() => import('./screens/admin/audit-logs').then(m => m.AuditLogsScreen));
const FeatureFlagsScreenLazy: ComponentType = dynamic(() => import('./screens/admin/feature-flags').then(m => m.FeatureFlagsScreen));
const APIDocsScreenLazy: ComponentType = dynamic(() => import('./screens/admin/api-docs').then(m => m.APIDocsScreen));
const APIKeysScreenLazy: ComponentType = dynamic(() => import('./screens/admin/api-keys').then(m => m.APIKeysScreen));
const PlaygroundScreenLazy: ComponentType = dynamic(() => import('./screens/admin/playground').then(m => m.PlaygroundScreen));
const WebhooksScreenLazy: ComponentType = dynamic(() => import('./screens/admin/webhooks').then(m => m.WebhooksScreen));
const ProfileScreenLazy: ComponentType = dynamic(() => import('./screens/account/profile').then(m => m.ProfileScreen));
const SecuritySettingsScreenLazy: ComponentType = dynamic(() => import('./screens/account/security-settings').then(m => m.SecuritySettingsScreen));
const NotificationsScreenLazy: ComponentType = dynamic(() => import('./screens/account/notifications').then(m => m.NotificationsScreen));
const PreferencesScreenLazy: ComponentType = dynamic(() => import('./screens/account/preferences').then(m => m.PreferencesScreen));
const PrivacyPolicyScreenLazy: ComponentType = dynamic(() => import('./screens/legal/privacy-policy').then(m => m.PrivacyPolicyScreen));
const TermsScreenLazy: ComponentType = dynamic(() => import('./screens/legal/terms').then(m => m.TermsScreen));
const ComplianceScreenLazy: ComponentType = dynamic(() => import('./screens/legal/compliance').then(m => m.ComplianceScreen));
const HelpCenterScreenLazy: ComponentType = dynamic(() => import('./screens/support/help-center').then(m => m.HelpCenterScreen));
const TicketScreenLazy: ComponentType = dynamic(() => import('./screens/support/ticket').then(m => m.TicketScreen));
const SystemStatusScreenLazy: ComponentType = dynamic(() => import('./screens/support/system-status').then(m => m.SystemStatusScreen));
const InvestorDashboardScreenLazy: ComponentType = dynamic(() => import('./screens/investor/investor-dashboard').then(m => m.InvestorDashboardScreen));
const CapTableScreenLazy: ComponentType = dynamic(() => import('./screens/investor/cap-table').then(m => m.CapTableScreen));
const ChangelogScreenLazy: ComponentType = dynamic(() => import('./screens/misc/changelog').then(m => m.ChangelogScreen));
const RoadmapScreenLazy: ComponentType = dynamic(() => import('./screens/misc/roadmap').then(m => m.RoadmapScreen));
const FeedbackScreenLazy: ComponentType = dynamic(() => import('./screens/misc/feedback').then(m => m.FeedbackScreen));

/**
 * The routing key → component map for the "core" researcher screens.
 * Eager-imported (NOT lazy) because they're in the researcher's primary
 * workflow — the dashboard, search, results, candidate detail, KG, etc.
 * These screens should load instantly when a researcher logs in.
 *
 * The 37 "remaining" admin/account/legal/etc screens are lazy-loaded
 * below — they're rarely visited and shouldn't bloat the initial JS.
 */
export const coreScreens: Record<string, ComponentType> = {
  'search': DiseaseSearchScreen,
  'results': SearchResultsScreen,
  'candidate': CandidateDetailScreen,
  'knowledge-graph': KnowledgeGraphScreen,
  'clinical-trials': ClinicalTrialsScreen,
  'safety': SafetyProfileScreen,
  'ip-patents': IPPatentsScreen,
  'evidence-builder': EvidenceBuilderScreen,
  'reports': ReportGenerationScreen,
  'advanced-search': AdvancedSearchScreen,
  'saved-queries': SavedQueriesScreen,
  'comparison': DrugComparisonScreen,
  'interactions': DrugInteractionScreen,
  'molecular-similarity': MolecularSimilarityScreen,
  'score-breakdown': ScoreBreakdownScreen,
  'disease-detail': DiseaseDetailScreen,
  'shortlists': ShortlistsScreen,
  'history': QueryHistoryScreen,
  'batch-query': BatchQueryScreen,
  'prediction-explorer': PredictionExplorerScreen,
  'evidence-timeline': EvidenceTimelineScreen,
  'mechanism': MechanismOfActionScreen,
  'regulatory': RegulatoryPathwayScreen,
  // 37 remaining screens — lazy-loaded via next/dynamic for code splitting.
  // Each screen is its own JS chunk, loaded on-demand only when the user
  // navigates to it. See the dynamic() declarations above.
  'pipeline': PipelineScreenLazy,
  'analytics': AnalyticsScreenLazy,
  'team': TeamMembersScreenLazy,
  'projects': ProjectsScreenLazy,
  'shared-queries': SharedQueriesScreenLazy,
  'annotations': AnnotationsScreenLazy,
  'data-sources': DataSourcesScreenLazy,
  'graph-stats': GraphStatisticsScreenLazy,
  'quality': QualityScreenLazy,
  'subscription': SubscriptionScreenLazy,
  'usage': UsageScreenLazy,
  'deals': DealsScreenLazy,
  'invoices': InvoicesScreenLazy,
  'users': UsersAdminScreenLazy,
  'roles': RolesScreenLazy,
  'sso': SSOScreenLazy,
  'audit-logs': AuditLogsScreenLazy,
  'feature-flags': FeatureFlagsScreenLazy,
  'api-docs': APIDocsScreenLazy,
  'api-keys': APIKeysScreenLazy,
  'playground': PlaygroundScreenLazy,
  'webhooks': WebhooksScreenLazy,
  'profile': ProfileScreenLazy,
  'security': SecuritySettingsScreenLazy,
  'notifications': NotificationsScreenLazy,
  'preferences': PreferencesScreenLazy,
  'privacy': PrivacyPolicyScreenLazy,
  'terms': TermsScreenLazy,
  'compliance': ComplianceScreenLazy,
  'help-center': HelpCenterScreenLazy,
  'tickets': TicketScreenLazy,
  'system-status': SystemStatusScreenLazy,
  'investor-dashboard': InvestorDashboardScreenLazy,
  'cap-table': CapTableScreenLazy,
  'changelog': ChangelogScreenLazy,
  'roadmap': RoadmapScreenLazy,
  'feedback': FeedbackScreenLazy,
};
