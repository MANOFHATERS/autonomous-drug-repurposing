'use client';

import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent } from '@/components/ui/card';
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table';
import { useDrugOSNav } from '../nav-context';
import { recentQueries, diseases } from '@/lib/empty-defaults';
import {
  scoreColor, PageHeader, FadeIn, EmptyDataState,
} from './_core-shared';

export function QueryHistoryScreen() {
  const { navigate } = useDrugOSNav();
  // FE-054 ROOT FIX (TM13): recentQueries is empty until a query-history
  // API is wired. Show an honest empty state instead of a blank table.
  if (recentQueries.length === 0) {
    return (
      <FadeIn>
        <PageHeader title="Query History" description="Your past search history" />
        <EmptyDataState title="No queries yet" hint="Your past disease searches will appear here so you can re-run them." />
      </FadeIn>
    );
  }
  return (
    <FadeIn>
      <PageHeader title="Query History" description="Your past search history" />
      <Card>
        <CardContent className="p-0">
          <Table>
            <TableHeader><TableRow className="bg-muted/50"><TableHead>Date</TableHead><TableHead>Disease</TableHead><TableHead>Candidates</TableHead><TableHead>Top Score</TableHead><TableHead></TableHead></TableRow></TableHeader>
            <TableBody>
              {recentQueries.map(q => {
                const disease = diseases.find(d => d.name === q.disease);
                return (
                  <TableRow key={q.id}>
                    <TableCell className="text-sm text-muted-foreground">{q.date}</TableCell>
                    <TableCell className="font-medium">{q.disease}</TableCell>
                    <TableCell><Badge variant="secondary">{q.candidates}</Badge></TableCell>
                    <TableCell><span className="font-bold" style={{ color: scoreColor(q.topScore) }}>{q.topScore}</span></TableCell>
                    <TableCell><Button variant="ghost" size="sm" onClick={() => disease && navigate({ page: 'app', section: 'results', id: disease.id })}>Re-run</Button></TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </FadeIn>
  );
}
