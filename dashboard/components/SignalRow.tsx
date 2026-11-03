import type { DailySignal, LivePrice } from '@/lib/types'
import ScoreBar from './ScoreBar'

interface Props {
  signal: DailySignal
  rank: number
  live: LivePrice | null
  style?: React.CSSProperties
}

const RATING_STYLE: Record<string, string> = {
  'STRONG BUY': 'text-cyan border-cyan/30 bg-cyan/5',
  'BUY':        'text-green border-green/30 bg-green/5',
  'WATCH':      'text-amber border-amber/30 bg-amber/5',
  'AVOID':      'text-muted border-muted/30',
}

function fmt(n: number): string {
  if (n >= 1000) return n.toLocaleString('en-US', { maximumFractionDigits: 2 })
  if (n >= 1)    return n.toFixed(4)
  return n.toFixed(6)
}

export default function SignalRow({ signal, rank, live, style }: Props) {
  const livePrice = live ? parseFloat(live.price) : null
  const livePct   = live ? parseFloat(live.priceChangePercent) : signal.chg
  const price     = livePrice ?? signal.price

  const pctColor  = livePct >= 0 ? 'text-green' : 'text-red'

  return (
    <tr
      className="row-appear border-b border-border/40 hover:bg-surface/60 transition-colors"
      style={style}
    >
      {/* Rank */}
      <td className="py-3 pl-4 pr-2 font-mono text-muted text-xs w-8">{rank}</td>

      {/* Coin + badges */}
      <td className="py-3 pr-4">
        <div className="flex items-center gap-2">
          <span className="font-mono font-medium text-sm text-text">{signal.coin}</span>
          <span
            className={`text-[10px] font-mono px-1.5 py-0.5 rounded border ${RATING_STYLE[signal.rating] ?? ''}`}
          >
            {signal.rating}
          </span>
        </div>
        <div className="flex gap-2 mt-0.5">
          {signal.above_e20 && (
            <span className="text-[10px] text-muted">E20↑</span>
          )}
          {signal.macd_bull && (
            <span className="text-[10px] text-muted">MACD↑</span>
          )}
        </div>
      </td>

      {/* Live price */}
      <td className="py-3 pr-4 font-mono text-sm text-right">
        <span className={livePrice ? 'text-text' : 'text-muted'}>
          ${fmt(price)}
        </span>
      </td>

      {/* 24h change */}
      <td className={`py-3 pr-4 font-mono text-sm text-right ${pctColor}`}>
        {livePct >= 0 ? '+' : ''}{livePct.toFixed(2)}%
      </td>

      {/* RSI */}
      <td className="py-3 pr-4 font-mono text-sm text-right">
        <span className={
          signal.rsi > 70 ? 'text-red' :
          signal.rsi < 40 ? 'text-amber' : 'text-text'
        }>
          {signal.rsi}
        </span>
      </td>

      {/* Volume spike */}
      <td className="py-3 pr-4 font-mono text-sm text-right">
        <span className={signal.vol_x >= 2 ? 'text-cyan' : signal.vol_x >= 1.2 ? 'text-green' : 'text-muted'}>
          {signal.vol_x >= 0.1 ? `×${signal.vol_x.toFixed(1)}` : '—'}
        </span>
      </td>

      {/* Score bar */}
      <td className="py-3 pr-4">
        <ScoreBar score={signal.score} />
      </td>

      {/* Volume (M) */}
      <td className="py-3 pr-4 font-mono text-xs text-right text-muted hidden lg:table-cell">
        ${signal.qv.toFixed(1)}M
      </td>
    </tr>
  )
}
