# Win Rate Signal Improvements

## Summary

Enhanced the signal generation system to use historical win rate as a primary filter and ranking factor. This ensures only high-probability setups generate alerts.

---

## Changes Made

### 1. **Signal Generation** (`signals.py` → `signals_v2.py`)

#### Win Rate Filter
- **New function**: `_win_rate_filter_and_score()`
- Filters signals below minimum win rate threshold
- Only applies when sufficient samples exist (≥10 by default)
- Adds score boost proportional to win rate (max +20 points)

#### Win Rate Scoring
```
Win Rate → Score Boost:
50% → 0 points
60% → +6.7 points  
70% → +13.3 points
80% → +20 points (max)
```

### 2. **Signal Ranking** (`scanner.py` → `scanner_v2.py`)

#### Enhanced `_rank_trade_candidates()`
- Adds `win_rate_modifier` to trade quality score
- Normalizes win rate: 50% = 0, 80% = 100
- Weights by sample confidence: min(1.0, samples/50)
- Maximum 15 point boost to trade quality

#### Win Rate in Telegram Alerts
New field in message:
```
📊 Win rate: <b>67.5% (23 setup/timeframe)</b>
```

### 3. **Adaptive Thresholds** (`adaptive.py` → `adaptive_v2.py`)

#### Win Rate Based Adaptation
| Win Rate | Mode | Score Delta | Vol Delta | RR Delta |
|----------|------|-------------|-----------|----------|
| ≥75% | RELAXED | +3.0 | +0.06 | +0.06 |
| 70-74% | RELAXED | +1.5 | +0.03 | +0.03 |
| 60-69% | ADAPTIVE | 0 | 0 | 0 |
| 55-59% | ADAPTIVE | -1.5 | -0.03 | -0.03 |
| <55% | TIGHTENED | -3.0 | -0.06 | -0.06 |

#### Win Rate Tiers
- **ELITE** (≥75%): Top tier, maximum relaxation
- **STRONG** (70-74%): Strong performer
- **GOOD** (60-69%): Solid baseline
- **AVERAGE** (55-59%): Minimum viable
- **WEAK** (<55%): Filtered out

### 4. **Configuration** (`config.json` → `config_v2.json`)

```json
// Win rate filter settings
"signal_min_win_rate_pct": 55.0,      // Minimum historical win rate %
"signal_min_win_rate_samples": 10,    // Minimum samples for win rate to apply
"signal_win_rate_weight": 0.2,        // Weight of win rate in scoring (0-1)
"signal_win_rate_boost_max": 20.0,    // Maximum score boost from win rate

// Adaptive thresholds
"adaptive_min_score_floor": 50,
"adaptive_min_vol_x_floor": 1,
"adaptive_min_rr_floor": 1.5
```

---

## Expected Impact

### Before
- 10-50 signals per scan
- Many low-probability setups
- No win rate visibility in alerts

### After
- **2-10 signals per scan** (filtered by win rate)
- **Only ≥55% win rate setups** with 10+ samples
- **Ranked by combined quality + win rate**
- **Win rate displayed in every alert**

### Signal Quality Improvement
| Metric | Before | After |
|--------|--------|-------|
| Avg signal win rate | ~50% | ~65%+ |
| False positive rate | High | Low |
| Alert relevance | Mixed | High |
| User confidence | Low | High |

---

## Deployment

### Files to Replace
```bash
cp signals_v2.py signals.py
cp scanner_v2.py scanner.py
cp adaptive_v2.py adaptive.py
cp config_v2.json config.json
git add signals.py scanner.py adaptive.py config.json
git commit -m "feat: win rate filtering + ranking for buy signals"
git push
```

### Rollback Plan
```bash
git revert HEAD
# Or disable via config:
"signal_min_win_rate_pct": 0
"signal_win_rate_weight": 0
```

---

## Monitoring

Watch for in logs:
```
[BUY AUDIT] rejected: win_rate_filter=3
[BUY] 5 quality signals / 12 qualified signals / 150 deep scans
```

In Telegram alerts:
```
📊 Win rate: <b>72.3% (34 exact setup)</b>
```

In audit history:
```json
{
  "rejected": {"win_rate_filter": 3, "pump_filter": 5, ...}
}
```

---

## Tuning Guide

### More Aggressive (Higher Precision)
```json
"signal_min_win_rate_pct": 60.0,
"signal_min_win_rate_samples": 20,
"signal_win_rate_weight": 0.3
```

### More Permissive (Higher Recall)
```json
"signal_min_win_rate_pct": 50.0,
"signal_min_win_rate_samples": 5,
"signal_win_rate_weight": 0.1
```

### Disable Win Rate Filter
```json
"signal_min_win_rate_pct": 0,
"signal_win_rate_weight": 0
```

---

## How It Works

1. **Signal Generation** → Checks historical win rate for same (coin, interval, setup)
2. **Win Rate Filter** → Rejects if win_rate < 55% AND samples ≥ 10
3. **Score Boost** → Adds 0-20 points to signal score based on win rate
4. **Ranking** → Adds 0-15 points to trade_quality based on win rate
5. **Adaptive Thresholds** → Relaxes/tightens entry criteria by win rate tier
6. **Alert Display** → Shows win rate in Telegram message