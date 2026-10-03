import { useEffect, useRef, useState } from 'react';
import type { LoginStreamClientMessage, LoginStreamServerMessage, StoreConnectionStatus } from '../../../../contracts/types';
import { api, storeLoginStreamUrl } from '../lib/api';

/*
 * The store's own sign-in page, drawn from the server's browser.
 * The server streams only this user's sign-in tab; taps and keys go back to that tab and nowhere else.
 * The user types their mobile number and SMS code into the store's page; the server relays them and stores neither.
 */

const SPECIAL_KEYS = new Set(['Backspace', 'Tab', 'Enter', 'Escape', 'ArrowLeft', 'ArrowRight', 'Delete']);

type Props = {
  token: string;
  storeId: string;
  storeName: string;
  onFinished: (status: StoreConnectionStatus) => void;
  onCancel: () => void;
};

export default function StoreLoginCanvas({ token, storeId, storeName, onFinished, onCancel }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const typingRef = useRef<HTMLInputElement>(null);
  const socketRef = useRef<WebSocket | null>(null);
  const viewportRef = useRef({ width: 412, height: 780 });
  const pressedRef = useRef(false);
  const [state, setState] = useState<'opening' | 'live' | 'closed'>('opening');
  const [notice, setNotice] = useState('');
  const finishedRef = useRef(onFinished);
  finishedRef.current = onFinished;

  useEffect(() => {
    let cancelled = false;
    let socket: WebSocket | null = null;
    (async () => {
      try {
        const { ticket } = await api.storeLoginTicket(token, storeId);
        if (cancelled) return;
        socket = new WebSocket(storeLoginStreamUrl(storeId, ticket));
        socketRef.current = socket;
        socket.onmessage = (event) => {
          const msg = JSON.parse(String(event.data)) as LoginStreamServerMessage;
          if (msg.type === 'viewport') {
            viewportRef.current = { width: msg.width, height: msg.height };
            const canvas = canvasRef.current;
            if (canvas) { canvas.width = msg.width * 2; canvas.height = msg.height * 2; }
          } else if (msg.type === 'frame') {
            const image = new Image();
            image.onload = () => {
              const canvas = canvasRef.current;
              if (!canvas) return;
              // Taps are mapped onto the page size this frame shows, so the canvas follows it exactly.
              viewportRef.current = { width: msg.width, height: msg.height };
              if (canvas.width !== msg.width * 2 || canvas.height !== msg.height * 2) { canvas.width = msg.width * 2; canvas.height = msg.height * 2; }
              canvas.getContext('2d')?.drawImage(image, 0, 0, canvas.width, canvas.height);
              setState('live');
            };
            image.src = `data:image/jpeg;base64,${msg.data}`;
          } else if (msg.type === 'notice' || msg.type === 'error') {
            setNotice(msg.text);
          } else if (msg.type === 'status') {
            finishedRef.current(msg.status);
          }
        };
        socket.onclose = () => { if (!cancelled) setState('closed'); };
      } catch (err) {
        if (!cancelled) { setNotice(err instanceof Error ? err.message : 'Could not open the store window.'); setState('closed'); }
      }
    })();
    return () => { cancelled = true; socket?.close(); socketRef.current = null; };
  }, [token, storeId]);

  function send(msg: LoginStreamClientMessage) {
    const socket = socketRef.current;
    if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify(msg));
  }

  function point(event: React.PointerEvent | React.WheelEvent) {
    const rect = canvasRef.current!.getBoundingClientRect();
    const { width, height } = viewportRef.current;
    return { x: ((event.clientX - rect.left) / rect.width) * width, y: ((event.clientY - rect.top) / rect.height) * height };
  }

  return <div className="ob-login">
    <div className="ob-login-bar"><span className="ob-lock" aria-hidden>🔒</span><span>{storeName} sign-in · yuu Rewards</span></div>
    <div className={`ob-login-screen${state === 'live' ? ' live' : ''}`}>
      <canvas
        ref={canvasRef}
        className={state === 'live' ? 'live' : ''}
        aria-label={`${storeName} sign-in page. Tap fields to type.`}
        onPointerDown={(e) => { e.preventDefault(); pressedRef.current = true; typingRef.current?.focus({ preventScroll: true }); send({ type: 'down', ...point(e) }); }}
        onPointerUp={(e) => { pressedRef.current = false; send({ type: 'up', ...point(e) }); }}
        onPointerMove={(e) => { if (pressedRef.current) send({ type: 'move', ...point(e) }); }}
        onWheel={(e) => send({ type: 'wheel', ...point(e), dx: e.deltaX, dy: e.deltaY })}
      />
      {state !== 'live' && <div className="ob-login-wait">{state === 'closed' ? 'The store window closed.' : `Opening ${storeName}…`}</div>}
      {/* Keyboard input for the remote page; one-time-code lets phones offer the SMS code. */}
      <input
        ref={typingRef}
        className="ob-typing"
        autoComplete="one-time-code"
        autoCapitalize="off"
        autoCorrect="off"
        spellCheck={false}
        aria-label={`Type into the ${storeName} sign-in page`}
        onKeyDown={(e) => { if (SPECIAL_KEYS.has(e.key)) { e.preventDefault(); send({ type: 'key', key: e.key as Extract<LoginStreamClientMessage, { type: 'key' }>['key'] }); } }}
        onInput={(e) => {
          const text = e.currentTarget.value;
          e.currentTarget.value = '';
          for (let i = 0; i < text.length; i += 64) send({ type: 'text', text: text.slice(i, i + 64) });
        }}
      />
    </div>
    {notice && <p className="ob-notice" role="status">{notice}</p>}
    <p className="m2-muted ob-small">You’re typing into {storeName}’s own page. Mandate passes your taps and keys straight to it and never stores your number or code; it keeps only the store’s sign-in session so Kumi can fill your cart.</p>
    <button className="m2-link" onClick={onCancel}>Cancel</button>
  </div>;
}
