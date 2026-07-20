'use client';

// FE-029 v129 ROOT FIX (Teammate 13, per-route error boundary):
//
// Next.js App Router has a built-in per-route error-boundary convention:
// any `error.tsx` file in a route segment becomes the error boundary for
// that segment AND its children. Errors thrown during render of the route
// or any descendant are caught here automatically — without white-screening
// the whole layout.
//
// The previous codebase had a hand-rolled `<ErrorBoundary>` class component
// wired into `layout.tsx`, but NO `error.tsx`. That meant:
//   - The class boundary caught errors thrown during the synchronous render
//     of children inside the React tree.
//   - BUT it did NOT catch errors thrown in async server components, errors
//     thrown during route segment loading, or errors thrown in the root
//     layout itself.
//   - The Next.js convention (`error.tsx`) is the canonical, framework-
//     blessed mechanism — it integrates with the router, supports `reset()`
//     to retry the failed segment, and works for both server and client
//     errors.
//
// ROOT FIX: add this `error.tsx` at the root route segment. It catches any
// error thrown by `page.tsx` or any descendant route segment. The UI:
//   1. Tells the user something went wrong (without leaking stack traces
//      in production — `error.digest` is the only identifier shown, which
//      is safe to share with support).
//   2. Offers a "Try again" button that calls `reset()` — this re-renders
//      the failed route segment. If the error was transient (a network
//      race, a stale cache), this recovers without a full page reload.
//   3. Offers a "Reload page" button for cases where `reset()` isn't enough.
//   4. In development, shows the full error message + stack so developers
//      can debug without digging through server logs.
//
// This file is a Client Component (`'use client'`) — Next.js requires
// error boundaries to be client components because they need to handle
// the `error` and `reset` props interactively.
//
// References:
//   - https://nextjs.org/docs/app/api-reference/file-conventions/error
//
// Coexistence with `<ErrorBoundary>` in `layout.tsx`:
//   - The class-based `<ErrorBoundary>` in `layout.tsx` catches errors
//     thrown during the synchronous render of the layout's children tree.
//   - This `error.tsx` catches errors at the route segment level (which
//     includes async server component errors).
//   - They complement each other — neither replaces the other.

import { useEffect } from 'react';
import { Button } from '@/components/ui/button';
import { AlertCircle, RefreshCw, RotateCcw } from 'lucide-react';

interface AppErrorProps {
  error: Error & { digest?: string };
  reset: () => void;
}

export default function AppRouteError({ error, reset }: AppErrorProps) {
  // Log the error to the console (and, in production, to whatever
  // error-reporting service the operator wires up — Sentry/Bugsnag/etc.).
  // The `useEffect` ensures this only runs on the client, after React has
  // committed the error UI, so it doesn't block the initial paint.
  useEffect(() => {
    if (typeof console !== 'undefined' && console.error) {
      console.error('[AppRouteError]', error);
    }
    // Operators: wire up Sentry/Bugsnag/etc. here. Example:
    //   if (typeof window !== 'undefined' && window.Sentry) {
    //     window.Sentry.captureException(error);
    //   }
  }, [error]);

  // Don't leak stack traces in production — only show the digest (a hash
  // that operators can use to look up the full error in their logs).
  const isDev = process.env.NODE_ENV !== 'production';
  const displayMessage = isDev
    ? `${error.name}: ${error.message}`
    : error.digest
      ? `Error ID: ${error.digest}`
      : 'An unexpected error occurred.';

  return (
    <div
      role="alert"
      style={{
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: '2rem',
        background: '#F8F8FA',
        fontFamily: 'system-ui, -apple-system, sans-serif',
      }}
    >
      <div
        style={{
          maxWidth: '640px',
          width: '100%',
          padding: '2.5rem',
          borderRadius: '0.75rem',
          border: '1px solid #E2E1EA',
          background: '#FFFFFF',
          boxShadow: '0 4px 6px -1px rgba(0, 0, 0, 0.05), 0 2px 4px -2px rgba(0, 0, 0, 0.04)',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '1rem' }}>
          <AlertCircle style={{ width: '2rem', height: '2rem', color: '#C0392B' }} />
          <h1 style={{ margin: 0, fontSize: '1.5rem', fontWeight: 700, color: '#1A1A2E' }}>
            Something went wrong
          </h1>
        </div>
        <p style={{ marginTop: 0, marginBottom: '1.5rem', color: '#6B6B80', lineHeight: 1.5 }}>
          The application encountered an unexpected error while rendering this page.
          Your data is safe — only this page failed to render. You can try again, or
          reload the page if the problem persists.
        </p>

        <div
          style={{
            padding: '0.75rem 1rem',
            background: '#F8F8FA',
            borderRadius: '0.5rem',
            border: '1px solid #E2E1EA',
            marginBottom: '1.5rem',
            fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
            fontSize: '0.85rem',
            color: '#3D3870',
            wordBreak: 'break-word',
          }}
        >
          {displayMessage}
          {isDev && error.stack && (
            <pre
              style={{
                marginTop: '0.75rem',
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-word',
                fontSize: '0.75rem',
                color: '#6B6B80',
                maxHeight: '300px',
                overflow: 'auto',
              }}
            >
              {error.stack}
            </pre>
          )}
        </div>

        <div style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap' }}>
          <Button
            type="button"
            onClick={() => reset()}
            style={{
              backgroundColor: '#5B4FCF',
              color: '#FFFFFF',
            }}
          >
            <RotateCcw className="h-4 w-4 mr-1.5" />
            Try again
          </Button>
          <Button
            type="button"
            variant="outline"
            onClick={() => {
              if (typeof window !== 'undefined') {
                window.location.reload();
              }
            }}
          >
            <RefreshCw className="h-4 w-4 mr-1.5" />
            Reload page
          </Button>
        </div>
      </div>
    </div>
  );
}
