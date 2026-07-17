'use client';

import { useState, useRef } from 'react';
import { Search, X, Clock, ArrowRight } from 'lucide-react';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
// FE-021 ROOT FIX: previously this component imported `diseases` from
// `@/lib/empty-defaults` (an empty array) and filtered it for autocomplete
// suggestions — so suggestions were ALWAYS empty and the autocomplete did
// nothing. The real disease search lives at /api/diseases/search (backed by
// NLM MeSH via lib/services/mesh.ts). We now call it via the
// useDiseaseSearch() hook (debounced 300ms, typed against the real
// DiseaseSearchResult shape).
import { useDiseaseSearch } from '@/components/drugos/use-api-data';

interface DiseaseSearchBarProps {
  onSearch?: (query: string) => void;
  onDiseaseSelect?: (diseaseId: string) => void;
  placeholder?: string;
  className?: string;
}

export function DiseaseSearchBar({
  onSearch,
  onDiseaseSelect,
  placeholder = 'Search diseases, drugs, genes, pathways...',
  className = '',
}: DiseaseSearchBarProps) {
  const [query, setQuery] = useState('');
  const [isFocused, setIsFocused] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  // FE-021: real MeSH-backed disease search (debounced 300ms). Returns
  // { data: { items: DiseaseSearchResult[] } | null, loading, error }.
  const { data: searchResult, loading, error } = useDiseaseSearch(query);
  const suggestions = searchResult?.items ?? [];

  const handleSelect = (diseaseId: string, diseaseName: string) => {
    setQuery(diseaseName);
    setIsFocused(false);
    onDiseaseSelect?.(diseaseId);
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (query.trim()) {
      onSearch?.(query.trim());
      setIsFocused(false);
    }
  };

  const showDropdown = isFocused && (query.length >= 2 || suggestions.length > 0 || loading || !!error);

  return (
    <div className={`relative ${className}`}>
      <form onSubmit={handleSubmit} className="relative">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
        <Input
          ref={inputRef}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onFocus={() => setIsFocused(true)}
          onBlur={() => setTimeout(() => setIsFocused(false), 200)}
          placeholder={placeholder}
          className="pl-10 pr-10 h-11 bg-white border-border focus:border-primary focus:ring-primary"
        />
        {query && (
          <button
            type="button"
            onClick={() => setQuery('')}
            className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
          >
            <X className="h-4 w-4" />
          </button>
        )}
      </form>

      {showDropdown && (
        <div className="absolute z-50 w-full mt-1 bg-popover border border-border rounded-lg shadow-lg overflow-hidden">
          {loading && (
            <div className="p-3 text-sm text-muted-foreground flex items-center gap-2">
              <Clock className="h-3.5 w-3.5 animate-spin" />
              Searching MeSH...
            </div>
          )}
          {error && (
            <div className="p-3 text-sm text-red-600">
              Search failed: {error.message || error.error}
            </div>
          )}
          {!loading && !error && suggestions.length === 0 && query.length >= 2 && (
            <div className="p-3 text-sm text-muted-foreground">
              No MeSH descriptors matched &ldquo;{query}&rdquo;.
            </div>
          )}
          {!loading && !error && suggestions.length > 0 && (
            <div className="p-2">
              <p className="text-xs font-medium text-muted-foreground px-2 mb-1">Diseases (MeSH)</p>
              {suggestions.map((disease) => (
                <button
                  key={disease.descriptorUi}
                  onClick={() => handleSelect(disease.descriptorUi, disease.name)}
                  className="flex items-center justify-between w-full px-2 py-2 text-sm rounded-md hover:bg-accent text-left"
                >
                  <div className="flex-1 min-w-0">
                    <div className="font-medium truncate">{disease.name}</div>
                    {disease.scopeNote && (
                      <div className="text-xs text-muted-foreground line-clamp-1">{disease.scopeNote}</div>
                    )}
                  </div>
                  <div className="flex items-center gap-2 ml-2">
                    {disease.treeNumber && disease.treeNumber.length > 0 && (
                      <Badge variant="secondary" className="text-xs shrink-0">
                        {disease.treeNumber[0]}
                      </Badge>
                    )}
                    <ArrowRight className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
