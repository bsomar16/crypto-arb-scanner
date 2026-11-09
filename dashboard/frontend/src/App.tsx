import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
  SignalModel, PositionModel, RegimeModel, NarrativeModel, MLModel, PortfolioSummary,
  DashboardState, SignalModel, PositionModel
} from './types';
import {
  LayoutDashboard, TrendingUp, TrendingDown, Zap, Brain, Globe, Target,
  AlertTriangle, CheckCircle, XCircle, ChevronDown, ChevronUp, RefreshCw,
  ExternalLink, Bell, Settings, Wifi, WifiOff, BarChart2, PieChart,
  ArrowUpRight, ArrowDownRight, Shield, Heart, Flame, Sparkles, Loader2
} from 'lucide-react';
import { LineChart, Line, AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend, Pie, Cell, BarChart, Bar, XAxis as BarXAxis, YAxis as BarYAxis } from 'recharts';
import { format, formatDistanceToNow } from 'date-fns';

// ─── Types ──────────────────────────────────────────────────────────
interface Signal extends SignalModel {}
interface Position extends PositionModel {}
interface Regime extends RegimeModel {}
interface Narrative extends NarrativeModel {}
interface MLStatus extends MLModel {}
interface Portfolio extends PortfolioSummary {}

interface WSMessage {
  type: 'init' | 'state_update' | 'pong' | 'signal_new' | 'signal_update' | 'position_update' | 'regime_change';
  data: any;
  channel?: string;
}

// ─── Hooks ──────────────────────────────────────────────────────────
function useWebSocket(onMessage: (msg: WSMessage) => void) {
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const reconnectAttempts = useRef(0);

  const connect = useCallback(() => {
    // Use environment variable for WebSocket URL, fallback to same host
    const wsUrl = import.meta.env.VITE_WS_URL || 
      `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}/ws`;
    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      console.log('WebSocket connected');
      reconnectAttempts.current = 0;
    };

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        onMessage(msg);
      } catch (e) {
        console.error('WS parse error:', e);
      }
    };

    ws.onclose = () => {
      console.log('WebSocket disconnected');
      if (reconnectAttempts.current < 5) {
        reconnectAttempts.current++;
        const delay = Math.min(1000 * Math.pow(2, reconnectAttempts.current), 30000);
        reconnectTimeoutRef.current = setTimeout(connect, delay);
      }
    };

    ws.onerror = (err) => {
      console.error('WebSocket error:', err);
    };
  }, [onMessage]);

  useEffect(() => {
    connect();
    return () => {
      if (reconnectTimeoutRef.current) clearTimeout(reconnectTimeoutRef.current);
      if (wsRef.current) wsRef.current.close();
    };
  }, [connect]);

  const send = useCallback((msg: object) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(msg));
    }
  }, []);

  return { send, isConnected: wsRef.current?.readyState === WebSocket.OPEN };
}

function useDashboardState() {
  const [state, setState] = useState<DashboardState>({
    signals: [], positions: [], regime: null, narratives: [],
    ml_status: null, portfolio: null, last_update: new Date().toISOString()
  });

  const ws = useWebSocket((msg) => {
    if (msg.type === 'init' || msg.type === 'state_update') {
      setState(prev => ({ ...prev, ...msg.data }));
    } else if (msg.type === 'signal_new') {
      setState(prev => ({ ...prev, signals: [msg.data, ...prev.signals].slice(0, 100) }));
    } else if (msg.type === 'signal_update') {
      setState(prev => ({
        ...prev,
        signals: prev.signals.map(s => s.coin === msg.data.coin ? { ...s, ...msg.data } : s)
      }));
    } else if (msg.type === 'position_update') {
      setState(prev => ({
        ...prev,
        positions: prev.positions.map(p => p.coin === msg.data.coin ? { ...p, ...msg.data } : p)
      }));
    } else if (msg.type === 'regime_change') {
      setState(prev => ({ ...prev, regime: msg.data }));
    }
  });

  return state;
}

// ─── Components ─────────────────────────────────────────────────────

