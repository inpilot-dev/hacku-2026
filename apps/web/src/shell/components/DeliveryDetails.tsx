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

export default function DeliveryDetails() {
  const [form, setForm] = useState<ProfileInput>(EMPTY);
  const [missing, setMissing] = useState<string[] | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    api.profile(TOKEN).then((p: Profile) => {
      setForm({ full_name: p.full_name ?? '', email: p.email ?? '', phone: p.phone ?? '', address_line1: p.address_line1 ?? '',
        address_line2: p.address_line2 ?? '', district: p.district ?? '', region: p.region, city: p.city ?? 'Hong Kong',
        country: p.country ?? 'Hong Kong', postal_code: p.postal_code ?? '' });
      setMissing(p.missing);
    }).catch((err) => { if (err instanceof ApiError && err.status === 404) setMissing(['full_name', 'email', 'phone', 'address_line1', 'district']); else setError('Could not load your delivery details.'); });
  }, []);

  const set = (key: keyof ProfileInput) => (e: React.ChangeEvent<HTMLInputElement>) => setForm((f) => ({ ...f, [key]: e.target.value }));

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true); setError('');
    try {
      const saved = await api.saveProfile(TOKEN, { ...form, address_line2: form.address_line2 || null, postal_code: form.postal_code || null, region: form.region || null });
      setMissing(saved.missing);
      setForm((f) => ({ ...f, region: saved.region }));
      toast.success('Delivery details saved');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save.');
    } finally { setSaving(false); }
  }

  return <form className="grid gap-4" onSubmit={(e) => void save(e)}>
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
        <Select value={form.region ?? ''} onValueChange={(v) => setForm((f) => ({ ...f, region: v }))}>
          <SelectTrigger id="region" className="w-full"><SelectValue placeholder="From the district" /></SelectTrigger>
          <SelectContent>{REGIONS.map((r) => <SelectItem key={r} value={r}>{r}</SelectItem>)}</SelectContent>
        </Select>
      </F>
    </div>
    {error && <p className="text-sm text-destructive" role="alert">{error}</p>}
    <div className="flex items-center gap-3">
      <Button type="submit" disabled={saving}>{saving ? 'Saving…' : 'Save details'}</Button>
      {missing && missing.length === 0 && <span className="text-xs text-success">Ready for checkout</span>}
    </div>
  </form>;
}

function F({ id, label, children }: { id: string; label: string; children: React.ReactNode }) {
  return <div className="grid gap-1.5"><Label htmlFor={id}>{label}</Label>{children}</div>;
}
