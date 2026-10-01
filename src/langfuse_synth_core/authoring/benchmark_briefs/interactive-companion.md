# Brief v1: interactive-companion

Build a support-answer demo for a support operations lead with a seeded regression
and a Companion interaction that retries one failed return-policy question using a
corrected policy. Use 60 seeded traces first and preserve the story at 300.

Deliver a three-beat Presenter Runbook: find the seeded failure, retry it from the
Companion, inspect the new trace and its result in Langfuse. Expose every presenter
action in the delivered UI. Keep the interaction traceable to the selected failure;
show useful waiting and failure states. Verify the seeded examples and live result.

A coherent walkthrough completes all three beats with observable evidence and working
links. Record its first completion time, admission and rehearsal separately. An offline
Companion preview counts as iteration evidence; this brief's completed walkthrough
requires the live result to be visible in Langfuse.
