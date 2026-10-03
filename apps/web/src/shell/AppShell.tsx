import { useEffect, useState } from 'react';
import { ShoppingBag, ShoppingBasket, UserRound, Wallet } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Toaster } from '@/components/ui/sonner';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { TooltipProvider } from '@/components/ui/tooltip';
import { cn } from '@/lib/utils';
import AllowanceSetup from './components/AllowanceSetup';
import { ReceiptDialog } from './components/Receipts';
import { AccountProvider, useAccount } from './data/account';
import BuyTab from './tabs/BuyTab';
import GroceriesTab from './tabs/GroceriesTab';
import ProfileTab from './tabs/ProfileTab';
import WalletTab from './tabs/WalletTab';
import './app.css';

/*
 * The app: three tabs, like a phone app. "Buy" is the one-time agent (anything, from the open web),
 * "Groceries" the repeat shopping under an allowance and its stores, "Wallet" the card and receipts, Profile holds the delivery details. The current tab lives in the URL hash so reloads and links keep it.
 */

const TABS = [
  { id: 'buy', label: 'Buy', icon: ShoppingBag },
  { id: 'groceries', label: 'Groceries', icon: ShoppingBasket },
  { id: 'wallet', label: 'Wallet', icon: Wallet },
  { id: 'profile', label: 'Profile', icon: UserRound },
] as const;
export type TabId = (typeof TABS)[number]['id'];

function tabFromHash(): TabId {
  const hash = window.location.hash.replace('#', '');
  return TABS.some((t) => t.id === hash) ? (hash as TabId) : 'buy';
}

export default function AppShell() {
  return <AccountProvider><TooltipProvider><Shell /></TooltipProvider></AccountProvider>;
}

function Shell() {
  const account = useAccount();
  const [tab, setTab] = useState<TabId>(tabFromHash);
  const online = account.online;

  useEffect(() => {
    const onHash = () => setTab(tabFromHash());
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);

  const go = (next: string) => {
    window.history.replaceState(null, '', `#${next}`);
    setTab(next as TabId);
  };

  return <>
    <Tabs value={tab} onValueChange={go} className="min-h-dvh gap-0">
      <header className="sticky top-0 z-20 border-b bg-background/80 backdrop-blur no-print">
        <div className="mx-auto flex h-14 max-w-3xl items-center gap-4 px-4">
          <span className="text-[15px] font-semibold tracking-tight">Mandate</span>
          <TabsList className="hidden md:inline-flex">
            {TABS.map(({ id, label, icon: Icon }) => <TabsTrigger key={id} value={id} className="gap-1.5 px-3"><Icon />{label}</TabsTrigger>)}
          </TabsList>
          <Badge variant="outline" className={cn('ml-auto gap-1.5 font-normal', online === false && 'text-destructive')}>
            <span className={cn('size-1.5 rounded-full', online === false ? 'bg-destructive' : 'bg-success')} />
            {online === false ? 'Wallet offline' : 'Sandbox'}
          </Badge>
        </div>
      </header>

      <main className="mx-auto w-full max-w-3xl flex-1 px-4 pt-6 pb-48 md:pb-36">
        {/* Every tab stays mounted: a checkout or a purchase in progress keeps running while you look elsewhere. */}
        <TabsContent value="buy" forceMount className="data-[state=inactive]:hidden"><BuyTab onOpenProfile={() => { go('profile'); }} /></TabsContent>
        <TabsContent value="groceries" forceMount className="data-[state=inactive]:hidden"><GroceriesTab onOpenWallet={() => go('wallet')} /></TabsContent>
        <TabsContent value="wallet" forceMount className="data-[state=inactive]:hidden"><WalletTab /></TabsContent>
        <TabsContent value="profile" forceMount className="data-[state=inactive]:hidden"><ProfileTab /></TabsContent>
      </main>

      {/* Phones: a bottom tab bar, clear of the home indicator. */}
      <nav className="fixed inset-x-0 bottom-0 z-20 border-t bg-background/90 backdrop-blur md:hidden no-print"
        style={{ paddingBottom: 'env(safe-area-inset-bottom)' }}>
        <TabsList variant="line" className="grid h-16! w-full grid-cols-4 rounded-none p-0">
          {TABS.map(({ id, label, icon: Icon }) => <TabsTrigger key={id} value={id}
            className="h-full min-h-11 flex-col gap-1 rounded-none text-xs after:hidden">
            <Icon className="size-5" />{label}
          </TabsTrigger>)}
        </TabsList>
      </nav>
      <Toaster position="top-center" />
    </Tabs>
    <AllowanceSetup open={account.setup !== null} change={account.setup === 'change'} onOpenChange={(open) => { if (!open) account.closeSetup(); }} />
    <ReceiptDialog />
  </>;
}
