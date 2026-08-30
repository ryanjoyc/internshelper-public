---
name: engineering-mode
description: Run a rigorous engineering workflow for non-trivial investigation, bug fixing, feature work, refactoring, UI hardening, planning, or skill authoring. Use when the user invokes engineering-mode, asks for pstack-style rigor, or delegates a substantial task through implementation and verification. Do not activate for casual questions or tiny edits.
---

# Engineering mode

Adapt pstack's core method to Codex: understand deeply, make the smallest complete change, prove the real outcome, and report it clearly.

## Start

1. Read the repository's instructions and applicable project skills before acting. Project-specific skills override generic workflow details.
2. Inspect the current state, working tree, and available verification paths. Preserve unrelated and pre-existing changes.
3. Classify the request using [playbooks.md](references/playbooks.md), then put the selected playbook's checkpoints into the working plan. Mark a checkpoint skipped only with a concrete reason.
4. Read only the principle skills that apply to actual decisions. Do not cite a principle unless it changes what you do.

## Working rules

- For diagnosis or review, inspect and report. Do not implement unless the request includes implementation.
- For implementation, continue through safe local edits and proportionate verification. Do not stop at a plan when the user asked for a result.
- Reproduce bugs before fixing them. Use `$tdd` when a cheap regression path exists.
- Use `$how` before changing an unfamiliar cross-file subsystem.
- Use `$blast-radius` for shared contracts, schemas, APIs, common UI primitives, or deceptively small changes with broad callers.
- Use `$interrogate` before handing off a high-risk or contested diff. It reviews only unless the user separately authorizes fixes.
- Apply `$technical-writing` to maintained documentation and `$unslop` to the final prose.
- Use subagents when independent exploration or independent review will improve coverage or reduce elapsed time. Give each a bounded scope, keep shared writes disjoint, and verify their artifacts directly.
- Prefer deterministic scripts or existing tests over repeated manual work. Do not build tooling whose cost exceeds the task.
- Keep the user informed during long work. State assumptions, material discoveries, and verification results.

## Authority

Skill invocation does not broaden the user's request. Follow the active approval and sandbox rules. Never infer permission to merge, deploy, publish, delete data, contact people, spend money, or modify unrelated systems.

## Completion

Before declaring done:

1. Inspect the final diff and current repository state.
2. Run the cheapest checks that prove the requested behavior, then broader nearby checks when risk warrants them.
3. Exercise the real surface when practical. A passing unit test is not proof of a visual interaction, CLI flow, migration, or external integration.
4. Record material remaining risks and anything that could not be verified.
5. Update project-specific state or guidance when the repository requires it.

Lead the final response with the outcome. Include evidence, material caveats, and the next action. Separate user impact from maintainer detail when both matter.
