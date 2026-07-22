'use client';

import { useState } from 'react';
import { Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent } from '@/components/ui/card';
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table';
import { useDrugOSNav } from '../nav-context';
import { savedQueries, diseases } from '@/lib/empty-defaults';
import { PageHeader, FadeIn } from './_core-shared';

export function SavedQueriesScreen() {
  const [queries, setQueries] = useState(savedQueries);
  const { navigate } = useDrugOSNav();
  return (
    <FadeIn>
      <PageHeader title="Saved Queries" description="Manage and re-run your saved search queries" />
      <Card>
        <CardContent className="p-0">
          <Table>
            <TableHeader><TableRow className="bg-muted/50"><TableHead>Name</TableHead><TableHead>Disease</TableHead><TableHead>Filters</TableHead><TableHead>Results</TableHead><TableHead>Created</TableHead><TableHead></TableHead></TableRow></TableHeader>
            <TableBody>
              {queries.map(q => (
                <TableRow key={q.id} className="cursor-pointer hover:bg-muted/30" onClick={() => {
                  const disease = diseases.find(d => d.name === q.disease);
                  if (disease) navigate({ page: 'app', section: 'results', id: disease.id });
                }}>
                  <TableCell className="font-medium">{q.name}</TableCell>
                  <TableCell>{q.disease}</TableCell>
                  <TableCell><span className="text-xs text-muted-foreground">{q.filters}</span></TableCell>
                  <TableCell><Badge variant="secondary">{q.results}</Badge></TableCell>
                  <TableCell className="text-xs text-muted-foreground">{q.created}</TableCell>
                  <TableCell><Button variant="ghost" size="sm" className="h-7" onClick={e => { e.stopPropagation(); setQueries(prev => prev.filter(x => x.id !== q.id)); }}><Trash2 className="h-3.5 w-3.5 text-muted-foreground" /></Button></TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </FadeIn>
  );
}
