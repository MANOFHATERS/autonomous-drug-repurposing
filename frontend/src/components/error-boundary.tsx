"use client";

import { Component, ReactNode, ErrorInfo } from "react";

// FE-029 v123 FORENSIC ROOT FIX: Global ErrorBoundary.
//
// The previous app had NO <ErrorBoundary> anywhere. Any render crash
// (a thrown error in any component's render or lifecycle method)
// propagated up to the root <html>, white-screening the ENTIRE layout.
// The user lost all navigation, all session state, all in-progress form
// data. The only recovery was a hard refresh — which usually reproduced
// the crash (because the underlying data was still bad).
//
// ROOT FIX: a React ErrorBoundary component that catches render errors
// and shows a recovery UI with:
//   1. A clear error message ("Something went wrong").
//   2. The error name + message (so the user can screenshot it for support).
//   3. A "Try Again" button that resets the boundary's internal state,
//      re-rendering the children. If the error was transient (a race
//      condition, a fetch that succeeded on retry), this recovers.
//   4. A "Reload Page" button that does a full window.location.reload()
//      for cases where the boundary's state reset isn't enough.
//
// The boundary is a Class component (React requires Class for
// componentDidCatch). It logs the error to console.error AND captures
// it for an external error-reporting service (Sentry, Bugsnag, etc.)
// via the onError prop — operators NEED visibility into production
// crashes. The error is also stamped with the component stack so the
// report points to the offending component, not just the error message.
//
// USAGE:
//   <ErrorBoundary fallback={<CustomFallback />}>
//     <ComponentThatMightCrash />
//   </ErrorBoundary>
//
// When no fallback is provided, a default UI is rendered (see
// DefaultErrorFallback below).

interface ErrorBoundaryProps {
  children: ReactNode;
  fallback?: ReactNode | ((error: Error, reset: () => void) => ReactNode);
  // Optional callback invoked when an error is caught. Use this to send
  // the error to Sentry/Bugsnag/etc. The component stack is included
  // so the report points to the offending component.
  onError?: (error: Error, info: ErrorInfo) => void;
  // Optional reset keys — when any of these change, the boundary resets
  // (useful for resetting on route change so a crash on /page1 doesn't
  // block /page2 from rendering).
  resetKeys?: unknown[];
}

interface ErrorBoundaryState {
  error: Error | null;
}

export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  constructor(props: ErrorBoundaryProps) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    // Update state so the next render shows the fallback UI.
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // Log to console for dev visibility.
    console.error("[ErrorBoundary] caught render error:", error, info);
    // Forward to the onError callback if provided (for Sentry/Bugsnag).
    if (this.props.onError) {
      try {
        this.props.onError(error, info);
      } catch (cbErr) {
        // The error callback itself threw — log but don't fail the render.
        console.error("[ErrorBoundary] onError callback threw:", cbErr);
      }
    }
  }

  componentDidUpdate(prevProps: ErrorBoundaryProps): void {
    // Reset the boundary when any resetKey changes — this lets the
    // parent trigger a reset by changing a key (e.g. the current route
    // path) without manually calling a ref method.
    if (this.state.error && this.props.resetKeys) {
      const changed = this.props.resetKeys.some(
        (k, i) => !Object.is(k, prevProps.resetKeys?.[i]),
      );
      if (changed) {
        this.setState({ error: null });
      }
    }
  }

  reset = (): void => {
    this.setState({ error: null });
  };

  render(): ReactNode {
    if (this.state.error) {
      if (this.props.fallback) {
        if (typeof this.props.fallback === "function") {
          return this.props.fallback(this.state.error, this.reset);
        }
        return this.props.fallback;
      }
      return <DefaultErrorFallback error={this.state.error} reset={this.reset} />;
    }
    return this.props.children;
  }
}

// Default fallback UI — used when no `fallback` prop is provided.
function DefaultErrorFallback({
  error,
  reset,
}: {
  error: Error;
  reset: () => void;
}): ReactNode {
  return (
    <div
      role="alert"
      style={{
        padding: "2rem",
        margin: "2rem auto",
        maxWidth: "640px",
        borderRadius: "0.5rem",
        border: "1px solid #E2E1EA",
        background: "#F8F8FA",
        color: "#1A1A2E",
        fontFamily: "system-ui, -apple-system, sans-serif",
        lineHeight: 1.5,
      }}
    >
      <h2 style={{ marginTop: 0, color: "#C0392B" }}>Something went wrong</h2>
      <p>
        The application encountered an unexpected error while rendering this
        section. Your data is safe — only this section failed to render.
      </p>
      <details style={{ marginTop: "1rem", marginBottom: "1rem" }}>
        <summary style={{ cursor: "pointer", fontWeight: 600 }}>
          Error details
        </summary>
        <pre
          style={{
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
            background: "#FFFFFF",
            padding: "0.75rem",
            borderRadius: "0.25rem",
            border: "1px solid #E2E1EA",
            fontSize: "0.85rem",
            marginTop: "0.5rem",
          }}
        >
          {error.name}: {error.message}
          {error.stack ? `\n\n${error.stack}` : ""}
        </pre>
      </details>
      <div style={{ display: "flex", gap: "0.75rem", flexWrap: "wrap" }}>
        <button
          type="button"
          onClick={reset}
          style={{
            padding: "0.5rem 1rem",
            borderRadius: "0.25rem",
            border: "1px solid #5B4FCF",
            background: "#5B4FCF",
            color: "#FFFFFF",
            cursor: "pointer",
            fontWeight: 600,
          }}
        >
          Try again
        </button>
        <button
          type="button"
          onClick={() => {
            if (typeof window !== "undefined") {
              window.location.reload();
            }
          }}
          style={{
            padding: "0.5rem 1rem",
            borderRadius: "0.25rem",
            border: "1px solid #E2E1EA",
            background: "#FFFFFF",
            color: "#1A1A2E",
            cursor: "pointer",
          }}
        >
          Reload page
        </button>
      </div>
    </div>
  );
}
