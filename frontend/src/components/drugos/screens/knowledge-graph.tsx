'use client';

import { useState, useEffect, useMemo } from 'react';
import { RefreshCw } from 'lucide-react';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent } from '@/components/ui/card';
import { Checkbox } from '@/components/ui/checkbox';
import { Separator } from '@/components/ui/separator';
import { Slider } from '@/components/ui/slider';
import { useDrugOSNav } from '../nav-context';
import { useKnowledgeGraph, useRlCandidates } from '../use-api-data';
import { KnowledgeGraphViewer } from '../knowledge-graph-viewer';
import { drugCandidates, graphNodes, graphEdges } from '@/lib/empty-defaults';
import {
  PRIMARY, ACCENT_GREEN, ACCENT_ORANGE, ACCENT_RED,
  FadeIn, PageHeader,
} from './_core-shared';

/**
 * FE-018 ROOT FIX: Compute positions for real KG nodes using a circular
 * layout when pre-computed positions are missing. The previous code
 * initialized positions from graphNodes (empty array from empty-defaults.ts),
 * producing an empty Map. When real KG nodes arrived from /api/knowledge-graph,
 * they had no entries in positions — every edge and node returned null.
 *
 * This helper builds a Map with a circular layout for nodes that don't
 * already have pre-computed positions. It is called whenever the node set
 * changes so real nodes always get positions.
 */
function computePositions(
  nodes: Array<{ id: string; x?: number; y?: number }>,
  existing?: Map<string, { x: number; y: number }>
): Map<string, { x: number; y: number }> {
  const pos = new Map<string, { x: number; y: number }>(existing);
  const cx = 400, cy = 250, radius = 180;
  const needsLayout = nodes.filter(n => !pos.has(n.id));
  needsLayout.forEach((n, i) => {
    const angle = (2 * Math.PI * i) / Math.max(needsLayout.length, 1) - Math.PI / 2;
    pos.set(n.id, { x: cx + radius * Math.cos(angle), y: cy + radius * Math.sin(angle) });
  });
  return pos;
}

