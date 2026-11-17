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
type Market = { country_code: string; bidding_zone: string; market_name: string; is_aggregate: boolean; has_prices: boolean; price_method?: string; price_market_count?: number; first_source_date: string; last_source_date: string; source_timezone: string }
type Point = Record<string, string | number | null>
type Overview = { market: Market; prices: Point[]; generation: Point[]; demand: Point[]; cross_border_flows: Point[]; weather: Point[]; lineage: Record<string, string> }
type Hour = { key: string; label: string; price: number | null; baseline: number | null; demand: number | null; generation: number | null; renewables: number | null; sources: Record<string, number>; flow: number | null; weather: number | null }
const weatherNames: Record<string, string> = { temperature: 'Air temperature', wind_speed: 'Wind speed', shortwave_radiation: 'Solar energy reaching the ground' }
const renewableTypes = new Set(['wind_onshore', 'wind_offshore', 'solar', 'hydro_run_of_river', 'hydro_water_reservoir', 'biomass', 'geothermal', 'other_renewables'])

async function getJson<T>(url: string): Promise<T> {
  const response = await fetch(url)
  if (!response.ok) { const payload = await response.json().catch(() => ({})); throw new Error(payload.detail ?? `Request failed (${response.status})`) }
  return response.json() as Promise<T>
}
function number(value: unknown): number | null { return typeof value === 'number' && Number.isFinite(value) ? value : null }
function fmt(value: number | null, digits = 1) { return value == null ? '—' : new Intl.NumberFormat('en-GB', { maximumFractionDigits: digits }).format(value) }
function euros(value: number, digits = 1) { return value < 0 ? `−€${fmt(Math.abs(value), digits)}` : `€${fmt(value, digits)}` }
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
  const [generationSelection, setGenerationSelection] = useState('all')

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
  const selectedGeneration = hourly.map((row) => generationSelection === 'all' ? row.generation : row.sources[generationSelection] ?? null)
  const comparableHours = hourly.filter((row) => row.demand != null && (generationSelection === 'all' ? row.generation != null : row.sources[generationSelection] != null))
  const selectedGenerationAverage = mean(comparableHours.map((row) => generationSelection === 'all' ? row.generation! : row.sources[generationSelection]!))
  const comparableDemandAverage = mean(comparableHours.map((row) => row.demand!))
  const generationOfDemand = selectedGenerationAverage != null && comparableDemandAverage ? selectedGenerationAverage / comparableDemandAverage * 100 : null
  const meanPrice = mean(prices)
  const isMarketAverage = data?.market.price_method === 'market_average'
  const generationName = generationSelection === 'all' ? 'All reported generation' : generationSelection.replaceAll('_', ' ')
  const labels = hourly.map((row) => row.label)
  const priceChart = { ...chartBase('€ per kWh · wholesale only', labels), series: [{ name: 'Wholesale price', type: 'line', data: hourly.map((row) => row.price == null ? null : row.price / 1000), showSymbol: false }, { name: 'Usual price for this hour', type: 'line', data: hourly.map((row) => row.baseline == null ? null : row.baseline / 1000), showSymbol: false, lineStyle: { type: 'dashed' } }] } satisfies EChartsOption
  const sourceNames = [...new Set(hourly.flatMap((row) => Object.keys(row.sources)))].sort()
  const generationChart = { ...chartBase('GW · power each hour', labels), series: [{ name: generationName, type: 'line', data: selectedGeneration.map((value) => value == null ? null : value / 1000), showSymbol: false, lineStyle: { color: '#6ee0a5', width: 2 } }, { name: 'Power needed from public grid', type: 'line', data: hourly.map((row) => row.demand == null ? null : row.demand / 1000), showSymbol: false, lineStyle: { color: '#87adff', width: 2 } }] } satisfies EChartsOption
  const simpleChart = (unit: string, name: string, values: (number | null)[], color: string): EChartsOption => ({ ...chartBase(unit, labels), series: [{ name, type: 'line', data: values, showSymbol: false, lineStyle: { color, width: 2 } }] })
  const zoneName = selected?.market_name ?? 'Europe'
  const latestMonth = selected ? monthForDate(selected.last_source_date) : month

  function selectMarket(next: string) { const item = markets.find((market) => market.bidding_zone === next); setZone(next); if (item) setMonth(monthForDate(item.last_source_date)) }

  return <main className="dashboard">
    <header className="page-header"><div><div className="eyebrow">EUROGRID · ELECTRICITY MARKETS</div><h1>Electricity at a glance</h1><p>Explore wholesale prices and public electricity data across Europe, by country or European aggregate.</p></div><span className="data-source">Energy-Charts · local research data</span></header>
    <section className="how-to-read" aria-label="How to read the dashboard"><b>How to read these numbers</b><span><strong>GW</strong> = power being produced or needed at one time · <strong>MWh</strong> = energy over an hour · <strong>€/kWh</strong> is a familiar-sized version of the wholesale price (1 MWh = 1,000 kWh).</span><span>Wholesale prices are not home bills. Grid generation and load do not include every import, export or industrial source, so a gap between the lines is not a blackout warning.</span></section>
    <section className="controls"><label>COUNTRY OR ALL EUROPE<select value={zone} onChange={(event) => selectMarket(event.target.value)}>{markets.map((market) => <option key={market.bidding_zone} value={market.bidding_zone}>{market.market_name}</option>)}</select></label><div className="month-control"><label htmlFor="month">MONTH</label><div><button aria-label="Previous month" disabled={!firstMonth || month <= firstMonth} onClick={() => setMonth(shiftMonth(month, -1))}>←</button><input id="month" type="month" min={firstMonth} max={lastMonth} value={month} onChange={(event) => setMonth(event.target.value)} /><button aria-label="Next month" disabled={!lastMonth || month >= lastMonth} onClick={() => setMonth(shiftMonth(month, 1))}>→</button><button className="latest-button" disabled={month === latestMonth} onClick={() => setMonth(latestMonth)}>Latest available</button></div></div><span className="coverage-copy">Available: {firstMonth || '—'} to {lastMonth || '—'}</span></section>
    <p className="period-copy">{zoneName} · {month || 'Loading month'} · times shown in {selected?.source_timezone ?? 'the source timezone'}{selected?.country_code === 'all' ? ' · all timestamps are compared in UTC' : selected?.has_prices ? ' · hourly averages from source intervals' : ''}</p>
    {error && <div className="error">Could not load this view: {error}. Check the local API and database.</div>}{loading && <div className="loading">Loading {zoneName} data…</div>}
    {!loading && data && <>
      <section className="metrics"><article><small>AVERAGE WHOLESALE PRICE · {month}</small><strong>{meanPrice == null ? 'No price data' : `${euros(meanPrice / 1000, 3)} / kWh`}</strong><p>{meanPrice == null ? 'No price is published for this selection.' : `That is ${euros(meanPrice)} per MWh. Wholesale market price only; not a household tariff.`}</p></article><article><small>LOWEST TO HIGHEST HOURLY PRICE · {month}</small><strong>{prices.length ? `${euros(Math.min(...prices) / 1000, 3)} – ${euros(Math.max(...prices) / 1000, 3)} / kWh` : 'No price data'}</strong><p>{prices.length ? `Wholesale-only equivalent · ${euros(Math.min(...prices))}–${euros(Math.max(...prices))} per MWh across the selected month.` : 'A price range is not available for this selection.'}</p></article><article><small>AVERAGE POWER NEEDED FROM THE GRID</small><strong>{demand == null ? '—' : `${fmt(demand / 1000, 1)} GW`}</strong><p>At a typical hour in {month}, this much power was being drawn from the public grid. (1 GW = 1,000 MW.)</p></article><article><small>AVERAGE {generationName.toUpperCase()}</small><strong>{selectedGenerationAverage == null ? '—' : `${fmt(selectedGenerationAverage / 1000, 1)} GW`}</strong><p>{generationOfDemand == null ? 'Not enough matching hours to compare with grid load.' : `About ${fmt(generationOfDemand, 0)}% of the average public-grid load during matching hours.`}</p></article></section>
      {selected?.is_aggregate && <p className="aggregate-note">All Europe is the provider's combined Europe series. The price figures above are a simple average of the available country-market prices for each hour, not one official European market price. Weather is location-specific, so choose a country to see its reference weather.</p>}
      <section className="charts">
        {data.market.has_prices ? <article className="chart-card full"><h2>{isMarketAverage ? 'Average wholesale price across available country markets' : 'Wholesale electricity price'}</h2><p>Shown as euros per kWh to make the scale familiar; this is still the wholesale price, not a home bill. {isMarketAverage ? `A simple average of ${data.market.price_market_count} available country-market prices each hour; markets are equally weighted.` : 'Dashed line shows the usual price at the same hour in stored data.'}</p><ReactEChartsCore echarts={echarts} option={priceChart} style={{ height: 330 }} notMerge lazyUpdate /></article> : <article className="chart-card full no-chart"><h2>Wholesale electricity price</h2><p>This data source has no price series for the United Kingdom. Prices are available by market area, not for every country.</p></article>}
        <article className="chart-card"><div className="chart-title"><h2>Power generated compared with power needed</h2><select aria-label="Generation to compare with grid load" value={generationSelection} onChange={(event) => setGenerationSelection(event.target.value)}><option value="all">All sources combined</option>{sourceNames.map((name) => <option key={name} value={name}>{name.replaceAll('_', ' ')}</option>)}</select></div><p>Both lines use GW, so compare their heights. The number above shows this selection as a share of average grid demand for matching hours.</p>{sourceNames.length ? <ReactEChartsCore echarts={echarts} option={generationChart} style={{ height: 320 }} notMerge lazyUpdate /> : <div className="no-chart">No generation records for this month.</div>}</article>
        <article className="chart-card"><h2>Renewable share of generation</h2><p>Renewable output divided by the valid generation sources reported in that hour.</p><ReactEChartsCore echarts={echarts} option={simpleChart('% of reported generation', 'Renewable share', hourly.map((row) => row.renewables), '#e3c56e')} style={{ height: 320 }} notMerge lazyUpdate /></article>
        <article className="chart-card"><h2>Scheduled cross-border electricity</h2><p>Planned net trading across borders, in MW. The direction follows the provider’s sign convention.</p>{data.cross_border_flows.length ? <ReactEChartsCore echarts={echarts} option={simpleChart('MW · scheduled flow', 'Net scheduled flow', hourly.map((row) => row.flow), '#c68cff')} style={{ height: 320 }} notMerge lazyUpdate /> : <div className="no-chart">No cross-border flow series is available for this selection.</div>}</article>
        <article className="chart-card"><div className="chart-title"><h2>Weather near the selected country</h2><select value={weatherVariable} onChange={(event) => setWeatherVariable(event.target.value)}><option value="temperature">Air temperature (°C)</option><option value="wind_speed">Wind speed (m/s)</option><option value="shortwave_radiation">Solar energy at ground (W/m²)</option></select></div><p>{weatherVariable === 'temperature' ? 'Air temperature at one reference location in the selected country.' : weatherVariable === 'wind_speed' ? 'Wind speed measured near the ground at the reference location; it is context, not turbine output.' : 'Average solar energy reaching a flat surface each hour. It helps explain sunlight available for solar panels; it is not electricity produced.'} Weather is context only: to estimate a home solar-and-battery result we also need panel size and direction, battery size, and home electricity use.</p>{data.weather.some((row) => row.variable === weatherVariable) ? <ReactEChartsCore echarts={echarts} option={simpleChart(String(data.weather.find((row) => row.variable === weatherVariable)?.weather_unit ?? 'weather reading'), weatherNames[weatherVariable], hourly.map((row) => row.weather), '#4ccbd2')} style={{ height: 320 }} notMerge lazyUpdate /> : <div className="no-chart">{selected?.is_aggregate ? 'Choose a country to see weather at its reference location.' : `No ${weatherNames[weatherVariable].toLowerCase()} observations are available for ${zoneName} in ${month}.`}</div>}</article>
      </section>
      <details className="sources"><summary>Data sources and limitations</summary><p>{Object.values(data.lineage).join(' ')}</p><p>Source observations have been grouped into UTC hours for comparison. Some series are missing in some countries or periods; a blank chart does not mean zero.</p></details>
    </>}
    <footer>EUROGRID INTELLIGENCE · LOCAL RESEARCH DASHBOARD</footer>
  </main>
}
