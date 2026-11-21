// Chaos mode: inject a test-only fault into a copy of the data and show what the pipeline and reports did.
import { useEffect, useState } from 'react'
import { getJson, postJson, fmt, dateTime, titleCase } from '../lib/api'
import { ErrorState, Loading, StatusPill } from './States'

type Fault = { label: string; description: string; source_table: string; domain: string }
type ReportCheck = { status: string; missing_evidence: string[]; causal_statements: string[]; finding_counts: Record<string, number> }
type Side = { rows_changed: number; dbt: { ran: boolean; reason?: string; counts?: Record<string, number> }; assessment: { quality: Record<string, string>; reports: Record<string, ReportCheck> } | null }
type ChaosRun = {
  run_id: string; created_at: string; fault: string; label: string; description: string; domain: string; source_table: string
  event: { anomaly_id: string; geography: string; start_utc: string; end_utc: string }
  fault_window: { start_utc: string; end_utc: string }; clean: Side; faulty: Side
  detected_by_dbt: boolean; failing_tests: string[]; downstream_models: string[]; affected_outputs: string[]
  report_checks: Record<string, Record<string, boolean>>
}
type RunSummary = { run_id: string; label: string; anomaly_id: string; created_at: string; detected_by_dbt: boolean }
type Event = { anomaly_id: string; geography: string; direction: string; start_utc: string }

const CHECK_LABELS: Record<string, string> = { not_complete: 'Report not marked complete', names_missing_evidence: 'Names the missing evidence', no_causal_claims: 'No causal claims', no_proven_causes: 'No proven causes' }

export function ChaosPanel() {
  const [faults, setFaults] = useState<Record<string, Fault>>({})
  const [events, setEvents] = useState<Event[]>([])
  const [runs, setRuns] = useState<RunSummary[]>([])
  const [fault, setFault] = useState('missing_feed')
  const [anomaly, setAnomaly] = useState('')
  const [includeAgent, setIncludeAgent] = useState(false)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState<ChaosRun | null>(null)

  useEffect(() => {
    Promise.all([getJson<Record<string, Fault>>('/api/chaos/faults'), getJson<{ items: Event[] }>('/api/anomalies', { limit: 100 }), getJson<RunSummary[]>('/api/chaos/runs')])
      .then(([available, anomalies, past]) => { setFaults(available); setEvents(anomalies.items); setAnomaly(anomalies.items[0]?.anomaly_id ?? ''); setRuns(past) })
      .catch((reason: Error) => setError(reason.message))
  }, [])

  async function run() {
    setRunning(true); setError('')
    try {
      setResult(await postJson<ChaosRun>('/api/chaos/runs', { fault, anomaly_id: anomaly, include_agent: includeAgent }))
      setRuns(await getJson<RunSummary[]>('/api/chaos/runs'))
    } catch (reason) { setError((reason as Error).message) } finally { setRunning(false) }
  }
  async function open(runId: string) {
    try { setResult(await getJson<ChaosRun>(`/api/chaos/runs/${runId}`)) } catch (reason) { setError((reason as Error).message) }
  }

  return <article className="chart-card full chaos">
    <h2>Chaos mode · test-only failure injection</h2>
    <p>Copies a slice of the data around one event into <code>data/chaos/</code>, breaks the copy, then re-runs <b>dbt build and tests</b>, the <b>data-quality gate</b> and the <b>investigation</b>, side by side with an unbroken copy. The production database is only read, never changed.</p>
    <div className="form-grid">
      <label>FAULT<select value={fault} onChange={(item) => setFault(item.target.value)}>{Object.entries(faults).map(([key, value]) => <option key={key} value={key}>{value.label}</option>)}</select></label>
      <label>EVENT<select value={anomaly} onChange={(item) => setAnomaly(item.target.value)}>{events.map((item) => <option key={item.anomaly_id} value={item.anomaly_id}>{item.geography} {item.direction} · {item.start_utc.slice(0, 16).replace('T', ' ')}</option>)}</select></label>
      <label className="checkbox"><input type="checkbox" checked={includeAgent} onChange={(item) => setIncludeAgent(item.target.checked)} /> Also run the AI agent (needs Ollama, +30–60 s)</label>
    </div>
    {faults[fault] && <p className="table-note">{faults[fault].description}</p>}
    <div className="run-bar"><button className="button primary" disabled={running || !anomaly} onClick={run}>{running ? 'Breaking a copy and re-running dbt…' : 'Run chaos experiment'}</button><small>Builds two small databases and runs the dbt project twice: about 30 seconds.</small></div>
    {running && <Loading label="Copying data, injecting the fault, running dbt build on both copies, re-checking quality and the report…" />}
    {error && <ErrorState message={error} hint={error.includes('Ollama') ? 'Start Ollama or untick the AI agent option.' : ''} />}
    {result && <ChaosResult result={result} />}
    {runs.length > 0 && <details className="sources"><summary>Earlier chaos runs ({runs.length})</summary>
      <div className="table-scroll"><table className="compact"><thead><tr><th>When</th><th>Fault</th><th>Event</th><th>dbt caught it</th><th /></tr></thead>
        <tbody>{runs.map((item) => <tr key={item.run_id} className="clickable" onClick={() => open(item.run_id)}><td>{dateTime(item.created_at)}</td><td>{item.label}</td><td>{item.anomaly_id}</td><td>{item.detected_by_dbt ? 'yes' : 'no'}</td><td className="link">Open →</td></tr>)}</tbody></table></div></details>}
  </article>
}

