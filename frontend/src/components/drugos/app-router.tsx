'use client'

/**
 * FE-023 ROOT FIX (Teammate 17): app-router.tsx is now a slim router.
 *
 * Previously this file was 3294 lines containing ~45 React components
 * (public marketing pages, auth pages, app pages, AppShell, layout helpers)
 * PLUS the main router (RouterContext, useUrlRoute, DrugOSApp).
 *
 * ROOT FIX: every component has been extracted into its own file under
 * `screens/<category>/` (or `app-shell.tsx` for AppShell). This file now
 * contains ONLY:
 *   - RouterContext + useRouter (legacy context bridge)
 *   - useUrlRoute hook (URL <-> Route sync)
 *   - DrugOSApp (the main router component)
 *   - Named re-exports for backward compat (existing imports like
 *     `import { LandingPage } from './app-router'` continue to work).
 *
 * The NextRouterProvider at `next-router-provider.tsx` mounts the
 * Next.js App Router's `useRouter` into the legacy RouterContext so all
 * `useRouter()` calls in the extracted screens produce REAL URL paths.
 *
 * FE-024 ROOT FIX: the original 90+ lucide-react icon barrel import is
 * GONE. Each extracted screen imports only the icons it actually uses.
 * The slim router below imports ZERO lucide-react icons (it doesn't
 * render any UI itself — it just dispatches to the right screen).
 */

import React, { useState, useEffect, useCallback, createContext, useContext, useMemo } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { type Route, routeToUrl, parseUrlToRoute } from './url-route'

// ───────────────────────────────────────────────────────────────────────────
// FE-023 ROOT FIX: Import every screen from its new per-screen file.
// Re-export them below for backward compat with existing callers.
// ───────────────────────────────────────────────────────────────────────────

// Shared layout helpers (DrugOSLogo, StatusDot, SectionHeading, PublicHeader,
// PublicFooter, PublicLayout).
import {
  DrugOSLogo,
  StatusDot,
  SectionHeading,
  PublicHeader,
  PublicFooter,
  PublicLayout,
} from './screens/_app-layout'

// Public marketing pages
import { LandingPage } from './screens/public/landing'
import { PricingPage } from './screens/public/pricing'
import { AboutPage } from './screens/public/about'
import { SecurityPage } from './screens/public/security'
import { StatusPage } from './screens/public/status'
import { BlogPage } from './screens/public/blog'
import { ContactPage } from './screens/public/contact'
import { CareersPage } from './screens/public/careers'
import { CaseStudiesPage } from './screens/public/case-studies'
import { FeaturePage } from './screens/public/feature'

// Auth pages
import { AuthLayout } from './screens/auth/_auth-layout'
import { LoginPage } from './screens/auth/login'
import { RegisterPage } from './screens/auth/register'
import { ForgotPasswordPage } from './screens/auth/forgot-password'
import { ResetPasswordPage } from './screens/auth/reset-password'
import { MFAChallengePage } from './screens/auth/mfa-challenge'
import { EmailVerificationPage } from './screens/auth/email-verification'
import { AcademicVerificationPage } from './screens/auth/academic-verification'
import { OrgSelectionPage } from './screens/auth/org-selection'
import { OnboardingWelcomePage } from './screens/auth/onboarding-welcome'
import { OnboardingRolePage } from './screens/auth/onboarding-role'
import { OnboardingWorkspacePage } from './screens/auth/onboarding-workspace'
import { OnboardingInvitePage } from './screens/auth/onboarding-invite'
import { AdminApprovalPage } from './screens/auth/admin-approval'
import { AccountLockedPage } from './screens/auth/account-locked'

// AppShell + sidebarNavGroups
import { AppShell, sidebarNavGroups } from './app-shell'

// App pages (rendered inside AppShell)
import { AppDashboard } from './screens/app/app-dashboard'
import { AppSearchPage } from './screens/app/app-search'
import { AppSearchResultsPage } from './screens/app/app-search-results'
import { AppPlaceholderSection } from './screens/app/app-placeholder'
import { CoreScreenBridge } from './screens/app/core-screen-bridge'
import { CoreScreenSkeleton } from './screens/app/core-screen-skeleton'
import { AppSectionRenderer } from './screens/app/app-section-renderer'

// =====================================================================
// TYPES & ROUTER CONTEXT
// =====================================================================

interface RouterContextType {
  route: Route
  navigate: (r: Route) => void
}

/**
 * FE-001: legacy RouterContext. New code should import `useRouter` from
 * `./next-router-provider` instead — that module bridges this context
 * to next/navigation's real `useRouter`, so `navigate({...})` calls
 * produce REAL URL paths instead of query strings.
 *
 * This context is kept ONLY for backward compat with screens that still
 * call `useRouter()` (which now re-exports from next-router-provider).
 */
const RouterContext = createContext<RouterContextType | null>(null)

/**
 * Legacy useRouter hook. Reads from RouterContext. New code should
 * import from `./next-router-provider` directly.
 */
function useRouter() {
  const ctx = useContext(RouterContext)
  if (!ctx) {
    throw new Error('useRouter must be used within a RouterContext.Provider')
  }
  return ctx
}

// =====================================================================
// URL ROUTE HOOK
// =====================================================================

/**
 * Synchronizes React state with the browser URL. On mount, parses the
 * current URL into a Route. On `navigate(r)`, pushes the new URL onto
 * the history stack. Listens for `popstate` so the back/forward buttons
 * update the route.
 *
 * This hook is used ONLY by the legacy DrugOSApp component below. The
 * Next.js App Router pages (under `frontend/src/app/(group)/page.tsx`)
 * use next/navigation's `useRouter` directly and do NOT need this hook.
 */

