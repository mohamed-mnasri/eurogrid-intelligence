import { useEffect, useMemo, useState } from 'react'
import ReactEChartsCore from 'echarts-for-react/esm/core'
import * as echarts from 'echarts/core'
import { LineChart } from 'echarts/charts'
import { DataZoomComponent, GridComponent, LegendScrollComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import type { EChartsOption } from 'echarts'
import './styles.css'

// Register only the chart features used by this page.
echarts.use([LineChart, DataZoomComponent, GridComponent, LegendScrollComponent, TooltipComponent, CanvasRenderer])

const API = import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000'
type Market = { country_code: string; bidding_zone: string; market_name: string; is_aggregate: boolean; has_prices: boolean; first_source_date: string; last_source_date: string; source_timezone: string }
type Point = Record<string, string | number | null>
type Overview = { market: Market; prices: Point[]; generation: Point[]; demand: Point[]; cross_border_flows: Point[]; weather: Point[]; lineage: Record<string, string> }
type Hour = { key: string; label: string; price: number | null; baseline: number | null; demand: number | null; generation: number | null; renewables: number | null; sources: Record<string, number>; flow: number | null; weather: number | null }
const weatherNames: Record<string, string> = { temperature: 'Air temperature', wind_speed: 'Wind speed', shortwave_radiation: 'Sunlight reaching the ground' }
const renewableTypes = new Set(['wind_onshore', 'wind_offshore', 'solar', 'hydro_run_of_river', 'hydro_water_reservoir', 'biomass', 'geothermal', 'other_renewables'])

async function getJson<T>(url: string): Promise<T> {
  const response = await fetch(url)
  if (!response.ok) { const payload = await response.json().catch(() => ({})); throw new Error(payload.detail ?? `Request failed (${response.status})`) }
  return response.json() as Promise<T>
}
function number(value: unknown): number | null { return typeof value === 'number' && Number.isFinite(value) ? value : null }
function fmt(value: number | null, digits = 1) { return value == null ? '—' : new Intl.NumberFormat('en-GB', { maximumFractionDigits: digits }).format(value) }
function monthBounds(month: string) { const [year, m] = month.split('-').map(Number); return { start: `${month}-01`, end: new Date(Date.UTC(year, m, 0)).toISOString().slice(0, 10) } }
function shiftMonth(month: string, delta: number) { const [year, m] = month.split('-').map(Number); return new Date(Date.UTC(year, m - 1 + delta, 1)).toISOString().slice(0, 7) }
function monthForDate(date: string) { return date?.slice(0, 7) ?? '' }
function chartBase(unit: string, labels: string[]): EChartsOption {
  return { animation: false, grid: { left: 68, right: 20, top: 48, bottom: 52, containLabel: true }, tooltip: { trigger: 'axis', confine: true }, legend: { top: 3, type: 'scroll' }, xAxis: { type: 'category', data: labels, axisLabel: { hideOverlap: true } }, yAxis: { type: 'value', name: unit, nameLocation: 'middle', nameGap: 48 }, dataZoom: [{ type: 'inside' }, { type: 'slider', height: 12, bottom: 4 }] }
}

export default function App() {
  const [markets, setMarkets] = useState<Market[]>([])
  const [zone, setZone] = useState('')
  const [month, setMonth] = useState('')
  const [data, setData] = useState<Overview | null>(null)
  const [error, setError] = useState('')
  const [coverageReady, setCoverageReady] = useState(false)
  const [loadedRequest, setLoadedRequest] = useState('')
  const [weatherVariable, setWeatherVariable] = useState('temperature')

  useEffect(() => {
    getJson<{ markets: Market[] }>(`${API}/api/coverage`).then(({ markets: available }) => {
      setMarkets(available)
      const allEurope = available.find((item) => item.country_code === 'all') ?? available[0]
      if (allEurope) { setZone(allEurope.bidding_zone); setMonth(monthForDate(allEurope.last_source_date)) }
    }).catch((reason: Error) => setError(reason.message)).finally(() => setCoverageReady(true))
  }, [])
  const selected = markets.find((market) => market.bidding_zone === zone)
  const bounds = month ? monthBounds(month) : null
  const firstMonth = selected ? monthForDate(selected.first_source_date) : ''
  const lastMonth = selected ? monthForDate(selected.last_source_date) : ''
  const effectiveStart = bounds && firstMonth && month === firstMonth && bounds.start < selected!.first_source_date ? selected!.first_source_date : bounds?.start
  const effectiveEnd = bounds && lastMonth && month === lastMonth && bounds.end > selected!.last_source_date ? selected!.last_source_date : bounds?.end
  const requestKey = zone && effectiveStart && effectiveEnd ? `${zone}|${effectiveStart}|${effectiveEnd}` : ''
  const loading = !coverageReady || (!!requestKey && loadedRequest !== requestKey)

  useEffect(() => {
    if (!zone || !effectiveStart || !effectiveEnd) return
    const params = new URLSearchParams({ bidding_zone: zone, start_date: effectiveStart, end_date: effectiveEnd })
    const thisRequest = `${zone}|${effectiveStart}|${effectiveEnd}`
    let active = true
    getJson<Overview>(`${API}/api/overview?${params}`).then((result) => { if (active) { setData(result); setError(''); setLoadedRequest(thisRequest) } }).catch((reason: Error) => { if (active) { setData(null); setError(reason.message); setLoadedRequest(thisRequest) } })
    return () => { active = false }
  }, [zone, effectiveStart, effectiveEnd])

  const hourly = useMemo<Hour[]>(() => {
    if (!data) return []
    // Provider series can be hourly or 15-minute. Group by UTC hour before comparing them.
    const buckets = new Map<string, { label: string; price: number[]; baseline: number[]; demand: number[]; source: Record<string, number[]>; flow: number[]; weather: number[] }>()
    const ensure = (row: Point) => {
      const instant = Date.parse(String(row.interval_start_utc ?? ''))
      if (!Number.isFinite(instant)) return null
      const key = new Date(Math.floor(instant / 3600000) * 3600000).toISOString()
      if (!buckets.has(key)) buckets.set(key, { label: '', price: [], baseline: [], demand: [], source: {}, flow: [], weather: [] })
      const bucket = buckets.get(key)!
      if (!bucket.label) bucket.label = String(row.original_timestamp ?? key).replace('T', ' ').slice(0, 16)
      return bucket
    }
    const add = (items: Point[], field: string, destination: 'price' | 'baseline' | 'demand' | 'flow' | 'weather') => items.forEach((row) => { const bucket = ensure(row); const value = number(row[field]); if (bucket && row.data_status === 'valid' && value != null) bucket[destination].push(value) })
    add(data.prices, 'price_eur_per_mwh', 'price'); add(data.prices, 'mean_price_eur_per_mwh', 'baseline')
    add(data.demand, 'demand_mw', 'demand'); add(data.cross_border_flows, 'net_cross_border_trading_mw', 'flow')
    data.weather.filter((row) => row.variable === weatherVariable).forEach((row) => { const bucket = ensure(row); const value = number(row.weather_value); if (bucket && row.data_status === 'valid' && value != null) bucket.weather.push(value) })
    data.generation.forEach((row) => { const bucket = ensure(row); const value = number(row.generation_mw); const name = String(row.production_type); if (bucket && row.data_status === 'valid' && value != null) (bucket.source[name] ??= []).push(value) })
    const mean = (values: number[]) => values.length ? values.reduce((a, b) => a + b, 0) / values.length : null
    return [...buckets.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([key, item]) => {
      const sources = Object.fromEntries(Object.entries(item.source).map(([name, values]) => [name, mean(values)!]))
      const generation = Object.values(sources).reduce((sum, value) => sum + value, 0)
      const renewable = Object.entries(sources).filter(([name]) => renewableTypes.has(name)).reduce((sum, [, value]) => sum + value, 0)
      return { key, label: item.label, price: mean(item.price), baseline: mean(item.baseline), demand: mean(item.demand), generation: Object.keys(sources).length ? generation : null, renewables: generation > 0 ? renewable / generation * 100 : null, sources, flow: mean(item.flow), weather: mean(item.weather) }
    })
  }, [data, weatherVariable])

  const prices = hourly.filter((row) => row.price != null).map((row) => row.price!)
  const mean = (values: number[]) => values.length ? values.reduce((a, b) => a + b, 0) / values.length : null
  const demand = mean(hourly.flatMap((row) => row.demand == null ? [] : [row.demand]))
  const generation = mean(hourly.flatMap((row) => row.generation == null ? [] : [row.generation]))
  const renewableShare = mean(hourly.flatMap((row) => row.renewables == null ? [] : [row.renewables]))
  const labels = hourly.map((row) => row.label)
  const priceChart = { ...chartBase('€ per MWh', labels), series: [{ name: 'Wholesale price', type: 'line', data: hourly.map((row) => row.price), showSymbol: false }, { name: 'Usual price for this hour', type: 'line', data: hourly.map((row) => row.baseline), showSymbol: false, lineStyle: { type: 'dashed' } }] } satisfies EChartsOption
  const sourceNames = [...new Set(hourly.flatMap((row) => Object.keys(row.sources)))].sort()
  const generationChart = { ...chartBase('MW · power produced', labels), legend: { top: 3, type: 'scroll' }, series: sourceNames.map((name) => ({ name: name.replaceAll('_', ' '), type: 'line', stack: 'generation', areaStyle: { opacity: .2 }, showSymbol: false, data: hourly.map((row) => row.sources[name] ?? null) })) } satisfies EChartsOption
  const simpleChart = (unit: string, name: string, values: (number | null)[], color: string): EChartsOption => ({ ...chartBase(unit, labels), series: [{ name, type: 'line', data: values, showSymbol: false, lineStyle: { color, width: 2 } }] })
  const zoneName = selected?.market_name ?? 'Europe'
  const latestMonth = selected ? monthForDate(selected.last_source_date) : month

  function selectMarket(next: string) { const item = markets.find((market) => market.bidding_zone === next); setZone(next); if (item) setMonth(monthForDate(item.last_source_date)) }

  return <main className="dashboard">
    <header className="page-header"><div><div className="eyebrow">EUROGRID · ELECTRICITY MARKETS</div><h1>Electricity at a glance</h1><p>Explore wholesale prices and public electricity data across Europe, by country or European aggregate.</p></div><span className="data-source">Energy-Charts · local research data</span></header>
    <section className="how-to-read" aria-label="How to read the dashboard"><b>How to read these numbers</b><span><strong>MW</strong> = power at a point in time · <strong>MWh</strong> = energy used or produced over an hour · <strong>€/MWh</strong> = wholesale energy price, not a household bill.</span><span>Generation and demand below describe the public grid. Imports, exports, storage and industry self-supply affect the full electricity balance, so these lines are not a blackout or spare-capacity measure.</span></section>
    <section className="controls"><label>COUNTRY OR EUROPEAN AREA<select value={zone} onChange={(event) => selectMarket(event.target.value)}>{markets.map((market) => <option key={market.bidding_zone} value={market.bidding_zone}>{market.market_name}</option>)}</select></label><div className="month-control"><label htmlFor="month">MONTH</label><div><button aria-label="Previous month" disabled={!firstMonth || month <= firstMonth} onClick={() => setMonth(shiftMonth(month, -1))}>←</button><input id="month" type="month" min={firstMonth} max={lastMonth} value={month} onChange={(event) => setMonth(event.target.value)} /><button aria-label="Next month" disabled={!lastMonth || month >= lastMonth} onClick={() => setMonth(shiftMonth(month, 1))}>→</button><button className="latest-button" disabled={month === latestMonth} onClick={() => setMonth(latestMonth)}>Latest available</button></div></div><span className="coverage-copy">Available: {firstMonth || '—'} to {lastMonth || '—'}</span></section>
    <p className="period-copy">{zoneName} · {month || 'Loading month'} · times shown in {selected?.source_timezone ?? 'the source timezone'}{selected?.has_prices ? ' · hourly averages from source intervals' : ' · no single wholesale price series is available for this area'}</p>
    {error && <div className="error">Could not load this view: {error}. Check the local API and database.</div>}{loading && <div className="loading">Loading {zoneName} data…</div>}
    {!loading && data && <>
      <section className="metrics"><article><small>WHOLESALE PRICE · LAST HOUR</small><strong>{prices.length ? `€${fmt(prices.at(-1)!)} / MWh` : 'Not available'}</strong><p>{prices.length ? 'Day-ahead market price for one megawatt-hour.' : 'This provider has no single Europe-wide price or UK price series here.'}</p></article><article><small>PRICE RANGE · SELECTED MONTH</small><strong>{prices.length ? `€${fmt(Math.min(...prices))} – €${fmt(Math.max(...prices))}` : 'Not available'}</strong><p>Lowest to highest hourly wholesale price in the selected month.</p></article><article><small>AVERAGE PUBLIC-GRID LOAD</small><strong>{demand == null ? '—' : `${fmt(demand, 0)} MW`}</strong><p>Average power drawn from the public grid in this month.</p></article><article><small>AVERAGE REPORTED GENERATION</small><strong>{generation == null ? '—' : `${fmt(generation, 0)} MW`}</strong><p>Average generation shown in this public dataset · {renewableShare == null ? 'renewable share unavailable' : `${fmt(renewableShare)}% renewable`}.</p></article></section>
      {selected?.is_aggregate && <p className="aggregate-note">{selected.market_name} is a provider-supplied aggregate. It cannot be split into regional or country values from this series; choose a country above to view country-level records.</p>}
      <section className="charts">
        {selected?.has_prices ? <article className="chart-card full"><h2>Wholesale electricity price</h2><p>Price to buy one MWh on the day-ahead market. Dashed line shows the stored average for the same UTC hour.</p><ReactEChartsCore echarts={echarts} option={priceChart} style={{ height: 330 }} notMerge lazyUpdate /></article> : <article className="chart-card full no-chart"><h2>Wholesale electricity price</h2><p>There is no single Europe-wide day-ahead price: each bidding zone has its own market price. This provider also has no UK price series in the configured catalogue.</p></article>}
        <article className="chart-card"><h2>Electricity generated by source</h2><p>Each area is one source. Stacked areas show the reported output for each hour, in megawatts.</p>{sourceNames.length ? <ReactEChartsCore echarts={echarts} option={generationChart} style={{ height: 320 }} notMerge lazyUpdate /> : <div className="no-chart">No generation records for this month.</div>}</article>
        <article className="chart-card"><h2>Power needed from the public grid</h2><p>Provider-reported public-grid load. This is not the country’s full energy consumption.</p><ReactEChartsCore echarts={echarts} option={simpleChart('MW · power needed', 'Public-grid load', hourly.map((row) => row.demand), '#87adff')} style={{ height: 320 }} notMerge lazyUpdate /></article>
        <article className="chart-card"><h2>Renewable share of generation</h2><p>Renewable output divided by the valid generation sources reported in that hour.</p><ReactEChartsCore echarts={echarts} option={simpleChart('% of reported generation', 'Renewable share', hourly.map((row) => row.renewables), '#e3c56e')} style={{ height: 320 }} notMerge lazyUpdate /></article>
        <article className="chart-card"><h2>Scheduled cross-border electricity</h2><p>Planned net trading across borders, in MW. The direction follows the provider’s sign convention.</p>{data.cross_border_flows.length ? <ReactEChartsCore echarts={echarts} option={simpleChart('MW · scheduled flow', 'Net scheduled flow', hourly.map((row) => row.flow), '#c68cff')} style={{ height: 320 }} notMerge lazyUpdate /> : <div className="no-chart">No cross-border flow series is available for this selection.</div>}</article>
        <article className="chart-card"><div className="chart-title"><h2>Weather at a reference location</h2><select value={weatherVariable} onChange={(event) => setWeatherVariable(event.target.value)}><option value="temperature">Air temperature (°C)</option><option value="wind_speed">Wind speed (m/s)</option><option value="shortwave_radiation">Sunlight (W/m²)</option></select></div><p>One nearby reference point, not a country-wide average or a direct measure of power generation.</p>{data.weather.length ? <ReactEChartsCore echarts={echarts} option={simpleChart(String(data.weather.find((row) => row.variable === weatherVariable)?.weather_unit ?? 'weather reading'), weatherNames[weatherVariable], hourly.map((row) => row.weather), '#4ccbd2')} style={{ height: 320 }} notMerge lazyUpdate /> : <div className="no-chart">Weather is not supplied for this aggregate or month.</div>}</article>
      </section>
      <details className="sources"><summary>Data sources and limitations</summary><p>{Object.values(data.lineage).join(' ')}</p><p>Source observations have been grouped into UTC hours for comparison. Some series are missing in some countries or periods; a blank chart does not mean zero.</p></details>
    </>}
    <footer>EUROGRID INTELLIGENCE · LOCAL RESEARCH DASHBOARD</footer>
  </main>
}
