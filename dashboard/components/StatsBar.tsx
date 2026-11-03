import type { DailySignal } from '@/lib/types'

interface Props {
  signals: DailySignal[]
  scanned: number
}

export default function StatsBar({ signals, scanned }: Props) {
  const strong = signals.filter(s => s.rating === 'STRONG BUY').length
  const buy    = signals.filter(s => s.rating === 'BUY').length
  const avgRsi = signals.length
    ? (signals.reduce((a, b) => a + b.rsi, 0) / signals.length).toFixed(1)
    : '—'
  const avgScore = signals.length
    ? Math.round(signals.reduce((a, b) => a + b.score, 0) / signals.length)
    : 0

  const stats = [
    { label: 'Strong buy', value: strong,   color: 'text-cyan' },
    { label: 'Buy',        value: buy,      color: 'text-green' },
    { label: 'Scanned',    value: scanned,  color: 'text-text' },
    { label: 'Avg RSI',    value: avgRsi,   color: 'text-text' },
    { label: 'Avg score',  value: avgScore, color: 'text-text' },
  ]

  return (
    <div className="grid grid-cols-3 sm:grid-cols-5 gap-px bg-border rounded-lg overflow-hidden border border-border">
      {stats.map(({ label, value, color }) => (
        <div key={label} className="bg-surface px-4 py-3">
          <div className={`font-mono text-xl font-medium ${color}`}>{value}</div>
          <div className="text-muted text-xs mt-0.5">{label}</div>
        </div>
      ))}
    </div>
  )
}
