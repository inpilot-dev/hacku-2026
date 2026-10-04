import { LocaleProvider, useLocale } from '@/lib/locale';
import BrandLogo from '../components/BrandLogo';
import { useEffect, useState } from 'react';
import { Moon, Sun, ShoppingBag, ShoppingBasket, UserRound, Wallet } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Alert, AlertDescription } from '@/components/ui/alert';
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
  return TABS.some((t) => t.id === hash) ? (hash as TabId) : 'groceries';
}

export default function AppShell() {
  return <LocaleProvider><AccountProvider><TooltipProvider><Shell /></TooltipProvider></AccountProvider></LocaleProvider>;
}

function Shell() {
  const account = useAccount();
  const { locale, setLocale, t } = useLocale();
  const [tab, setTab] = useState<TabId>(tabFromHash);
  const online = account.online;
  const [theme, setTheme] = useState<'light' | 'dark'>(() => {
    try { return localStorage.getItem('mandate-theme-v1') === 'dark' ? 'dark' : 'light'; } catch { return 'light'; }
  });
  const [returnToBuy, setReturnToBuy] = useState(false);
  const [profileRevision, setProfileRevision] = useState(0);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    document.documentElement.classList.toggle('dark', theme === 'dark');
    try { localStorage.setItem('mandate-theme-v1', theme); } catch { /* Theme still works without storage. */ }
  }, [theme]);

  useEffect(() => {
    const onHash = () => setTab(tabFromHash());
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);

  const go = (next: string) => {
    window.history.replaceState(null, '', `#${next}`);
    setTab(next as TabId);
    window.scrollTo(0, 0);
  };

  return <>
    <Tabs value={tab} onValueChange={go} className="min-h-dvh gap-0">
      <header className="sticky top-0 z-20 border-b bg-background/80 backdrop-blur no-print">
        <div className="mx-auto flex h-14 max-w-3xl items-center gap-2 sm:gap-4 px-4">
          <span className="text-[17px] font-semibold tracking-tight"><BrandLogo /></span>
          <TabsList className="hidden md:inline-flex">
            {TABS.map(({ id, label, icon: Icon }) => <TabsTrigger key={id} value={id} className="gap-1.5 px-3"><Icon />{t(label, ({ Buy: '購物', Groceries: '日用品', Wallet: '錢包', Profile: '個人資料' } as Record<string, string>)[label])}</TabsTrigger>)}
          </TabsList>
          <Badge variant="outline" className={cn('ml-auto gap-1.5 font-normal', online === false && 'text-destructive')}>
            <span className={cn('size-1.5 rounded-full', online === false ? 'bg-destructive' : online === null ? 'bg-muted-foreground' : 'bg-success')} />
            {online === false ? 'Offline' : online === null ? 'Connecting' : 'Sandbox'}
          </Badge>
          <Button variant="ghost" size="sm" onClick={() => setLocale(locale === 'en' ? 'zh-HK' : 'en')}>{locale === 'en' ? '繁中' : 'English'}</Button>
          <Button variant="ghost" size="icon" className="shrink-0 rounded-full" onClick={() => setTheme((current) => current === 'light' ? 'dark' : 'light')}
            aria-label={theme === 'light' ? 'Switch to dark mode' : 'Switch to light mode'} title={theme === 'light' ? 'Dark mode' : 'Light mode'}>
            {theme === 'light' ? <Moon /> : <Sun />}
          </Button>
        </div>
      </header>

      <main className="mx-auto w-full max-w-3xl flex-1 px-4 pt-6 pb-48 md:pb-36">
        {(online === false || account.refreshError) && <Alert className="mb-5"><AlertDescription className="flex items-center justify-between gap-3"><span>{online === false ? 'Can’t connect to the server. Reconnecting…' : account.refreshError}</span><Button variant="outline" size="sm" onClick={() => void account.refresh()}>Retry</Button></AlertDescription></Alert>}
        {/* Every tab stays mounted: a checkout or a purchase in progress keeps running while you look elsewhere. */}
        <TabsContent value="buy" forceMount className="data-[state=inactive]:hidden"><BuyTab profileRevision={profileRevision} active={tab === 'buy'} onOpenProfile={() => { setReturnToBuy(true); go('profile'); }} /></TabsContent>
        <TabsContent value="groceries" forceMount className="data-[state=inactive]:hidden"><GroceriesTab active={tab === 'groceries'} onOpenWallet={() => go('wallet')} /></TabsContent>
        <TabsContent value="wallet" forceMount className="data-[state=inactive]:hidden"><WalletTab /></TabsContent>
        <TabsContent value="profile" forceMount className="data-[state=inactive]:hidden"><ProfileTab returning={returnToBuy} onSaved={() => { setProfileRevision((value) => value + 1); if (returnToBuy) { setReturnToBuy(false); go('buy'); } }} /></TabsContent>
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
      <Toaster position="top-center" theme={theme} />
    </Tabs>
    <AllowanceSetup open={account.setup !== null} change={account.setup === 'change'} onOpenChange={(open) => { if (!open) account.closeSetup(); }} />
    <ReceiptDialog />
  </>;
}
