'use client'

// FE-023 ROOT FIX (Teammate 17 Subagent C): extracted from app-router.tsx
// (originally lines 1728-1827). Auth registration page. Preserved
// VERBATIM — only the import block at the top is new.

import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { useRouter } from '../../next-router-provider'
import { useSession } from '../../session-provider'
import { api, type ApiError } from '@/lib/api-client'
import { AuthLayout } from './_auth-layout'

export function RegisterPage() {
  const { navigate } = useRouter()
  const { refresh } = useSession()
  const [form, setForm] = useState({ firstName: '', lastName: '', email: '', password: '', organization: '', role: '' })
  const [submitting, setSubmitting] = useState(false)
  const [errorMsg, setErrorMsg] = useState<string | null>(null)

  const handleRegister = async () => {
    setErrorMsg(null)
    if (!form.firstName.trim() || !form.email.trim() || !form.password) {
      setErrorMsg('First name, email, and password are required')
      return
    }
    if (!form.role) {
      setErrorMsg('Please select your role')
      return
    }
    setSubmitting(true)
    try {
      await api.register({
        email: form.email.trim(),
        password: form.password,
        name: `${form.firstName} ${form.lastName}`.trim(),
        organizationName: form.organization.trim() || undefined,
        role: form.role,
      })
      await refresh()
      // Skip the onboarding-role step since the user already picked a role
      // during registration. Jump straight to the workspace setup step.
      navigate({ page: 'onboarding-workspace' })
    } catch (e: any) {
      const err = e as ApiError
      setErrorMsg(err.message || 'Registration failed.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <AuthLayout title="Create your account" subtitle="Start discovering new treatments today">
      <Card>
        <CardContent className="pt-6 space-y-4">
          {errorMsg && (
            <div className="rounded-md bg-[#C0392B]/10 border border-[#C0392B]/30 text-[#C0392B] text-sm px-3 py-2">
              {errorMsg}
            </div>
          )}
          <div className="grid grid-cols-2 gap-4">
            <div>
              <Label>First Name</Label>
              <Input placeholder="Manoj" value={form.firstName} onChange={e => setForm({ ...form, firstName: e.target.value })} disabled={submitting} />
            </div>
            <div>
              <Label>Last Name</Label>
              <Input placeholder="Pagadala" value={form.lastName} onChange={e => setForm({ ...form, lastName: e.target.value })} disabled={submitting} />
            </div>
          </div>
          <div>
            <Label>Email</Label>
            <Input type="email" placeholder="you@university.edu" value={form.email} onChange={e => setForm({ ...form, email: e.target.value })} disabled={submitting} />
          </div>
          <div>
            <Label>Password</Label>
            <Input type="password" placeholder="Min 10 chars + upper + lower + digit + symbol" value={form.password} onChange={e => setForm({ ...form, password: e.target.value })} disabled={submitting} />
          </div>
          <div>
            <Label>Organization</Label>
            <Input placeholder="University or Company" value={form.organization} onChange={e => setForm({ ...form, organization: e.target.value })} disabled={submitting} />
          </div>
          <div>
            <Label>Role</Label>
            <Select value={form.role} onValueChange={v => setForm({ ...form, role: v })}>
              <SelectTrigger><SelectValue placeholder="Select your role" /></SelectTrigger>
              <SelectContent>
                <SelectItem value="researcher">Researcher</SelectItem>
                <SelectItem value="data_scientist">Data Scientist</SelectItem>
                <SelectItem value="pi">Principal Investigator</SelectItem>
                <SelectItem value="admin">Admin</SelectItem>
                <SelectItem value="business_dev">Business Development</SelectItem>
                <SelectItem value="developer">Developer</SelectItem>
                <SelectItem value="viewer">Viewer</SelectItem>
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground mt-1">
              Your role determines which sections of the app you can access.
              Admins see everything; researchers see research tools only.
            </p>
          </div>
          <Button className="w-full bg-[#5B4FCF] hover:bg-[#4B3FBF]" onClick={handleRegister} disabled={submitting}>
            {submitting ? 'Creating account…' : 'Create Account'}
          </Button>
          <p className="text-center text-sm text-muted-foreground">
            Already have an account?{' '}
            <button onClick={() => navigate({ page: 'login' })} className="text-[#5B4FCF] font-medium hover:underline">Sign in</button>
          </p>
        </CardContent>
      </Card>
    </AuthLayout>
  )
}
