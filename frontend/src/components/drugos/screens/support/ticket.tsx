'use client';

import { useState } from 'react';
import { EmptyState } from '../../use-api-data';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Plus } from 'lucide-react';
import { FadeIn, PageHeader, PRIMARY } from '../_remaining-shared';

// ═══════════════════════════════════════════
// 31. TICKET SCREEN
// ═══════════════════════════════════════════
export function TicketScreen() {
  const [createOpen, setCreateOpen] = useState(false);
  const [ticketSubject, setTicketSubject] = useState('');
  const [ticketPriority, setTicketPriority] = useState('medium');
  const [ticketDescription, setTicketDescription] = useState('');
  const [ticketMsg, setTicketMsg] = useState<string | null>(null);
  // FE-035: No /api/tickets endpoint. We do NOT fabricate ticket data.
  // Submit opens the user's email client via mailto:.
  const handleSubmitTicket = () => {
    const subject = encodeURIComponent(`[DrugOS ${ticketPriority.toUpperCase()}] ${ticketSubject || '(no subject)'}`);
    const body = encodeURIComponent(`${ticketDescription}\n\n— Sent from the DrugOS Support Tickets screen`);
    window.location.href = `mailto:support@drugos.example?subject=${subject}&body=${body}`;
    setTicketMsg('Opening your email client… If nothing happens, email support@drugos.example directly.');
    setCreateOpen(false);
    setTicketSubject(''); setTicketPriority('medium'); setTicketDescription('');
  };
  return (
    <FadeIn><div className="space-y-6">
      <PageHeader title="Support Tickets" desc="Open a ticket with the DrugOS support team" actions={<Button style={{ backgroundColor: PRIMARY }} onClick={() => setCreateOpen(true)}><Plus className="h-4 w-4 mr-1.5" />New Ticket</Button>} />
      {ticketMsg && <div className="rounded-md bg-emerald-50 border border-emerald-200 text-emerald-700 text-sm px-3 py-2">{ticketMsg}</div>}
      <EmptyState title="No ticket history available" description="There is no /api/tickets endpoint in this deployment, so we cannot show past tickets. Use 'New Ticket' to email support@drugos.example. Wire a real ticketing backend to enable in-app history." />
      <Dialog open={createOpen} onOpenChange={setCreateOpen}><DialogContent><DialogHeader><DialogTitle>Create Support Ticket</DialogTitle><DialogDescription>Your ticket will be sent via email to support@drugos.example.</DialogDescription></DialogHeader><div className="space-y-4"><div><Label>Subject</Label><Input placeholder="Brief description of the issue" value={ticketSubject} onChange={e => setTicketSubject(e.target.value)} /></div><div><Label>Priority</Label><Select value={ticketPriority} onValueChange={setTicketPriority}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="low">Low</SelectItem><SelectItem value="medium">Medium</SelectItem><SelectItem value="high">High</SelectItem><SelectItem value="critical">Critical</SelectItem></SelectContent></Select></div><div><Label>Description</Label><Textarea placeholder="Provide details about the issue..." className="min-h-[100px]" value={ticketDescription} onChange={e => setTicketDescription(e.target.value)} /></div></div><DialogFooter><Button variant="outline" onClick={() => setCreateOpen(false)}>Cancel</Button><Button style={{ backgroundColor: PRIMARY }} onClick={handleSubmitTicket} disabled={!ticketSubject.trim()}>Submit Ticket</Button></DialogFooter></DialogContent></Dialog>
    </div></FadeIn>
  );
}
