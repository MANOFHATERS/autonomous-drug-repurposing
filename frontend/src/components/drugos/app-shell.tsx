'use client'

// FE-023 ROOT FIX (Teammate 17 Subagent C): extracted from app-router.tsx
// (originally lines 2236-2679). The authenticated AppShell layout: left
// sidebar with role-gated nav groups, top header with breadcrumbs /
// search / notifications / user menu, and the main content area.
// Also exports `sidebarNavGroups` — the array of sidebar nav items that
// other components (AppPlaceholderSection) consume.
//
// Preserved VERBATIM — only the import block at the top is new. The
// sidebarNavGroups array is exported alongside AppShell so consumers
// (app-placeholder.tsx) can import it directly.

import React, { useState, useEffect } from 'react'
import {
  Search, Shield, Database, Users, CreditCard, Settings, HelpCircle, ChevronDown, ChevronRight,
  Bell, Menu, TrendingUp, Code, Lock, LayoutDashboard,
  FileText, Key, BookOpen,
  Star, Activity,
  Network, FlaskConical,
  Bookmark,
  FolderKanban, Share2, MessageSquare,
  CheckCircle2, GitBranch, BarChart3,
  Eye, Scale, ShieldCheck, GitFork, GitCommit, Target, Flag,
  LogOut, User,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Badge } from '@/components/ui/badge'
import { Avatar, AvatarFallback } from '@/components/ui/avatar'
import {
  Breadcrumb, BreadcrumbItem, BreadcrumbLink, BreadcrumbList,
  BreadcrumbPage, BreadcrumbSeparator
} from '@/components/ui/breadcrumb'
import {
  Sheet, SheetContent, SheetTitle
} from '@/components/ui/sheet'
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel,
  DropdownMenuSeparator, DropdownMenuTrigger
} from '@/components/ui/dropdown-menu'
import { cn } from '@/lib/utils'
import { useRouter } from './next-router-provider'
import { useSession } from './session-provider'
import { api } from '@/lib/api-client'
import { canAccessSection, roleLabel } from '@/lib/rbac'
import {
  useNotifications as useNotificationsFeed,
} from '@/components/drugos/use-api-data'
import { DrugOSLogo } from './screens/_app-layout'

// =====================================================================
// APP SHELL (Authenticated Layout)
// =====================================================================

