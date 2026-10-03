import React, { Suspense, lazy } from 'react';
import ReactDOM from 'react-dom/client';

// The simple flow is the default; `?classic` keeps the full dashboard (evidence, audit, safety lab).
const classic = new URLSearchParams(window.location.search).has('classic');
const Root = classic
  ? lazy(() => import('./styles.css').then(() => import('./app/App')))
  : lazy(() => import('./simple/simple.css').then(() => import('./simple/SimpleApp')));

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode><Suspense fallback={null}><Root /></Suspense></React.StrictMode>,
);
