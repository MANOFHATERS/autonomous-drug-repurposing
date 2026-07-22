'use client'

// FE-023 ROOT FIX (Teammate 17 Subagent C): extracted from app-router.tsx
// (originally lines 205-448). Shared layout primitives used by every public
// marketing page and the auth pages. Each component is preserved VERBATIM
// from app-router.tsx — only the import block at the top is new.

import React, { useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Menu, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Separator } from '@/components/ui/separator'
import { useRouter } from '../next-router-provider'
import { useSession } from '../session-provider'

// =====================================================================
// SHARED UI COMPONENTS
// =====================================================================

export function DrugOSLogo({ size = 'md' }: { size?: 'sm' | 'md' | 'lg' }) {
  const s = size === 'sm' ? 'w-7 h-7 text-sm' : size === 'lg' ? 'w-12 h-12 text-xl' : 'w-9 h-9 text-lg'
  return (
    <div className="flex items-center gap-2.5">
      <div className={`${s} rounded-xl bg-gradient-to-br from-[#5B4FCF] to-[#7B6FEF] flex items-center justify-center text-white font-bold shadow-lg shadow-[#5B4FCF]/20`}>
        D
      </div>
      {size !== 'sm' && (
        <span className={`font-bold text-foreground ${size === 'lg' ? 'text-2xl' : 'text-lg'}`}>
          DrugOS
        </span>
      )}
    </div>
  )
}

export function StatusDot({ status }: { status: string }) {
  const c = status === 'operational' || status === 'active' || status === 'healthy' || status === 'Paid' ? 'bg-emerald-500' : status === 'degraded' || status === 'yellow' ? 'bg-amber-500' : 'bg-red-500'
  return <span className={`inline-block w-2.5 h-2.5 rounded-full ${c}`} />
}

export function SectionHeading({ title, subtitle, action }: { title: string; subtitle?: string; action?: React.ReactNode }) {
  return (
    <div className="flex items-end justify-between mb-6">
      <div>
        <h2 className="text-2xl font-bold text-foreground">{title}</h2>
        {subtitle && <p className="text-muted-foreground mt-1">{subtitle}</p>}
      </div>
      {action}
    </div>
  )
}

// =====================================================================
// PUBLIC HEADER & FOOTER
// =====================================================================

