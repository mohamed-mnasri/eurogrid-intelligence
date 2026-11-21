// Scenario Lab: synthetic cases with known injected conditions, scored per dimension (handbook 13.3).
import { Fragment, useEffect, useState } from 'react'
import { getJson, postJson, dateTime, titleCase } from '../lib/api'
import { Empty, ErrorState, Loading, PageHeader, StatusPill } from '../components/States'

type CaseResult = { case_id: string; label: string; description: string; generator: string; status: string | null; report_id: string | null; checks: Record<string, boolean>; passed: boolean
  details: { tools_called?: string[]; cited_signal_tools?: string[]; unexpected_signals?: string[]; causal_statements?: string[]; missing_evidence?: string[]; finding_counts?: Record<string, number> } }
type CaseDefinition = { case_id: string; label: string; description: string; expected_signals: string[]; expected_status: string }
type Run = { generator: string; created_at: string; cases: CaseResult[]; dimension_totals: Record<string, { passed: number; scored: number }>; cases_passed: number; cases_total: number; rules: string; not_covered: string; case_definitions: CaseDefinition[] }
type History = { run_id: number; generator: string; created_at: string; cases_passed: number; cases_total: number; failed_checks: Record<string, string[]> }

const DIMENSIONS = ['detected', 'tool_selection', 'parameter_accuracy', 'evidence_accuracy', 'data_quality_handling', 'expected_signals', 'no_false_signals', 'unsupported_claims', 'repeatability']
const generatorName = (value: string) => value.startsWith('langgraph') ? `AI agent (${value.replace('langgraph-ollama:', '')})` : 'Rule-based'

