// Application shell: sidebar navigation and hash-based routing (no router dependency).
import { lazy, Suspense, useEffect, useState } from 'react'
import './styles.css'
import './app.css'
import { getJson, daysSince } from './lib/api'
import { Loading } from './components/States'
import { ErrorBoundary } from './components/ErrorBoundary'
import { ChaosPanel } from './components/ChaosPanel'
import { Welcome } from './components/Welcome'

// Each page is its own chunk, loaded when first opened.
const MarketOverview = lazy(() => import('./pages/MarketOverview'))
const MarketAnalytics = lazy(() => import('./pages/MarketAnalytics'))
const Investigations = lazy(() => import('./pages/Investigations'))
const Planner = lazy(() => import('./pages/Planner'))
const DataOperations = lazy(() => import('./pages/DataOperations'))
const ScenarioLab = lazy(() => import('./pages/ScenarioLab'))
const Replay = lazy(() => import('./pages/Replay'))

type Route = { id: string; label: string; hint: string }
const ROUTES: Route[] = [
  { id: 'overview', label: 'Market Overview', hint: 'Prices, grid use, generation' },
  { id: 'analytics', label: 'Market Analytics', hint: 'Countries, seasons, anomalies' },
  { id: 'investigations', label: 'AI Investigations', hint: 'Cases, evidence, trace' },
  { id: 'planner', label: 'Renewable Planner', hint: 'Solar + battery payback' },
  { id: 'replay', label: 'Event Replay', hint: 'Market event → household' },
  { id: 'operations', label: 'Data Operations', hint: 'Connectors, tests, freshness' },
  { id: 'lab', label: 'Scenario Lab', hint: 'Synthetic evaluation' },
]

function currentHash() {
  const [page = 'overview', ...rest] = window.location.hash.replace(/^#\/?/, '').split('/')
  return { page: ROUTES.some((route) => route.id === page) ? page : 'overview', param: rest.join('/') }
}

export default function App() {
  const [route, setRoute] = useState(currentHash)
  const [lastDate, setLastDate] = useState<string | null>(null)
  const [apiDown, setApiDown] = useState(false)
  const [dataset, setDataset] = useState('')

  useEffect(() => {
    const onChange = () => setRoute(currentHash())
    window.addEventListener('hashchange', onChange)
    return () => window.removeEventListener('hashchange', onChange)
  }, [])
  useEffect(() => {
    document.title = `EuroGrid · ${ROUTES.find((item) => item.id === route.page)?.label ?? 'Market Overview'}`
  }, [route.page])
  useEffect(() => {
    getJson<{ markets: { last_source_date: string | null }[] }>('/api/coverage')
      .then(({ markets }) => setLastDate(markets.map((item) => item.last_source_date ?? '').sort().at(-1) || null))
      .catch(() => setApiDown(true))
    getJson<{ dataset?: string }>('/api/health').then((value) => setDataset(value.dataset ?? '')).catch(() => setDataset(''))
  }, [])

  const age = daysSince(lastDate)
  return <div className="app">
    <aside className="sidebar">
      <div className="brand"><span className="brand-mark" aria-hidden="true">⚡</span><div><b>EuroGrid</b><small>Grid-to-Home Intelligence</small></div></div>
      <nav aria-label="Pages">
        {ROUTES.map((item) => <a key={item.id} href={`#/${item.id}`} className={route.page === item.id ? 'active' : ''} aria-current={route.page === item.id ? 'page' : undefined}>
          <span>{item.label}</span><small>{item.hint}</small>
        </a>)}
      </nav>
      <div className="sidebar-footer">
        {apiDown ? <span className="pill pill-failing">API offline</span>
          : lastDate ? <span className={`pill ${age != null && age > 7 ? 'pill-stale' : 'pill-complete'}`} title="Newest market interval in the Gold models">Data to {lastDate}{age != null && age > 7 ? ` · ${age} d old` : ''}</span>
            : <span className="pill">Checking data…</span>}
        {dataset === 'synthetic' && <span className="pill pill-coinciding" title="The API is serving the synthetic demo dataset">Synthetic demo data</span>}
        <small>Local research tool · not live market data</small>
      </div>
    </aside>
    <div className="content">
      {dataset === 'synthetic' && <div className="demo-banner" role="note"><b>Synthetic demo data.</b> Invented values for Germany and France (Feb–Mar 2026) with a known injected price spike on 5 March, built by <code>python -m eurogrid.demo</code>. Not real market data.</div>}
      <div className="welcome-wrap"><Welcome /></div>
      <ErrorBoundary resetKey={route.page}>
      <Suspense fallback={<main className="dashboard"><Loading label="Opening page…" /></main>}>
      {route.page === 'overview' && <MarketOverview />}
      {route.page === 'analytics' && <MarketAnalytics />}
      {route.page === 'investigations' && <Investigations anomalyId={route.param} />}
      {route.page === 'planner' && <Planner />}
      {route.page === 'replay' && <Replay anomalyId={route.param} />}
      {route.page === 'operations' && <DataOperations chaos={<ChaosPanel />} />}
      {route.page === 'lab' && <ScenarioLab />}
      </Suspense>
      </ErrorBoundary>
      <footer className="app-footer">EUROGRID INTELLIGENCE · LOCAL RESEARCH APPLICATION</footer>
    </div>
  </div>
}
