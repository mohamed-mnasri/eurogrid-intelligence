import { useEffect, useMemo, useState } from 'react'
import ReactEChartsCore from 'echarts-for-react/esm/core'
import * as echarts from 'echarts/core'
import { LineChart } from 'echarts/charts'
import { DataZoomComponent, GridComponent, LegendScrollComponent, MarkLineComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import type { EChartsOption } from 'echarts'
import './styles.css'

// Register the small set of chart features used by the market page.
echarts.use([LineChart, DataZoomComponent, GridComponent, LegendScrollComponent, MarkLineComponent, TooltipComponent, CanvasRenderer])

const API = import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000'
const weatherCities: Record<string, string> = { de: 'Berlin', fr: 'Paris', es: 'Madrid', pl: 'Warsaw', nl: 'Amsterdam', uk: 'London' }
type Market = { country_code: string; bidding_zone: string; market_name: string; is_aggregate: boolean; has_prices: boolean; price_method?: string; price_market_count?: number; first_source_date: string; last_source_date: string; source_timezone: string }
type Point = Record<string, string | number | null>
type Benchmark = { geography: string; daily_kwh_per_person: number | null; annual_kwh_per_person: number | null; consumption_year: string | null; retail_eur_per_kwh: number | null; retail_period: string | null; retail_band: string; retail_tax_level: string; sources: Record<string, string> }
type Overview = { market: Market; prices: Point[]; generation: Point[]; demand: Point[]; cross_border_flows: Point[]; weather: Point[]; lineage: Record<string, string> }
type Hour = { key: string; label: string; price: number | null; baseline: number | null; demand: number | null; generation: number | null; renewables: number | null; sources: Record<string, number>; temperature: number | null; windSpeed: number | null; solarRadiation: number | null }
type DaySummary = { day: string; highTemperature: number | null; meanWind: number | null; sunlightKwhM2: number | null; solarMwh: number; solarLoadShare: number | null; windMwh: number; windLoadShare: number | null }
const renewableTypes = new Set(['wind_onshore', 'wind_offshore', 'solar', 'hydro_run_of_river', 'hydro_water_reservoir', 'biomass', 'geothermal', 'other_renewables'])

async function getJson<T>(url: string): Promise<T> {
  const response = await fetch(url)
  if (!response.ok) { const payload = await response.json().catch(() => ({})); throw new Error(payload.detail ?? `Request failed (${response.status})`) }
  return response.json() as Promise<T>
}
function numeric(value: unknown): number | null { return typeof value === 'number' && Number.isFinite(value) ? value : null }
function fmt(value: number | null | undefined, digits = 1) { return value == null ? '—' : new Intl.NumberFormat('en-GB', { maximumFractionDigits: digits }).format(value) }
function euro(value: number, digits = 2) { return value < 0 ? `−€${fmt(Math.abs(value), digits)}` : `€${fmt(value, digits)}` }
function monthBounds(month: string) { const [year, m] = month.split('-').map(Number); return { start: `${month}-01`, end: new Date(Date.UTC(year, m, 0)).toISOString().slice(0, 10) } }
function shiftMonth(month: string, delta: number) { const [year, m] = month.split('-').map(Number); return new Date(Date.UTC(year, m - 1 + delta, 1)).toISOString().slice(0, 7) }
function monthForDate(date: string) { return date?.slice(0, 7) ?? '' }
function displaySource(source: string) { return source.replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase()) }
function chartBase(unit: string, labels: string[]): EChartsOption {
  return { animation: false, grid: { left: 72, right: 22, top: 48, bottom: 54, containLabel: true }, tooltip: { trigger: 'axis', confine: true }, legend: { top: 3, type: 'scroll' }, xAxis: { type: 'category', data: labels, axisLabel: { hideOverlap: true } }, yAxis: { type: 'value', name: unit, nameLocation: 'middle', nameGap: 50 }, dataZoom: [{ type: 'inside' }, { type: 'slider', height: 12, bottom: 4 }] }
}

