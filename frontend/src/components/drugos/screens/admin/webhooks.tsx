'use client';

import { EmptyState } from '../../use-api-data';
import { DemoDataBanner } from '@/components/ui/DemoDataBanner';
import { FadeIn, PageHeader } from '../_remaining-shared';

// ═══════════════════════════════════════════
// 22. WEBHOOKS SCREEN
// ═══════════════════════════════════════════
/**
 * FE-011 ROOT FIX (Team Member 15, v108): The previous WebhooksScreen
 * rendered 3 fabricated webhooks ("https://api.myapp.com/webhooks/drugos
 * 99.2% success", "https://hooks.slack.com/services/T0/B0/xxx 100%
 * success", "https://old-api.partner.com/wh 42.0% success failing").
 * The "Add Webhook" dialog had no submit handler. The WebhookEndpoint
 * Prisma model exists but no /api/webhooks route exists. No banner.
 *
 * ROOT FIX: There is no /api/webhooks CRUD route in the codebase
 * (the WebhookEndpoint Prisma model exists but is unused). Per the
 * issue spec we render an honest EmptyState. We do NOT fabricate
 * webhook URLs or success rates.
 */
export function WebhooksScreen() {
  // Issue 313 (audit 301-320): No /api/admin/webhooks endpoint exists.
  // The DemoDataBanner makes it visible that any webhook URLs, secrets,
  // or success rates shown here would be fabricated. The WebhookEndpoint
  // Prisma model was REMOVED in BE-069 (it was dead code with no CRUD
  // route and no delivery worker). Implementing webhooks requires the
  // full feature: CRUD routes, HMAC-signed delivery, retry logic, and
  // a delivery-log table.
  return (
    <FadeIn>
      <div className="space-y-6">
        <PageHeader title="Webhooks" desc="Configure webhook endpoints for event notifications" />
        <DemoDataBanner
          reason="Webhook delivery infrastructure is not implemented. There is no /api/admin/webhooks endpoint. The WebhookEndpoint Prisma model was removed in BE-069 because it was dead code with no CRUD route, no delivery worker, and no HMAC signing. Any webhook URLs or success rates shown here would be fabricated."
        />
        <EmptyState
          title="Webhooks not configured"
          description="The WebhookEndpoint Prisma model was REMOVED (BE-069) because it was dead code with no /api/webhooks CRUD route. Implementing webhooks requires: (1) POST /api/admin/webhooks to create, (2) GET /api/admin/webhooks to list, (3) DELETE /api/admin/webhooks/[id] to revoke, (4) a delivery worker that signs payloads with HMAC and retries on failure, and (5) a delivery-log table for success-rate calculation. Until these exist, no webhook URLs or success rates are shown."
        />
      </div>
    </FadeIn>
  );
}
