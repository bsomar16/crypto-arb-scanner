import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import {
  SignalModel, PositionModel, RegimeModel, NarrativeModel, MLModel, PortfolioSummary,
  DashboardState
} from './types';
import {
  LayoutDashboard, TrendingUp, TrendingDown, Zap, Brain, Globe, Target,
  AlertTriangle, CheckCircle, XCircle, ChevronDown, ChevronUp, RefreshCw,
  ExternalLink, Bell, Settings, Wifi, WifiOff, BarChart2, PieChart,
  ArrowUpRight, ArrowDownRight, Shield, Heart, Flame, Sparkles, Loader2,
  Search, SlidersHorizontal, Activity
} from 'lucide-react';
import {
  BarChart, Bar, LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, Legend, PieChart as RechartsPieChart, Pie, Cell,
  XAxis as BarXAxis
} from 'recharts';
import { formatDistanceToNow } from 'date-fns';

// ─── API & WebSocket Endpoints ───────────────────────────────────────
const DEFAULT_API_URL = 'https://crypto-arb-scanner-production.up.railway.app';
const DEFAULT_WS_URL = 'wss://crypto-arb-scanner-production.up.railway.app/ws';

const API_URL = (import.meta.env.VITE_API_URL || DEFAULT_API_URL).replace(/\/$/, '');
const WS_URL = import.meta.env.VITE_WS_URL || DEFAULT_WS_URL;

interface WSMessage {
  type: 'init' | 'state_update' | 'pong' | 'signal_new' | 'signal_update' | 'position_update' | 'regime_change';
  data: any;
  channel?: string;
}

