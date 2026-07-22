'use client';

import { EmptyState } from '../../use-api-data';
import { DemoDataBanner } from '@/components/ui/DemoDataBanner';
import { FadeIn, PageHeader } from '../_remaining-shared';

// ═══════════════════════════════════════════
// 18. FEATURE FLAGS SCREEN
// ═══════════════════════════════════════════
/**
 * FE-010 ROOT FIX (Team Member 15, v108): The previous FeatureFlagsScreen
 * rendered 6 fabricated feature flags ("gxp_mode enabled", "batch_query
 * enabled", "graphql_api disabled", "ai_explain enabled", "cro_isolation
 * enabled", "new_kg_v2 disabled") with fabricated environment assignments.
 * The Switch components were non-functional (no onCheckedChange). No
 * API call. No banner. An admin toggling a Switch expected the flag
 * to change — nothing happened. The "gxp_mode enabled" flag was
 * particularly dangerous: GxP validated mode has regulatory
 * implications, and a fake toggle gives false confidence.
 *
 * ROOT FIX: There is no `/api/feature-flags` endpoint in the codebase.
 * Per the issue spec we render an honest EmptyState. The "gxp_mode"
 * fake toggle is GONE — GxP compliance must come from real validated
 * audit reports, not a UI Switch.
 */
export function FeatureFlagsScreen() {
  // Issue 312 (audit 301-320): No /api/admin/feature-flags endpoint exists.
  // The DemoDataBanner makes it visible that any flag toggles shown here
  // would be non-functional. The previous screen fabricated flag names
  // like 'gxp_mode' with non-functional Switch toggles — a regulatory
  // hazard because GxP validation requires formal CSV documentation.
  return (
    <FadeIn>
      <div className="space-y-6">
        <PageHeader title="Feature Flags" desc="Control feature rollouts and experiments" />
        <DemoDataBanner
          reason="Feature flag controls are not implemented. There is no /api/admin/feature-flags endpoint. Any flag toggles shown here would be non-functional UI mockups. The previous screen fabricated a 'gxp_mode' toggle — GxP compliance requires formal CSV (Computer System Validation) documentation, not a UI Switch."
        />
        <EmptyState
          title="Feature flags not configured"
          description="There is no /api/admin/feature-flags endpoint in the codebase. Feature flags must be backed by a real configuration store (database, LaunchDarkly, Unleash, etc.) with proper authorization and audit logging — not a hardcoded array of fake flag names with non-functional Switch toggles. Implement the backend before exposing flag controls to admins."
        />
      </div>
    </FadeIn>
  );
}
