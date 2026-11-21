// Grid-to-Home event replay: a detected market event replayed against a stored household simulation.
import { useEffect, useState } from 'react'
import { API, getJson, postJson, fmt, euro, dateTime } from '../lib/api'
import { baseOption, palette, type EChartsOption } from '../lib/charts'
import { Chart } from '../components/Chart'
import { navigate } from '../lib/nav'
import { Empty, ErrorState, Loading, PageHeader, StatusPill } from '../components/States'
import type { Anomaly } from './MarketAnalytics'

type TariffCost = { tariff: string; cost_actual_eur: number; cost_usual_eur: number; event_premium_eur: number; event_hours_cost_actual_eur: number; event_hours_cost_usual_eur: number }
type Option = { option_id: string; label: string; pv_peak_kw: number; battery_kwh: number | null; load_kwh: number; pv_kwh: number; grid_import_kwh: number; grid_export_kwh: number; battery_discharge_kwh: number; event_hours_import_kwh: number; event_hours_battery_discharge_kwh: number; self_sufficiency_pct: number | null; costs: TariffCost[] }
type Flows = Record<string, number[]>
type Replay = {
  replay_id: string; anomaly: Anomaly; simulation_id: string; scenario_id: string; location: { label?: string; latitude: number; longitude: number }
  window: { start_utc: string; end_utc: string; hours: number; timezone: string }
  market: { report_id: string; generator: string; status: string; observations: string[]; coinciding: string[]; plausible: string[]; missing_evidence: string[] }
  options: Option[]; assumptions: string[]; labels: string[]
  hourly: { hours_utc: string[]; in_event: boolean[]; wholesale_actual_eur_per_mwh: number[]; wholesale_usual_eur_per_mwh: number[]; dynamic_import_actual_eur_per_kwh: number[]; dynamic_import_usual_eur_per_kwh: number[]; flows: Record<string, Flows> }
}
type Simulation = { simulation_id: string; scenario_id: string; created_at: string }
type ReplaySummary = { replay_id: string; anomaly_id: string; simulation_id: string; created_at: string }

export default function ReplayPage({ anomalyId }: { anomalyId: string }) {
  const [events, setEvents] = useState<Anomaly[]>([])
  const [simulations, setSimulations] = useState<Simulation[]>([])
  const [history, setHistory] = useState<ReplaySummary[]>([])
  const [simulationId, setSimulationId] = useState('')
  const [result, setResult] = useState<Replay | null>(null)
  const [error, setError] = useState('')
  const [loadError, setLoadError] = useState('')
  const [running, setRunning] = useState(false)
  const [option, setOption] = useState('')

  useEffect(() => {
    Promise.all([getJson<{ items: Anomaly[] }>('/api/anomalies', { limit: 200 }), getJson<{ items: Simulation[] }>('/api/simulations', { limit: 100 }), getJson<ReplaySummary[]>('/api/replays')])
      .then(([anomalies, sims, past]) => { setEvents(anomalies.items); setSimulations(sims.items); setSimulationId(sims.items[0]?.simulation_id ?? ''); setHistory(past) })
      .catch((reason: Error) => setLoadError(reason.message))
  }, [])

  async function run() {
    setRunning(true); setError('')
    try {
      const value = await postJson<Replay>('/api/replays', { anomaly_id: anomalyId, simulation_id: simulationId })
      setResult(value); setOption(value.options.at(-1)?.option_id ?? 'no-system')
      setHistory(await getJson<ReplaySummary[]>('/api/replays'))
    } catch (reason) { setError((reason as Error).message) } finally { setRunning(false) }
  }
  async function open(replayId: string) {
    try { const value = await getJson<Replay>(`/api/replays/${replayId}`); setResult(value); setOption(value.options.at(-1)?.option_id ?? 'no-system'); navigate('replay', value.anomaly.anomaly_id) }
    catch (reason) { setError((reason as Error).message) }
  }

  const event = events.find((item) => item.anomaly_id === anomalyId)
  return <main className="dashboard">
    <PageHeader eyebrow="GRID-TO-HOME EVENT REPLAY" title="What would this market event have meant for a household?">Pick a detected price event and a household you simulated in the planner. The household's day is replayed hour by hour with the actual prices and weather of that day, then compared with the same hours at usual prices.</PageHeader>
    {loadError && <ErrorState message={loadError} />}
    <section className="form-card">
      <div className="form-grid">
        <label>MARKET EVENT<select value={anomalyId} onChange={(item) => navigate('replay', item.target.value)}><option value="">Choose a detected event…</option>{events.map((item) => <option key={item.anomaly_id} value={item.anomaly_id}>{item.geography} {item.direction} · {item.start_utc.slice(0, 16).replace('T', ' ')} UTC · peak {fmt(item.observed_value, 0)} EUR/MWh</option>)}</select></label>
        <label>HOUSEHOLD (STORED SIMULATION)<select value={simulationId} onChange={(item) => setSimulationId(item.target.value)}>{simulations.length === 0 && <option value="">No simulations yet</option>}{simulations.map((item) => <option key={item.simulation_id} value={item.simulation_id}>{item.scenario_id} · {dateTime(item.created_at)}</option>)}</select></label>
      </div>
      {simulations.length === 0 && <p className="table-note">Run a simulation on the <a href="#/planner">Renewable Planner</a> first; the replay reuses its household, location and systems.</p>}
      {event && <p className="table-note">Event: {event.geography} price {event.direction} {event.start_utc.slice(0, 16).replace('T', ' ')}–{event.end_utc.slice(11, 16)} UTC, peak {fmt(event.observed_value, 1)} EUR/MWh vs usual {fmt(event.baseline_median, 1)} (score {fmt(event.max_abs_score, 1)}). <a href={`#/investigations/${event.anomaly_id}`}>Open its investigation →</a></p>}
      <div className="run-bar"><button className="button primary" disabled={!anomalyId || !simulationId || running} onClick={run}>{running ? 'Replaying the event day…' : 'Replay event'}</button><small>Weather for the event days is fetched once from Open-Meteo and cached. Usual prices are the median of the same hour over the prior 28 days.</small></div>
    </section>
    {error && <ErrorState message={error} hint={error.includes('missing') ? 'The replay does not estimate gaps; choose an event with complete prices.' : ''} />}
    {running && <Loading label="Replaying the household through the event day…" />}
    {!result && !running && !error && <Empty title="No replay shown.">Choose an event and a household, then replay. Earlier replays are listed below.</Empty>}
    {result && <ReplayView result={result} option={option} setOption={setOption} />}
    {history.length > 0 && <article className="chart-card full spaced"><h2>Earlier replays</h2>
      <div className="table-scroll"><table className="compact"><thead><tr><th>When</th><th>Event</th><th>Household simulation</th><th /></tr></thead>
        <tbody>{history.map((item) => <tr key={item.replay_id} className="clickable" onClick={() => open(item.replay_id)}><td>{dateTime(item.created_at)}</td><td>{item.anomaly_id}</td><td>{item.simulation_id}</td><td className="link">Open →</td></tr>)}</tbody></table></div></article>}
  </main>
}

