# PULLBACK ENGINE — FINAL LOCKED ARCHITECTURE v1.0

This package is the first implementation stage of the locked specification.

## What is implemented here

- ten A–J stock endpoint configuration, 45-stock shards treated as one universe
- separate NIFTY endpoint
- concurrent endpoint fetching
- per-stock fault isolation
- 1-minute OHLCV validation
- deterministic complete 1m -> 5m aggregation
- EMA20 / EMA60
- Wilder ATR14
- session VWAP
- NIFTY regime mathematics
- confirmed pivot mathematics with the p+3 confirmation delay
- impulse/retracement calculations
- canonical A/B/C structure calculations
- trend / early-entry / 1m reacceleration mathematics
- Setup state store and duplicate-trigger protection
- architecture-focused unit tests

## Deliberate rule

The engine never fabricates a candle, never converts unavailable data into TRUE/FALSE, and never generates a signal merely because the scanner is quiet.

## Endpoint availability

The supplied IP endpoints were configured exactly as supplied. If the server is unreachable, the engine records endpoint failure and continues; it does not treat the failure as a trading signal.

## Run

```powershell
python -m pip install -r requirements.txt
python -m pytest -q
python run_engine.py
```

The current endpoint host was unreachable from the build environment during packaging, so live endpoint connectivity is tested by the runtime rather than being falsely reported as verified.
