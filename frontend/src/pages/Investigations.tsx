// AI Investigations: case list, case detail with evidence, activity trace and export (handbook 2.2, 14).
import { useEffect, useState } from 'react'
import { API, getJson, postJson, fmt, dateTime, titleCase } from '../lib/api'
import { navigate } from '../lib/nav'
import { Empty, ErrorState, Loading, PageHeader, StatusPill } from '../components/States'
import type { Anomaly } from './MarketAnalytics'

type Finding = { statement: string; evidence_ids: string[] }
type ToolCall = { step: number; tool_name: string; parameters: Record<string, unknown>; status: string; evidence_id: string; chosen_by?: string }
type Evidence = { evidence_id: string; tool_name: string; parameters: Record<string, unknown>; models: string[]; status: string; result: Record<string, unknown>; source_ids: string[] }
type Report = {
  report_id: string; anomaly_id: string; generator: string; status: string; anomaly: Anomaly & { threshold?: number }
  confidence_factors: Record<string, { status: string; completeness_ratio?: number | null; note?: string }>
  observations: Finding[]; coinciding_relationships: Finding[]; plausible_contributing_factors: Finding[]; proven_causes: Finding[]
  missing_evidence: string[]; limitations: string[]; tool_trace: ToolCall[]; evidence: Evidence[]
}
type ReportSummary = { report_id: string; generator: string; status: string; created_at: string }
type Telemetry = { measured: boolean; calls?: number; failed_calls?: number; wall_s?: number | null; model_s?: number; prompt_tokens?: number; completion_tokens?: number; median_call_s?: number }
type Trace = { report_id: string; generator: string; model: string | null; telemetry?: Telemetry; node_trace: string[]; tool_trace: ToolCall[]; draft_findings: { category: string; statement: string; evidence_ids: string[] }[]; missing_evidence: string[] }
type Zone = { bidding_zone: string }

const PAGE = 25

export default function Investigations({ anomalyId }: { anomalyId: string }) {
  const [zones, setZones] = useState<Zone[]>([])
  const [zone, setZone] = useState('')
  const [direction, setDirection] = useState('')
  const [offset, setOffset] = useState(0)
  const [page, setPage] = useState<{ total: number; items: Anomaly[] } | null>(null)
  const [listError, setListError] = useState('')

  useEffect(() => { getJson<Zone[]>('/api/anomalies/zones').then(setZones).catch(() => setZones([])) }, [])
  useEffect(() => {
    let active = true
    getJson<{ total: number; items: Anomaly[] }>('/api/anomalies', { bidding_zone: zone || undefined, direction: direction || undefined, limit: PAGE, offset })
      .then((value) => { if (active) { setPage(value); setListError('') } })
      .catch((reason: Error) => { if (active) setListError(reason.message) })
    return () => { active = false }
  }, [zone, direction, offset])

  return <main className="dashboard">
    <PageHeader eyebrow="AI INVESTIGATION CENTRE" title="Investigate price anomalies">Each case records the detected event, every tool called, the evidence behind each statement, what could not be checked, and how the report was produced. No report claims a proven cause.</PageHeader>
    <div className="split">
      <section className="case-list" aria-label="Detected anomalies">
        <div className="case-filters">
          <select aria-label="Market" value={zone} onChange={(event) => { setZone(event.target.value); setOffset(0) }}><option value="">All markets</option>{zones.map((item) => <option key={item.bidding_zone}>{item.bidding_zone}</option>)}</select>
          <select aria-label="Direction" value={direction} onChange={(event) => { setDirection(event.target.value); setOffset(0) }}><option value="">Spikes and dips</option><option value="spike">Spikes</option><option value="dip">Dips</option></select>
        </div>
        {listError ? <ErrorState message={listError} /> : !page ? <Loading label="Loading cases…" /> : page.items.length === 0
          ? <Empty title="No anomalies saved yet.">Open Market Analytics and run detection for a market and year.</Empty>
          : <>
            <ul>{page.items.map((item) => <li key={item.anomaly_id}><button className={item.anomaly_id === anomalyId ? 'active' : ''} onClick={() => navigate('investigations', item.anomaly_id)}>
              <span className="case-row-top"><b>{item.geography}</b><StatusPill status={item.direction} /><span className="score">score {fmt(item.max_abs_score, 1)}</span></span>
              <small>{item.start_utc.slice(0, 16).replace('T', ' ')} UTC · {item.hour_count} h · peak {fmt(item.observed_value, 0)} EUR/MWh</small>
            </button></li>)}</ul>
            <div className="pager"><button className="button small" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>← Newer</button><span>{offset + 1}–{Math.min(offset + PAGE, page.total)} of {page.total}</span><button className="button small" disabled={offset + PAGE >= page.total} onClick={() => setOffset(offset + PAGE)}>Older →</button></div>
          </>}
      </section>
      <section className="case-detail">{anomalyId ? <CaseDetail key={anomalyId} anomalyId={anomalyId} /> : <Empty title="Select a case.">Pick an anomaly on the left to see its investigation, or start one.</Empty>}</section>
    </div>
  </main>
}

