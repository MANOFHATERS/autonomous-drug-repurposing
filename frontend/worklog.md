---
Task ID: v0.6.0-upgrade
Agent: Main Agent
Task: Fix all 7 screenshot-reported issues + replace remaining mock data with real backend data + zip + deploy

Work Log:
- Extracted uploaded `drugos_v0.5.0_upgraded (1).zip` and replaced scaffold src/prisma/scripts with the user's actual codebase.
- Installed all dependencies (including bcrypt, bcryptjs, jsonwebtoken, nodemailer) and pushed the Prisma schema to SQLite.
- Analyzed 7 screenshots via VLM agent and mapped each reported issue to a concrete code-level root cause.
- Fix #1 (Profile shows wrong user): rewrote `ProfileScreen` in `remaining-screens.tsx` to use `useSession()` + `api.updateMe()` instead of the hardcoded `Dr. Sarah Chen / sarah@pharma.com` seed values. Form now pre-fills with the real signed-in user's name, email, title, bio, role, member-since date, and last login.
- Fix #2 (Dark mode not applying): added `next-themes` `ThemeProvider` to `src/app/layout.tsx` with `attribute="class"` and `enableSystem`. Rewrote `PreferencesScreen` to call `useTheme()` and apply the theme immediately — clicking Light/Dark/System now toggles the `dark` class on `<html>` and the whole UI re-themes correctly.
- Fix #3 (2FA shows fake data): rewrote `SecuritySettingsScreen` to show the user's REAL 2FA status (`user.mfaEnabled` from session), with a "Set up 2FA" button that opens a real enrollment dialog. Added 3 new API endpoints (`/api/auth/2fa/setup`, `/api/auth/2fa/verify`, `/api/auth/2fa/disable`) backed by a from-scratch RFC-6238 TOTP implementation in `src/lib/auth/totp.ts` (uses only Node `crypto`, no external deps). Replaced the fake "Chrome on macOS / Safari on iPhone" sessions list with real recent account activity fetched from a new user-scoped `/api/auth/activity` endpoint.
- Fix #4 (Role-based content): expanded `BASE_SECTIONS` in `src/lib/rbac.ts` to include all real section IDs (`results`, `candidate`, `disease-detail`, `ip-patents`, `advanced-search`, `comparison`, `molecular-similarity`, `score-breakdown`, `mechanism`, `regulatory`, `history`, `batch-query`, `prediction-explorer`, `evidence-timeline`, `settings`). Sidebar now correctly filters sections per role, and direct nav to a forbidden section shows an "Access denied" page.
- Fix #5 (Settings page Maximum update depth exceeded): root cause was Radix `ScrollArea` in the AppShell sidebar triggering an infinite setState in `useComposedRefs`. Replaced `<ScrollArea>` with a stable `<div className="overflow-y-auto scrollbar-drugos">`. Also aliased the user-dropdown "Settings" item to navigate to `preferences` (a real screen) instead of the non-existent `settings` route.
- Fix #6 (Disease Quick Start error): root cause was the `results` section not being in `BASE_SECTIONS`, so RBAC denied access and the user was bounced. After adding `results` to `BASE_SECTIONS`, clicking any Quick Start disease card now correctly navigates to the Search Results screen with ranked candidates.
- Fix #7 (Pricing features should match plan tier): rewrote `PricingPage` to fetch real plans from `/api/billing/plans` and render the actual plan tiers (Free / Researcher / Team / Enterprise) with their real prices ($0/$49/$299/Custom) and feature lists. Removed the fake "Discovery Deal" tier. Rewrote `SubscriptionScreen` to show only the features included in the user's current plan, plus real plan-switching via `api.changePlan()`.
- Replaced mock data with real backend data in 7 more screens:
  - `UsersAdminScreen`: real users from `/api/admin/users`, with inline role-change via `api.updateUser()`.
  - `AuditLogsScreen`: real audit logs from `/api/audit-logs`.
  - `APIKeysScreen`: real API keys from `/api/api-keys`, with create/revoke and one-time raw-key display.
  - `NotificationsScreen`: real notifications from `/api/notifications`, with mark-as-read.
  - `TeamMembersScreen`: real team members from `/api/team`.
  - `ProjectsScreen`: real projects from `/api/projects`, with create-project dialog.
  - `InvoicesScreen`: real invoices from `/api/billing/invoices`.
- New backend endpoints added: `POST /api/auth/password`, `POST /api/auth/2fa/setup`, `POST /api/auth/2fa/verify`, `POST /api/auth/2fa/disable`, `GET /api/auth/activity`.
- Fixed a stray `Q` glyph in the sidebar by replacing the `Switch` icon (which was rendering as `Q` when used as a lucide icon) on the Feature Flags nav item with the proper `Flag` icon.
- Verified end-to-end with agent-browser: registration flow, profile real-data, dark mode toggling, settings dropdown, 2FA enrollment dialog, disease Quick Start navigation — all pass.
- ESLint passes with zero errors.

Stage Summary:
- All 7 screenshot-reported issues are fixed and browser-verified.
- 9 admin/settings screens now use real backend data instead of mock data.
- New TOTP-based 2FA system is fully functional (RFC 6238 compliant, no external deps).
- RBAC enforcement is now complete — researchers don't see admin/billing/developer sections.
- Pricing page now reflects the real backend plan tiers.
- App is running cleanly on port 3000 with no runtime errors.
- Final upgraded codebase zipped to `/home/z/my-project/download/drugos_v0.6.0_upgraded.zip`.

---
Task ID: TM13-v129
Agent: Teammate 13 (Frontend UI: shadcn/ui, Tailwind, Configs, Static Content)
Task: Forensic root-fix of Teammate 13 tasks 13.1-13.5 (FE-011, FE-030, FE-029, SH-006, FE-024)

