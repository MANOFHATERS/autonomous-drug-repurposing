'use client';

import { useState } from 'react';
import { ChevronRight, ChevronDown } from 'lucide-react';
import { PathwayViz } from '@/components/drugos/pathway-viz';
import type { PathwayChainItem } from '@/lib/ml-contracts';

/**
 * TM13 ROOT FIX (v132, CRITICAL — Phase 4 → Frontend wiring):
 * PathwayExpander renders the pathway_chain attached to each RL candidate
 * as an expandable "N pathways" cell in the candidate table.
 *
 * BROKEN STATE (what this fixes):
 *   - The Python rl/service.py attached pathway_chain to each candidate
 *     (after the v132 fix), but the frontend had NO component to render
 *     it. The candidate table's Pathway column would have shown raw JSON.
 *   - The previous PathwayViz component accepted a PathwayData prop
 *     (nodes + edges for canvas rendering), NOT a PathwayChainItem[]. So
 *     even if the candidate table tried to pass pathway_chain to
 *     PathwayViz, the types didn't match and nothing would render.
 *
 * ROOT FIX:
 *   1. PathwayExpander takes a PathwayChainItem[] (the pathway_chain
 *      field from RankedHypothesis).
 *   2. Collapsed state shows "N pathways" (or "No pathway data" when
 *      the array is empty).
 *   3. Expanded state renders one PathwayViz per chain item. PathwayViz
 *      was updated to ALSO accept a single PathwayChainItem (in addition
 *      to its existing PathwayData prop), so it can render the chain
 *      format directly.
 *
 * SCIENTIFIC CONTEXT:
 *   The pathway chain is the "biological pathway chain that explains
 *   the prediction" deliverable mandated by project docx §6 (Phase 4
 *   output). It connects the drug to the disease via the multi-hop KG
 *   path: drug → protein → pathway → disease. This is what makes the
 *   AI's reasoning transparent and auditable — a researcher can see
 *   WHY the model ranked this pair highly, not just THAT it did.
 */

interface PathwayExpanderProps {
  pathways: PathwayChainItem[];
  /**
   * Optional className for the outer container. The candidate table
   * passes a max-width so the expanded pathways don't blow out the
   * table layout.
   */
  className?: string;
}

export function PathwayExpander({ pathways, className = '' }: PathwayExpanderProps) {
  const [expanded, setExpanded] = useState(false);

  // Defensive: if pathways is null/undefined (shouldn't happen — the Zod
  // schema defaults to []), treat as empty.
  const chains = Array.isArray(pathways) ? pathways : [];
  const count = chains.length;

  if (count === 0) {
    // No pathway data — render the empty state inline. The candidate
    // table's Pathway column shows this for every candidate when the
    // Python service's pathway_enrichment_available flag is true but
    // the KG had no paths for this pair.
    return (
      <span className={`text-xs text-muted-foreground ${className}`}>
        No pathway data
      </span>
    );
  }

  return (
    <div className={className}>
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          setExpanded((prev) => !prev);
        }}
        className="inline-flex items-center text-blue-600 hover:text-blue-800 dark:text-blue-400 dark:hover:text-blue-300 focus:outline-none focus:underline"
        aria-expanded={expanded}
        aria-label={`Toggle ${count} pathway${count !== 1 ? 's' : ''}`}
      >
        {expanded ? (
          <ChevronDown className="h-3.5 w-3.5 mr-0.5" aria-hidden="true" />
        ) : (
          <ChevronRight className="h-3.5 w-3.5 mr-0.5" aria-hidden="true" />
        )}
        <span className="text-xs font-medium">
          {count} pathway{count !== 1 ? 's' : ''}
        </span>
      </button>
      {expanded && (
        <div className="mt-2 space-y-2">
          {chains.map((chainItem, i) => (
            <PathwayViz
              key={`${chainItem.pathway}-${i}`}
              pathwayChain={chainItem}
            />
          ))}
        </div>
      )}
    </div>
  );
}