function ChaosResult({ result }: { result: ChaosRun }) {
  const clean = result.clean.assessment
  const faulty = result.faulty.assessment
  if (!faulty || !clean) return <ErrorState message={result.faulty.dbt.reason ?? 'The copy could not be assessed.'} hint="Chaos mode needs the dbt environment (.venv-dbt) to build the Gold views on each copy." />
  const domains = Object.keys(faulty.quality)
  return <div className="chaos-result">
    <h3>{result.label} · {result.event.geography} event of {result.event.start_utc.slice(0, 16).replace('T', ' ')} UTC <small className="muted">run {result.run_id} · {fmt(result.faulty.rows_changed, 0)} rows changed in the copy</small></h3>
    <div className="report-grid">
      <div>
        <h4>1 · dbt build and tests</h4>
        <p>Clean copy: {result.clean.dbt.counts?.pass ?? 0} tests passed. Broken copy: {result.faulty.dbt.counts?.pass ?? 0} passed, {(result.faulty.dbt.counts?.fail ?? 0) + (result.faulty.dbt.counts?.error ?? 0)} failed.</p>
        {result.detected_by_dbt ? <ul className="plain">{result.failing_tests.map((name) => <li key={name}><StatusPill status="fail" /> {name}</li>)}</ul>
          : <p className="warning-note">No dbt test failed. The project's schema tests (unique, not null, accepted values) cannot see rows that are absent; only the quality gate below caught this fault.</p>}
        <h4>2 · Downstream impact</h4>
        <p>dbt models built from <code>{result.source_table}</code>: {result.downstream_models.join(', ') || 'none'}.</p>
        <p>App outputs that read them: {result.affected_outputs.join(', ')}.</p>
      </div>
      <div>
        <h4>3 · Data-quality gate for the event window</h4>
        <table className="compact"><thead><tr><th>Domain</th><th>Clean copy</th><th>Broken copy</th></tr></thead>
          <tbody>{domains.map((name) => <tr key={name} className={clean.quality[name] !== faulty.quality[name] ? 'highlight' : ''}><td>{titleCase(name)}</td><td><StatusPill status={clean.quality[name]} /></td><td><StatusPill status={faulty.quality[name]} /></td></tr>)}</tbody></table>
        <h4>4 · Investigation report on the broken copy</h4>
        {Object.entries(result.report_checks).map(([generator, checks]) => <div key={generator} className="check-block">
          <b>{generator === 'agent' ? 'AI agent' : 'Rule-based'}</b> · clean <StatusPill status={clean.reports[generator]?.status} /> → broken <StatusPill status={faulty.reports[generator]?.status} />
          <ul className="plain">{Object.entries(checks).map(([name, ok]) => <li key={name} className={ok ? 'positive' : 'negative'}>{ok ? '✓' : '✗'} {CHECK_LABELS[name] ?? name}</li>)}</ul>
          <small className="muted">Missing evidence named: {faulty.reports[generator]?.missing_evidence.join(' · ') || 'none'}</small>
        </div>)}
      </div>
    </div>
  </div>
}
