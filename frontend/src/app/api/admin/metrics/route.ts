import { NextResponse } from "next/server";
import {
  requireAdmin,
  internalError,
  writeAuditLog,
  isPlatformSuperuser,
} from "@/lib/api-helpers";
import { db } from "@/lib/db";
import { getDatasetStats } from "@/lib/services/dataset-stats";
import { getKnowledgeGraphStats } from "@/lib/services/knowledge-graph-stats";

/**
 * GET /api/admin/metrics
 *
 * Issue 315 (audit 301-320): Wire Investor Dashboard to real platform metrics.
 *
 * Previously the InvestorDashboardScreen rendered fabricated ARR/MRR/customer
 * counts/NRR/cohort data — a serious problem because showing fake financials
 * to actual investors is securities fraud.
 *
 * ROOT FIX: This endpoint returns REAL, derivable platform metrics from
 * existing DB tables and services. It does NOT fabricate financial data.
 *
 * What we CAN compute from existing data:
 *   - totalUsers: COUNT(User)
 *   - totalOrganizations: COUNT(Organization)
 *   - activeSubscriptions: COUNT(Subscription WHERE status='active')
 *   - totalProjects: COUNT(Project)
 *   - totalHypotheses: COUNT(Hypothesis)
 *   - totalValidatedHypotheses: COUNT(Hypothesis WHERE status='validated')
 *   - auditLogEventsLast30Days: COUNT(AuditLog WHERE createdAt > now-30d)
 *   - topActionsLast30Days: GROUP BY action, COUNT, ORDER BY count DESC
 *   - dailyActiveUsersLast7Days: COUNT(DISTINCT userId) in AuditLog per day
 *   - kgNodeCount / kgEdgeCount: from getKnowledgeGraphStats()
 *   - datasetNodesLoaded / datasetEdgesLoaded: from getDatasetStats()
 *
 * What we CANNOT compute (and explicitly refuse to fabricate):
 *   - ARR / MRR: requires Stripe integration (not deployed)
 *   - Customer counts: requires CRM integration (not deployed)
 *   - NRR / cohort retention: requires subscription-event history (not modeled)
 *   - EBITDA projections: requires accounting system integration (not deployed)
 *
 * The InvestorDashboardScreen uses this endpoint to show REAL platform
 * traction (users, projects, hypotheses, validated discoveries, KG size)
 * WITHOUT fabricating financial data. Any financial card shows a clear
 * "Requires Stripe integration" notice — never a fabricated dollar value.
 *
 * AUTH: requires admin/owner role. Org-scoped admins see only their org's
 * metrics. platformOwner sees system-wide metrics.
 */
