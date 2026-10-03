import { useCallback, useEffect, useRef, useState } from 'react';
import type { StoreConnection, StoreConnectionStatus } from '../../../../../contracts/types';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { api, ApiError } from '@/lib/api';
import StoreLoginCanvas from '@/simple/StoreLoginCanvas';
import { TOKEN } from '../data/account';
import './store-login.css';

/*
 * The user's store accounts: connect (sign in through the relayed store page), sign in again, or disconnect.
 * Connecting never changes the allowance; the allowance alone decides where the agent may shop.
 */

const STATUS_TEXT: Record<StoreConnectionStatus, string> = {
  not_connected: 'Not connected',
  awaiting_login: 'Signing in…',
  connected: 'Connected',
  expired: 'Signed out',
};

type Props = {
  online: boolean | null;
  /** Allowance setup: which stores the new allowance includes, and how to change that. */
  picked?: Record<string, boolean>;
  onPick?: (storeId: string, on: boolean) => void;
  /** Wallet: the stores the current allowance includes. */
  allowedIds?: string[];
  onStores?: (stores: StoreConnection[]) => void;
};

export default function StoreAccounts({ online, picked, onPick, allowedIds, onStores }: Props) {
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
      show((await api.stores(TOKEN)).stores);
    } catch (err) {
      // Older APIs have no store connections: fall back to the snapshot shop, sandbox only.
      if (err instanceof ApiError && err.status === 404) show([{ store_id: 'wellcome', name: 'Wellcome', status: 'not_connected', connected_at: null, message: 'Real carts are not available on this server.' }]);
      else setError(err instanceof Error ? err.message : 'Could not load your stores.');
    }
  }, [show]);

  useEffect(() => { void load(); }, [load]);

  async function connect(store: StoreConnection) {
    setError('');
    if (store.status === 'awaiting_login') { setSigningIn(store); return; }  // resume, don't restart
    setBusy(store.store_id);
    try { setSigningIn(await api.connectStore(TOKEN, store.store_id)); } catch (err) {
      setError(err instanceof Error ? err.message : `Could not open ${store.name}.`);
    } finally { setBusy(''); }
  }

  async function disconnect(store: StoreConnection) {
    setError(''); setBusy(store.store_id);
    try { replace(await api.disconnectStore(TOKEN, store.store_id)); } catch (err) {
      setError(err instanceof Error ? err.message : `Could not disconnect ${store.name}.`);
    } finally { setBusy(''); }
  }

  async function finishSignIn(status: StoreConnectionStatus) {
    const store = signingIn;
    setSigningIn(null);
    if (!store) return;
    const next = await api.store(TOKEN, store.store_id).catch(() => null);
    if (next) replace(next);
    if (status !== 'connected') setError(next?.message ?? `${store.name} sign-in didn’t finish.`);
  }

  if (signingIn) {
    return <div className="shell-store-login">
      <StoreLoginCanvas token={TOKEN} storeId={signingIn.store_id} storeName={signingIn.name}
        onFinished={(status) => void finishSignIn(status)} onCancel={() => { setSigningIn(null); void load(); }} />
    </div>;
  }

  return <div className="space-y-3">
    {error && <p className="text-sm text-destructive" role="alert">{error}</p>}
    {!stores ? <p className="text-sm text-muted-foreground">Loading stores…</p> : <ul className="divide-y rounded-xl border">
      {stores.map((store) => <li key={store.store_id} className="flex min-h-14 items-center gap-3 px-4 py-2">
        {onPick && <Checkbox id={`pick-${store.store_id}`} checked={Boolean(picked?.[store.store_id])}
          onCheckedChange={(on) => onPick(store.store_id, on === true)} aria-label={`The agent may shop at ${store.name}`} />}
        <label htmlFor={onPick ? `pick-${store.store_id}` : undefined} className="min-w-0 flex-1">
          <span className="flex items-center gap-2 text-sm font-medium">{store.name}
            {allowedIds?.includes(store.store_id) && <Badge variant="secondary" className="font-normal">In allowance</Badge>}</span>
          <span className={store.status === 'connected' ? 'text-xs text-success' : 'text-xs text-muted-foreground'}>{STATUS_TEXT[store.status]}</span>
        </label>
        {store.status === 'connected'
          ? <Button variant="ghost" size="sm" onClick={() => void disconnect(store)} disabled={busy === store.store_id}>Disconnect</Button>
          : <Button variant="outline" size="sm" onClick={() => void connect(store)} disabled={busy === store.store_id || online === false}>
            {store.status === 'expired' ? 'Sign in again' : store.status === 'awaiting_login' ? 'Continue' : 'Connect'}</Button>}
      </li>)}
    </ul>}
    <p className="text-xs text-muted-foreground">Connecting lets the agent fill your real cart; it never checks out. Disconnect forgets the saved sign-in.</p>
  </div>;
}
