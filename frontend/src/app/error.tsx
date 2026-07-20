'use client';

/**
 * FE-001 ROOT FIX (v129) + FE-029 ROOT FIX (v123): Next.js App Router
 * error.tsx with a real recovery UI.
 *
 * This is a client component (Next.js requires error.tsx to be a client
 * component because it uses React's error recovery mechanism). It catches
 * any uncaught error from the route segment below it and shows a recovery
 * UI with "Try again" (resets the error boundary) and "Reload page"
 * (full window.location.reload).
 *
 * This is the route-level equivalent of the root ErrorBoundary in
 * app/layout.tsx. The difference: this catches errors in a specific route
 * segment, so a crash on /drugs/aspirin doesn't take down /dashboard.
 */

import { useEffect } from "react";
import { Button } from "@/components/ui/button";

export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // Log to console for operator visibility (wire to Sentry/Bugsnag in prod).
    if (typeof console !== "undefined" && console.error) {
      console.error("[RouteError]", error, error.digest);
    }
  }, [error]);

  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        fontFamily: "system-ui, -apple-system, sans-serif",
        padding: "24px",
      }}
    >
      <div style={{ maxWidth: "480px", textAlign: "center" }}>
        <div
          style={{
            width: "64px",
            height: "64px",
            margin: "0 auto 24px",
            borderRadius: "50%",
            backgroundColor: "rgba(192, 57, 43, 0.1)",
            color: "#C0392B",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontSize: "32px",
            fontWeight: 700,
          }}
        >
          !
        </div>
        <h2 style={{ fontSize: "24px", fontWeight: 700, color: "#1F1F2E", marginBottom: "12px" }}>
          Something went wrong
        </h2>
        <p style={{ fontSize: "14px", color: "#6B6B80", marginBottom: "24px", lineHeight: 1.5 }}>
          An unexpected error occurred while rendering this page. The error has
          been logged. You can try again or reload the page.
        </p>
        {error.digest && (
          <p style={{ fontSize: "12px", color: "#9B9BA8", marginBottom: "24px", fontFamily: "monospace" }}>
            Error ID: {error.digest}
          </p>
        )}
        <div style={{ display: "flex", gap: "12px", justifyContent: "center" }}>
          <Button onClick={reset} variant="default">
            Try again
          </Button>
          <Button
            onClick={() => {
              if (typeof window !== "undefined") window.location.reload();
            }}
            variant="outline"
          >
            Reload page
          </Button>
        </div>
      </div>
    </div>
  );
}
