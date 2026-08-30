---
name: principle-fix-root-causes
description: Apply while debugging when a tempting guard, workaround, or isolated patch may hide the real defect. Reproduce the symptom, trace its cause, and fix the recurring pattern rather than silencing one instance.
---

# Fix root causes

- Reproduce before changing code so the fix can be verified.
- Ask why until reaching the state, invariant, boundary, or ownership error that creates the symptom.
- Instrument uncertain behavior instead of guessing.
- Resist guards that merely suppress a crash or invalid state.
- Search for sibling instances of the same pattern.
- For restart-only bugs, inspect persistent state, caches, locks, and serialized configuration before assuming the code changed.

A workaround is acceptable only when the root cannot be changed within scope and the limitation is explicit and tested.
