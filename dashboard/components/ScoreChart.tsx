'use client'
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell
} from 'recharts'
import type { DailySignal } from '@/lib/types'

interface Props { signals: DailySignal[] }

export default function ScoreChart({ signals }: Props) {
  const buckets: Record<string, number> = {
    '50–59': 0, '60–69': 0, '70–79': 0, '80–89': 0, '90+': 0,
  }
  for (const s of signals) {
    if (s.score >= 90)      buckets['90+']++
    else if (s.score >= 80) buckets['80–89']++
    else if (s.score >= 70) buckets['70–79']++
    else if (s.score >= 60) buckets['60–69']++
    else                    buckets['50–59']++
  }
  const data = Object.entries(buckets).map(([range, count]) => ({ range, count }))

  const colors = ['#4b6480', '#22c55e', '#22c55e', '#00e5ff', '#00e5ff']

  return (
    <div className="bg-surface border border-border rounded-lg p-4">
      <p className="text-xs text-muted font-mono mb-3">Score distribution</p>
      <ResponsiveContainer width="100%" height={100}>
        <BarChart data={data} barSize={20} margin={{ top: 0, bottom: 0, left: -20, right: 0 }}>
          <XAxis
            dataKey="range"
            tick={{ fill: '#4b6480', fontSize: 10, fontFamily: 'JetBrains Mono' }}
            axisLine={false} tickLine={false}
          />
          <YAxis
            tick={{ fill: '#4b6480', fontSize: 10, fontFamily: 'JetBrains Mono' }}
            axisLine={false} tickLine={false} allowDecimals={false}
          />
          <Tooltip
            contentStyle={{ background: '#111827', border: '1px solid #1e2d40', borderRadius: 6, fontFamily: 'JetBrains Mono', fontSize: 11 }}
            itemStyle={{ color: '#c8d8e8' }}
            cursor={{ fill: 'rgba(255,255,255,0.04)' }}
          />
          <Bar dataKey="count" radius={[3, 3, 0, 0]}>
            {data.map((_, i) => <Cell key={i} fill={colors[i]} />)}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}
