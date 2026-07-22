'use client';

import { useState } from 'react';
import { EmptyState } from '../../use-api-data';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Textarea } from '@/components/ui/textarea';
import { Badge } from '@/components/ui/badge';
import { Avatar, AvatarFallback } from '@/components/ui/avatar';
import { FadeIn, PageHeader, PRIMARY } from '../_remaining-shared';

// ═══════════════════════════════════════════
// 6. ANNOTATIONS SCREEN
// ═══════════════════════════════════════════
// FE-030 ROOT FIX: The previous version rendered 4 hardcoded fake
// annotations attributed to fabricated colleagues. Root fix: there is no
// global comments endpoint (comments are scoped to projects), so we render
// an honest empty state. We NEVER fabricate comments or attribute them to
// fake colleagues.
export function AnnotationsScreen() {
  const [newComment, setNewComment] = useState('');
  const annotations: Array<{ candidate: string; disease: string; author: string; comment: string; date: string; resolved: boolean }> = [];
  return (
    <FadeIn><div className="space-y-6">
      <PageHeader title="Annotations" desc="Collaborative notes on drug candidates" actions={<Badge variant="outline">{annotations.filter(a => !a.resolved).length} Open</Badge>} />
      <div className="space-y-4">
        {annotations.length === 0 && (
          <EmptyState title="No annotations yet" description="Open a project to add comments and annotations to drug candidates. Annotations are scoped to projects — there is no global feed." />
        )}
        {annotations.map((a, i) => (<Card key={i} className={a.resolved ? 'opacity-60' : ''}><CardContent className="p-4"><div className="flex items-start justify-between mb-2"><div className="flex items-center gap-2"><Badge variant="secondary" className="text-xs">{a.candidate}</Badge><Badge variant="outline" className="text-xs">{a.disease}</Badge>{a.resolved && <Badge className="text-xs bg-green-100 text-green-700">Resolved</Badge>}</div><Button variant="ghost" size="sm">{a.resolved ? 'Reopen' : 'Resolve'}</Button></div>
          <p className="text-sm">{a.comment}</p><div className="flex items-center gap-2 mt-3 text-xs text-muted-foreground"><span>{a.author}</span><span>·</span><span>{a.date}</span></div>
        </CardContent></Card>))}
      </div>
      <Card><CardContent className="p-4"><div className="flex gap-3"><Avatar className="h-8 w-8"><AvatarFallback className="bg-primary/10 text-primary text-xs">YO</AvatarFallback></Avatar><div className="flex-1"><Textarea placeholder="Add a comment or annotation..." value={newComment} onChange={e => setNewComment(e.target.value)} className="min-h-[60px]" /><div className="flex justify-end mt-2"><Button size="sm" style={{ backgroundColor: PRIMARY }}>Post Comment</Button></div></div></div></CardContent></Card>
    </div></FadeIn>
  );
}