function ReplayView({ result, option, setOption }: { result: Replay; option: string; setOption: (value: string) => void }) {
  const tariffs = result.options[0].costs.map((cost) => cost.tariff)
  const labels = result.hourly.hours_utc.map((hour) => new Date(hour).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', timeZone: result.window.timezone }))
  const eventIndexes = result.hourly.in_event.map((flag, index) => flag ? index : -1).filter((index) => index >= 0)
  const eventArea = eventIndexes.length ? { silent: true, itemStyle: { color: 'rgba(255,143,128,.12)' }, label: { formatter: 'Event', color: palette.red }, data: [[{ xAxis: labels[eventIndexes[0]] }, { xAxis: labels[eventIndexes.at(-1)!] }]] } : undefined
  const priceOption: EChartsOption = {
    ...baseOption('EUR/MWh', labels),
    tooltip: { trigger: 'axis', confine: true, valueFormatter: (value) => `${fmt(value as number, 1)} EUR/MWh` },
    series: [
      { name: 'Actual day-ahead price', type: 'line', data: result.hourly.wholesale_actual_eur_per_mwh, lineStyle: { color: palette.red, width: 2 }, itemStyle: { color: palette.red }, markArea: eventArea as never },
      { name: 'Usual price (28-day median, same hour)', type: 'line', data: result.hourly.wholesale_usual_eur_per_mwh, lineStyle: { color: palette.amber, type: 'dashed' }, itemStyle: { color: palette.amber } },
    ],
  }
  const flows = result.hourly.flows[option] ?? result.hourly.flows['no-system']
  const selected = result.options.find((item) => item.option_id === option) ?? result.options[0]
  const flowOption: EChartsOption = {
    ...baseOption('kWh in the hour', labels),
    tooltip: { trigger: 'axis', confine: true, valueFormatter: (value) => `${fmt(value as number, 2)} kWh` },
    series: [
      { name: 'Household use', type: 'line', data: flows.load_kwh, showSymbol: false, lineStyle: { color: '#e9f0e8', width: 2 }, itemStyle: { color: '#e9f0e8' }, markArea: eventArea as never },
      { name: 'Solar output', type: 'line', data: flows.pv_kwh, showSymbol: false, areaStyle: { opacity: .15 }, lineStyle: { color: palette.amber }, itemStyle: { color: palette.amber } },
      { name: 'Bought from grid', type: 'bar', data: flows.grid_import_kwh, itemStyle: { color: '#5b7166' } },
      { name: 'From battery', type: 'bar', data: flows.battery_discharge_kwh, itemStyle: { color: palette.teal } },
    ],
  }
  const none = result.options[0]
  const dynamicIndex = tariffs.findIndex((name) => name.toLowerCase().includes('dynamic'))
  return <section className="charts">
    <article className="chart-card full">
      <div className="chart-title"><div><h2>Replay · {result.anomaly.geography} {result.anomaly.direction} · {result.window.start_utc.slice(0, 10)} ({result.window.timezone})</h2>
        <p>{result.scenario_id} household at {result.location.label || `${result.location.latitude}, ${result.location.longitude}`} · {result.window.hours} hours replayed · {result.labels.join(' · ')}</p></div>
        <span className="exports">Event-to-household report: <a href={`${API}/api/replays/${result.replay_id}/export?format=markdown`}>Markdown</a> · <a href={`${API}/api/replays/${result.replay_id}/export?format=json`}>JSON</a></span></div>
      <div className="replay-grid">
        <div><h3>What the market did (measured) <StatusPill status={result.market.status} /></h3>
          <ul className="plain">{[...result.market.observations, ...result.market.coinciding].map((item) => <li key={item}>{item}</li>)}</ul>
          {result.market.plausible.length > 0 && <><h4>Plausible contributing factors (not proven)</h4><ul className="plain">{result.market.plausible.map((item) => <li key={item}>{item}</li>)}</ul></>}
          {result.market.missing_evidence.length > 0 && <><h4>Could not be checked</h4><ul className="plain muted">{result.market.missing_evidence.map((item) => <li key={item}>{item}</li>)}</ul></>}
          <p className="muted">From investigation {result.market.report_id} ({result.market.generator}).</p></div>
        <div><h3>What it meant for the household</h3>
          {dynamicIndex >= 0 && <p className="headline">Without solar or a battery, on a dynamic tariff, the event added <b>{euro(none.costs[dynamicIndex].event_premium_eur, 2)}</b> to this day's electricity cost ({euro(none.costs[dynamicIndex].cost_usual_eur, 2)} at usual prices → {euro(none.costs[dynamicIndex].cost_actual_eur, 2)}).</p>}
          <p className="headline">On the household's own tariff the change was <b>{euro(none.costs[0].event_premium_eur, 2)}</b>{none.costs[0].event_premium_eur === 0 ? ': a fixed price does not pass wholesale spikes through.' : '.'}</p>
        </div>
      </div>
    </article>
    <article className="chart-card full">
      <h2>Cost of the replayed day by configuration</h2>
      <p>Import cost minus export revenue over the replayed day (standing charges excluded). "Usual" uses the same energy flows at usual wholesale prices; the premium is the event's effect. Event hours = the detected anomaly window only.</p>
      <div className="table-scroll"><table><thead><tr><th>Configuration</th><th>Bought from grid<br /><small>kWh, day</small></th><th>Battery in event hours<br /><small>kWh</small></th>{tariffs.map((name) => <th key={name}>{name}<br /><small>actual · usual · premium</small></th>)}{tariffs.map((name) => <th key={`${name}-event`}>Event hours only<br /><small>{name.split('(')[0].trim()}: actual vs usual</small></th>)}</tr></thead>
        <tbody>{result.options.map((item) => <tr key={item.option_id} className={`clickable ${item.option_id === option ? 'highlight' : ''}`} onClick={() => setOption(item.option_id)}>
          <td>{item.label}</td><td>{fmt(item.grid_import_kwh, 2)}</td><td>{item.battery_kwh ? fmt(item.event_hours_battery_discharge_kwh, 2) : '—'}</td>
          {item.costs.map((cost) => <td key={cost.tariff} className="nowrap">{euro(cost.cost_actual_eur, 2)} · {euro(cost.cost_usual_eur, 2)} · <span className={cost.event_premium_eur > 0.005 ? 'negative' : 'positive'}>{cost.event_premium_eur >= 0 ? '+' : ''}{euro(cost.event_premium_eur, 2)}</span></td>)}
          {item.costs.map((cost) => <td key={`${cost.tariff}-event`} className="nowrap">{euro(cost.event_hours_cost_actual_eur, 2)} vs {euro(cost.event_hours_cost_usual_eur, 2)}</td>)}</tr>)}</tbody></table></div>
    </article>
    <article className="chart-card">
      <h2>Wholesale price through the day</h2>
      <p>Actual day-ahead price against the usual price for each hour. The shaded band is the detected event. Times in {result.window.timezone}.</p>
      <Chart option={priceOption} height={300} />
    </article>
    <article className="chart-card">
      <h2>Household energy · {selected.label}</h2>
      <p>Hourly use, solar output, energy bought from the grid and energy supplied by the battery. Click a table row to switch configuration.</p>
      <Chart option={flowOption} height={300} />
    </article>
    <article className="chart-card full">
      <h2>Assumptions and limitations</h2>
      <ul className="plain">{result.assumptions.map((item) => <li key={item}>{item}</li>)}</ul>
    </article>
  </section>
}