// ─── Unified Data Hook (HTTP Instant Load + Resilient WebSocket + Auto-Poll) ───
function useDashboardState() {
  const [state, setState] = useState<DashboardState>({
    signals: [],
    positions: [],
    regime: null,
    narratives: [],
    ml_status: null,
    portfolio: null,
    last_update: new Date().toISOString()
  });

  const [connectionMode, setConnectionMode] = useState<'live' | 'synced' | 'connecting' | 'offline'>('connecting');
  const [isFetching, setIsFetching] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const reconnectAttempt = useRef(0);
  const isMountedRef = useRef(true);

  // 1. Direct HTTP State Fetch (fast 150ms instant load)
  const fetchState = useCallback(async () => {
    if (!isMountedRef.current) return;
    setIsFetching(true);
    try {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 7000);
      const res = await fetch(`${API_URL}/api/state`, { signal: controller.signal });
      clearTimeout(timer);

      if (res.ok) {
        const data = await res.json();
        if (isMountedRef.current) {
          setState(prev => ({
            ...prev,
            ...data,
            last_update: data.last_update || new Date().toISOString()
          }));
          if (wsRef.current?.readyState === WebSocket.OPEN) {
            setConnectionMode('live');
          } else {
            setConnectionMode('synced');
          }
        }
      }
    } catch (err) {
      console.warn('HTTP poll failed, relying on active connection:', err);
      if (wsRef.current?.readyState !== WebSocket.OPEN) {
        setConnectionMode('offline');
      }
    } finally {
      if (isMountedRef.current) setIsFetching(false);
    }
  }, []);

  // 2. Continuous Resilient WebSocket Connection
  const connectWs = useCallback(() => {
    if (!isMountedRef.current) return;
    if (wsRef.current && (wsRef.current.readyState === WebSocket.OPEN || wsRef.current.readyState === WebSocket.CONNECTING)) {
      return;
    }

    try {
      const ws = new WebSocket(WS_URL);
      wsRef.current = ws;

      ws.onopen = () => {
        if (!isMountedRef.current) return;
        reconnectAttempt.current = 0;
        setConnectionMode('live');
        // Subscribe to all channels
        ws.send(JSON.stringify({ type: 'subscribe', channels: ['signals', 'portfolio', 'regime', 'narratives'] }));
      };

      ws.onmessage = (event) => {
        if (!isMountedRef.current) return;
        try {
          const msg: WSMessage = JSON.parse(event.data);
          if (msg.type === 'init' || msg.type === 'state_update') {
            setState(prev => ({ ...prev, ...msg.data, last_update: msg.data.last_update || new Date().toISOString() }));
          } else if (msg.type === 'signal_new') {
            setState(prev => ({ ...prev, signals: [msg.data, ...prev.signals].slice(0, 100), last_update: new Date().toISOString() }));
          } else if (msg.type === 'signal_update') {
            setState(prev => ({
              ...prev,
              signals: prev.signals.map(s => s.coin === msg.data.coin ? { ...s, ...msg.data } : s),
              last_update: new Date().toISOString()
            }));
          } else if (msg.type === 'position_update') {
            setState(prev => ({
              ...prev,
              positions: prev.positions.map(p => p.coin === msg.data.coin ? { ...p, ...msg.data } : p),
              last_update: new Date().toISOString()
            }));
          } else if (msg.type === 'regime_change') {
            setState(prev => ({ ...prev, regime: msg.data, last_update: new Date().toISOString() }));
          }
        } catch (e) {
          console.error('WS parse error:', e);
        }
      };

      ws.onclose = () => {
        if (!isMountedRef.current) return;
        setConnectionMode('synced');
        // Infinite reconnect with exponential backoff (capped at 8 seconds)
        reconnectAttempt.current += 1;
        const delay = Math.min(1000 * Math.pow(1.5, reconnectAttempt.current), 8000);
        reconnectTimeoutRef.current = setTimeout(connectWs, delay);
      };

      ws.onerror = () => {
        if (wsRef.current?.readyState !== WebSocket.OPEN) {
          setConnectionMode('synced');
        }
      };
    } catch (e) {
      console.warn('WebSocket init exception:', e);
      setConnectionMode('synced');
    }
  }, []);

  // 3. Mount lifecycle & event listeners
  useEffect(() => {
    isMountedRef.current = true;
    // Initial fetch immediately
    fetchState();
    connectWs();

    // Background HTTP poll every 20 seconds
    const pollInterval = setInterval(() => {
      fetchState();
    }, 20000);

    // Mobile / Tab wake-up handler
    const handleVisibility = () => {
      if (document.visibilityState === 'visible') {
        fetchState();
        if (!wsRef.current || wsRef.current.readyState === WebSocket.CLOSED) {
          connectWs();
        }
      }
    };
    document.addEventListener('visibilitychange', handleVisibility);
    window.addEventListener('online', fetchState);

    return () => {
      isMountedRef.current = false;
      clearInterval(pollInterval);
      document.removeEventListener('visibilitychange', handleVisibility);
      window.removeEventListener('online', fetchState);
      if (reconnectTimeoutRef.current) clearTimeout(reconnectTimeoutRef.current);
      if (wsRef.current) wsRef.current.close();
    };
  }, [fetchState, connectWs]);

  const refresh = useCallback(() => {
    fetchState();
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: 'ping' }));
    } else {
      connectWs();
    }
  }, [fetchState, connectWs]);

  return {
    state,
    connectionMode,
    isFetching,
    refresh
  };
}

// ─── Status Badge Component ──────────────────────────────────────────
function StatusIndicator({
  mode,
  lastUpdate,
  isFetching
}: {
  mode: 'live' | 'synced' | 'connecting' | 'offline';
  lastUpdate: string;
  isFetching: boolean;
}) {
  const dotClass = mode === 'live' ? '' : mode === 'synced' ? 'disconnected' : 'offline';
  const label = mode === 'live' ? 'Live (WS)' : mode === 'synced' ? 'Synced (HTTP)' : mode === 'connecting' ? 'Connecting...' : 'Offline';

  let timeAgo = 'just now';
  try {
    timeAgo = formatDistanceToNow(new Date(lastUpdate), { addSuffix: true });
  } catch {}

  return (
    <div className="status-indicator" title={`Backend: ${API_URL}`}>
      <span className={`status-dot ${dotClass}`} />
      <span style={{ fontWeight: 600 }}>{label}</span>
      {isFetching && <Loader2 size={12} className="spinning" style={{ marginLeft: 4, color: 'var(--accent)' }} />}
      <span className="mono" style={{ marginLeft: 6, fontSize: '0.65rem', color: 'var(--text-dim)' }}>
        {timeAgo}
      </span>
    </div>
  );
}

