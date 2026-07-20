'use client';

// FE-029 v129 ROOT FIX (Teammate 13, GLOBAL error boundary):
//
// Next.js App Router's `global-error.tsx` is the LAST line of defense. It
// catches errors thrown by the ROOT `layout.tsx` itself — errors that
// `error.tsx` CANNOT catch because `error.tsx` is rendered INSIDE the
// root layout (so if the layout crashes, the error boundary can't render).
//
// `global-error.tsx` REPLACES the root `<html>` and `<body>` tags when it
// activates. It MUST therefore render its own `<html>` and `<body>` tags —
// otherwise the page has no document shell.
//
// When does this fire?
//   - The root layout throws during render.
//   - A top-level provider (SessionProvider, ThemeProvider, etc.) throws.
//   - The root layout's import chain has a SyntaxError or TypeError at
//     module-evaluation time.
//   - A server component above the `error.tsx` boundary throws.
//
// Coexistence with `error.tsx` and the class-based `<ErrorBoundary>`:
//   - `<ErrorBoundary>` in `layout.tsx`: catches synchronous render errors
//     inside the React tree below the layout.
//   - `error.tsx`: catches errors at the route segment level (incl. async
//     server component errors below the root layout).
//   - `global-error.tsx` (this file): catches errors AT or ABOVE the root
//     layout. The nuclear option — when nothing else can render.
//
// References:
//   - https://nextjs.org/docs/app/api-reference/file-conventions/global-error

import { useEffect } from 'react';

interface GlobalErrorProps {
  error: Error & { digest?: string };
  reset: () => void;
}

export default function GlobalError({ error, reset }: GlobalErrorProps) {
  useEffect(() => {
    if (typeof console !== 'undefined' && console.error) {
      console.error('[GlobalError]', error);
    }
    // Operators: wire up Sentry/Bugsnag/etc. here. This is the only error
    // boundary that fires when the entire app is broken, so it's the most
    // important one to instrument.
  }, [error]);

  const isDev = process.env.NODE_ENV !== 'production';

  return (
    <html lang="en">
      <body
        style={{
          margin: 0,
          minHeight: '100vh',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          padding: '2rem',
          background: '#F8F8FA',
          fontFamily: 'system-ui, -apple-system, sans-serif',
          color: '#1A1A2E',
        }}
      >
        <div
          role="alert"
          style={{
            maxWidth: '640px',
            width: '100%',
            padding: '2.5rem',
            borderRadius: '0.75rem',
            border: '1px solid #E2E1EA',
            background: '#FFFFFF',
            boxShadow: '0 4px 6px -1px rgba(0, 0, 0, 0.05)',
          }}
        >
          <h1
            style={{
              margin: '0 0 1rem 0',
              fontSize: '1.5rem',
              fontWeight: 700,
              color: '#C0392B',
            }}
          >
            DrugOS failed to load
          </h1>
          <p style={{ marginTop: 0, marginBottom: '1.5rem', color: '#6B6B80', lineHeight: 1.5 }}>
            A critical error prevented the application from starting. This is not
            a transient issue — reloading may not help. Please contact support if
            the problem persists.
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
            {isDev
              ? `${error.name}: ${error.message}`
              : error.digest
                ? `Error ID: ${error.digest}`
                : 'A critical error occurred.'}
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
            <button
              type="button"
              onClick={() => reset()}
              style={{
                padding: '0.5rem 1rem',
                borderRadius: '0.375rem',
                border: '1px solid #5B4FCF',
                background: '#5B4FCF',
                color: '#FFFFFF',
                cursor: 'pointer',
                fontWeight: 600,
                fontFamily: 'inherit',
                fontSize: '0.95rem',
              }}
            >
              Try again
            </button>
            <button
              type="button"
              onClick={() => {
                if (typeof window !== 'undefined') {
                  window.location.reload();
                }
              }}
              style={{
                padding: '0.5rem 1rem',
                borderRadius: '0.375rem',
                border: '1px solid #E2E1EA',
                background: '#FFFFFF',
                color: '#1A1A2E',
                cursor: 'pointer',
                fontFamily: 'inherit',
                fontSize: '0.95rem',
              }}
            >
              Reload page
            </button>
          </div>
        </div>
      </body>
    </html>
  );
}
