export interface DailySignal {
  coin: string
  price: number
  rsi: number
  vol_x: number
  chg: number
  score: number
  rating: 'STRONG BUY' | 'BUY' | 'WATCH' | 'AVOID'
  qv: number           // quote volume in millions
  above_e20: boolean
  macd_bull: boolean
}

export interface SignalsData {
  generated_at: string   // ISO timestamp
  scanned: number
  signals: DailySignal[]
}

export interface LivePrice {
  symbol: string
  price: string
  priceChangePercent: string
}
