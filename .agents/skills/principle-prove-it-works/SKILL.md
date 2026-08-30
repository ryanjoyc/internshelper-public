---
name: principle-prove-it-works
description: Apply after completing a task and before declaring success. Verify the real artifact or user path directly instead of relying on compilation, generated files, cached output, or an agent's summary.
---

# Prove it works

Ask what observation would fail if the implementation were wrong, then make that observation.

- Build or type-check when relevant, but do not stop there.
- Run the changed path from input to output.
- Exercise the real UI, CLI, API, migration, or integration when practical.
- Inspect delegated artifacts and diffs directly.
- Prefer a deterministic rerunnable check over a one-time claim.
- When a check fails, verify the observation method before drawing a system conclusion.
- State any gap that could not be tested. Do not round partial evidence up to proof.