export function PublicHeader() {
  const { navigate, route } = useRouter()
  const { user, loading } = useSession()
  const [mobileOpen, setMobileOpen] = useState(false)

  // FE-059 ROOT FIX (Teammate 13, LOW): the previous PublicHeader showed the
  // SAME public marketing nav (Features/Pricing/About/Security/Blog/Careers)
  // to EVERYONE — including authenticated researchers who had already signed
  // in and wanted app navigation, not marketing. It also did not gate admin-
  // only destinations by role. Root fix: when the user is authenticated, show
  // app-focused nav (Dashboard, Search, Knowledge Graph, Reports) so a logged-
  // in researcher reaches their workspace in one click instead of wading
  // through landing-page marketing. When the user is an admin/superadmin, an
  // "Admin" item is appended (role-gated). Unauthenticated visitors still see
  // the marketing nav. This is auth-state + role gating, as the audit asked.
  const isLoggedIn = !loading && !!user
  const isAdmin = isLoggedIn && (user?.role === 'admin' || user?.role === 'superadmin')
  const navItems = isLoggedIn
    ? [
        { label: 'Dashboard', action: () => navigate({ page: 'app', section: 'dashboard' }) },
        { label: 'Search', action: () => navigate({ page: 'app', section: 'search' }) },
        { label: 'Knowledge Graph', action: () => navigate({ page: 'app', section: 'knowledge-graph' }) },
        { label: 'Reports', action: () => navigate({ page: 'app', section: 'reports' }) },
        ...(isAdmin
          ? [{ label: 'Admin', action: () => navigate({ page: 'app', section: 'users' }) }]
          : []),
      ]
    : [
        { label: 'Features', action: () => navigate({ page: 'features', slug: 'disease-search' }) },
        { label: 'Pricing', action: () => navigate({ page: 'pricing' }) },
        { label: 'About', action: () => navigate({ page: 'about' }) },
        { label: 'Security', action: () => navigate({ page: 'security' }) },
        { label: 'Blog', action: () => navigate({ page: 'blog' }) },
        { label: 'Careers', action: () => navigate({ page: 'careers' }) },
      ]

  return (
    <header className="sticky top-0 z-50 bg-white/80 backdrop-blur-xl border-b border-border/50">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex items-center justify-between h-16">
          <button onClick={() => navigate({ page: 'landing' })} className="flex items-center gap-2.5 hover:opacity-80 transition-opacity">
            <DrugOSLogo size="sm" />
            <span className="font-bold text-lg text-foreground">DrugOS</span>
          </button>

          <nav className="hidden md:flex items-center gap-1">
            {navItems.map(item => (
              <button key={item.label} onClick={item.action} className="px-3 py-2 text-sm font-medium text-muted-foreground hover:text-foreground transition-colors rounded-lg hover:bg-accent">
                {item.label}
              </button>
            ))}
          </nav>

          <div className="hidden md:flex items-center gap-3">
            {!loading && user ? (
              <>
                <Button variant="ghost" size="sm" onClick={() => navigate({ page: 'app', section: 'dashboard' })}>
                  Dashboard
                </Button>
                <Button size="sm" onClick={() => navigate({ page: 'app', section: 'search' })} className="bg-[#5B4FCF] hover:bg-[#4B3FBF]">
                  Open Workspace
                </Button>
              </>
            ) : (
              <>
                <Button variant="ghost" size="sm" onClick={() => navigate({ page: 'login' })}>Sign In</Button>
                <Button size="sm" onClick={() => navigate({ page: 'register' })} className="bg-[#5B4FCF] hover:bg-[#4B3FBF]">Start Free</Button>
              </>
            )}
          </div>

          <button className="md:hidden p-2" onClick={() => setMobileOpen(!mobileOpen)}>
            {mobileOpen ? <X className="w-5 h-5" /> : <Menu className="w-5 h-5" />}
          </button>
        </div>
      </div>

      <AnimatePresence>
        {mobileOpen && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            className="md:hidden border-t border-border bg-white"
          >
            <div className="px-4 py-4 space-y-1">
              {navItems.map(item => (
                <button key={item.label} onClick={() => { item.action(); setMobileOpen(false) }} className="block w-full text-left px-3 py-2 text-sm font-medium text-muted-foreground hover:text-foreground hover:bg-accent rounded-lg">
                  {item.label}
                </button>
              ))}
              <Separator className="my-2" />
              {!loading && user ? (
                <>
                  <Button variant="ghost" size="sm" className="w-full justify-start" onClick={() => { navigate({ page: 'app', section: 'dashboard' }); setMobileOpen(false) }}>Dashboard</Button>
                  <Button size="sm" className="w-full bg-[#5B4FCF] hover:bg-[#4B3FBF]" onClick={() => { navigate({ page: 'app', section: 'search' }); setMobileOpen(false) }}>Open Workspace</Button>
                </>
              ) : (
                <>
                  <Button variant="ghost" size="sm" className="w-full justify-start" onClick={() => { navigate({ page: 'login' }); setMobileOpen(false) }}>Sign In</Button>
                  <Button size="sm" className="w-full bg-[#5B4FCF] hover:bg-[#4B3FBF]" onClick={() => { navigate({ page: 'register' }); setMobileOpen(false) }}>Start Free</Button>
                </>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </header>
  )
}

export function PublicFooter() {
  const { navigate } = useRouter()
  const footerSections = [
    {
      title: 'Product',
      links: [
        { label: 'Disease Search', action: () => navigate({ page: 'features', slug: 'disease-search' }) },
        { label: 'Knowledge Graph', action: () => navigate({ page: 'features', slug: 'knowledge-graph' }) },
        { label: 'Safety Profiling', action: () => navigate({ page: 'features', slug: 'safety-profiling' }) },
        { label: 'Evidence Reports', action: () => navigate({ page: 'features', slug: 'evidence-reports' }) },
        { label: 'API & Dev Tools', action: () => navigate({ page: 'features', slug: 'api-developer' }) },
        { label: 'Pricing', action: () => navigate({ page: 'pricing' }) },
      ]
    },
    {
      title: 'Company',
      links: [
        { label: 'About', action: () => navigate({ page: 'about' }) },
        { label: 'Blog', action: () => navigate({ page: 'blog' }) },
        { label: 'Careers', action: () => navigate({ page: 'careers' }) },
        { label: 'Case Studies', action: () => navigate({ page: 'case-studies' }) },
        { label: 'Contact', action: () => navigate({ page: 'contact' }) },
      ]
    },
    {
      title: 'Trust',
      links: [
        { label: 'Security', action: () => navigate({ page: 'security' }) },
        { label: 'Status', action: () => navigate({ page: 'status' }) },
        { label: 'Privacy', action: () => navigate({ page: 'landing' }) },
        { label: 'Terms', action: () => navigate({ page: 'landing' }) },
        { label: 'HIPAA', action: () => navigate({ page: 'security' }) },
      ]
    },
    {
      title: 'Resources',
      links: [
        { label: 'Documentation', action: () => navigate({ page: 'landing' }) },
        { label: 'API Reference', action: () => navigate({ page: 'features', slug: 'api-developer' }) },
        { label: 'Community', action: () => navigate({ page: 'landing' }) },
        { label: 'Changelog', action: () => navigate({ page: 'landing' }) },
      ]
    },
  ]

  return (
    <footer className="bg-white border-t border-border">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-12 lg:py-16">
        <div className="grid grid-cols-2 md:grid-cols-5 gap-8">
          <div className="col-span-2 md:col-span-1">
            <DrugOSLogo size="sm" />
            <p className="mt-3 text-sm text-muted-foreground max-w-xs">
              AI-powered drug repurposing for rare and complex diseases.
            </p>
            <div className="flex items-center gap-3 mt-4">
              <Button variant="outline" size="sm" onClick={() => navigate({ page: 'contact' })}>Book a Demo</Button>
            </div>
          </div>
          {footerSections.map(section => (
            <div key={section.title}>
              <h4 className="text-sm font-semibold text-foreground mb-3">{section.title}</h4>
              <ul className="space-y-2">
                {section.links.map(link => (
                  <li key={link.label}>
                    <button onClick={link.action} className="text-sm text-muted-foreground hover:text-foreground transition-colors">
                      {link.label}
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
        <Separator className="my-8" />
        <div className="flex flex-col sm:flex-row items-center justify-between gap-4 text-sm text-muted-foreground">
          <p>&copy; 2026 DrugOS Corp. All rights reserved.</p>
          <div className="flex items-center gap-4">
            <button onClick={() => navigate({ page: 'status' })} className="flex items-center gap-1.5 hover:text-foreground transition-colors">
              <StatusDot status="operational" /> All systems operational
            </button>
          </div>
        </div>
      </div>
    </footer>
  )
}

export function PublicLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen flex flex-col bg-[#F8F8FA]">
      <PublicHeader />
      <main className="flex-1">{children}</main>
      <PublicFooter />
    </div>
  )
}
