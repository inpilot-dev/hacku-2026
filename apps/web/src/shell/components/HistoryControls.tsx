import { ArrowDown, History } from 'lucide-react';
import { Button } from '@/components/ui/button';

export default function HistoryControls({ hidden, expanded, onMore, onLatest }: {
  hidden: number; expanded: boolean; onMore: () => void; onLatest: () => void;
}) {
  if (!hidden && !expanded) return null;
  return <nav aria-label="Conversation history" className="mb-5 flex flex-wrap items-center justify-center gap-2 border-b pb-4">
    {hidden > 0 && <Button variant="secondary" size="sm" className="rounded-full" onClick={onMore}><History />Load earlier<span className="text-xs text-muted-foreground">{hidden}</span></Button>}
    {expanded && <Button variant="ghost" size="sm" className="rounded-full" onClick={onLatest}>Back to latest<ArrowDown /></Button>}
  </nav>;
}
