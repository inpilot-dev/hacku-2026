import { useEffect, useState } from 'react';
import type { Profile, ProfileInput } from '../../../../../contracts/types';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { api, ApiError } from '@/lib/api';
import { toast } from 'sonner';
import { TOKEN } from '../data/account';

/*
 * Delivery details (GET/PUT /profile): what the one-time agent types into a shop's guest checkout.
 * They go to the shop and to the browser agent's models; nothing else uses them.
 */

const EMPTY: ProfileInput = { full_name: '', email: '', phone: '', address_line1: '', address_line2: '', district: '', region: null, city: 'Hong Kong', country: 'Hong Kong', postal_code: '' };
const REGIONS = ['Hong Kong Island', 'Kowloon', 'New Territories'];

export default function DeliveryDetails({ onSaved, returning }: { onSaved?: () => void; returning?: boolean }) {
  const [form, setForm] = useState<ProfileInput>(EMPTY);
  const [missing, setMissing] = useState<string[] | null>(null);
  const [saving, setSaving] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [loadFailed, setLoadFailed] = useState(false);
  const [retryTick, setRetryTick] = useState(0);
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true); setLoadFailed(false); setError('');
    api.profile(TOKEN).then((p: Profile) => {
      if (cancelled) return;
      setForm({ full_name: p.full_name ?? '', email: p.email ?? '', phone: p.phone ?? '', address_line1: p.address_line1 ?? '',
        address_line2: p.address_line2 ?? '', district: p.district ?? '', region: p.region, city: p.city ?? 'Hong Kong',
        country: p.country ?? 'Hong Kong', postal_code: p.postal_code ?? '' });
      setMissing(p.missing);
    }).catch((err) => {
      if (cancelled) return;
      if (err instanceof ApiError && err.status === 404) setMissing(['full_name', 'email', 'phone', 'address_line1', 'district']);
      else { setLoadFailed(true); setError('Could not load your delivery details.'); }
    }).finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [retryTick]);

  const set = (key: keyof ProfileInput) => (e: React.ChangeEvent<HTMLInputElement>) => { setDirty(true); setForm((f) => ({ ...f, [key]: e.target.value })); };

  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (loading || saving || loadFailed) return;
    setSaving(true); setError('');
    try {
      const saved = await api.saveProfile(TOKEN, { ...form, address_line2: form.address_line2 || null, postal_code: form.postal_code || null, region: form.region || null });
      setMissing(saved.missing);
      setDirty(false);
      setForm((f) => ({ ...f, region: saved.region }));
      toast.success('Delivery details saved');
      if (!saved.missing.length) onSaved?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save.');
    } finally { setSaving(false); }
  }

  return <form className="grid gap-4" onSubmit={(e) => void save(e)}>
    {loadFailed && <div className="flex items-center justify-between gap-3 rounded-lg border p-3" role="alert"><p className="text-sm text-destructive">{error}</p><Button type="button" variant="outline" size="sm" onClick={() => setRetryTick((value) => value + 1)}>Retry</Button></div>}
    <fieldset disabled={loading || saving || loadFailed} className="contents">
    {missing && missing.length > 0 && <p className="text-sm text-muted-foreground">The one-time agent needs these before it can check out for you.</p>}
    <div className="grid gap-4 sm:grid-cols-2">
      <F id="full_name" label="Full name"><Input id="full_name" autoComplete="name" required value={form.full_name} onChange={set('full_name')} /></F>
      <F id="phone" label="Phone"><Input id="phone" type="tel" autoComplete="tel" required value={form.phone} onChange={set('phone')} /></F>
    </div>
    <F id="email" label="Email"><Input id="email" type="email" autoComplete="email" required value={form.email} onChange={set('email')} /></F>
    <F id="address_line1" label="Street address"><Input id="address_line1" autoComplete="address-line1" required value={form.address_line1} onChange={set('address_line1')} placeholder="1 Example Road" /></F>
    <F id="address_line2" label="Flat, floor, building (optional)"><Input id="address_line2" autoComplete="address-line2" value={form.address_line2 ?? ''} onChange={set('address_line2')} placeholder="Flat A, 10/F" /></F>
    <div className="grid gap-4 sm:grid-cols-2">
      <F id="district" label="District"><Input id="district" required value={form.district} onChange={set('district')} placeholder="Wan Chai" /></F>
      <F id="region" label="Region">
        <Select value={form.region ?? ''} onValueChange={(v) => { setDirty(true); setForm((f) => ({ ...f, region: v })); }}>
          <SelectTrigger id="region" className="w-full"><SelectValue placeholder="From the district" /></SelectTrigger>
          <SelectContent>{REGIONS.map((r) => <SelectItem key={r} value={r}>{r}</SelectItem>)}</SelectContent>
        </Select>
      </F>
    </div>
    {error && !loadFailed && <p className="text-sm text-destructive" role="alert">{error}</p>}
    <div className="flex items-center gap-3">
      <Button type="submit" disabled={loading || saving || loadFailed}>{loading ? 'Loading…' : saving ? 'Saving…' : returning ? 'Save and return to shopping' : 'Save details'}</Button>
      {missing && missing.length === 0 && !dirty && !loadFailed && <span className="text-xs text-success">Ready for checkout</span>}
    </div>
    </fieldset>
  </form>;
}

function F({ id, label, children }: { id: string; label: string; children: React.ReactNode }) {
  return <div className="grid gap-1.5"><Label htmlFor={id}>{label}</Label>{children}</div>;
}
