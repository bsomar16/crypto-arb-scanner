# KILL LIST — BUY research hypotheses

Research-only record. Rejected hypotheses are not retuned on their spent windows.

| Date | Hypothesis | Data / windows | Result | Decision |
|---|---|---|---|---|
| 2026-10-03 | 4h structure >=50 + BASE expansion | PR #99; W1/W2/W3, 750 bars each | 10/15 pooled, but W1 0/3, W2 10/12, W3 0 signals; single-window cluster | REJECT |
| 2026-10-03 | 4h extension <=2% + bullish order block | PR #97 rolling validation | Pooled candidate 4/10 = 40%; inconsistent by window | REJECT |
| 2026-10-01 | Retest optional | PR #91 OOS | 22/91 = 24.18%, versus baseline 22/67 = 32.84% | REJECT |
| 2026-10-01 | Sweep required | PR #91 OOS | 1/8 = 12.5% | REJECT |
| 2026-10-01 | Score -2 / -4 | PR #90/#91 OOS | No precision improvement; added detections did not add wins | REJECT |
| 2026-10-01 | R:R -0.10 / -0.20 | PR #90/#91 OOS | No precision improvement | REJECT |
| 2026-10-01 | Liquidity -20% / -40% | PR #90/#91 OOS | No change in closed precision | REJECT |
| 2026-10-01 | Volume -5% | PR #90/#91 OOS | No change in closed precision | REJECT |

Contamination rule: PR #99's three rolling windows are spent for the structure >=50 + BASE hypothesis. Do not tune thresholds or interactions against those windows.
