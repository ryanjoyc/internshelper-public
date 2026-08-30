---
name: tdd
description: Use test-driven development for a bug or behavior change when the user asks for TDD or when an obvious, cheap regression path exists. Skip new tests when the only path is brittle, expensive, integration-heavy, or weaker than a direct runtime check.
---

# TDD

Make the broken behavior executable before changing production code.

## Workflow

1. Identify the intended behavior, current behavior, and smallest observable reproduction.
2. Choose the closest existing unit, component, integration, browser, or CLI test layer.
3. Add the smallest test that expresses the intended contract instead of mirroring implementation details.
4. Run it before the fix. Confirm it fails for the reported reason.
5. Make the smallest production change that fixes the behavior.
6. Rerun the regression test and then the nearby suite proportionate to risk.

If a failing test is impractical, say why before fixing. Use the closest executable check, such as a focused script, browser reproduction, log assertion, or integration scenario. Prefer no new test over a test dominated by mocks, timing, unrelated fixtures, or private implementation details.

Report the failing-before evidence, passing-after evidence, and adjacent checks. Never weaken a correct assertion to accommodate a wrong implementation.
