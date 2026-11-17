import { useEffect, useMemo, useState } from 'react'
import ReactEChartsCore from 'echarts-for-react/esm/core'
import * as echarts from 'echarts/core'
import { LineChart } from 'echarts/charts'
import { DataZoomComponent, GridComponent, LegendScrollComponent, MarkLineComponent, TitleComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import type { EChartsOption } from 'echarts'
import './styles.css'

// Register only the chart types and features used on this page to keep its JS bundle smaller.
echarts.use([LineChart, DataZoomComponent, GridComponent, LegendScrollComponent, MarkLineComponent, TitleComponent, TooltipComponent, CanvasRenderer])

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

async function getJson<T>(url: string): Promise<T> {
  const response = await fetch(url)
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}))
    throw new Error(payload.detail ?? `Request failed (${response.status})`)
  }
  return response.json() as Promise<T>
}

function formatNumber(value: number | null | undefined, digits = 1) {
  return value == null ? '—' : new Intl.NumberFormat('en-GB', { maximumFractionDigits: digits }).format(value)
}

function localLabel(row: Point) {
  const original = String(row.original_timestamp ?? '')
  return original.replace('T', ' ').slice(0, 16)
}

function chartBase(title: string, unit: string, labels: string[]): EChartsOption {
  return {
    animation: false,
    grid: { left: 58, right: 24, top: 38, bottom: 56 },
    tooltip: { trigger: 'axis', backgroundColor: '#0d1918', borderColor: '#314542', textStyle: { color: '#eff5ef' } },
    legend: { top: 0, right: 8, textStyle: { color: '#91a39c' }, itemWidth: 14, itemHeight: 3 },
    xAxis: { type: 'category', data: labels, boundaryGap: false, axisLabel: { color: '#71837d', hideOverlap: true }, axisLine: { lineStyle: { color: '#273c38' } }, axisTick: { show: false } },
    yAxis: { type: 'value', name: unit, nameTextStyle: { color: '#71837d' }, axisLabel: { color: '#71837d' }, splitLine: { lineStyle: { color: '#1c2b29' } } },
    title: { text: title, left: 8, top: 2, textStyle: { color: '#eaf1e8', fontSize: 13, fontWeight: 500 } },
    dataZoom: [{ type: 'inside' }, { type: 'slider', height: 16, bottom: 5, borderColor: '#273c38', fillerColor: 'rgba(103, 202, 143, .13)', textStyle: { color: '#71837d' } }],
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
  const [weatherVariable, setWeatherVariable] = useState('temperature_2m')

  useEffect(() => {
    getJson<{ markets: Market[] }>(`${API}/api/coverage`)
      .then(({ markets: available }) => {
        setMarkets(available)
        const first = available.find((item) => item.bidding_zone === 'DE-LU') ?? available[0]
        if (first) {
          setZone(first.bidding_zone)
          setStart(first.first_source_date)
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
  const priceLabels = useMemo(() => data?.prices.map(localLabel) ?? [], [data])
  const priceOption = useMemo(() => {
    if (!data) return {}
    const option = chartBase('Day-ahead price and hourly baseline', 'EUR / MWh', priceLabels)
    return {
      ...option,
      series: [
        { name: 'Observed price', type: 'line', data: data.prices.map((row) => row.price_eur_per_mwh), showSymbol: false, connectNulls: false, lineStyle: { color: '#6ee0a5', width: 2 }, itemStyle: { color: '#6ee0a5' } },
        { name: 'Stored-history hour mean', type: 'line', data: data.prices.map((row) => row.mean_price_eur_per_mwh), showSymbol: false, connectNulls: true, lineStyle: { color: '#f2b66d', width: 1.5, type: 'dashed' }, itemStyle: { color: '#f2b66d' } },
      ],
    } satisfies EChartsOption
  }, [data, priceLabels])

  const generationOption = useMemo(() => {
    if (!data) return {}
    const times = [...new Set(data.generation.map((row) => String(row.interval_start_utc)))].sort()
    const seriesNames = [...new Set(data.generation.map((row) => String(row.production_type)))].sort()
    const byTypeTime = new Map(data.generation.map((row) => [`${row.production_type}|${row.interval_start_utc}`, row.generation_mw]))
    const option = chartBase('Generation by source', 'MW', times.map((value) => value.slice(11, 16)))
    return {
      ...option,
      legend: { ...(option.legend as object), data: seriesNames, type: 'scroll', left: 68, right: 18 },
      series: seriesNames.map((name) => ({
        name,
        type: 'line',
        stack: 'generation',
        areaStyle: { opacity: 0.18 },
        showSymbol: false,
        connectNulls: false,
        data: times.map((time) => byTypeTime.get(`${name}|${time}`) ?? null),
      })),
    } satisfies EChartsOption
  }, [data])

  const demandOption = useMemo(() => {
    if (!data) return {}
    const labels = data.demand.map(localLabel)
    const option = chartBase('Demand (provider load series)', 'MW', labels)
    return { ...option, series: [{ name: 'Demand', type: 'line', data: data.demand.map((row) => row.demand_mw), showSymbol: false, connectNulls: false, lineStyle: { color: '#87adff', width: 2 }, areaStyle: { color: 'rgba(92, 134, 228, .10)' } }] } satisfies EChartsOption
  }, [data])

  const flowOption = useMemo(() => {
    if (!data) return {}
    const labels = data.cross_border_flows.map(localLabel)
    const option = chartBase('Net scheduled cross-border trading', 'MW · provider sign', labels)
    return { ...option, series: [{ name: 'Net trading', type: 'line', data: data.cross_border_flows.map((row) => row.net_cross_border_trading_mw), showSymbol: false, connectNulls: false, lineStyle: { color: '#c68cff', width: 2 }, areaStyle: { color: 'rgba(198, 140, 255, .10)' }, markLine: { silent: true, symbol: 'none', data: [{ yAxis: 0 }], lineStyle: { color: '#63716d', type: 'dashed' } } }] } satisfies EChartsOption
  }, [data])

  const weatherRows = useMemo(() => data?.weather.filter((row) => row.variable === weatherVariable) ?? [], [data, weatherVariable])
  const weatherLabel = weatherVariable === 'temperature_2m' ? 'Temperature' : weatherVariable === 'wind_speed_10m' ? 'Wind speed' : 'Shortwave radiation'
  const weatherUnit = weatherRows[0]?.weather_unit ?? ''
  const weatherOption = useMemo(() => {
    const labels = weatherRows.map(localLabel)
    const option = chartBase(`${weatherLabel} · coordinate reference`, String(weatherUnit), labels)
    return { ...option, series: [{ name: weatherLabel, type: 'line', data: weatherRows.map((row) => row.weather_value), showSymbol: false, connectNulls: false, lineStyle: { color: '#4ccbd2', width: 2 } }] } satisfies EChartsOption
  }, [weatherRows, weatherLabel, weatherUnit])

  const renewableProfile = useMemo(() => {
    if (!data) return []
    const renewableTypes = new Set(['wind_onshore', 'wind_offshore', 'solar', 'hydro_run_of_river', 'hydro_water_reservoir', 'biomass', 'geothermal', 'other_renewables'])
    const hourly = new Map<string, { renewable: number; total: number }>()
    for (const row of data.generation) {
      if (row.data_status !== 'valid' || typeof row.generation_mw !== 'number') continue
      const key = String(row.interval_start_utc)
      const bucket = hourly.get(key) ?? { renewable: 0, total: 0 }
      bucket.total += row.generation_mw
      if (renewableTypes.has(String(row.production_type))) bucket.renewable += row.generation_mw
      hourly.set(key, bucket)
    }
    return [...hourly.entries()].sort(([left], [right]) => left.localeCompare(right))
      .filter(([, item]) => item.total > 0)
      .map(([timestamp, item]) => ({ timestamp, percent: 100 * item.renewable / item.total }))
  }, [data])
  const renewableShare = renewableProfile.length
    ? renewableProfile.reduce((sum, item) => sum + item.percent, 0) / renewableProfile.length
    : null
  const renewableOption = useMemo(() => {
    const labels = renewableProfile.map((item) => item.timestamp.slice(11, 16))
    const option = chartBase('Renewable share of reported generation', '%', labels)
    return { ...option, yAxis: { ...(option.yAxis as object), min: 0, max: 100 }, series: [{ name: 'Renewable share', type: 'line', data: renewableProfile.map((item) => item.percent), showSymbol: false, connectNulls: false, lineStyle: { color: '#e3c56e', width: 2 }, areaStyle: { color: 'rgba(227, 197, 110, .10)' } }] } satisfies EChartsOption
  }, [renewableProfile])

  const latestPrice = data?.prices.at(-1)?.price_eur_per_mwh as number | null | undefined
  const avgDemand = data?.demand.length ? data.demand.reduce((sum, row) => sum + (Number(row.demand_mw) || 0), 0) / data.demand.length : null
  const priceRows = data?.prices ?? []
  const inDateOrder = priceRows.filter((row) => row.data_status === 'valid' && typeof row.price_eur_per_mwh === 'number')
  const highestPrice = inDateOrder.length ? Math.max(...inDateOrder.map((row) => Number(row.price_eur_per_mwh))) : null
  const lowestPrice = inDateOrder.length ? Math.min(...inDateOrder.map((row) => Number(row.price_eur_per_mwh))) : null

  return (
    <main className="app-shell">
      <aside className="rail">
        <div className="brand-mark">E<span>G</span></div>
        <div className="rail-divider" />
        <button className="rail-item active" aria-label="Market overview"><span>⌁</span></button>
        <button className="rail-item" aria-label="Market analytics"><span>▥</span></button>
        <button className="rail-item" aria-label="Data operations"><span>◷</span></button>
        <div className="rail-spacer" />
        <div className="rail-status"><i />LOCAL</div>
      </aside>

      <section className="workspace">
        <header className="topbar">
          <div className="breadcrumb"><span>EUROGRID</span><b>/</b><strong>Market intelligence</strong></div>
          <div className="topbar-right"><span className="live-dot" /> Local data workspace <span className="avatar">M</span></div>
        </header>

        <div className="page-content">
          <div className="title-row">
            <div>
              <div className="eyebrow">EUROPEAN POWER SYSTEMS <span>·</span> MARKET OVERVIEW</div>
              <h1>Market overview</h1>
              <p className="subtitle">A source-traceable view of prices, power mix and system signals.</p>
            </div>
            <div className="sample-badge"><span className="badge-icon">◈</span><div><b>Research sample</b><small>Public data · local only</small></div></div>
          </div>

          <section className="filter-panel" aria-label="Market and date filters">
            <label className="filter-control market-control"><span>MARKET ZONE</span><select value={zone} onChange={(event) => setZone(event.target.value)} disabled={!markets.length}>
              {markets.map((market) => <option value={market.bidding_zone} key={market.bidding_zone}>{market.country_code?.toUpperCase()} · {market.bidding_zone}</option>)}
            </select></label>
            <div className="filter-divider" />
            <label className="filter-control"><span>FROM</span><input type="date" value={start} onChange={(event) => setStart(event.target.value)} /></label>
            <span className="date-arrow">→</span>
            <label className="filter-control"><span>TO</span><input type="date" value={end} onChange={(event) => setEnd(event.target.value)} /></label>
            <div className="filter-spacer" />
            <div className="coverage-chip"><i />{selectedMarket ? `${selectedMarket.valid_price_interval_count.toLocaleString()} price intervals` : 'Loading coverage'}</div>
          </section>

          {error && <div className="notice error-notice"><b>Couldn’t load this view.</b> {error}<span> Confirm the API is running and that you selected dates with source coverage.</span></div>}
          {loading && <div className="loading-line"><i /> Querying tested Gold models…</div>}
          {!loading && !error && data && <>
            <section className="metric-grid">
              <article className="metric-card"><div className="metric-label">LATEST DAY-AHEAD</div><div className="metric-value">{formatNumber(latestPrice)} <small>€/MWh</small></div><div className="metric-foot">Last observed interval in range</div></article>
              <article className="metric-card"><div className="metric-label">RANGE HIGH / LOW</div><div className="metric-value compact">{formatNumber(highestPrice)} <small className="muted">/</small> {formatNumber(lowestPrice)}</div><div className="metric-foot">Valid price observations · €/MWh</div></article>
              <article className="metric-card"><div className="metric-label">AVERAGE DEMAND</div><div className="metric-value">{formatNumber(avgDemand, 0)} <small>MW</small></div><div className="metric-foot">Provider load series for {data.market.country_code?.toUpperCase() || 'selected area'}</div></article>
              <article className="metric-card"><div className="metric-label">MEAN RENEWABLE SHARE</div><div className="metric-value">{renewableShare == null ? '—' : formatNumber(renewableShare)} <small>%</small></div><div className="metric-foot">Calculated from available valid generation rows</div></article>
            </section>

            <section className="chart-grid">
              <article className="chart-card price-card"><div className="chart-heading"><div><span className="chart-index">01</span><h2>Price history</h2></div><span className="unit-tag">EUR/MWh</span></div><p className="chart-caption">Solid line is observed price. Dashed line is the stored-data mean for the same UTC hour.</p><ReactEChartsCore echarts={echarts} option={priceOption} style={{ height: 310 }} notMerge lazyUpdate /></article>
              <article className="chart-card"><div className="chart-heading"><div><span className="chart-index">02</span><h2>Generation mix</h2></div><span className="unit-tag">MW</span></div><p className="chart-caption">Country-level public production series from Energy-Charts.</p><ReactEChartsCore echarts={echarts} option={generationOption} style={{ height: 310 }} notMerge lazyUpdate /></article>
              <article className="chart-card"><div className="chart-heading"><div><span className="chart-index">03</span><h2>Renewable share</h2></div><span className="unit-tag">%</span></div><p className="chart-caption">Calculated from the available valid renewable production types each hour.</p><ReactEChartsCore echarts={echarts} option={renewableOption} style={{ height: 310 }} notMerge lazyUpdate /></article>
              <article className="chart-card"><div className="chart-heading"><div><span className="chart-index">04</span><h2>Demand</h2></div><span className="unit-tag">MW</span></div><p className="chart-caption">The provider’s public load series; this is power, not MWh consumed.</p><ReactEChartsCore echarts={echarts} option={demandOption} style={{ height: 290 }} notMerge lazyUpdate /></article>
              <article className="chart-card"><div className="chart-heading"><div><span className="chart-index">05</span><h2>Cross-border trading</h2></div><span className="unit-tag">MW</span></div><p className="chart-caption">Net scheduled trading is shown with the provider’s sign unchanged.</p><ReactEChartsCore echarts={echarts} option={flowOption} style={{ height: 290 }} notMerge lazyUpdate /></article>
              <article className="chart-card weather-card"><div className="chart-heading"><div><span className="chart-index">06</span><h2>Weather context</h2></div><select className="small-select" value={weatherVariable} onChange={(event) => setWeatherVariable(event.target.value)}><option value="temperature_2m">Temperature</option><option value="wind_speed_10m">Wind speed</option><option value="shortwave_radiation">Solar radiation</option></select></div><p className="chart-caption">Reference point in {data.market.country_code?.toUpperCase() || 'the selected country'}; not a whole-zone average.</p><ReactEChartsCore echarts={echarts} option={weatherOption} style={{ height: 290 }} notMerge lazyUpdate /></article>
              <article className="quality-card"><div className="quality-heading"><span className="quality-icon">✓</span><div><h2>Coverage &amp; quality</h2><p>Observed records in this query window</p></div></div><div className="quality-list"><div><span>Expected market intervals</span><b>{data.window.expected_hourly_intervals}</b></div><div><span>Price intervals returned</span><b>{data.quality.price_intervals_returned}</b></div><div><span>Flagged price values</span><b className={data.quality.flagged_price_values ? 'warn-text' : ''}>{data.quality.flagged_price_values}</b></div><div><span>Unobserved hour slots · estimate</span><b className={data.quality.unobserved_hour_slots_estimate ? 'warn-text' : ''}>{data.quality.unobserved_hour_slots_estimate}</b></div><div><span>Generation / demand / flows</span><b>{data.quality.generation_rows} / {data.quality.demand_rows} / {data.quality.flow_rows}</b></div><div><span>Weather observations</span><b>{data.quality.weather_rows}</b></div><div><span>Flagged non-price records</span><b className={data.quality.flagged_power_weather_values ? 'warn-text' : ''}>{data.quality.flagged_power_weather_values}</b></div><div><span>Stale observations</span><b className={data.quality.stale_observations ? 'warn-text' : ''}>{data.quality.stale_observations}</b></div></div><div className="quality-note">A gap means no source interval was returned. It is not treated as zero.</div></article>
            </section>

            <section className="lineage-panel"><div className="lineage-title"><span>↗</span><b>DATA LINEAGE</b><small>Charts map to tested Gold models and the exact raw response hashes used.</small></div><div className="lineage-items">{Object.entries(data.lineage).map(([name, source]) => <div key={name}><b>{name.replaceAll('_', ' ')}</b><span>{source}</span></div>)}</div><div className="source-traces">{data.source_trace.map((source) => <div key={`${source.model}-${source.raw_sha256}`}><b>{source.measure} · {source.model}</b><span>{source.provider} / {source.dataset} · {source.record_count} rows</span><code title={source.raw_sha256}>raw SHA-256 {source.raw_sha256.slice(0, 16)}…</code></div>)}</div></section>
          </>}
          {!loading && !error && !data && <div className="empty-state">Choose a market zone and date range to inspect available records.</div>}
          <footer className="page-footer"><span>EUROGRID INTELLIGENCE <i>·</i> PHASE 5</span><span>Inputs: tested dbt Gold models <i>·</i> Local DuckDB</span></footer>
        </div>
      </section>
    </main>
  )
}
