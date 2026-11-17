import { useEffect, useMemo, useState } from 'react'
import ReactEChartsCore from 'echarts-for-react/esm/core'
import * as echarts from 'echarts/core'
import { LineChart } from 'echarts/charts'
import { DataZoomComponent, GridComponent, LegendScrollComponent, MarkLineComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import type { EChartsOption } from 'echarts'
import './styles.css'

// Register only the ECharts features used here to keep the local dashboard lean.
echarts.use([LineChart, DataZoomComponent, GridComponent, LegendScrollComponent, MarkLineComponent, TooltipComponent, CanvasRenderer])

const API = import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000'

type Market = {
  country_code: string
  bidding_zone: string
  first_source_date: string
  last_source_date: string
  source_timezone: string
  price_interval_count: number
  valid_price_interval_count: number
  flagged_price_interval_count: number
  provider: string
  dataset: string
}

type Point = Record<string, string | number | null>
type HourRow = {
  key: string
  time: string
  price: number | null
  demand: number | null
  generation: number | null
  renewableShare: number | null
  weather: number | null
}
type Overview = {
  market: Market
  window: { start_date: string; end_date: string; expected_hourly_intervals: number }
  prices: Point[]
  generation: Point[]
  demand: Point[]
  cross_border_flows: Point[]
  weather: Point[]
  quality: Record<string, number>
  lineage: Record<string, string>
  source_trace: { measure: string; model: string; provider: string; dataset: string; raw_sha256: string; record_count: number }[]
}

const weatherLabels: Record<string, string> = {
  temperature: 'Air temperature',
  wind_speed: 'Wind speed',
  shortwave_radiation: 'Sunlight reaching the ground',
}
const weatherDescriptions: Record<string, string> = {
  temperature: 'Air temperature at this reference location.',
  wind_speed: 'Wind speed at this reference location; metres per second (m/s).',
  shortwave_radiation: 'Sunlight energy arriving at each square metre; watts per square metre (W/m²).',
}

async function getJson<T>(url: string): Promise<T> {
  const response = await fetch(url)
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}))
    throw new Error(payload.detail ?? `Request failed (${response.status})`)
  }
  return response.json() as Promise<T>
}

