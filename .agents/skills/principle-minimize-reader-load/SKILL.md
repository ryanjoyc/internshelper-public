---
name: principle-minimize-reader-load
description: Apply when code is difficult to trace or holds too much hidden mutable state. Reduce the layers and facts a maintainer must keep in mind to answer where a value comes from and what can change it.
---

# Minimize reader load

Evaluate two independent costs:

1. Layers to trace between the question and the answer.
2. Hidden or mutable state the reader must remember.

- Collapse one-caller wrappers and pass-through adapters.
- Keep adjacent layers only when each changes the abstraction meaningfully.
- Prefer locals over fields, fields over module state, and derived values over synchronized copies.
- Put invariants at the owning boundary instead of repeating them in consumers.
- Demand that every new layer or piece of state reduce more reader work than it adds.

A new maintainer should be able to answer where a value comes from and what changes it without reconstructing the whole system.
