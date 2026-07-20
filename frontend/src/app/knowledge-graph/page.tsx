import { AppShell, CoreScreenBridge } from "@/components/drugos/app-router";

export const metadata = { title: "Knowledge Graph — DrugOS" };

/**
 * FE-001 + FE-008 ROOT FIX (v129): real /knowledge-graph route.
 *
 * The KnowledgeGraphScreen renders the canvas-based KnowledgeGraphViewer
 * (1000+ nodes smoothly, pan/zoom/tooltip) instead of the legacy inline SVG.
 */
export default function Page() {
  return (
    <AppShell section="knowledge-graph">
      <CoreScreenBridge section="knowledge-graph" />
    </AppShell>
  );
}
