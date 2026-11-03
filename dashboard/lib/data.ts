import type { SignalsData, LivePrice } from './types'

// ── Stored signals (written by GitHub Actions) ──────────────────────────────
export async function fetchSignals(): Promise<SignalsData | null> {
  try {
    const res = await fetch('/data/signals.json', { cache: 'no-store' })
    if (!res.ok) return null
    return res.json()
  } catch {
    return null
  }
}

// ── Live Binance prices for coins in the signals list ───────────────────────
export async function fetchLivePrices(coins: string[]): Promise<Record<string, LivePrice>> {
  if (!coins.length) return {}
  const out: Record<string, LivePrice> = {}
  try {
    // Use the public Binance vision mirror (no key needed, no CORS block)
    const symbols = coins.map(c => `"${c}USDT"`).join(',')
    const url = `https://data-api.binance.vision/api/v3/ticker/24hr?symbols=[${symbols}]`
    const res = await fetch(url, { next: { revalidate: 30 } })
    if (!res.ok) return {}
    const data: LivePrice[] = await res.json()
    for (const t of data) {
      const coin = t.symbol.replace('USDT', '')
      out[coin] = t
    }
  } catch {
    // silent — UI shows stale data indicator
  }
  return out
}
