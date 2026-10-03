import { useEffect, useRef, useState } from 'react';
import { ExternalLink, Link2, Link2Off, Radio } from 'lucide-react';
import type { AuditEvent } from '../../../../contracts/types';
import { api } from '../lib/api';
import { describeEvent, packetForEvent, type Packet } from './gates';

/*
 * Follows the live wallet's audit stream for the demo user (GET /events, the same token the shopping demo uses).
 * Events that existed before the monitor opened are listed but not animated. The chain strip compares each event's
 * previous_hash with the event before it; it does not recompute hashes (the independent verifier does that).
 */

const TOKEN = 'dev-user-token';
const POLL_MS = 1200;

function tone(event: AuditEvent) {
  const p = event.payload as { status?: string };
  if (event.type === 'payment_completed' || event.type === 'authorization_approved') return 'pass';
  if (event.type === 'authorization_refused') return p.status === 'requires_review' ? 'review' : 'block';
  if (event.type === 'payment_refused' || event.type === 'mandate_revoked' || event.type === 'card_frozen') return 'block';
  return 'info';
}

export type LiveSummary = { decisions: number; blocked: number; held: number; paidMinor: number };

export default function LiveMonitor({ onPackets, onSummary }: { onPackets: (packets: Packet[]) => void; onSummary: (summary: LiveSummary) => void }) {
  const [history, setHistory] = useState<AuditEvent[]>([]);
  const [fresh, setFresh] = useState<AuditEvent[]>([]);
  const [state, setState] = useState<'connecting' | 'live' | 'offline'>('connecting');
  const cursor = useRef<number | null>(null);
  const emit = useRef(onPackets);
  emit.current = onPackets;

  useEffect(() => {
    let disposed = false;
    let timer: number | undefined;

    async function catchUp() {
      let after = 0;
      const all: AuditEvent[] = [];
      for (;;) {
        const page = await api.events(TOKEN, after, 200);
        all.push(...page.events);
        after = page.next_after;
        if (!page.has_more) break;
      }
      if (disposed) return;
      cursor.current = after;
      setHistory(all.slice(-24));
    }

    async function poll() {
      try {
        if (cursor.current === null) await catchUp();
        else {
          const page = await api.events(TOKEN, cursor.current, 200);
          if (disposed) return;
          cursor.current = page.next_after;
          if (page.events.length) {
            setFresh((prev) => [...prev, ...page.events].slice(-60));
            emit.current(page.events.flatMap((event) => packetForEvent(event) ?? []));
          }
        }
        if (!disposed) setState('live');
      } catch {
        if (!disposed) setState('offline');
      }
      if (!disposed) timer = window.setTimeout(poll, pageDelay());
    }
    const pageDelay = () => (document.hidden ? POLL_MS * 4 : POLL_MS);
    void poll();
    return () => { disposed = true; window.clearTimeout(timer); };
  }, []);

  const chain = [...history, ...fresh].slice(-9);
  const blocked = fresh.filter((event) => tone(event) === 'block').length;
  const held = fresh.filter((event) => tone(event) === 'review').length;
  const paid = fresh.reduce((sum, event) => sum + (event.type === 'payment_completed' ? ((event.payload as { receipt?: { amount_minor?: number } }).receipt?.amount_minor ?? 0) : 0), 0);
  const feed = [...fresh].reverse();
  const summarize = useRef(onSummary);
  summarize.current = onSummary;
  useEffect(() => { summarize.current({ decisions: fresh.length, blocked, held, paidMinor: paid }); }, [fresh.length, blocked, held, paid]);

  return <div className="lm">
    <div className="lm-bar">
      <span className={`lm-state ${state}`}><Radio size={15} />{state === 'live' ? 'Watching your live wallet' : state === 'offline' ? 'Wallet offline, retrying' : 'Connecting…'}</span>
      <p>Open the shopping demo next to this tab and ask the agent for something, including something it should not buy. Every decision the wallet makes appears here as it happens.</p>
      <a className="sl-cta" href="./" target="_blank" rel="noopener">Open the shopping demo<ExternalLink size={16} /></a>
    </div>

    <div className="lm-chain" data-theme="dark" aria-label="Most recent audit events and their hash links">
      <div className="lm-chain-head"><span>Tamper-evident audit chain</span><small>each block must point at the previous block’s hash</small></div>
      <div className="lm-chain-row">
        {chain.length === 0 && <span className="lm-empty">No wallet events yet.</span>}
        {chain.map((event, i) => {
          const prev = i > 0 ? chain[i - 1] : null;
          const linked = prev ? event.previous_hash === prev.event_hash : null;
          const isNew = fresh.includes(event);
          return <div key={event.event_id} className="lm-link-wrap">
            {prev && <span className={`lm-link ${linked ? 'ok' : 'bad'}`} title={linked ? 'previous_hash matches' : 'previous_hash does not match'}>{linked ? <Link2 size={14} /> : <Link2Off size={14} />}</span>}
            <div className={`lm-block ${tone(event)} ${isNew ? 'new' : ''}`}>
              <small>#{event.sequence}</small>
              <strong>{event.type.replace(/_/g, ' ')}</strong>
              <code>{event.event_hash.replace(/^sha256:/, '').slice(0, 10)}</code>
            </div>
          </div>;
        })}
      </div>
    </div>

    <ol className="lm-feed" aria-live="polite">
      {feed.length === 0 && <li className="lm-empty">Waiting for the first decision… {history.length ? `(${history.length} earlier events are in the chain above)` : ''}</li>}
      {feed.map((event) => <li key={event.event_id} className={`lm-item ${tone(event)}`}>
        <span className="lm-dot" />
        <span className="lm-main"><strong>{describeEvent(event)}</strong><small>{event.actor_id} · #{event.sequence}{event.transaction_id ? ` · ${event.transaction_id.slice(0, 8)}…` : ''}</small></span>
        <time>{new Date(event.occurred_at).toLocaleTimeString('en-HK', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}</time>
      </li>)}
    </ol>
  </div>;
}
