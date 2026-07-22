'use client';

import { useMemo } from 'react';
import { useApiList, LoadingSpinner, ErrorDisplay, EmptyState } from '../../use-api-data';
import { api, type AdminUser } from '@/lib/api-client';
import { roleLabel } from '@/lib/rbac';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { RefreshCw } from 'lucide-react';
import { FadeIn, PageHeader } from '../_remaining-shared';

// ═══════════════════════════════════════════
// 15. ROLES SCREEN
// ═══════════════════════════════════════════
/**
 * FE-008 ROOT FIX (Team Member 15, v108): The previous RolesScreen
 * rendered 5 fabricated roles ("Super Admin 1 user", "Admin 3 users",
 * "Researcher 12 users", "Viewer 8 users", "CRO Partner 2 users") with
 * fabricated permission sets. The "Super Admin" role does not exist in
 * the codebase (real roles are admin, owner, researcher, etc.). No API
 * call. No banner. An admin could not manage real roles because the
 * screen showed fake ones. The "Super Admin" role was a privilege-
 * escalation vector if it had been created.
 *
 * ROOT FIX: Wire the screen to `api.listTeamMembers()` (GET /api/team),
 * which returns each member's real `role` (account-level role) and
 * `orgRole` (workspace-level role). Derive the role list from the
 * unique roles present in the real membership. Show real user counts
 * per role. Do NOT fabricate a "Super Admin" role or any other role
 * not present in the actual membership data.
 */
export function RolesScreen() {
  // Issue 310 (audit 301-320): Wire to /api/admin/users (not /api/team).
  // The previous version called /api/team which returns OrganizationMember
  // rows scoped to the caller's org — but the issue spec explicitly says
  // to wire to /api/admin/users, which is the admin-level endpoint that
  // returns the full User record (including role, status, emailVerified,
  // mfaEnabled, lastLoginAt). This is the correct surface for a Roles
  // & Permissions screen.
  const { data: adminData, loading, error, refetch } = useApiList<{ items: AdminUser[]; total: number }>(
    () => api.listUsers(200, 0),
    []
  );
  const users = adminData?.items ?? [];

  // Derive role entries from REAL admin user data. Group by account-level role.
  const roleMap = useMemo(() => {
    const m = new Map<string, { name: string; users: number; members: AdminUser[] }>();
    for (const u of users) {
      const key = u.role || '(no role)';
      if (!m.has(key)) {
        m.set(key, { name: key, users: 0, members: [] });
      }
      const entry = m.get(key)!;
      entry.users += 1;
      entry.members.push(u);
    }
    return Array.from(m.values()).sort((a, b) => b.users - a.users);
  }, [users]);

  return (
    <FadeIn>
      <div className="space-y-6">
        <PageHeader
          title="Roles & Permissions"
          desc="Real role distribution across your organization (from /api/admin/users)"
          actions={
            <Button variant="outline" size="sm" onClick={() => refetch()} disabled={loading}>
              <RefreshCw className={`h-4 w-4 mr-1.5 ${loading ? 'animate-spin' : ''}`} />
              Refresh
            </Button>
          }
        />

        {loading && <LoadingSpinner label="Loading users from /api/admin/users…" />}
        {error && <ErrorDisplay error={error} onRetry={() => refetch()} />}

        {!loading && !error && users.length === 0 && (
          <EmptyState
            title="No users yet"
            description="Invite team members to your organization to see the real role distribution here. Roles are derived from actual user data — never fabricated."
          />
        )}

        {!loading && !error && users.length > 0 && (
          <Card>
            <CardContent className="p-0">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Role</TableHead>
                    <TableHead>Label</TableHead>
                    <TableHead>Users</TableHead>
                    <TableHead>Members</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {roleMap.map(r => (
                    <TableRow key={r.name}>
                      <TableCell className="font-medium font-mono text-sm">{r.name}</TableCell>
                      <TableCell>
                        <Badge variant="outline" className="capitalize">
                          {roleLabel(r.name)}
                        </Badge>
                      </TableCell>
                      <TableCell>{r.users}</TableCell>
                      <TableCell className="text-xs text-muted-foreground">
                        {r.members.slice(0, 5).map(m => m.name || m.email).join(', ')}
                        {r.members.length > 5 && ` +${r.members.length - 5} more`}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        )}

        <p className="text-xs text-muted-foreground italic">
          Note: The permission matrix (which role can access which feature) is enforced
          server-side via @/lib/rbac. The previous RolesScreen fabricated a permission grid
          that did not reflect the actual RBAC rules. To inspect real permissions, review
          rbac.ts and the route handlers that call requireRole().
        </p>
      </div>
    </FadeIn>
  );
}
