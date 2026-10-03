import { useCallback, useEffect, useRef, useState } from 'react';
import type { StoreConnection, StoreConnectionStatus } from '../../../../contracts/types';
import { api, ApiError } from '../lib/api';
import StoreLoginCanvas from './StoreLoginCanvas';

/*
 * The user's store accounts: connect (sign in through the relayed store page), sign in again, or disconnect.
 * Used by onboarding (with "Kumi may shop here" ticks) and by the profile sheet (connections only).
 * Connecting never changes the allowance; the mandate alone decides where Kumi may shop.
 */

const STATUS_TEXT: Record<StoreConnectionStatus, string> = {
  not_connected: 'Not connected',
  awaiting_login: 'Signing in…',
  connected: 'Connected',
  expired: 'Signed out',
};

type Props = {
  token: string;
  online: boolean | null;
  /** Onboarding: which stores the new allowance includes, and how to change that. */
  picked?: Record<string, boolean>;
  onPick?: (storeId: string, on: boolean) => void;
  /** Profile: the stores the current allowance includes, shown as a tag. */
  allowedIds?: string[];
  onStores?: (stores: StoreConnection[]) => void;
};

export default function StoresPanel({ token, online, picked, onPick, allowedIds, onStores }: Props) {
  const [stores, setStores] = useState<StoreConnection[] | null>(null);
  const [signingIn, setSigningIn] = useState<StoreConnection | null>(null);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');

  const onStoresRef = useRef(onStores);
  onStoresRef.current = onStores;
  const show = useCallback((next: StoreConnection[]) => { setStores(next); onStoresRef.current?.(next); }, []);
  const replace = (next: StoreConnection) => setStores((current) => {
    const updated = current?.map((s) => (s.store_id === next.store_id ? next : s)) ?? null;
    if (updated) onStoresRef.current?.(updated);
    return updated;
  });

  const load = useCallback(async () => {
    try {
      show((await api.stores(token)).stores);
    } catch (err) {
      // Older APIs have no store connections: fall back to the snapshot shop, sandbox only.
      if (err instanceof ApiError && err.status === 404) show([{ store_id: 'wellcome', name: 'Wellcome', status: 'not_connected', connected_at: null, message: 'Real carts are not available on this server.' }]);
      else setError(err instanceof Error ? err.message : 'Could not load your stores.');
    }
  }, [token, show]);

  useEffect(() => { void load(); }, [load]);

  async function connect(store: StoreConnection) {
    setError(''); setBusy(store.store_id);
    try { setSigningIn(await api.connectStore(token, store.store_id)); } catch (err) {
      setError(err instanceof Error ? err.message : `Could not open ${store.name}.`);
    } finally { setBusy(''); }
  }

  async function disconnect(store: StoreConnection) {
    setError(''); setBusy(store.store_id);
    try { replace(await api.disconnectStore(token, store.store_id)); } catch (err) {
      setError(err instanceof Error ? err.message : `Could not disconnect ${store.name}.`);
    } finally { setBusy(''); }
  }

  async function finishSignIn(status: StoreConnectionStatus) {
    const store = signingIn;
    setSigningIn(null);
    if (!store) return;
    const next = await api.store(token, store.store_id).catch(() => null);
    if (next) replace(next);
    if (status !== 'connected') setError(next?.message ?? `${store.name} sign-in didn’t finish.`);
  }

  if (signingIn) {
    return <StoreLoginCanvas token={token} storeId={signingIn.store_id} storeName={signingIn.name}
      onFinished={(status) => void finishSignIn(status)} onCancel={() => { setSigningIn(null); void load(); }} />;
  }

  return <div className="ob-stores-panel">
    {error && <p className="ob-error" role="alert">{error}</p>}
    {!stores ? <p className="m2-muted">Loading stores…</p> : <ul className="ob-stores">
      {stores.map((store) => <li key={store.store_id}>
        {onPick ? <label className="ob-store-pick">
          <input type="checkbox" checked={Boolean(picked?.[store.store_id])} onChange={(e) => onPick(store.store_id, e.target.checked)} />
          <StoreName store={store} />
        </label> : <div className="ob-store-pick">
          <StoreName store={store} tag={allowedIds?.includes(store.store_id) ? 'In allowance' : undefined} />
        </div>}
        {store.status === 'connected'
          ? <button className="m2-link" onClick={() => void disconnect(store)} disabled={busy === store.store_id}>Disconnect</button>
          : <button className="ob-connect" onClick={() => void connect(store)} disabled={busy === store.store_id || online === false}>{store.status === 'expired' ? 'Sign in again' : 'Connect'}</button>}
      </li>)}
    </ul>}
    <p className="m2-muted ob-small">Disconnect forgets Mandate’s saved sign-in. You stay signed in on the store’s own site and app.</p>
  </div>;
}

function StoreName({ store, tag }: { store: StoreConnection; tag?: string }) {
  return <span><b>{store.name}{tag && <em className="ob-tag">{tag}</em>}</b><small className={`ob-status ${store.status}`}>{STATUS_TEXT[store.status]}</small></span>;
}