// ─── Market Regime Card ──────────────────────────────────────────────
function RegimeCard({ regime }: { regime: RegimeModel | null }) {
  if (!regime) return <div className="card skeleton" style={{ height: 120 }} />;

  const regimeColors: Record<string, string> = {
    STRONG_BULL: 'var(--success)',
    BULL: 'var(--success)',
    R_BULL: 'var(--success)',
    R_CONT: '#38bdf8',
    NEUTRAL_BULL: 'var(--success)',
    NEUTRAL: 'var(--text-muted)',
    R_SIDE: 'var(--text-muted)',
    NEUTRAL_BEAR: 'var(--warning)',
    BEAR: 'var(--danger)',
    R_BEAR: 'var(--danger)',
    STRONG_BEAR: 'var(--danger)',
  };
  const color = regimeColors[regime.state] || 'var(--text-muted)';

  return (
    <div className="card">
      <div className="card-header">
        <span className="card-title">
          <Globe className="logo-icon" style={{ color }} />
          Market Regime
        </span>
        <span className="card-badge" style={{ background: `${color}15`, color, border: `1px solid ${color}30` }}>
          {regime.state}
        </span>
      </div>
      <div className="card-body">
        <div className="card-row">
          <span className="card-label">Confidence</span>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <div className="progress-bar" style={{ width: 100 }}>
              <div className="progress-fill progress-success" style={{ width: `${Math.min(100, regime.confidence)}%` }} />
            </div>
            <span className="card-value mono">{regime.confidence.toFixed(1)}%</span>
          </div>
        </div>
        <div className="card-row">
          <span className="card-label">Weighted 24h</span>
          <span className={`card-value mono ${regime.weighted_chg >= 0 ? 'positive' : 'negative'}`}>
            {regime.weighted_chg >= 0 ? '+' : ''}{regime.weighted_chg.toFixed(2)}%
          </span>
        </div>
        <div className="card-row">
          <span className="card-label">Vol Regime</span>
          <span className="card-value mono">{regime.vol_regime}</span>
        </div>
        <div className="card-row">
          <span className="card-label">Breadth</span>
          <span className="card-value mono">{regime.breadth_pct.toFixed(0)}% Bullish</span>
        </div>
      </div>
    </div>
  );
}

// ─── ML Status Card ──────────────────────────────────────────────────
function MLStatusCard({ ml }: { ml: MLModel | null }) {
  if (!ml) return <div className="card skeleton" style={{ height: 120 }} />;

  return (
    <div className="card">
      <div className="card-header">
        <span className="card-title">
          <Brain className="logo-icon" style={{ color: 'var(--purple)' }} />
          ML Classifier
        </span>
        <span className="card-badge badge-purple">{ml.model_type.toUpperCase()}</span>
      </div>
      <div className="card-body">
        <div className="card-row">
          <span className="card-label">Model Status</span>
          <span className={`card-badge ${ml.trained ? 'badge-high' : 'badge-low'}`}>
            {ml.trained ? 'TRAINED' : 'UNTRAINED'}
          </span>
        </div>
        <div className="card-row">
          <span className="card-label">Validation Acc</span>
          <span className="card-value mono">{(ml.val_accuracy * 100).toFixed(1)}%</span>
        </div>
        <div className="card-row">
          <span className="card-label">AUC Score</span>
          <span className="card-value mono">{(ml.val_auc * 100).toFixed(1)}%</span>
        </div>
        <div className="card-row">
          <span className="card-label">Training Size</span>
          <span className="card-value mono">{ml.train_samples.toLocaleString()} bars</span>
        </div>
      </div>
    </div>
  );
}