function StatusIndicator({ connected, lastUpdate }: { connected: boolean; lastUpdate: string }) {
  return (
    <div className="status-indicator" title={connected ? 'Connected' : 'Disconnected'}>
      <span className={`status-dot ${connected ? '' : 'disconnected'}`} />
      <span>{connected ? 'Live' : 'Offline'}</span>
      <span className="mono" style={{ marginLeft: 8, fontSize: '0.65rem', color: 'var(--text-dim)' }}>
        {formatDistanceToNow(new Date(lastUpdate), { addSuffix: true })}
      </span>
    </div>
  );
}

function RegimeCard({ regime }: { regime: Regime | null }) {
  if (!regime) return <div className="card skeleton" style={{ height: 120 }} />;

  const regimeColors: Record<string, string> = {
    STRONG_BULL: 'var(--success)', BULL: 'var(--success)', NEUTRAL_BULL: 'var(--success)',
    NEUTRAL: 'var(--text-muted)', NEUTRAL_BEAR: 'var(--warning)',
    BEAR: 'var(--danger)', STRONG_BEAR: 'var(--danger)',
    HIGH_VOL: 'var(--danger)', LOW_VOL: 'var(--accent)'
  };
  const color = regimeColors[regime.state] || 'var(--text-muted)';

  return (
    <div className="card">
      <div className="card-header">
        <span className="card-title">
          <Globe className="logo-icon" style={{ color }} />
          Market Regime
        </span>
        <span className="card-badge" style={{ background: `${color}15`, color }}>
          {regime.state}
        </span>
      </div>
      <div className="card-body">
        <div className="card-row">
          <span className="card-label">Confidence</span>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <div className="progress-bar" style={{ width: 120 }}>
              <div className="progress-fill progress-success" style={{ width: `${regime.confidence}%` }} />
            </div>
            <span className="card-value mono">{regime.confidence.toFixed(1)}%</span>
          </div>
        </div>
        <div className="card-row">
          <span className="card-label">Breadth</span>
          <span className="card-value mono">{regime.breadth_pct >= 0 ? '+' : ''}{regime.breadth_pct.toFixed(1)}%</span>
        </div>
        <div className="card-row">
          <span className="card-label">Weighted Change</span>
          <span className="card-value mono {'positive' : ''}{regime.weighted_chg.toFixed(2)}%</span>
        </div>
        <div className="card-row">
          <span className="card-label">Vol Regime</span>
          <span className="card-badge badge-warm">{regime.vol_regime}</span>
        </div>
        <div className="card-row">
          <span className="card-label">Persistence</span>
          <span className="card-value mono">{(regime.persistence * 100).toFixed(0)}%</span>
        </div>
      </div>
    </div>
  );
}

function NarrativeList({ narratives }: { narratives: Narrative[] }) {
  if (!narratives?.length) return <div className="empty-state"><Sparkles className="empty-icon" /><p className="empty-title">No Active Narratives</p><p className="empty-desc">No strong sector rotations detected</p></div>;

  return (
    <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 0 }}>
      <div className="card-header">
        <span className="card-title">
          <Sparkles className="logo-icon" style={{ color: 'var(--purple)' }} />
          Active Narratives
        </span>
        <span className="card-badge badge-purple">{narratives.length}</span>
      </div>
      <div className="card-body" style={{ padding: 0, gap: 0 }}>
        {narratives.map((n) => (
          <div key={n.name} className="card-row" style={{ padding: '12px 16px', borderBottom: '1px solid var(--border)', flex: 1 }}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4, flex: 1, minWidth: 0 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span className="card-badge badge-purple" style={{ fontSize: '0.6rem', padding: '2px 6px' }}>{n.category.toUpperCase()}</span>
                <span className="card-title" style={{ fontSize: '0.85rem' }}>{n.name}</span>
              </div>
              <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
                <span className="card-label">Momentum</span>
                <span className="card-value mono positive">{n.momentum_score.toFixed(0)}</span>
              </div>
              <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginTop: 4 }}>
                <span className="card-label">Coins</span>
                <span className="card-value mono">{n.top_coins.slice(0, 3).join(', ')}{n.top_coins.length > 3 ? '…' : ''}</span>
              </div>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 4, minWidth: 120 }}>
              <div className="progress-bar" style={{ width: 100 }}><div className="progress-fill progress-purple" style={{ width: `${n.momentum_score}%` }} /></div>
              <span className="card-label">Confidence: {n.confidence.toFixed(0)}%</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function MLStatusCard({ ml }: { ml: MLStatus | null }) {
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
          <span className="card-label">Status</span>
          <span className={`card-badge ${ml.trained ? 'badge-high' : 'badge-low'}`}>
            {ml.trained ? 'TRAINED' : 'UNTRAINED'}
          </span>
        </div>
        <div className="card-row">
          <span className="card-label">Accuracy</span>
          <span className="card-value mono">{(ml.val_accuracy * 100).toFixed(1)}%</span>
        </div>
        <div className="card-row">
          <span className="card-label">AUC Score</span>
          <span className="card-value mono">{(ml.val_auc * 100).toFixed(1)}%</span>
        </div>
        <div className="card-row">
          <span className="card-label">Training Samples</span>
          <span className="card-value mono">{ml.train_samples.toLocaleString()}</span>
        </div>
        {ml.last_trained && (
          <div className="card-row">
            <span className="card-label">Last Retrained</span>
            <span className="card-value mono" style={{ fontSize: '0.7rem' }}>{formatDistanceToNow(new Date(ml.last_trained), { addSuffix: true })}</span>
          </div>
        )}
      </div>
    </div>
  );
}

