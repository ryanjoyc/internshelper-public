---
name: interrogate
description: Perform an independent adversarial review of a diff, design, or implementation and synthesize a lead-reviewer verdict. Use when asked to interrogate, challenge, stress-test, find blind spots, or conduct a rigorous review. Review only; do not apply fixes without separate authorization.
---

# Interrogate

Try to break the work. The deliverable is a judged verdict, not a pile of reviewer comments.

## Scope and intent

1. Resolve the review scope from the requested files, current diff, branch comparison, or recent work.
2. State the implementation's intended outcome in one paragraph.
3. Gather enough surrounding code, tests, and contracts for reviewers to assess behavior rather than formatting.

## Independent review

For a non-trivial review, start up to three read-only subagents in parallel. Give every reviewer the same intent, scope, diff pointers, and baseline rubric. When several available models can handle the task, vary the models. Otherwise preserve independence and assign complementary attention areas without sharing conclusions:

- correctness, state, concurrency, and failure behavior;
- contracts, compatibility, security, and data boundaries;
- maintainability, tests, user experience, and operational behavior.

Require each finding to include a concrete failure scenario, evidence location, severity, and a suggested verification. Tell reviewers to return no finding when they cannot substantiate one.

## Lead judgment

Inspect every reported issue against the code. Deduplicate equivalent findings and note agreement, but do not accept a claim merely because several reviewers repeated it.

Classify each result:

- Act on: a demonstrated correctness, security, data-loss, compatibility, or serious maintainability problem.
- Consider: a legitimate tradeoff whose benefit may not justify the change now.
- Noted: true but low-impact or outside the present scope.
- Dismissed: incorrect, unsupported, stylistic, or missing context.

## Response

Return the stated intent, reviewer coverage, findings by category, dismissed claims with brief reasons, and an agreement map. Cite files and symbols. State verification gaps. Do not mutate the reviewed work unless the user asks for fixes after seeing the verdict.
