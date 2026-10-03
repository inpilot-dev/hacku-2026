import React, { Suspense, lazy } from 'react';
import ReactDOM from 'react-dom/client';

// The tabbed app is the default; `?legacy` keeps the previous single-page flow, `?classic` the full dashboard
// (evidence, audit, safety lab), `?card` the virtual card page, `?about` the product introduction and `?security`
// the agent-harness security lab.
const params = new URLSearchParams(window.location.search);
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
  <React.StrictMode><Suspense fallback={null}><Root /></Suspense></React.StrictMode>,
);