function PortfolioCard({ portfolio }: { portfolio: Portfolio | null }) {
  if (!portfolio) return <div className="card skeleton" style={{ height: 140 }} />;

  return (
    <div className="card">
      <div className="card-header">
        <span className="card-title">
          <PieChart className="logo-icon" style={{ color: 'var(--accent)' }} />
          Portfolio
        </span>
        <span className="card-badge badge-high">{portfolio.open_positions} Open</span>
      </div>
      <div className="card-body">
        <div className="card-row">
          <span className="card-label">Total Exposure</span>
          <span className="card-value mono">${portfolio.total_exposure.toLocaleString()}</span>
        </div>
        <div className="card-row">
          <span className="card-label">Total Risk</span>
          <span className="card-value mono">${portfolio.total_risk.toLocaleString()} ({(portfolio.total_risk / portfolio.total_exposure * 100).toFixed(2)}%)</span>
        </div>
        <div className="card-row">
          <span className="card-label">Daily P&L</span>
          <span className={`card-value mono ${portfolio.daily_pnl >= 0 ? 'positive' : 'negative'}`}>
            {portfolio.daily_pnl >= 0 ? '+' : ''}${portfolio.daily_pnl.toFixed(2)}
          </span>
        </div>
        <div className="card-row">
          <span className="card-label">Max Drawdown</span>
          <span className="card-value mono negative">${portfolio.max_drawdown.toFixed(2)}%</span>
        </div>
        <div className="card-row">
          <span className="card-label">Sectors</span>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {Object.entries(portfolio.sector_exposure).map(([sector, value]) => (
              <span key={sector} className="card-badge badge-cool" style={{ fontSize: '0.6rem' }}>
                {sector}: ${value.toLocaleString()}
              </span>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

function SignalCard({ signal }: { signal: Signal }) {
  const setupIcons: Record<string, React.ReactNode> = {
    BREAKOUT: <TrendingUp style={{ color: 'var(--success)' }} />,
    MOMENTUM: <Zap style={{ color: 'var(--accent)' }} />,
    PULLBACK: <TrendingDown style={{ color: 'var(--warning)' }} />,
    REVERSAL: <ArrowUpRight style={{ color: 'var(--purple)' }} />
  };

  const pumpColors: Record<string, string> = { HIGH: 'var(--success)', MEDIUM: 'var(--warning)', LOW: 'var(--text-muted)' };
  const pumpColor = pumpColors[signal.pump_tier || 'LOW'] || 'var(--text-muted)';

  return (
    <div className="card" style={{ position: 'relative' }}>
      <div className="card-header">
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          {setupIcons[signal.setup_type] || <Zap />}
          <span className="card-title">{signal.coin}</span>
          <span className="card-badge" style={{ background: `${pumpColor}15`, color: pumpColor }}>
            {signal.pump_tier || 'UNKNOWN'}
          </span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span className="card-badge" style={{ fontSize: '0.6rem', background: `var(--${signal.interval.includes('m') ? 'accent' : signal.interval.includes('h') ? 'warning' : 'purple'})15`, color: `var(--${signal.interval.includes('m') ? 'accent' : signal.interval.includes('h') ? 'warning' : 'purple'})` }}>
            {signal.interval}
          </span>
          <span className={`card-badge ${signal.ml_win_prob && signal.ml_win_prob > 0.6 ? 'badge-high' : signal.ml_win_prob && signal.ml_win_prob < 0.5 ? 'badge-low' : 'badge-medium'}`}>
            {signal.ml_prediction || '—'}
          </span>
        </div>
      </div>
      <div className="card-body">
        <div className="card-row">
          <span className="card-label">Entry / Stop / Target</span>
          <span className="card-value mono">
            ${signal.entry.toLocaleString()} / ${signal.stop.toLocaleString()} / ${signal.target.toLocaleString()}
          </span>
        </div>
        <div className="card-row">
          <span className="card-label">R:R / Potential</span>
          <span className="card-value mono">{signal.rr.toFixed(2)}R / +{signal.potential_pct.toFixed(1)}%</span>
        </div>
        <div className="card-row">
          <span className="card-label">Score / Quality</span>
          <span className="card-value mono">{signal.score.toFixed(0)} / {signal.trade_quality.toFixed(1)}</span>
        </div>
        <div className="card-row">
          <span className="card-label">ML Win Prob</span>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <div className="progress-bar" style={{ width: 100 }}>
              <div className="progress-fill progress-success" style={{ width: `${(signal.ml_win_prob || 0.5) * 100}%` }} />
            </div>
            <span className="card-value mono">{((signal.ml_win_prob || 0.5) * 100).toFixed(0)}%</span>
          </div>
        </div>
        <div className="card-row">
          <span className="card-label">Regime / Narrative</span>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span className="card-badge badge-cool" style={{ fontSize: '0.6rem' }}>{signal.market_regime || '—'}</span>
            {signal.narrative_active && (
              <span className="card-badge badge-purple" style={{ fontSize: '0.6rem' }}>
                {signal.narrative_category} ({signal.narrative_momentum?.toFixed(0)})
              </span>
            )}
          </div>
        </div>
        {signal.derivatives_signals?.length && (
          <div className="card-row">
            <span className="card-label">Derivatives</span>
            <span className="card-value" style={{ fontSize: '0.7rem' }}>{signal.derivatives_signals.slice(0, 2).join(' · ')}</span>
          </div>
        )}
        <div className="card-row">
          <span className="card-label">Cluster</span>
          <span className={`card-badge ${signal.is_cluster_rep ? 'badge-high' : 'badge-medium'}`} style={{ fontSize: '0.6rem' }}>
            {signal.is_cluster_rep ? 'Representative' : `Member of ${signal.correlation_cluster}`}
          </span>
        </div>
      </div>
    </div>
  );
}

function SignalList({ signals }: { signals: Signal[] }) {
  if (!signals?.length) return <div className="empty-state"><Target className="empty-icon" /><p className="empty-title">No Active Signals</p><p className="empty-desc">Waiting for quality setups...</p></div>;

  return (
    <div className="grid">
      {signals.map((s) => (
        <SignalCard key={`${s.coin}-${s.interval}-${s.setup_type}`} signal={s} />
      ))}
    </div>
  );
}

function PositionCard({ position }: { position: Position }) {
  const pnlColor = position.pnl_pct >= 0 ? 'positive' : 'negative';

  return (
    <div className="card">
      <div className="card-header">
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span className="card-title">{position.coin}</span>
          <span className={`card-badge ${pnlColor === 'positive' ? 'badge-high' : 'badge-low'}`}>
            {position.status}
          </span>
        </div>
        <span className={`card-value mono ${pnlColor}`}>
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
          <span className="card-label">Size / R:R</span>
          <span className="card-value mono">${position.size_usdt.toLocaleString()} / {position.rr.toFixed(2)}R</span>
        </div>
        <div className="card-row">
          <span className="card-label">P&L</span>
          <span className={`card-value mono ${pnlColor}`}>
            {position.pnl_usdt >= 0 ? '+' : ''}${position.pnl_usdt.toFixed(2)}
          </span>
        </div>
        <div className="card-row">
          <span className="card-label">Progress</span>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, width: '100%' }}>
            <span className="card-badge" style={{ fontSize: '0.6rem' }}>{position.tp1_hit ? 'TP1 ✓' : 'TP1'}</span>
            <span className="card-badge" style={{ fontSize: '0.6rem' }}>{position.tp2_hit ? 'TP2 ✓' : 'TP2'}</span>
            <span className="card-badge" style={{ fontSize: '0.6rem' }}>{position.trailing_activated ? 'Trailing ✓' : 'Trail'}</span>
          </div>
        </div>
        <div className="card-row">
          <span className="card-label">Held</span>
          <span className="card-value mono" style={{ fontSize: '0.7rem' }}>{formatDistanceToNow(new Date(position.entry_time), { addSuffix: true })}</span>
        </div>
      </div>
    </div>
  );
}

function PositionList({ positions }: { positions: Position[] }) {
  if (!positions?.length) return <div className="empty-state"><PieChart className="empty-icon" /><p className="empty-title">No Open Positions</p><p className="empty-desc">Signals will appear here when executed</p></div>;

  return (
    <div className="grid-compact">
      {positions.map((p) => (
        <PositionCard key={p.coin} position={p} />
      ))}
    </div>
  );
}

function SignalChart({ signals }: { signals: Signal[] }) {
  if (!signals?.length) return null;

  // Prepare data for chart - group by coin
  const data = signals.slice(0, 10).map((s, i) => ({
    name: s.coin,
    quality: s.trade_quality,
    potential: s.potential_pct,
    rr: s.rr,
    score: s.score,
    ml: (s.ml_win_prob || 0.5) * 100,
    pump: s.pump_score || 0
  }));

  return (
    <div className="card">
      <div className="card-header">
        <span className="card-title">
          <BarChart2 className="logo-icon" style={{ color: 'var(--accent)' }} />
          Signal Quality Distribution
        </span>
      </div>
      <div className="card-body">
        <div className="chart-container">
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={data} layout="vertical">
              <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
              <BarXAxis type="number" tick={{ fill: 'var(--text-muted)', fontSize: 10 }} />
              <YAxis dataKey="name" type="category" tick={{ fill: 'var(--text)', fontSize: 11 }} width={60} />
              <Tooltip
                contentStyle={{ background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 6 }}
                labelStyle={{ color: 'var(--text-muted)', fontSize: 10 }}
                formatter={(value: number, name: string) => [value.toFixed(1), name]}
              />
              <Legend />
              <Bar dataKey="quality" name="Quality" fill="var(--accent)" radius={[0, 4, 4, 0]} />
              <Bar dataKey="potential" name="Potential %" fill="var(--success)" radius={[0, 4, 4, 0]} />
              <Bar dataKey="ml" name="ML %" fill="var(--purple)" radius={[0, 4, 4, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  );
}

function RegimeChart({ regime }: { regime: Regime | null }) {
  if (!regime) return null;

  const data = [
    { name: 'Confidence', value: regime.confidence, color: 'var(--accent)' },
    { name: 'Breadth', value: (regime.breadth_pct + 100) / 2, color: 'var(--success)' },
    { name: 'Persistence', value: regime.persistence * 100, color: 'var(--purple)' },
    { name: 'Weighted Chg', value: Math.min(100, Math.max(0, (regime.weighted_chg + 10) * 5)), color: 'var(--warning)' }
  ];

  return (
    <div className="card">
      <div className="card-header">
        <span className="card-title">
          <PieChart className="logo-icon" style={{ color: 'var(--purple)' }} />
          Regime Metrics
        </span>
      </div>
      <div className="card-body">
        <div className="chart-container">
          <ResponsiveContainer width="100%" height={200}>
            <Pie data={data} cx="50%" cy="50%" innerRadius={60} outerRadius={80} label={({ name, percent }) => `${name} ${(percent * 100).toFixed(0)}%`}>
              {data.map((entry, index) => (
                <Cell key={`cell-${index}`} fill={entry.color} />
              ))}
            </Pie>
            <Tooltip contentStyle={{ background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 6 }} />
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  );
}

function MLPerformanceChart({ ml }: { ml: MLStatus | null }) {
  if (!ml || !ml.trained) return null;

  // Generate mock historical performance data
  const data = Array.from({ length: 30 }, (_, i) => ({
    day: `Day ${30 - i}`,
    accuracy: Math.max(0.5, Math.min(0.9, ml.val_accuracy + (Math.random() - 0.5) * 0.15)),
    auc: Math.max(0.5, Math.min(0.95, ml.val_auc + (Math.random() - 0.5) * 0.1))
  }).reverse();

  return (
    <div className="card">
      <div className="card-header">
        <span className="card-title">
          <Brain className="logo-icon" style={{ color: 'var(--purple)' }} />
          ML Performance (30d)
        </span>
      </div>
      <div className="card-body">
        <div className="chart-container">
          <ResponsiveContainer width="100%" height={200}>
            <LineChart data={data}>
              <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
              <XAxis dataKey="day" tick={{ fill: 'var(--text-muted)', fontSize: 10 }} />
              <YAxis tick={{ fill: 'var(--text-muted)', fontSize: 10 }} domain={[0.4, 1]} />
              <Tooltip
                contentStyle={{ background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 6 }}
                labelStyle={{ color: 'var(--text-muted)', fontSize: 10 }}
              />
              <Legend />
              <Line type="monotone" dataKey="accuracy" name="Accuracy" stroke="var(--accent)" strokeWidth={2} dot={false} />
              <Line type="monotone" dataKey="auc" name="AUC" stroke="var(--purple)" strokeWidth={2} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  );
}

function SectorExposureChart({ portfolio }: { portfolio: Portfolio | null }) {
  if (!portfolio?.sector_exposure) return null;

  const data = Object.entries(portfolio.sector_exposure).map(([name, value]) => ({
    name, value
  }));

  return (
    <div className="card">
      <div className="card-header">
        <span className="card-title">
          <PieChart className="logo-icon" style={{ color: 'var(--accent)' }} />
          Sector Exposure
        </span>
      </div>
      <div className="card-body">
        <div className="chart-container">
          <ResponsiveContainer width="100%" height={200}>
            <Pie data={data} cx="50%" cy="50%" innerRadius={60} outerRadius={80}
              label={({ name, percent }) => `${name} ${(percent * 100).toFixed(0)}%`}
            >
              {data.map((entry, index) => (
                <Cell key={`cell-${index}`} fill={`hsl(${index * 60}, 70%, 50%)`} />
              ))}
            </Pie>
            <Tooltip contentStyle={{ background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 6 }} />
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  );
}

// ─── Main App ───────────────────────────────────────────────────────
export default function App() {
  const state = useDashboardState();
  const { isConnected } = useWebSocket(useCallback(() => {}, []));
  const [activeTab, setActiveTab] = useState<'signals' | 'positions' | 'analytics' | 'settings'>('signals');

  const tabs = [
    { id: 'signals', label: 'Signals', icon: Target, count: state.signals.length },
    { id: 'positions', label: 'Positions', icon: PieChart, count: state.positions.length },
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
            <span>Crypto Signals</span>
          </div>
          <StatusIndicator connected={isConnected} lastUpdate={state.last_update} />
        </div>
        <div className="header-right">
          <div style={{ display: 'flex', gap: 8 }}>
            {tabs.map((tab) => (
              <button
                key={tab.id}
                className={`btn ${activeTab === tab.id ? 'btn-primary' : 'btn-secondary'}`}
                onClick={() => setActiveTab(tab.id as any)}
                style={{ display: 'flex', alignItems: 'center', gap: 6 }}
              >
                <tab.icon size={16} />
                {tab.label} {tab.count > 0 && <span className="mono" style={{ fontSize: '0.7rem', background: 'rgba(0,0,0,0.2)', padding: '1px 6px', borderRadius: 10 }}>{tab.count}</span>
              </button>
            ))}
          </div>
          <button className="btn btn-ghost" title="Refresh" onClick={() => window.location.reload()}>
            <RefreshCw size={18} />
          </button>
        </div>
      </header>

      {/* Main Content */}
      <div className="main">
        {/* Sidebar */}
        <aside className="sidebar">
          {/* Regime */}
          <div className="sidebar-section">
            <span className="sidebar-title">Market Regime</span>
            <RegimeCard regime={state.regime} />
          </div>

          {/* ML Status */}
          <div className="sidebar-section">
            <span className="sidebar-title">ML Classifier</span>
            <MLStatusCard ml={state.ml_status} />
          </div>

          {/* Portfolio */}
          <div className="sidebar-section">
            <span className="sidebar-title">Portfolio</span>
            <PortfolioCard portfolio={state.portfolio} />
          </div>

          {/* Narratives */}
          <div className="sidebar-section">
            <span className="sidebar-title">Narratives</span>
            <NarrativeList narratives={state.narratives} />
          </div>
        </aside>

        {/* Main Content */}
        <div className="content">
          <div className="content-header">
            <h1 className="content-title">
              {activeTab === 'signals' && 'Live Signals'}
              {activeTab === 'positions' && 'Open Positions'}
              {activeTab === 'analytics' && 'Analytics'}
              {activeTab === 'settings' && 'Settings'}
            </h1>
            <div className="content-actions">
              {activeTab === 'signals' && (
                <>
                  <button className="btn btn-secondary" title="Filter"><ChevronDown size={16} /> Filter</button>
                  <button className="btn btn-secondary" title="Export"><ExternalLink size={16} /></button>
                </>
              )}
            </div>
          </div>

          {activeTab === 'signals' && (
            <>
              <SignalChart signals={state.signals} />
              <SignalList signals={state.signals} />
            </>
          )}

          {activeTab === 'positions' && (
            <PositionList positions={state.positions} />
          )}

          {activeTab === 'analytics' && (
            <div className="grid">
              <SignalChart signals={state.signals} />
              <RegimeChart regime={state.regime} />
              <MLPerformanceChart ml={state.ml_status} />
              <SectorExposureChart portfolio={state.portfolio} />
            </div>
          )}

          {activeTab === 'settings' && (
            <div className="grid" style={{ maxWidth: 800, margin: '0 auto' }}>
              <div className="card">
                <div className="card-header"><span className="card-title">Configuration</span></div>
                <div className="card-body">
                  <div className="card-row">
                    <span className="card-label">Auto Refresh</span>
                    <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}>
                      <input type="checkbox" defaultChecked />
                      <span className="card-label">Enabled</span>
                    </label>
                  </div>
                  <div className="card-row">
                    <span className="card-label">Refresh Interval</span>
                    <select className="btn btn-secondary" style={{ width: 'auto' }}>
                      <option>10 seconds</option>
                      <option>30 seconds</option>
                      <option>60 seconds</option>
                    </select>
                  </div>
                  <div className="card-row">
                    <span className="card-label">Min Signal Quality</span>
                    <input type="range" min="0" max="100" defaultValue={70} className="progress-bar" style={{ width: 200, height: 6, appearance: 'none', background: 'var(--border)' }} />
                  </div>
                </div>
              </div>
              <div className="card">
                <div className="card-header"><span className="card-title">Notifications</span></div>
                <div className="card-body">
                  <div className="card-row">
                    <span className="card-label">Telegram Alerts</span>
                    <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}>
                      <input type="checkbox" defaultChecked />
                      <span className="card-label">Enabled</span>
                    </label>
                  </div>
                  <div className="card-row">
                    <span className="card-label">Min Quality for Alert</span>
                    <input type="range" min="50" max="100" defaultValue={75} className="progress-bar" style={{ width: 200, height: 6, appearance: 'none', background: 'var(--border)' }} />
                  </div>
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Footer */}
        <footer className="footer">
          <div>Crypto Signal Dashboard v1.0.0</div>
          <div className="footer-links">
            <a href="#" className="footer-link">Documentation</a>
            <a href="#" className="footer-link">API Reference</a>
            <a href="#" className="footer-link">GitHub</a>
          </div>
        </footer>
      </div>
    </div>
  );
}