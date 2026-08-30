---
name: principle-laziness-protocol
description: Apply when sizing a change, refactoring, or considering a new abstraction. Prefer deletion and the smallest complete solution that a future maintainer can understand quickly.
---

# Laziness protocol

- Look for code, state, wrappers, and configuration that can be removed first.
- Keep call chains flat unless a boundary hides meaningful complexity.
- Put repeated decisions behind one source of truth.
- Avoid threading a new signal through many layers when one owner can answer directly.
- Do not create an abstraction for one caller or a hypothetical future.
- Minimize the diff without leaving the requested behavior incomplete.

The target is less maintenance work, not fewer lines at the expense of clarity or correctness.
