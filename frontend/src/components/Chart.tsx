// Thin ECharts wrapper so pages share one registration and sizing convention.
import ReactEChartsCore from 'echarts-for-react/esm/core'
import { echarts, type EChartsOption } from '../lib/charts'

export function Chart({ option, height = 320, onEvents }: { option: EChartsOption; height?: number; onEvents?: Record<string, (event: never) => void> }) {
  return <ReactEChartsCore echarts={echarts} option={option} style={{ height }} notMerge lazyUpdate onEvents={onEvents} />
}