function numeric(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function formatNumber(value: number | null | undefined, digits = 1) {
  return value == null ? '—' : new Intl.NumberFormat('en-GB', { maximumFractionDigits: digits }).format(value)
}

function localLabel(row: Point) {
  const original = String(row.original_timestamp ?? '')
  return original.replace('T', ' ').slice(0, 16)
}

function axisLabel(row: Point, singleDay: boolean) {
  const original = String(row.original_timestamp ?? '')
  return singleDay ? original.slice(11, 16) : original.replace('T', ' ').slice(0, 16)
}

function chartBase(unit: string, labels: string[]): EChartsOption {
  return {
    animation: false,
    grid: { left: 64, right: 22, top: 42, bottom: 58, containLabel: true },
    tooltip: { trigger: 'axis', confine: true, backgroundColor: '#0d1918', borderColor: '#49625a', textStyle: { color: '#eff5ef' } },
    legend: { top: 2, right: 8, textStyle: { color: '#bdc9c2' }, itemWidth: 14, itemHeight: 3, pageTextStyle: { color: '#bdc9c2' } },
    xAxis: { type: 'category', data: labels, boundaryGap: false, axisLabel: { color: '#aab9b1', hideOverlap: true, margin: 12 }, axisLine: { lineStyle: { color: '#42554e' } }, axisTick: { show: false } },
    yAxis: { type: 'value', name: unit, nameTextStyle: { color: '#aab9b1', padding: [0, 0, 0, 8] }, axisLabel: { color: '#aab9b1' }, splitLine: { lineStyle: { color: '#293a35' } } },
    dataZoom: [{ type: 'inside' }, { type: 'slider', height: 14, bottom: 5, borderColor: '#344a42', fillerColor: 'rgba(103, 202, 143, .16)', textStyle: { color: '#aab9b1' } }],
  }
}

export default function App() {
  const [markets, setMarkets] = useState<Market[]>([])
  const [zone, setZone] = useState('')
  const [start, setStart] = useState('')
  const [end, setEnd] = useState('')
  const [data, setData] = useState<Overview | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [weatherVariable, setWeatherVariable] = useState('temperature')

  useEffect(() => {
    getJson<{ markets: Market[] }>(`${API}/api/coverage`)
      .then(({ markets: available }) => {
        setMarkets(available)
        const first = available.find((item) => item.bidding_zone === 'DE-LU') ?? available[0]
        if (first) {
          setZone(first.bidding_zone)
          // Start with one day so the first chart reads like an hourly day view.
          setStart(first.last_source_date)
          setEnd(first.last_source_date)
        }
      })
      .catch((reason: Error) => setError(reason.message))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    if (!zone || !start || !end) return
    const params = new URLSearchParams({ bidding_zone: zone, start_date: start, end_date: end })
    setLoading(true)
    setError('')
    getJson<Overview>(`${API}/api/overview?${params}`)
      .then(setData)
      .catch((reason: Error) => { setData(null); setError(reason.message) })
      .finally(() => setLoading(false))
  }, [zone, start, end])

  const selectedMarket = markets.find((market) => market.bidding_zone === zone)
  const singleDay = start === end
  const dateRangeLabel = singleDay ? start : `${start} to ${end}`
  const priceLabels = useMemo(() => data?.prices.map((row) => axisLabel(row, singleDay)) ?? [], [data, singleDay])

  const hourlyRows = useMemo<HourRow[]>(() => {
    if (!data) return []
    const generationByTime = new Map<string, { total: number; renewables: number }>()
    const demandByTime = new Map<string, number>()
    const weatherByTime = new Map<string, number>()
    const renewableTypes = new Set(['wind_onshore', 'wind_offshore', 'solar', 'hydro_run_of_river', 'hydro_water_reservoir', 'biomass', 'geothermal', 'other_renewables'])

    for (const row of data.generation) {
      const value = numeric(row.generation_mw)
      if (row.data_status !== 'valid' || value == null) continue
      const key = String(row.interval_start_utc)
      const bucket = generationByTime.get(key) ?? { total: 0, renewables: 0 }
      bucket.total += value
      if (renewableTypes.has(String(row.production_type))) bucket.renewables += value
      generationByTime.set(key, bucket)
    }
    for (const row of data.demand) {
      const value = numeric(row.demand_mw)
      if (row.data_status === 'valid' && value != null) demandByTime.set(String(row.interval_start_utc), value)
    }
    for (const row of data.weather) {
      const value = numeric(row.weather_value)
      if (row.variable === weatherVariable && row.data_status === 'valid' && value != null) weatherByTime.set(String(row.interval_start_utc), value)
    }

    return data.prices.map((price) => {
      const key = String(price.interval_start_utc)
      const generation = generationByTime.get(key)
      return {
        key,
        time: localLabel(price),
        price: numeric(price.price_eur_per_mwh),
        demand: demandByTime.get(key) ?? null,
        generation: generation?.total ?? null,
        renewableShare: generation && generation.total > 0 ? generation.renewables / generation.total * 100 : null,
        weather: weatherByTime.get(key) ?? null,
      }
    })
  }, [data, weatherVariable])

  const comparisonRows = hourlyRows.filter((row) => row.demand != null && row.generation != null)
  const averageDemand = comparisonRows.length ? comparisonRows.reduce((sum, row) => sum + row.demand!, 0) / comparisonRows.length : null
  const averageGeneration = comparisonRows.length ? comparisonRows.reduce((sum, row) => sum + row.generation!, 0) / comparisonRows.length : null
  const demandShare = averageDemand != null && averageGeneration != null && averageGeneration > 0
    ? averageDemand / averageGeneration * 100
    : null
  const renewableRows = hourlyRows.filter((row) => row.renewableShare != null)
  const averageRenewableShare = renewableRows.length
    ? renewableRows.reduce((sum, row) => sum + row.renewableShare!, 0) / renewableRows.length
    : null

  const priceOption = useMemo(() => {
    if (!data) return {}
    const option = chartBase('€/MWh', priceLabels)
    return {
      ...option,
      series: [
        { name: 'Hourly wholesale price', type: 'line', data: data.prices.map((row) => row.price_eur_per_mwh), showSymbol: true, symbolSize: 5, connectNulls: false, lineStyle: { color: '#6ee0a5', width: 2 }, itemStyle: { color: '#6ee0a5' } },
        { name: 'Usual price at this hour', type: 'line', data: data.prices.map((row) => row.mean_price_eur_per_mwh), showSymbol: false, connectNulls: true, lineStyle: { color: '#f2b66d', width: 1.5, type: 'dashed' }, itemStyle: { color: '#f2b66d' } },
      ],
    } satisfies EChartsOption
  }, [data, priceLabels])

  const generationOption = useMemo(() => {
    if (!data) return {}
    const times = [...new Set(data.generation.map((row) => String(row.interval_start_utc)))].sort()
    const seriesNames = [...new Set(data.generation.map((row) => String(row.production_type)))].sort()
    const displayNames = seriesNames.map((name) => name.replaceAll('_', ' '))
    const byTypeTime = new Map(data.generation.map((row) => [`${row.production_type}|${row.interval_start_utc}`, row.generation_mw]))
    const labelsByTime = new Map(data.generation.map((row) => [String(row.interval_start_utc), axisLabel(row, singleDay)]))
    const option = chartBase('MW (power produced)', times.map((time) => labelsByTime.get(time) ?? time))
    return {
      ...option,
      legend: { ...(option.legend as object), data: displayNames, type: 'scroll', left: 8, right: 8 },
      series: seriesNames.map((name) => ({
        name: name.replaceAll('_', ' '),
        type: 'line',
        stack: 'generation',
        areaStyle: { opacity: 0.18 },
        showSymbol: false,
        connectNulls: false,
        data: times.map((time) => byTypeTime.get(`${name}|${time}`) ?? null),
      })),
    } satisfies EChartsOption
  }, [data, singleDay])

  const renewableOption = useMemo(() => {
    const option = chartBase('% of recorded generation', hourlyRows.map((row) => row.time.slice(11, 16)))
    return {
      ...option,
      yAxis: { ...(option.yAxis as object), min: 0, max: 100 },
      series: [{ name: 'Renewable share', type: 'line', data: hourlyRows.map((row) => row.renewableShare), showSymbol: false, connectNulls: false, lineStyle: { color: '#e3c56e', width: 2 }, areaStyle: { color: 'rgba(227, 197, 110, .10)' } }],
    } satisfies EChartsOption
  }, [hourlyRows])

  const demandOption = useMemo(() => {
    const option = chartBase('MW (power needed)', hourlyRows.map((row) => row.time.slice(11, 16)))
    return { ...option, series: [{ name: 'Reported demand', type: 'line', data: hourlyRows.map((row) => row.demand), showSymbol: true, symbolSize: 4, connectNulls: false, lineStyle: { color: '#87adff', width: 2 }, areaStyle: { color: 'rgba(92, 134, 228, .10)' } }] } satisfies EChartsOption
  }, [hourlyRows])

  const flowOption = useMemo(() => {
    const option = chartBase('MW (scheduled power flow)', data?.cross_border_flows.map((row) => axisLabel(row, singleDay)) ?? [])
    return { ...option, series: [{ name: 'Net cross-border flow', type: 'line', data: data?.cross_border_flows.map((row) => row.net_cross_border_trading_mw) ?? [], showSymbol: false, connectNulls: false, lineStyle: { color: '#c68cff', width: 2 }, areaStyle: { color: 'rgba(198, 140, 255, .10)' }, markLine: { silent: true, symbol: 'none', data: [{ yAxis: 0 }], lineStyle: { color: '#879790', type: 'dashed' } } }] } satisfies EChartsOption
  }, [data, singleDay])

  const weatherRows = useMemo(() => data?.weather.filter((row) => row.variable === weatherVariable && row.data_status === 'valid') ?? [], [data, weatherVariable])
  const weatherUnit = String(weatherRows[0]?.weather_unit ?? '')
  const weatherOption = useMemo(() => {
    const localTimeByInterval = new Map(hourlyRows.map((row) => [row.key, row.time]))
    const labels = weatherRows.map((row) => {
      const localTime = localTimeByInterval.get(String(row.interval_start_utc)) ?? axisLabel(row, singleDay)
      return singleDay ? localTime.slice(11, 16) : localTime
    })
    const option = chartBase(weatherUnit || 'weather reading', labels)
    return { ...option, series: [{ name: weatherLabels[weatherVariable], type: 'line', data: weatherRows.map((row) => row.weather_value), showSymbol: true, symbolSize: 4, connectNulls: false, lineStyle: { color: '#4ccbd2', width: 2 } }] } satisfies EChartsOption
  }, [hourlyRows, weatherRows, weatherUnit, weatherVariable, singleDay])

  const lastPrice = hourlyRows.at(-1)?.price ?? null
  const highestPrice = hourlyRows.reduce<number | null>((max, row) => row.price == null ? max : max == null ? row.price : Math.max(max, row.price), null)
  const lowestPrice = hourlyRows.reduce<number | null>((min, row) => row.price == null ? min : min == null ? row.price : Math.min(min, row.price), null)

  function changeMarket(nextZone: string) {
    const nextMarket = markets.find((market) => market.bidding_zone === nextZone)
    setZone(nextZone)
    if (nextMarket) {
      setStart(nextMarket.last_source_date)
      setEnd(nextMarket.last_source_date)
    }
  }

  return (
    <main className="app-shell">
      <aside className="rail" aria-label="EuroGrid navigation">
        <div className="brand-mark">E<span>G</span></div><div className="rail-divider" />
        <button className="rail-item active" aria-label="Market overview"><span>⌁</span></button>
        <div className="rail-spacer" /><div className="rail-status"><i />LOCAL</div>
      </aside>

      <section className="workspace">
        <header className="topbar"><div className="breadcrumb"><span>EUROGRID</span><b>/</b><strong>Electricity at a glance</strong></div><div className="topbar-right"><span className="live-dot" /> Sample data · local only <span className="avatar">M</span></div></header>
        <div className="page-content">
          <div className="title-row">
            <div><div className="eyebrow">EUROPEAN POWER SYSTEMS <span>·</span> BEGINNER VIEW</div><h1>Electricity at a glance</h1><p className="subtitle">Choose a country and day to see hourly prices, electricity produced, demand, and local weather.</p></div>
            <div className="sample-badge"><span className="badge-icon">◈</span><div><b>Research sample</b><small>January 2025 · public data</small></div></div>
          </div>

          <section className="filter-panel" aria-label="Market and date filters">
            <label className="filter-control market-control"><span>COUNTRY / MARKET AREA</span><select value={zone} onChange={(event) => changeMarket(event.target.value)} disabled={!markets.length}>
              {markets.map((market) => <option value={market.bidding_zone} key={market.bidding_zone}>{market.country_code?.toUpperCase()} · {market.bidding_zone}</option>)}
            </select><small>Choose the area with available price data.</small></label>
            <div className="filter-divider" />
            <label className="filter-control"><span>FROM (LOCAL DATE)</span><input type="date" value={start} onChange={(event) => setStart(event.target.value)} /><small>Same date = hourly view.</small></label>
            <span className="date-arrow" aria-hidden="true">→</span>
            <label className="filter-control"><span>TO (LOCAL DATE)</span><input type="date" value={end} onChange={(event) => setEnd(event.target.value)} /><small>Up to 31 days.</small></label>
            <div className="filter-spacer" />
            <div className="coverage-chip"><i />{selectedMarket ? `${selectedMarket.valid_price_interval_count.toLocaleString()} hourly price records available` : 'Loading market list'}</div>
          </section>

          <details className="quick-guide" open>
            <summary>New to electricity data? Start here</summary>
            <div className="guide-grid">
              <p><b>MW measures power.</b> Think of it as how fast electricity is being produced or needed at a given hour.</p>
              <p><b>MWh measures energy.</b> 1 MW sustained for 1 hour equals 1 MWh. The price is wholesale euros per MWh, not a household bill.</p>
              <p><b>Demand vs generation.</b> Generation is reported production, not the maximum power plants could make. Trade, storage, and missing data can explain differences.</p>
            </div>
          </details>

          {error && <div className="notice error-notice"><b>Couldn’t load this view.</b> {error}<span> Check that the API is running and the selected dates have data.</span></div>}
          {loading && <div className="loading-line"><i />Loading hourly electricity data…</div>}
          {!loading && !error && data && <>
            <div className="section-intro"><div><h2>Selected period: {dateRangeLabel}</h2><p>Times shown in the source’s local timezone{data.market.source_timezone ? ` (${data.market.source_timezone})` : ''}.</p></div><span>{hourlyRows.length} price hours in this view</span></div>

            <section className="metric-grid" aria-label="Key numbers for selected period">
              <article className="metric-card"><div className="metric-label">LAST HOURLY WHOLESALE PRICE</div><div className="metric-value">{lastPrice == null ? '—' : `€${formatNumber(lastPrice)}`}<small> / MWh</small></div><div className="metric-foot">At {hourlyRows.at(-1)?.time ?? '—'} · price to buy 1 MWh in this market.</div></article>
              <article className="metric-card"><div className="metric-label">HIGHEST / LOWEST PRICE</div><div className="metric-value compact">{highestPrice == null ? '—' : `€${formatNumber(highestPrice)}`} <small className="muted">/</small> {lowestPrice == null ? '—' : `€${formatNumber(lowestPrice)}`}<small> / MWh</small></div><div className="metric-foot">Highest and lowest hourly wholesale price in this period.</div></article>
              <article className="metric-card"><div className="metric-label">POWER NEEDED VS PRODUCED · AVERAGE</div><div className="metric-value">{formatNumber(averageDemand, 0)}<small> MW needed</small></div><div className="metric-foot">{formatNumber(averageGeneration, 0)} MW produced · demand is {formatNumber(demandShare, 0)}% of reported generation.</div></article>
              <article className="metric-card"><div className="metric-label">RENEWABLE PART OF PRODUCTION</div><div className="metric-value">{averageRenewableShare == null ? '—' : formatNumber(averageRenewableShare)}<small> %</small></div><div className="metric-foot">Average share of recorded power production from renewable sources.</div></article>
            </section>
            <p className="comparison-note">The demand/generation comparison uses hours where both readings are present. It is not spare capacity: imports, exports, storage, and incomplete source series affect the balance.</p>

            <section className="chart-grid" aria-label="Hourly electricity and weather charts">
              <article className="chart-card price-card"><div className="chart-heading"><div><span className="chart-index">01</span><h2>Wholesale price each hour</h2></div><span className="unit-tag">€ / MWh</span></div><p className="chart-caption">Cost for 1 MWh (1,000 kWh) on the wholesale market. Dashed line is the usual price at that hour in stored data; this is not a home electricity tariff.</p><ReactEChartsCore echarts={echarts} option={priceOption} style={{ height: 330 }} notMerge lazyUpdate /></article>
              <article className="chart-card"><div className="chart-heading"><div><span className="chart-index">02</span><h2>Power produced by source</h2></div><span className="unit-tag">MW</span></div><p className="chart-caption">Each coloured area is a power source. Areas stack to show total recorded generation each hour; this is actual output, not maximum capacity.</p><ReactEChartsCore echarts={echarts} option={generationOption} style={{ height: 320 }} notMerge lazyUpdate /></article>
              <article className="chart-card"><div className="chart-heading"><div><span className="chart-index">03</span><h2>Renewables in recorded production</h2></div><span className="unit-tag">%</span></div><p className="chart-caption">For example, 50% means half of the generation types with valid readings were renewable in that hour. It is not a share of household use.</p><ReactEChartsCore echarts={echarts} option={renewableOption} style={{ height: 320 }} notMerge lazyUpdate /></article>
              <article className="chart-card"><div className="chart-heading"><div><span className="chart-index">04</span><h2>Power needed each hour (demand)</h2></div><span className="unit-tag">MW</span></div><p className="chart-caption">The provider’s estimate of power needed across the country at each hour. Compare it with reported generation above, allowing for trade and missing data.</p><ReactEChartsCore echarts={echarts} option={demandOption} style={{ height: 300 }} notMerge lazyUpdate /></article>
              <article className="chart-card"><div className="chart-heading"><div><span className="chart-index">05</span><h2>Scheduled electricity traded across borders</h2></div><span className="unit-tag">MW</span></div><p className="chart-caption">Planned net electricity flow across interconnectors. Positive and negative directions follow the provider’s sign convention.</p><ReactEChartsCore echarts={echarts} option={flowOption} style={{ height: 300 }} notMerge lazyUpdate /></article>
              <article className="chart-card weather-card"><div className="chart-heading"><div><span className="chart-index">06</span><h2>Weather near the market</h2></div><select aria-label="Weather measurement" className="small-select" value={weatherVariable} onChange={(event) => setWeatherVariable(event.target.value)}><option value="temperature">Air temperature (°C)</option><option value="wind_speed">Wind speed (m/s)</option><option value="shortwave_radiation">Sunlight (W/m²)</option></select></div><p className="chart-caption">{weatherDescriptions[weatherVariable]} One reference location in {data.market.country_code?.toUpperCase() || 'this country'}; it is not a national average.</p>{weatherRows.length ? <ReactEChartsCore echarts={echarts} option={weatherOption} style={{ height: 300 }} notMerge lazyUpdate /> : <div className="chart-empty">No {weatherLabels[weatherVariable].toLowerCase()} readings were returned for this period.</div>}</article>
            </section>

            <section className="hourly-table-card"><div className="table-heading"><div><span className="chart-index">07</span><h2>Hour-by-hour details</h2></div><span>Local time · {dateRangeLabel}</span></div><p className="chart-caption">Read across a row to compare price, power needed, power produced, renewable share, and the chosen weather reading for that same hour.</p>
              <div className="table-scroll"><table><thead><tr><th>Local hour</th><th>Wholesale price<br /><small>€ / MWh</small></th><th>Power needed<br /><small>MW</small></th><th>Power produced<br /><small>MW</small></th><th>Renewables<br /><small>% of production</small></th><th>{weatherLabels[weatherVariable]}<br /><small>{weatherUnit || 'reading'}</small></th></tr></thead><tbody>
                {hourlyRows.map((row) => <tr key={row.key}><td>{row.time}</td><td>{row.price == null ? '—' : `€${formatNumber(row.price)}`}</td><td>{formatNumber(row.demand, 0)}</td><td>{formatNumber(row.generation, 0)}</td><td>{row.renewableShare == null ? '—' : formatNumber(row.renewableShare)}%</td><td>{formatNumber(row.weather, 1)}</td></tr>)}
              </tbody></table></div>
              <p className="table-note">Generation is the sum of available valid source readings for that hour. A dash means no valid reading; it does not mean zero.</p>
            </section>

            <details className="quality-panel"><summary>Data coverage and where it came from</summary><div className="quality-list"><div><span>Expected price hours</span><b>{data.window.expected_hourly_intervals}</b></div><div><span>Price readings returned</span><b>{data.quality.price_intervals_returned}</b></div><div><span>Price readings needing review</span><b>{data.quality.flagged_price_values}</b></div><div><span>Hours without a price reading · estimate</span><b>{data.quality.unobserved_hour_slots_estimate}</b></div><div><span>Generation / demand / flow readings</span><b>{data.quality.generation_rows} / {data.quality.demand_rows} / {data.quality.flow_rows}</b></div><div><span>Weather readings</span><b>{data.quality.weather_rows}</b></div><div><span>Stale readings</span><b>{data.quality.stale_observations}</b></div></div><div className="lineage-items">{Object.entries(data.lineage).map(([name, source]) => <div key={name}><b>{name.replaceAll('_', ' ')}</b><span>{source}</span></div>)}</div><div className="source-traces">{data.source_trace.map((source) => <div key={`${source.model}-${source.raw_sha256}`}><b>{source.measure} · {source.model}</b><span>{source.provider} / {source.dataset} · {source.record_count} rows</span><code title={source.raw_sha256}>raw SHA-256 {source.raw_sha256.slice(0, 16)}…</code></div>)}</div></details>
          </>}
          {!loading && !error && !data && <div className="empty-state">Choose a market area and date range to inspect its hourly readings.</div>}
          <footer className="page-footer"><span>EUROGRID INTELLIGENCE <i>·</i> LOCAL RESEARCH SAMPLE</span><span>Source: tested dbt models <i>·</i> DuckDB</span></footer>
        </div>
      </section>
    </main>
  )
}
