'use client'

// FE-023 ROOT FIX (Teammate 17 Subagent C): extracted from app-router.tsx
// (originally lines 1648-1726). Auth login page. Preserved VERBATIM —
// only the import block at the top is new.

import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Separator } from '@/components/ui/separator'
import { useRouter } from '../../next-router-provider'
import { useSession } from '../../session-provider'
import { api, type ApiError } from '@/lib/api-client'
import { AuthLayout } from './_auth-layout'

export function LoginPage() {
  const { navigate } = useRouter()
  const { refresh } = useSession()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [errorMsg, setErrorMsg] = useState<string | null>(null)

  const handleSubmit = async () => {
    setErrorMsg(null)
    if (!email.trim() || !password) {
      setErrorMsg('Email and password are required')
      return
    }
    setSubmitting(true)
    try {
      await api.login({ email: email.trim(), password })
      await refresh()
      navigate({ page: 'app', section: 'dashboard' })
    } catch (e: any) {
      const err = e as ApiError
      setErrorMsg(err.message || 'Login failed. Check your credentials.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <AuthLayout title="Welcome back" subtitle="Sign in to your DrugOS account">
      <Card>
        <CardContent className="pt-6 space-y-4">
          {errorMsg && (
            <div className="rounded-md bg-[#C0392B]/10 border border-[#C0392B]/30 text-[#C0392B] text-sm px-3 py-2">
              {errorMsg}
            </div>
          )}
          <div>
            <Label htmlFor="login-email">Email</Label>
            <Input
              id="login-email"
              type="email"
              placeholder="you@company.com"
              value={email}
              onChange={e => setEmail(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') handleSubmit() }}
              disabled={submitting}
            />
          </div>
          <div>
            <div className="flex items-center justify-between mb-1">
              <Label htmlFor="login-password">Password</Label>
              <button onClick={() => navigate({ page: 'forgot-password' })} className="text-xs text-[#5B4FCF] hover:underline">Forgot password?</button>
            </div>
            <Input
              id="login-password"
              type="password"
              placeholder="Enter your password"
              value={password}
              onChange={e => setPassword(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') handleSubmit() }}
              disabled={submitting}
            />
          </div>
          <Button className="w-full bg-[#5B4FCF] hover:bg-[#4B3FBF]" onClick={handleSubmit} disabled={submitting}>
            {submitting ? 'Signing in…' : 'Sign In'}
          </Button>
          <Separator />
          <p className="text-center text-sm text-muted-foreground">
            Don&apos;t have an account?{' '}
            <button onClick={() => navigate({ page: 'register' })} className="text-[#5B4FCF] font-medium hover:underline">Sign up</button>
          </p>
          <p className="text-center text-xs text-muted-foreground">
            Demo tip: passwords need 10+ chars, upper + lower + digit + symbol.
          </p>
        </CardContent>
      </Card>
    </AuthLayout>
  )
}
