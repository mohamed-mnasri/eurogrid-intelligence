// Data Operations: connector health, ingestion runs, dbt tests, lineage, freshness and the source register.
import { useEffect, useState, type ReactNode } from 'react'
import { getJson, fmt, dateTime, titleCase } from '../lib/api'
import { Empty, ErrorState, Loading, PageHeader, StatusPill } from '../components/States'

type Connector = { provider: string; dataset: string; runs: number; successes: number; failures: number; cache_hits: number; rows_received: number; last_started: string; last_success: string | null; last_status: string; last_error: string | null; health: string }
type Run = { run_id: number; provider: string; dataset: string; geography: string; start: string | null; end: string | null; started_at: string; status: string; rows_received: number; error_details: string | null; cache_hit: boolean }
type Ingestion = { connectors: Connector[]; recent_runs: Run[]; note?: string }
type DbtResult = { name: string; status: string; failures: number | null; message: string | null; model: string | null; execution_time_s: number }
type Lineage = { name: string; kind: string; materialized: string; parents: string[]; downstream: string[] }
type SourceFreshness = { source: string; status: string; max_loaded_at: string | null; age_hours: number | null }
type Dbt = { available: boolean; note?: string; generated_at?: string; command?: string; counts?: Record<string, number>; results?: DbtResult[]; lineage?: Lineage[]; freshness?: SourceFreshness[] }
type Fresh = { geography: string; domain: string; model: string; rows: number; valid_pct: number; last_interval_utc: string; missing_hours: number; expected_hours: number; age_days: number | null; status: string }
type Freshness = { reference_utc: string; items: Fresh[]; rules: string }
type PipelineStage = { stage: string; status: string; duration_s: number | null; details: Record<string, unknown>; error: string | null }
type PipelineRun = { run_id: string; started_at: string; status: string; window: string[] | null; stages: PipelineStage[] }
type Source = { provider: string; datasets: string; licence: string; attribution: string; access: string; url: string; status: string }

function stageNote(stage: PipelineStage) {
  const d = stage.details
  if (stage.error) return ` · ${stage.error.split('\n')[0].slice(0, 80)}`
  if (typeof d.reason === 'string') return ` · ${d.reason}`
  if (stage.stage === 'ingest' && typeof d.rows_received === 'number') return ` · ${fmt(d.rows_received, 0)} rows${Array.isArray(d.failures) && d.failures.length ? `, ${d.failures.length} failed` : ''}`
  if (stage.stage === 'normalize' && typeof d.files === 'number') return ` · ${d.files} new file(s)`
  if (stage.stage === 'dbt' && Array.isArray(d.failing) && d.failing.length) return ` · failing: ${d.failing.join(', ')}`
  if (stage.stage === 'dbt' && d.counts) return ` · ${Object.entries(d.counts as Record<string, number>).map(([k, v]) => `${v} ${k}`).join(', ')}`
  if (stage.stage === 'detect' && d.events) return ` · ${Object.values(d.events as Record<string, number>).reduce((a, b) => a + b, 0)} event(s)`
  return ''
}

const DOMAINS = ['price', 'generation', 'load', 'cross_border_flows', 'weather']

function Section({ title, intro, children }: { title: string; intro?: ReactNode; children: ReactNode }) {
  return <article className="chart-card full"><h2>{title}</h2>{intro && <p>{intro}</p>}{children}</article>
}

