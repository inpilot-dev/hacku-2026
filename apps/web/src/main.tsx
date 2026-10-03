import React, { Suspense, lazy } from 'react';
import ReactDOM from 'react-dom/client';

// The tabbed app is the default; `?legacy` keeps the previous single-page flow, `?classic` the full dashboard
// (evidence, audit, safety lab), `?card` the virtual card page, `?about` the product introduction and `?security`
// the agent-harness security lab.
const params = new URLSearchParams(window.location.search);
if (![...params.keys()].some((key) => ['classic', 'card', 'about', 'security', 'legacy'].includes(key))) {
  try {
    const theme = localStorage.getItem('mandate-theme-v1') === 'dark' ? 'dark' : 'light';
    document.documentElement.dataset.theme = theme;
    document.documentElement.classList.toggle('dark', theme === 'dark');
  } catch { document.documentElement.dataset.theme = 'light'; }
}

class AppBoundary extends React.Component<{ children: React.ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() {
    if (this.state.failed) return <div role="alert" style={{ maxWidth: 480, margin: '15vh auto', padding: 24, fontFamily: 'system-ui' }}>
      <h1 style={{ fontSize: 22 }}>Couldn’t open Mandate</h1>
      <button onClick={() => window.location.reload()} style={{ padding: '10px 18px', borderRadius: 12, cursor: 'pointer' }}>Reload</button>
    </div>;
    return this.props.children;
  }
}
const Root = params.has('classic')
  ? lazy(() => import('./styles.css').then(() => import('./app/App')))
  : params.has('card')
    ? lazy(() => import('./simple/simple.css').then(() => import('./card/CardSetup')))
    : params.has('about')
      ? lazy(() => import('./landing/Landing'))
      : params.has('security')
        ? lazy(() => import('./security/SecurityLab'))
        : params.has('legacy')
          ? lazy(() => import('./simple/simple.css').then(() => import('./simple/SimpleApp')))
          : lazy(() => import('./shell/AppShell'));

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode><AppBoundary><Suspense fallback={<div role="status" aria-label="Loading Mandate" style={{ padding: 24, fontFamily: 'system-ui', color: '#777' }}>Mandate</div>}><Root /></Suspense></AppBoundary></React.StrictMode>,
);
