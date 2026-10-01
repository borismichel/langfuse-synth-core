# Golden content and process repeatability

Read when generation changes or a determinism check fails. The golden is a small,
reviewable full Spool for pinned inputs; scenario tests separately assert its meaning.
Edit those tests as the story grows. Update golden bytes only through deliberate freezing.

First identify the failure:

- **Scenario assertion:** fix the story or revise an intentionally changed expectation.
- **Golden content mismatch:** inspect why the full payload changed. An accidental change
  needs a code fix; an intended change can be frozen after review.
- **Process repeatability failure:** sort unordered collections and remove unseeded inputs.
  Freezing a different byte sequence does not fix process-dependent generation.
- **Egress failure:** keep model calls at authoring time and commit their output. See
  [model-free-seed.md](model-free-seed.md).

Run the generated process-repeatability test before freezing when that test is present.
Newer core provides `assert_repeatable(GoldenSpec(...))` and makes `freeze` reject detected
repeatability failures. The default older v4.1.1 `freeze` does **not** enforce that new
check. A generated compatibility test can run it with the older pin; run that test first
or upgrade to a released core that includes the gate. An older kit with no such test
still needs explicit ordered generation; its fixed-hash snapshot alone proves no
independence from process ordering.

For an intended content change, copy the actual seed reference, target volume and golden
path from the kit's generated determinism test. A typical scaffold uses:

```bash
synth-authoring freeze golden_seed:seed \
  --golden tests/golden/my_kit_spool.ndjson \
  --target-traces 24 --search-path tests --search-path src
pytest
```

Keep the fixture scale small. Confirm the reviewed change explains the snapshot diff,
then rerun scenario assertions. An adapter must apply every supported declared parameter
through runtime configuration and reject unsupported/conflicting inputs. Inspect that
adapter's accepted names before supplying `--params`; older scaffold adapters may ignore
parameters and need updating first.