function CaseDetail({ anomalyId }: { anomalyId: string }) {
  const [event, setEvent] = useState<Anomaly | null>(null)
  const [reports, setReports] = useState<ReportSummary[]>([])
  const [selected, setSelected] = useState('')
  const [report, setReport] = useState<Report | null>(null)
  const [trace, setTrace] = useState<Trace | null>(null)
  const [error, setError] = useState('')
  const [running, setRunning] = useState<'' | 'rule-based' | 'agent'>('')
  const [runError, setRunError] = useState('')
  const [model, setModel] = useState('qwen3.5:4b')
  const [evidenceId, setEvidenceId] = useState('')
  const [tab, setTab] = useState<'report' | 'trace' | 'evidence'>('report')

  useEffect(() => {
    Promise.all([getJson<Anomaly>(`/api/anomalies/${anomalyId}`), getJson<ReportSummary[]>(`/api/anomalies/${anomalyId}/investigations`)])
      .then(([value, list]) => { setEvent(value); setReports(list); setSelected(list[0]?.report_id ?? '') })
      .catch((reason: Error) => setError(reason.message))
  }, [anomalyId])
  useEffect(() => {
    if (!selected) return
    let active = true
    Promise.all([getJson<Report>(`/api/investigations/${selected}`), getJson<Trace>(`/api/investigations/${selected}/trace`)])
      .then(([value, steps]) => { if (active) { setReport(value); setTrace(steps) } })
      .catch((reason: Error) => { if (active) setError(reason.message) })
    return () => { active = false }
  }, [selected])

  async function run(generator: 'rule-based' | 'agent') {
    setRunning(generator); setRunError('')
    try {
      const created = await postJson<Report>('/api/investigations', { anomaly_id: anomalyId, generator, model })
      const list = await getJson<ReportSummary[]>(`/api/anomalies/${anomalyId}/investigations`)
      setReports(list); setSelected(created.report_id); setTab('report')
    } catch (reason) { setRunError((reason as Error).message) } finally { setRunning('') }
  }

  if (error) return <ErrorState message={error} />
  if (!event) return <Loading label="Loading case…" />
  const evidenceById = new Map((report?.evidence ?? []).map((item) => [item.evidence_id, item]))
  const openEvidence = (id: string) => { setEvidenceId(id); setTab('evidence') }

  return <div className="case">
    <div className="case-head">
      <div><div className="eyebrow">CASE {event.anomaly_id}</div>
        <h2>{event.geography} price {event.direction} · {event.start_utc.slice(0, 16).replace('T', ' ')} → {event.end_utc.slice(11, 16)} UTC</h2>
        <p>Peak {fmt(event.observed_value, 1)} EUR/MWh against a usual {fmt(event.baseline_median, 1)} EUR/MWh for the same local hour · robust score {fmt(event.max_abs_score, 1)} (threshold 5) · {event.hour_count} hour(s).</p></div>
    </div>
    <div className="run-bar">
      <button className="button" disabled={!!running} onClick={() => run('rule-based')}>{running === 'rule-based' ? 'Running…' : 'Run rule-based investigation'}</button>
      <span className="run-agent"><button className="button primary" disabled={!!running} onClick={() => run('agent')}>{running === 'agent' ? 'Agent investigating…' : 'Run AI agent'}</button>
        <input aria-label="Local model" value={model} onChange={(item) => setModel(item.target.value)} /></span>
      <button className="button" onClick={() => navigate('replay', anomalyId)}>Replay for a household →</button>
      <small>Rule-based: instant, fixed tool sequence. AI agent: a local Ollama model chooses the tools and drafts findings; code checks every statement. Usually 30–60 s.</small>
    </div>
    {running === 'agent' && <Loading label="The local model is choosing tools and drafting findings. Each step is checked by code…" />}
    {runError && <ErrorState message={runError} hint={runError.includes('Ollama') ? 'Start Ollama (Start menu) and make sure the model is pulled.' : ''} />}
    {reports.length === 0 ? <Empty title="No investigation yet.">Run the rule-based investigation or the AI agent above.</Empty> : <>
      <div className="report-picker">
        {reports.map((item) => <button key={item.report_id} className={item.report_id === selected ? 'active' : ''} onClick={() => setSelected(item.report_id)}>
          <b>{item.generator.startsWith('langgraph') ? 'AI agent' : 'Rule-based'}</b><StatusPill status={item.status} /><small>{dateTime(item.created_at)}</small></button>)}
      </div>
      {!report || report.report_id !== selected ? <Loading label="Loading report…" /> : <>
        <div className="tab-bar">
          <div className="segmented">{(['report', 'trace', 'evidence'] as const).map((value) => <button key={value} className={tab === value ? 'active' : ''} onClick={() => setTab(value)}>{value === 'report' ? 'Findings' : value === 'trace' ? 'Activity trace' : `Evidence (${report.evidence.length})`}</button>)}</div>
          <span className="exports">Export: <a href={`${API}/api/investigations/${report.report_id}/export?format=markdown`}>Markdown</a> · <a href={`${API}/api/investigations/${report.report_id}/export?format=json`}>JSON</a></span>
        </div>
        {tab === 'report' && <ReportView report={report} onEvidence={openEvidence} />}
        {tab === 'trace' && trace?.report_id === selected && <TraceView trace={trace} onEvidence={openEvidence} />}
        {tab === 'evidence' && <EvidenceView evidence={report.evidence} selected={evidenceId || report.evidence[0]?.evidence_id} onSelect={setEvidenceId} lookup={evidenceById} />}
      </>}
    </>}
  </div>
}

