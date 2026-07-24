"""Time generation: ISO formatting + diurnally/weekly-weighted timestamp sampling.

Two layers live here, both data-model-facing:

* **Formatting** (``iso`` / ``iso_date``) — the Langfuse ingestion API's on-the-wire
  timestamp format. ``iso`` is byte-identical across the gold-standard kits and was
  extracted in Ring 1a; ``events`` calls it to stamp every envelope.
* **Sampling** (``sample_timestamps`` / ``sample_in_range`` / ``hour_weight`` / …) — the
  time *toolbox* the seed path draws backdated timestamps from, so time-series views look
  real (business-hours peaks, overnight troughs, weekend dip). Ring 2 (#33) moved these
  from the EV kit: the *logic* (weighted hourly choice + intra-hour jitter) is
  scenario-agnostic, and the run's inputs (``rng`` / ``run_date`` / ``window_days`` / ``n``)
  are the parameters. The diurnal/weekly weight curves are the canonical business-day priors
  below, shared as module constants.

A kit whose time model is a different *algorithm* (e.g. a sessions-per-day count-driven
sampler rather than this draw-N-over-a-window one) keeps its own sampler — that is the
docs/SEAM.md tie-break (delta is logic, not values), and such a sampler is simply a
different tool the toolbox may also grow, not a re-parametrization of this one. (So the
curves are not threaded as per-call knobs — no kit passes an alternate, and one that needs a
different curve needs a different algorithm; that would be speculative generality here.)
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .rng import Rng

# Diurnal shape: relative weight per local hour (0-23). Peaks mid-morning & mid-afternoon.
DIURNAL = [
    0.05, 0.03, 0.02, 0.02, 0.03, 0.06,  # 00-05 overnight trough
    0.15, 0.35, 0.70, 0.95, 1.00, 0.95,  # 06-11 morning ramp -> peak
    0.80, 0.85, 0.95, 0.90, 0.75, 0.55,  # 12-17 afternoon
    0.40, 0.30, 0.22, 0.16, 0.10, 0.07,  # 18-23 evening wind-down
]
# Weekly shape: Mon..Sun. Weekend dip.
WEEKLY = [1.0, 1.0, 1.05, 1.0, 0.9, 0.45, 0.35]


def iso(dt: datetime) -> str:
    """ISO-8601 with milliseconds and a trailing Z, as the ingestion API expects."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def iso_date(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def now_utc() -> datetime:
    """The single wall-clock read in the whole program — the run anchor.

    Captured once at the start of a command and threaded through as ``run_date`` so the
    rest of the seed path stays deterministic.
    """
    return datetime.now(timezone.utc).replace(microsecond=0)


def hour_weight(dt: datetime) -> float:
    return DIURNAL[dt.hour] * WEEKLY[dt.weekday()]


def window_start(run_date: datetime, window_days: int) -> datetime:
    """Midnight UTC, ``window_days`` before the run date."""
    start = run_date - timedelta(days=window_days)
    return start.replace(hour=0, minute=0, second=0, microsecond=0)


def sample_timestamps(rng: Rng, run_date: datetime, window_days: int, n: int) -> list[datetime]:
    """Return ``n`` timestamps over the window, diurnally/weekly weighted, sorted ascending."""
    start = window_start(run_date, window_days)
    total_hours = window_days * 24
    hours = [start + timedelta(hours=h) for h in range(total_hours)]
    weights = [hour_weight(h) for h in hours]

    rsub = rng.sub("timegen")
    chosen_hours = rsub.choices(hours, weights, k=n)
    out: list[datetime] = []
    for h in chosen_hours:
        jitter = rsub.uniform(0, 3600)  # seconds within the hour
        out.append(h + timedelta(seconds=jitter))
    out.sort()
    return out


def sample_in_range(rng: Rng, start: datetime, end: datetime, n: int, label: str = "range",
                    ramp: float | None = None) -> list[datetime]:
    """Sample ``n`` diurnally/weekly-weighted timestamps within an arbitrary [start, end).

    If ``ramp`` is given (0 < ramp <= 1), multiply in a linear weight rising from
    ``ramp`` at ``start`` to 1.0 at ``end`` — biasing the draw toward ``end`` so the
    resulting volume *climbs* across the range (e.g. an appeal rate trending up to now)."""
    start = start.replace(minute=0, second=0, microsecond=0)
    total_hours = max(1, int((end - start).total_seconds() // 3600))
    hours = [start + timedelta(hours=h) for h in range(total_hours)]
    weights = [hour_weight(h) for h in hours] or [1.0]
    if ramp is not None and total_hours > 1:
        span = total_hours - 1
        weights = [w * (ramp + (1.0 - ramp) * (i / span)) for i, w in enumerate(weights)]
    rsub = rng.sub("timegen", label)
    out = []
    for h in rsub.choices(hours, weights, k=n):
        out.append(h + timedelta(seconds=rsub.uniform(0, 3600)))
    out.sort()
    return out


def in_window(ts: datetime, start: datetime, end: datetime) -> bool:
    return start <= ts < end


def day_anchor(run_date: datetime, day_offset: int) -> datetime:
    """A timestamp ``day_offset`` days from the run date (offset is typically negative)."""
    return (run_date + timedelta(days=day_offset)).replace(microsecond=0)
