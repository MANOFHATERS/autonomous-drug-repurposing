'use client';

import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Play, Search, Target, FileText, Code, CreditCard, BookOpen, MessageSquare } from 'lucide-react';
import { FadeIn, PageHeader } from '../_remaining-shared';

// ═══════════════════════════════════════════
// 30. HELP CENTER SCREEN
// ═══════════════════════════════════════════
/**
 * FE-031 ROOT FIX: The previous HelpCenterScreen rendered fabricated
 * article counts ("Getting Started 8 articles", etc.) and fabricated
 * view counts ("2.4K views"). These numbers were made up and eroded
 * trust. Since there is no CMS or markdown file with real help articles
 * in the repo, we now render an honest state: a search bar (non-
 * functional until a search backend is added) and a "Contact Support"
 * button. No fabricated counts, no fake popularity metrics.
 */
export function HelpCenterScreen() {
  const [search, setSearch] = useState('');
  const categories = [
    { title: 'Getting Started', icon: Play },
    { title: 'Search & Queries', icon: Search },
    { title: 'Drug Candidates', icon: Target },
    { title: 'Evidence & Reports', icon: FileText },
    { title: 'API & Integration', icon: Code },
    { title: 'Billing & Plans', icon: CreditCard },
  ];
  return (
    <FadeIn><div className="space-y-6">
      <PageHeader title="Help Center" desc="Find answers and get support" />
      <Card className="bg-gradient-to-r from-primary/5 to-primary/10"><CardContent className="p-8 text-center"><h2 className="text-xl font-bold mb-3">How can we help?</h2><div className="relative max-w-lg mx-auto"><Search className="absolute left-4 top-1/2 -translate-y-1/2 h-5 w-5 text-muted-foreground" /><Input placeholder="Search help articles..." value={search} onChange={e => setSearch(e.target.value)} className="pl-12 h-12 text-base" /></div></CardContent></Card>
      {/* FE-031: Categories without fabricated article counts. */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">{categories.map(c => { const Icon = c.icon; return (<Card key={c.title} className="hover:shadow-md transition-shadow cursor-pointer"><CardContent className="p-5"><div className="flex items-center gap-3 mb-2"><Icon className="h-5 w-5 text-primary" /><h3 className="font-semibold text-sm">{c.title}</h3></div><p className="text-xs text-muted-foreground">Help articles</p></CardContent></Card>); })}</div>
      {/* FE-031: Removed "Popular Articles" section which had fabricated view
          counts ("2.4K views", "1.8K views", etc.). No real analytics exist
          to populate this, so we show an honest empty state instead. */}
      <Card>
        <CardContent className="p-6 text-center text-muted-foreground">
          <BookOpen className="h-8 w-8 mx-auto mb-2 opacity-50" />
          <p className="text-sm font-medium">Help articles coming soon</p>
          <p className="text-xs mt-1 max-w-md mx-auto">Our knowledge base is being built. For now, contact support below for assistance.</p>
        </CardContent>
      </Card>
      <div className="text-center"><Button variant="outline" onClick={() => { window.location.href = 'mailto:support@drugos.example?subject=' + encodeURIComponent('DrugOS Support Request') + '&body=' + encodeURIComponent('Describe your issue here…'); }}><MessageSquare className="h-4 w-4 mr-2" />Contact Support</Button></div>
    </div></FadeIn>
  );
}