function Refs({ ids, onEvidence }: { ids: string[]; onEvidence: (id: string) => void }) {
  return <span className="refs">{ids.map((id) => <button key={id} className="ref" onClick={() => onEvidence(id)} title="Open the tool call behind this statement">{id}</button>)}</span>
}

function FindingList({ title, items, empty, tone, onEvidence }: { title: string; items: Finding[]; empty: string; tone: string; onEvidence: (id: string) => void }) {
  return <div className={`finding-group tone-${tone}`}><h3>{title} <small>{items.length}</small></h3>
    {items.length ? <ul>{items.map((item, index) => <li key={index}>{item.statement} <Refs ids={item.evidence_ids} onEvidence={onEvidence} /></li>)}</ul> : <p className="muted">{empty}</p>}</div>
}

function ReportView({ report, onEvidence }: { report: Report; onEvidence: (id: string) => void }) {
  return <div className="report">
    <div className="report-status"><StatusPill status={report.status} /><span>{report.status === 'complete' ? 'Core price, generation and load data were complete for the window.' : report.status === 'limited' ? 'Some evidence was missing or nothing usable was kept, so findings are limited.' : 'Price data for the window is missing, so the anomaly cannot be verified.'}</span><small>{report.generator} · {report.report_id}</small></div>
    <FindingList title="Observations" items={report.observations} empty="None." tone="obs" onEvidence={onEvidence} />
    <FindingList title="Relationships that coincide with the event" items={report.coinciding_relationships} empty="No series changed enough (under the written rule thresholds) to count as coinciding." tone="coin" onEvidence={onEvidence} />
    <FindingList title="Plausible contributing factors" items={report.plausible_contributing_factors} empty="None identified." tone="plaus" onEvidence={onEvidence} />
    <div className="finding-group tone-proven"><h3>Proven causes <small>0</small></h3><p className="muted">None. Public market data shows what coincided; it cannot establish the price-formation mechanism.</p></div>
    <div className="report-grid">
      <div><h3>Missing evidence</h3>{report.missing_evidence.length ? <ul className="plain">{report.missing_evidence.map((item) => <li key={item}>{item}</li>)}</ul> : <p className="muted">Nothing missing.</p>}</div>
      <div><h3>Confidence factors</h3><p className="muted">Rule-based coverage indicators, not a probability.</p>
        <table className="compact"><tbody>{Object.entries(report.confidence_factors).map(([name, value]) => <tr key={name}><td>{titleCase(name)}</td><td><StatusPill status={value.status} /></td><td>{value.completeness_ratio != null ? `${fmt(value.completeness_ratio * 100, 0)}%` : value.note ?? ''}</td></tr>)}</tbody></table></div>
    </div>
    <details className="sources"><summary>Limitations ({report.limitations.length})</summary><ul className="plain">{report.limitations.map((item) => <li key={item}>{item}</li>)}</ul></details>
  </div>
}

