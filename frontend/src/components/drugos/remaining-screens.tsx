'use client';

/**
 * FE-023 ROOT FIX (Teammate 17): remaining-screens.tsx is now a thin barrel.
 *
 * Previously this file was 3864 lines containing 37 screen components. The
 * monolithic file caused:
 *   - Slow Hot Module Replacement (every edit re-compiled all 37 screens).
 *   - Slow TypeScript type-checking (every edit triggered a 3864-line recheck).
 *   - Bundle bloat (all 37 screens were bundled into one chunk).
 *   - Cognitive load (finding the right screen required scrolling 3000+ lines).
 *   - Merge conflicts (multiple developers working on different screens
 *     conflicted on the same file).
 *
 * ROOT FIX: each screen now lives in its own file under `screens/<category>/`.
 * This file re-exports every screen as a named export AND maintains the
 * `remainingScreens` map (the routing key → component lookup used by
 * `core-screens.tsx`'s `coreScreens` map).
 *
 * The 37 screens are organized by domain:
 *   - screens/admin/       — 20 admin/collab screens (pipeline, analytics, etc.)
 *   - screens/account/     — 6 account screens (profile, security, etc.)
 *   - screens/legal/       — 3 legal screens (privacy, terms, compliance)
 *   - screens/support/     — 3 support screens (help-center, tickets, status)
 *   - screens/investor/    — 2 investor screens (investor-dashboard, cap-table)
 *   - screens/misc/        — 3 misc screens (changelog, roadmap, feedback)
 *
 * No behavior changes. Every screen's source code is preserved verbatim in
 * its new file. The barrel pattern is the production-grade way to split a
 * monolithic file without breaking import paths — existing callers that
 * `import { remainingScreens } from './remaining-screens'` continue to work.
 */

import type { ComponentType } from 'react';

// Admin screens
import { PipelineScreen } from './screens/admin/pipeline';
import { AnalyticsScreen } from './screens/admin/analytics';
import { TeamMembersScreen } from './screens/admin/team-members';
import { ProjectsScreen } from './screens/admin/projects';
import { SharedQueriesScreen } from './screens/admin/shared-queries';
import { AnnotationsScreen } from './screens/admin/annotations';
import { DataSourcesScreen } from './screens/admin/data-sources';
import { GraphStatisticsScreen } from './screens/admin/graph-statistics';
import { QualityScreen } from './screens/admin/quality';
import { DealsScreen } from './screens/admin/deals';
import { InvoicesScreen } from './screens/admin/invoices';
import { UsersAdminScreen } from './screens/admin/users-admin';
import { RolesScreen } from './screens/admin/roles';
import { SSOScreen } from './screens/admin/sso';
import { AuditLogsScreen } from './screens/admin/audit-logs';
import { FeatureFlagsScreen } from './screens/admin/feature-flags';
import { APIDocsScreen } from './screens/admin/api-docs';
import { APIKeysScreen } from './screens/admin/api-keys';
import { PlaygroundScreen } from './screens/admin/playground';
import { WebhooksScreen } from './screens/admin/webhooks';

// Account screens
import { SubscriptionScreen } from './screens/account/subscription';
import { UsageScreen } from './screens/account/usage';
import { ProfileScreen } from './screens/account/profile';
import { SecuritySettingsScreen } from './screens/account/security-settings';
import { NotificationsScreen } from './screens/account/notifications';
import { PreferencesScreen } from './screens/account/preferences';

// Legal screens
import { PrivacyPolicyScreen } from './screens/legal/privacy-policy';
import { TermsScreen } from './screens/legal/terms';
import { ComplianceScreen } from './screens/legal/compliance';

// Support screens
import { HelpCenterScreen } from './screens/support/help-center';
import { TicketScreen } from './screens/support/ticket';
import { SystemStatusScreen } from './screens/support/system-status';

// Investor screens
import { InvestorDashboardScreen } from './screens/investor/investor-dashboard';
import { CapTableScreen } from './screens/investor/cap-table';

// Misc screens
import { ChangelogScreen } from './screens/misc/changelog';
import { RoadmapScreen } from './screens/misc/roadmap';
import { FeedbackScreen } from './screens/misc/feedback';

// Re-export every screen as a named export (for callers that want a specific
// screen by name, e.g. `import { PipelineScreen } from './remaining-screens'`).
export {
  PipelineScreen,
  AnalyticsScreen,
  TeamMembersScreen,
  ProjectsScreen,
  SharedQueriesScreen,
  AnnotationsScreen,
  DataSourcesScreen,
  GraphStatisticsScreen,
  QualityScreen,
  SubscriptionScreen,
  UsageScreen,
  DealsScreen,
  InvoicesScreen,
  UsersAdminScreen,
  RolesScreen,
  SSOScreen,
  AuditLogsScreen,
  FeatureFlagsScreen,
  APIDocsScreen,
  APIKeysScreen,
  PlaygroundScreen,
  WebhooksScreen,
  ProfileScreen,
  SecuritySettingsScreen,
  NotificationsScreen,
  PreferencesScreen,
  PrivacyPolicyScreen,
  TermsScreen,
  ComplianceScreen,
  HelpCenterScreen,
  TicketScreen,
  SystemStatusScreen,
  InvestorDashboardScreen,
  CapTableScreen,
  ChangelogScreen,
  RoadmapScreen,
  FeedbackScreen,
};

/**
 * The routing key → component map. Consumed by `coreScreens` in
 * `core-screens.tsx` (via the lazy `dynamic()` wrappers) and by
 * `AppSectionRenderer` in `screens/app/app-section-renderer.tsx`.
 *
 * Keys are stable — they are referenced by URL paths, sidebar nav items,
 * and audit log resource strings. Renaming a key breaks every saved URL
 * and every audit log entry that references it.
 */
export const remainingScreens: Record<string, ComponentType> = {
  'pipeline': PipelineScreen,
  'analytics': AnalyticsScreen,
  'team': TeamMembersScreen,
  'projects': ProjectsScreen,
  'shared-queries': SharedQueriesScreen,
  'annotations': AnnotationsScreen,
  'data-sources': DataSourcesScreen,
  'graph-stats': GraphStatisticsScreen,
  'quality': QualityScreen,
  'subscription': SubscriptionScreen,
  'usage': UsageScreen,
  'deals': DealsScreen,
  'invoices': InvoicesScreen,
  'users': UsersAdminScreen,
  'roles': RolesScreen,
  'sso': SSOScreen,
  'audit-logs': AuditLogsScreen,
  'feature-flags': FeatureFlagsScreen,
  'api-docs': APIDocsScreen,
  'api-keys': APIKeysScreen,
  'playground': PlaygroundScreen,
  'webhooks': WebhooksScreen,
  'profile': ProfileScreen,
  'security': SecuritySettingsScreen,
  'notifications': NotificationsScreen,
  'preferences': PreferencesScreen,
  'privacy': PrivacyPolicyScreen,
  'terms': TermsScreen,
  'compliance': ComplianceScreen,
  'help-center': HelpCenterScreen,
  'tickets': TicketScreen,
  'system-status': SystemStatusScreen,
  'investor-dashboard': InvestorDashboardScreen,
  'cap-table': CapTableScreen,
  'changelog': ChangelogScreen,
  'roadmap': RoadmapScreen,
  'feedback': FeedbackScreen,
};
