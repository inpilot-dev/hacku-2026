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
  // Touch: a short tap is a click; a drag scrolls the store page (a phone can't send wheel events).
  const touchRef = useRef<{ x: number; y: number; lastX: number; lastY: number; scrolling: boolean } | null>(null);
  const [state, setState] = useState<'opening' | 'live' | 'closed'>('opening');
  const [size, setSize] = useState({ width: 412, height: 860 });
  const screenRef = useRef<HTMLDivElement>(null);
  const sizedRef = useRef(false);
  const [attempt, setAttempt] = useState(0);  // Reopen: a new stream onto the same pending sign-in
  const finalRef = useRef(false);
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
        socket.onopen = () => sendRoom();
        socket.onmessage = (event) => {
          const msg = JSON.parse(String(event.data)) as LoginStreamServerMessage;
          if (msg.type === 'viewport') {
            viewportRef.current = { width: msg.width, height: msg.height };
            setSize({ width: msg.width, height: msg.height });
            const canvas = canvasRef.current;
            if (canvas) { canvas.width = msg.width * 2; canvas.height = msg.height * 2; }
          } else if (msg.type === 'frame') {
            const image = new Image();
            image.onload = () => {
              const canvas = canvasRef.current;
              if (!canvas) return;
              // Taps are mapped onto the page size this frame shows, so the canvas follows it exactly.
              viewportRef.current = { width: msg.width, height: msg.height };
              setSize((s) => (s.width === msg.width && s.height === msg.height ? s : { width: msg.width, height: msg.height }));
              if (canvas.width !== msg.width * 2 || canvas.height !== msg.height * 2) { canvas.width = msg.width * 2; canvas.height = msg.height * 2; }
              canvas.getContext('2d')?.drawImage(image, 0, 0, canvas.width, canvas.height);
              if (!sizedRef.current) { sizedRef.current = true; sendRoom(); }  // again once the page is up
              setState('live');
            };
            image.src = `data:image/jpeg;base64,${msg.data}`;
          } else if (msg.type === 'notice' || msg.type === 'error') {
            setNotice(msg.text);
          } else if (msg.type === 'status') {
            finalRef.current = true;
            finishedRef.current(msg.status);
          }
        };
        socket.onclose = () => {
          if (cancelled || finalRef.current) return;
          setState('closed');
          // The stream can drop while the store still finished signing in; ask before showing "closed".
          api.store(token, storeId).then((store) => {
            if (!cancelled && store.status !== 'awaiting_login') { finalRef.current = true; finishedRef.current(store.status); }
          }).catch(() => undefined);
        };
      } catch (err) {
        if (!cancelled) { setNotice(err instanceof Error ? err.message : 'Could not open the store window.'); setState('closed'); }
      }
    })();
    setState('opening'); setNotice(''); sizedRef.current = false;
    return () => { cancelled = true; socket?.close(); socketRef.current = null; };
  }, [token, storeId, attempt]);

  /** Ask for a store page exactly as big as the room here, so it shows 1:1 and nothing is cut off. */
  function sendRoom() {
    const width = Math.round(Math.min(screenRef.current?.parentElement?.clientWidth ?? 412, 480));
    const height = Math.round(Math.min(Math.max(window.innerHeight * 0.62, 420), 1000));
    send({ type: 'resize', width, height });
  }

  useEffect(() => {
    let timer = 0;
    const onResize = () => { window.clearTimeout(timer); timer = window.setTimeout(sendRoom, 250); };
    window.addEventListener('resize', onResize);
    return () => { window.removeEventListener('resize', onResize); window.clearTimeout(timer); };
  }, []);

  function send(msg: LoginStreamClientMessage) {
    const socket = socketRef.current;
    if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify(msg));
  }

  // Wheel over the store page scrolls the store page, not the dialog around it (needs a non-passive listener).
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      const rect = canvas.getBoundingClientRect();
      const { width, height } = viewportRef.current;
      send({ type: 'wheel', x: ((event.clientX - rect.left) / rect.width) * width, y: ((event.clientY - rect.top) / rect.height) * height, dx: event.deltaX, dy: event.deltaY });
    };
    canvas.addEventListener('wheel', onWheel, { passive: false });
    return () => canvas.removeEventListener('wheel', onWheel);
  }, []);

  function onDown(e: React.PointerEvent) {
    e.preventDefault();
    typingRef.current?.focus({ preventScroll: true });
    if (e.pointerType === 'touch') { touchRef.current = { x: e.clientX, y: e.clientY, lastX: e.clientX, lastY: e.clientY, scrolling: false }; return; }
    pressedRef.current = true;
    send({ type: 'down', ...point(e) });
  }

  function onMove(e: React.PointerEvent) {
    const touch = touchRef.current;
    if (touch) {
      if (!touch.scrolling && Math.hypot(e.clientX - touch.x, e.clientY - touch.y) > 8) touch.scrolling = true;
      if (touch.scrolling) {
        const scale = viewportRef.current.width / canvasRef.current!.getBoundingClientRect().width;
        send({ type: 'wheel', ...point(e), dx: (touch.lastX - e.clientX) * scale, dy: (touch.lastY - e.clientY) * scale });
        touch.lastX = e.clientX; touch.lastY = e.clientY;
      }
      return;
    }
    if (pressedRef.current) send({ type: 'move', ...point(e) });
  }

  function onUp(e: React.PointerEvent) {
    const touch = touchRef.current;
    touchRef.current = null;
    if (touch) {
      if (!touch.scrolling) { send({ type: 'down', ...point(e) }); send({ type: 'up', ...point(e) }); }
      return;
    }
    pressedRef.current = false;
    send({ type: 'up', ...point(e) });
  }

  function point(event: React.PointerEvent) {
    const rect = canvasRef.current!.getBoundingClientRect();
    const { width, height } = viewportRef.current;
    return { x: ((event.clientX - rect.left) / rect.width) * width, y: ((event.clientY - rect.top) / rect.height) * height };
  }

  return <div className="ob-login">
    <div className="ob-login-bar"><span className="ob-lock" aria-hidden>🔒</span><span>{storeName} sign-in · yuu Rewards</span></div>
    <div ref={screenRef} className={`ob-login-screen${state === 'live' ? ' live' : ''}`} style={{ width: size.width, aspectRatio: `${size.width} / ${size.height}` }}>
      <canvas
        ref={canvasRef}
        className={state === 'live' ? 'live' : ''}
        aria-label={`${storeName} sign-in page. Tap fields to type.`}
        onPointerDown={onDown}
        onPointerUp={onUp}
        onPointerMove={onMove}
        onPointerCancel={() => { touchRef.current = null; pressedRef.current = false; }}
      />
      {state === 'opening' && <div className="ob-login-wait">Opening {storeName}…</div>}
      {state === 'closed' && <div className="ob-login-wait ob-login-closed"><span>The store window stopped.</span>
        <button className="ob-connect" onClick={() => setAttempt((n) => n + 1)}>Reopen, keep my progress</button></div>}
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
    <p className="m2-muted ob-small ob-hint">Scroll inside the window (drag on a phone) if part of the page, like a “not a robot” check, is out of view.</p>
    {notice && <p className="ob-notice" role="status">{notice}</p>}
    <p className="m2-muted ob-small">You’re typing into {storeName}’s own page. Mandate passes your taps and keys straight to it and never stores your number or code; it keeps only the store’s sign-in session so Kumi can fill your cart.</p>
    <button className="m2-link" onClick={onCancel}>Cancel</button>
  </div>;
}
