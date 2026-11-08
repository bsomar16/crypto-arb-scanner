# Crypto-Arb-Scanner Improvements

## Summary of Changes

This update adds two major features to the crypto-arb-scanner:

### 1. Multi-Exchange Discovery
- **Before**: Only scanned Binance USDT pairs
- **After**: Scans 8 exchanges simultaneously:
  - Binance (primary)
  - Bybit
  - OKX
  - MEXC
  - Gate.io
  - KuCoin
  - HTX (Huobi)
  - Bitget

**Benefits**:
- Discovers coins not listed on Binance yet
- Finds early movers on smaller exchanges before they hit major exchanges
- Aggregates liquidity across exchanges for better volume analysis
- Deduplicates by keeping highest-volume exchange per coin

### 2. Pump Potential Filter (>40% Moves)
- **Before**: Alerted on any signal with >5% potential
- **After**: Only alerts when modelled potential exceeds 40% (configurable)

**Pump Detection Algorithm** analyzes:
1. **Distance from recent high** (room to grow) - 25 points max
2. **Volatility (ATR %)** - 20 points max
3. **Volume acceleration** - 15 points max
4. **Price compression/consolidation** - 15 points max
5. **RSI position** (not overbought) - 20 points max
6. **24h change context** (not already pumped) - 15 points max
7. **15m microstructure momentum** - 15 points max

**Pump Tiers**:
- **HIGH** (≥70): Strong setup for >40% move
- **MEDIUM** (50-69): Moderate potential
- **LOW** (<50): Filtered out

---

## Files Changed

### New/Modified Files
1. **`discovery.py`** - Complete rewrite with multi-exchange support
2. **`scanner.py`** - Added pump filter and multi-exchange discovery integration
3. **`signals.py`** - Added pump potential scoring to each signal
4. **`config.json`** - New configuration options

### Configuration Options (config.json)

```json
// Multi-exchange discovery
"discovery_exchanges": ["BINANCE", "BYBIT", "OKX", "MEXC", "GATE", "KUCOIN", "HTX", "BITGET"],
"discovery_min_quote_volume": 300000,
"discovery_universe_size": 600,
"discovery_deep_candidates": 300,
"discovery_workers": 24,
"discovery_fast_scan_size": 600,
"discovery_min_pump_score": 60,
"discovery_pump_weight": 0.3,

// Pump filter
"signal_min_pump_potential_pct": 40.0,
"signal_require_pump_filter": true
```

### Key Configuration Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `discovery_exchanges` | All 8 | Which exchanges to scan |
| `discovery_min_quote_volume` | 300,000 | Minimum 24h quote volume (USDT) |
| `discovery_min_pump_score` | 60 | Minimum pump score for discovery (0-100) |
| `discovery_pump_weight` | 0.3 | Weight of pump score in discovery ranking |
| `signal_min_pump_potential_pct` | 40.0 | Minimum modelled potential % for alerts |
| `signal_require_pump_filter` | true | Enable/disable pump filter |

---

## Deployment Instructions

### 1. Replace Files in Repository
Copy the improved files to your repository:
```bash
cp crypto-arb-scanner-improved/discovery.py crypto-arb-scanner/
cp crypto-arb-scanner-improved/scanner.py crypto-arb-scanner/
cp crypto-arb-scanner-improved/signals.py crypto-arb-scanner/
cp crypto-arb-scanner-improved/config.json crypto-arb-scanner/
```

### 2. Commit and Push
```bash
cd crypto-arb-scanner
git add discovery.py scanner.py signals.py config.json
git commit -m "feat: multi-exchange discovery + pump potential filter (>40%)"
git push origin main
```

### 3. GitHub Actions
The workflow will automatically use the new code on next scheduled run. No secrets changes needed.

### 4. Testing Locally
```bash
cd crypto-arb-scanner
python scanner.py --mode buy --json-logs
```

---

## Expected Behavior Changes

### Discovery Phase
- **Before**: ~300-600 Binance-only candidates
- **After**: ~500-1000+ candidates from 8 exchanges, deduplicated

### Signal Generation
- **Before**: 10-50 signals per scan (many <20% potential)
- **After**: 2-10 signals per scan (all >40% modelled potential)

### Alert Quality
- **Before**: Many small-move alerts (5-20%)
- **After**: Only high-conviction >40% potential alerts

### Telegram Message Format (New Fields)
```
⚡ Pump potential: HIGH (75/100) · Target: >40%
```
Shows pump tier (HIGH/MEDIUM/LOW) and pump score (0-100).

---

## Performance Considerations

### API Calls
- Discovery: 8 parallel exchange calls (vs 1 before)
- Each candidate: 2-3 klines calls (1h + 15m)
- Total: ~2-3x more API calls, but still well within rate limits

### Rate Limits
- Binance: 1200 weight/min (unchanged)
- Bybit: 100 req/sec
- OKX: 30 req/sec
- MEXC: 50 req/sec
- Gate: 50 req/sec
- KuCoin: 100 req/sec
- HTX: 100 req/sec
- Bitget: 50 req/sec

All well within limits for 600 candidates with 24 workers.

### Execution Time
- Discovery: ~30-60 seconds (parallel)
- Deep scan: ~2-5 minutes (unchanged)
- Total scan: ~3-6 minutes (acceptable for 15-min schedule)

---

## Rollback Plan

If issues arise:
```bash
git revert HEAD
git push origin main
```

Or disable new features via config:
```json
"discovery_exchanges": ["BINANCE"],
"signal_require_pump_filter": false
```

---

## Monitoring

Watch for:
1. **Discovery logs**: `DISCOVERY` prefix shows exchange results
2. **Pump filter logs**: `BUY` prefix shows "Pump filter (>40%): X -> Y signals"
3. **Telegram alerts**: Verify pump tier shows in messages
4. **GitHub Actions**: Check workflow duration and success rate

---

## Future Enhancements

1. **Cross-exchange arbitrage signals** - Leverage multi-exchange prices
2. **New listing detection** - Alert when coins appear on new exchanges
3. **Volume profile analysis** - Better pump timing
4. **On-chain metrics integration** - Whale activity, exchange flows
5. **ML-based pump prediction** - Train on historical >40% moves