export const sidebarNavGroups = [
  {
    label: 'Overview',
    items: [
      { id: 'dashboard', label: 'Dashboard', icon: LayoutDashboard },
      { id: 'pipeline', label: 'Pipeline', icon: GitBranch },
      { id: 'analytics', label: 'Analytics', icon: BarChart3 },
    ]
  },
  {
    label: 'Research',
    items: [
      { id: 'search', label: 'Disease Search', icon: Search },
      { id: 'knowledge-graph', label: 'Knowledge Graph', icon: Network },
      { id: 'clinical-trials', label: 'Clinical Trials', icon: FlaskConical },
      { id: 'safety', label: 'Safety', icon: Shield },
    ]
  },
  {
    label: 'Evidence',
    items: [
      { id: 'evidence-builder', label: 'Evidence Builder', icon: FileText },
      { id: 'reports', label: 'Reports', icon: FileText },
      { id: 'saved-queries', label: 'Saved Queries', icon: Bookmark },
      { id: 'shortlists', label: 'Shortlists', icon: Star },
    ]
  },
  {
    label: 'Team',
    items: [
      { id: 'team', label: 'Team Members', icon: Users },
      { id: 'projects', label: 'Projects', icon: FolderKanban },
      { id: 'shared-queries', label: 'Shared Queries', icon: Share2 },
      { id: 'annotations', label: 'Annotations', icon: MessageSquare },
    ]
  },
  {
    label: 'Data',
    items: [
      { id: 'data-sources', label: 'Data Sources', icon: Database },
      { id: 'graph-stats', label: 'Graph Statistics', icon: BarChart3 },
      { id: 'quality', label: 'Quality', icon: CheckCircle2 },
    ]
  },
  {
    label: 'Billing',
    items: [
      { id: 'subscription', label: 'Subscription', icon: CreditCard },
      { id: 'usage', label: 'Usage', icon: Activity },
      { id: 'deals', label: 'Deals', icon: TrendingUp },
      { id: 'invoices', label: 'Invoices', icon: FileText },
    ]
  },
  {
    label: 'Admin',
    items: [
      { id: 'users', label: 'Users', icon: Users },
      { id: 'roles', label: 'Roles', icon: Shield },
      { id: 'sso', label: 'SSO', icon: Key },
      { id: 'audit-logs', label: 'Audit Logs', icon: FileText },
      { id: 'feature-flags', label: 'Feature Flags', icon: Flag },
    ]
  },
  {
    label: 'Developer',
    items: [
      { id: 'api-docs', label: 'API Docs', icon: BookOpen },
      { id: 'api-keys', label: 'API Keys', icon: Key },
      { id: 'playground', label: 'Playground', icon: Code },
      { id: 'webhooks', label: 'Webhooks', icon: GitFork },
    ]
  },
  {
    label: 'Settings',
    items: [
      { id: 'profile', label: 'Profile', icon: User },
      { id: 'security', label: 'Security', icon: Lock },
      { id: 'notifications', label: 'Notifications', icon: Bell },
      { id: 'preferences', label: 'Preferences', icon: Settings },
    ]
  },
  {
    label: 'Legal',
    items: [
      { id: 'privacy', label: 'Privacy Policy', icon: Eye },
      { id: 'terms', label: 'Terms of Service', icon: Scale },
      { id: 'compliance', label: 'Compliance', icon: ShieldCheck },
    ]
  },
  {
    label: 'Support',
    items: [
      { id: 'help-center', label: 'Help Center', icon: HelpCircle },
      { id: 'tickets', label: 'Support Tickets', icon: FileText },
      { id: 'system-status', label: 'System Status', icon: Activity },
    ]
  },
  {
    label: 'Investor',
    items: [
      { id: 'investor-dashboard', label: 'Dashboard', icon: TrendingUp },
      { id: 'cap-table', label: 'Cap Table', icon: BarChart3 },
    ]
  },
  {
    label: 'More',
    items: [
      { id: 'changelog', label: 'Changelog', icon: GitCommit },
      { id: 'roadmap', label: 'Roadmap', icon: Target },
      { id: 'feedback', label: 'Feedback', icon: MessageSquare },
    ]
  },
]

