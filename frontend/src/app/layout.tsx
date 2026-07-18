import type { Metadata } from "next";
import { Inter } from "next/font/google";
import { Geist_Mono } from "next/font/google";
import "./globals.css";
import { Toaster } from "@/components/ui/toaster";
import { SessionProvider } from "@/components/drugos/session-provider";
import { ThemeProvider } from "next-themes";
// FE-029 v123 FORENSIC ROOT FIX: wrap the entire app in an ErrorBoundary
// so a render crash in any component shows a recovery UI instead of
// white-screening the whole layout. The boundary catches errors from
// the render tree below it; the recovery UI offers "Try again" (resets
// the boundary's internal state, re-rendering the children) and "Reload
// page" (full window.location.reload for cases where state reset isn't
// enough).
import { ErrorBoundary } from "@/components/error-boundary";
// FE-030 v123 FORENSIC ROOT FIX: wrap async content in <Suspense> so the
// server can stream HTML to the client BEFORE all async data has loaded.
// The fallback is a minimal loading spinner — the user sees the shell
// immediately, then the content streams in as it becomes available.
// Without Suspense, the entire page must render before any byte is sent,
// making the dashboard feel slow even when the backend is fast.
import { Suspense } from "react";

const interSans = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "DrugOS — Autonomous Drug Repurposing Platform",
  description: "AI-powered drug repurposing platform for discovering new therapeutic uses of existing drugs. Search diseases, rank candidates, explore knowledge graphs, and build evidence packages.",
  keywords: ["DrugOS", "drug repurposing", "AI", "knowledge graph", "clinical trials", "pharmaceutical"],
  authors: [{ name: "DrugOS Team" }],
  icons: {
    // FE-030 ROOT FIX: was loaded from https://z-cdn.chatglm.cn/z-ai/static/logo.svg
    // — a third-party CDN. Every page load leaked visitor IPs to an
    // unrelated operator, created an availability dependency on the CDN,
    // and (for SVG favicons in older browsers) was a potential script-
    // injection vector. Bundled locally in /public/logo.svg instead.
    icon: "/logo.svg",
  },
  openGraph: {
    title: "DrugOS — Drug Repurposing Platform",
    description: "AI-powered drug repurposing for rare and complex diseases",
    siteName: "DrugOS",
    type: "website",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body
        className={`${interSans.variable} ${geistMono.variable} antialiased bg-background text-foreground`}
      >
        <ThemeProvider attribute="class" defaultTheme="light" enableSystem disableTransitionOnChange>
          <SessionProvider>
            {/* FE-029 v123: top-level ErrorBoundary catches any render
                crash that propagates up from the page tree. Shows a
                recovery UI with "Try again" / "Reload page" buttons
                instead of white-screening the whole layout. */}
            <ErrorBoundary
              onError={(error, info) => {
                // Operators can wire this to Sentry/Bugsnag/etc. The
                // componentStack is included so the report points to
                // the offending component, not just the error message.
                // For now we just log to stderr (server) / console
                // (client) — the operator can grep for [ErrorBoundary]
                // to find production crashes.
                if (typeof console !== "undefined" && console.error) {
                  console.error("[RootErrorBoundary]", error, info.componentStack);
                }
              }}
            >
              {/* FE-030 v123: top-level Suspense boundary so the server
                  can stream HTML to the client before all async data
                  resolves. The fallback is intentionally minimal —
                  individual page sections should have their own
                  <Suspense> boundaries with more specific fallbacks
                  (skeletons, spinners) for better UX. */}
              <Suspense
                fallback={
                  <div
                    style={{
                      minHeight: "100vh",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      fontFamily: "system-ui, -apple-system, sans-serif",
                      color: "#6B6B80",
                    }}
                  >
                    Loading DrugOS…
                  </div>
                }
              >
                {children}
              </Suspense>
            </ErrorBoundary>
          </SessionProvider>
          <Toaster />
        </ThemeProvider>
      </body>
    </html>
  );
}
