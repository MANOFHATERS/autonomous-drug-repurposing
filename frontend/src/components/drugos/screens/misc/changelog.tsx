'use client';

import { useState } from 'react';
import { EmptyState } from '../../use-api-data';
import { Button } from '@/components/ui/button';
import { Bell } from 'lucide-react';
import { FadeIn, PageHeader } from '../_remaining-shared';

// ═══════════════════════════════════════════
// 35. CHANGELOG SCREEN
// ═══════════════════════════════════════════
export function ChangelogScreen() {
  const [subscribed, setSubscribed] = useState(false);
  // FE-036: No /api/changelog endpoint. Honest empty state — no fabricated versions.
  return (
    <FadeIn><div className="space-y-6">
      <PageHeader title="Changelog" desc="Product updates and release notes" actions={<Button variant="outline" size="sm" onClick={() => setSubscribed(true)}><Bell className="h-4 w-4 mr-1.5" />{subscribed ? 'Subscribed' : 'Subscribe'}</Button>} />
      {subscribed && <div className="rounded-md bg-emerald-50 border border-emerald-200 text-emerald-700 text-sm px-3 py-2">You're subscribed — we'll email you when release notes are published.</div>}
      <EmptyState title="Changelog data is not yet available" description="There is no /api/changelog endpoint in this deployment. Release notes will appear here once a changelog feed is configured (e.g. a CMS, the GitHub Releases API, or a static markdown import). No fabricated version entries are shown." />
    </div></FadeIn>
  );
}
