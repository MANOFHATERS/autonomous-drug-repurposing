/**
 * FE-001 ROOT FIX (v129): Next.js App Router loading.tsx.
 *
 * Shown automatically by Next.js App Router when a route segment is loading
 * (e.g. during server-side data fetch or client-side navigation). This
 * replaces the previous fake router's lack of any loading state — the user
 * now sees an immediate skeleton instead of a frozen screen.
 */
export default function Loading() {
  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        fontFamily: "system-ui, -apple-system, sans-serif",
        color: "#6B6B80",
        flexDirection: "column",
        gap: "16px",
      }}
    >
      <div
        style={{
          width: "48px",
          height: "48px",
          border: "4px solid rgba(91, 79, 207, 0.2)",
          borderTopColor: "#5B4FCF",
          borderRadius: "50%",
          animation: "spin 1s linear infinite",
        }}
      />
      <p style={{ fontSize: "14px", margin: 0 }}>Loading DrugOS…</p>
      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
    </div>
  );
}
