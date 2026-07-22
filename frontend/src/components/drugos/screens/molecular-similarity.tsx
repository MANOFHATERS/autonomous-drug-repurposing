'use client';

import { Atom } from 'lucide-react';
import { Card, CardContent } from '@/components/ui/card';
import { PageHeader, FadeIn } from './_core-shared';

export function MolecularSimilarityScreen() {
  return (
    <FadeIn>
      <PageHeader title="Molecular Similarity Search" description="Find drugs with similar molecular structures" />
      <Card>
        <CardContent className="py-16 text-center">
          <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-amber-100">
            <Atom className="h-6 w-6 text-amber-700" />
          </div>
          <h3 className="text-base font-semibold text-foreground mb-2">Molecular similarity service not deployed</h3>
          <p className="text-sm text-muted-foreground max-w-lg mx-auto mb-4">
            Molecular similarity requires a Tanimoto/ECFP computation service (RDKit) that is not yet deployed.
            This screen will populate automatically once the RDKit similarity service is live at
            <code className="bg-muted px-1 rounded mx-1">POST /api/similarity</code>.
          </p>
          <div className="mt-4 text-xs text-amber-700/80 italic">
            Fabricated similarity scores are never shown on this screen — patient safety requires real Tanimoto computations only.
          </div>
        </CardContent>
      </Card>
    </FadeIn>
  );
}
