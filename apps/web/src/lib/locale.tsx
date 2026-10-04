import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';
type Locale = 'en' | 'zh-HK';
const Context = createContext({ locale: 'en' as Locale, setLocale: (_value: Locale) => {}, t: (en: string, _zh: string) => en });
export function LocaleProvider({ children }: { children: ReactNode }) {
  const [locale, setLocale] = useState<Locale>(() => { try { return localStorage.getItem('mandate-locale') === 'zh-HK' ? 'zh-HK' : 'en'; } catch { return 'en'; } });
  useEffect(() => { document.documentElement.lang = locale; try { localStorage.setItem('mandate-locale', locale); } catch { /* Device storage unavailable. */ } }, [locale]);
  return <Context.Provider value={{ locale, setLocale, t: (en, zh) => locale === 'en' ? en : zh }}>{children}</Context.Provider>;
}
export const useLocale = () => useContext(Context);
