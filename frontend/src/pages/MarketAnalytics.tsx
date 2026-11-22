// Market Analytics: country comparison, seasonality and detected anomalies (handbook 14).
import { useEffect, useState } from 'react'
import { getJson, postJson, fmt } from '../lib/api'
import { baseOption, palette, type EChartsOption } from '../lib/charts'
import { Chart } from '../components/Chart'
import { navigate } from '../lib/nav'
import { Empty, ErrorState, Loading, PageHeader, StatusPill } from '../components/States'

type MonthStat = { month: string; mean_eur_per_mwh: number; min_eur_per_mwh: number; max_eur_per_mwh: number; intervals: number; negative_intervals: number }
type Comparison = { year: number; markets: { bidding_zone: string; market_name: string; months: MonthStat[] }[]; source: string }
type Profile = { bidding_zone: string; market_name: string; timezone: string; cells: { month: number; hour: number; mean_eur_per_mwh: number }[]; source: string }
export type Anomaly = { anomaly_id: string; geography: string; direction: string; start_utc: string; end_utc: string; hour_count: number; max_abs_score: number; observed_value: number; baseline_median: number }
type Zone = { bidding_zone: string; timezone: string }
type Coverage = { markets: { bidding_zone: string; has_prices: boolean; is_aggregate: boolean; first_source_date: string | null; last_source_date: string | null }[] }

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const SERIES_COLOURS = [palette.green, palette.blue, palette.amber, palette.pink, palette.teal, palette.red, '#c5a3ff', '#9fd36f']

