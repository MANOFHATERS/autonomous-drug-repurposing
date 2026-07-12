'use client';

import { useState } from 'react';
import { ChevronDown, ChevronUp, ExternalLink, Star, Info } from 'lucide-react';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import { SafetyBadge } from '@/components/drugos/safety-badge';
import { ScoreBar } from '@/components/drugos/score-bar';
import { useDrugMechanisms } from '@/components/drugos/use-api-data';
import type { DrugCandidate } from '@/lib/types';

interface CandidateTableProps {
  candidates: DrugCandidate[];
  onSelect?: (candidate: DrugCandidate) => void;
  onCompare?: (candidate: DrugCandidate) => void;
  showDiseaseColumn?: boolean;
  className?: string;
}

/**
 * FE-024 ROOT FIX: Format RL debug info for the tooltip.
 * Returns an empty string if no debug info is present.
 *
 * This data is for ML engineers debugging the ranker. It is meaningless
 * to a pharma researcher and must NEVER appear in a table column. The
 * "Mechanism" column shows real mechanism-of-action text fetched from
 * ChEMBL via useDrugMechanisms.
 */
function formatRlDebugInfo(c: DrugCandidate): string {
  const d = c.rlDebugInfo;
  if (!d) return '';
  const parts: string[] = [];
  if (typeof d.reward === 'number') parts.push(`reward=${d.reward.toFixed(4)}`);
  if (typeof d.policyProb === 'number') parts.push(`policy_prob=${d.policyProb.toFixed(4)}`);
  if (typeof d.gnnScore === 'number') parts.push(`gnn_score=${d.gnnScore.toFixed(4)}`);
  if (typeof d.rank === 'number') parts.push(`rl_rank=${d.rank}`);
  if (d.source) parts.push(`source=${d.source}`);
  return parts.join(' · ');
}

