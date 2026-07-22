'use client'

// FE-023 ROOT FIX (Teammate 17 Subagent C): extracted from app-router.tsx
// (originally lines 1104-1186). Public "System Status" page. Preserved
// VERBATIM — only the import block at the top is new.
//
// The module-level `systemStatus` constant was defined at the top of
// app-router.tsx (lines 114-121) but used only by StatusPage. Moved here
// as a local declaration per hostile-auditor rule 4. The FE-065 comment
// that explained the placeholder has been preserved verbatim.

import { CheckCircle2, AlertTriangle } from 'lucide-react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import { StatusDot } from '../_app-layout'

// FE-065: Empty placeholder — replace with useApiResource(() => api.getSystemStatus()).
// Note: /api/system/status returns { services: { ... }, generatedAt } which is
// a different shape than this array. The StatusPage component must be adapted
// when this is wired up. Until then, an empty array renders an honest empty
// state (no fake "operational" / "degraded" statuses).
const systemStatus: Array<{
  id: string; service: string; status: string; latency: number; uptime: number
}> = []

export function StatusPage() {
  const operationalCount = systemStatus.filter(s => s.status === 'operational').length
  const overallStatus = operationalCount === systemStatus.length ? 'operational' : 'degraded'

  return (
    <div className="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8 py-12 lg:py-20">
      <div className="text-center mb-12">
        <h1 className="text-4xl font-bold text-foreground">System Status</h1>
        <p className="text-lg text-muted-foreground mt-3">Real-time service health monitoring</p>
      </div>

      {/* Overall Status */}
      <Card className={cn('mb-8', overallStatus === 'operational' ? 'border-[#1D9E75]' : 'border-[#D4853A]')}>
        <CardContent className="pt-6 flex items-center gap-4">
          <div className={cn(
            'w-14 h-14 rounded-full flex items-center justify-center',
            overallStatus === 'operational' ? 'bg-[#1D9E75]/10 text-[#1D9E75]' : 'bg-[#D4853A]/10 text-[#D4853A]'
          )}>
            {overallStatus === 'operational' ? <CheckCircle2 className="w-7 h-7" /> : <AlertTriangle className="w-7 h-7" />}
          </div>
          <div>
            <h2 className="text-xl font-bold text-foreground">
              {overallStatus === 'operational' ? 'All Systems Operational' : 'Partial Service Degradation'}
            </h2>
            <p className="text-muted-foreground">Last updated: just now</p>
          </div>
        </CardContent>
      </Card>

      {/* Service List */}
      <Card className="mb-8">
        <CardContent className="pt-6">
          <div className="space-y-4">
            {systemStatus.map(service => (
              <div key={service.service} className="flex items-center justify-between py-3 border-b border-border last:border-0">
                <div className="flex items-center gap-3">
                  <StatusDot status={service.status} />
                  <span className="font-medium text-foreground">{service.service}</span>
                </div>
                <div className="flex items-center gap-6 text-sm">
                  <span className="text-muted-foreground">Latency: {service.latency}</span>
                  <span className="text-muted-foreground">Uptime: {service.uptime}%</span>
                  <Badge variant={service.status === 'operational' ? 'secondary' : 'destructive'} className="text-xs">
                    {service.status}
                  </Badge>
                </div>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      {/* Incident History */}
      <Card>
        <CardHeader>
          <CardTitle>Incident History</CardTitle>
          <CardDescription>Recent incidents and resolutions</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="space-y-4">
            {[
              { date: 'Jun 8, 2026', title: 'Report Generator Degraded Performance', status: 'Resolved', duration: '2h 15m' },
              { date: 'May 22, 2026', title: 'API Gateway Intermittent 503 Errors', status: 'Resolved', duration: '45m' },
              { date: 'May 10, 2026', title: 'Scheduled Maintenance - Knowledge Graph Reindex', status: 'Completed', duration: '4h' },
            ].map(incident => (
              <div key={incident.title} className="flex items-start gap-3 py-3 border-b border-border last:border-0">
                <CheckCircle2 className="w-5 h-5 text-[#1D9E75] shrink-0 mt-0.5" />
                <div className="flex-1">
                  <p className="font-medium text-foreground">{incident.title}</p>
                  <div className="flex items-center gap-3 mt-1 text-sm text-muted-foreground">
                    <span>{incident.date}</span>
                    <span>Duration: {incident.duration}</span>
                    <Badge variant="secondary" className="text-xs">{incident.status}</Badge>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
