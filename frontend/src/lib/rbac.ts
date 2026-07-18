/**
 * Role-Based Access Control (RBAC) for DruGOS.
 *
 * Each sidebar section has a list of roles allowed to view it. The AppShell
 * sidebar filters out sections the current user's role cannot access, and
 * the AppSectionRenderer redirects to the dashboard if a user tries to
 * navigate directly to a forbidden section.
 *
 * Role hierarchy:
 *   viewer        — read-only access to research outputs
 *   researcher    — search diseases, build evidence packages, save queries
 *   data-scientist — researcher + datasets, graph statistics, quality metrics
 *   pi            — researcher + projects, team oversight
 *   business-dev  — researcher + deals, market opportunity data
 *   developer     — developer platform: API keys, webhooks, playground
 *   billing       — billing & invoices only
 *   admin         — full access (users, roles, SSO, audit logs, feature flags)
 *   owner         — full access (same as admin + billing)
 */

export type Role =
  | "viewer"
  | "researcher"
  | "data_scientist"
  | "pi"
  | "business_dev"
  | "developer"
  | "billing"
  | "admin"
  | "owner"
  // BE-017 v123 FORENSIC ROOT FIX: add `platformOwner` to the canonical
  // TypeScript Role type. The Prisma schema declares it (UserRole enum),
  // requireAdmin in api-helpers.ts accepts it, but the TS type omitted it —
  // so `roleLabel("platformOwner")` returned the raw string instead of
  // "Platform Owner", and `visibleSectionsForRole("platformOwner")` granted
  // no restricted sections (a platformOwner saw only BASE_SECTIONS in the
  // sidebar — same as a viewer, despite having admin API access).
  | "platformOwner";

/** Sections every authenticated user can see. */
const BASE_SECTIONS = [
  "dashboard",
  "search",
  "search-results",
  "results",
  "candidate-detail",
  "candidate",
  "disease-detail",
  "knowledge-graph",
  "pathways",
  "safety",
  "patents",
  "ip-patents",
  "clinical-trials",
  "literature",
  "admet",
  "interactions",
  "evidence-builder",
  "reports",
  "saved-queries",
  "shortlists",
  "history",
  "batch-query",
  "prediction-explorer",
  "evidence-timeline",
  "mechanism",
  "regulatory",
  "advanced-search",
  "comparison",
  "molecular-similarity",
  "score-breakdown",
  "profile",
  "security",
  "notifications",
  "preferences",
  "settings",
  "help-center",
  "tickets",
  "system-status",
  "privacy",
  "terms",
  "compliance",
];

/** Sections restricted to specific roles. */
// BE-017 v123: `platformOwner` is added to every section that includes
// `owner` — platform owners are SaaS operator staff with legitimate
// need-to-know across all functional areas (they debug issues, audit
// access, and assist customers). They are NOT the same as `owner` (which
// is an org-scoped role), but for sidebar-visibility purposes they have
// the same access. The /api/admin/* routes are gated separately on
// `platformRole === "admin"` (a DIFFERENT field) — see
// lib/auth/require-platform-admin.ts.
const ROLE_SECTIONS: Record<string, string[]> = {
  // Team & collaboration
  team: ["pi", "admin", "owner", "platformOwner"],
  projects: ["researcher", "data_scientist", "pi", "admin", "owner", "business_dev", "platformOwner"],
  "shared-queries": ["researcher", "data_scientist", "pi", "admin", "owner", "platformOwner"],
  annotations: ["researcher", "data_scientist", "pi", "admin", "owner", "platformOwner"],

  // Data science — datasets, graph stats, quality
  "data-sources": ["data_scientist", "pi", "admin", "owner", "platformOwner"],
  "graph-stats": ["data_scientist", "pi", "admin", "owner", "platformOwner"],
  quality: ["data_scientist", "pi", "admin", "owner", "platformOwner"],

  // Billing
  subscription: ["owner", "admin", "billing", "platformOwner"],
  usage: ["owner", "admin", "billing", "platformOwner"],
  deals: ["owner", "admin", "business_dev", "platformOwner"],
  invoices: ["owner", "admin", "billing", "platformOwner"],

  // Admin console
  users: ["admin", "owner", "platformOwner"],
  roles: ["admin", "owner", "platformOwner"],
  sso: ["admin", "owner", "platformOwner"],
  "audit-logs": ["admin", "owner", "platformOwner"],
  "feature-flags": ["admin", "owner", "platformOwner"],

  // Developer platform
  "api-docs": ["developer", "admin", "owner", "platformOwner"],
  "api-keys": ["developer", "admin", "owner", "platformOwner"],
  playground: ["developer", "admin", "owner", "platformOwner"],
  webhooks: ["developer", "admin", "owner", "platformOwner"],

  // Investor relations
  "investor-dashboard": ["owner", "platformOwner"],
  "cap-table": ["owner", "platformOwner"],

  // More
  changelog: ["researcher", "data_scientist", "pi", "admin", "owner", "business_dev", "developer", "viewer", "platformOwner"],
  roadmap: ["researcher", "data_scientist", "pi", "admin", "owner", "business_dev", "developer", "viewer", "platformOwner"],
  feedback: ["researcher", "data_scientist", "pi", "admin", "owner", "business_dev", "developer", "viewer", "platformOwner"],
};

/**
 * Returns true if the given role is allowed to access the given section.
 * Base sections are accessible to everyone; restricted sections require
 * the role to appear in the ROLE_SECTIONS list.
 */
export function canAccessSection(role: string | undefined | null, sectionId: string): boolean {
  if (!role) return false;
  if (BASE_SECTIONS.includes(sectionId)) return true;
  const allowed = ROLE_SECTIONS[sectionId];
  if (!allowed) return false; // unknown section — default deny
  return allowed.includes(role);
}

/**
 * Returns the list of section IDs the given role can access. Used to
 * filter the sidebar navigation.
 */
export function visibleSectionsForRole(role: string | undefined | null): string[] {
  if (!role) return BASE_SECTIONS;
  const all = [...BASE_SECTIONS];
  for (const [section, roles] of Object.entries(ROLE_SECTIONS)) {
    if (roles.includes(role)) all.push(section);
  }
  return all;
}

/** Human-readable label for each role. */
export const ROLE_LABELS: Record<string, string> = {
  viewer: "Viewer",
  researcher: "Researcher",
  "data_scientist": "Data Scientist",
  pi: "Principal Investigator",
  "business_dev": "Business Development",
  developer: "Developer",
  billing: "Billing",
  admin: "Administrator",
  owner: "Owner",
  // BE-017 v123: add label for platformOwner so roleLabel() returns a
  // human-readable string instead of the raw "platformOwner" identifier.
  platformOwner: "Platform Owner",
};

/** Returns a friendly label for a role, or the raw role string if unknown. */
export function roleLabel(role: string | undefined | null): string {
  if (!role) return "Unknown";
  return ROLE_LABELS[role] || role;
}