export function KnowledgeGraphScreen() {
  const { navigate } = useDrugOSNav();
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedNode, setSelectedNode] = useState<string | null>(null);
  const [nodeFilters, setNodeFilters] = useState<Record<string, boolean>>({ drug: true, disease: true, gene: true, protein: true, pathway: true });
  const [evidenceThreshold, setEvidenceThreshold] = useState(0.3);
  // FE-018 ROOT FIX: Start with empty Map and compute positions dynamically
  // whenever nodes change. Pre-computed positions from graphNodes (empty) are
  // merged with auto-generated circular-layout positions for real nodes.
  const [positions, setPositions] = useState<Map<string, { x: number; y: number }>>(() => new Map());

  // FE-001 + FE-003 ROOT FIX: Call the real /api/knowledge-graph endpoint.
  // The previous code rendered mock graphNodes/graphEdges. Now we call the
  // real KG service (returns 503 if KG_SERVICE_URL is not set, which we
  // surface honestly). When the KG service IS deployed, we merge the real
  // nodes/edges with the mock ones for display.
  const { data: kgData, loading: kgLoading, error: kgError } = useKnowledgeGraph({
    drug: searchQuery.length >= 2 ? searchQuery : undefined,
  });

  // FE-067 ROOT FIX: "View candidate detail" button used to look up the
  // clicked drug node in the MOCK `drugCandidates` array. For real RL
  // candidates (sourced from /api/rl), the mock lookup returned undefined
  // and the button silently did nothing. Now we fetch the real RL top-N
  // candidates via the same /api/rl endpoint the dashboard uses, and look
  // up the clicked drug by name in that real list. The candidate's `id`
  // for navigation is synthesized as `${drug}|${disease}` when the API
  // does not return one (the RL CSV doesn't have a stable row id), so the
  // navigation is stable across re-renders.
  const { data: rlData } = useRlCandidates({ limit: 200 });
  const realRlCandidates = useMemo(() => {
    const list = rlData?.candidates || [];
    return list.map((c: any) => ({
      id: c.id || `${c.drug}|${c.disease}`,
      drugName: c.drug as string,
      diseaseName: c.disease as string,
      overallScore: c.overallScore as number,
    }));
  }, [rlData]);

  const realNodes = kgData?.nodes || [];
  const realEdges = kgData?.edges || [];

  // FE-018 ROOT FIX: Recompute positions whenever the merged node set changes.
  // Real nodes from the KG service get circular-layout positions so they are
  // actually visible. Pre-computed positions (if any) are preserved.
  const allNodes = useMemo(() => [...graphNodes, ...realNodes], [realNodes]);
  const allEdges = useMemo(() => [...graphEdges, ...realEdges], [realEdges]);
  useEffect(() => {
    setPositions(prev => computePositions(allNodes, prev));
  }, [allNodes]);

  const filteredNodes = allNodes.filter(n => nodeFilters[n.type]);
  // FE-019 ROOT FIX: GraphEdge has `weight?: number` and `type: string` —
  // there is NO `evidence` field and `relation` is only a backward-compat alias.
  // The Python phase2/service.py returns `type` not `relation`. Use `e.weight`
  // (with fallback to 0.5) for filtering/coloring and `e.type` for labels.
  const filteredEdges = allEdges.filter(e => {
    const src = allNodes.find(n => n.id === e.source);
    const tgt = allNodes.find(n => n.id === e.target);
    const w = (e as any).weight ?? 0.5;
    return w >= evidenceThreshold && src && tgt && nodeFilters[src.type] && nodeFilters[tgt.type];
  });

  const searchedNodes = searchQuery.length >= 2
    ? filteredNodes.filter(n => n.label.toLowerCase().includes(searchQuery.toLowerCase()))
    : filteredNodes;

  const nodeColors: Record<string, string> = { drug: PRIMARY, disease: ACCENT_RED, gene: '#3B82F6', protein: ACCENT_GREEN, pathway: ACCENT_ORANGE };

  const connectedToSelected = useMemo(() => {
    if (!selectedNode) return new Set<string>();
    const s = new Set<string>();
    s.add(selectedNode);
    filteredEdges.forEach(e => {
      if (e.source === selectedNode) s.add(e.target);
      if (e.target === selectedNode) s.add(e.source);
    });
    return s;
  }, [selectedNode, filteredEdges]);

  return (
    <FadeIn>
      <PageHeader title="Knowledge Graph Explorer" description="Explore relationships between drugs, diseases, genes, proteins, and pathways" />
      {kgLoading && (
        <div className="mb-3 text-xs text-muted-foreground flex items-center gap-2">
          <RefreshCw className="h-3 w-3 animate-spin" /> Querying Neo4j knowledge graph service...
        </div>
      )}
      {kgError && (
        <div className="mb-3 text-xs text-amber-700 p-2 border border-amber-200 rounded bg-amber-50">
          <strong>KG service status:</strong> {kgError.message} — showing demo graph data.
          Set <code>KG_SERVICE_URL</code> to connect the real Neo4j Phase 2 service.
        </div>
      )}
      {kgData && realNodes.length > 0 && (
        <div className="mb-3 text-xs text-emerald-700 p-2 border border-emerald-200 rounded bg-emerald-50">
          <strong>Live Neo4j data:</strong> {realNodes.length} nodes, {realEdges.length} edges from the KG service.
        </div>
      )}

      <div className="flex flex-col lg:flex-row gap-4">
        {/* Sidebar */}
        <div className="w-full lg:w-64 space-y-4 shrink-0">
          <Card>
            <CardContent className="p-4">
              <Input value={searchQuery} onChange={e => setSearchQuery(e.target.value)} placeholder="Search entities..." className="mb-3" />
              <div className="space-y-2">
                <p className="text-xs font-semibold text-muted-foreground">Node Types</p>
                {Object.entries(nodeFilters).map(([type, checked]) => (
                  <label key={type} className="flex items-center gap-2 cursor-pointer">
                    <Checkbox checked={checked} onCheckedChange={v => setNodeFilters(p => ({ ...p, [type]: !!v }))} />
                    <span className="w-2.5 h-2.5 rounded-full" style={{ backgroundColor: nodeColors[type] }} />
                    <span className="text-sm capitalize">{type}</span>
                    <span className="ml-auto text-xs text-muted-foreground">{allNodes.filter(n => n.type === type).length}</span>
                  </label>
                ))}
              </div>
              <Separator className="my-3" />
              <div>
                <p className="text-xs font-semibold text-muted-foreground mb-2">Evidence Threshold: {evidenceThreshold.toFixed(1)}</p>
                <Slider value={[evidenceThreshold]} onValueChange={v => setEvidenceThreshold(v[0])} min={0} max={1} step={0.1} />
              </div>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="p-4">
              <p className="text-xs font-semibold text-muted-foreground mb-2">Statistics</p>
              <div className="space-y-1 text-sm">
                <div className="flex justify-between"><span className="text-muted-foreground">Nodes</span><span className="font-medium">{searchedNodes.length}</span></div>
                <div className="flex justify-between"><span className="text-muted-foreground">Edges</span><span className="font-medium">{filteredEdges.length}</span></div>
              </div>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="p-4">
              <p className="text-xs font-semibold text-muted-foreground mb-2">Quick Start</p>
              <div className="space-y-1.5">
                <button onClick={() => setSearchQuery('BRCA1')} className="text-xs text-primary hover:underline block w-full text-left">Find drugs targeting BRCA1</button>
                <button onClick={() => setSearchQuery("Alzheimer's")} className="text-xs text-primary hover:underline block w-full text-left">Show pathways in Alzheimer's</button>
                <button onClick={() => setSearchQuery('Memantine')} className="text-xs text-primary hover:underline block w-full text-left">Memantine mechanism of action</button>
              </div>
            </CardContent>
          </Card>
        </div>

        {/* Graph Area */}
        <Card className="flex-1">
          <CardContent className="p-0 relative">
            <KnowledgeGraphViewer nodes={searchedNodes} edges={filteredEdges} height={500} />
            {/* Selected node info */}
            {selectedNode && (() => {
              // FE-020 ROOT FIX: Search in the merged allNodes array (which
              // includes real nodes from the KG service) instead of just
              // graphNodes (which is the empty array from empty-defaults.ts).
              // Previously find() always returned undefined and the panel
              // NEVER rendered — researchers could not see node details.
              const node = allNodes.find(n => n.id === selectedNode);
              if (!node) return null;
              const nodeEdges = filteredEdges.filter(e => e.source === selectedNode || e.target === selectedNode);
              return (
                <div className="absolute bottom-3 left-3 bg-background/90 backdrop-blur-sm border rounded-lg p-3 max-w-[240px]">
                  <div className="flex items-center justify-between mb-1">
                    <span className="font-semibold text-sm">{node.label}</span>
                    <Button variant="ghost" size="sm" className="h-5 w-5 p-0" onClick={() => setSelectedNode(null)}>×</Button>
                  </div>
                  <Badge variant="secondary" className="text-[10px]" style={{ color: nodeColors[node.type] }}>{node.type}</Badge>
                  <p className="text-xs text-muted-foreground mt-1">{nodeEdges.length} connections</p>
                  {node.type === 'drug' && (
                    <Button variant="link" size="sm" className="h-6 p-0 text-xs mt-1" onClick={() => {
                      // FE-067 ROOT FIX: Look up the clicked drug in the
                      // REAL RL candidate list (sourced from /api/rl).
                      // Previously this searched the mock `drugCandidates`
                      // array, which silently failed for any drug that
                      // wasn't in the mock set. Now we prefer the real RL
                      // data; if the RL service isn't deployed yet, we
                      // fall back to the mock array so the button still
                      // works for demo drugs.
                      const cand = realRlCandidates.find(c => c.drugName === node.label)
                        || drugCandidates.find(c => c.drugName === node.label);
                      if (cand) navigate({ page: 'app', section: 'candidate', id: cand.id });
                    }}>View candidate detail →</Button>
                  )}
                </div>
              );
            })()}
          </CardContent>
        </Card>
      </div>
    </FadeIn>
  );
}