export default function ScenarioLab() {
  const [runs, setRuns] = useState<Run[] | null>(null)
  const [history, setHistory] = useState<History[]>([])
  const [error, setError] = useState('')
  const [running, setRunning] = useState(false)
  const [open, setOpen] = useState('')
  const [reload, setReload] = useState(0)

  useEffect(() => {
    Promise.all([getJson<Run[]>('/api/evaluations'), getJson<History[]>('/api/evaluations/history')])
      .then(([latest, past]) => { setRuns(latest); setHistory(past); setError('') }).catch((reason: Error) => setError(reason.message))
  }, [reload])

  async function runSuite() {
    setRunning(true)
    try { await postJson('/api/evaluations/run'); setReload((value) => value + 1) } catch (reason) { setError((reason as Error).message) } finally { setRunning(false) }
  }

  const definitions = runs?.[0]?.case_definitions ?? []
  return <main className="dashboard">
    <PageHeader eyebrow="SCENARIO LAB" title="Does the investigator get known cases right?" aside={<button className="button primary" onClick={runSuite} disabled={running}>{running ? 'Running 7 cases…' : 'Run rule-based suite'}</button>}>
      Each case builds a separate synthetic database with one known condition injected around a German price spike, then detects, investigates and scores the result. These are synthetic test cases, not real market events, and there is no blended accuracy score: every check is shown as passed or failed.
    </PageHeader>
    {error && <ErrorState message={error} onRetry={() => setReload((value) => value + 1)} />}
    {!runs && !error && <Loading label="Loading evaluation results…" />}
    {runs && runs.length === 0 && <Empty title="No evaluation has been run yet.">Run the rule-based suite here (a few seconds). Agent runs call the local model several times per case, so run them from a terminal: <code>python -m eurogrid.evaluation --generator agent</code></Empty>}
    {runs && runs.length > 0 && <section className="charts">
      <section className="metrics wide">{runs.map((run) => <article key={run.generator}><small>{generatorName(run.generator).toUpperCase()}</small><strong>{run.cases_passed}/{run.cases_total} cases pass every check</strong><p>Run {dateTime(run.created_at)}. {Object.values(run.dimension_totals).some((value) => value.passed < value.scored) ? `Weakest check: ${Object.entries(run.dimension_totals).sort(([, a], [, b]) => a.passed / a.scored - b.passed / b.scored)[0][0].replaceAll('_', ' ')}.` : 'No check failed.'}</p></article>)}</section>
      {runs.map((run) => <article key={run.generator} className="chart-card full">
        <h2>{generatorName(run.generator)} · results by case and check</h2>
        <p>✓ passed · ✗ failed · blank = not scored for this generator. Click a case for what the investigator did.</p>
        <div className="table-scroll"><table className="matrix"><thead><tr><th>Case</th><th>Status</th>{DIMENSIONS.map((dimension) => <th key={dimension}>{titleCase(dimension)}<br /><small>{run.dimension_totals[dimension] ? `${run.dimension_totals[dimension].passed}/${run.dimension_totals[dimension].scored}` : '—'}</small></th>)}</tr></thead>
          <tbody>{run.cases.map((item) => { const key = `${run.generator}-${item.case_id}`; return <Fragment key={key}>
            <tr className="clickable" onClick={() => setOpen(open === key ? '' : key)}><td><b>{item.label}</b> {item.passed ? <span className="pill pill-complete">pass</span> : <span className="pill pill-failing">fail</span>}</td><td><StatusPill status={item.status} /></td>
              {DIMENSIONS.map((dimension) => <td key={dimension} className={`check ${item.checks[dimension] === undefined ? '' : item.checks[dimension] ? 'ok' : 'bad'}`}>{item.checks[dimension] === undefined ? '' : item.checks[dimension] ? '✓' : '✗'}</td>)}</tr>
            {open === key && <tr className="expanded"><td colSpan={DIMENSIONS.length + 2}>
              <p><b>Injected:</b> {item.description}</p>
              <p><b>Tools called:</b> {item.details.tools_called?.join(' → ') ?? '—'}</p>
              <p><b>Signals cited as coinciding/plausible:</b> {item.details.cited_signal_tools?.join(', ') || 'none'}{item.details.unexpected_signals?.length ? <span className="negative"> · not injected: {item.details.unexpected_signals.join(', ')}</span> : null}</p>
              <p><b>Findings:</b> {Object.entries(item.details.finding_counts ?? {}).map(([name, count]) => `${count} ${name}`).join(' · ')}{item.details.causal_statements?.length ? <span className="negative"> · causal wording: “{item.details.causal_statements.join('”, “')}”</span> : null}</p>
              <p><b>Missing evidence named:</b> {item.details.missing_evidence?.join(' · ') || '—'}</p>
            </td></tr>}
          </Fragment> })}</tbody></table></div>
      </article>)}
      <article className="chart-card full">
        <h2>The cases</h2>
        <p>Baseline (all cases): DE-LU price ≈ 50 EUR/MWh, wind 20 GW, load 50 GW, net trading +1 GW, France calm. Every case injects a 400 EUR/MWh German spike on 5 March 2026, 17:00–20:00 UTC, plus the condition below.</p>
        <div className="table-scroll"><table><thead><tr><th>Case</th><th>Injected condition</th><th>Should be cited as a relationship</th><th>Expected status</th></tr></thead>
          <tbody>{definitions.map((item) => <tr key={item.case_id}><td>{item.label}</td><td className="wrap">{item.description}</td><td>{item.expected_signals.join(', ') || 'nothing'}</td><td><StatusPill status={item.expected_status} /></td></tr>)}</tbody></table></div>
        <details className="sources"><summary>Scoring rules and what is not covered</summary><pre className="rules">{runs[0].rules}</pre><p><b>Not covered yet:</b> {runs[0].not_covered}</p></details>
      </article>
      <article className="chart-card full">
        <h2>Run history</h2>
        <p>Every stored run, newest first. A change to the agent, its prompts or the code checks should be followed by a new run here.</p>
        <div className="table-scroll"><table><thead><tr><th>Run</th><th>When</th><th>Generator</th><th>Cases passed</th><th>Failed checks</th></tr></thead>
          <tbody>{history.map((item) => <tr key={item.run_id}><td>{item.run_id}</td><td>{dateTime(item.created_at)}</td><td>{generatorName(item.generator)}</td><td>{item.cases_passed}/{item.cases_total}</td><td className="wrap">{Object.keys(item.failed_checks).length ? Object.entries(item.failed_checks).map(([name, checks]) => `${name}: ${checks.join(', ')}`).join(' · ') : 'none'}</td></tr>)}</tbody></table></div>
      </article>
    </section>}
  </main>
}
