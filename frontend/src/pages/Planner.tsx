// Renewable Planner: household inputs, configuration comparison, seasonal balance, cash flow and battery dispatch.
import { useEffect, useState } from 'react'
import { getJson, postJson, fmt, euro } from '../lib/api'
import { baseOption, palette, type EChartsOption } from '../lib/charts'
import { Chart } from '../components/Chart'
import { Empty, ErrorState, Loading, PageHeader } from '../components/States'

import { LOCATIONS, OBJECTIVES, pickBest, type Hour, type Month, type Objective, type ScenarioResult, type SystemConfig, type SystemResult } from '../lib/household'

export default function Planner() {
  const [place, setPlace] = useState('berlin')
  const [consumption, setConsumption] = useState(3500)
  const [pvSource, setPvSource] = useState<'pvgis' | 'weather_model'>('pvgis')
  const [year, setYear] = useState(2023)
  const [tariffKind, setTariffKind] = useState<'fixed' | 'dynamic'>('fixed')
  const [importPrice, setImportPrice] = useState(0.39)
  const [priceSource, setPriceSource] = useState('illustrative default')
  const [exportPrice, setExportPrice] = useState(0.08)
  const [discount, setDiscount] = useState(4)
  const [horizon, setHorizon] = useState(20)
  const [systems, setSystems] = useState<SystemConfig[]>([])
  const [objective, setObjective] = useState<Objective>('npv')
  const [budget, setBudget] = useState(12000)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState('')
  const [simulationId, setSimulationId] = useState('')
  const [result, setResult] = useState<ScenarioResult | null>(null)
  const [focus, setFocus] = useState('')

  useEffect(() => { getJson<SystemConfig[]>('/api/simulations/default-systems').then(setSystems).catch((reason: Error) => setError(reason.message)) }, [])
  const location = LOCATIONS[place]
  // Prefill the fixed import price from Eurostat's latest household price for the country (all taxes included).
  useEffect(() => {
    let active = true
    getJson<{ retail_eur_per_kwh: number | null; retail_period: string | null }>('/api/household-benchmark', { geo: location.geo })
      .then((value) => {
        if (!active) return
        if (value.retail_eur_per_kwh != null) { setImportPrice(Number(value.retail_eur_per_kwh.toFixed(3))); setPriceSource(`Eurostat ${value.retail_period ?? ''}, band 2,500–4,999 kWh, all taxes`) }
        else { setImportPrice(location.retail); setPriceSource('illustrative default (Eurostat has no value)') }
      })
      .catch(() => { if (active) { setImportPrice(location.retail); setPriceSource('illustrative default (Eurostat unavailable)') } })
    return () => { active = false }
  }, [location.geo, location.retail])
  const dynamicYearOk = tariffKind === 'fixed' || pvSource === 'weather_model'

  function choosePlace(key: string) {
    setPlace(key); setConsumption(LOCATIONS[key].household)
  }
  function chooseTariff(kind: 'fixed' | 'dynamic') {
    setTariffKind(kind)
    if (kind === 'dynamic') { setPvSource('weather_model'); setYear(2025) }
  }

  async function run() {
    setRunning(true); setError('')
    const tariff = tariffKind === 'fixed'
      ? { kind: 'fixed', import_eur_per_kwh: importPrice, export_eur_per_kwh: exportPrice }
      : { kind: 'dynamic', bidding_zone: location.zone, export_eur_per_kwh: exportPrice }
    try {
      const response = await postJson<{ simulation_id: string; result: ScenarioResult }>('/api/simulations', {
        scenario_id: `${place}-${consumption}`, location: { latitude: location.latitude, longitude: location.longitude, timezone: location.timezone, label: location.label },
        annual_consumption_kwh: consumption, simulation_year: year, pv_source: pvSource, tariff,
        finance: { horizon_years: horizon, discount_rate_pct: discount }, systems })
      setSimulationId(response.simulation_id); setResult(response.result); setFocus(response.result.systems.at(-1)?.system_id ?? '')
    } catch (reason) { setError((reason as Error).message) } finally { setRunning(false) }
  }

  const best = result ? pickBest(result.systems, objective, budget) : null
  return <main className="dashboard">
    <PageHeader guide="planner" eyebrow="RENEWABLE INVESTMENT PLANNER" title="Would solar and a battery pay off?">Hour-by-hour simulation of a household with rooftop solar, optionally a battery, over a full year, then the bill, payback and net present value under your tariff and finance assumptions.</PageHeader>
    <section className="form-card">
      <div className="form-grid">
        <label>LOCATION<select value={place} onChange={(event) => choosePlace(event.target.value)}>{Object.entries(LOCATIONS).map(([key, value]) => <option key={key} value={key}>{value.label}</option>)}</select></label>
        <label>ANNUAL USE (kWh)<input type="number" min={500} max={50000} step={100} value={consumption} onChange={(event) => setConsumption(Number(event.target.value))} /></label>
        <label>SOLAR DATA<select value={pvSource} onChange={(event) => { const value = event.target.value as 'pvgis' | 'weather_model'; setPvSource(value); if (value === 'pvgis' && year > 2023) setYear(2023) }}><option value="pvgis">PVGIS (EU JRC, 2005–2023)</option><option value="weather_model">Weather model (Open-Meteo)</option></select></label>
        <label>WEATHER YEAR<select value={year} onChange={(event) => setYear(Number(event.target.value))}>{(pvSource === 'pvgis' ? [2020, 2021, 2022, 2023] : [2023, 2024, 2025]).map((value) => <option key={value}>{value}</option>)}</select></label>
        <label>TARIFF<select value={tariffKind} onChange={(event) => chooseTariff(event.target.value as 'fixed' | 'dynamic')}><option value="fixed">Fixed price per kWh</option><option value="dynamic">Dynamic (follows wholesale)</option></select></label>
        {tariffKind === 'fixed' ? <label>IMPORT PRICE (€/kWh)<input type="number" min={0.01} max={2} step={0.01} value={importPrice} onChange={(event) => { setImportPrice(Number(event.target.value)); setPriceSource('your value') }} /><small className="field-note">{priceSource}</small></label>
          : <label>WHOLESALE MARKET<input value={location.zone} readOnly /></label>}
        <label>EXPORT PAYMENT (€/kWh)<input type="number" min={0} max={2} step={0.01} value={exportPrice} onChange={(event) => setExportPrice(Number(event.target.value))} /></label>
        <label>DISCOUNT RATE (%)<input type="number" min={0} max={20} step={0.5} value={discount} onChange={(event) => setDiscount(Number(event.target.value))} /></label>
        <label>HORIZON (years)<input type="number" min={1} max={40} value={horizon} onChange={(event) => setHorizon(Number(event.target.value))} /></label>
      </div>
      <h3>Configurations <small>Replace the illustrative costs with an installer quotation.</small></h3>
      <div className="system-grid">{systems.map((system, index) => <div key={system.system_id} className="system-card">
        <b>{system.label}</b><small>{system.pv.peak_kw} kWp · tilt {system.pv.tilt_deg}° · {system.battery ? `${system.battery.usable_capacity_kwh} kWh battery` : 'no battery'}</small>
        <label>INSTALLED COST (€)<input type="number" min={0} step={100} value={system.capex_eur} onChange={(event) => setSystems(systems.map((item, position) => position === index ? { ...item, capex_eur: Number(event.target.value), cost_source: 'user-entered quotation' } : item))} /></label>
      </div>)}</div>
      {tariffKind === 'dynamic' && <p className="table-note">The dynamic tariff is an illustrative model: {location.zone} day-ahead price + supplier markup €0.02 + grid charges €0.10 + taxes €0.05 per kWh, plus 19% VAT. It needs a complete year of wholesale prices, so it uses the weather model and 2025.</p>}
      <div className="run-bar"><button className="button primary" onClick={run} disabled={running || !systems.length || !dynamicYearOk}>{running ? 'Simulating 8,760 hours…' : 'Run simulation'}</button><small>PVGIS or Open-Meteo is called once per orientation and cached; the first run for a place can take ~10 s.</small></div>
    </section>
    {error && <ErrorState message={error} hint={error.includes('wholesale') ? 'Choose a year with complete wholesale prices for this market.' : 'Check the inputs; the API explains which value is invalid.'} />}
    {running && <Loading label="Simulating every hour of the year for each configuration…" />}
    {!result && !running && !error && <Empty title="No simulation yet.">Set the household and tariff above, then run the simulation.</Empty>}
    {result && <Results result={result} simulationId={simulationId} focus={focus} setFocus={setFocus} best={best} objective={objective} setObjective={setObjective} budget={budget} setBudget={setBudget} />}
  </main>
}