export async function GET() {
  const auth = await requireAdmin();
  if (auth.user === null) return auth.response;

  try {
    const isSuperuser = isPlatformSuperuser(auth.user);

    // For org-scoped admins, count only users/projects in their org.
    // For platformOwner, count system-wide.
    const orgId = !isSuperuser ? auth.user.orgId : null;

    // Run all DB counts in parallel for performance.
    const [
      totalUsers,
      totalOrganizations,
      activeSubscriptions,
      totalProjects,
      totalHypotheses,
      totalValidatedHypotheses,
      totalEvidencePackages,
      auditLogEventsLast30Days,
      topActionsRows,
      dailyActivityRows,
      datasetStats,
      kgStats,
    ] = await Promise.all([
      // User count — org-scoped for non-superusers via OrganizationMember
      db.user.count(
        isSuperuser
          ? undefined
          : {
              where: {
                organizationMemberships: { some: { organizationId: orgId! } },
              },
            },
      ),
      // Org count — superuser sees all, org admin sees only their org
      db.organization.count(isSuperuser ? undefined : { where: { id: orgId! } }),
      // Active subscriptions — org-scoped
      db.subscription.count({
        where: {
          ...(isSuperuser ? {} : { organizationId: orgId! }),
          status: "active",
        },
      }),
      // Project count — org-scoped (Project has organizationId)
      db.project.count({ where: isSuperuser ? undefined : { organizationId: orgId! } }),
      // Hypothesis count — derived via project join, org-scoped
      db.hypothesis.count({
        where: isSuperuser
          ? undefined
          : { project: { organizationId: orgId! } },
      }),
      // Validated hypothesis count — the "real discoveries" metric
      db.hypothesis.count({
        where: {
          status: "validated",
          ...(isSuperuser ? {} : { project: { organizationId: orgId! } }),
        },
      }),
      // Evidence package count — org-scoped via project join
      // (EvidencePackage has no direct organizationId, but Project does)
      db.evidencePackage.count({
        where: isSuperuser
          ? undefined
          : { project: { organizationId: orgId! } },
      }),
      // Audit log events in last 30 days — org-scoped
      db.auditLog.count({
        where: {
          createdAt: { gt: new Date(Date.now() - 30 * 24 * 60 * 60 * 1000) },
          ...(isSuperuser ? {} : { organizationId: orgId! }),
        },
      }),
      // Top actions in last 30 days — GROUP BY action
      db.auditLog.groupBy({
        by: ["action"],
        _count: { _all: true },
        where: {
          createdAt: { gt: new Date(Date.now() - 30 * 24 * 60 * 60 * 1000) },
          ...(isSuperuser ? {} : { organizationId: orgId! }),
        },
        orderBy: { _count: { action: "desc" } },
        take: 10,
      }),
      // Daily active users (distinct userId) for last 7 days — raw query.
      // Prisma $queryRawTyped with parameterized WHERE clause for org scoping.
      isSuperuser
        ? db.$queryRaw<Array<{ day: string; active_users: bigint }>>`
            SELECT DATE("createdAt") AS day,
                   COUNT(DISTINCT "userId") AS active_users
            FROM "AuditLog"
            WHERE "createdAt" > NOW() - INTERVAL '7 days'
              AND "userId" IS NOT NULL
            GROUP BY DATE("createdAt")
            ORDER BY day DESC
          `
        : db.$queryRaw<Array<{ day: string; active_users: bigint }>>`
            SELECT DATE("createdAt") AS day,
                   COUNT(DISTINCT "userId") AS active_users
            FROM "AuditLog"
            WHERE "createdAt" > NOW() - INTERVAL '7 days'
              AND "userId" IS NOT NULL
              AND "organizationId" = ${orgId!}
            GROUP BY DATE("createdAt")
            ORDER BY day DESC
          `,
      // Phase 1 dataset stats
      getDatasetStats(),
      // Phase 2 KG stats
      getKnowledgeGraphStats().catch(() => null),
    ]);

    // Convert Prisma groupBy result to plain object
    const topActionsLast30Days = topActionsRows.map((r) => ({
      action: r.action,
      count: Number(r._count._all),
    }));

    // Convert raw query result (bigint -> number)
    const dailyActiveUsersLast7Days = dailyActivityRows.map((r) => ({
      day: typeof r.day === "string" ? r.day : String(r.day),
      activeUsers: Number(r.active_users),
    }));

    // Aggregate top-level metrics
    const metrics = {
      scope: isSuperuser ? "system" : "organization",
      organizationId: orgId || null,
      generatedAt: new Date().toISOString(),
      // REAL user/org/subscription counts
      totalUsers,
      totalOrganizations,
      activeSubscriptions,
      // REAL research activity counts
      totalProjects,
      totalHypotheses,
      totalValidatedHypotheses,
      totalEvidencePackages,
      // REAL platform activity (last 30 days)
      auditLogEventsLast30Days,
      topActionsLast30Days,
      dailyActiveUsersLast7Days,
      // REAL Phase 1 + Phase 2 data scale
      dataset: {
        nodesLoaded: datasetStats.nodesLoaded,
        edgesLoaded: datasetStats.edgesLoaded,
        sourcesLoaded: datasetStats.sources.filter((s) => s.loaded).length,
        sourcesTotal: datasetStats.sources.length,
        source: datasetStats.source,
        status: (datasetStats as any).status,
      },
      knowledgeGraph: kgStats
        ? {
            nodeCount: kgStats.nodeCount,
            edgeCount: kgStats.edgeCount,
            source: kgStats.source,
          }
        : null,
      // EXPLICITLY NOT FABRICATED — financial metrics are omitted, not invented
      financials: {
        arr: null,
        mrr: null,
        customerCount: null,
        nrr: null,
        note: "Financial metrics (ARR/MRR/customer count/NRR) require Stripe + CRM integration. NOT fabricated. Wire a billing system before exposing these to investors.",
      },
    };

    await writeAuditLog({
      user: auth.user,
      action: "admin_metrics_query",
      resource: "admin:metrics",
      metadata: {
        scope: metrics.scope,
        totalUsers,
        totalProjects,
        totalValidatedHypotheses,
      },
    });

    return NextResponse.json(metrics);
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e);
    return internalError(`Admin metrics query failed: ${msg}`);
  }
}
