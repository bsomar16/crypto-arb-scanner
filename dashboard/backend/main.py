#!/usr/bin/env python3
"""
FastAPI WebSocket Dashboard Backend

Real-time crypto signal dashboard with:
- Live signal feed via WebSocket
- REST API for historical data
- Portfolio & position tracking
- Regime & narrative monitoring
- ML model performance
"""

import asyncio
import json
import os
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Set
from dataclasses import dataclass, asdict

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import uvicorn

# Ensure project root is in sys.path
_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
if _root not in sys.path:
    sys.path.insert(0, _root)

# Try to import scanner modules
try:
    from botutil import load_json
    from integration import SignalEnricher, create_enricher
    from regime_strategies import RegimeDetector
    from narrative_tracker import NarrativeTracker
    from ml_classifier import get_ml_classifier
    from risk_management import DynamicRiskManager
    from attribution_dashboard import get_attribution_engine
    from markets import fetch_binance_24h
    SCANNER_AVAILABLE = True
except ImportError:
    SCANNER_AVAILABLE = False
    print("Warning: Scanner modules not available, running in demo mode")

app = FastAPI(title="Crypto Signal Dashboard", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
async def root():
    return {"status": "ok", "service": "crypto-signal-dashboard", "websocket": "/ws"}

@app.get("/health")
async def health():
    return {"status": "healthy"}


# ─── WebSocket Manager ──────────────────────────────────────────────
class ConnectionManager:
    def __init__(self):
        self.active_connections: Set[WebSocket] = set()
        self.subscriptions: Dict[WebSocket, Set[str]] = {}
    
    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.add(websocket)
        self.subscriptions[websocket] = {"signals", "portfolio", "regime", "narratives"}
        print(f"Client connected. Total: {len(self.active_connections)}")
    
    def disconnect(self, websocket: WebSocket):
        self.active_connections.discard(websocket)
        self.subscriptions.pop(websocket, None)
        print(f"Client disconnected. Total: {len(self.active_connections)}")
    
    async def broadcast(self, message: dict, channel: str = "signals"):
        dead = set()
        for ws in self.active_connections:
            if channel in self.subscriptions.get(ws, set()):
                try:
                    await ws.send_json(message)
                except Exception:
                    dead.add(ws)
        for ws in dead:
            self.disconnect(ws)

manager = ConnectionManager()

# ─── Data Models ────────────────────────────────────────────────────
class SignalModel(BaseModel):
    coin: str
    interval: str
    setup_type: str
    entry: float
    stop: float
    target: float
    rr: float
    potential_pct: float
    score: float
    trade_quality: float
    ml_win_prob: Optional[float] = None
    ml_prediction: Optional[str] = None
    market_regime: Optional[str] = None
    regime_confidence: Optional[float] = None
    narrative_active: Optional[bool] = False
    narrative_category: Optional[str] = None
    narrative_momentum: Optional[float] = None
    pump_tier: Optional[str] = None
    pump_score: Optional[float] = None
    timestamp: str
    is_cluster_rep: bool = True

class PositionModel(BaseModel):
    coin: str
    entry: float
    current_price: float
    stop: float
    target: float
    size_usdt: float
    pnl_pct: float
    pnl_usdt: float
    rr: float
    status: str
    tp1_hit: bool = False
    tp2_hit: bool = False
    trailing_activated: bool = False
    entry_time: str

class RegimeModel(BaseModel):
    state: str
    confidence: float
    breadth_pct: float
    weighted_chg: float
    vol_regime: str
    persistence: float
    timestamp: str

class NarrativeModel(BaseModel):
    name: str
    category: str
    momentum_score: float
    price_momentum: float
    top_coins: List[str]
    confidence: float
    catalysts: List[str]

class MLModel(BaseModel):
    model_type: str
    trained: bool
    val_accuracy: float
    val_auc: float
    train_samples: int
    last_trained: Optional[str] = None

class PortfolioSummary(BaseModel):
    total_exposure: float
    total_risk: float
    open_positions: int
    daily_pnl: float
    max_drawdown: float
    sector_exposure: Dict[str, float]

class DashboardState(BaseModel):
    signals: List[SignalModel] = []
    positions: List[PositionModel] = []
    regime: Optional[RegimeModel] = None
    narratives: List[NarrativeModel] = []
    ml_status: Optional[MLModel] = None
    portfolio: Optional[PortfolioSummary] = None
    last_update: str

# ─── Global State ───────────────────────────────────────────────────
dashboard_state = DashboardState(
    signals=[],
    positions=[],
    regime=None,
    narratives=[],
    ml_status=None,
    portfolio=None,
    last_update=datetime.now(timezone.utc).isoformat()
)

enricher = None
regime_detector = None
narrative_tracker = None
ml_classifier = None
risk_manager = None
attribution_engine = None

# ─── Background Tasks ───────────────────────────────────────────────
async def update_dashboard_loop():
    """Periodically fetch and broadcast updated data."""
    while True:
        try:
            await refresh_all_data()
            await manager.broadcast({
                "type": "state_update",
                "data": dashboard_state.model_dump()
            })
        except Exception as e:
            print(f"Dashboard update error: {e}")
        await asyncio.sleep(10)  # Update every 10 seconds

async def refresh_all_data():
    """Refresh all dashboard data from scanner modules."""
    global dashboard_state, enricher, regime_detector, narrative_tracker, ml_classifier, risk_manager, attribution_engine
    
    if not SCANNER_AVAILABLE:
        # Demo data
        dashboard_state.signals = get_demo_signals()
        dashboard_state.positions = get_demo_positions()
        dashboard_state.regime = get_demo_regime()
        dashboard_state.narratives = get_demo_narratives()
        dashboard_state.ml_status = get_demo_ml()
        dashboard_state.portfolio = get_demo_portfolio()
        dashboard_state.last_update = datetime.now(timezone.utc).isoformat()
        return
    
    try:
        # Initialize components if needed
        if enricher is None:
            from config import load_cfg
            cfg = load_cfg()
            enricher = create_enricher(cfg)
            regime_detector = RegimeDetector(cfg)
            narrative_tracker = NarrativeTracker(cfg)
            ml_classifier = get_ml_classifier(cfg)
            risk_manager = DynamicRiskManager(cfg)
            attribution_engine = get_attribution_engine(cfg)
        
        # Fetch live data
        t24 = fetch_binance_24h()
        enricher.update_spot_data(t24)
        
        # Update regime
        regime_info = regime_detector.detect_regime(t24)
        dashboard_state.regime = RegimeModel(**regime_info)
        
        # Update narratives
        narratives = narrative_tracker.scan_narratives(t24)
        dashboard_state.narratives = [
            NarrativeModel(
                name=n.name,
                category=n.category,
                momentum_score=n.momentum_score,
                price_momentum=n.price_momentum,
                top_coins=n.coins[:5],
                confidence=n.confidence,
                catalysts=n.catalysts[:3]
            )
            for n in narratives
        ]
        
        # Update ML status
        dashboard_state.ml_status = MLModel(
            model_type=ml_classifier.model_type,
            trained=ml_classifier.is_trained,
            val_accuracy=ml_classifier.metadata.get("val_accuracy", 0),
            val_auc=ml_classifier.metadata.get("val_auc", 0),
            train_samples=ml_classifier.metadata.get("train_samples", 0),
            last_trained=ml_classifier.metadata.get("trained_at")
        )
        
        # Update portfolio (would come from position manager)
        dashboard_state.portfolio = PortfolioSummary(
            total_exposure=15000,
            total_risk=150,
            open_positions=3,
            daily_pnl=245.50,
            max_drawdown=-120.0,
            sector_exposure={"smart_contract": 5000, "defi": 3000, "l2": 2000}
        )
        
        dashboard_state.last_update = datetime.now(timezone.utc).isoformat()
        
    except Exception as e:
        print(f"Refresh error: {e}")
        if not dashboard_state.signals:
            dashboard_state.signals = get_demo_signals()
            dashboard_state.positions = get_demo_positions()
            dashboard_state.regime = get_demo_regime()
            dashboard_state.narratives = get_demo_narratives()
            dashboard_state.ml_status = get_demo_ml()
            dashboard_state.portfolio = get_demo_portfolio()
            dashboard_state.last_update = datetime.now(timezone.utc).isoformat()

# ─── REST API ───────────────────────────────────────────────────────
@app.get("/api/state", response_model=DashboardState)
async def get_state():
    return dashboard_state

@app.get("/api/signals", response_model=List[SignalModel])
async def get_signals(
    limit: int = Query(50, le=200),
    regime: Optional[str] = None,
    min_quality: float = Query(0, ge=0, le=100)
):
    signals = dashboard_state.signals
    if regime:
        signals = [s for s in signals if s.market_regime == regime]
    if min_quality:
        signals = [s for s in signals if s.trade_quality >= min_quality]
    return signals[:limit]

@app.get("/api/positions", response_model=List[PositionModel])
async def get_positions():
    return dashboard_state.positions

@app.get("/api/regime", response_model=Optional[RegimeModel])
async def get_regime():
    return dashboard_state.regime

@app.get("/api/narratives", response_model=List[NarrativeModel])
async def get_narratives():
    return dashboard_state.narratives

@app.get("/api/ml-status", response_model=Optional[MLModel])
async def get_ml_status():
    return dashboard_state.ml_status

@app.get("/api/portfolio", response_model=Optional[PortfolioSummary])
async def get_portfolio():
    return dashboard_state.portfolio

@app.get("/api/attribution")
async def get_attribution(days: int = Query(30, ge=1, le=90)):
    if not attribution_engine:
        return {"error": "Attribution engine not available"}
    return attribution_engine.generate_report(days)

# ─── WebSocket ──────────────────────────────────────────────────────
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        # Send initial state
        await websocket.send_json({
            "type": "init",
            "data": dashboard_state.model_dump()
        })
        
        while True:
            data = await websocket.receive_json()
            # Handle subscription changes
            if data.get("type") == "subscribe":
                channels = set(data.get("channels", ["signals", "portfolio", "regime", "narratives"]))
                manager.subscriptions[websocket] = channels
            elif data.get("type") == "ping":
                await websocket.send_json({"type": "pong", "timestamp": datetime.now(timezone.utc).isoformat()})
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        print(f"WebSocket error: {e}")
        manager.disconnect(websocket)

# ─── Demo Data (fallback) ───────────────────────────────────────────
def get_demo_signals() -> List[SignalModel]:
    return [
        SignalModel(
            coin="BTC", interval="4h", setup_type="BREAKOUT",
            entry=42150, stop=40900, target=50500, rr=3.6, potential_pct=19.8,
            score=87, trade_quality=92, ml_win_prob=0.78, ml_prediction="WIN",
            market_regime="STRONG_BULL", regime_confidence=92,
            narrative_active=True, narrative_category="store_of_value", narrative_momentum=85,
            pump_tier="HIGH", pump_score=82,
            timestamp=datetime.now(timezone.utc).isoformat(), is_cluster_rep=True
        ),
        SignalModel(
            coin="ETH", interval="1h", setup_type="PULLBACK",
            entry=2580, stop=2510, target=2850, rr=2.8, potential_pct=10.5,
            score=82, trade_quality=88, ml_win_prob=0.72, ml_prediction="WIN",
            market_regime="BULL", regime_confidence=85,
            narrative_active=True, narrative_category="smart_contract", narrative_momentum=78,
            pump_tier="MEDIUM", pump_score=65,
            timestamp=datetime.now(timezone.utc).isoformat(), is_cluster_rep=True
        ),
        SignalModel(
            coin="SOL", interval="15m", setup_type="MOMENTUM",
            entry=98.5, stop=95.2, target=112, rr=3.2, potential_pct=13.7,
            score=79, trade_quality=84, ml_win_prob=0.68, ml_prediction="WIN",
            market_regime="BULL", regime_confidence=80,
            narrative_active=True, narrative_category="smart_contract", narrative_momentum=82,
            pump_tier="HIGH", pump_score=75,
            timestamp=datetime.now(timezone.utc).isoformat(), is_cluster_rep=True
        ),
        SignalModel(
            coin="ARB", interval="4h", setup_type="BREAKOUT",
            entry=1.12, stop=1.06, target=1.45, rr=3.8, potential_pct=29.5,
            score=85, trade_quality=90, ml_win_prob=0.75, ml_prediction="WIN",
            market_regime="BULL", regime_confidence=82,
            narrative_active=True, narrative_category="l2", narrative_momentum=88,
            pump_tier="HIGH", pump_score=78,
            timestamp=datetime.now(timezone.utc).isoformat(), is_cluster_rep=True
        ),
    ]

def get_demo_positions() -> List[PositionModel]:
    return [
        PositionModel(
            coin="BTC", entry=41200, current_price=42150, stop=40900, target=50500,
            size_usdt=3000, pnl_pct=2.3, pnl_usdt=69.0, rr=3.6,
            status="OPEN", tp1_hit=True, tp2_hit=False, trailing_activated=True,
            entry_time=(datetime.now(timezone.utc) - timedelta(hours=12)).isoformat()
        ),
        PositionModel(
            coin="ETH", entry=2520, current_price=2580, stop=2510, target=2850,
            size_usdt=2000, pnl_pct=2.4, pnl_usdt=48.0, rr=2.8,
            status="OPEN", tp1_hit=False, tp2_hit=False, trailing_activated=False,
            entry_time=(datetime.now(timezone.utc) - timedelta(hours=4)).isoformat()
        ),
    ]

def get_demo_regime() -> RegimeModel:
    return RegimeModel(
        state="STRONG_BULL", confidence=92, breadth_pct=68,
        weighted_chg=4.2, vol_regime="NORMAL", persistence=0.85,
        timestamp=datetime.now(timezone.utc).isoformat()
    )

def get_demo_narratives() -> List[NarrativeModel]:
    return [
        NarrativeModel(
            name="AI & Machine Learning", category="ai", momentum_score=87,
            price_momentum=12.4, top_coins=["FET", "RNDR", "TAO", "AKT", "AGIX"],
            confidence=90, catalysts=["NVIDIA earnings", "AI agent launches"]
        ),
        NarrativeModel(
            name="Layer 2 Scaling", category="l2", momentum_score=84,
            price_momentum=8.7, top_coins=["ARB", "OP", "MATIC", "IMX", "ZKSYNC"],
            confidence=85, catalysts=["Mainnet upgrades", "Airdrop announcements"]
        ),
        NarrativeModel(
            name="Real World Assets", category="rwa", momentum_score=72,
            price_momentum=5.3, top_coins=["ONDO", "PENDLE", "MANTLE", "HNT"],
            confidence=75, catalysts=["Treasury launches", "Regulatory clarity"]
        ),
    ]

def get_demo_ml() -> MLModel:
    return MLModel(
        model_type="xgboost", trained=True, val_accuracy=0.71, val_auc=0.84,
        train_samples=1247, last_trained=datetime.now(timezone.utc).isoformat()
    )

def get_demo_portfolio() -> PortfolioSummary:
    return PortfolioSummary(
        total_exposure=15000, total_risk=150, open_positions=3,
        daily_pnl=245.50, max_drawdown=-120.0,
        sector_exposure={"smart_contract": 5000, "defi": 3000, "l2": 2000, "ai": 1500}
    )

# ─── Startup ────────────────────────────────────────────────────────
@app.on_event("startup")
async def startup_event():
    # Initial data load
    await refresh_all_data()
    # Start background update loop
    asyncio.create_task(update_dashboard_loop())

# ─── Main ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")