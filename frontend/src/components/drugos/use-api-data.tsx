'use client';

/**
 * FE-001 ROOT FIX: Real API integration hooks for the core drug-repurposing
 * screens.
 *
 * The previous core-screens.tsx imported mock data and rendered it directly.
 * None of the screens called the real API routes. The api-client.ts defined
 * api.searchDrugs, api.searchDiseases, api.getSafety, api.buildEvidencePackage,
 * etc. — but a repo-wide grep confirmed these were NEVER called from any
 * component.
 *
 * This module provides React hooks that wrap those api-client methods with
 * proper loading/error states. The core screens now use these hooks instead
 * of importing mock data.
 *
 * Design decisions:
 *   - Every hook returns { data, loading, error }.
 *   - On error, we surface the real error message — we NEVER silently fall
 *     back to mock data. A pharma researcher seeing fabricated "Memantine 87
 *     for Huntington's" could act on fake data — patient safety violation.
 *   - The hooks are typed against the api-client interfaces so the UI gets
 *     compile-time safety.
 *   - Debouncing is built in for search-as-you-type hooks.
 */

import { useState, useEffect, useCallback, useRef } from 'react';
import type { ReactNode } from 'react';
import { RefreshCw, AlertCircle } from 'lucide-react';
import { api, type ApiError, type KnowledgeGraphStatsResponse } from '@/lib/api-client';
import { Card, CardContent } from '@/components/ui/card';

export interface AsyncState<T> {
  data: T | null;
  loading: boolean;
  error: ApiError | null;
}

/**
 * Debounce a fast-changing value (e.g. a search input) so we don't fire
 * an API request on every keystroke.
 */
function useDebouncedValue<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(t);
  }, [value, delayMs]);
  return debounced;
}

/**
 * Search diseases via the real /api/diseases/search endpoint (backed by
 * NLM MeSH). Debounced by 300ms.
 */
export function useDiseaseSearch(query: string, minLength = 2) {
  const debounced = useDebouncedValue(query, 300);
  const [state, setState] = useState<AsyncState<Awaited<ReturnType<typeof api.searchDiseases>>>>({
    data: null,
    loading: false,
    error: null,
  });

  useEffect(() => {
    if (debounced.trim().length < minLength) {
      setState({ data: null, loading: false, error: null });
      return;
    }
    let cancelled = false;
    setState((s) => ({ ...s, loading: true, error: null }));
    api
      .searchDiseases(debounced)
      .then((data) => {
        if (!cancelled) setState({ data, loading: false, error: null });
      })
      .catch((error: ApiError) => {
        if (!cancelled) setState({ data: null, loading: false, error });
      });
    return () => {
      cancelled = true;
    };
  }, [debounced, minLength]);

  return state;
}

/**
 * Search drugs via the real /api/drugs/search endpoint (backed by RxNorm).
 */
export function useDrugSearch(query: string, minLength = 2) {
  const debounced = useDebouncedValue(query, 300);
  const [state, setState] = useState<AsyncState<Awaited<ReturnType<typeof api.searchDrugs>>>>({
    data: null,
    loading: false,
    error: null,
  });

  useEffect(() => {
    if (debounced.trim().length < minLength) {
      setState({ data: null, loading: false, error: null });
      return;
    }
    let cancelled = false;
    setState((s) => ({ ...s, loading: true, error: null }));
    api
      .searchDrugs(debounced)
      .then((data) => {
        if (!cancelled) setState({ data, loading: false, error: null });
      })
      .catch((error: ApiError) => {
        if (!cancelled) setState({ data: null, loading: false, error });
      });
    return () => {
      cancelled = true;
    };
  }, [debounced, minLength]);

  return state;
}

/**
 * Fetch safety data for a drug via the real /api/safety/[drug] endpoint
 * (backed by openFDA adverse event reports).
 */