// ─── Portfolio Card ──────────────────────────────────────────────────
function PortfolioCard({ portfolio }: { portfolio: PortfolioSummary | null }) {
  if (!portfolio) return <div className="card skeleton" style={{ height: 140 }} />;

  return (
    <div className="card">
      <div className="card-header">
        <span className="card-title">
          <PieChart className="logo-icon" style={{ color: 'var(--accent)' }} />
          Portfolio Risk
        </span>
        <span className="card-badge badge-high">{portfolio.open_positions} Open</span>
      </div>
      <div className="card-body">
        <div className="card-row">
          <span className="card-label">Total Exposure</span>
          <span className="card-value mono">${portfolio.total_exposure.toLocaleString()}</span>
        </div>
        <div className="card-row">
          <span className="card-label">Risk Budget</span>
          <span className="card-value mono">${portfolio.total_risk.toLocaleString()}</span>
        </div>
        <div className="card-row">
          <span className="card-label">Daily P&L</span>
          <span className={`card-value mono ${portfolio.daily_pnl >= 0 ? 'positive' : 'negative'}`}>
            {portfolio.daily_pnl >= 0 ? '+' : ''}${portfolio.daily_pnl.toFixed(2)}
          </span>
        </div>
        <div className="card-row">
          <span className="card-label">Max Drawdown</span>
          <span className="card-value mono negative">{portfolio.max_drawdown.toFixed(2)}%</span>
        </div>
      </div>
    </div>
  );
}