export function AppShell({ children, section }: { children: React.ReactNode; section: string }) {
  const { navigate } = useRouter()
  const { user, loading, signOut, organizations, activeOrganizationId } = useSession()
  const [sidebarOpen, setSidebarOpen] = useState(true)
  const [mobileOpen, setMobileOpen] = useState(false)
  const [expandedGroups, setExpandedGroups] = useState<string[]>(['Overview', 'Research', 'Evidence', 'Team', 'Billing', 'Admin', 'Developer', 'Settings'])
  const [showNotifs, setShowNotifs] = useState(false)
  const [headerSearch, setHeaderSearch] = useState('')

  // FE-028: Real notification feed (polls every 60s). Replaces the empty
  // notifData placeholder so the bell badge and dropdown show real data.
  const { notifications: notifData, unreadCount: unreadNotifs, refetch: refetchNotifs } = useNotificationsFeed({ pollMs: 60_000 })

  const handleMarkNotifRead = async (id: string) => {
    try { await api.markNotificationRead(id) } catch { /* surfaced by next poll */ }
    refetchNotifs()
  }

  // Auth guard: if session resolves and there's no user, bounce to login.
  // FE-045 v123 FORENSIC ROOT FIX: the previous version ONLY called
  // `navigate({ page: 'login' })` which sets React state but does NOT
  // update the browser URL. The browser's URL bar still showed the
  // original URL (e.g. /?page=app&section=users), so the user could
  // bookmark/share a deep link to a protected section. When they
  // revisited, the URL bar showed the protected section — the AppShell
  // auth guard would redirect to login, but the URL was misleading.
  // Worse, server-side middleware cannot distinguish "user is on
  // login page" from "user is on admin page" because the URL doesn't
  // change — so middleware-based RBAC per-route is impossible with
  // the current client-side-only router.
  //
  // ROOT FIX: in addition to the React-state navigate(), also push
  // the new route to the browser history via history.replaceState
  // so the URL bar updates. This doesn't make middleware RBAC possible
  // (the router is still client-side), but it does make the URL bar
  // accurate so users see "?page=login" instead of "?page=app&section=users"
  // when they're bounced to the login screen. The full fix (moving to
  // Next.js App Router pages with server-side RBAC) is a separate
  // architectural change tracked in the roadmap.
  useEffect(() => {
    if (!loading && !user) {
      navigate({ page: 'login' })
      // Update the URL bar so it reflects the actual page the user is on.
      // history.replaceState doesn't trigger a navigation — it just
      // updates the URL bar in place. We do this AFTER navigate() so
      // the React state has already updated (in case any code reads
      // the URL bar to determine the current route — none currently do,
      // but this is defensive).
      if (typeof window !== 'undefined' && window.history) {
        try {
          const newUrl = `${window.location.pathname}?page=login`
          window.history.replaceState({ page: 'login' }, '', newUrl)
        } catch (_historyErr) {
          // Some browsers (Safari in private mode) throw on history
          // API calls. The React-state navigate() already happened, so
          // the user IS on the login page — just the URL bar is stale.
          // Not a security issue, just a cosmetic one.
        }
      }
    }
  }, [loading, user, navigate])

  // While session is loading, show a small splash so we don't flash the login
  // page for users who actually have a valid cookie.
  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-[#F8F8FA]">
        <div className="text-center">
          <div className="w-12 h-12 mx-auto mb-4 rounded-full border-4 border-[#5B4FCF]/30 border-t-[#5B4FCF] animate-spin" />
          <p className="text-sm text-muted-foreground">Loading your workspace…</p>
        </div>
      </div>
    )
  }

  if (!user) {
    // The useEffect above will redirect; render nothing in the meantime.
    return null
  }

  const userInitials = (user.name || user.email || '?')
    .split(/[\s@.]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((s: string) => s[0]?.toUpperCase())
    .join('') || user.email[0]?.toUpperCase()
  const activeOrg = organizations.find(o => o.id === activeOrganizationId) || organizations[0]

  const toggleGroup = (label: string) => {
    setExpandedGroups(prev => prev.includes(label) ? prev.filter(g => g !== label) : [...prev, label])
  }

  // Get current section label
  const currentLabel = sidebarNavGroups.flatMap(g => g.items).find(i => i.id === section)?.label || section

  const handleSignOut = async () => {
    await signOut()
    navigate({ page: 'landing' })
  }

  const sidebarContent = (
    <div className="flex flex-col h-full">
      <div className="h-14 flex items-center gap-2.5 px-4 border-b border-border shrink-0">
        <button onClick={() => navigate({ page: 'landing' })} className="flex items-center gap-2.5 hover:opacity-80 transition-opacity">
          <DrugOSLogo size="sm" />
          {sidebarOpen && <span className="font-bold text-foreground">DrugOS</span>}
        </button>
      </div>

      {sidebarOpen && (
        <div className="px-3 pt-3">
          <div className="relative">
            <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
            <input
              value={headerSearch}
              onChange={e => setHeaderSearch(e.target.value)}
              placeholder="Search..."
              className="w-full pl-9 pr-3 py-1.5 text-sm border border-border rounded-lg bg-accent focus:outline-none focus:ring-1 focus:ring-primary/30"
            />
          </div>
        </div>
      )}

      <div className="flex-1 py-2 overflow-y-auto scrollbar-drugos">
        <div className="space-y-0.5 px-2">
          {sidebarNavGroups.map(group => {
            // Filter out sections the current user's role cannot access.
            const visibleItems = group.items.filter(item => canAccessSection(user.role, item.id))
            // Hide the whole group header if no items are visible.
            if (visibleItems.length === 0) return null
            const isExpanded = expandedGroups.includes(group.label)
            return (
              <div key={group.label}>
                <button
                  onClick={() => toggleGroup(group.label)}
                  className="w-full flex items-center gap-2 px-2 py-1.5 text-xs font-semibold text-muted-foreground uppercase tracking-wider hover:text-foreground transition-colors"
                >
                  {sidebarOpen && <span className="flex-1 text-left">{group.label}</span>}
                  {sidebarOpen && (isExpanded ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />)}
                </button>
                {isExpanded && sidebarOpen && (
                  <div className="space-y-0.5 pb-1">
                    {visibleItems.map(item => {
                      const Icon = item.icon
                      const isActive = section === item.id
                      return (
                        <button
                          key={item.id}
                          onClick={() => { navigate({ page: 'app', section: item.id }); setMobileOpen(false) }}
                          className={cn(
                            'w-full flex items-center gap-2.5 px-3 py-1.5 text-sm rounded-md transition-colors',
                            isActive ? 'bg-[#5B4FCF]/10 text-[#5B4FCF] font-medium' : 'text-muted-foreground hover:text-foreground hover:bg-accent'
                          )}
                        >
                          <Icon className="w-4 h-4 shrink-0" />
                          <span className="truncate">{item.label}</span>
                        </button>
                      )
                    })}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      </div>

      <div className="border-t border-border p-3">
        <div className="text-[10px] text-muted-foreground text-center">
          DrugOS v0.3.0 · {roleLabel(user.role)} · © 2026
        </div>
      </div>
    </div>
  )

  return (
    <div className="min-h-screen flex bg-[#F8F8FA]">
      {/* Mobile overlay */}
      {mobileOpen && <div className="fixed inset-0 bg-black/40 z-40 lg:hidden" onClick={() => setMobileOpen(false)} />}

      {/* Desktop Sidebar */}
      <aside className={cn(
        'hidden lg:flex flex-col border-r border-border bg-card transition-all duration-200 shrink-0',
        sidebarOpen ? 'w-64' : 'w-16'
      )}>
        {sidebarContent}
      </aside>

      {/* Mobile Sidebar */}
      <Sheet open={mobileOpen} onOpenChange={setMobileOpen}>
        <SheetContent side="left" className="w-72 p-0">
          <SheetTitle className="sr-only">Navigation Menu</SheetTitle>
          {sidebarContent}
        </SheetContent>
      </Sheet>

      {/* Main Area */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Header */}
        <header className="sticky top-0 z-30 h-14 border-b border-border bg-card/95 backdrop-blur-sm flex items-center px-4 gap-3">
          <Button variant="ghost" size="sm" className="lg:hidden h-8 w-8 p-0" onClick={() => setMobileOpen(true)}>
            <Menu className="h-5 w-5" />
          </Button>
          <Button variant="ghost" size="sm" className="hidden lg:flex h-8 w-8 p-0" onClick={() => setSidebarOpen(!sidebarOpen)}>
            <Menu className="h-4 w-4" />
          </Button>

          <Breadcrumb className="hidden sm:flex">
            <BreadcrumbList>
              <BreadcrumbItem>
                <BreadcrumbLink onClick={() => navigate({ page: 'app', section: 'dashboard' })} className="cursor-pointer">DrugOS</BreadcrumbLink>
              </BreadcrumbItem>
              <BreadcrumbSeparator />
              <BreadcrumbItem>
                <BreadcrumbPage>{currentLabel}</BreadcrumbPage>
              </BreadcrumbItem>
            </BreadcrumbList>
          </Breadcrumb>

          <div className="flex-1" />

          <div className="flex items-center gap-2">
            {/* Search */}
            <div className="hidden md:flex items-center relative">
              <Search className="w-4 h-4 absolute left-3 text-muted-foreground" />
              <Input
                value={headerSearch}
                onChange={e => setHeaderSearch(e.target.value)}
                onKeyDown={e => {
                  if (e.key === 'Enter' && headerSearch.trim().length >= 2) {
                    navigate({ page: 'app', section: 'search', sub: 'results', id: headerSearch.trim() })
                    setHeaderSearch('')
                  }
                }}
                placeholder="Search diseases..."
                className="pl-9 w-56 h-8 text-sm"
              />
            </div>

            {/* Notifications */}
            <DropdownMenu open={showNotifs} onOpenChange={setShowNotifs}>
              <DropdownMenuTrigger asChild>
                <Button variant="ghost" size="sm" className="h-8 w-8 p-0 relative">
                  <Bell className="h-4 w-4" />
                  {unreadNotifs > 0 && (
                    <span className="absolute -top-0.5 -right-0.5 h-4 w-4 rounded-full bg-[#C0392B] text-white text-[10px] font-bold flex items-center justify-center">
                      {unreadNotifs}
                    </span>
                  )}
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-80">
                <DropdownMenuLabel className="flex items-center justify-between">
                  Notifications
                  <Badge variant="secondary" className="text-[10px]">{unreadNotifs} new</Badge>
                </DropdownMenuLabel>
                <DropdownMenuSeparator />
                {notifData.length === 0 ? (
                  <div className="px-4 py-6 text-center text-sm text-muted-foreground">
                    No notifications yet.
                  </div>
                ) : (
                  notifData.slice(0, 5).map(n => (
                    <DropdownMenuItem
                      key={n.id}
                      className="flex flex-col items-start gap-1 p-3 cursor-pointer"
                      onClick={() => { if (!n.readAt) handleMarkNotifRead(n.id) }}
                    >
                      <div className="flex items-center gap-2 w-full">
                        <span className={cn(
                          'h-2 w-2 rounded-full shrink-0',
                          n.type === 'success' && 'bg-[#1D9E75]',
                          n.type === 'warning' && 'bg-[#D4853A]',
                          n.type === 'error' && 'bg-[#C0392B]',
                          n.type === 'info' && 'bg-[#5B4FCF]'
                        )} />
                        <span className="text-sm font-medium truncate">{n.title}</span>
                        {!n.readAt && <Badge className="ml-auto text-[9px] h-4">New</Badge>}
                      </div>
                      <span className="text-xs text-muted-foreground line-clamp-1">{n.body}</span>
                    </DropdownMenuItem>
                  ))
                )}
              </DropdownMenuContent>
            </DropdownMenu>

            {/* User Menu */}
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="ghost" size="sm" className="h-8 gap-2 px-2">
                  <Avatar className="h-6 w-6">
                    <AvatarFallback className="bg-[#5B4FCF] text-white text-[10px]">{userInitials}</AvatarFallback>
                  </Avatar>
                  <span className="hidden sm:inline text-sm font-medium">{user.name || user.email}</span>
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-56">
                <DropdownMenuLabel>
                  <div className="flex flex-col">
                    <span>{user.name || 'User'}</span>
                    <span className="text-xs font-normal text-muted-foreground">{user.email}</span>
                    {activeOrg && (
                      <span className="text-[10px] mt-1 text-[#5B4FCF] font-medium uppercase tracking-wide">{activeOrg.name} · {activeOrg.plan}</span>
                    )}
                  </div>
                </DropdownMenuLabel>
                <DropdownMenuSeparator />
                <DropdownMenuItem onClick={() => navigate({ page: 'app', section: 'profile' })}>
                  <User className="mr-2 h-4 w-4" /> Profile
                </DropdownMenuItem>
                <DropdownMenuItem onClick={() => navigate({ page: 'app', section: 'preferences' })}>
                  <Settings className="mr-2 h-4 w-4" /> Settings
                </DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem onClick={handleSignOut}>
                  <LogOut className="mr-2 h-4 w-4" /> Sign Out
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </header>

        {/* Page Content */}
        <main className="flex-1 overflow-auto p-4 md:p-6">
          {children}
        </main>
      </div>
    </div>
  )
}
