export interface SignalModel {
  coin: string;
  interval: string;
  setup_type: string;
  entry: number;
  stop: number;
  target: number;
  rr: number;
  potential_pct: number;
  score: number;
  trade_quality: number;
  ml_win_prob?: number;
  ml_prediction?: string;
  market_regime?: string;
  regime_confidence?: number;
  narrative_active?: boolean;
  narrative_category?: string;
  narrative_momentum?: number;
  pump_tier?: string;
  pump_score?: number;
  timestamp: string;
  is_cluster_rep: boolean;
  derivatives_signals?: string[];
  correlation_cluster?: number;
  is_cluster_rep?: boolean;
}

export interface PositionModel {
  coin: string;
  entry: number;
  current_price: number;
  stop: number;
  target: number;
  size_usdt: number;
  pnl_pct: number;
  pnl_usdt: number;
  rr: number;
  status: string;
  tp1_hit: boolean;
  tp2_hit: boolean;
  trailing_activated: boolean;
  entry_time: string;
}

export interface RegimeModel {
  state: string;
  confidence: number;
  breadth_pct: number;
  weighted_chg: number;
  vol_regime: string;
  persistence: number;
  timestamp: string;
}

export interface NarrativeModel {
  name: string;
  category: string;
  momentum_score: number;
  price_momentum: number;
  top_coins: string[];
  confidence: number;
  catalysts: string[];
}

export interface MLModel {
  model_type: string;
  trained: boolean;
  val_accuracy: number;
  val_auc: number;
  train_samples: number;
  last_trained?: string;
}

export interface PortfolioSummary {
  total_exposure: number;
  total_risk: number;
  open_positions: number;
  daily_pnl: number;
  max_drawdown: number;
  sector_exposure: Record<string, number>;
}

export interface DashboardState {
  signals: SignalModel[];
  positions: PositionModel[];
  regime: RegimeModel | null;
  narratives: NarrativeModel[];
  ml_status: MLModel | null;
  portfolio: PortfolioSummary | null;
  last_update: string;
}