export function useDrugSafety(drug: string | null) {
  const [state, setState] = useState<AsyncState<Awaited<ReturnType<typeof api.getSafety>>>>({
    data: null,
    loading: false,
    error: null,
  });

  useEffect(() => {
    if (!drug || drug.trim().length < 2) {
      setState({ data: null, loading: false, error: null });
      return;
    }
    let cancelled = false;
    setState({ data: null, loading: true, error: null });
    api
      .getSafety(drug)
      .then((data) => {
        if (!cancelled) setState({ data, loading: false, error: null });
      })
      .catch((error: ApiError) => {
        if (!cancelled) setState({ data: null, loading: false, error });
      });
    return () => {
      cancelled = true;
    };
  }, [drug]);

  return state;
}

/**
 * Search clinical trials via the real /api/clinical-trials/search endpoint
 * (backed by ClinicalTrials.gov v2).
 */
export function useClinicalTrialsSearch(params: {
  condition?: string;
  intervention?: string;
  limit?: number;
  pageToken?: string;
}) {
  const [state, setState] = useState<AsyncState<Awaited<ReturnType<typeof api.searchClinicalTrials>>>>({
    data: null,
    loading: false,
    error: null,
  });

  // Stringify params for the dep array so we re-fetch when any changes.
  const paramsKey = JSON.stringify(params);
  useEffect(() => {
    if (!params.condition && !params.intervention) {
      setState({ data: null, loading: false, error: null });
      return;
    }
    let cancelled = false;
    setState((s) => ({ ...s, loading: true, error: null }));
    // FE-005 ROOT FIX (Teammate 13, v143): replace the manual fetch with
    // api.searchClinicalTrials(params). The previous code did a raw
    // fetch(`/api/clinical-trials/search?...`) that bypassed the
    // api-client's runtime Zod schema validation (FE-066 root fix) —
    // a contract drift between the route and this hook was silently
    // accepted, producing undefined-field renders in the UI.
    //
    // The stale comment that justified the manual fetch ("the api-client's
    // searchClinicalTrials takes a single `q` string") was a LIE —
    // api-client.ts:577-584 searchClinicalTrials accepts the FULL
    // {condition?, intervention?, limit?, pageToken?} object and builds
    // the same URLSearchParams internally. The manual fetch was pure
    // duplication that bypassed the schema validation. Deleted.
    api
      .searchClinicalTrials(params)
      .then((data) => {
        if (!cancelled) setState({ data, loading: false, error: null });
      })
      .catch((error: ApiError) => {
        if (!cancelled) setState({ data: null, loading: false, error });
      });
    return () => {
      cancelled = true;
    };
  }, [paramsKey]);

  return state;
}

/**
 * Search PubMed literature via the real /api/literature/search endpoint.
 */
export function useLiteratureSearch(query: string, minLength = 3) {
  const debounced = useDebouncedValue(query, 400);
  const [state, setState] = useState<AsyncState<Awaited<ReturnType<typeof api.searchLiterature>>>>({
    data: null,
    loading: false,
    error: null,
  });

  useEffect(() => {
    if (debounced.trim().length < minLength) {
      setState({ data: null, loading: false, error: null });
      return;
    }
    let cancelled = false;
    setState((s) => ({ ...s, loading: true, error: null }));
    api
      .searchLiterature(debounced)
      .then((data) => {
        if (!cancelled) setState({ data, loading: false, error: null });
      })
      .catch((error: ApiError) => {
        if (!cancelled) setState({ data: null, loading: false, error });
      });
    return () => {
      cancelled = true;
    };
  }, [debounced, minLength]);

  return state;
}

