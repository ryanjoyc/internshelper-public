---
name: principle-build-the-lever
description: Apply to non-trivial repeated or high-risk work when a small script, codemod, generator, query, or verification tool would make execution faster and review reproducible.
---

# Build the lever

Create the smallest deterministic tool that performs or proves the work.

- Learn the recipe on one unit, then automate when repetition or reviewability justifies it.
- Make the tool safe to rerun.
- Compare its output against the learned example.
- Prefer one deterministic pass over many agents making the same mechanical edit.
- Keep the tool when the task or verification will recur. Keep a one-off probe local when it will not.
- Do not build a framework for a task that takes fewer reliable steps by hand.

The lever earns its place when a reviewer can rerun it instead of trusting the author.
