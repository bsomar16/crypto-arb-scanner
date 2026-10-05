# FINAL HOLDOUT — DO NOT ANALYZE

Declared: 2026-10-05 UTC.

Holdout period: 2026-10-06 00:00 UTC through 2026-11-06 23:59 UTC.

Purpose: forward OOS validation of the frozen production BUY policy and any predeclared candidate that survives the pass rules.

Rules:
- No threshold tuning, feature selection, candidate ranking, or retrospective slicing may use this period before the holdout closes.
- Signal capture may record entry-time features and outcomes for later analysis.
- Production execution remains fail-closed and unchanged.
- A hypothesis declared after the start of this period cannot be evaluated against this holdout as if it were predeclared.
- At the end of the period, analyze the complete frozen dataset once, then compare against the declared baseline.

This holdout is a forward-data collection period, not a backtest window.
