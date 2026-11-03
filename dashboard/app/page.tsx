'use client'
import { useEffect, useState, useCallback } from 'react'
import Header from '@/components/Header'
import StatsBar from '@/components/StatsBar'
import SignalRow from '@/components/SignalRow'
import ScoreChart from '@/components/ScoreChart'
import EmptyState from '@/components/EmptyState'
import { fetchSignals, fetchLivePrices } from '@/lib/data'
import type { SignalsData, LivePrice } from '@/lib/types'

type Filter = 'ALL' | 'STRONG BUY' | 'BUY'
type SortKey = 'score' | 'chg' | 'rsi' | 'vol_x'

export default function Dashboard() {
  const [data, setData]         = useState<SignalsData | null>(null)
  const [live, setLive]         = useState<Record<string, LivePrice>>({})
  const [liveOk, setLiveOk]     = useState(false)
  const [filter, setFilter]     = useState<Filter>('ALL')
  const [sort, setSort]         = useState<SortKey>('score')
  const [loading, setLoading]   = useState(true)

  // ── Load stored signals ──────────────────────────────────────────────────
  useEffect(() => {
    fetchSignals().then(d => {
      setData(d)
      setLoading(false)
    })
  }, [])

  // ── Poll live prices every 30s ────────────────────────────────────────────
  const refreshLive = useCallback(async () => {
    if (!data?.signals?.length) return
    const coins = data.signals.map(s => s.coin)
    const prices = await fetchLivePrices(coins)
    setLive(prices)
    setLiveOk(Object.keys(prices).length > 0)
  }, [data])

  useEffect(() => {
    refreshLive()
    const id = setInterval(refreshLive, 30_000)
    return () => clearInterval(id)
  }, [refreshLive])

  // ── Derived list ──────────────────────────────────────────────────────────
  const signals = (data?.signals ?? [])
    .filter(s => filter === 'ALL' || s.rating === filter)
    .sort((a, b) => {
      if (sort === 'score') return b.score - a.score
      if (sort === 'chg')   return b.chg - a.chg
      if (sort === 'rsi')   return b.rsi - a.rsi
      if (sort === 'vol_x') return b.vol_x - a.vol_x
      return 0
    })

  const FILTERS: Filter[] = ['ALL', 'STRONG BUY', 'BUY']
  const SORTS: { key: SortKey; label: string }[] = [
    { key: 'score',  label: 'Score' },
    { key: 'chg',    label: '24h %' },
    { key: 'rsi',    label: 'RSI' },
    { key: 'vol_x',  label: 'Volume' },
  ]

  return (
    <div className="min-h-screen bg-base">
      <Header generatedAt={data?.generated_at ?? null} liveOk={liveOk} />

      <main className="max-w-7xl mx-auto px-4 py-6 space-y-6">

        {/* Top stats + chart */}
        {data && (
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
            <div className="lg:col-span-2">
              <StatsBar signals={data.signals} scanned={data.scanned} />
            </div>
            <ScoreChart signals={data.signals} />
          </div>
        )}

        {/* Filter + sort toolbar */}
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex gap-1">
            {FILTERS.map(f => (
              <button
                key={f}
                onClick={() => setFilter(f)}
                className={`text-xs font-mono px-3 py-1.5 rounded border transition-colors ${
                  filter === f
                    ? 'border-cyan/50 bg-cyan/10 text-cyan'
                    : 'border-border text-muted hover:border-border/80 hover:text-text'
                }`}
              >
                {f}
              </button>
            ))}
          </div>
          <div className="flex items-center gap-2">
            <span className="text-muted text-xs font-mono">sort:</span>
            <div className="flex gap-1">
              {SORTS.map(s => (
                <button
                  key={s.key}
                  onClick={() => setSort(s.key)}
                  className={`text-xs font-mono px-2.5 py-1 rounded border transition-colors ${
                    sort === s.key
                      ? 'border-border bg-surface text-text'
                      : 'border-transparent text-muted hover:text-text'
                  }`}
                >
                  {s.label}
                </button>
              ))}
            </div>
          </div>
        </div>

        {/* Table */}
        {loading ? (
          <div className="text-muted text-sm font-mono py-16 text-center">Loading signals…</div>
        ) : !data || !data.signals.length ? (
          <EmptyState />
        ) : (
          <div className="rounded-lg border border-border overflow-x-auto">
            <table className="w-full min-w-[640px]">
              <thead>
                <tr className="border-b border-border">
                  {['#', 'Coin', 'Price', '24h', 'RSI', 'Volume', 'Score', 'Mkt vol'].map((h, i) => (
                    <th
                      key={h}
                      className={`py-2.5 text-muted text-xs font-mono font-medium ${
                        i === 0 ? 'pl-4 pr-2 text-left' :
                        i === 1 ? 'pr-4 text-left' :
                        i === 6 ? 'pr-4 text-left' :
                        i === 7 ? 'pr-4 text-right hidden lg:table-cell' :
                        'pr-4 text-right'
                      }`}
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {signals.map((s, i) => (
                  <SignalRow
                    key={s.coin}
                    rank={i + 1}
                    signal={s}
                    live={live[s.coin] ?? null}
                    style={{ animationDelay: `${i * 30}ms` }}
                  />
                ))}
              </tbody>
            </table>
            <div className="px-4 py-2 border-t border-border text-muted text-xs font-mono">
              {signals.length} signal{signals.length !== 1 ? 's' : ''} shown
              {data?.generated_at && (
                <span className="ml-3">
                  · last scan {new Date(data.generated_at).toUTCString().slice(0, 25)}
                </span>
              )}
            </div>
          </div>
        )}

        {/* Disclaimer */}
        <p className="text-muted text-xs font-mono text-center pb-4">
          Signals only — verify order-book depth and fees before trading.
          Live prices from Binance public API · refreshed every 30s.
        </p>
      </main>
    </div>
  )
}
