import { useEffect, useState } from 'react';
import { RotateCcw, Trash2 } from 'lucide-react';
import type { ShoppingItem } from '../../../../../contracts/types';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import type { Groceries, Preset } from '../data/useGroceries';

/* Edit a shopping preset's name and list (saved on this device); the agent shops from the edited list. */

export default function PresetEditor({ g, preset, onClose }: { g: Groceries; preset: Preset | null; onClose: () => void }) {
  const [title, setTitle] = useState('');
  const [items, setItems] = useState<ShoppingItem[]>([]);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!preset) return;
    const draft = g.presetDraft(preset);
    setTitle(draft.title); setItems(draft.items); setError('');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [preset]);

  const update = (index: number, patch: Partial<ShoppingItem>) => setItems((cur) => cur.map((row, i) => (i === index ? { ...row, ...patch } : row)));
  const done = (result: string | null) => { if (result) setError(result); else onClose(); };
  const isDefault = preset ? g.isDefaultPreset(preset.id) : false;

  return <Dialog open={Boolean(preset)} onOpenChange={(open) => { if (!open) onClose(); }}>
    <DialogContent className="max-h-[92dvh] overflow-y-auto sm:max-w-lg">
      <DialogHeader><DialogTitle>Edit preset</DialogTitle>
        <DialogDescription>Saved on this device. The agent shops from this list; if it’s unavailable, the original preset is not used instead.</DialogDescription></DialogHeader>
      <div className="grid gap-1.5"><Label htmlFor="preset-title">Name</Label><Input id="preset-title" value={title} maxLength={60} onChange={(e) => setTitle(e.target.value)} /></div>
      <div className="grid gap-2">
        <Label>Items</Label>
        {items.map((item, i) => <div key={i} className="grid grid-cols-[1fr_4.5rem_5rem_auto] gap-2">
          <Input aria-label={`Item ${i + 1}`} value={item.name} maxLength={100} placeholder="e.g. jasmine rice" onChange={(e) => update(i, { name: e.target.value })} />
          <Input aria-label={`Quantity for item ${i + 1}`} type="number" min={1} max={20} value={item.quantity || ''} onChange={(e) => update(i, { quantity: e.target.value === '' ? 0 : Number(e.target.value) })} />
          <Input aria-label={`Unit for item ${i + 1}`} value={item.unit ?? ''} maxLength={24} placeholder="Unit" onChange={(e) => update(i, { unit: e.target.value })} />
          <Button variant="ghost" size="icon" aria-label={`Remove ${item.name || `item ${i + 1}`}`} onClick={() => setItems((cur) => cur.filter((_, j) => j !== i))}><Trash2 /></Button>
        </div>)}
        <Button variant="outline" size="sm" className="justify-self-start" disabled={items.length >= 20} onClick={() => setItems((cur) => [...cur, { name: '', quantity: 1 }])}>Add an item</Button>
      </div>
      {error && <p className="text-sm text-destructive" role="alert">{error}</p>}
      <DialogFooter className="gap-2 sm:justify-between">
        <Button variant="ghost" onClick={() => preset && done(g.resetPreset(preset.id))}>{isDefault ? <><RotateCcw />Reset</> : <><Trash2 />Delete</>}</Button>
        <div className="flex gap-2">
          <Button variant="outline" onClick={() => preset && done(g.savePreset(preset.id, title, items, true))}>Save a copy</Button>
          <Button onClick={() => preset && done(g.savePreset(preset.id, title, items))}>Save</Button>
        </div>
      </DialogFooter>
    </DialogContent>
  </Dialog>;
}
