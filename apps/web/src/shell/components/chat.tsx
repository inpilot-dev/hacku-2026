import type { CSSProperties, ReactNode } from 'react';
import { Loader2 } from 'lucide-react';
import ReactiveCharacter from '@/components/ReactiveCharacter';
import { cn } from '@/lib/utils';

/*
 * Chat building blocks shared by the tabs, and the characters who speak in them:
 * Kumi shops, Kip guards the wallet, Bean checks the rules, Stella keeps the receipts.
 */

export type Agent = 'kumi' | 'kip' | 'bean' | 'stella';

export const IDENTITY: Record<Agent, { name: string; role: string }> = {
  kumi: { name: 'Kumi', role: 'Shopping companion' },
  kip: { name: 'Kip', role: 'Wallet guardian' },
  bean: { name: 'Bean', role: 'Rules checker' },
  stella: { name: 'Stella', role: 'Receipt keeper' },
};

const tint = (who: Agent): CSSProperties => ({
  background: `var(--${who}-soft)`, borderColor: `var(--${who}-line)`,
});

/** A character, animated when idle (blinks, looks around), with a pose for other states. */
export function Sticker({ who, state = 'idle', size = 56, className, label, motion }: { who: Agent; state?: string; size?: number; className?: string; label?: string; motion?: 'working' | 'success' | 'refused' }) {
  return <ReactiveCharacter name={who} state={state} size={size} className={className} label={label} motion={motion ?? (state === goodPose(who) ? 'success' : state === 'refused' || state === 'sad' || state === 'revoked' || state === 'fail' ? 'refused' : undefined)} />;
}

export function PageHeader({ title, description, action, who, state }: {
  title: string; description?: ReactNode; action?: ReactNode; who?: Agent; state?: string;
}) {
  return <div className="mb-6 flex items-center justify-between gap-4">
    <div className="flex min-w-0 items-center gap-3">
      {who && <Sticker who={who} state={state} size={52} className="shrink-0" label={`${IDENTITY[who].name}, ${IDENTITY[who].role.toLowerCase()}`} />}
      <div className="min-w-0 space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        {description && <p className="text-sm text-muted-foreground">{description}</p>}
      </div>
    </div>
    {action}
  </div>;
}

export function Bubble({ from, children, tone = 'info', state }: { from: 'you' | Agent; children: ReactNode; tone?: 'good' | 'bad' | 'info'; state?: string }) {
  if (from === 'you') {
    return <div className="chat-arrive flex justify-end pl-10">
      <div className="max-w-[85%] rounded-3xl rounded-tr-md bg-primary px-4 py-2.5 text-sm leading-relaxed whitespace-pre-line break-words text-primary-foreground">{children}</div>
    </div>;
  }
  const who = IDENTITY[from];
  return <div className="chat-arrive flex items-start gap-2.5 pr-6">
    <Sticker who={from} state={state ?? (tone === 'bad' ? badPose(from) : tone === 'good' ? goodPose(from) : 'idle')} size={36} className="mt-0.5 shrink-0" />
    <div className={cn('min-w-0 max-w-[85%] rounded-3xl rounded-tl-md border px-4 py-2.5', tone === 'bad' && 'border-destructive/30')} style={tint(from)}>
      <p className="mb-0.5 text-xs font-semibold" style={{ color: `var(--${from})` }}>{who.name}<span className="ml-1.5 font-normal">{who.role}</span></p>
      <div className="text-sm leading-relaxed whitespace-pre-line break-words">{children}</div>
    </div>
  </div>;
}

// The poses each character has (public/agents/<name>-<pose>.png).
const goodPose = (who: Agent) => ({ kumi: 'happy', kip: 'approved', bean: 'done', stella: 'pass' }[who]);
const badPose = (who: Agent) => ({ kumi: 'sad', kip: 'refused', bean: 'thinking', stella: 'fail' }[who]);

export function Working({ children, who }: { children: ReactNode; who?: Agent }) {
  return <div className="flex items-center gap-2.5 text-sm text-muted-foreground" role="status">
    {who ? <Sticker who={who} size={36} className="shrink-0" motion="working" /> : null}
    <span className="working-dots" style={{ color: `var(--${who ?? 'kumi'})` }} aria-hidden="true"><i /><i /><i /></span>{children}
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

/** Friendlier wording for the agent's known status formats (from the previous chat, shopping/MessageList.tsx). */
export function present(text: string): string {
  const match = text.match(/^Jev picked (\d+) of (\d+) items at (.+?) for (HK\$[\d,.]+)/);
  if (match) {
    const missing = text.match(/Not added: ([\s\S]+)$/);
    return `I found ${match[1]} of your ${match[2]} items at ${storeTitle(match[3])} for ${match[4]}.${missing ? `\nStill missing: ${missing[1].replace(/\s*\(no confident match[\s\S]*$/, '')}. I left that out for you to review.` : ''}`;
  }
  if (/^Jev is choosing products/.test(text)) return 'Looking through the shelves for your list…';
  if (text.startsWith('Checkout response unavailable')) return 'I couldn’t confirm the checkout response. Checking the saved transaction before doing anything else.';
  const paid = text.match(/^Sandbox payment confirmed: (HK\$\d[\d,]*(?:\.\d{2})?)/);
  if (paid) return `All set! Your sandbox payment of ${paid[1]} is confirmed. No real purchase was made.`;
  return text;
}

const storeTitle = (id: string) => ({ wellcome: 'Wellcome', marketplace: 'Market Place' }[id] ?? id);