function Results({ result, simulationId, focus, setFocus, best, objective, setObjective, budget, setBudget }: { result: ScenarioResult; simulationId: string; focus: string; setFocus: (id: string) => void; best: SystemResult | null; objective: Objective; setObjective: (value: Objective) => void; budget: number; setBudget: (value: number) => void }) {
  const [months, setMonths] = useState<Month[] | null>(null)
  const [week, setWeek] = useState(25)
  const [hours, setHours] = useState<Hour[] | null>(null)
  const [detailError, setDetailError] = useState('')
  const system = result.systems.find((item) => item.system_id === focus) ?? result.systems[0]

  useEffect(() => {
    let active = true
    getJson<{ items: Month[] }>(`/api/simulations/${simulationId}/monthly`, { system_id: system.system_id }).then((value) => { if (active) setMonths(value.items) }).catch((reason: Error) => { if (active) setDetailError(reason.message) })
    return () => { active = false }
  }, [simulationId, system.system_id])
  useEffect(() => {
    let active = true
    getJson<{ items: Hour[] }>(`/api/simulations/${simulationId}/hourly`, { system_id: system.system_id, offset: week * 168, limit: 168 }).then((value) => { if (active) setHours(value.items) }).catch((reason: Error) => { if (active) setDetailError(reason.message) })
    return () => { active = false }
  }, [simulationId, system.system_id, week])

  const monthLabels = (months ?? []).map((row) => new Date(`${row.month}-01T00:00:00Z`).toLocaleDateString('en-GB', { month: 'short', timeZone: 'UTC' }))
  const balanceOption: EChartsOption = {
    ...baseOption('kWh per month', monthLabels),
    tooltip: { trigger: 'axis', confine: true, valueFormatter: (value) => `${fmt(value as number, 0)} kWh` },
    series: [
      { name: 'Solar used directly', type: 'bar', stack: 'supply', data: (months ?? []).map((row) => Math.round(row.solar_direct_kwh)), itemStyle: { color: palette.green } },
      { name: 'From battery', type: 'bar', stack: 'supply', data: (months ?? []).map((row) => Math.round(row.battery_discharge_kwh)), itemStyle: { color: palette.teal } },
      { name: 'From grid', type: 'bar', stack: 'supply', data: (months ?? []).map((row) => Math.round(row.grid_import_kwh)), itemStyle: { color: '#4c5f56' } },
      { name: 'Exported to grid', type: 'line', data: (months ?? []).map((row) => Math.round(row.grid_export_kwh)), lineStyle: { color: palette.amber, type: 'dashed' }, itemStyle: { color: palette.amber } },
      { name: 'Household use', type: 'line', data: (months ?? []).map((row) => Math.round(row.load_kwh)), lineStyle: { color: '#e9f0e8', width: 2 }, itemStyle: { color: '#e9f0e8' } },
    ],
  }
  const years = result.systems[0].financial.cashflow.map((row) => `Y${row.year}`)
  const cashOption: EChartsOption = {
    ...baseOption('Cumulative € (after upfront cost)', ['Y0', ...years]),
    tooltip: { trigger: 'axis', confine: true, valueFormatter: (value) => euro(value as number) },
    series: result.systems.map((item, index) => ({ name: item.label, type: 'line', showSymbol: false, lineStyle: { width: item.system_id === system.system_id ? 3 : 1.5, color: [palette.green, palette.blue, palette.amber, palette.pink][index % 4] }, itemStyle: { color: [palette.green, palette.blue, palette.amber, palette.pink][index % 4] },
      data: [-item.financial.capex_eur, ...item.financial.cashflow.map((row) => Math.round(row.cumulative_cashflow_eur))],
      markLine: index === 0 ? { silent: true, symbol: 'none', data: [{ yAxis: 0, label: { formatter: 'Break-even', color: '#c9d6cd' }, lineStyle: { color: '#c9d6cd', type: 'dashed' } }] } : undefined })),
  }
  const hourLabels = (hours ?? []).map((row) => new Date(row.interval_start_utc).toLocaleString('en-GB', { weekday: 'short', hour: '2-digit', timeZone: 'UTC' }))
  const dispatchOption: EChartsOption = {
    ...baseOption('kWh in the hour', hourLabels, true),
    yAxis: [{ type: 'value', name: 'kWh in the hour', nameLocation: 'middle', nameGap: 44, axisLabel: { color: '#9aaea2' }, splitLine: { lineStyle: { color: '#1f2f28' } } },
      { type: 'value', name: 'Battery charge level (kWh)', nameLocation: 'middle', nameGap: 44, axisLabel: { color: '#9aaea2' }, splitLine: { show: false } }],
    tooltip: { trigger: 'axis', confine: true, valueFormatter: (value) => `${fmt(value as number, 2)} kWh` },
    series: [
      { name: 'Solar output', type: 'line', data: (hours ?? []).map((row) => row.pv_kwh), showSymbol: false, areaStyle: { opacity: .15 }, lineStyle: { color: palette.amber }, itemStyle: { color: palette.amber } },
      { name: 'Household use', type: 'line', data: (hours ?? []).map((row) => row.load_kwh), showSymbol: false, lineStyle: { color: '#e9f0e8' }, itemStyle: { color: '#e9f0e8' } },
      { name: 'Battery charging', type: 'bar', data: (hours ?? []).map((row) => row.battery_charge_kwh), itemStyle: { color: palette.teal } },
      { name: 'Battery discharging', type: 'bar', data: (hours ?? []).map((row) => -row.battery_discharge_kwh), itemStyle: { color: palette.pink } },
      { name: 'Battery level', type: 'line', yAxisIndex: 1, data: (hours ?? []).map((row) => row.soc_end_kwh), showSymbol: false, lineStyle: { color: palette.blue, width: 2 }, itemStyle: { color: palette.blue } },
    ],
  }

  return <section className="charts">
    <article className="chart-card full">
      <div className="chart-title"><div><h2>Configuration comparison · {result.simulation_year} weather</h2><p>Energy figures are for one simulated year; money uses year-one savings, then the cash flow applies degradation and price escalation.</p></div>
        <div className="weather-controls"><label>CHOOSE BY<select value={objective} onChange={(event) => setObjective(event.target.value as Objective)}>{Object.entries(OBJECTIVES).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
          {objective === 'budget' && <label>BUDGET (€)<input type="number" step={500} value={budget} onChange={(event) => setBudget(Number(event.target.value))} /></label>}</div></div>
      <div className="table-scroll"><table><thead><tr><th>Configuration</th><th>Solar output<br /><small>kWh/yr</small></th><th>Self-consumed<br /><small>% of solar used at home</small></th><th>Self-sufficiency<br /><small>% of use covered</small></th><th>Grid import<br /><small>kWh</small></th><th>Export<br /><small>kWh</small></th><th>Bill<br /><small>without → with</small></th><th>Net savings<br /><small>year 1</small></th><th>Cost</th><th>Payback</th><th>NPV</th></tr></thead>
        <tbody>{result.systems.map((item) => <tr key={item.system_id} className={`clickable ${item.system_id === system.system_id ? 'highlight' : ''}`} onClick={() => setFocus(item.system_id)}>
          <td>{item.label}{best?.system_id === item.system_id && <span className="pill pill-complete best">Best: {OBJECTIVES[objective]}</span>}</td>
          <td>{fmt(item.energy.pv_kwh, 0)}</td><td>{fmt(item.energy.self_consumption_pct, 0)}%</td><td>{fmt(item.energy.self_sufficiency_pct, 0)}%</td><td>{fmt(item.energy.grid_import_kwh, 0)}</td><td>{fmt(item.energy.grid_export_kwh, 0)}</td>
          <td className="nowrap">{euro(item.financial.bill_without_system_eur)} → {euro(item.financial.bill_with_system_eur)}</td><td>{euro(item.financial.annual_net_savings_eur)}</td><td>{euro(item.financial.capex_eur)}</td>
          <td title={item.financial.payback_note}>{item.financial.simple_payback_years == null ? 'none' : `${fmt(item.financial.simple_payback_years, 1)} yr`}</td><td className={item.financial.npv_eur < 0 ? 'negative' : 'positive'}>{euro(item.financial.npv_eur)}</td></tr>)}</tbody></table></div>
      {objective === 'budget' && !best && <p className="table-note">No configuration fits a €{fmt(budget, 0)} budget.</p>}
      <div className="label-list">{result.labels.map((label) => <span key={label}>{label}</span>)}<span>{result.tariff_note}</span><span>Load profile: {result.load_profile_source}.</span></div>
    </article>
    {detailError && <article className="chart-card full"><ErrorState message={detailError} /></article>}
    <article className="chart-card full">
      <h2>Where the household's electricity comes from · {system.label}</h2>
      <p>Monthly totals: solar used directly, energy returned by the battery and energy bought from the grid stack up to household use. The dashed line is solar exported. Click a row above to switch configuration.</p>
      {months ? <Chart option={balanceOption} height={330} /> : <Loading label="Loading monthly balance…" />}
    </article>
    <article className="chart-card">
      <h2>Cumulative cash flow</h2>
      <p>Starts at minus the installed cost; crossing zero is the payback year. Includes maintenance and battery replacement where configured.</p>
      <Chart option={cashOption} height={300} />
    </article>
    <article className="chart-card">
      <h2>Sensitivity · {system.label}</h2>
      <p>NPV if one assumption changes; base case {euro(system.financial.npv_eur)}.</p>
      <div className="table-scroll"><table className="compact"><thead><tr><th>Case</th><th>NPV</th><th>Payback</th></tr></thead>
        <tbody>{system.sensitivity.map((row) => <tr key={row.case}><td>{row.case}</td><td className={row.npv_eur < 0 ? 'negative' : 'positive'}>{euro(row.npv_eur)}</td><td>{row.simple_payback_years == null ? 'none' : `${fmt(row.simple_payback_years, 1)} yr`}</td></tr>)}</tbody></table></div>
    </article>
    <article className="chart-card full">
      <div className="chart-title"><div><h2>Battery dispatch · one week · {system.label}</h2><p>Hourly solar output, household use, battery charging (up) and discharging (down), and the battery's charge level (right axis). Times in UTC.</p></div>
        <div className="weather-controls"><label>WEEK OF THE YEAR<input type="range" min={0} max={51} value={week} onChange={(event) => setWeek(Number(event.target.value))} /></label><span className="muted">Week {week + 1}</span></div></div>
      {!system.battery_kwh && <p className="table-note">This configuration has no battery; the chart shows solar and household use only.</p>}
      {hours ? <Chart option={dispatchOption} height={330} /> : <Loading label="Loading hours…" />}
    </article>
  </section>
}
