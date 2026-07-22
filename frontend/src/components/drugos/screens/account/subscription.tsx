'use client';

import { useState, useEffect } from 'react';
import { useDrugOSNav } from '../../nav-context';
import { useSession } from '../../session-provider';
import { api, type Plan, type Subscription } from '@/lib/api-client';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle, CardFooter } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Check } from 'lucide-react';
import { FadeIn, PageHeader, PRIMARY } from '../_remaining-shared';

// ═══════════════════════════════════════════
// 10. SUBSCRIPTION SCREEN — real plan data from /api/billing/*, shows only the user's plan's features
// ═══════════════════════════════════════════
export function SubscriptionScreen() {
  const { navigate } = useDrugOSNav();
  const { organizations, activeOrganizationId } = useSession();
  const [plans, setPlans] = useState<Plan[]>([]);
  const [subscription, setSubscription] = useState<Subscription | null>(null);
  const [loading, setLoading] = useState(true);
  const [changing, setChanging] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  // FE-021 ROOT FIX: Prompt for current password (and TOTP if MFA enabled)
  // before calling changePlan. The route requires re-authentication.
  const [showPasswordPrompt, setShowPasswordPrompt] = useState(false);
  const [pendingPlanId, setPendingPlanId] = useState<string | null>(null);
  const [currentPassword, setCurrentPassword] = useState('');
  const [totpCode, setTotpCode] = useState('');

  useEffect(() => {
    let mounted = true;
    Promise.all([
      api.listPlans(),
      api.getSubscription(),
    ]).then(([plansRes, subRes]) => {
      if (!mounted) return;
      setPlans(plansRes.plans);
      setSubscription(subRes.subscription);
      setLoading(false);
    }).catch(e => {
      if (!mounted) return;
      setErr(e?.message || 'Failed to load subscription data.');
      setLoading(false);
    });
    return () => { mounted = false };
  }, []);

  const activeOrg = organizations.find(o => o.id === activeOrganizationId) || organizations[0];
  const currentPlanId = subscription?.plan || activeOrg?.plan || 'free';
  const currentPlan = plans.find(p => p.id === currentPlanId) || plans[0];

  // FE-021 ROOT FIX: Show password prompt first. The billing/subscription
  // route requires currentPassword (and TOTP if MFA is enabled). We collect
  // these from the user before calling api.changePlan.
  const promptForPassword = (planId: string) => {
    setPendingPlanId(planId);
    setCurrentPassword('');
    setTotpCode('');
    setShowPasswordPrompt(true);
    setMsg(null);
    setErr(null);
  };

  const handleChangePlan = async () => {
    if (!pendingPlanId || !currentPassword) return;
    setChanging(pendingPlanId); setShowPasswordPrompt(false); setErr(null);
    try {
      await api.changePlan({
        planId: pendingPlanId,
        currentPassword,
        ...(totpCode ? { totpCode } : {}),
      });
      const subRes = await api.getSubscription();
      setSubscription(subRes.subscription);
      setMsg(`Plan changed to ${plans.find(p => p.id === pendingPlanId)?.name || pendingPlanId}.`);
    } catch (e: any) {
      setErr(e?.message || 'Failed to change plan. Check your password and 2FA code.');
    } finally {
      setChanging(null);
      setPendingPlanId(null);
      setCurrentPassword('');
      setTotpCode('');
    }
  };

  if (loading) {
    return <FadeIn><div className="p-8 text-center text-muted-foreground">Loading subscription…</div></FadeIn>;
  }

  return (
    <FadeIn><div className="space-y-6">
      <PageHeader title="Subscription" desc="Manage your plan and billing" />
      {msg && <div className="rounded-md bg-emerald-50 border border-emerald-200 text-emerald-700 text-sm px-3 py-2 dark:bg-emerald-950/40 dark:border-emerald-900 dark:text-emerald-300">{msg}</div>}
      {err && <div className="rounded-md bg-red-50 border border-red-200 text-red-700 text-sm px-3 py-2 dark:bg-red-950/40 dark:border-red-900 dark:text-red-300">{err}</div>}

      {/* FE-021 ROOT FIX: Password prompt modal for re-authentication. The
          billing/subscription route requires currentPassword (and TOTP if MFA
          enabled) for all plan changes. This modal collects the credentials
          before calling api.changePlan. */}
      {showPasswordPrompt && (
        <Card className="border-amber-300 bg-amber-50">
          <CardContent className="p-4">
            <p className="text-sm font-semibold text-amber-900 mb-2">Re-authentication required</p>
            <p className="text-xs text-amber-800 mb-3">Changing your plan requires your current password for security.</p>
            <div className="space-y-2">
              <Input
                type="password"
                placeholder="Current password"
                value={currentPassword}
                onChange={e => setCurrentPassword(e.target.value)}
                className="bg-white"
              />
              <Input
                type="text"
                placeholder="2FA code (if MFA enabled)"
                value={totpCode}
                onChange={e => setTotpCode(e.target.value)}
                className="bg-white"
                maxLength={6}
              />
              <div className="flex gap-2">
                <Button size="sm" onClick={handleChangePlan} disabled={!currentPassword}>Confirm Change</Button>
                <Button size="sm" variant="ghost" onClick={() => setShowPasswordPrompt(false)}>Cancel</Button>
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Current plan — only shows features included in the user's plan */}
      {currentPlan && (
        <Card className="border-primary/30">
          <CardContent className="p-6">
            <div className="flex items-center justify-between mb-4">
              <div>
                <h3 className="text-lg font-semibold">{currentPlan.name} Plan</h3>
                <p className="text-sm text-muted-foreground">Your current plan · {currentPlan.seats} seat{currentPlan.seats === 1 ? '' : 's'}</p>
              </div>
              <div className="text-right">
                {/* FE-024 ROOT FIX: Use priceCents / 100 instead of the
                    non-existent `price` field. The billing.ts Plan interface
                    uses priceCents, not price. */}
                <p className="text-3xl font-bold">${((currentPlan.priceCents || 0) / 100).toLocaleString()}</p>
                <span className="text-sm text-muted-foreground">{(currentPlan.priceCents || 0) === 0 ? 'forever' : '/month'}</span>
              </div>
            </div>
            <div>
              <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-2">Features included in your plan</p>
              <ul className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                {currentPlan.features.map((f, i) => (
                  <li key={i} className="flex items-start gap-2 text-sm">
                    <Check className="h-4 w-4 text-emerald-500 shrink-0 mt-0.5" />
                    <span>{f}</span>
                  </li>
                ))}
              </ul>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Available plans — only shows the upgrade options the user is allowed to switch to */}
      <div>
        <h3 className="text-lg font-semibold mb-3">Available Plans</h3>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
          {plans.map(plan => {
            const isCurrent = plan.id === currentPlanId;
            return (
              <Card key={plan.id} className={`hover:shadow-md transition-shadow ${isCurrent ? 'border-primary ring-1 ring-primary/30' : ''}`}>
                <CardHeader>
                  <CardTitle className="text-lg flex items-center justify-between">
                    {plan.name}
                    {isCurrent && <Badge style={{ backgroundColor: PRIMARY, color: 'white' }}>Current</Badge>}
                  </CardTitle>
                  <div className="mt-1">
                    {/* FE-024 ROOT FIX: Use priceCents instead of price. */}
                    <span className="text-2xl font-bold">${(plan.priceCents / 100).toLocaleString()}</span>
                    <span className="text-sm text-muted-foreground">{plan.priceCents === 0 ? ' forever' : '/month'}</span>
                  </div>
                </CardHeader>
                <CardContent>
                  <p className="text-xs text-muted-foreground mb-2">{plan.seats} seat{plan.seats === 1 ? '' : 's'}</p>
                  <ul className="space-y-1.5">
                    {plan.features.slice(0, 5).map((f, i) => (
                      <li key={i} className="flex items-start gap-2 text-sm">
                        <Check className="h-3 w-3 text-emerald-500 shrink-0 mt-0.5" />
                        <span>{f}</span>
                      </li>
                    ))}
                  </ul>
                </CardContent>
                <CardFooter>
                  <Button
                    variant={isCurrent ? 'outline' : 'default'}
                    className="w-full"
                    disabled={isCurrent || changing === plan.id}
                    onClick={() => promptForPassword(plan.id)}
                    style={!isCurrent ? { backgroundColor: PRIMARY } : undefined}
                  >
                    {changing === plan.id ? 'Switching…' : isCurrent ? 'Current Plan' : (plan.priceCents === 0 ? 'Downgrade' : 'Upgrade')}
                  </Button>
                </CardFooter>
              </Card>
            );
          })}
        </div>
      </div>
    </div></FadeIn>
  );
}
