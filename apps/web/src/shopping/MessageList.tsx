import { useEffect, useRef } from 'react';
import ReactiveCharacter from '../components/ReactiveCharacter';

export type ConversationMessage = {
  id: string;
  speaker: 'you' | 'kumi' | 'kip' | 'bean' | 'stella';
  text: string;
  tone?: 'good' | 'bad' | 'info';
};

export default function MessageList({ messages }: { messages: ConversationMessage[] }) {
  const end = useRef<HTMLDivElement>(null);
  const following = useRef(true);
  useEffect(() => {
    const update = () => { following.current = window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 240; };
    window.addEventListener('scroll', update, { passive: true });
    return () => window.removeEventListener('scroll', update);
  }, []);
  useEffect(() => {
    if (following.current && messages.length > 1) end.current?.scrollIntoView({ block: 'nearest', behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
  }, [messages.length]);
  return <div className="conversation-messages" role="log" aria-label="Shopping conversation" aria-live="polite" aria-relevant="additions">
    {messages.map((message) => <article key={message.id} className={`conversation-message from-${message.speaker} ${message.tone ?? ''}`}>
      {message.speaker !== 'you' && <ReactiveCharacter name={message.speaker} state="idle" size={36} />}
      <div><span className="conversation-speaker">{message.speaker === 'you' ? 'You' : message.speaker[0].toUpperCase() + message.speaker.slice(1)}</span><p>{message.text}</p></div>
    </article>)}
    <div ref={end} />
  </div>;
}