export default function App() {
  const [markets, setMarkets] = useState<Market[]>([])
  const [zone, setZone] = useState('')
  const [month, setMonth] = useState('')
  const [priceDay, setPriceDay] = useState('')
  const [data, setData] = useState<Overview | null>(null)
  const [error, setError] = useState('')
  const [coverageReady, setCoverageReady] = useState(false)
  const [loadedRequest, setLoadedRequest] = useState('')
  const [generationSelection, setGenerationSelection] = useState('all')
  const [dailyUse, setDailyUse] = useState('')
  const [usageEdited, setUsageEdited] = useState(false)
  const [benchmark, setBenchmark] = useState<Benchmark | null>(null)
  const [benchmarkErrorGeo, setBenchmarkErrorGeo] = useState('')

  useEffect(() => {
    getJson<{ markets: Market[] }>(`${API}/api/coverage`).then(({ markets: available }) => {
      setMarkets(available)
      const allEurope = available.find((item) => item.country_code === 'all') ?? available[0]
      if (allEurope) { setZone(allEurope.bidding_zone); setMonth(monthForDate(allEurope.last_source_date)); setPriceDay(allEurope.last_source_date) }
    }).catch((reason: Error) => setError(reason.message)).finally(() => setCoverageReady(true))
  }, [])

  const selected = markets.find((market) => market.bidding_zone === zone)
  const benchmarkGeo = selected?.country_code === 'all' ? 'EU27_2020' : selected?.country_code.toUpperCase() ?? ''
  const currentBenchmark = benchmark?.geography === benchmarkGeo ? benchmark : null
  useEffect(() => {
    if (!benchmarkGeo) return
    let active = true
    const params = new URLSearchParams({ geo: benchmarkGeo })
    getJson<Benchmark>(`${API}/api/household-benchmark?${params}`).then((value) => {
      if (active) {
        setBenchmark(value)
        if (!usageEdited && value.daily_kwh_per_person != null) setDailyUse(value.daily_kwh_per_person.toFixed(1))
      }
    }).catch(() => { if (active) setBenchmarkErrorGeo(benchmarkGeo) })
    return () => { active = false }
  }, [benchmarkGeo, usageEdited])

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
    // Different feeds can be hourly or 15-minute; average them into UTC-hour buckets first.
    const buckets = new Map<string, { label: string; price: number[]; baseline: number[]; demand: number[]; source: Record<string, number[]>; weather: Record<string, number[]> }>()
    const ensure = (row: Point) => {
      const instant = Date.parse(String(row.interval_start_utc ?? ''))
      if (!Number.isFinite(instant)) return null
      const key = new Date(Math.floor(instant / 3600000) * 3600000).toISOString()
      if (!buckets.has(key)) buckets.set(key, { label: '', price: [], baseline: [], demand: [], source: {}, weather: {} })
      const bucket = buckets.get(key)!
      if (!bucket.label) bucket.label = String(row.original_timestamp ?? key).replace('T', ' ').slice(0, 16)
      return bucket
    }
    const add = (rows: Point[], field: string, into: 'price' | 'baseline' | 'demand') => rows.forEach((row) => {
      const bucket = ensure(row); const value = numeric(row[field])
      if (bucket && row.data_status === 'valid' && value != null) bucket[into].push(value)
    })
    add(data.prices, 'price_eur_per_mwh', 'price'); add(data.prices, 'mean_price_eur_per_mwh', 'baseline'); add(data.demand, 'demand_mw', 'demand')
    data.generation.forEach((row) => {
      const bucket = ensure(row); const value = numeric(row.generation_mw); const source = String(row.production_type)
      if (bucket && row.data_status === 'valid' && value != null) (bucket.source[source] ??= []).push(value)
    })
    data.weather.forEach((row) => {
      const bucket = ensure(row); const value = numeric(row.weather_value); const variable = String(row.variable)
      if (bucket && row.data_status === 'valid' && value != null) (bucket.weather[variable] ??= []).push(value)
    })
    const average = (values: number[]) => values.length ? values.reduce((a, b) => a + b, 0) / values.length : null
    return [...buckets.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([key, item]) => {
      const sources = Object.fromEntries(Object.entries(item.source).map(([name, values]) => [name, average(values)!]))
      const weather = Object.fromEntries(Object.entries(item.weather).map(([name, values]) => [name, average(values)!]))
      const generation = Object.values(sources).reduce((sum, value) => sum + value, 0)
      const renewables = Object.entries(sources).filter(([name]) => renewableTypes.has(name)).reduce((sum, [, value]) => sum + value, 0)
      return { key, label: item.label, price: average(item.price), baseline: average(item.baseline), demand: average(item.demand), generation: Object.keys(sources).length ? generation : null, renewables: generation > 0 ? renewables : null, sources, temperature: weather.temperature, windSpeed: weather.wind_speed, solarRadiation: weather.shortwave_radiation }
    })
  }, [data])

  const mean = (values: number[]) => values.length ? values.reduce((a, b) => a + b, 0) / values.length : null
  const timezone = data?.market.source_timezone || 'UTC'
  const selectedDay = priceDay >= (effectiveStart ?? '') && priceDay <= (effectiveEnd ?? '') ? priceDay : effectiveEnd ?? ''
  const dayPriceRows = hourly.filter((row) => row.label.slice(0, 10) === selectedDay && row.price != null)
  const dayWholesaleEurKwh = mean(dayPriceRows.map((row) => row.price!))
  const monthlyDemand = mean(hourly.flatMap((row) => row.demand == null ? [] : [row.demand]))
  const sourceNames = [...new Set(hourly.flatMap((row) => Object.keys(row.sources)))].sort()
  const selectedOutput = (row: Hour) => generationSelection === 'all'
    ? row.generation
    : generationSelection === 'renewables' ? row.renewables : row.sources[generationSelection] ?? null
  const generationName = generationSelection === 'all' ? 'All reported generation' : generationSelection === 'renewables' ? 'Renewable generation' : displaySource(generationSelection)
  const comparisonRows = hourly.filter((row) => row.demand != null && row.demand > 0 && selectedOutput(row) != null)
  const coveragePercent = mean(comparisonRows.map((row) => selectedOutput(row)! / row.demand! * 100))
  const generationLabels = hourly.map((row) => row.label)
  const generationOption = {
    ...chartBase('% of public-grid load · 100% = load', generationLabels),
    yAxis: { type: 'value', min: 0, max: (range: { max: number }) => Math.max(110, Math.ceil(range.max / 10) * 10), name: '% of public-grid load', nameLocation: 'middle', nameGap: 54 },
    series: [{ name: generationName, type: 'line', data: hourly.map((row) => row.demand && selectedOutput(row) != null ? selectedOutput(row)! / row.demand * 100 : null), showSymbol: false, lineStyle: { color: '#6ee0a5', width: 2 }, markLine: { silent: true, symbol: 'none', data: [{ yAxis: 100, label: { formatter: '100% · reported load' }, lineStyle: { color: '#efbd68', type: 'dashed', width: 2 } }] } }],
  } satisfies EChartsOption
  const loadOption = { ...chartBase('GW · public-grid load', generationLabels), series: [{ name: 'Power needed from public grid', type: 'line', data: hourly.map((row) => row.demand == null ? null : row.demand / 1000), showSymbol: false, lineStyle: { color: '#87adff', width: 2 }, areaStyle: { opacity: .08 } }] } satisfies EChartsOption
  const priceOption = { ...chartBase('€ per kWh · wholesale', hourly.map((row) => row.label)), series: [{ name: 'Wholesale price', type: 'line', data: hourly.map((row) => row.price == null ? null : row.price / 1000), showSymbol: false, lineStyle: { color: '#6ee0a5', width: 2 } }, { name: 'Usual price for this hour', type: 'line', data: hourly.map((row) => row.baseline == null ? null : row.baseline / 1000), showSymbol: false, lineStyle: { color: '#f2b66d', type: 'dashed' } }] } satisfies EChartsOption

  const days = useMemo<DaySummary[]>(() => {
    const daily = new Map<string, { temps: number[]; winds: number[]; radiation: number[]; solarMwh: number; windMwh: number; loadMwh: number }>()
    for (const hour of hourly) {
      const day = new Intl.DateTimeFormat('en-CA', { timeZone: timezone, year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date(hour.key))
      const summary = daily.get(day) ?? { temps: [], winds: [], radiation: [], solarMwh: 0, windMwh: 0, loadMwh: 0 }
      if (hour.temperature != null) summary.temps.push(hour.temperature)
      if (hour.windSpeed != null) summary.winds.push(hour.windSpeed)
      if (hour.solarRadiation != null) summary.radiation.push(hour.solarRadiation)
      summary.solarMwh += hour.sources.solar ?? 0
      summary.windMwh += (hour.sources.wind_onshore ?? 0) + (hour.sources.wind_offshore ?? 0)
      summary.loadMwh += hour.demand ?? 0
      daily.set(day, summary)
    }
    return [...daily.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([day, value]) => ({
      day,
      highTemperature: value.temps.length ? Math.max(...value.temps) : null,
      meanWind: mean(value.winds),
      sunlightKwhM2: value.radiation.length ? value.radiation.reduce((a, b) => a + b, 0) / 1000 : null,
      solarMwh: value.solarMwh,
      solarLoadShare: value.loadMwh > 0 ? value.solarMwh / value.loadMwh * 100 : null,
      windMwh: value.windMwh,
      windLoadShare: value.loadMwh > 0 ? value.windMwh / value.loadMwh * 100 : null,
    }))
  }, [hourly, timezone])

  const usageValue = Number(dailyUse)
  const wholesaleCost = Number.isFinite(usageValue) && usageValue >= 0 && dayWholesaleEurKwh != null ? usageValue * dayWholesaleEurKwh / 1000 : null
  const retailCost = Number.isFinite(usageValue) && usageValue >= 0 && currentBenchmark?.retail_eur_per_kwh != null ? usageValue * currentBenchmark.retail_eur_per_kwh : null
  const zoneName = selected?.market_name ?? 'Europe'
  const weatherCity = selected ? weatherCities[selected.country_code] : undefined
  const latestMonth = selected ? monthForDate(selected.last_source_date) : month

  function setMonthAndDay(nextMonth: string) {
    setMonth(nextMonth)
    const nextBounds = monthBounds(nextMonth)
    const end = selected && nextBounds.end > selected.last_source_date ? selected.last_source_date : nextBounds.end
    const start = selected && nextBounds.start < selected.first_source_date ? selected.first_source_date : nextBounds.start
    setPriceDay(end < start ? start : end)
  }
  function selectMarket(nextZone: string) {
    const market = markets.find((item) => item.bidding_zone === nextZone)
    setZone(nextZone)
    if (market) { setMonth(monthForDate(market.last_source_date)); setPriceDay(market.last_source_date) }
  }

  return <main className="dashboard">
    <header className="page-header"><div><div className="eyebrow">EUROGRID · ELECTRICITY MARKETS</div><h1>Electricity at a glance</h1><p>Choose a country and month to compare electricity use, generation, market prices, and household costs.</p></div><span className="data-source">Energy-Charts · Eurostat</span></header>
    <section className="how-to-read" aria-label="How to read the dashboard"><b>How to read the numbers</b><span><strong>GW</strong> = power needed or produced at one moment. <strong>MWh</strong> = energy over time. <strong>kWh</strong> is the unit used on household electricity bills. 1 MWh = 1,000 kWh.</span><span>The 100% line means reported public-grid load. If reported generation is below it, that is a difference between these reports; imports and other generation can supply the grid. It does not mean Europe is short of electricity.</span></section>
    <section className="controls"><label>COUNTRY OR ALL EUROPE<select value={zone} onChange={(event) => selectMarket(event.target.value)}>{markets.map((market) => <option key={market.bidding_zone} value={market.bidding_zone}>{market.market_name}</option>)}</select></label><div className="month-control"><label htmlFor="month">MONTH</label><div><button aria-label="Previous month" disabled={!firstMonth || month <= firstMonth} onClick={() => setMonthAndDay(shiftMonth(month, -1))}>←</button><input id="month" type="month" min={firstMonth} max={lastMonth} value={month} onChange={(event) => setMonthAndDay(event.target.value)} /><button aria-label="Next month" disabled={!lastMonth || month >= lastMonth} onClick={() => setMonthAndDay(shiftMonth(month, 1))}>→</button><button className="latest-button" disabled={month === latestMonth} onClick={() => setMonthAndDay(latestMonth)}>Latest available</button></div></div><span className="coverage-copy">Data: {firstMonth || '—'} to {lastMonth || '—'}</span></section>
    <p className="period-copy">{zoneName} · {month || 'Loading month'} · times shown in {selected?.source_timezone ?? 'the source timezone'}{selected?.country_code === 'all' ? ' · aligned in UTC' : ''}</p>
    {error && <div className="error">Could not load this view: {error}. Check that the local API and database are running.</div>}{loading && <div className="loading">Loading {zoneName} data…</div>}
    {!loading && data && <>
      <section className="metrics">
        <article><small>AVERAGE WHOLESALE PRICE · {selectedDay}</small><strong>{dayWholesaleEurKwh == null ? 'No price data' : `${euro(dayWholesaleEurKwh / 1000, 3)} / kWh`}</strong><p>{dayWholesaleEurKwh == null ? 'No market price is available for this day.' : `Average for the day · ${euro(dayWholesaleEurKwh)} per MWh. This is the wholesale market, not a home bill.`}</p></article>
        <article><small>POWER NEEDED FROM THE PUBLIC GRID · MONTH AVERAGE</small><strong>{monthlyDemand == null ? '—' : `${fmt(monthlyDemand / 1000, 1)} GW`}</strong><p>Average power the provider says was drawn from the public grid during {month}.</p></article>
        <article><small>{generationSelection === 'all' ? 'REPORTED GENERATION COMPARED WITH GRID LOAD' : `${generationName.toUpperCase()} AS A SHARE OF GRID LOAD`}</small><strong>{coveragePercent == null ? '—' : `${fmt(coveragePercent, 1)}%`}</strong><p>{coveragePercent == null ? 'Not enough matching readings to compare.' : generationSelection === 'all' ? `${coveragePercent < 100 ? `${fmt(100 - coveragePercent, 1)} percentage points below` : `${fmt(coveragePercent - 100, 1)} percentage points above`} the reported load. This gap is not a measured shortfall.` : `This source provides about ${fmt(coveragePercent, 1)}% of the reported public-grid load in matching hours.`}</p></article>
      </section>

      <section className="cost-panel"><div className="cost-heading"><div><span className="eyebrow">A DAILY COST COMPARISON</span><h2>What would this amount of electricity cost?</h2><p>Enter your actual daily use if you know it. The starting value is a country-level average per person, not an assumed household size.</p></div><label className="date-field">PRICE DAY<input type="date" min={effectiveStart} max={effectiveEnd} value={selectedDay} onChange={(event) => setPriceDay(event.target.value)} /></label></div>
        <div className="cost-content"><label className="usage-input">Electricity used that day<input type="number" min="0" step="0.1" placeholder={currentBenchmark?.daily_kwh_per_person == null ? 'e.g. 150' : fmt(currentBenchmark.daily_kwh_per_person, 1)} value={dailyUse} onChange={(event) => { setDailyUse(event.target.value); setUsageEdited(true) }} /><small>{currentBenchmark?.daily_kwh_per_person != null ? `Eurostat ${currentBenchmark.consumption_year} average: ${fmt(currentBenchmark.daily_kwh_per_person, 1)} kWh per person per day. Edit this to your household's total.` : 'Enter the kWh used. Eurostat per-person benchmark is unavailable.'}</small></label>
          <article className="cost-result"><small>WHOLESALE-ONLY COMPARISON</small><strong>{wholesaleCost == null ? '—' : euro(wholesaleCost)}</strong><p>{Number.isFinite(usageValue) && usageValue >= 0 && dayWholesaleEurKwh != null ? `${fmt(usageValue, 1)} kWh × the selected day's average wholesale price. Actual hourly pricing depends on when electricity was used.` : 'Enter daily use and choose a day with price data.'}</p></article>
          <article className="cost-result"><small>EUROSTAT HOUSEHOLD-PRICE BENCHMARK</small><strong>{retailCost == null ? '—' : euro(retailCost)}</strong><p>{currentBenchmark?.retail_eur_per_kwh != null ? `${euro(currentBenchmark.retail_eur_per_kwh, 3)}/kWh · ${currentBenchmark.retail_band} · ${currentBenchmark.retail_tax_level} · ${currentBenchmark.retail_period}. Benchmark only, not your tariff.` : benchmarkErrorGeo === benchmarkGeo ? 'Could not load Eurostat reference prices. Wholesale comparison remains available.' : 'Loading the latest Eurostat household-price reference…'}</p></article>
        </div>
        {selected?.country_code === 'all' && <p className="benchmark-note">For All Europe, household prices and use use the EU-27 benchmark. The wholesale figure above averages the available country-market prices; it is not a single European tariff or market price.</p>}
      </section>

      <section className="charts">
        <article className="chart-card full"><h2>{data.market.price_method === 'market_average' ? 'Average wholesale price across available country markets' : 'Wholesale electricity price'}</h2><p>Each point is the hourly wholesale market price. The dashed line is the stored usual price for that hour. All Europe uses an equally weighted average of available markets; household tariffs are shown separately above.</p><ReactEChartsCore echarts={echarts} option={priceOption} style={{ height: 330 }} notMerge lazyUpdate /></article>
        <article className="chart-card"><h2>Power needed from the public grid</h2><p>Provider-reported load: the power being drawn from the public grid each hour. Values are shown in GW (1 GW = 1,000 MW).</p><ReactEChartsCore echarts={echarts} option={loadOption} style={{ height: 320 }} notMerge lazyUpdate /></article>
        <article className="chart-card"><div className="chart-title"><h2>Power generated as a share of load</h2><select aria-label="Generation source" value={generationSelection} onChange={(event) => setGenerationSelection(event.target.value)}><option value="all">All generation combined</option><option value="renewables">Renewable generation combined</option>{sourceNames.map((name) => <option key={name} value={name}>{displaySource(name)}</option>)}</select></div><p>100% is the reported public-grid load. Choose renewables or one source to see how much of that load it represents. A value below 100% is a reporting gap, not proof of an electricity shortage.</p>{sourceNames.length ? <ReactEChartsCore echarts={echarts} option={generationOption} style={{ height: 320 }} notMerge lazyUpdate /> : <div className="no-chart">No generation records for this month.</div>}</article>
        <article className="chart-card full"><h2>{weatherCity ? `Daily weather in ${weatherCity} beside actual solar and wind generation` : 'Daily weather beside actual solar and wind generation'}</h2><p>Temperature, wind, and solar energy describe the weather at {weatherCity ?? 'one reference location'}; grid output is reported for the whole country. Solar energy reaching the ground is not electricity generation. The output columns show actual reported solar and wind electricity in MWh and as a share of grid load; this comparison does not claim weather caused the output.</p>
          {selected?.is_aggregate && <p className="benchmark-note">All Europe has no single representative weather location. The table still shows reported Europe-wide solar and wind generation; select a country for local weather readings.</p>}
          <div className="table-scroll"><table><thead><tr><th>Day</th><th>High air temperature<br /><small>°C · {weatherCity ?? 'reference location'}</small></th><th>Average wind near ground<br /><small>m/s · {weatherCity ?? 'reference location'}</small></th><th>Solar energy reaching ground<br /><small>kWh/m²/day · {weatherCity ?? 'reference location'}</small></th><th>Grid solar output<br /><small>MWh/day · % of load</small></th><th>Grid wind output<br /><small>MWh/day · % of load</small></th></tr></thead><tbody>{days.map((day) => <tr key={day.day}><td>{day.day}</td><td>{fmt(day.highTemperature, 1)}</td><td>{fmt(day.meanWind, 1)}</td><td>{fmt(day.sunlightKwhM2, 1)}</td><td>{fmt(day.solarMwh, 0)} · {day.solarLoadShare == null ? '—' : `${fmt(day.solarLoadShare, 1)}%`}</td><td>{fmt(day.windMwh, 0)} · {day.windLoadShare == null ? '—' : `${fmt(day.windLoadShare, 1)}%`}</td></tr>)}</tbody></table></div>
          {!selected?.is_aggregate && <p className="table-note">Weather is modelled at one reference point, not measured across the country. Wind speed at this point is context only; wind generation is the separate grid-output figure. Daily MWh totals add up available hourly reports; missing hours are not treated as measured zero generation.</p>}
        </article>
      </section>
      <details className="sources"><summary>Data sources and limitations</summary><p>{Object.values(data.lineage).join(' ')}</p><p>Household price figures are Eurostat national averages for a consumption band, not a personal contract. The wholesale comparison uses the day's average price and does not model when during the day electricity was used. Blank source readings are missing, not zero.</p></details>
    </>}
    <footer>EUROGRID INTELLIGENCE · LOCAL RESEARCH DASHBOARD</footer>
  </main>
}
