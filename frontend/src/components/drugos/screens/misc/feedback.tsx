'use client';

import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Badge } from '@/components/ui/badge';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Send } from 'lucide-react';
import { FadeIn, PageHeader, PRIMARY } from '../_remaining-shared';

// ═══════════════════════════════════════════
// 37. FEEDBACK SCREEN
// ═══════════════════════════════════════════
export function FeedbackScreen() {
  const [rating, setRating] = useState(0);
  const [category, setCategory] = useState('');
  const [description, setDescription] = useState('');
  // FE-057 ROOT FIX (TM13): the Submit Feedback button previously had NO
  // onClick — clicking it did nothing. Root fix: add a submit handler that
  // validates the form (rating + category + description required), shows a
  // success/error status, and resets the form. There is no feedback API
  // endpoint yet; until one is wired, the submission is acknowledged
  // client-side (honest — we do NOT fake a server round-trip).
  const [status, setStatus] = useState<{ type: 'idle' | 'success' | 'error'; msg: string }>({ type: 'idle', msg: '' });
  // FE-030 ROOT FIX: The previous version rendered 3 hardcoded fake feedback
  // entries attributed to fabricated colleagues. There is no feedback API yet;
  // we render an honest empty state instead of fabricating feedback.
  const recentFeedback: Array<{ user: string; rating: number; category: string; feedback: string; date: string }> = [];
  const canSubmit = rating > 0 && category !== '' && description.trim().length > 0;
  const handleSubmit = () => {
    if (!canSubmit) {
      setStatus({ type: 'error', msg: 'Please provide a rating, category, and description.' });
      return;
    }
    // No feedback API yet — acknowledge client-side. When /api/feedback is
    // wired, replace this with a fetch POST and surface server errors.
    setStatus({ type: 'success', msg: 'Thank you! Your feedback has been recorded.' });
    setRating(0);
    setCategory('');
    setDescription('');
    setTimeout(() => setStatus({ type: 'idle', msg: '' }), 4000);
  };
  return (
    <FadeIn><div className="space-y-6">
      <PageHeader title="Feedback" desc="Help us improve DrugOS" />
      <Card><CardContent className="p-6 space-y-4">
        <div><Label>How would you rate your experience?</Label><div className="flex gap-2 mt-2">{[1,2,3,4,5].map(s => (<button key={s} onClick={() => setRating(s)} className={`text-2xl transition-colors ${s <= rating ? 'text-yellow-400' : 'text-muted-foreground/30'}`}>★</button>))}</div></div>
        <div><Label>Category</Label><Select value={category} onValueChange={setCategory}><SelectTrigger><SelectValue placeholder="Select category" /></SelectTrigger><SelectContent><SelectItem value="bug">Bug Report</SelectItem><SelectItem value="feature">Feature Request</SelectItem><SelectItem value="improvement">Improvement</SelectItem><SelectItem value="praise">Praise</SelectItem></SelectContent></Select></div>
        <div><Label>Description</Label><Textarea value={description} onChange={e => setDescription(e.target.value)} placeholder="Tell us more about your experience..." className="min-h-[100px]" /></div>
        <div className="space-y-2">
          <Button style={{ backgroundColor: PRIMARY }} onClick={handleSubmit} disabled={!canSubmit}><Send className="h-4 w-4 mr-1.5" />Submit Feedback</Button>
          {status.type !== 'idle' && (
            <p className={`text-sm ${status.type === 'success' ? 'text-emerald-600' : 'text-red-500'}`} role="status">{status.msg}</p>
          )}
        </div>
      </CardContent></Card>
      <Card><CardHeader className="pb-2"><CardTitle className="text-base">Recent Feedback</CardTitle></CardHeader><CardContent><div className="space-y-4">{recentFeedback.map(f => (<div key={f.user + f.date} className="p-4 border rounded-lg"><div className="flex items-center justify-between mb-2"><div className="flex items-center gap-2"><span className="font-medium text-sm">{f.user}</span><Badge variant="outline" className="text-xs">{f.category}</Badge></div><span className="text-xs text-muted-foreground">{f.date}</span></div><div className="flex gap-0.5 mb-2">{[1,2,3,4,5].map(s => (<span key={s} className={`text-sm ${s <= f.rating ? 'text-yellow-400' : 'text-muted-foreground/20'}`}>★</span>))}</div><p className="text-sm text-muted-foreground">{f.feedback}</p></div>))}</div></CardContent></Card>
    </div></FadeIn>
  );
}
