"""A kit's live surfaces: the wall-clock emission seam, and the shared UI chrome.

* ``emit`` — the **live-emission seam** (portal #208): Companion Apps, playground
  submissions, workbench runs and experiment tasks emit *now*, through the Langfuse SDK,
  with no Spool and no backdating. The other side of the determinism line from
  :mod:`langfuse_synth_core.seed`.
* ``theme`` / ``paths`` — the shared web chrome: the Langfuse design tokens + page shell,
  and the prefix-aware internal paths driven by ``LIVE_BASE_PATH``.

The scenario's actual pages, forms, and routes live in each kit's own ``live`` package.
"""
