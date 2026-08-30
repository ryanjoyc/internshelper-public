---
name: how
description: Explain how a subsystem, feature flow, ownership boundary, or architectural layer works. Use for code walkthroughs, onboarding explanations, placement questions, or architecture critiques. Use why-focused research when the question is historical motivation rather than current behavior.
---

# How

Build a working mental model from the code. Do not infer behavior from filenames or documentation when the implementation can answer it.

## Workflow

1. Restate the scope with a reasonable interpretation. Ask only when different interpretations would materially change the answer.
2. Find the entry points, core data shape, boundaries, state owners, and final effects.
3. Follow the real call and data flow. Read callers, callees, configuration, persistence, tests, and adapters that change behavior.
4. For a narrow question, investigate directly. For a subsystem spanning several independent areas, send up to three read-only subagents to separate slices, then reconcile their findings against the code.
5. Test or trace any key claim that remains uncertain and is cheap to observe.

## Response

Adapt these sections to the question:

- Overview: what the subsystem does and where its responsibility begins and ends.
- Key concepts: only the types or abstractions required for the mental model.
- Flow: trigger to outcome, including decision points and state changes.
- Where it lives: the small set of files and symbols a maintainer should open first.
- Gotchas: non-obvious behavior, sharp edges, and uncertainty.

For critique requests, explain the system first. Then separate confirmed architectural problems from tradeoffs and preferences. Cite real files and symbols.
