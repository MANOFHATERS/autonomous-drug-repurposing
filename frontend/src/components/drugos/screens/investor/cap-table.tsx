'use client';

import { EmptyState } from '../../use-api-data';
import { DemoDataBanner } from '@/components/ui/DemoDataBanner';
import { FadeIn, PageHeader } from '../_remaining-shared';

// ═══════════════════════════════════════════
// 34. CAP TABLE SCREEN
// ═══════════════════════════════════════════
/**
 * FE-013 ROOT FIX (Team Member 15, v108): The previous CapTableScreen
 * rendered 3 fabricated funding rounds ("Pre-Seed $500K $3M valuation",
 * "Seed $2M $10M valuation", "Series A $8M $40M valuation") and 5
 * fabricated shareholders. An investor saw "$40M valuation" —
 * fabricated. Investment decisions were made on fake cap table data.
 *
 * ROOT FIX: Per the issue spec, remove both screens entirely. Cap
 * table data must come from a real cap table management system
 * (Carta, Pulley, Capbase), not hardcoded arrays. We render an
 * honest EmptyState.
 */
export function CapTableScreen() {
  // Issue 316 (audit 301-320): No /api/admin/cap-table endpoint exists.
  // Cap table data (shareholders, share classes, funding rounds,
  // valuations) must come from a real cap table management system like
  // Carta, Pulley, or Capbase. The DemoDataBanner makes it 100% visible
  // that this screen is non-functional — anyone (especially investors)
  // seeing this screen immediately knows the data is not real.
  return (
    <FadeIn>
      <div className="space-y-6">
        <PageHeader title="Cap Table" desc="Capitalization table and funding history" />
        <DemoDataBanner
          reason="Cap table data is not available. There is no /api/admin/cap-table endpoint. Cap table data (shareholders, share classes, funding rounds, valuations) must come from a real cap table management system like Carta, Pulley, or Capbase. Any cap table data shown here would be fabricated — showing fabricated cap table data to investors is securities fraud."
        />
        <EmptyState
          title="Cap table not available"
          description="Cap table data (shareholders, share classes, funding rounds, valuations) must come from a real cap table management system like Carta, Pulley, or Capbase — not a hardcoded array. Showing fabricated cap table data to investors is securities fraud. Integrate this screen with your cap table platform before exposing it."
        />
      </div>
    </FadeIn>
  );
}
