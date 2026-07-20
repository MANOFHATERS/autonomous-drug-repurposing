import Link from "next/link";
import { Button } from "@/components/ui/button";

/**
 * FE-001 ROOT FIX (v129): Next.js App Router not-found.tsx.
 *
 * Shown when a user navigates to a URL that doesn't match any route file.
 * Before this fix, the fake router would silently fall back to the landing
 * page for unknown URLs — masking broken links and making debugging harder.
 *
 * With a real 404 page, broken links are visible immediately, and search
 * engines get a proper 404 status code so they don't index invalid pages.
 */
export default function NotFound() {
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
        <p style={{ fontSize: "72px", fontWeight: 800, color: "#5B4FCF", margin: 0, lineHeight: 1 }}>
          404
        </p>
        <h2 style={{ fontSize: "24px", fontWeight: 700, color: "#1F1F2E", margin: "16px 0 12px" }}>
          Page not found
        </h2>
        <p style={{ fontSize: "14px", color: "#6B6B80", marginBottom: "24px", lineHeight: 1.5 }}>
          The page you&apos;re looking for doesn&apos;t exist. It may have been
          moved or deleted, or the URL might be misspelled.
        </p>
        <Link href="/">
          <Button>Back to DrugOS home</Button>
        </Link>
      </div>
    </div>
  );
}