export default function DataOperations({ chaos }: { chaos?: ReactNode }) {
  const [ingestion, setIngestion] = useState<Ingestion | null>(null)
  const [dbt, setDbt] = useState<Dbt | null>(null)
  const [freshness, setFreshness] = useState<Freshness | null>(null)
  const [sources, setSources] = useState<Source[]>([])
  const [pipeline, setPipeline] = useState<PipelineRun[]>([])
  const [error, setError] = useState('')
  const [reload, setReload] = useState(0)

  useEffect(() => {
    Promise.all([getJson<Ingestion>('/api/operations/ingestion'), getJson<Dbt>('/api/operations/dbt'), getJson<Freshness>('/api/operations/freshness'), getJson<Source[]>('/api/operations/sources'), getJson<PipelineRun[]>('/api/operations/pipeline')])
      .then(([a, b, c, d, e]) => { setIngestion(a); setDbt(b); setFreshness(c); setSources(d); setPipeline(e); setError('') })
      .catch((reason: Error) => setError(reason.message))
  }, [reload])

  if (error) return <main className="dashboard"><PageHeader eyebrow="DATA OPERATIONS" title="Pipeline health" /><ErrorState message={error} onRetry={() => setReload((value) => value + 1)} /></main>
  if (!ingestion || !dbt || !freshness) return <main className="dashboard"><PageHeader eyebrow="DATA OPERATIONS" title="Pipeline health" /><Loading label="Reading the ingestion ledger, dbt artifacts and Gold models…" /></main>

  const failing = ingestion.connectors.filter((item) => item.health !== 'healthy')
  const testsFailed = (dbt.counts?.fail ?? 0) + (dbt.counts?.error ?? 0)
  const statusCounts = freshness.items.reduce<Record<string, number>>((acc, item) => ({ ...acc, [item.status]: (acc[item.status] ?? 0) + 1 }), {})
  const geographies = [...new Set(freshness.items.map((item) => item.geography))].sort()
  const cell = (geo: string, domain: string) => freshness.items.find((item) => item.geography === geo && item.domain === domain)
  const newest = freshness.items.map((item) => item.last_interval_utc).sort().at(-1)

  return <main className="dashboard">
    <PageHeader eyebrow="DATA OPERATIONS" title="Pipeline health and data coverage" aside={<button className="button" onClick={() => setReload((value) => value + 1)}>Refresh</button>}>Connector runs from the ingestion ledger, dbt test results and lineage from dbt's own artifacts, and coverage of every Gold model by country. Everything here is read-only.</PageHeader>
    <section className="metrics">
      <article><small>CONNECTORS</small><strong>{ingestion.connectors.length - failing.length}/{ingestion.connectors.length} healthy</strong><p>{failing.length ? `Last run failed: ${failing.map((item) => `${item.provider} ${item.dataset}`).join(', ')}.` : 'The latest run of every connector succeeded.'}</p></article>
      <article><small>DBT TESTS</small><strong>{dbt.available ? `${dbt.counts?.pass ?? 0} passed${testsFailed ? ` · ${testsFailed} failed` : ''}` : 'No results'}</strong><p>{dbt.available ? `From ${dbt.command ?? 'dbt'} at ${dateTime(dbt.generated_at)}.` : dbt.note}</p></article>
      <article><small>COVERAGE (COUNTRY × DOMAIN)</small><strong>{Object.entries(statusCounts).map(([status, count]) => `${count} ${status}`).join(' · ')}</strong><p>{freshness.rules}</p></article>
      <article><small>NEWEST DATA</small><strong>{newest ? newest.slice(0, 10) : '—'}</strong><p>Latest interval end across all Gold models, checked {dateTime(freshness.reference_utc)}.</p></article>
    </section>
    <section className="charts">
      <Section title="Pipeline runs" intro={<>Each <code>python -m eurogrid.pipeline run</code>: ingest → normalize (new raw files only, newest fetch wins) → dbt build and tests → anomaly detection. A failed stage stops the run.</>}>
        {pipeline.length === 0 ? <Empty title="No pipeline runs yet.">Run <code>python -m eurogrid.pipeline run</code> to ingest the days since the newest stored price.</Empty>
          : <div className="table-scroll"><table><thead><tr><th>Started</th><th>Window</th><th>Status</th>{['ingest', 'normalize', 'dbt', 'detect'].map((name) => <th key={name}>{titleCase(name)}</th>)}</tr></thead>
            <tbody>{pipeline.map((run) => <tr key={run.run_id}><td>{dateTime(run.started_at)}</td><td>{run.window ? `${run.window[0]} → ${run.window[1]}` : 'up to date'}</td><td><StatusPill status={run.status} /></td>
              {['ingest', 'normalize', 'dbt', 'detect'].map((name) => { const stage = run.stages.find((item) => item.stage === name); return <td key={name}>{stage ? <><StatusPill status={stage.status} /><small className="stage-note">{stage.duration_s != null ? `${fmt(stage.duration_s, 1)} s` : ''}{stageNote(stage)}</small></> : '—'}</td> })}</tr>)}</tbody></table></div>}
      </Section>
      {chaos}
      <Section title="Coverage by country and domain" intro="Each cell: status, latest interval, and hours missing between the first and last interval. Missing hours are counted, never filled.">
        {freshness.items.length === 0 ? <Empty title="No Gold data found." /> : <div className="table-scroll"><table className="matrix"><thead><tr><th>Geography</th>{DOMAINS.map((domain) => <th key={domain}>{titleCase(domain)}</th>)}</tr></thead>
          <tbody>{geographies.map((geo) => <tr key={geo}><td>{(geo ?? 'unassigned').toUpperCase()}</td>{DOMAINS.map((domain) => { const item = cell(geo, domain); return <td key={domain}>{item ? <><StatusPill status={item.status} /><small>to {item.last_interval_utc.slice(0, 10)}{item.missing_hours ? ` · ${fmt(item.missing_hours, 0)} h missing` : ''}{item.valid_pct < 100 ? ` · ${fmt(item.valid_pct, 1)}% valid` : ''}</small></> : <span className="muted">not ingested</span>}</td> })}</tr>)}</tbody></table></div>}
      </Section>
      <Section title="Connectors" intro="One row per provider dataset, from data/metadata/ingestion_runs.sqlite3.">
        {ingestion.connectors.length === 0 ? <Empty title="No ingestion runs recorded yet." /> : <div className="table-scroll"><table><thead><tr><th>Provider</th><th>Dataset</th><th>Health</th><th>Runs</th><th>Failures</th><th>Rows received</th><th>Last success</th><th>Last error</th></tr></thead>
          <tbody>{ingestion.connectors.map((item) => <tr key={`${item.provider}-${item.dataset}`}><td>{item.provider}</td><td>{item.dataset}</td><td><StatusPill status={item.health} /></td><td>{item.runs}</td><td>{item.failures}</td><td>{fmt(item.rows_received, 0)}</td><td>{dateTime(item.last_success)}</td><td className="wrap">{item.last_error ?? '—'}</td></tr>)}</tbody></table></div>}
      </Section>
      <Section title="Recent ingestion runs">
        <div className="table-scroll"><table><thead><tr><th>Run</th><th>Started</th><th>Source</th><th>Geography</th><th>Period</th><th>Status</th><th>Rows</th><th>Error</th></tr></thead>
          <tbody>{ingestion.recent_runs.map((run) => <tr key={run.run_id}><td>{run.run_id}</td><td>{dateTime(run.started_at)}</td><td>{run.provider} · {run.dataset}</td><td>{run.geography || '—'}</td><td>{run.start ?? '—'} → {run.end ?? '—'}</td><td><StatusPill status={run.status} /></td><td>{fmt(run.rows_received, 0)}{run.cache_hit ? ' (cache)' : ''}</td><td className="wrap">{run.error_details ?? '—'}</td></tr>)}</tbody></table></div>
      </Section>
      <Section title="dbt tests" intro={dbt.available ? `${dbt.results?.length ?? 0} results from target/run_results.json.` : dbt.note}>
        {dbt.available && (dbt.freshness ?? []).length > 0 && <div className="table-scroll"><table className="compact"><thead><tr><th>Source freshness</th><th>Status</th><th>Newest interval</th><th>Age</th></tr></thead>
          <tbody>{(dbt.freshness ?? []).map((item) => <tr key={item.source}><td>{item.source}</td><td><StatusPill status={item.status === 'pass' ? 'complete' : item.status === 'warn' ? 'stale' : 'missing'} /> <small className="muted">{item.status}</small></td><td>{dateTime(item.max_loaded_at)}</td><td>{item.age_hours != null ? `${fmt(item.age_hours / 24, 1)} days` : '—'}</td></tr>)}</tbody></table>
          <p className="table-note">dbt source freshness: warn after 3 days, error after 10 days without new intervals (run by the pipeline before each build).</p></div>}
        {dbt.available && <div className="table-scroll"><table><thead><tr><th>Test</th><th>Model</th><th>Status</th><th>Failing rows</th><th>Time</th></tr></thead>
          <tbody>{(dbt.results ?? []).map((item) => <tr key={item.name}><td className="wrap">{item.name}</td><td>{item.model ?? '—'}</td><td><StatusPill status={item.status} /></td><td>{item.failures ?? '—'}</td><td>{fmt(item.execution_time_s, 2)} s</td></tr>)}</tbody></table></div>}
      </Section>
      <Section title="Lineage: what a failure would affect" intro="Each dbt source and model, its direct parents, and every downstream model that depends on it (from target/manifest.json).">
        {dbt.available && <div className="table-scroll"><table><thead><tr><th>Node</th><th>Kind</th><th>Built as</th><th>Reads from</th><th>Downstream models affected</th></tr></thead>
          <tbody>{(dbt.lineage ?? []).map((item) => <tr key={item.name}><td>{item.name}</td><td>{item.kind}</td><td>{item.materialized}</td><td className="wrap">{item.parents.join(', ') || '—'}</td><td className="wrap">{item.downstream.join(', ') || 'none'}</td></tr>)}</tbody></table></div>}
      </Section>
      <Section title="Source and licence register" intro="Restricted price zones are not redistributed; every response's own licence field is preserved with the raw file.">
        <div className="table-scroll"><table><thead><tr><th>Provider</th><th>Datasets</th><th>Licence</th><th>Access</th><th>Status</th></tr></thead>
          <tbody>{sources.map((item) => <tr key={item.provider}><td><a href={item.url} target="_blank" rel="noreferrer">{item.provider}</a></td><td className="wrap">{item.datasets}</td><td className="wrap">{item.licence}<br /><small className="muted">{item.attribution}</small></td><td className="wrap">{item.access}</td><td><StatusPill status={item.status} /></td></tr>)}</tbody></table></div>
      </Section>
    </section>
  </main>
}
