import type { Metadata } from "next";
import { Inter } from "next/font/google";
import { Geist_Mono } from "next/font/google";
import "./globals.css";
import { Toaster } from "@/components/ui/toaster";
import { SessionProvider } from "@/components/drugos/session-provider";
import { ThemeProvider } from "next-themes";
// FE-029 v123 FORENSIC ROOT FIX: wrap the entire app in an ErrorBoundary
// so a render crash in any component shows a recovery UI instead of
// white-screening the whole layout.
import { ErrorBoundary } from "@/components/error-boundary";
// FE-030 v123 FORENSIC ROOT FIX: wrap async content in <Suspense> so the
// server can stream HTML to the client BEFORE all async data has loaded.
import { Suspense } from "react";
// FE-001 ROOT FIX (v129, hostile-auditor pass): mount the NextRouterProvider
// ONCE at the root. This bridges the legacy in-app RouterContext to the real
// Next.js App Router (next/navigation useRouter / usePathname / useSearchParams),
// so every `navigate({...})` call in the legacy components produces a REAL URL
// path (`/dashboard`, `/drugs/aspirin`) instead of a query string.
// This provider MUST be inside <Suspense> because useSearchParams() requires
// a Suspense boundary in Next.js 16 when used in a client component that's
// rendered inside a server layout.
import { NextRouterProvider } from "@/components/drugos/next-router-provider";

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
            <ErrorBoundary
              onError={(error, info) => {
                if (typeof console !== "undefined" && console.error) {
                  console.error("[RootErrorBoundary]", error, info.componentStack);
                }
              }}
            >
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
                {/* FE-001 v129: NextRouterProvider reads the current URL via
                    next/navigation (usePathname + useSearchParams) and exposes
                    a navigate(r) function that calls router.push(routeToPath(r)).
                    All legacy components that use useRouter() from the in-app
                    RouterContext continue to work — but their navigations now
                    produce real URL paths. */}
                <NextRouterProvider>
                  {children}
                </NextRouterProvider>
              </Suspense>
            </ErrorBoundary>
          </SessionProvider>
          <Toaster />
        </ThemeProvider>
      </body>
    </html>
  );
}
