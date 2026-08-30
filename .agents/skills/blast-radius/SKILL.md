---
name: blast-radius
description: Find what a change could break beyond its visible diff and prove the key safety assumption with executable evidence. Use when asked what a change might break, when reviewing shared contracts, or when a small diff has suspiciously broad effects. This is analysis unless the user also asks for fixes.
---

# Blast radius

The goal is not a caller list. Find the failure paths that symbol search misses, then prove the fact on which safety depends.

## Workflow

1. Establish the change's intent and exact behavioral delta.
2. Trace direct callers and indirect contracts: serialized data, database columns, APIs, configuration, feature flags, framework lifecycle, generated code, and consumers in other languages or processes.
3. State the one or two assumptions that make the change safe.
4. For each assumption, gather the strongest affordable evidence:
   - point to the controlling source;
   - walk the bad case and show where it stops;
   - run a focused script or test through the real code;
   - reproduce it on the real surface when warranted.
5. Classify confirmed risks by likelihood and impact. Keep cleared risks separate. Mark any untested safety assumption as unproven.
6. For a wide change, use independent read-only reviewers on disjoint risk areas and verify their claims directly.

## Deliverable

- What changed.
- The key safety assumption and the strongest proof reached.
- Confirmed risks with evidence and a practical check.
- Cleared risks and why they are safe.
- The cheapest pre-merge test that would catch the credible failure.

Do not inflate uncertainty into a long list of hypothetical problems. Do not call a change safe because the writeup sounds convincing.
