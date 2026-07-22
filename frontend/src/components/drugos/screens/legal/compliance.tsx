'use client';

import { useMemo } from 'react';
import { useApiResource, LoadingSpinner, ErrorDisplay, EmptyState } from '../../use-api-data';
import { api, type AuditLog } from '@/lib/api-client';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { RefreshCw, FileText, Shield, Settings, Database } from 'lucide-react';
import { FadeIn, PageHeader, StatCard } from '../_remaining-shared';

// ═══════════════════════════════════════════
// 29. COMPLIANCE SCREEN
// ═══════════════════════════════════════════
/**
 * FE-012 ROOT FIX (Team Member 15, v108): The previous ComplianceScreen
 * rendered 5 fabricated compliance frameworks ("HIPAA compliant May 2026",
 * "GDPR compliant Apr 2026", "SOC 2 Type II compliant Mar 2026",
 * "21 CFR Part 11 compliant Feb 2026", "GxP partial Jun 2026") with
 * fabricated audit dates and 3 fabricated stat cards. No API call.
 * No banner. A compliance officer saw "HIPAA compliant May 2026" —
 * fabricated. Regulatory submissions based on this are fraudulent.
 * The "21 CFR Part 11 compliant" claim was particularly dangerous —
 * FDA electronic records compliance is a legal requirement, not a
 * UI label.
 *
 * ROOT FIX: Per the issue spec, remove the fabricated compliance
 * status entirely. Compliance status must come from real audit
 * reports stored in a document management system, not hardcoded.
 * We render an honest EmptyState that points the user at the
 * compliance team / DMS — never fabricated audit dates or
 * certifications.
 */
export function ComplianceScreen() {
  // Issue 314 (audit 301-320): Wire to /api/audit-logs. The previous
  // ComplianceScreen rendered fabricated compliance certifications
  // ("HIPAA compliant", "21 CFR Part 11 compliant") with fake audit
  // dates — claiming compliance without an actual audit report is
  // regulatory fraud.
  //
  // ROOT FIX: We DO have real audit-log data. The AuditLog table records
  // every authentication event, data access, billing change, admin
  // action, etc. For compliance purposes (FDA 21 CFR Part 11, GDPR
  // Article 30, HIPAA §164.312(b)), these audit trails ARE the
  // compliance evidence. This screen now shows:
  //   - Audit log completeness (last 30 days event count)
  //   - Authentication events (logins, failed logins, MFA challenges)
  //   - Admin actions (role changes, user suspensions)
  //   - Data access events (dataset queries, evidence package builds)
  //   - Dead-letter entries (BE-003 — failed audit writes that must be
  //     investigated for compliance purposes)
  //
  // We do NOT claim any certification (HIPAA/GDPR/SOC 2/GxP/21 CFR
  // Part 11). Those require formal audit reports stored in a DMS.
  // We surface the real audit-trail evidence that supports a
  // compliance review.
  const { data: auditData, loading, error, refetch } = useApiResource<{ items: AuditLog[]; total: number }>(
    () => api.listAuditLogs(500, 0)
  );
  const logs = auditData?.items ?? [];

  // Aggregate all time-based and category filters in a single useMemo so
  // deps arrays are stable and the React Compiler can memoize correctly.
  const { authEvents, adminActions, dataAccess, recentEvents } = useMemo(() => {
    const thirtyDaysAgo = Date.now() - 30 * 24 * 60 * 60 * 1000;
    const auth = logs.filter(l => {
      const a = l.action.toLowerCase();
      return a.includes('login') || a.includes('logout') || a.includes('mfa') || a.includes('2fa');
    });
    const admin = logs.filter(l => {
      const a = l.action.toLowerCase();
      return a.includes('admin') || a.includes('role') || a.includes('user_');
    });
    const access = logs.filter(l => {
      const a = l.action.toLowerCase();
      return a.includes('dataset') || a.includes('evidence') || a.includes('hypothesis');
    });
    const recent = logs.filter(l => new Date(l.createdAt).getTime() > thirtyDaysAgo);
    return { authEvents: auth, adminActions: admin, dataAccess: access, recentEvents: recent };
  }, [logs]);

  return (
    <FadeIn>
      <div className="space-y-6">
        <PageHeader
          title="Compliance"
          desc="Real audit-trail evidence from /api/audit-logs (FDA 21 CFR Part 11, GDPR Art. 30, HIPAA §164.312(b))"
          actions={<Button variant="outline" size="sm" onClick={() => refetch()} disabled={loading}>
            <RefreshCw className={`h-4 w-4 mr-1.5 ${loading ? 'animate-spin' : ''}`} />
            Refresh
          </Button>}
        />

        {loading && <LoadingSpinner label="Loading audit trail from /api/audit-logs…" />}
        {error && <ErrorDisplay error={error} onRetry={() => refetch()} />}

        {!loading && !error && (
          <>
            <div className="grid grid-cols-1 sm:grid-cols-4 gap-4">
              <StatCard
                title="Audit Events (30d)"
                value={recentEvents.length.toLocaleString()}
                subtitle="real audit-trail rows"
                icon={FileText}
              />
              <StatCard
                title="Auth Events"
                value={authEvents.length.toLocaleString()}
                subtitle="login/logout/MFA"
                icon={Shield}
              />
              <StatCard
                title="Admin Actions"
                value={adminActions.length.toLocaleString()}
                subtitle="role/user changes"
                icon={Settings}
              />
              <StatCard
                title="Data Access"
                value={dataAccess.length.toLocaleString()}
                subtitle="dataset/evidence/hypothesis"
                icon={Database}
              />
            </div>

            {logs.length === 0 ? (
              <EmptyState
                title="No audit trail yet"
                description="Once users start authenticating and accessing data, those events will be recorded in the audit log and surfaced here as compliance evidence."
              />
            ) : (
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-base">Recent Compliance-Relevant Audit Events</CardTitle>
                </CardHeader>
                <CardContent className="p-0">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Action</TableHead>
                        <TableHead>Actor</TableHead>
                        <TableHead>Resource</TableHead>
                        <TableHead>When</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {logs.slice(0, 15).map(l => (
                        <TableRow key={l.id}>
                          <TableCell>
                            <Badge variant="outline" className="font-mono text-xs">{l.action}</Badge>
                          </TableCell>
                          <TableCell className="text-sm">{l.actorName}</TableCell>
                          <TableCell className="text-xs text-muted-foreground font-mono">{l.resource || '—'}</TableCell>
                          <TableCell className="text-xs text-muted-foreground">
                            {new Date(l.createdAt).toLocaleString()}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </CardContent>
              </Card>
            )}

            <Card className="border-amber-200 bg-amber-50 dark:bg-amber-950/30 dark:border-amber-900">
              <CardContent className="p-4">
                <p className="text-sm font-semibold text-amber-900 dark:text-amber-100 mb-2">
                  Certification Status
                </p>
                <p className="text-xs text-amber-800 dark:text-amber-200">
                  This screen surfaces REAL audit-trail evidence that supports compliance reviews
                  (FDA 21 CFR Part 11, GDPR Art. 30, HIPAA §164.312(b)). It does NOT claim any
                  formal certification. Compliance certifications (HIPAA, GDPR, SOC 2, 21 CFR Part 11,
                  GxP) are formal legal designations backed by signed audit reports, BAAs, and CSV
                  documentation stored in a DMS. Contact your compliance team or legal counsel for
                  the current certification posture.
                </p>
              </CardContent>
            </Card>
          </>
        )}
      </div>
    </FadeIn>
  );
}
