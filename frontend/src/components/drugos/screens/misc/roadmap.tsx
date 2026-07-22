'use client';

import { EmptyState } from '../../use-api-data';
import { FadeIn, PageHeader } from '../_remaining-shared';

// ═══════════════════════════════════════════
// 36. ROADMAP SCREEN
// ═══════════════════════════════════════════
/**
 * FE-014 ROOT FIX (Team Member 15, v108): The previous RoadmapScreen
 * rendered a fabricated product roadmap (Q2 2026 → Q1 2027) with
 * fabricated vote counts. There is no roadmap CMS in the codebase.
 *
 * ROOT FIX: Per the issue spec, replace the fabricated roadmap with
 * an honest EmptyState. The product roadmap should be backed by a
 * CMS (Contentful, Sanity, etc.) or a project-tracking tool
 * (Linear, Jira) — not a hardcoded array.
 */
export function RoadmapScreen() {
  return (
    <FadeIn>
      <div className="space-y-6">
        <PageHeader title="Product Roadmap" desc="Upcoming features and improvements" />
        <EmptyState
          title="Roadmap not available"
          description="The product roadmap is not backed by a CMS or project-tracking integration in this deployment. There is no /api/roadmap endpoint. When a CMS (Contentful, Sanity) or project tracker (Linear, Jira) integration is added, this screen will show real roadmap items with real statuses and real vote counts. No fabricated roadmap items or vote counts are rendered."
        />
      </div>
    </FadeIn>
  );
}
