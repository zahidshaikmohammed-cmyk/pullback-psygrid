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
- CP5 continuous runtime, monitoring, persistence and diagnostic funnel
- CP6 four-panel operator output
- CP7 integration supervisor composing CP5 runtime with CP6 output
- CP8 final verification/parity audit with deterministic conformance checks
- architecture-focused unit and integration tests

## Runtime architecture

**CP2 → CP3 → CP4 → CP5 → CP6 → CP7 → CP8**

- CP2: data ingestion and stock/NIFTY health
- CP3: 5m pullback anatomy and candidate state
- CP4: exact 1m trigger evaluation and one-signal-per-SetupID protection
- CP5: continuous runtime, concurrency, alert-once delivery, monitoring and 15-minute reports
- CP6: operator display only; no strategy decisions
- CP7: application integration/lifecycle boundary; composes exactly one CP5 cycle with one CP6 snapshot and owns the canonical application loop
- CP8: final verification/parity audit; validates the implementation against the locked v1.0 contracts without changing strategy behavior

CP7 and CP8 do not alter strategy mathematics or loosen any signal gate. A zero-signal cycle remains a valid cycle and hunting continues.

## CP8 final verification

Run the final audit directly:

```powershell
python run_cp8.py
```

CP8 verifies timing/no-lookahead, data integrity, indicator availability, pullback/trigger parity, state-machine completeness, CP4/CP5/CP6/CP7 integration contracts, and the no-fabrication rule. A failed audit exits non-zero; CP8 never hides a failed contract.

## Deliberate rule

The engine never fabricates a candle, never converts unavailable data into TRUE/FALSE, and never generates a signal merely because the scanner is quiet.

## Endpoint availability

The supplied IP endpoints were configured exactly as supplied. If the server is unreachable, the engine records endpoint failure and continues; it does not treat the failure as a trading signal.

## Test

```powershell
python -m pip install -r requirements.txt
python -m pytest -q tests
```

The repository CI runs the complete `tests/` suite on every push to `main`.

## Run

Canonical integrated runtime:

```powershell
python run_cp7.py
```

Final verification:

```powershell
python run_cp8.py
```

Lower-level launchers remain available for individual checkpoints. CP7 is the canonical CP5+CP6 application launcher; CP8 is the final verification gate.

The current endpoint host may be unreachable from a build environment; connectivity is therefore reported by the runtime rather than falsely reported as verified.
