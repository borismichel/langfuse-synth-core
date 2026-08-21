"""Prove the read seam and the live-emission seam against a **real** Langfuse project.

Mocked tests do not verify backend ingestion. This script emits one wall-clock trace through
the live seam (portal #208) and reads it back through the read seam, asserting the whole
story survives the round trip — which is the claim the seams make.

It read the story back on **both** API generations while there were two, and their agreeing
field for field is what made dropping the deprecated arm in #213 a deletion rather than a
change of behaviour. It reads v4 only now, like the seam.

    export LANGFUSE_BASE_URL=https://cloud.langfuse.com
    export LANGFUSE_PUBLIC_KEY=pk-lf-…      # a THROWAWAY project, never a demo project
    export LANGFUSE_SECRET_KEY=sk-lf-…
    python examples/v4_seam_check.py

What it asserts, in order:

1. **Live emission lands.** One trace with a root observation, a nested span and a nested
   generation carrying model / usage / cost — emitted through the Langfuse SDK, at wall
   clock, with no Spool and no ingestor anywhere in the call path.
2. **The read seam reads it back** — `/api/public/v2/observations` and
   `/api/public/v3/scores`: the trace assembled out of its observations, the trace-level
   attributes propagated onto them, the overall input and output on the root observation,
   and the v3 single typed `value` split back into numeric and string.
3. **Scores normalise** — a numeric trace score and a categorical observation score, each
   with its subject flattened.
4. **Experiments read through the Experiments API** — `/api/public/experiments` →
   `/api/public/experiment-items`. Point it at an existing dataset with
   `SEAM_CHECK_DATASET=<name>`, or set `SEAM_CHECK_CREATE_EXPERIMENT=1` to have it create a
   two-item dataset and run a model-free experiment on it first.

It writes a handful of observations and scores into whatever project the keys point at, so
point it at a throwaway one.
"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from langfuse_synth_core import read as read_mod  # noqa: E402
from langfuse_synth_core.live.emit import LiveEmitter  # noqa: E402

BASE = os.environ.get("LANGFUSE_BASE_URL", "").rstrip("/")
STAMP = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
TRACE_NAME = f"seam_check_{STAMP}"
SESSION = f"seam-session-{STAMP}"
FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"{'✓' if ok else '✗'} {label}{(' — ' + detail) if detail else ''}")
    if not ok:
        FAILURES.append(label)


def emit_one_trace() -> str:
    """Emit one live trace through the seam and return its id."""
    emitter = LiveEmitter.from_env(BASE)
    with emitter.trace(TRACE_NAME, user_id="seam_check_user", session_id=SESSION,
                       tags=["seam-check"], environment="staging",
                       input={"ask": "does the seam hold?"}) as trace:
        with trace.span("retrieve", input={"query": "seam"}) as span:
            span.update(output={"hits": 2})
        with trace.generation("answer", model="claude-sonnet-4",
                              input=[{"role": "system", "content": "you are a seam check"}],
                              usage={"input": 120, "output": 34},
                              cost={"input": 0.001, "output": 0.003, "total": 0.004}) as gen:
            gen.update(output="the seam holds")
            generation_id = gen.id
        trace.update(output={"verdict": "ok"})
        trace_id = trace.id

    emitter.score("seam_quality", 0.92, trace_id=trace_id, data_type="NUMERIC",
                  comment="live-emitted, read back through the seam")
    emitter.score("seam_verdict", "pass", observation_id=generation_id, trace_id=trace_id,
                  data_type="CATEGORICAL")
    emitter.flush()
    print(f"· emitted trace {trace_id} (generation {generation_id}) at wall clock")
    return trace_id


def await_trace(reader: read_mod.LangfuseReader, trace_id: str, *, arm: str) -> object | None:
    """Poll until ingestion has caught up (it is asynchronous), or give up loudly.

    Spans and scores land on *different* paths — spans over OTLP, scores as `score-create`
    envelopes on the legacy ingestion endpoint — and they become readable at different
    times: on Cloud the spans were queryable within ~7s while the scores took ~30s more.
    So the wait is for the whole story, not just the first thing to arrive.
    """
    waited = 0
    for attempt in range(14):
        trace = reader.trace(trace_id)
        if trace and len(trace.observations) >= 3 and len(trace.scores) >= 2:
            print(f"· [{arm}] trace + scores readable after ~{waited}s")
            return trace
        time.sleep(5 + attempt)
        waited += 5 + attempt
    if trace:
        print(f"· [{arm}] gave up waiting after ~{waited}s with "
              f"{len(trace.observations)} observation(s), {len(trace.scores)} score(s)")
    return trace


def assert_story(trace, arm: str) -> None:
    check(f"[{arm}] trace assembled", trace is not None and trace.id is not None)
    if trace is None:
        return
    check(f"[{arm}] trace name propagated", trace.name == TRACE_NAME, str(trace.name))
    check(f"[{arm}] session propagated", trace.session_id == SESSION, str(trace.session_id))
    check(f"[{arm}] user propagated", trace.user_id == "seam_check_user", str(trace.user_id))
    check(f"[{arm}] tags propagated", "seam-check" in trace.tags, str(trace.tags))
    check(f"[{arm}] overall input on the root observation",
          bool(trace.input), repr(trace.input)[:80])
    check(f"[{arm}] overall output on the root observation",
          bool(trace.output), repr(trace.output)[:80])
    names = [o.name for o in trace.observations]
    check(f"[{arm}] children nested under the trace",
          {"retrieve", "answer"} <= set(names), str(names))

    gen = next((o for o in trace.observations if o.name == "answer"), None)
    check(f"[{arm}] generation carries its model", gen is not None and gen.model,
          getattr(gen, "model", None) or "")
    check(f"[{arm}] generation carries usage",
          bool(gen and gen.usage_details), str(getattr(gen, "usage_details", None)))
    check(f"[{arm}] generation carries cost",
          bool(gen and (gen.total_cost or gen.cost_details)),
          str(getattr(gen, "total_cost", None)))
    check(f"[{arm}] generation is a child, not a root", bool(gen and not gen.is_root))

    numeric = next((s for s in trace.scores if s.name == "seam_quality"), None)
    check(f"[{arm}] numeric score normalised",
          bool(numeric and abs((numeric.numeric_value or 0) - 0.92) < 1e-6
               and numeric.string_value is None),
          str(getattr(numeric, "numeric_value", None)))
    check(f"[{arm}] numeric score names its trace",
          bool(numeric and numeric.trace_id == trace.id))
    categorical = next((s for s in trace.scores if s.name == "seam_verdict"), None)
    check(f"[{arm}] categorical score normalised",
          bool(categorical and categorical.string_value == "pass"
               and categorical.numeric_value is None),
          str(getattr(categorical, "string_value", None)))
    check(f"[{arm}] observation-scoped score names its observation",
          bool(categorical and categorical.observation_id),
          str(getattr(categorical, "observation_id", None)))

    session = trace.session_id and read_mod.LangfuseReader(BASE).session(trace.session_id)
    check(f"[{arm}] session groups the trace",
          bool(session and trace.id in session.trace_ids),
          str(getattr(session, "trace_ids", None))[:80])


def make_experiment() -> str:
    """Create a two-item dataset and run a model-free experiment on it.

    The task calls no model — an experiment is a *shape* here, not a scenario, and the point
    is to have real dataset-run rows to read back through both arms.
    """
    from langfuse import get_client

    name = f"seam-check-dataset-{STAMP}"
    client = get_client()
    client.create_dataset(name=name, description="throwaway — read-seam check (#208)")
    for i in range(2):
        client.create_dataset_item(dataset_name=name, input={"n": i}, expected_output=str(i))
    dataset = client.get_dataset(name)
    dataset.run_experiment(name=f"seam-check-run-{STAMP}",
                           task=lambda *, item, **_: str(item.input["n"]))
    client.flush()
    print(f"· created dataset {name!r} with one experiment run")
    return name


def check_experiments(arm: str, dataset: str | None) -> None:
    reader = read_mod.LangfuseReader(BASE)
    if not dataset:
        print(f"· [{arm}] experiments: SKIPPED — set SEAM_CHECK_DATASET=<dataset name>, or "
              "SEAM_CHECK_CREATE_EXPERIMENT=1 to make one, to exercise this arm")
        return
    runs = []
    for attempt in range(8):          # a fresh run is not instantly listable
        runs = reader.experiments(dataset_name=dataset)
        if runs:
            break
        time.sleep(5 + attempt)
    check(f"[{arm}] dataset runs read", bool(runs), f"{len(runs)} run(s)")
    if runs:
        items = reader.experiment_items(runs[0])
        check(f"[{arm}] run items carry their traces",
              bool(items) and all(i.trace_id for i in items), f"{len(items)} item(s)")


def main() -> int:
    if not (BASE and os.environ.get("LANGFUSE_PUBLIC_KEY")
            and os.environ.get("LANGFUSE_SECRET_KEY")):
        print("set LANGFUSE_BASE_URL / LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY first")
        return 2

    trace_id = emit_one_trace()
    dataset = os.environ.get("SEAM_CHECK_DATASET")
    if not dataset and os.environ.get("SEAM_CHECK_CREATE_EXPERIMENT") == "1":
        dataset = make_experiment()

    arm = "v4"
    reader = read_mod.LangfuseReader(BASE)
    trace = await_trace(reader, trace_id, arm=arm)
    if trace is None:
        check(f"[{arm}] trace readable", False,
              "not visible after ~90s — ingestion lag, or the keys point somewhere else")
    else:
        assert_story(trace, arm)
        check_experiments(arm, dataset)

    print()
    if FAILURES:
        print(f"FAILED ({len(FAILURES)}): " + "; ".join(FAILURES))
        return 1
    print("PASSED — both seams verified against a real Langfuse project")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
