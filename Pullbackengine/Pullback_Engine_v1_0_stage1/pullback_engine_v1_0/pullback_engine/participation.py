from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Sequence

from .core import Candle


# This is deliberately a NON-BLOCKING diagnostic layer.
# It must never be used as an entry veto by CP3/CP4.
PARTICIPATION_WINDOW = 3
MIN_RECOVERY_FRACTION = 0.15
MIN_RELATIVE_VOLUME = 0.70


@dataclass(frozen=True)
class ParticipationReturn:
    status: str
    direction: str
    checked_candles: int
    baseline_volume: float | None
    lowest_relative_volume: float | None
    current_relative_volume: float | None
    recovery_fraction: float | None
    price_response: bool

    @property
    def detected(self) -> bool:
        return self.status in {"EARLY", "CONFIRMED"}


def detect_early_participation_return(
    candles: Sequence[Candle],
    pullback_start: int,
    pullback_end: int,
    impulse_start: int,
    impulse_end: int,
    direction: str,
    window: int = PARTICIPATION_WINDOW,
) -> ParticipationReturn:
    """Detect an early, modest return of directional participation.

    The calculation is informational only. It does not decide whether a
    setup is eligible and it never vetoes a signal.

    Baseline: median volume of the final three completed impulse candles.
    Pullback: first ``window`` completed pullback candles only.
    A return requires: participation first contracted, then recovered from
    that low, with a small directional price response.
    """
    direction = direction.upper()
    if direction not in {"LONG", "SHORT"}:
        raise ValueError("direction must be LONG or SHORT")

    if window < 1:
        raise ValueError("window must be positive")

    if not (
        0 <= impulse_start <= impulse_end < len(candles)
        and 0 <= pullback_start <= pullback_end < len(candles)
        and impulse_end < pullback_start
    ):
        return ParticipationReturn(
            "INSUFFICIENT_DATA", direction, 0, None, None, None, None, False
        )

    impulse_slice = candles[max(impulse_start, impulse_end - 2) : impulse_end + 1]
    baseline = median(c.volume for c in impulse_slice)
    if baseline <= 0:
        return ParticipationReturn(
            "INSUFFICIENT_DATA", direction, 0, None, None, None, None, False
        )

    rows = list(candles[pullback_start : min(pullback_end + 1, pullback_start + window)])
    if len(rows) < 2:
        return ParticipationReturn(
            "INSUFFICIENT_DATA", direction, len(rows), baseline, None, None, None, False
        )

    rel = [c.volume / baseline for c in rows]
    low = min(rel)

    # We only call it a return if participation had first faded below its
    # impulse baseline and subsequently moved upward by a modest amount.
    contracted = low < 1.0
    best: ParticipationReturn | None = None

    for i in range(1, len(rows)):
        current = rel[i]
        previous = rel[i - 1]
        gap = 1.0 - low
        recovery = (current - low) / gap if gap > 0 else 0.0

        price_response = (
            rows[i].close > rows[i].open and rows[i].close >= rows[i - 1].close
            if direction == "LONG"
            else rows[i].close < rows[i].open and rows[i].close <= rows[i - 1].close
        )

        if (
            contracted
            and current > previous
            and current >= MIN_RELATIVE_VOLUME
            and recovery >= MIN_RECOVERY_FRACTION
            and price_response
        ):
            status = "EARLY" if i == 1 else "CONFIRMED"
            best = ParticipationReturn(
                status,
                direction,
                len(rows),
                baseline,
                low,
                current,
                recovery,
                True,
            )
            break

    if best is not None:
        return best

    return ParticipationReturn(
        "NOT_DETECTED",
        direction,
        len(rows),
        baseline,
        low,
        rel[-1],
        ((rel[-1] - low) / (1.0 - low)) if low < 1.0 else 0.0,
        False,
    )