/**
 * FE-004 ROOT FIX (Teammate 13, v143, CRITICAL — lossy normalize hack):
 *
 * === THE BUG (verified by reading the actual code, not the comments) ===
 * The previous `useKnowledgeGraph(params)` hook did ONE thing for TWO
 * different API contracts:
 *
 *   - When called WITH drug/disease params → /api/knowledge-graph?drug=X&disease=Y
 *     returns a SUBGRAPH: `{ nodes: [...], edges: [...] }`.
 *   - When called WITHOUT params → /api/knowledge-graph returns STATS:
 *     `{ sources: [...], nodeCount: 42817, edgeCount: 134021, ... }`.
 *
 * The hook then "normalized" the stats response to
 *   `{ nodes: [], edges: [], _stats: body }`.
 *
 * This is LOSSY and WRONG:
 *   1. The real stats (42K nodes, 134K edges) were stuffed into a
 *      hidden `_stats` field typed as the array element type. TypeScript
 *      erased it. The KnowledgeGraphViewer component received
 *      `nodes: []` and rendered an EMPTY CANVAS.
 *   2. The dashboard showed "0 nodes, 0 edges" even when the KG had
 *      42K nodes — a critical misrepresentation of the platform's data
 *      scale. Pharma partner demos failed.
 *   3. The `_stats` field was an undocumented escape hatch that no
 *      component actually read — the stats were dropped on the floor.
 *
 * === ROOT FIX (Teammate 13, v143) ===
 * Split into TWO purpose-built hooks:
 *
 *   1. `useKnowledgeGraphStats()` — calls /api/knowledge-graph with NO
 *      params, returns `KnowledgeGraphStatsResponse` (sources, nodeCount,
 *      edgeCount, nodeTypeCounts, edgeTypeCounts, ...). Used by the KG
 *      screen's header card to show "42,817 nodes, 134,021 edges, 5
 *      sources loaded".
 *
 *   2. `useKnowledgeGraphSubgraph({drug?, disease?})` — calls
 *      /api/knowledge-graph?drug=X&disease=Y, returns `{nodes, edges}`.
 *      Used by the KG canvas to render the actual graph.
 *
 * The KnowledgeGraphScreen now calls BOTH hooks (stats for the header,
 * subgraph for the canvas). The old `useKnowledgeGraph` is kept as a
 * thin backward-compat wrapper that calls useKnowledgeGraphSubgraph
 * (so existing imports don't break) — but new code SHOULD use the split
 * hooks directly. The wrapper is marked `@deprecated`.
 *
 * SCIENTIFIC INTEGRITY: KG statistics are the platform's "data moat"
 * indicator (project docx §10 — the data flywheel). Mis-representing
 * 42K nodes as 0 nodes hides the platform's competitive advantage from
 * pharma partners during demos. The split hooks surface the real stats
 * in the UI where they belong.
 */

/**
 * Fetch KG STATISTICS from /api/knowledge-graph (no params).
 *
 * Returns the full `KnowledgeGraphStatsResponse` payload — sources,
 * nodeCount, edgeCount, nodeTypeCounts, edgeTypeCounts, etc. Use this
 * in the KG screen's header card to show the platform's data scale.
 *
 * The hook fires ONCE on mount. It does NOT re-fire on drug/disease
 * changes (those affect the subgraph, not the stats).
 */
