import { Check, ChevronRight, Printer, ReceiptText } from 'lucide-react';
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
    <DialogContent className="receipt-dialog max-h-[92dvh] overflow-y-auto gap-0 p-0 sm:max-w-md">
      {record && <>
        <DialogHeader className="receipt-toolbar px-6 pt-5 pb-4">
          <DialogTitle className="flex items-center gap-2 text-sm"><ReceiptText className="size-4 text-stella" />Receipt</DialogTitle>
          <DialogDescription className="sr-only">{storeName(record.receipt.merchant_id)} · {when(record, 'long')}</DialogDescription>
        </DialogHeader>
        <div className="receipt-stage">
          <article id="printable-receipt" className="receipt-sheet">
            <header className="receipt-head">
              <span className="receipt-brand">MANDATE</span>
              <h2>{storeName(record.receipt.merchant_id)}</h2>
              <p>GROCERY RECEIPT</p>
              <time>{when(record, 'long')}</time>
              <span className="receipt-stamp"><Check aria-hidden="true" /> PAID</span>
            </header>
            <div className="receipt-rule" />
            <div className="receipt-columns"><span>ITEM / QTY</span><span>HKD</span></div>
            {receipts.detailLoading ? <Skeleton className="my-4 h-24" /> : record.quote ? <>
              <ul className="receipt-items">
                {record.quote.items.map((item) => <li key={item.product_id}>
                  <div className="receipt-item-name">{item.title}</div>
                  <div className="receipt-item-amount"><span>QTY {item.quantity}</span><span>{money(item.line_total_minor)}</span></div>
                </li>)}
              </ul>
              {record.quote.charges.length > 0 && <div className="receipt-charges">
                {record.quote.charges.map((c, i) => <div key={`${c.label}-${i}`}><span>{c.label}</span><span>{c.amount_minor ? money(c.amount_minor) : 'Free'}</span></div>)}
              </div>}
            </> : <p className="receipt-unavailable">{receipts.detailError || 'Item details are no longer available for this purchase.'}</p>}
            <div className="receipt-rule" />
            <div className="receipt-total"><span>TOTAL PAID</span><strong>{money(record.receipt.amount_minor)}</strong></div>
            <div className="receipt-payment"><span>Payment</span><span>{record.receipt.payment_route?.label.replace(/\s*\(scoped network token\)/gi, '') ?? 'Sandbox wallet'}</span></div>
            <div className="receipt-rule" />
            <dl className="receipt-reference">
              <dt>Receipt ID</dt><dd>{record.receipt.id}</dd>
              <dt>Transaction ID</dt><dd>{record.receipt.transaction_id}</dd>
            </dl>
            <footer className="receipt-foot"><span>Sandbox payment</span><span>Kept by Stella · Mandate</span></footer>
          </article>
        </div>
        <div className="receipt-actions px-6 pt-3 pb-5 no-print">
          <Button variant="outline" className="w-full bg-background" onClick={() => window.print()}><Printer />Print or save PDF</Button>
        </div>
      </>}

    </DialogContent>
  </Dialog>;
}