function TraceView({ trace, onEvidence }: { trace: Trace; onEvidence: (id: string) => void }) {
  const t = trace.telemetry
  return <div className="trace">
    {t?.measured && <div className="telemetry"><b>Model cost of this investigation</b>
      <span>{t.calls} model calls{t.failed_calls ? ` (${t.failed_calls} invalid)` : ''}</span>
      <span>{fmt(t.wall_s, 1)} s total · median {fmt(t.median_call_s, 1)} s per call</span>
      <span>{fmt(t.prompt_tokens, 0)} prompt + {fmt(t.completion_tokens, 0)} output tokens</span>
      <span className="muted">Local model: no API cost; time is on this PC's GPU/CPU.</span></div>}
    <h3>Path through the workflow</h3>
    <ol className="timeline">{trace.node_trace.map((step, index) => { const [node, detail] = step.split(':'); return <li key={index} className={step.includes('by model') ? 'by-model' : step.includes('by code') ? 'by-code' : ''}><b>{titleCase(node)}</b>{detail && <span>{detail}</span>}</li> })}</ol>
    <h3>Tool calls</h3>
    <div className="table-scroll"><table><thead><tr><th>#</th><th>Tool</th><th>Chosen by</th><th>Status</th><th>Window (UTC)</th><th>Evidence</th></tr></thead>
      <tbody>{trace.tool_trace.map((call) => <tr key={call.step}><td>{call.step}</td><td>{call.tool_name}</td><td>{call.chosen_by ?? '—'}</td><td><StatusPill status={call.status} /></td>
        <td>{String(call.parameters.start_utc ?? '').slice(0, 16).replace('T', ' ')} → {String(call.parameters.end_utc ?? '').slice(11, 16)}</td><td><Refs ids={[call.evidence_id]} onEvidence={onEvidence} /></td></tr>)}</tbody></table></div>
    {trace.model && <details className="sources" open={trace.draft_findings.length > 0}><summary>Model's original draft before code checks ({trace.draft_findings.length} statements)</summary>
      {trace.draft_findings.length ? <ul className="plain">{trace.draft_findings.map((item, index) => <li key={index}><StatusPill status={item.category} /> {item.statement} <small className="muted">[{item.evidence_ids.join(', ') || 'no evidence cited'}]</small></li>)}</ul> : <p className="muted">The model returned no draft (or its reply was invalid), so the report has no model findings.</p>}</details>}
  </div>
}

function EvidenceView({ evidence, selected, onSelect, lookup }: { evidence: Evidence[]; selected?: string; onSelect: (id: string) => void; lookup: Map<string, Evidence> }) {
  const item = selected ? lookup.get(selected) : undefined
  return <div className="evidence">
    <ul className="evidence-list">{evidence.map((entry) => <li key={entry.evidence_id}><button className={entry.evidence_id === selected ? 'active' : ''} onClick={() => onSelect(entry.evidence_id)}><b>{entry.tool_name}</b><StatusPill status={entry.status} /><small>{entry.evidence_id}</small></button></li>)}</ul>
    {item ? <div className="evidence-detail">
      <h3>{item.tool_name} <StatusPill status={item.status} /></h3>
      <p className="muted">Stored record of this tool call: the exact parameters, the Gold models it read, its result summary and the raw source files behind it.</p>
      <dl><dt>Parameters</dt><dd><code>{JSON.stringify(item.parameters)}</code></dd><dt>Models read</dt><dd>{item.models.join(', ') || '—'}</dd><dt>Raw source files</dt><dd>{item.source_ids.length ? `${item.source_ids.length}: ${item.source_ids.slice(0, 4).map((id) => id.slice(0, 12)).join(', ')}${item.source_ids.length > 4 ? '…' : ''}` : '—'}</dd></dl>
      <pre className="json">{JSON.stringify(item.result, null, 2)}</pre>
    </div> : <Empty title="Select an evidence record." />}
  </div>
}
