import { ChevronRight, Printer, ReceiptText } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Skeleton } from '@/components/ui/skeleton';
import { money } from '@/lib/format';
import { useAccount, type ReceiptRecord } from '../data/account';
import { storeName } from './AllowanceSetup';

/* Paid grocery receipts from the wallet's audit trail, and one receipt to read or print. */

const when = (r: ReceiptRecord, style: 'short' | 'long' = 'short') => new Date(r.receipt.paid_at || r.occurredAt)
  .toLocaleString('en-HK', style === 'long' ? { dateStyle: 'long', timeStyle: 'short' } : { dateStyle: 'medium', timeStyle: 'short' });

export function ReceiptList({ limit }: { limit?: number }) {
  const { receipts } = useAccount();
  if (receipts.loading && !receipts.records.length) return <div className="space-y-2"><Skeleton className="h-14" /><Skeleton className="h-14" /></div>;
  if (receipts.error) return <div className="space-y-2 text-sm"><p className="text-destructive" role="alert">{receipts.error}</p>
    <Button variant="outline" size="sm" onClick={() => void receipts.load(true)}>Try again</Button></div>;
  if (!receipts.records.length) return <p className="text-sm text-muted-foreground">No paid receipts yet. Refused orders are not receipts.</p>;
  const shown = limit ? receipts.records.slice(0, limit) : receipts.records;
  return <ul className="divide-y rounded-xl border">
    {shown.map((record) => <li key={record.receipt.transaction_id}>
      <button className="flex min-h-14 w-full items-center gap-3 px-4 py-2 text-left hover:bg-accent/60" onClick={() => void receipts.open(record)}>
        <ReceiptText className="size-4 text-muted-foreground" />
        <span className="min-w-0 flex-1"><span className="block text-sm font-medium">{storeName(record.receipt.merchant_id)} groceries</span>
          <span className="block text-xs text-muted-foreground">{when(record)}</span></span>
        <span className="text-sm font-medium tabular-nums">{money(record.receipt.amount_minor)}</span>
        <ChevronRight className="size-4 text-muted-foreground" />
      </button>
    </li>)}
  </ul>;
}

export function ReceiptDialog() {
  const { receipts } = useAccount();
  const record = receipts.selected;
  return <Dialog open={Boolean(record)} onOpenChange={(open) => { if (!open) receipts.close(); }}>
    <DialogContent className="max-h-[92dvh] overflow-y-auto sm:max-w-md">
      {record && <>
        <DialogHeader>
          <DialogTitle>{storeName(record.receipt.merchant_id)}</DialogTitle>
          <DialogDescription>{when(record, 'long')} · sandbox payment</DialogDescription>
        </DialogHeader>
        <article id="printable-receipt" className="space-y-3 text-sm">
          {receipts.detailLoading ? <Skeleton className="h-24" /> : record.quote ? <ul className="space-y-1.5">
            {record.quote.items.map((item) => <li key={item.product_id} className="flex justify-between gap-4"><span>{item.quantity}× {item.title}</span><span className="tabular-nums">{money(item.line_total_minor)}</span></li>)}
            {record.quote.charges.map((c, i) => <li key={`${c.label}-${i}`} className="flex justify-between gap-4 text-muted-foreground"><span>{c.label}</span><span>{c.amount_minor ? money(c.amount_minor) : 'Free'}</span></li>)}
          </ul> : <p className="text-muted-foreground">{receipts.detailError || 'Item details are no longer available for this purchase.'}</p>}
          <div className="flex justify-between border-t pt-3 font-semibold"><span>Paid</span><span className="tabular-nums">{money(record.receipt.amount_minor)}</span></div>
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-xs text-muted-foreground">
            <dt>Payment route</dt><dd className="break-all">{record.receipt.payment_route?.label ?? 'Sandbox wallet'}</dd>
            <dt>Receipt</dt><dd className="break-all">{record.receipt.id}</dd>
            <dt>Transaction</dt><dd className="break-all">{record.receipt.transaction_id}</dd>
          </dl>
        </article>
        <Button variant="outline" className="no-print" onClick={() => window.print()}><Printer />Print or save PDF</Button>
      </>}
    </DialogContent>
  </Dialog>;
}