function useUrlRoute(): [Route, (r: Route) => void] {
  const [route, setRoute] = useState<Route>(() =>
    typeof window === 'undefined' ? { page: 'landing' } : parseUrlToRoute(window.location.href)
  )
  useEffect(() => {
    const onPop = () => setRoute(parseUrlToRoute(window.location.href))
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])
  const navigate = useCallback((r: Route) => {
    setRoute(r)
    if (typeof window !== 'undefined') {
      window.history.pushState({}, '', routeToUrl(r))
    }
  }, [])
  return [route, navigate]
}

// =====================================================================
// MAIN APP ROUTER COMPONENT
// =====================================================================

/**
 * The legacy DrugOSApp router. Used by the standalone `<DrugOSApp />`
 * entrypoint (e.g., for Storybook, integration tests, or the legacy
 * /playground route). The Next.js App Router pages do NOT use this —
 * they render the imported screen components directly inside their
 * own `layout.tsx` wrappers (PublicLayout for marketing, AppShell
 * for app pages).
 *
 * This component is preserved for backward compat. It mounts the
 * RouterContext.Provider so any screen calling `useRouter()` works.
 */
export default function DrugOSApp() {
  const [route, navigate] = useUrlRoute()

  const routerContext = useMemo(() => ({
    route,
    navigate,
  }), [route, navigate])

  const renderPage = () => {
    // Public pages (with PublicLayout)
    const publicPages: Record<string, React.ReactNode> = {
      'landing': <LandingPage />,
      'pricing': <PricingPage />,
      'about': <AboutPage />,
      'security': <SecurityPage />,
      'status': <StatusPage />,
      'blog': <BlogPage />,
      'contact': <ContactPage />,
      'careers': <CareersPage />,
      'case-studies': <CaseStudiesPage />,
    }

    if (route.page === 'features') {
      return <PublicLayout><FeaturePage slug={route.slug} /></PublicLayout>
    }

    if (publicPages[route.page]) {
      return <PublicLayout>{publicPages[route.page]}</PublicLayout>
    }

    // Auth pages
    const authPages: Record<string, React.ReactNode> = {
      'login': <LoginPage />,
      'register': <RegisterPage />,
      'forgot-password': <ForgotPasswordPage />,
      'reset-password': <ResetPasswordPage />,
      'mfa-challenge': <MFAChallengePage />,
      'email-verification': <EmailVerificationPage />,
      'academic-verification': <AcademicVerificationPage />,
      'org-selection': <OrgSelectionPage />,
      'onboarding-welcome': <OnboardingWelcomePage />,
      'onboarding-role': <OnboardingRolePage />,
      'onboarding-workspace': <OnboardingWorkspacePage />,
      'onboarding-invite': <OnboardingInvitePage />,
      'admin-approval': <AdminApprovalPage />,
      'account-locked': <AccountLockedPage />,
    }

    if (authPages[route.page]) {
      return authPages[route.page]
    }

    // App pages (with AppShell)
    if (route.page === 'app') {
      return (
        <AppShell section={route.section}>
          <AppSectionRenderer section={route.section} sub={route.sub} id={route.id} />
        </AppShell>
      )
    }

    // Fallback
    return <PublicLayout><LandingPage /></PublicLayout>
  }

  return (
    <RouterContext.Provider value={routerContext}>
      <AnimatePresence mode="wait">
        <motion.div
          key={route.page === 'app' ? `app-${route.section}` : route.page === 'features' ? `features-${route.slug}` : route.page}
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.15 }}
        >
          {renderPage()}
        </motion.div>
      </AnimatePresence>
    </RouterContext.Provider>
  )
}

// =====================================================================
// NAMED EXPORTS — backward compat for Next.js App Router pages.
//
// Each Next.js route file (app/(group)/path/page.tsx) imports the
// corresponding page component here and renders it inside the appropriate
// layout. The NextRouterProvider (in next-router-provider.tsx) is mounted
// once at the root layout and bridges the legacy RouterContext to
// next/navigation's real useRouter.
// =====================================================================

export {
  // Layout helpers
  DrugOSLogo,
  StatusDot,
  SectionHeading,
  PublicHeader,
  PublicFooter,
  PublicLayout,
  AuthLayout,
  // Public marketing pages
  LandingPage,
  PricingPage,
  AboutPage,
  SecurityPage,
  StatusPage,
  BlogPage,
  ContactPage,
  CareersPage,
  CaseStudiesPage,
  FeaturePage,
  // Auth pages
  LoginPage,
  RegisterPage,
  ForgotPasswordPage,
  ResetPasswordPage,
  MFAChallengePage,
  EmailVerificationPage,
  AcademicVerificationPage,
  OrgSelectionPage,
  OnboardingWelcomePage,
  OnboardingRolePage,
  OnboardingWorkspacePage,
  OnboardingInvitePage,
  AdminApprovalPage,
  AccountLockedPage,
  // App shell + app pages
  AppShell,
  AppDashboard,
  AppSearchPage,
  AppSearchResultsPage,
  AppSectionRenderer,
  CoreScreenBridge,
  CoreScreenSkeleton,
  AppPlaceholderSection,
  // Sidebar nav groups (consumed by AppPlaceholderSection)
  sidebarNavGroups,
}

// Also export the legacy RouterContext + useRouter so the new
// NextRouterProvider can override them. Importers that need the router
// should import from next-router-provider.tsx instead.
export {
  RouterContext,
  useRouter as useDrugOSRouter,
}
