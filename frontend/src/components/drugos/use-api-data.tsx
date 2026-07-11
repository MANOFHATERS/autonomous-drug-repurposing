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
import { api, type ApiError } from '@/lib/api-client';

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
    // The api-client's searchClinicalTrials takes a single `q` string;
    // the underlying API route accepts condition + intervention + pageToken.
    // We build the query string manually to pass all params.
    const qs = new URLSearchParams();
    if (params.condition) qs.set("condition", params.condition);
    if (params.intervention) qs.set("intervention", params.intervention);
    if (params.limit) qs.set("limit", String(params.limit));
    if (params.pageToken) qs.set("pageToken", params.pageToken);
    fetch(`/api/clinical-trials/search?${qs.toString()}`, { credentials: "include" })
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
        return body as Awaited<ReturnType<typeof api.searchClinicalTrials>>;
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
 * Fetch the knowledge graph for a drug or disease via the real
 * /api/knowledge-graph endpoint. Returns 503 if the KG service is not
 * deployed (KG_SERVICE_URL not set) — we surface that honestly.
 */
export function useKnowledgeGraph(params: { drug?: string; disease?: string }) {
  const [state, setState] = useState<
    AsyncState<{ nodes: any[]; edges: any[] }>
  >({ data: null, loading: false, error: null });

  const paramsKey = JSON.stringify(params);
  useEffect(() => {
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
 */
export function useRlCandidates(params: { drug?: string; disease?: string; limit?: number }) {
  const [state, setState] = useState<AsyncState<{ candidates: any[]; source?: string; total?: number }>>({
    data: null,
    loading: false,
    error: null,
  });

  const paramsKey = JSON.stringify(params);
  useEffect(() => {
    if (!params.drug && !params.disease) {
      setState({ data: null, loading: false, error: null });
      return;
    }
    let cancelled = false;
    setState({ data: null, loading: true, error: null });
    fetch(`/api/rl`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params),
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

// We import these here so the icons used by LoadingSpinner/ErrorDisplay are
// always available without each screen importing them separately.
import { RefreshCw, AlertCircle } from 'lucide-react';
