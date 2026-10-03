import { useEffect, useRef } from 'react';
import ReactiveCharacter from '../components/ReactiveCharacter';

export type ConversationMessage = {
  id: string;
  speaker: 'you' | 'kumi' | 'kip' | 'bean' | 'stella';
  text: string;
  tone?: 'good' | 'bad' | 'info';
};
type Agent = Exclude<ConversationMessage['speaker'], 'you'>;
const identities = {
  kumi: { name: 'Kumi', role: 'Shopping companion' },
  kip: { name: 'Kip', role: 'Wallet guardian' },
  bean: { name: 'Bean', role: 'Rules checker' },
  stella: { name: 'Stella', role: 'Receipt keeper' },
};

// Only shorten known status formats. The original remains available for inspection.
function present(message: ConversationMessage): { text: string; detail?: string } {
  const text = message.text;
  if (message.speaker === 'you') return { text };
  const match = text.match(/^Jev picked (\d+) of (\d+) items at (.+?) for (HK\$[\d,.]+):/);
  if (match) {
    const missing = text.match(/Not added: ([\s\S]+)$/);
    return { text: `I found ${match[1]} of your ${match[2]} items at ${match[3]} for ${match[4]}.${missing ? `\nStill missing: ${missing[1].replace(/\s*\(no confident match[\s\S]*$/, '')}. I’ll leave that out for you to review.` : ''}`, detail: text };
  }
  const quote = text.match(/^Quote ready: (.+?)\. Review the basket below\.$/);
  if (quote) return { text: `Your basket is ready! ${quote[1]}.\nTake a look below before we check out.` };
  if (text.startsWith('Checkout response unavailable')) return { text: 'I couldn’t confirm the checkout response. Let me check the saved transaction before we do anything else.', detail: text };
  if (text.startsWith('Recovered the confirmed sandbox receipt')) return { text: 'Found it — your sandbox payment was confirmed. I recovered the receipt without submitting a second payment.', detail: text };
  const paid = text.match(/^Sandbox payment confirmed: (HK\$[\d,.]+)/);
  if (paid) return { text: `All set! Your sandbox payment of ${paid[1]} is confirmed. Your receipt is ready below. No real purchase was made.`, detail: text };
  return { text };
}

export default function MessageList({ messages, working }: { messages: ConversationMessage[]; working?: { speaker: Agent; text: string } }) {
  const end = useRef<HTMLDivElement>(null);
  const following = useRef(true);
  useEffect(() => {
    const update = () => { following.current = window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 240; };
    window.addEventListener('scroll', update, { passive: true });
    return () => window.removeEventListener('scroll', update);
  }, []);
  useEffect(() => {
    if (following.current && messages.length > 1) end.current?.scrollIntoView({ block: 'nearest', behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
  }, [messages.length, working?.speaker, working?.text]);
  return <div className="conversation-messages" role="log" aria-label="Shopping conversation" aria-live="polite" aria-relevant="additions">
    {messages.map((message, index) => {
      const grouped = index > 0 && messages[index - 1].speaker === message.speaker;
      const content = present(message);
      const identity = message.speaker === 'you' ? null : identities[message.speaker];
      return <article key={message.id} className={`conversation-message from-${message.speaker} ${message.tone ?? ''} ${grouped ? 'is-grouped' : ''}`} aria-label={identity?.name ?? 'You'}>
        {identity && <div className="conversation-avatar" aria-hidden="true">{!grouped && <ReactiveCharacter name={message.speaker as Agent} state="idle" size={42} />}</div>}
        <div className="conversation-bubble">
          {!grouped && <div className="conversation-identity"><span className="conversation-speaker">{identity?.name ?? 'You'}</span>{identity && <span className="conversation-role">{identity.role}</span>}</div>}
          <p>{content.text}</p>
          {content.detail && <details className="conversation-detail"><summary>Details</summary><p>{content.detail}</p></details>}
        </div>
      </article>;
    })}
    {working && <article className={`conversation-message from-${working.speaker} conversation-working`} role="status">
      <div className="conversation-avatar" aria-hidden="true"><ReactiveCharacter name={working.speaker} state="idle" size={42} /></div>
      <div className="conversation-bubble"><div className="conversation-identity"><span className="conversation-speaker">{identities[working.speaker].name}</span><span className="conversation-role">{identities[working.speaker].role}</span></div><div className="conversation-progress"><span className="conversation-dots" aria-hidden="true"><i /><i /><i /></span><span>{working.text}</span></div></div>
    </article>}
    <div ref={end} />
  </div>;
}
