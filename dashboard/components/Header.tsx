'use client'
import { useEffect, useState } from 'react'

interface Props {
  generatedAt: string | null
  liveOk: boolean
}

export default function Header({ generatedAt, liveOk }: Props) {
  const [now, setNow] = useState('')

  useEffect(() => {
    const tick = () => setNow(new Date().toUTCString().slice(0, 25))
    tick()
    const id = setInterval(tick, 1000)
    return () => clearInterval(id)
  }, [])

  const age = generatedAt
    ? Math.floor((Date.now() - new Date(generatedAt).getTime()) / 60000)
    : null

  return (
    <header className="sticky top-0 z-30 border-b border-border bg-base/90 backdrop-blur-sm">
      <div className="max-w-7xl mx-auto px-4 h-14 flex items-center justify-between gap-4">

        {/* Brand */}
        <div className="flex items-center gap-3">
          <span className="text-cyan text-lg font-mono font-medium tracking-tight">
            ◈ ARB·SIGNAL
          </span>
          <span className="hidden sm:block text-muted text-xs font-mono">
            crypto-arb-scanner
          </span>
        </div>

        {/* Status pills */}
        <div className="flex items-center gap-3 font-mono text-xs">
          {/* Live feed indicator */}
          <span className="flex items-center gap-1.5">
            <span
              className={`live-dot w-1.5 h-1.5 rounded-full ${liveOk ? 'bg-green' : 'bg-red'}`}
            />
            <span className={liveOk ? 'text-green' : 'text-red'}>
              {liveOk ? 'live' : 'offline'}
            </span>
          </span>

          {/* Signals age */}
          {age !== null && (
            <span className="text-muted hidden sm:block">
              signals {age}m ago
            </span>
          )}

          {/* UTC clock */}
          <span className="text-muted">{now}</span>
        </div>
      </div>
    </header>
  )
}
