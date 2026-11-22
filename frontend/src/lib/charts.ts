// One place that registers the ECharts features used across pages.
import * as echarts from 'echarts/core'
import { BarChart, HeatmapChart, LineChart, ScatterChart } from 'echarts/charts'
import { DataZoomComponent, GridComponent, LegendScrollComponent, MarkAreaComponent, MarkLineComponent, TooltipComponent, VisualMapComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { LegacyGridContainLabel } from 'echarts/features'
import type { EChartsOption } from 'echarts'

echarts.use([LineChart, ScatterChart, BarChart, HeatmapChart, DataZoomComponent, GridComponent, LegendScrollComponent, MarkAreaComponent, MarkLineComponent, TooltipComponent, VisualMapComponent, CanvasRenderer,
  // ECharts 6 needs this for grid.containLabel (keeps axis labels inside the chart area).
  LegacyGridContainLabel])

export { echarts }
export type { EChartsOption }

export const palette = { green: '#6ee0a5', blue: '#87adff', amber: '#f2b66d', pink: '#e58fd0', red: '#ff8f80', teal: '#5fd0d4', grey: '#8ba095' }

export function baseOption(unit: string, labels: string[], zoom = false): EChartsOption {
  return {
    animation: false,
    grid: { left: 64, right: 22, top: 44, bottom: zoom ? 54 : 30, containLabel: true },
    tooltip: { trigger: 'axis', confine: true },
    legend: { top: 3, type: 'scroll', textStyle: { color: '#c9d6cd' } },
    xAxis: { type: 'category', data: labels, axisLabel: { hideOverlap: true, color: '#9aaea2' } },
    yAxis: { type: 'value', name: unit, nameLocation: 'middle', nameGap: 52, nameTextStyle: { color: '#9aaea2' }, axisLabel: { color: '#9aaea2' }, splitLine: { lineStyle: { color: '#1f2f28' } } },
    dataZoom: zoom ? [{ type: 'inside' }, { type: 'slider', height: 12, bottom: 4 }] : undefined,
  }
}
