import { useEffect, useState } from 'react';
import './reactive-character.css';

type CharacterName = 'kumi' | 'bean' | 'kip' | 'stella';
type IdleFrame = 'idle' | 'blink' | 'look-left' | 'look-right';
const idleAssets = new Map<CharacterName, Promise<void>>();

function preloadIdle(name: CharacterName) {
  let ready = idleAssets.get(name);
  if (!ready) {
    ready = Promise.all(['blink', 'look-left', 'look-right'].map((frame) => new Promise<void>((resolve, reject) => {
      const image = new Image();
      image.onload = () => resolve();
      image.onerror = () => reject(new Error(`Missing ${name}-${frame} character asset`));
      image.src = `/agents/${name}-${frame}.png`;
    }))).then(() => undefined);
    idleAssets.set(name, ready);
  }
  return ready;
}

export default function ReactiveCharacter({ name, state, size = 64, label = '', className = '', loading }: {
  name: CharacterName; state: string; size?: number; label?: string; className?: string; loading?: 'lazy';
}) {
  const canGreet = state === 'idle';
  const [hovered, setHovered] = useState(false);
  const [frame, setFrame] = useState<IdleFrame>('idle');

  useEffect(() => {
    setFrame('idle');
    if (!canGreet || hovered) return;
    const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
    let timer: ReturnType<typeof setTimeout>;
    let disposed = false;
    let loaded = false;
    const active = () => !disposed && loaded && !document.hidden && !reducedMotion.matches;
    const later = (fn: () => void, delay: number) => { timer = setTimeout(fn, delay); };
    const schedule = () => {
      if (!active()) return;
      later(() => {
        if (!active()) return;
        if (Math.random() < 0.62) {
          setFrame('blink');
          later(() => { setFrame('idle'); schedule(); }, 130);
        } else {
          const leftFirst = Math.random() < 0.5;
          setFrame(leftFirst ? 'look-left' : 'look-right');
          later(() => {
            setFrame('idle');
            later(() => {
              setFrame(leftFirst ? 'look-right' : 'look-left');
              later(() => { setFrame('idle'); schedule(); }, 850);
            }, 250);
          }, 850);
        }
      }, 1400 + Math.random() * 1800);
    };
    const restart = () => {
      clearTimeout(timer);
      setFrame('idle');
      schedule();
    };
    document.addEventListener('visibilitychange', restart);
    reducedMotion.addEventListener('change', restart);
    void preloadIdle(name).then(() => { if (!disposed) { loaded = true; restart(); } }).catch(() => {
      // Keep the original face if a decorative asset fails to load.
    });
    return () => {
      disposed = true;
      clearTimeout(timer);
      document.removeEventListener('visibilitychange', restart);
      reducedMotion.removeEventListener('change', restart);
    };
  }, [name, canGreet, hovered]);

  const visibleFrame = canGreet && !hovered ? frame : state;
  return <span className={`reactive-character ${className}`} data-character={name} data-state={state}
    data-idle-frame={visibleFrame} data-can-greet={canGreet}
    onPointerEnter={(event) => { if (event.pointerType === 'mouse' && matchMedia('(hover: hover) and (pointer: fine)').matches) setHovered(true); }}
    onPointerLeave={() => setHovered(false)}
    role={label ? 'img' : undefined} aria-label={label || undefined}
    aria-hidden={label ? undefined : true} style={{ width: size, height: size }}>
    <img className="character-base" src={`/agents/${name}-${visibleFrame}.png`} alt="" width={size} height={size} loading={loading} draggable={false} />
    {canGreet && <img className="character-greeting" src={`/agents/${name}-smile.png`} alt="" width={size} height={size} draggable={false} />}
  </span>;
}