export default function MarketAnalytics() {
  const [years, setYears] = useState<string[]>([])
  const [year, setYear] = useState('')
  const [zones, setZones] = useState<Zone[]>([])
  const [zone, setZone] = useState('DE-LU')
  const [coverage, setCoverage] = useState<Coverage['markets']>([])
  const [comparison, setComparison] = useState<Comparison | null>(null)
  const [profile, setProfile] = useState<Profile | null>(null)
  const [anomalies, setAnomalies] = useState<{ total: number; items: Anomaly[] } | null>(null)
  const [error, setError] = useState('')
  const [profileError, setProfileError] = useState('')
  const [detecting, setDetecting] = useState(false)
  const [reload, setReload] = useState(0)

  useEffect(() => {
    Promise.all([getJson<Coverage>('/api/coverage'), getJson<Zone[]>('/api/anomalies/zones')]).then(([cov, approved]) => {
      setCoverage(cov.markets)
      const priced = cov.markets.filter((item) => item.has_prices && !item.is_aggregate && item.first_source_date)
      const first = Math.min(...priced.map((item) => Number(item.first_source_date!.slice(0, 4))))
      const last = Math.max(...priced.map((item) => Number(item.last_source_date!.slice(0, 4))))
      const list = Number.isFinite(first) ? Array.from({ length: last - first + 1 }, (_, index) => String(first + index)) : []
      setYears(list); setYear(list.at(-1) ?? '')
      setZones(approved)
      if (approved.length && !approved.some((item) => item.bidding_zone === 'DE-LU')) setZone(approved[0].bidding_zone)
    }).catch((reason: Error) => setError(reason.message))
  }, [])

  useEffect(() => {
    if (!year) return
    let active = true
    getJson<Comparison>('/api/analytics/price-comparison', { year }).then((value) => { if (active) { setComparison(value); setError('') } })
      .catch((reason: Error) => { if (active) setError(reason.message) })
    return () => { active = false }
  }, [year, reload])

  useEffect(() => {
    if (!year || !zone) return
    let active = true
    getJson<Profile>('/api/analytics/price-profile', { bidding_zone: zone, year }).then((value) => { if (active) { setProfile(value); setProfileError('') } })
      .catch((reason: Error) => { if (active) { setProfile(null); setProfileError(reason.message) } })
    getJson<{ total: number; items: Anomaly[] }>('/api/anomalies', { bidding_zone: zone, start_date: `${year}-01-01`, end_date: `${Number(year) + 1}-01-01`, limit: 200 })
      .then((value) => { if (active) setAnomalies(value) }).catch(() => { if (active) setAnomalies({ total: 0, items: [] }) })
    return () => { active = false }
  }, [year, zone, reload])

  async function detect() {
    const market = coverage.find((item) => item.bidding_zone === zone)
    const start = market?.first_source_date && market.first_source_date > `${year}-01-01` ? market.first_source_date : `${year}-01-01`
    const end = market?.last_source_date && market.last_source_date < `${year}-12-31` ? market.last_source_date : `${year}-12-31`
    setDetecting(true)
    try { await postJson('/api/anomalies/detect', { bidding_zone: zone, start_date: start, end_date: end }); setReload((value) => value + 1) }
    catch (reason) { setError((reason as Error).message) }
    finally { setDetecting(false) }
  }

  const markets = comparison?.markets.filter((market) => market.months.length) ?? []
  const comparisonOption: EChartsOption = {
    ...baseOption('EUR/MWh', MONTHS),
    tooltip: { trigger: 'axis', confine: true, valueFormatter: (value) => typeof value === 'number' ? `${fmt(value, 1)} EUR/MWh` : 'no data' },
    series: markets.map((market, index) => ({
      name: market.market_name, type: 'line', showSymbol: true, symbolSize: 5, connectNulls: false,
      lineStyle: { width: market.bidding_zone === zone ? 3 : 1.5, color: SERIES_COLOURS[index % SERIES_COLOURS.length] },
      itemStyle: { color: SERIES_COLOURS[index % SERIES_COLOURS.length] },
      data: MONTHS.map((_, month) => market.months.find((row) => Number(row.month.slice(5, 7)) === month + 1)?.mean_eur_per_mwh ?? null),
    })),
  }
  const annual = markets.map((market) => {
    const intervals = market.months.reduce((sum, row) => sum + row.intervals, 0)
    return { name: market.market_name, zone: market.bidding_zone, mean: market.months.reduce((sum, row) => sum + row.mean_eur_per_mwh * row.intervals, 0) / intervals,
      negativeShare: market.months.reduce((sum, row) => sum + row.negative_intervals, 0) / intervals * 100,
      max: Math.max(...market.months.map((row) => row.max_eur_per_mwh)), min: Math.min(...market.months.map((row) => row.min_eur_per_mwh)), months: market.months.length }
  }).sort((a, b) => b.mean - a.mean)
  const barOption: EChartsOption = {
    ...baseOption('EUR/MWh', annual.map((row) => row.name)),
    tooltip: { trigger: 'axis', confine: true, valueFormatter: (value) => `${fmt(value as number, 1)} EUR/MWh` },
    legend: undefined,
    series: [{ name: `${year} average (months with data)`, type: 'bar', data: annual.map((row) => ({ value: Number(row.mean.toFixed(2)), itemStyle: { color: row.zone === zone ? palette.green : '#3f6b55' } })) }],
  }

  const cells = profile?.cells ?? []
  const values = cells.map((cell) => cell.mean_eur_per_mwh)
  const heatOption: EChartsOption = {
    animation: false,
    grid: { left: 50, right: 20, top: 10, bottom: 96 },
    tooltip: { confine: true, formatter: (params: unknown) => { const [hour, month, value] = (params as { value: [number, number, number] }).value; return `${MONTHS[month]} · ${String(hour).padStart(2, '0')}:00–${String(hour + 1).padStart(2, '0')}:00 local<br/><b>${fmt(value, 1)} EUR/MWh</b> average` } },
    xAxis: { type: 'category', data: Array.from({ length: 24 }, (_, hour) => String(hour).padStart(2, '0')), name: `Local hour (${profile?.timezone ?? ''})`, nameLocation: 'middle', nameGap: 28, axisLabel: { color: '#9aaea2' }, nameTextStyle: { color: '#9aaea2' } },
    yAxis: { type: 'category', data: MONTHS, inverse: true, axisLabel: { color: '#9aaea2' } },
    visualMap: { min: values.length ? Math.min(...values) : 0, max: values.length ? Math.max(...values) : 1, calculable: true, orient: 'horizontal', left: 'center', bottom: 0, itemHeight: 220, text: ['EUR/MWh high', 'low'], textStyle: { color: '#9aaea2' }, inRange: { color: ['#163c5c', '#2f7d6d', '#d9c25f', '#e0703f'] } },
    series: [{ type: 'heatmap', data: cells.map((cell) => [cell.hour, cell.month - 1, cell.mean_eur_per_mwh]), emphasis: { itemStyle: { borderColor: '#fff', borderWidth: 1 } } }],
  }

  const events = anomalies?.items ?? []
  const anomalyOption: EChartsOption = {
    ...baseOption('Robust score (|z|)', []),
    xAxis: { type: 'time', axisLabel: { color: '#9aaea2' } },
    tooltip: { trigger: 'item', confine: true, formatter: (params: unknown) => { const item = (params as { data: { event: Anomaly } }).data.event; return `<b>${item.direction}</b> ${item.start_utc.slice(0, 16).replace('T', ' ')} UTC<br/>${fmt(item.observed_value, 1)} vs usual ${fmt(item.baseline_median, 1)} EUR/MWh<br/>score ${fmt(item.max_abs_score, 1)} · ${item.hour_count} h<br/><i>Click to open the case</i>` } },
    series: ['spike', 'dip'].map((direction) => ({
      name: direction === 'spike' ? 'Price spikes' : 'Price dips', type: 'scatter',
      itemStyle: { color: direction === 'spike' ? palette.red : palette.blue },
      symbolSize: (value: number[]) => Math.min(26, 6 + value[1] / 2),
      data: events.filter((item) => item.direction === direction).map((item) => ({ value: [item.start_utc, item.max_abs_score], event: item })),
      markLine: direction === 'spike' ? { silent: true, symbol: 'none', data: [{ yAxis: 5, label: { formatter: 'Detection threshold 5', color: palette.amber, position: 'insideEndTop' }, lineStyle: { color: palette.amber, type: 'dashed' } }] } : undefined,
    })),
  }

  return <main className="dashboard">
    <PageHeader eyebrow="MARKET ANALYTICS" title="Compare markets, seasons and unusual hours">Historical day-ahead prices across countries, the typical shape of a day in each month, and the price anomalies detected by the transparent median/MAD rule.</PageHeader>
    <section className="controls">
      <label>YEAR<select value={year} onChange={(event) => setYear(event.target.value)}>{years.map((value) => <option key={value}>{value}</option>)}</select></label>
      <label>FOCUS MARKET<select value={zone} onChange={(event) => setZone(event.target.value)}>{zones.map((item) => <option key={item.bidding_zone}>{item.bidding_zone}</option>)}</select></label>
      <span className="coverage-copy">Months without data stay blank; nothing is estimated.</span>
    </section>
    {error && <ErrorState message={error} onRetry={() => setReload((value) => value + 1)} />}
    {!comparison && !error && <Loading label="Loading price history…" />}
    {comparison && <section className="charts">
      <article className="chart-card full">
        <h2>Monthly average day-ahead price by country · {year}</h2>
        <p>Each line is one bidding zone, averaged over its valid intervals in each local month. The focus market is drawn thicker. Unit: EUR/MWh (divide by 1,000 for €/kWh).</p>
        {markets.length ? <Chart option={comparisonOption} /> : <Empty title={`No priced markets for ${year}.`} />}
      </article>
      <article className="chart-card">
        <h2>Average price · {year}</h2>
        <p>Interval-weighted average over the months each market has data ({annual.map((row) => `${row.zone}: ${row.months} mo`).join(', ')}). Markets with fewer months are not directly comparable.</p>
        {annual.length ? <Chart option={barOption} height={300} /> : <Empty title="No data." />}
      </article>
      <article className="chart-card">
        <h2>Price range and negative prices · {year}</h2>
        <p>Lowest and highest single interval, and the share of intervals below zero.</p>
        <div className="table-scroll"><table className="compact"><thead><tr><th>Market</th><th>Average<br /><small>EUR/MWh</small></th><th>Lowest</th><th>Highest</th><th>Below zero<br /><small>% of intervals</small></th></tr></thead>
          <tbody>{annual.map((row) => <tr key={row.zone} className={row.zone === zone ? 'highlight' : ''}><td>{row.name}</td><td>{fmt(row.mean, 1)}</td><td>{fmt(row.min, 1)}</td><td>{fmt(row.max, 1)}</td><td>{fmt(row.negativeShare, 1)}%</td></tr>)}</tbody></table></div>
      </article>
      <article className="chart-card full">
        <h2>Typical day by month · {zone} · {year}</h2>
        <p>Average price for each local hour of the day in each month. Midday dips show solar; evening peaks show demand after sunset. Blank rows are months without data.</p>
        {profileError ? <Empty title="No seasonal profile for this market.">{profileError}</Empty> : !profile ? <Loading label="Loading profile…" /> : cells.length ? <Chart option={heatOption} height={380} /> : <Empty title={`No ${zone} prices in ${year}.`} />}
      </article>
      <article className="chart-card full">
        <div className="chart-title"><div><h2>Detected price anomalies · {zone} · {year}</h2>
          <p>Each dot is one event: hours whose price was far from the median of the same local hour and day type over the prior 28 days (robust score above 5). Bigger dots are more extreme. Click a dot or row to open the investigation.</p></div>
          <button className="button" onClick={detect} disabled={detecting}>{detecting ? 'Detecting…' : `Run detection for ${year}`}</button></div>
        {!anomalies ? <Loading label="Loading anomalies…" /> : events.length === 0
          ? <Empty title={`No saved anomalies for ${zone} in ${year}.`}>Run detection to scan the year; results are saved with repeatable IDs.</Empty>
          : <>
            <Chart option={anomalyOption} height={300} onEvents={{ click: (event: { data?: { event?: Anomaly } }) => { if (event.data?.event) navigate('investigations', event.data.event.anomaly_id) } }} />
            <div className="table-scroll"><table><thead><tr><th>Start (UTC)</th><th>Direction</th><th>Hours</th><th>Peak<br /><small>EUR/MWh</small></th><th>Usual<br /><small>median, EUR/MWh</small></th><th>Score</th><th /></tr></thead>
              <tbody>{events.slice(0, 25).map((item) => <tr key={item.anomaly_id} className="clickable" onClick={() => navigate('investigations', item.anomaly_id)}>
                <td>{item.start_utc.slice(0, 16).replace('T', ' ')}</td><td><StatusPill status={item.direction} /></td><td>{item.hour_count}</td><td>{fmt(item.observed_value, 1)}</td><td>{fmt(item.baseline_median, 1)}</td><td>{fmt(item.max_abs_score, 1)}</td><td className="link">Investigate →</td></tr>)}</tbody></table></div>
            {events.length > 25 && <p className="table-note">Showing the 25 most recent of {anomalies.total} events. All are listed on the AI Investigations page.</p>}
          </>}
      </article>
    </section>}
  </main>
}