// ─── Narrative List ──────────────────────────────────────────────────
function NarrativeList({ narratives }: { narratives: NarrativeModel[] }) {
  if (!narratives?.length) return null;

  return (
    <div className="card">
      <div className="card-header">
        <span className="card-title">
          <Flame className="logo-icon" style={{ color: 'var(--orange)' }} />
          Hot Narratives
        </span>
      </div>
      <div className="card-body" style={{ padding: '8px 12px' }}>
        {narratives.map(n => (
          <div key={n.name} style={{ padding: '8px 0', borderBottom: '1px solid var(--border)' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span style={{ fontSize: '0.8rem', fontWeight: 600 }}>{n.name}</span>
              <span className="card-badge badge-warm">{n.momentum_score.toFixed(0)} pts</span>
            </div>
            <div style={{ display: 'flex', gap: 6, marginTop: 4, flexWrap: 'wrap' }}>
              {n.top_coins.slice(0, 4).map(c => (
                <span key={c} className="mono" style={{ fontSize: '0.65rem', color: 'var(--text-muted)', background: 'var(--bg)', padding: '1px 6px', borderRadius: 4 }}>
                  {c}
                </span>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ─── Signal Card with Full SLS v9 Support ────────────────────────────
function SignalCard({ signal }: { signal: SignalModel }) {
  const isSls = signal.strategy === 'SLS' || signal.strategy_family === 'SLS' || signal.setup_type.startsWith('EARLY_') || ['BRK', 'BNC', 'PB', 'TRD'].includes(signal.setup_type);

  // Setup badge colors & icons
  const setupMeta: Record<string, { label: string; color: string; bg: string }> = {
    BRK: { label: 'Breakout', color: '#22c55e', bg: 'rgba(34,197,94,0.15)' },
    BREAKOUT: { label: 'Breakout', color: '#22c55e', bg: 'rgba(34,197,94,0.15)' },
    PB: { label: 'Pullback', color: '#f59e0b', bg: 'rgba(245,158,11,0.15)' },
    PULLBACK: { label: 'Pullback', color: '#f59e0b', bg: 'rgba(245,158,11,0.15)' },
    BNC: { label: 'Support Bounce', color: '#a855f7', bg: 'rgba(168,85,247,0.15)' },
    TRD: { label: 'Trend Thrust', color: '#22d3ee', bg: 'rgba(34,211,238,0.15)' },
    MOMENTUM: { label: 'Momentum', color: '#22d3ee', bg: 'rgba(34,211,238,0.15)' },
    REVERSAL: { label: 'Reversal', color: '#ec4899', bg: 'rgba(236,72,153,0.15)' },
    EARLY_BRK: { label: 'Early Breakout', color: '#4ade80', bg: 'rgba(74,222,128,0.2)' },
    EARLY_REV: { label: 'Early Reversal', color: '#c084fc', bg: 'rgba(192,132,252,0.2)' },
    EARLY_VWAP: { label: 'VWAP Reclaim', color: '#38bdf8', bg: 'rgba(56,189,248,0.2)' },
    EARLY_REL: { label: 'Rel Strength', color: '#f43f5e', bg: 'rgba(244,63,94,0.2)' },
    EARLY_MOM: { label: 'Mom Accel', color: '#06b6d4', bg: 'rgba(6,182,212,0.2)' }
  };

  const meta = setupMeta[signal.setup_type] || { label: signal.setup_type, color: 'var(--text-muted)', bg: 'rgba(148,163,184,0.15)' };

  return (
    <div className="card" style={{ borderLeft: isSls ? '3px solid #38bdf8' : '1px solid var(--border)' }}>
      <div className="card-header">
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontWeight: 700, fontSize: '1.05rem', color: 'var(--text)' }}>{signal.coin}</span>
          <span className="card-badge" style={{ background: meta.bg, color: meta.color, border: `1px solid ${meta.color}40` }}>
            {meta.label}
          </span>
          {isSls && <span className="card-badge badge-sls">SLS v9</span>}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span className="card-badge" style={{ background: 'var(--card-hover)', color: 'var(--text)' }}>
            {signal.interval}
          </span>
          <span className="card-badge badge-high" style={{ fontSize: '0.7rem' }}>
            Score {signal.score?.toFixed(0) || '0'}
          </span>
        </div>
      </div>
      <div className="card-body">
        <div className="card-row">
          <span className="card-label">Entry</span>
          <span className="card-value mono" style={{ color: 'var(--accent)' }}>${Number(signal.entry).toLocaleString()}</span>
        </div>
        <div className="card-row">
          <span className="card-label">Stop Loss</span>
          <span className="card-value mono" style={{ color: 'var(--danger)' }}>${Number(signal.stop).toLocaleString()}</span>
        </div>
        <div className="card-row">
          <span className="card-label">Target / Potential</span>
          <span className="card-value mono positive">
            ${Number(signal.target || signal.t3 || 0).toLocaleString()} (+{signal.potential_pct.toFixed(1)}%)
          </span>
        </div>

        {/* 5-target ladder if SLS */}
        {signal.t1 && signal.t2 && (
          <div style={{ background: 'var(--bg)', padding: '6px 8px', borderRadius: 6, display: 'flex', justifyContent: 'space-between', fontSize: '0.65rem' }} className="mono">
            <span>TP1: ${Number(signal.t1).toFixed(2)}</span>
            <span>TP2: ${Number(signal.t2).toFixed(2)}</span>
            {signal.t3 && <span>TP3: ${Number(signal.t3).toFixed(2)}</span>}
          </div>
        )}

        <div className="card-row">
          <span className="card-label">R:R Ratio</span>
          <span className="card-value mono">{signal.rr.toFixed(2)}R</span>
        </div>

        {signal.sls_regime && (
          <div className="card-row">
            <span className="card-label">Regime</span>
            <span className="card-badge badge-cool">{signal.sls_regime}</span>
          </div>
        )}

        {signal.ml_win_prob !== undefined && (
          <div className="card-row">
            <span className="card-label">ML Confidence</span>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <div className="progress-bar" style={{ width: 70 }}>
                <div className="progress-fill progress-success" style={{ width: `${(signal.ml_win_prob || 0.5) * 100}%` }} />
              </div>
              <span className="card-value mono" style={{ fontSize: '0.75rem' }}>{((signal.ml_win_prob || 0.5) * 100).toFixed(0)}%</span>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ─── Position Card ───────────────────────────────────────────────────
function PositionCard({ position }: { position: PositionModel }) {
  const pnlColor = position.pnl_pct >= 0 ? 'positive' : 'negative';

  let timeAgo = 'recently';
  try {
    timeAgo = formatDistanceToNow(new Date(position.entry_time), { addSuffix: true });
  } catch {}

  return (
    <div className="card">
      <div className="card-header">
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span className="card-title">{position.coin}</span>
          <span className={`card-badge ${pnlColor === 'positive' ? 'badge-high' : 'badge-low'}`}>
            {position.status}
          </span>
        </div>
        <span className={`card-value mono ${pnlColor}`} style={{ fontSize: '1rem' }}>
          {position.pnl_pct >= 0 ? '+' : ''}{position.pnl_pct.toFixed(2)}%
        </span>
      </div>
      <div className="card-body">
        <div className="card-row">
          <span className="card-label">Entry / Current</span>
          <span className="card-value mono">${position.entry.toLocaleString()} / ${position.current_price.toLocaleString()}</span>
        </div>
        <div className="card-row">
          <span className="card-label">Stop / Target</span>
          <span className="card-value mono">${position.stop.toLocaleString()} / ${position.target.toLocaleString()}</span>
        </div>
        <div className="card-row">
          <span className="card-label">Position Size</span>
          <span className="card-value mono">${position.size_usdt.toLocaleString()}</span>
        </div>
        <div className="card-row">
          <span className="card-label">Unrealized P&L</span>
          <span className={`card-value mono ${pnlColor}`}>
            {position.pnl_usdt >= 0 ? '+' : ''}${position.pnl_usdt.toFixed(2)} USDT
          </span>
        </div>
        <div className="card-row">
          <span className="card-label">Target Progress</span>
          <div style={{ display: 'flex', gap: 6 }}>
            <span className={`card-badge ${position.tp1_hit ? 'badge-high' : 'badge-low'}`}>TP1 {position.tp1_hit ? '✓' : ''}</span>
            <span className={`card-badge ${position.tp2_hit ? 'badge-high' : 'badge-low'}`}>TP2 {position.tp2_hit ? '✓' : ''}</span>
            <span className={`card-badge ${position.trailing_activated ? 'badge-cool' : 'badge-low'}`}>Trailing {position.trailing_activated ? '✓' : ''}</span>
          </div>
        </div>
        <div className="card-row">
          <span className="card-label">Opened</span>
          <span className="card-value mono" style={{ fontSize: '0.7rem', color: 'var(--text-dim)' }}>{timeAgo}</span>
        </div>
      </div>
    </div>
  );
}

// ─── Analytics Charts ────────────────────────────────────────────────
function SignalQualityChart({ signals }: { signals: SignalModel[] }) {
  if (!signals?.length) return null;

  const data = signals.slice(0, 10).map(s => ({
    name: s.coin,
    quality: s.trade_quality || s.score,
    potential: s.potential_pct,
    score: s.score
  }));

  return (
    <div className="card">
      <div className="card-header">
        <span className="card-title">
          <BarChart2 className="logo-icon" style={{ color: 'var(--accent)' }} />
          Top 10 Signal Quality
        </span>
      </div>
      <div className="card-body">
        <div className="chart-container">
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={data}>
              <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
              <XAxis dataKey="name" tick={{ fill: 'var(--text)', fontSize: 11 }} />
              <YAxis tick={{ fill: 'var(--text-muted)', fontSize: 10 }} domain={[0, 100]} />
              <Tooltip contentStyle={{ background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 6 }} />
              <Bar dataKey="quality" name="Quality Score" fill="var(--accent)" radius={[4, 4, 0, 0]} />
              <Bar dataKey="potential" name="Potential %" fill="var(--success)" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  );
}

// ─── Main Application Component ──────────────────────────────────────
export default function App() {
  const { state, connectionMode, isFetching, refresh } = useDashboardState();
  const [activeTab, setActiveTab] = useState<'signals' | 'positions' | 'overview' | 'analytics' | 'settings'>('signals');
  const [searchQuery, setSearchQuery] = useState('');
  const [filterInterval, setFilterInterval] = useState('ALL');
  const [filterStrategy, setFilterStrategy] = useState('ALL');

  // Filter signals
  const filteredSignals = useMemo(() => {
    return state.signals.filter(s => {
      const matchCoin = s.coin.toLowerCase().includes(searchQuery.toLowerCase().trim());
      const matchInterval = filterInterval === 'ALL' || s.interval === filterInterval;
      const isSls = s.strategy === 'SLS' || s.strategy_family === 'SLS' || s.setup_type.startsWith('EARLY_') || ['BRK', 'BNC', 'PB', 'TRD'].includes(s.setup_type);
      const matchStrategy = filterStrategy === 'ALL' || (filterStrategy === 'SLS' ? isSls : !isSls);
      return matchCoin && matchInterval && matchStrategy;
    });
  }, [state.signals, searchQuery, filterInterval, filterStrategy]);

  const tabs = [
    { id: 'signals', label: 'Signals', icon: Target, count: filteredSignals.length },
    { id: 'positions', label: 'Positions', icon: PieChart, count: state.positions.length },
    { id: 'overview', label: 'Overview', icon: LayoutDashboard, count: 0 },
    { id: 'analytics', label: 'Analytics', icon: BarChart2, count: 0 },
    { id: 'settings', label: 'Settings', icon: Settings, count: 0 }
  ];

  return (
    <div className="app">
      {/* Header */}
      <header className="header">
        <div className="header-left">
          <div className="logo">
            <Zap className="logo-icon" />
            <span>Crypto Scanner</span>
          </div>
          <StatusIndicator mode={connectionMode} lastUpdate={state.last_update} isFetching={isFetching} />
        </div>

        <div className="header-right">
          <div style={{ display: 'flex', gap: 6, overflowX: 'auto' }}>
            {tabs.map((tab) => (
              <button
                key={tab.id}
                className={`btn ${activeTab === tab.id ? 'btn-primary' : 'btn-secondary'}`}
                onClick={() => setActiveTab(tab.id as any)}
              >
                <tab.icon size={15} />
                <span>{tab.label}</span>
                {tab.count > 0 && (
                  <span className="mono" style={{ fontSize: '0.65rem', background: 'rgba(0,0,0,0.25)', padding: '1px 6px', borderRadius: 10 }}>
                    {tab.count}
                  </span>
                )}
              </button>
            ))}
          </div>
          <button
            className="btn btn-ghost"
            title="Refresh State Now"
            onClick={refresh}
            disabled={isFetching}
            style={{ padding: '8px' }}
          >
            <RefreshCw size={17} className={isFetching ? 'spinning' : ''} />
          </button>
        </div>
      </header>

      {/* Main Content */}
      <div className="main">
        {/* Desktop Sidebar (Left) */}
        <aside className="sidebar">
          <div className="sidebar-section">
            <RegimeCard regime={state.regime} />
          </div>
          <div className="sidebar-section">
            <MLStatusCard ml={state.ml_status} />
          </div>
          <div className="sidebar-section">
            <PortfolioCard portfolio={state.portfolio} />
          </div>
          <div className="sidebar-section">
            <NarrativeList narratives={state.narratives} />
          </div>
        </aside>

        {/* Content Pane */}
        <div className="content">
          <div className="content-header">
            <h1 className="content-title">
              {activeTab === 'signals' && 'Live Signals'}
              {activeTab === 'positions' && 'Open Positions'}
              {activeTab === 'overview' && 'Market & Model Overview'}
              {activeTab === 'analytics' && 'Analytics & Performance'}
              {activeTab === 'settings' && 'Scanner Settings'}
            </h1>

            {/* Action Bar for Signals */}
            {activeTab === 'signals' && (
              <div className="content-actions">
                <div style={{ position: 'relative', display: 'flex', alignItems: 'center' }}>
                  <Search size={14} style={{ position: 'absolute', left: 10, color: 'var(--text-dim)' }} />
                  <input
                    type="text"
                    placeholder="Search coin (e.g. BTC, SOL)..."
                    className="search-input"
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    style={{ paddingLeft: 30, width: 170 }}
                  />
                </div>

                <select
                  className="filter-select"
                  value={filterInterval}
                  onChange={(e) => setFilterInterval(e.target.value)}
                >
                  <option value="ALL">All TF</option>
                  <option value="5m">5m</option>
                  <option value="15m">15m</option>
                  <option value="1h">1h</option>
                  <option value="4h">4h</option>
                </select>

                <select
                  className="filter-select"
                  value={filterStrategy}
                  onChange={(e) => setFilterStrategy(e.target.value)}
                >
                  <option value="ALL">All Strategies</option>
                  <option value="SLS">SLS v9 Only</option>
                  <option value="CLASSIC">Classic Only</option>
                </select>
              </div>
            )}
          </div>

          {/* TAB 1: Live Signals */}
          {activeTab === 'signals' && (
            <div className="grid">
              {filteredSignals.length > 0 ? (
                filteredSignals.map((s) => (
                  <SignalCard key={`${s.coin}-${s.interval}-${s.setup_type}`} signal={s} />
                ))
              ) : (
                <div className="empty-state">
                  <Target className="empty-icon" />
                  <p className="empty-title">No Matching Signals Found</p>
                  <p className="empty-desc">Active scans evaluate every 5 min. Adjust filters or wait for qualifying setups.</p>
                </div>
              )}
            </div>
          )}

          {/* TAB 2: Open Positions */}
          {activeTab === 'positions' && (
            <div className="grid">
              {state.positions.length > 0 ? (
                state.positions.map((p) => (
                  <PositionCard key={p.coin} position={p} />
                ))
              ) : (
                <div className="empty-state">
                  <PieChart className="empty-icon" />
                  <p className="empty-title">No Active Positions</p>
                  <p className="empty-desc">New executions and tracked positions will automatically sync here.</p>
                </div>
              )}
            </div>
          )}

          {/* TAB 3: Overview (Crucial for Mobile & Tablet screens) */}
          {activeTab === 'overview' && (
            <div className="grid">
              <RegimeCard regime={state.regime} />
              <MLStatusCard ml={state.ml_status} />
              <PortfolioCard portfolio={state.portfolio} />
              <NarrativeList narratives={state.narratives} />
            </div>
          )}

          {/* TAB 4: Analytics */}
          {activeTab === 'analytics' && (
            <div className="grid">
              <SignalQualityChart signals={state.signals} />
              <RegimeCard regime={state.regime} />
              <MLStatusCard ml={state.ml_status} />
              <PortfolioCard portfolio={state.portfolio} />
            </div>
          )}

          {/* TAB 5: Settings */}
          {activeTab === 'settings' && (
            <div className="grid" style={{ maxWidth: 700, margin: '0 auto' }}>
              <div className="card">
                <div className="card-header"><span className="card-title">Backend Connection</span></div>
                <div className="card-body">
                  <div className="card-row">
                    <span className="card-label">API Endpoint</span>
                    <span className="card-value mono" style={{ fontSize: '0.75rem' }}>{API_URL}</span>
                  </div>
                  <div className="card-row">
                    <span className="card-label">WebSocket</span>
                    <span className="card-value mono" style={{ fontSize: '0.75rem' }}>{WS_URL}</span>
                  </div>
                  <div className="card-row">
                    <span className="card-label">Connection Mode</span>
                    <span className="card-badge badge-high">{connectionMode.toUpperCase()}</span>
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* Footer */}
          <footer className="footer">
            <div>Crypto Signal Dashboard · Railway Backend + Vercel Frontend</div>
            <div className="footer-links">
              <span>Status: <b style={{ color: 'var(--success)' }}>Active</b></span>
            </div>
          </footer>
        </div>
      </div>
    </div>
  );
}