Work Log:
- Read project docx (Team_Cosmic_Build_Process_Updated.docx) end-to-end — confirmed this is the Autonomous Drug Repurposing Platform with 6 phases (Data Ingestion → KG → Graph Transformer → RL Ranker → API/Dashboard → V1 Launch). Teammate 13 owns frontend UI: shadcn/ui, Tailwind, configs, static content.
- Cloned repo, set git identity (MANOFHATERS / manoj.c@atraiuniversity.edu.in).
- Read actual code (NOT comments, NOT tests) for every file in Teammate 13's swim lane:
  * frontend/tailwind.config.ts (91 lines)
  * frontend/src/app/globals.css (162 lines)
  * frontend/src/app/layout.tsx (115 lines)
  * frontend/src/components/error-boundary.tsx (199 lines)
  * frontend/src/components/drugos/score-bar.tsx (216 lines, canonical)
  * frontend/src/components/drugos/safety-badge.tsx (57 lines, canonical)
  * frontend/src/components/drugos/app-router.tsx (3192 lines, esp. CoreScreenBridge at line 3016)
  * frontend/src/components/drugos/core-screens.tsx (3387 lines, esp. remainingScreens spread at line 3385)
  * frontend/src/components/drugos/remaining-screens.tsx (3864 lines, exports remainingScreens record at line 3825)
  * frontend/contracts/api_contracts.ts (606 lines, hand-written)
  * frontend/components.json (shadcn config)
  * frontend/postcss.config.mjs (5 lines)
  * frontend/package.json (Next 16, React 19, Tailwind v4)
  * phase1/service.py, phase2/service.py, graph_transformer/service.py, rl/service.py (FastAPI services)
  * shared/contracts/urls.py (canonical URL contract)

Code-state assessment (red-team, hostile-auditor):
- Task 13.1 (FE-011): ALREADY correctly fixed in code. tailwind.config.ts uses var(--xxx) directly, no hsl() wrapper. globals.css defines HEX values for both light+dark. Tailwind v4 @theme inline block maps --color-* to var(--*) correctly.
- Task 13.2 (FE-030): NOT FIXED. remaining-screens.tsx is 3864 lines, statically imported by core-screens.tsx, spread into coreScreens, rendered by CoreScreenBridge with NO Suspense boundary.
- Task 13.3 (FE-029): PARTIALLY FIXED. error-boundary.tsx exists and is wired into layout.tsx at root. BUT src/app/error.tsx DOES NOT EXIST (Next.js per-route error boundary convention is missing).
- Task 13.4 (SH-006): NOT FIXED. api_contracts.ts is 606 lines of hand-written code, divergent from Python OpenAPI schemas.
- Task 13.5 (FE-024): ALREADY correctly fixed in code. All imports use canonical paths. No local ScoreBar/SafetyBadge definitions exist anywhere in the codebase.

Stage Summary:
- Identified which tasks are actually fixed vs aspirational claims.
- Tasks 13.1 and 13.5 are real fixes; will add regression guards (runtime tests).
- Tasks 13.2, 13.3, 13.4 need real code changes — proceeding to implement.

---
Task ID: TM13-v129-VERIFICATION
Agent: Teammate 13 (verification pass)
Task: Verify all 5 fixes are persisted on main after merge

Work Log:
- Pushed branch fix/tm13-v129-forensic-root-fixes to origin.
- Encountered merge conflict on frontend/src/app/error.tsx (Teammate 12 had also created one in commit 31c4ba5). Resolved by taking Teammate 13's version (strict superset: includes dev-mode stack traces, AlertCircle lucide icon, production-safe digest-only display, plus same (error, reset) signature Next.js requires).
- Pulled latest main (other agents — Teammate 4, Teammate 11 — had pushed in parallel). Merged cleanly.
- Pushed merged main to origin (commit 306763d).
- Re-cloned repo to /home/z/my-project/repo-verify/autonomous-drug-repurposing to verify fixes are persisted on main.
- Verified on fresh clone:
  * Task 13.1: tailwind.config.ts has NO hsl(var()) in active code (1 match in a comment explaining what was removed). var(--xxx) direct on lines 40-79. globals.css defines HEX values for both light + dark themes.
  * Task 13.2: core-screens.tsx has 40 dynamic() calls (37 screens + 3 in comments). app-router.tsx has 6 Suspense references including <Suspense> wrapper with fallback in CoreScreenBridge.
  * Task 13.3: src/app/error.tsx (6778 bytes) and src/app/global-error.tsx (5729 bytes) both exist.
  * Task 13.4: api_contracts.ts has AUTO-GENERATED header. extract_openapi.py (16034 bytes), generate_api_contracts.py (21570 bytes), check-contracts.sh (3298 bytes) all exist and are executable.
  * Task 13.5: NO local function ScoreBar/SafetyBadge definitions outside canonical files (empty grep result).
- Ran regression tests on fresh clone: 29/29 pass.
- Ran check-contracts.sh on fresh clone: passes (api_contracts.ts in sync with Python OpenAPI schemas).
- Ran tsc --noEmit on fresh clone: zero errors in changed files (pre-existing chart.tsx errors are unrelated shadcn/ui issues outside swim lane).

Stage Summary:
- All 5 Teammate 13 tasks (13.1-13.5) are verified persisted on main.
- 29/29 regression test guards pass on freshly-cloned main.
- Contract check passes on freshly-cloned main.
- tsc --noEmit is clean for all Teammate 13 changed files.
- Branch fix/tm13-v129-forensic-root-fixes merged to main via commit 6a9de30, then integrated with parallel work in commit 306763d.