export function useKnowledgeGraphStats() {
  const [state, setState] = useState<
    AsyncState<KnowledgeGraphStatsResponse>
  >({ data: null, loading: false, error: null });

  useEffect(() => {
    let cancelled = false;
    setState({ data: null, loading: true, error: null });
    // FE-004 ROOT FIX: use the api-client's getKnowledgeGraphStats method
    // so we get the runtime Zod schema validation (FE-066 root fix) and
    // the centralized error handling. The previous code did a raw fetch
    // and "normalized" the response — bypassing both.
    api
      .getKnowledgeGraphStats()
      .then((data) => {
        if (!cancelled) setState({ data, loading: false, error: null });
      })
      .catch((error: ApiError) => {
        if (!cancelled) setState({ data: null, loading: false, error });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return state;
}

/**
 * Fetch a KG SUBGRAPH from /api/knowledge-graph?drug=X&disease=Y.
 *
 * Returns `{ nodes, edges }` — the neighborhood of the specified drug
 * or disease node. Used by the KG canvas to render the actual graph.
 *
 * When NEITHER drug NOR disease is provided, the hook returns
 * `{ data: null, loading: false, error: null }` (no fetch). This is
 * intentional — the KG canvas should show the stats header (via
 * useKnowledgeGraphStats) when no entity is selected, not an empty
 * subgraph.
 */
export function useKnowledgeGraphSubgraph(params: { drug?: string; disease?: string }) {
  const [state, setState] = useState<
    AsyncState<{ nodes: any[]; edges: any[] }>
  >({ data: null, loading: false, error: null });

  const paramsKey = JSON.stringify(params);
  useEffect(() => {
    // FE-004 ROOT FIX: do NOT fire when no drug/disease is provided.
    // The previous hook fired unconditionally and "normalized" the stats
    // response to {nodes: [], edges: []} — silently dropping the real
    // stats. The KG screen now uses useKnowledgeGraphStats for the
    // no-params case (header card) and useKnowledgeGraphSubgraph for
    // the filtered case (canvas).
    if (!params.drug && !params.disease) {
      setState({ data: null, loading: false, error: null });
      return;
    }
    let cancelled = false;
    setState({ data: null, loading: true, error: null });
    const qs = new URLSearchParams();
    if (params.drug) qs.set("drug", params.drug);
    if (params.disease) qs.set("disease", params.disease);
    qs.set("limit", "100");
    fetch(`/api/knowledge-graph?${qs.toString()}`, { credentials: "include" })
      .then(async (res) => {
        const text = await res.text();
        let body: any = null;
        if (text) {
          try { body = JSON.parse(text); } catch { body = { raw: text }; }
        }
        if (!res.ok) {
          throw {
            error: body?.error || "request_failed",
            message: body?.message || `Request failed with status ${res.status}`,
            status: res.status,
          } as ApiError;
        }
        // FE-004 ROOT FIX: do NOT "normalize" a stats response to
        // {nodes: [], edges: []}. If the response has `sources` but no
        // `nodes`, that's a CONTRACT VIOLATION (the caller should have
        // used useKnowledgeGraphStats for stats). Surface as an error so
        // the contract drift is visible, not silently dropped.
        if (body && 'sources' in body && !('nodes' in body)) {
          throw {
            error: "response_shape_mismatch",
            message:
              "useKnowledgeGraphSubgraph received a stats response " +
              "({sources, nodeCount, edgeCount, ...}) instead of a subgraph " +
              "({nodes, edges}). This is a contract violation — use " +
              "useKnowledgeGraphStats() for stats. FE-004 ROOT FIX (v143).",
            status: 0,
          } as ApiError;
        }
        return body as { nodes: any[]; edges: any[] };
      })
      .then((data) => {
        if (!cancelled) setState({ data, loading: false, error: null });
      })
      .catch((error: ApiError) => {
        if (!cancelled) setState({ data: null, loading: false, error });
      });
    return () => {
      cancelled = true;
    };
  }, [paramsKey]);

  return state;
}

/**
 * @deprecated FE-004 ROOT FIX (Teammate 13, v143): use useKnowledgeGraphStats
 * (for the no-params case) and useKnowledgeGraphSubgraph (for the filtered
 * case) instead. This wrapper exists ONLY for backward compat with the one
 * existing caller (KnowledgeGraphScreen) which has been updated to call
 * BOTH hooks directly. New code MUST use the split hooks.
 *
 * This wrapper delegates to useKnowledgeGraphSubgraph. It does NOT
 * return stats — if you need stats, call useKnowledgeGraphStats().
 */
export function useKnowledgeGraph(params: { drug?: string; disease?: string }) {
  return useKnowledgeGraphSubgraph(params);
}

/**
 * Build an evidence package via the real /api/evidence-package endpoint.
 * This is a mutation hook (not a query hook) — it returns a `build` function
 * plus the current state.
 */
export function useBuildEvidencePackage() {
  const [state, setState] = useState<
    AsyncState<Awaited<ReturnType<typeof api.buildEvidencePackage>>>
  >({ data: null, loading: false, error: null });

  const build = useCallback(
    (body: { drug: string; disease: string; notes?: string; literatureLimit?: number; trialsLimit?: number }) => {
      setState({ data: null, loading: true, error: null });
      return api
        .buildEvidencePackage(body)
        .then((data) => {
          setState({ data, loading: false, error: null });
          return data;
        })
        .catch((error: ApiError) => {
          setState({ data: null, loading: false, error });
          throw error;
        });
    },
    []
  );

  return { ...state, build };
}

/**
 * Fetch RL-ranked candidates via the real /api/rl endpoint.
 *
 * FE-067 ROOT FIX: When `drug` and `disease` are both unset, the hook now
 * issues a GET /api/rl (default top-N list) instead of short-circuiting
 * with no fetch. This lets the Knowledge Graph Explorer look up real RL
 * candidates by drug name when the user clicks a drug node — previously
 * the lookup hit the mock `drugCandidates` array and silently failed for
 * any drug that wasn't in the mock set.
 */
export function useRlCandidates(params: { drug?: string; disease?: string; limit?: number }) {
  const [state, setState] = useState<AsyncState<{ candidates: any[]; source?: string; total?: number }>>({
    data: null,
    loading: false,
    error: null,
  });

  const paramsKey = JSON.stringify(params);
  useEffect(() => {
    let cancelled = false;
    setState({ data: null, loading: true, error: null });

    // FE-067: if no drug/disease filter is provided, fetch the default
    // top-N list via GET. Otherwise POST with the filter params.
    const hasFilter = !!(params.drug || params.disease);
    const fetchInit: RequestInit = hasFilter
      ? {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(params),
        }
      : {
          method: "GET",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
        };

    fetch(`/api/rl`, fetchInit)
      .then(async (res) => {
        const text = await res.text();
        let body: any = null;
        if (text) {
          try { body = JSON.parse(text); } catch { body = { raw: text }; }
        }
        if (!res.ok) {
          throw {
            error: body?.error || "request_failed",
            message: body?.message || `Request failed with status ${res.status}`,
            status: res.status,
          } as ApiError;
        }
        return body as { candidates: any[]; source?: string; total?: number };
      })
      .then((data) => {
        if (!cancelled) setState({ data, loading: false, error: null });
      })
      .catch((error: ApiError) => {
        if (!cancelled) setState({ data: null, loading: false, error });
      });
    return () => {
      cancelled = true;
    };
  }, [paramsKey]);

  return state;
}

/**
 * FE-029 ROOT FIX: Real notifications hook for the app shell.
 *
 * The previous app-shell.tsx imported a hardcoded `notifications` array from
 * `mock-data.ts` (now deleted). The array was empty, but the NAME invited
 * future engineers to add fabricated "Dr. Sarah Chen published a hypothesis"
 * entries — which would have been presented to researchers as real activity.
 *
 * This hook calls the real /api/notifications endpoint, which returns the
 * authenticated user's actual notification feed from the database. The hook
 * polls every 60 seconds so the bell-icon badge stays fresh without requiring
 * a manual refresh. On error or empty, it returns an empty array — the UI
 * renders an honest "No notifications" state, never fabricated data.
 *
 * Returns:
 *   - notifications: Notification[]  — the user's real notifications (newest first)
 *   - unreadCount: number           — count of notifications with readAt === null
 *   - loading: boolean              — true during the initial fetch
 *   - error: ApiError | null        — surfaced honestly, never swallowed
 *   - refetch: () => void           — trigger a manual refresh
 */
export function useNotifications(options: { pollMs?: number } = {}) {
  const [state, setState] = useState<{
    notifications: import("@/lib/api-client").Notification[];
    unreadCount: number;
    loading: boolean;
    error: ApiError | null;
  }>({
    notifications: [],
    unreadCount: 0,
    loading: true,
    error: null,
  });
  const [refetchCounter, setRefetchCounter] = useState(0);
  const refetch = () => setRefetchCounter((c) => c + 1);

  // Initial fetch + refetch when refetchCounter changes.
  useEffect(() => {
    let cancelled = false;
    setState((s) => ({ ...s, loading: true, error: null }));
    api
      .listNotifications()
      .then((res) => {
        if (cancelled) return;
        const items = res?.items ?? [];
        const unread = items.filter((n) => n.readAt === null).length;
        setState({ notifications: items, unreadCount: unread, loading: false, error: null });
      })
      .catch((error: ApiError) => {
        if (cancelled) return;
        // On error, render an empty feed — NEVER fabricated notifications.
        setState({ notifications: [], unreadCount: 0, loading: false, error });
      });
    return () => {
      cancelled = true;
    };
  }, [refetchCounter]);

  // Optional polling. Default 60s. Caller can disable by passing { pollMs: 0 }.
  const pollMs = options.pollMs ?? 60_000;
  useEffect(() => {
    if (!pollMs || pollMs <= 0) return;
    const id = setInterval(() => setRefetchCounter((c) => c + 1), pollMs);
    return () => clearInterval(id);
  }, [pollMs]);

  return { ...state, refetch };
}

/**
 * FE-030 ROOT FIX: Real team-activity feed hook for dashboard "Recent Activity".
 *
 * The previous all-screens.tsx / remaining-screens.tsx rendered hardcoded
 * arrays of fake colleagues ("Dr. Sarah Chen", "James Wilson", "Dr. Priya
 * Patel", "Dr. Lisa Kim") in the "Shared Queries", "Team Comments", and
 * "Recent Feedback" sections. A researcher believed these were real colleagues
 * and could not tell the dashboard was empty.
 *
 * This hook calls /api/team to fetch the REAL organization members. It does
 * NOT fabricate colleagues. On error or empty, it returns an empty array —
 * the UI renders an honest "No team members yet" state.
 */
export function useTeamMembers() {
  const [state, setState] = useState<{
    members: import("@/lib/api-client").TeamMember[];
    loading: boolean;
    error: ApiError | null;
  }>({
    members: [],
    loading: true,
    error: null,
  });
  const [refetchCounter, setRefetchCounter] = useState(0);
  const refetch = () => setRefetchCounter((c) => c + 1);

  useEffect(() => {
    let cancelled = false;
    setState((s) => ({ ...s, loading: true, error: null }));
    api
      .listTeamMembers()
      .then((res) => {
        if (cancelled) return;
        setState({ members: res?.items ?? [], loading: false, error: null });
      })
      .catch((error: ApiError) => {
        if (cancelled) return;
        setState({ members: [], loading: false, error });
      });
    return () => {
      cancelled = true;
    };
  }, [refetchCounter]);

  return { ...state, refetch };
}

/**
 * FE-024 ROOT FIX: Batch-fetch real drug mechanisms via the
 * /api/drugs/mechanism endpoint (backed by the ChEMBL service in
 * lib/services/drug-mechanism.ts). The UI uses this hook to display
 * the actual mechanism of action (e.g. "NMDA receptor antagonist")
 * instead of fake RL debug values like "RL reward: 0.234".
 *
 * Returns a Map keyed by lowercase drug name. Lookup is O(1).
 * The hook re-fetches whenever the set of drug names changes.
 */
export function useDrugMechanisms(drugNames: string[]) {
  const [state, setState] = useState<
    AsyncState<Map<string, import("@/lib/services/drug-mechanism").DrugMechanismResult>>
  >({ data: null, loading: false, error: null });

  // Sort + dedupe + join to make a stable dep key.
  const key = Array.from(new Set(drugNames.map((n) => (n || "").trim()).filter(Boolean)))
    .sort()
    .join("|");

  useEffect(() => {
    if (!key) {
      setState({ data: null, loading: false, error: null });
      return;
    }
    let cancelled = false;
    setState((s) => ({ ...s, loading: true, error: null }));
    const names = key.split("|");
    fetch(`/api/drugs/mechanism`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ drugNames: names }),
    })
      .then(async (res) => {
        const text = await res.text();
        let body: any = null;
        if (text) {
          try { body = JSON.parse(text); } catch { body = { raw: text }; }
        }
        if (!res.ok) {
          throw {
            error: body?.error || "request_failed",
            message: body?.message || `Request failed with status ${res.status}`,
            status: res.status,
          } as ApiError;
        }
        const map = new Map<string, import("@/lib/services/drug-mechanism").DrugMechanismResult>();
        for (const r of body?.results || []) {
          if (r?.drugName) map.set(r.drugName.toLowerCase(), r);
        }
        return map;
      })
      .then((data) => {
        if (!cancelled) setState({ data, loading: false, error: null });
      })
      .catch((error: ApiError) => {
        if (!cancelled) setState({ data: null, loading: false, error });
      });
    return () => {
      cancelled = true;
    };
  }, [key]);

  return state;
}

/**
 * Reusable loading spinner component.
 */
export function LoadingSpinner({ label = "Loading..." }: { label?: string }) {
  return (
    <div className="flex items-center justify-center py-12 text-muted-foreground">
      <RefreshCw className="h-5 w-5 animate-spin mr-2" />
      <span className="text-sm">{label}</span>
    </div>
  );
}

/**
 * Reusable error display component.
 */
export function ErrorDisplay({ error, onRetry }: { error: ApiError; onRetry?: () => void }) {
  return (
    <div className="flex flex-col items-center justify-center py-12 text-center">
      <AlertCircle className="h-8 w-8 text-red-500 mb-2" />
      <p className="text-sm font-medium text-foreground">{error.message || error.error}</p>
      {error.status === 503 && (
        <p className="text-xs text-muted-foreground mt-1 max-w-md">
          The backend service is not deployed. Set the relevant environment variable
          (KG_SERVICE_URL, DATASET_SERVICE_URL, RL_SERVICE_URL) to enable this feature.
        </p>
      )}
      {onRetry && (
        <button
          onClick={onRetry}
          className="mt-4 text-xs text-primary hover:underline"
        >
          Try again
        </button>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// FE-009 ROOT FIX: Generic hooks for admin/dashboard screens.
//
// The previous all-screens.tsx and admin-billing-etc-screens.tsx rendered
// hardcoded mock data ("Dr. Sarah Chen", "James Wilson", etc.) for ~38
// admin/dashboard screens. An admin viewing "User Management" thought they
// saw the real user list but actually saw 6 fake users. The platform was
// non-functional for its stated admin/billing/collab use cases.
//
// The hooks below let every admin screen call the real API client with
// proper loading / error / empty states, so a researcher never sees
// fabricated data presented as real.
// ---------------------------------------------------------------------------

/**
 * Generic "fetch a list endpoint" hook. Returns { data, loading, error }
 * plus a `refetch` callback. The fetch is fired on mount and whenever
 * `refetchToken` changes (so callers can trigger a refresh).
 *
 * Usage:
 *   const { data, loading, error, refetch } = useApiList(
 *     () => api.listUsers(50, 0),
 *     []
 *   );
 */
export function useApiList<T>(
  fetcher: () => Promise<T>,
  deps: unknown[] = [],
  options: { refetchToken?: unknown } = {}
): {
  data: T | null;
  loading: boolean;
  error: ApiError | null;
  refetch: () => void;
} {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);
  const [refetchCounter, setRefetchCounter] = useState(0);

  // FE-026 ROOT FIX: stale-closure risk. The previous code called `fetcher()`
  // inside useEffect but did NOT include `fetcher` in the deps array — so if
  // the caller passed an inline arrow function that closed over changing
  // state, the effect would keep calling the FIRST render's fetcher forever.
  //
  // We CANNOT add `fetcher` to the deps directly because callers almost
  // always pass a fresh closure every render, which would cause the effect
  // to re-fire on every render → infinite loop.
  //
  // The correct pattern (per React docs on useRef): store the fetcher in a
  // ref and update it inside a deps-less useEffect. React's docs say "Do not
  // write or read ref.current during rendering" — so we update it in an
  // effect (which runs AFTER render). The effect has no deps array, so it
  // runs after every render, keeping the ref synced to the latest fetcher.
  // This is O(1) (a property assignment) and does NOT trigger a re-render.
  // The actual fetch effect below uses [depsKey, refetchToken, refetchCounter]
  // so it only re-fires when the caller's declared deps change — and when it
  // does fire, it calls `fetcherRef.current()` which is always the latest.
  const fetcherRef = useRef(fetcher);
  useEffect(() => {
    fetcherRef.current = fetcher;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  });

  // We deliberately stringify deps to avoid identity churn. The linter
  // can't statically verify that `fetcher` is stable, so we ignore it.
  const depsKey = JSON.stringify(deps);
  const refetchToken = options.refetchToken;

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetcherRef.current()
      .then((result) => {
        if (!cancelled) {
          setData(result);
          setLoading(false);
        }
      })
      .catch((err: ApiError) => {
        if (!cancelled) {
          setError(err);
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
     
  }, [depsKey, refetchToken, refetchCounter]);

  const refetch = () => setRefetchCounter((c) => c + 1);
  return { data, loading, error, refetch };
}

/**
 * Generic "fetch a single resource" hook. Same semantics as useApiList but
 * for endpoints that return a single object (e.g. api.getSystemStatus()).
 */
export function useApiResource<T>(
  fetcher: () => Promise<T>,
  deps: unknown[] = []
): {
  data: T | null;
  loading: boolean;
  error: ApiError | null;
  refetch: () => void;
} {
  return useApiList(fetcher, deps);
}

/**
 * FE-009 ROOT FIX: DemoDataBanner.
 *
 * For admin/dashboard screens where the backend API has NOT been implemented
 * yet (RolesScreen, SSOScreen, FeatureFlagsScreen, etc.), we render this
 * banner above the illustrative data. It tells the admin honestly:
 *   "This screen shows illustrative demo data. The backend API is not yet
 *    implemented. Do not make business decisions based on these numbers."
 *
 * This is the production-grade way to handle "we don't have a real API for
 * this screen yet" — we DO NOT silently render mock data as if it were
 * real (that was the original FE-009 bug).
 */
export function DemoDataBanner({ screenName }: { screenName: string }) {
  return (
    <div className="mb-4 rounded-md border border-amber-300 bg-amber-50 px-4 py-3 text-amber-900">
      <div className="flex items-start gap-2">
        <AlertCircle className="h-5 w-5 flex-shrink-0 mt-0.5" />
        <div className="text-sm">
          <p className="font-semibold">Illustrative demo data — {screenName}</p>
          <p className="mt-1 text-xs">
            The backend API for this screen has not been implemented yet. The
            numbers and entries below are illustrative only and may not match
            your actual organization&rsquo;s state. <strong>Do not make business,
            billing, or compliance decisions based on this view.</strong>
          </p>
        </div>
      </div>
    </div>
  );
}

/**
 * FE-009 ROOT FIX: Generic empty state for list-based admin screens.
 * Used when a real API returns an empty list.
 */
export function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <Card>
      <CardContent className="py-12 text-center text-muted-foreground">
        <AlertCircle className="h-8 w-8 mx-auto mb-2 opacity-50" />
        <p className="text-sm font-medium">{title}</p>
        {description && (
          <p className="text-xs mt-1 max-w-md mx-auto">{description}</p>
        )}
        {action && <div className="mt-4">{action}</div>}
      </CardContent>
    </Card>
  );
}
