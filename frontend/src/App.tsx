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
type Market = { country_code: string; bidding_zone: string; market_name: string; is_aggregate: boolean; has_prices: boolean; price_method?: string; price_market_count?: number; first_source_date: string; last_source_date: string; source_timezone: string; power_timezone?: string }
type Point = Record<string, string | number | string[] | null>
type Benchmark = { geography: string; retail_eur_per_kwh: number | null; retail_period: string | null; retail_band: string; retail_tax_level: string; sources: Record<string, string> }
type Overview = { market: Market; prices: Point[]; generation: Point[]; demand: Point[]; cross_border_flows: Point[]; weather: Point[]; lineage: Record<string, string> }
type Hour = { key: string; label: string; price: number | null; baseline: number | null; demand: number | null; generation: number | null; renewables: number | null; sources: Record<string, number>; temperature: number | null; windSpeed: number | null; solarRadiation: number | null }
type DaySummary = { day: string; highTemperature: number | null; meanWind: number | null; solarRadiationKwhM2: number | null; solarMwh: number | null; solarLoadShare: number | null; windMwh: number | null; windLoadShare: number | null }
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
function displayEurostatPeriod(period: string | null | undefined) {
  const match = period?.match(/^(\d{4})-S([12])$/)
  return match ? `${match[1]} · ${match[2] === '1' ? 'Jan–Jun' : 'Jul–Dec'}` : period ?? 'Eurostat'
}
function dateInTimezone(instant: string, timezone: string) { return new Intl.DateTimeFormat('en-CA', { timeZone: timezone, year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date(instant)) }
function addDays(date: string, days: number) { const value = new Date(`${date}T00:00:00Z`); value.setUTCDate(value.getUTCDate() + days); return value.toISOString().slice(0, 10) }
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
  const [weatherView, setWeatherView] = useState<'day' | 'week' | 'month'>('week')
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
        setBenchmarkErrorGeo('')
      }
    }).catch(() => { if (active) setBenchmarkErrorGeo(benchmarkGeo) })
    return () => { active = false }
  }, [benchmarkGeo])

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
    const buckets = new Map<string, { label: string; price: number[]; baseline: number[]; demand: number[]; source: Record<string, number[]>; invalidSources: Set<string>; weather: Record<string, number[]> }>()
    const ensure = (row: Point) => {
      const instant = Date.parse(String(row.interval_start_utc ?? ''))
      if (!Number.isFinite(instant)) return null
      const key = new Date(Math.floor(instant / 3600000) * 3600000).toISOString()
      if (!buckets.has(key)) buckets.set(key, { label: '', price: [], baseline: [], demand: [], source: {}, invalidSources: new Set(), weather: {} })
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
      if (!bucket) return
      if (row.data_status === 'valid' && value != null) (bucket.source[source] ??= []).push(value)
      else bucket.invalidSources.add(source)
    })
    data.weather.forEach((row) => {
      const bucket = ensure(row); const value = numeric(row.weather_value); const variable = String(row.variable)
      if (bucket && row.data_status === 'valid' && value != null) (bucket.weather[variable] ??= []).push(value)
    })
    const average = (values: number[]) => values.length ? values.reduce((a, b) => a + b, 0) / values.length : null
    const sourceTypes = new Set(data.generation.map((row) => String(row.production_type)))
    return [...buckets.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([key, item]) => {
      const sources = Object.fromEntries(Object.entries(item.source).filter(([name]) => !item.invalidSources.has(name)).map(([name, values]) => [name, average(values)!]))
      const weather = Object.fromEntries(Object.entries(item.weather).map(([name, values]) => [name, average(values)!]))
      const generationComplete = sourceTypes.size > 0 && [...sourceTypes].every((name) => sources[name] != null && !item.invalidSources.has(name))
      const renewableNames = [...sourceTypes].filter((name) => renewableTypes.has(name))
      const renewablesComplete = renewableNames.length > 0 && renewableNames.every((name) => sources[name] != null && !item.invalidSources.has(name))
      const generation = generationComplete ? Object.values(sources).reduce((sum, value) => sum + value, 0) : null
      const renewables = renewablesComplete ? renewableNames.reduce((sum, name) => sum + sources[name], 0) : null
      return { key, label: item.label, price: average(item.price), baseline: average(item.baseline), demand: average(item.demand), generation, renewables, sources, temperature: weather.temperature, windSpeed: weather.wind_speed, solarRadiation: weather.shortwave_radiation }
    })
  }, [data])

  const mean = (values: number[]) => values.length ? values.reduce((a, b) => a + b, 0) / values.length : null
  const timezone = data?.market.source_timezone || 'UTC'
  const powerTimezone = data?.market.power_timezone || timezone
  const selectedDay = priceDay >= (effectiveStart ?? '') && priceDay <= (effectiveEnd ?? '') ? priceDay : effectiveEnd ?? ''
  const monthlyWholesaleEurMwh = mean(hourly.flatMap((row) => row.price == null ? [] : [row.price]))
  const usualWholesaleEurMwh = mean(hourly.flatMap((row) => row.baseline == null ? [] : [row.baseline]))
  const priceVsUsualPercent = monthlyWholesaleEurMwh != null && usualWholesaleEurMwh != null && usualWholesaleEurMwh !== 0
    ? (monthlyWholesaleEurMwh - usualWholesaleEurMwh) / Math.abs(usualWholesaleEurMwh) * 100
    : null
  const monthlyDemand = mean(hourly.flatMap((row) => row.demand == null ? [] : [row.demand]))
  // Each point is an hourly average in MW, so summing its values gives MWh
  // across observed hours. Missing hours stay out of the total.
  const reportedGridUseMwh = hourly.reduce((sum, row) => sum + (row.demand ?? 0), 0)
  const reportedGridUseTwh = reportedGridUseMwh / 1_000_000
  const sourceNames = useMemo(() => [...new Set((data?.generation ?? []).map((row) => String(row.production_type)))].sort(), [data])
  const priceMarketNames = [...new Set(data?.prices.flatMap((row) => Array.isArray(row.market_names) ? row.market_names.map(String) : []) ?? [])].sort()
  const windSourceNames = useMemo(() => sourceNames.filter((name) => name.startsWith('wind_')), [sourceNames])
  const selectedOutput = (row: Hour) => generationSelection === 'all'
    ? row.generation
    : generationSelection === 'renewables' ? row.renewables : row.sources[generationSelection] ?? null
  const generationName = generationSelection === 'all' ? 'All reported generation' : generationSelection === 'renewables' ? 'Renewable generation' : displaySource(generationSelection)
  const comparisonRows = hourly.filter((row) => row.demand != null && row.demand > 0 && selectedOutput(row) != null)
  const hasPriceData = hourly.some((row) => row.price != null)
  const hasGridUseData = hourly.some((row) => row.demand != null)
  const hasSelectedGenerationData = comparisonRows.length > 0
  const coveragePercent = mean(comparisonRows.map((row) => selectedOutput(row)! / row.demand! * 100))
  const generationLabels = hourly.map((row) => row.label)
  const generationOption = {
    ...chartBase('% of reported public-grid use', generationLabels),
    yAxis: { type: 'value', min: 0, max: (range: { max: number }) => Math.max(110, Math.ceil(range.max / 10) * 10), name: '% of reported public-grid use', nameLocation: 'middle', nameGap: 54 },
    series: [{ name: `${generationName} · % of public-grid use`, type: 'line', data: hourly.map((row) => row.demand && selectedOutput(row) != null ? selectedOutput(row)! / row.demand * 100 : null), showSymbol: false, lineStyle: { color: '#6ee0a5', width: 2 }, markLine: { silent: true, symbol: 'none', data: [{ yAxis: 100, label: { formatter: '100% = same reported amount' }, lineStyle: { color: '#efbd68', type: 'dashed', width: 2 } }] } }],
  } satisfies EChartsOption
  const loadOption = { ...chartBase('GW · power drawn from public grid', generationLabels), series: [{ name: 'Power drawn from public grid', type: 'line', data: hourly.map((row) => row.demand == null ? null : row.demand / 1000), showSymbol: false, lineStyle: { color: '#87adff', width: 2 }, areaStyle: { opacity: .08 } }] } satisfies EChartsOption
  const priceOption = { ...chartBase('€ per kWh · wholesale', hourly.map((row) => row.label)), series: [{ name: 'Wholesale price', type: 'line', data: hourly.map((row) => row.price == null ? null : row.price / 1000), showSymbol: false, lineStyle: { color: '#6ee0a5', width: 2 } }, { name: 'Usual price for this hour', type: 'line', data: hourly.map((row) => row.baseline == null ? null : row.baseline / 1000), showSymbol: false, lineStyle: { color: '#f2b66d', type: 'dashed' } }] } satisfies EChartsOption

  const days = useMemo<DaySummary[]>(() => {
    const daily = new Map<string, { temps: number[]; winds: number[]; radiation: number[]; solarMwh: number; solarLoadMwh: number; solarHours: number; windMwh: number; windLoadMwh: number; windHours: number }>()
    for (const hour of hourly) {
      const day = dateInTimezone(hour.key, powerTimezone)
      const summary = daily.get(day) ?? { temps: [], winds: [], radiation: [], solarMwh: 0, solarLoadMwh: 0, solarHours: 0, windMwh: 0, windLoadMwh: 0, windHours: 0 }
      if (hour.temperature != null) summary.temps.push(hour.temperature)
      if (hour.windSpeed != null) summary.winds.push(hour.windSpeed)
      if (hour.solarRadiation != null) summary.radiation.push(hour.solarRadiation)
      if (hour.sources.solar != null && hour.demand != null) {
        summary.solarMwh += hour.sources.solar
        summary.solarLoadMwh += hour.demand
        summary.solarHours += 1
      }
      const windIsComplete = windSourceNames.length > 0 && windSourceNames.every((source) => hour.sources[source] != null)
      if (windIsComplete && hour.demand != null) {
        summary.windMwh += windSourceNames.reduce((sum, source) => sum + hour.sources[source], 0)
        summary.windLoadMwh += hour.demand
        summary.windHours += 1
      }
      daily.set(day, summary)
    }
    return [...daily.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([day, value]) => ({
      day,
      highTemperature: value.temps.length ? Math.max(...value.temps) : null,
      meanWind: mean(value.winds),
      solarRadiationKwhM2: value.radiation.length ? value.radiation.reduce((a, b) => a + b, 0) / 1000 : null,
      solarMwh: value.solarHours ? value.solarMwh : null,
      solarLoadShare: value.solarLoadMwh > 0 ? value.solarMwh / value.solarLoadMwh * 100 : null,
      windMwh: value.windHours ? value.windMwh : null,
      windLoadShare: value.windLoadMwh > 0 ? value.windMwh / value.windLoadMwh * 100 : null,
    }))
  }, [hourly, powerTimezone, windSourceNames])

  const weekOffset = selectedDay ? (new Date(`${selectedDay}T00:00:00Z`).getUTCDay() + 6) % 7 : 0
  const calculatedWeekStart = selectedDay ? addDays(selectedDay, -weekOffset) : ''
  const visibleStart = weatherView === 'month' ? effectiveStart ?? '' : calculatedWeekStart < (effectiveStart ?? '') ? effectiveStart ?? '' : calculatedWeekStart
  const calculatedWeekEnd = calculatedWeekStart ? addDays(calculatedWeekStart, 6) : ''
  const visibleEnd = weatherView === 'month' ? effectiveEnd ?? '' : weatherView === 'day' ? selectedDay : calculatedWeekEnd > (effectiveEnd ?? '') ? effectiveEnd ?? '' : calculatedWeekEnd
  const visibleDays = days.filter((day) => day.day >= visibleStart && day.day <= visibleEnd)
  const visibleHours = weatherView === 'day' ? hourly.filter((hour) => dateInTimezone(hour.key, powerTimezone) === selectedDay) : []
  const hasWeatherInView = weatherView === 'day'
    ? visibleHours.some((hour) => hour.temperature != null || hour.windSpeed != null || hour.solarRadiation != null)
    : visibleDays.some((day) => day.highTemperature != null || day.meanWind != null || day.solarRadiationKwhM2 != null)
  const hasWeatherTableRows = weatherView === 'day' ? visibleHours.length > 0 : visibleDays.length > 0
  const weatherRows = weatherView === 'day'
    ? visibleHours.map((hour) => {
      const solar = hour.sources.solar
      const wind = windSourceNames.length > 0 && windSourceNames.every((source) => hour.sources[source] != null)
        ? windSourceNames.reduce((sum, source) => sum + hour.sources[source], 0)
        : null
      return <tr key={hour.key}><td>{hour.label.slice(11, 16)}</td><td>{fmt(hour.temperature, 1)}</td><td>{fmt(hour.windSpeed, 1)}</td><td>{fmt(hour.solarRadiation, 0)}</td><td>{solar == null ? '—' : `${fmt(solar / 1000, 1)} GW · ${hour.demand ? `${fmt(solar / hour.demand * 100, 1)}%` : '—'}`}</td><td>{wind == null ? '—' : `${fmt(wind / 1000, 1)} GW · ${hour.demand ? `${fmt(wind / hour.demand * 100, 1)}%` : '—'}`}</td></tr>
    })
    : visibleDays.map((day) => <tr key={day.day}><td>{day.day}</td><td>{fmt(day.highTemperature, 1)}</td><td>{fmt(day.meanWind, 1)}</td><td>{fmt(day.solarRadiationKwhM2, 1)}</td><td>{day.solarMwh == null ? '—' : `${fmt(day.solarMwh, 0)} · ${day.solarLoadShare == null ? '—' : `${fmt(day.solarLoadShare, 1)}%`}`}</td><td>{day.windMwh == null ? '—' : `${fmt(day.windMwh, 0)} · ${day.windLoadShare == null ? '—' : `${fmt(day.windLoadShare, 1)}%`}`}</td></tr>)
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
    <header className="page-header"><div><div className="eyebrow">EUROGRID · ELECTRICITY MARKETS</div><h1>Electricity at a glance</h1><p>Choose a country and month to compare grid electricity, generation, wholesale prices, and household-price references.</p></div><span className="data-source">Energy-Charts · Eurostat</span></header>
    <section className="how-to-read" aria-label="How to read the dashboard"><b>How to read the numbers</b><span><strong>GW</strong> = power at one moment. <strong>MWh</strong> = energy over an hour or another period. <strong>kWh</strong> is the unit on household bills. 1 MWh = 1,000 kWh; 1 TWh = 1 billion kWh.</span><span>On the generation chart, 100% means reported generation matches the selected area's reported public-grid electricity use for the same hour. It compares these two provider reports, not a complete system balance.</span></section>
    <section className="controls"><label>COUNTRY OR ALL EUROPE<select value={zone} onChange={(event) => selectMarket(event.target.value)}>{markets.map((market) => <option key={market.bidding_zone} value={market.bidding_zone}>{market.market_name}</option>)}</select></label><div className="month-control"><label htmlFor="month">MONTH</label><div><button aria-label="Previous month" disabled={!firstMonth || month <= firstMonth} onClick={() => setMonthAndDay(shiftMonth(month, -1))}>←</button><input id="month" type="month" min={firstMonth} max={lastMonth} value={month} onChange={(event) => setMonthAndDay(event.target.value)} /><button aria-label="Next month" disabled={!lastMonth || month >= lastMonth} onClick={() => setMonthAndDay(shiftMonth(month, 1))}>→</button><button className="latest-button" disabled={month === latestMonth} onClick={() => setMonthAndDay(latestMonth)}>Latest available</button></div></div><span className="coverage-copy">Data: {firstMonth || '—'} to {lastMonth || '—'}</span>
      {selected?.country_code === 'all' && <p className="coverage-explainer"><strong>What “All Europe” means:</strong> power use and generation use Energy-Charts’ Europe aggregate reports, not a sum of every country chart. The provider dataset does not list every country included in that aggregate, so we cannot confirm it covers every European country. Wholesale prices are an equal-weight average of the country markets with data this month{priceMarketNames.length ? `: ${priceMarketNames.join(', ')}` : ''}. The household price reference is for the EU-27.</p>}
      {selected && selected.country_code !== 'all' && <p className="coverage-explainer">This country can have different date coverage for prices, grid use, generation, and weather. A chart will say when its data is unavailable for {month}.</p>}
    </section>
    <p className="period-copy">{zoneName} · {month || 'Loading month'} · times shown in {selected?.source_timezone ?? 'the source timezone'}{selected?.country_code === 'all' ? ' · aligned in UTC' : ''}</p>
    {error && <div className="error">Could not load this view: {error}. Check that the local API and database are running.</div>}{loading && <div className="loading">Loading {zoneName} data…</div>}
    {!loading && data && <>
      <section className="metrics">
        <article><small>WHOLESALE PRICE · {zoneName} · {month}</small><strong>{monthlyWholesaleEurMwh == null ? 'No price data' : `${euro(monthlyWholesaleEurMwh / 1000, 3)} / kWh`}</strong><p>{monthlyWholesaleEurMwh == null ? `No wholesale market prices are available for ${zoneName} in ${month}.` : `Average market price for each kWh of electricity, before household delivery costs and taxes.`}</p>{priceVsUsualPercent != null && <p className="metric-secondary">Compared with the usual price for those hours in stored history, this month was {fmt(Math.abs(priceVsUsualPercent), 1)}% {priceVsUsualPercent >= 0 ? 'higher' : 'lower'}. This is a historical comparison, not a forecast.</p>}{data.market.price_method === 'market_average' && priceMarketNames.length > 0 && <p className="metric-secondary">For All Europe, this month’s wholesale average uses {priceMarketNames.length} markets: {priceMarketNames.join(', ')}.</p>}</article>
        <article><small>HOUSEHOLD PRICE REFERENCE · {displayEurostatPeriod(currentBenchmark?.retail_period)}</small><strong>{currentBenchmark?.retail_eur_per_kwh == null ? '—' : `${euro(currentBenchmark.retail_eur_per_kwh, 3)} / kWh`}</strong><p>{currentBenchmark?.retail_eur_per_kwh != null ? `Eurostat household electricity price for ${benchmarkGeo === 'EU27_2020' ? 'the EU-27' : zoneName}. The band ${currentBenchmark.retail_band} means households using that much electricity in a year; the number above is the price of one kWh. It includes taxes and levies, unlike the wholesale price. It is a national reference, not a personal bill.` : benchmarkErrorGeo === benchmarkGeo ? `Eurostat has no household-price reference for ${benchmarkGeo === 'EU27_2020' ? 'the EU-27' : zoneName}. The wholesale series remains available.` : 'Loading the latest Eurostat household-price reference…'}</p></article>
        <article><small>GRID ELECTRICITY USE · {zoneName} · {month}</small><strong>{monthlyDemand == null ? 'No grid-use data' : `${fmt(monthlyDemand / 1000, 1)} GW average`}</strong><p>{monthlyDemand == null ? `No public-grid use readings are available for ${zoneName} in ${month}.` : `At a typical moment in ${month}, the public grid supplied about ${fmt(monthlyDemand / 1000, 1)} GW to users across ${zoneName}. This is the whole area, not one home.`}</p><p className="metric-secondary">Recorded electricity used this month: {fmt(reportedGridUseTwh, 2)} TWh ({fmt(reportedGridUseTwh, 2)} billion kWh). Missing hours are not included in this total.</p></article>
        <article><small>GENERATION COMPARED WITH GRID USE · {zoneName} · {month}</small><strong>{coveragePercent == null ? 'No comparison data' : `${fmt(coveragePercent, 1)}% of grid use`}</strong><p>{coveragePercent == null ? `No hours have both generation and grid-use readings for ${zoneName} in ${month}.` : `Across hours with both readings, ${generationName.toLowerCase()} was ${fmt(coveragePercent, 1)}% of the electricity used from this area’s public grid. 100% means the reported amounts matched; above 100% means reported generation was higher. It does not prove a whole-system surplus.`}</p></article>
      </section>

      <section className="charts">
        <article className="chart-card full"><h2>{data.market.price_method === 'market_average' ? 'Average wholesale price from available country markets' : 'Wholesale electricity price'}</h2><p>Each point is the market price for one kWh at that hour. The dashed line shows the usual market price for the same hour. Household prices include extra charges, so the two prices are useful context but are not the same kind of price.</p>{hasPriceData ? <ReactEChartsCore echarts={echarts} option={priceOption} style={{ height: 330 }} notMerge lazyUpdate /> : <div className="no-chart">No wholesale price data for {zoneName} in {month}.</div>}</article>
        <article className="chart-card"><h2>Power needed from the public grid</h2><p>Total for the selected area at each hour: power supplied to homes, businesses, and industry. The vertical scale is GW (1 GW = 1,000 MW).</p>{hasGridUseData ? <ReactEChartsCore echarts={echarts} option={loadOption} style={{ height: 320 }} notMerge lazyUpdate /> : <div className="no-chart">No grid-use data for {zoneName} in {month}.</div>}</article>
        <article className="chart-card"><div className="chart-title"><h2>Power generated compared with grid use</h2><select aria-label="Generation source" value={generationSelection} onChange={(event) => setGenerationSelection(event.target.value)}><option value="all">All generation combined</option><option value="renewables">Renewable generation combined</option>{sourceNames.map((name) => <option key={name} value={name}>{displaySource(name)}</option>)}</select></div><p>{zoneName} · each point is one hour. The vertical scale is the selected source’s generation as a percentage of this area’s grid electricity use for the same hour. At 100%, reported generation and grid use are equal.</p>{hasSelectedGenerationData ? <ReactEChartsCore echarts={echarts} option={generationOption} style={{ height: 320 }} notMerge lazyUpdate /> : <div className="no-chart">No matching generation and grid-use data for {zoneName} in {month}.</div>}</article>
        <article className="chart-card full">
          <div className="chart-title">
            <div>
              <h2>{weatherCity ? `Weather in ${weatherCity} and reported solar/wind generation` : 'Reported solar/wind generation and local weather'}</h2>
              <p>{weatherCity ? `Weather is modelled for ${weatherCity}; solar and wind electricity are country-level provider reports for ${zoneName}.` : `No single weather location represents ${zoneName}; country-level solar and wind reports are still shown.`} Weather describes conditions; generation is measured separately.</p>
            </div>
            <div className="weather-controls">
              <label>SHOW
                <select aria-label="Weather and generation period" value={weatherView} onChange={(event) => setWeatherView(event.target.value as 'day' | 'week' | 'month')}>
                  <option value="day">One day · hourly detail</option>
                  <option value="week">One week · daily summary</option>
                  <option value="month">Whole month · daily summary</option>
                </select>
              </label>
              {weatherView !== 'month' && <label>DATE
                <input aria-label="Weather date" type="date" min={effectiveStart} max={effectiveEnd} value={selectedDay} onChange={(event) => setPriceDay(event.target.value)} />
              </label>}
            </div>
          </div>
          {selected?.is_aggregate && <p className="benchmark-note">All Europe has no single representative weather location. The table still shows reported Europe-wide solar and wind generation; select a country for local weather readings.</p>}
          {!hasWeatherInView && !selected?.is_aggregate && <p className="benchmark-note">No weather readings are available for {weatherCity ?? zoneName} in this selected period; generation data may still be available.</p>}
          <p className="period-copy">{weatherView === 'day' ? `${selectedDay} · each row is one hour` : weatherView === 'week' ? `${visibleStart} to ${visibleEnd} · daily totals` : `${month} · daily totals`} · dates follow {powerTimezone}</p>
          <div className="table-scroll"><table><thead>{weatherView === 'day' ? <tr><th>Hour<br /><small>{timezone}</small></th><th>Temperature<br /><small>°C · {weatherCity ?? 'weather unavailable'}</small></th><th>Wind speed<br /><small>m/s · {weatherCity ?? 'weather unavailable'}</small></th><th>Sunlight reaching ground<br /><small>W/m² · hourly average at {weatherCity ?? 'weather point'}</small></th><th>Solar electricity<br /><small>GW · % of grid use</small></th><th>Wind electricity<br /><small>GW · % of grid use</small></th></tr> : <tr><th>Day</th><th>Temperature (daily high)<br /><small>°C · {weatherCity ?? 'weather unavailable'}</small></th><th>Average wind speed<br /><small>m/s · {weatherCity ?? 'weather unavailable'}</small></th><th>Sunlight reaching ground<br /><small>kWh/m²/day · {weatherCity ?? 'weather unavailable'}</small></th><th>Solar electricity<br /><small>MWh · % of grid use for matching hours</small></th><th>Wind electricity<br /><small>MWh · % of grid use for matching hours</small></th></tr>}</thead><tbody>{weatherRows}{!hasWeatherTableRows && <tr><td colSpan={6}>No weather or generation records for this selected period.</td></tr>}</tbody></table></div>
          <p className="table-note">“Sunlight reaching ground” is weather information for one square metre; it is not electricity generated. Solar and wind percentages compare reported generation with electricity used from the public grid for the same hours. MWh totals include only hours with both readings. — means a source did not report a value for that time. Weather is historical modelled data at one reference location.</p>
        </article>
      </section>
      <details className="sources"><summary>Data sources and limitations</summary><p>{Object.values(data.lineage).join(' ')}</p><p>The household-price reference is Eurostat's latest reported national average for a stated consumption band and period; it is not a personal tariff. The wholesale value is an observed market average and excludes household delivery charges and taxes. Public generation and grid-use series are not a complete system balance. Blank source readings are missing, not zero.</p></details>
    </>}
    <footer>EUROGRID INTELLIGENCE · LOCAL RESEARCH DASHBOARD</footer>
  </main>
}
