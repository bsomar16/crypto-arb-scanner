# Crypto Signal Dashboard

Real-time WebSocket dashboard for the crypto-arb-scanner.

## Features

- 📡 **Real-time WebSocket** - Live signal feed with auto-reconnect
- 📊 **Live Signal Feed** - Quality-scored signals with ML predictions
- 💼 **Portfolio Tracking** - Open positions with P&L, trailing stops
- 🌍 **Market Regime** - Visual regime detection with metrics
- 🧠 **ML Classifier** - XGBoost/LightGBM win probability
- 📰 **Narrative Tracking** - Sector rotation & catalyst detection
- 📈 **Analytics** - Signal quality, regime, ML performance charts
- 🎨 **Dark Theme** - Professional crypto trading aesthetic

## Quick Start

### Development (Local)

```bash
# Backend
cd dashboard/backend
pip install -r requirements.txt
python main.py

# Frontend (new terminal)
cd dashboard/frontend
npm install
npm run dev
```

Open http://localhost:3000

### Docker (Production)

```bash
# From dashboard directory
docker-compose up -d

# Or with scanner
docker-compose --profile scanner up -d
```

- Backend API: http://localhost:8080
- Frontend: http://localhost:3000
- WebSocket: ws://localhost:8080/ws

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Frontend (React)                      │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────┐  │
│  │  Sidebar    │  │   Main      │  │   Charts        │  │
│  │  - Regime   │  │  - Signals  │  │  - Quality      │  │
│  │  - ML       │  │  - Positions│  │  - Regime       │  │
│  │  - Portfolio│  │  - Analytics│  │  - ML Perf      │  │
│  │  - Narratives            │  │  - Sectors      │  │
│  └─────────────┘  └─────────────┘  └─────────────────┘  │
└─────────────────────────────────────────────────────────┘
                           │
                    WebSocket / REST
                           │
┌─────────────────────────────────────────────────────────┐
│                    Backend (FastAPI)                     │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────┐  │
│  │  WebSocket  │  │   REST API  │  │  Background     │  │
│  │  Manager    │  │  - /api/*   │  │  - Refresh Loop │  │
│  │  - Channels │  │  - /ws      │  │  - Broadcast    │  │
│  └─────────────┘  └─────────────┘  └─────────────────┘  │
└─────────────────────────────────────────────────────────┘
                           │
                    Scanner Modules
                           │
              ┌────────────┴────────────┐
              ▼                         ▼
        Signal Generation          Data Enrichment
        - Discovery                - ML Classifier
        - Signals                  - Regime Detector
        - Positions                - Narrative Tracker
        - Risk Management          - Derivatives
                                    - Attribution
```

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/state` | Full dashboard state |
| GET | `/api/signals` | Active signals (filterable) |
| GET | `/api/positions` | Open positions |
| GET | `/api/regime` | Current market regime |
| GET | `/api/narratives` | Active narratives |
| GET | `/api/ml-status` | ML classifier status |
| GET | `/api/portfolio` | Portfolio summary |
| GET | `/api/attribution?days=30` | Factor attribution |
| WS | `/ws` | Real-time updates |

## WebSocket Protocol

```javascript
// Connect
const ws = new WebSocket('ws://localhost:8080/ws');

// Subscribe to channels
ws.send(JSON.stringify({
  type: 'subscribe',
  channels: ['signals', 'positions', 'regime', 'narratives']
}));

// Message types received:
// { type: 'init', data: DashboardState }
// { type: 'state_update', data: DashboardState }
// { type: 'signal_new', data: Signal }
// { type: 'signal_update', data: Signal }
// { type: 'position_update', data: Position }
// { type: 'regime_change', data: Regime }
```

## Configuration

Environment variables (`.env`):

```env
# Required
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
GITHUB_TOKEN=
GITHUB_REPOSITORY=bsomar16/crypto-arb-scanner

# Dashboard
VITE_API_URL=http://localhost:8080
PORT=8080

# Optional: Exchange API keys for private endpoints
```

## Development

```bash
# Backend with auto-reload
cd dashboard/backend
uvicorn main:app --reload --port 8080

# Frontend with HMR
cd dashboard/frontend
npm run dev
```

## Deployment

### Vercel (Frontend)
```bash
cd dashboard/frontend
vercel --prod
```

### Railway/Render (Backend)
```bash
# Build command: pip install -r requirements.txt
# Start command: python main.py
# Port: 8080
```

### Docker Swarm/K8s
```bash
docker stack deploy -c docker-compose.yml crypto-dashboard
```

## Charts Library

Using **Recharts** for all visualizations:
- `BarChart` - Signal quality comparison
- `PieChart` - Regime metrics, sector exposure
- `LineChart` - ML performance over time
- `AreaChart` - Signal quality trends
- `ResponsiveContainer` - Auto-resize

## Theming

CSS custom properties in `src/index.css`:
```css
:root {
  --bg: #0a0e17;
  --bg-elevated: #111827;
  --card: #151e2e;
  --accent: #22d3ee;
  --success: #22c55e;
  --warning: #f59e0b;
  --danger: #ef4444;
  --purple: #a855f7;
  --orange: #f97316;
}
```

## Integration with Scanner

The dashboard connects to the scanner via:
1. **Shared config.json** - Single source of truth
2. **Shared state/** directory - Signal history, positions
3. **Python imports** - Direct module access when running locally
4. **REST/WebSocket** - When deployed separately

## Extending

### Add New Chart
```tsx
// In App.tsx analytics tab
import { ScatterChart, Scatter } from 'recharts';

<ResponsiveContainer width="100%" height={200}>
  <ScatterChart data={data}>
    <Scatter dataKey="x" name="Metric" fill="var(--accent)" />
  </ScatterChart>
</ResponsiveContainer>
```

### Add New API Endpoint
```python
# In main.py
@app.get("/api/custom")
async def custom_endpoint(param: str = Query(...)):
    return {"result": process(param)}
```

### Add WebSocket Channel
```python
# In ConnectionManager
channels = {"signals", "positions", "regime", "narratives", "custom"}

# In broadcast
await manager.broadcast({"type": "custom_update", data: data}, channel="custom")
```

## License

MIT - Part of crypto-arb-scanner project.