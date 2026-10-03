import type { ReactNode } from 'react';
import { Loader2 } from 'lucide-react';
import { cn } from '@/lib/utils';

/* Chat building blocks shared by the Buy and Groceries tabs. */

export function PageHeader({ title, description, action }: { title: string; description?: ReactNode; action?: ReactNode }) {
  return <div className="mb-6 flex items-start justify-between gap-4">
    <div className="space-y-1">
      <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
      {description && <p className="text-sm text-muted-foreground">{description}</p>}
    </div>
    {action}
  </div>;
}

export function Bubble({ from, children, tone = 'info' }: { from: 'you' | 'agent'; children: ReactNode; tone?: 'good' | 'bad' | 'info' }) {
  const mine = from === 'you';
  return <div className={cn('flex', mine ? 'justify-end' : 'justify-start')}>
    <div className={cn('max-w-[85%] rounded-2xl px-4 py-2.5 text-sm leading-relaxed whitespace-pre-line break-words',
      mine ? 'rounded-br-md bg-primary text-primary-foreground' : 'rounded-bl-md bg-muted text-foreground',
      !mine && tone === 'bad' && 'bg-destructive/10 text-destructive',
      !mine && tone === 'good' && 'bg-success/10')}>
      {children}
    </div>
  </div>;
}

export function Working({ children }: { children: ReactNode }) {
  return <div className="flex items-center gap-2 text-sm text-muted-foreground" role="status">
    <Loader2 className="size-4 animate-spin" />{children}
  </div>;
}

/** Turns bare http(s) links in agent text into links (agent replies carry shop links). */
export function Linkified({ text }: { text: string }) {
  const parts = text.split(/(https?:\/\/[^\s)]+)/g);
  return <>{parts.map((part, i) => /^https?:\/\//.test(part)
    ? <a key={i} href={part} target="_blank" rel="noopener noreferrer" className="font-medium underline underline-offset-2 break-all">{shortUrl(part)}</a>
    : <span key={i}>{part}</span>)}</>;
}

export function shortUrl(url: string) {
  try {
    const u = new URL(url);
    const path = decodeURIComponent(u.pathname).replace(/\/$/, '');
    return u.hostname.replace(/^www\./, '') + (path.length > 28 ? `${path.slice(0, 28)}…` : path);
  } catch { return url; }
}