export function CandidateTable({
  candidates,
  onSelect,
  onCompare,
  showDiseaseColumn = true,
  className = '',
}: CandidateTableProps) {
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [shortlisted, setShortlisted] = useState<Set<string>>(new Set());

  // FE-024 ROOT FIX: Batch-fetch real mechanisms from ChEMBL for every
  // drug in the candidate list. The hook dedupes + caches, so re-renders
  // don't re-fetch.
  const drugNames = candidates.map((c) => c.drugName);
  const mechanismState = useDrugMechanisms(drugNames);
  const mechanismMap = mechanismState.data;

  const toggleExpand = (id: string) => {
    setExpandedId((prev) => (prev === id ? null : id));
  };

  const toggleShortlist = (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    setShortlisted((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  return (
    <TooltipProvider delayDuration={200}>
      <div className={`rounded-lg border border-border overflow-hidden ${className}`}>
        <Table>
          <TableHeader>
            <TableRow className="bg-muted/50 hover:bg-muted/50">
              <TableHead className="w-10"></TableHead>
              <TableHead className="w-10"></TableHead>
              <TableHead>Drug Name</TableHead>
              <TableHead>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <span className="inline-flex items-center gap-1 cursor-help">
                      Composite Score
                      <Info className="h-3 w-3 text-muted-foreground" />
                    </span>
                  </TooltipTrigger>
                  <TooltipContent className="max-w-xs">
                    <p className="font-medium mb-1">Composite Score (Model Output)</p>
                    <p className="text-xs text-muted-foreground">
                      A 0–100 weighted blend of sub-scores: Knowledge Graph (40%),
                      Molecular Similarity (15%), Safety (25%), Clinical Evidence (20%).
                      This is a model output, NOT a statistical confidence interval.
                      Do not interpret &quot;87&quot; as &quot;87% confident this works&quot;.
                    </p>
                  </TooltipContent>
                </Tooltip>
              </TableHead>
              <TableHead>Safety</TableHead>
              <TableHead>Mechanism</TableHead>
              {showDiseaseColumn && <TableHead>Disease</TableHead>}
              <TableHead>Phase</TableHead>
              <TableHead>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <span className="inline-flex items-center gap-1 cursor-help">
                      Model Score
                      <Info className="h-3 w-3 text-muted-foreground" />
                    </span>
                  </TooltipTrigger>
                  <TooltipContent className="max-w-xs">
                    <p className="font-medium mb-1">Model Score (0–100)</p>
                    <p className="text-xs text-muted-foreground">
                      The same composite score shown in the bar — duplicated as a
                      numeric value for sorting and export. Not a probability.
                      The model is not calibrated; do not use this as a statistical
                      confidence measure without Platt scaling or isotonic regression.
                    </p>
                  </TooltipContent>
                </Tooltip>
              </TableHead>
              <TableHead className="w-10"></TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {candidates.length === 0 && (
              <TableRow>
                <TableCell colSpan={showDiseaseColumn ? 10 : 9} className="text-center text-muted-foreground py-12">
                  No candidates to display. Run an RL query to populate this table.
                </TableCell>
              </TableRow>
            )}
            {candidates.map((candidate) => {
              const isExpanded = expandedId === candidate.id;
              const isShortlisted = shortlisted.has(candidate.id);

              // FE-024: real mechanism from ChEMBL, or "—" if unknown / still loading.
              const mechResult = mechanismMap?.get(candidate.drugName.toLowerCase());
              const mechanismDisplay = mechResult?.mechanism
                || candidate.mechanism
                || (mechanismState.loading ? 'Loading…' : '—');
              const rlDebug = formatRlDebugInfo(candidate);

              return (
                <TableRow
                  key={candidate.id}
                  className="cursor-pointer hover:bg-muted/30"
                  onClick={() => onSelect?.(candidate)}
                >
                  <TableCell>
                    <button onClick={(e) => toggleShortlist(candidate.id, e)} className="focus:outline-none">
                      <Star
                        className={`h-4 w-4 transition-colors ${
                          isShortlisted ? 'fill-yellow-400 text-yellow-400' : 'text-muted-foreground hover:text-yellow-400'
                        }`}
                      />
                    </button>
                  </TableCell>
                  <TableCell>
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        toggleExpand(candidate.id);
                      }}
                      className="focus:outline-none"
                    >
                      {isExpanded ? (
                        <ChevronUp className="h-4 w-4 text-muted-foreground" />
                      ) : (
                        <ChevronDown className="h-4 w-4 text-muted-foreground" />
                      )}
                    </button>
                  </TableCell>
                  <TableCell>
                    <div>
                      <div className="font-medium text-foreground">{candidate.drugName}</div>
                      <div className="text-xs text-muted-foreground">{candidate.brandNames?.join(', ')}</div>
                    </div>
                  </TableCell>
                  <TableCell>
                    <ScoreBar score={candidate.compositeScore} size="sm" />
                  </TableCell>
                  <TableCell>
                    <SafetyBadge tier={candidate.safetyTier} />
                  </TableCell>
                  <TableCell>
                    {rlDebug ? (
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <span className="text-sm max-w-[200px] truncate block cursor-help">
                            {mechanismDisplay}
                          </span>
                        </TooltipTrigger>
                        <TooltipContent className="max-w-md">
                          <p className="font-medium mb-1">{candidate.drugName}</p>
                          <p className="text-xs">{mechanismDisplay}</p>
                          {mechResult?.source && (
                            <p className="text-xs text-muted-foreground mt-1">
                              Source: {mechResult.source}
                            </p>
                          )}
                          <div className="border-t border-border mt-2 pt-2">
                            <p className="text-[10px] uppercase tracking-wide text-muted-foreground mb-1">
                              RL Model Debug (not for clinical use)
                            </p>
                            <p className="text-xs font-mono">{rlDebug}</p>
                          </div>
                        </TooltipContent>
                      </Tooltip>
                    ) : (
                      <span className="text-sm max-w-[200px] truncate block">{mechanismDisplay}</span>
                    )}
                  </TableCell>
                  {showDiseaseColumn && (
                    <TableCell>
                      <span className="text-sm">{candidate.diseaseName}</span>
                    </TableCell>
                  )}
                  <TableCell>
                    <Badge variant="outline" className="text-xs">{candidate.clinicalPhase}</Badge>
                  </TableCell>
                  <TableCell>
                    <span className="text-sm font-medium">{candidate.compositeScore}</span>
                  </TableCell>
                  <TableCell>
                    <Button variant="ghost" size="sm" className="h-7 w-7 p-0">
                      <ExternalLink className="h-3.5 w-3.5" />
                    </Button>
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </div>
    </TooltipProvider>
  );
}
