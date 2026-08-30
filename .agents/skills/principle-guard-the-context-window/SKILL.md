---
name: principle-guard-the-context-window
description: Apply when work involves large files, verbose outputs, repeated reads, or parallel exploration. Keep raw bulk out of the coordinating thread and retain only evidence-bearing summaries.
---

# Guard the context window

- Read only material that can change the next decision.
- Send independent bulk exploration to bounded subagents when available.
- Ask subagents for findings, file pointers, commands, and uncertainty rather than raw dumps.
- Process large tool outputs into a compact structured result before reasoning over them.
- Keep frequently required rules in the entry skill and conditional detail in references.
- Divide long work into verifiable phases so old context can be summarized safely.

Do not delegate a small task merely to avoid reading the relevant code yourself.
