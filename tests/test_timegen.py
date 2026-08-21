"""Time-sampling toolbox — data-model middle field (Ring 2, #33).

The sampling logic (weighted hourly choice + intra-hour jitter) is scenario-agnostic and
deterministic from ``(rng seed, run_date, window, n)`` — those are the parameters. The
diurnal/weekly weight curves are the canonical business-day priors, shared as module
constants. These lock determinism and the constants' role in the draw.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from langfuse_synth_core import timegen
from langfuse_synth_core.rng import Rng

RUN_DATE = datetime(2026, 6, 9, 12, 0, 0, tzinfo=timezone.utc)


def test_iso_is_millisecond_z_format():
    assert timegen.iso(datetime(2026, 6, 9, 12, 0, 0, 123000, tzinfo=timezone.utc)) == \
        "2026-06-09T12:00:00.123Z"


def test_sample_timestamps_is_deterministic_and_sorted():
    a = timegen.sample_timestamps(Rng(42), RUN_DATE, 30, 200)
    b = timegen.sample_timestamps(Rng(42), RUN_DATE, 30, 200)
    assert a == b
    assert a == sorted(a)
    assert len(a) == 200
    # Every draw lands inside the backdated window [window_start, run_date + 1h jitter).
    start = timegen.window_start(RUN_DATE, 30)
    assert all(ts >= start for ts in a)


def test_sample_timestamps_varies_with_seed():
    a = timegen.sample_timestamps(Rng(42), RUN_DATE, 30, 200)
    b = timegen.sample_timestamps(Rng(43), RUN_DATE, 30, 200)
    assert a != b


def test_business_day_curve_biases_the_draw_toward_working_hours():
    # The weighted draw must concentrate in business hours, not spread uniformly — the
    # canonical curve is doing real work in the sampler.
    ts = timegen.sample_timestamps(Rng(1), RUN_DATE, 30, 1000)
    in_business_hours = sum(1 for t in ts if 8 <= t.hour <= 17)
    assert in_business_hours / len(ts) > 0.6  # heavily skewed to the working day


def test_hour_weight_is_the_diurnal_times_weekly_constant():
    dt = datetime(2026, 6, 10, 9, 0, 0, tzinfo=timezone.utc)  # Wednesday 09:00
    assert timegen.hour_weight(dt) == timegen.DIURNAL[9] * timegen.WEEKLY[2]


def test_day_anchor_and_window_start_are_utc_midnight_aligned():
    ws = timegen.window_start(RUN_DATE, 30)
    assert (ws.hour, ws.minute, ws.second, ws.microsecond) == (0, 0, 0, 0)
    anchor = timegen.day_anchor(RUN_DATE, -7)
    assert anchor == datetime(2026, 6, 2, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# The as-of date → run anchor resolution (portal #229)
# ---------------------------------------------------------------------------
def test_resolve_run_date_absent_is_the_wall_clock():
    """No as-of date means "now": the CLI path and the no-tether portal path both omit it."""
    before = timegen.now_utc()
    got = timegen.resolve_run_date(None)
    after = timegen.now_utc()
    assert before <= got <= after
    assert got.tzinfo is timezone.utc and got.microsecond == 0


def test_resolve_run_date_pins_a_date_at_noon_utc():
    assert timegen.resolve_run_date(date(2026, 9, 4)) == \
        datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)


def test_resolve_run_date_accepts_the_iso_string_the_portal_sends():
    # `--set generation.as_of_date=2026-09-04` — a kit whose loader keeps the raw string
    # still lands on the same anchor as one whose YAML coercion produced a `date`.
    assert timegen.resolve_run_date("2026-09-04") == timegen.resolve_run_date(date(2026, 9, 4))


def test_resolve_run_date_anchors_a_yaml_timestamp_on_its_day():
    # A config file could carry a full timestamp; the anchor is the DAY at the fixed hour,
    # the same as the portal's date string — one entry point, one answer.
    at_eight = datetime(2026, 9, 4, 8, 30, 0, tzinfo=timezone.utc)
    assert timegen.resolve_run_date(at_eight) == timegen.resolve_run_date(date(2026, 9, 4))


def test_resolve_run_date_never_clamps_a_future_date():
    """A future as-of date is by design (an AE tethers next week's demo); the window simply
    ends on it. No error, no warning, no clamp to today."""
    far = datetime.now(timezone.utc).date() + timedelta(days=3650)
    assert timegen.resolve_run_date(far).date() == far


def test_parse_as_of_date_normalises_every_loader_shape():
    assert timegen.parse_as_of_date(None) is None
    assert timegen.parse_as_of_date("") is None
    assert timegen.parse_as_of_date(date(2026, 9, 4)) == date(2026, 9, 4)
    at_three = datetime(2026, 9, 4, 15, tzinfo=timezone.utc)
    assert timegen.parse_as_of_date(at_three) == date(2026, 9, 4)
    assert timegen.parse_as_of_date("2026-09-04") == date(2026, 9, 4)
    with pytest.raises(ValueError):
        timegen.parse_as_of_date("next tuesday")
    with pytest.raises(ValueError):
        timegen.parse_as_of_date(20260904)
