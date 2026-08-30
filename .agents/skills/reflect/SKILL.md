---
name: reflect
description: Extract durable lessons from a completed or difficult work session and propose concrete improvements to skills, scripts, tests, or project instructions. Use when the user asks to reflect or when a substantial workflow should be captured. Do not edit durable guidance until the user approves the proposed changes.
---

# Reflect

Turn demonstrated lessons into durable improvements without converting one-off events into universal rules.

## Workflow

1. Build a compact session record from the current conversation, changed artifacts, decisions, failed approaches, verification, and user corrections. Do not search unrelated conversations.
2. For a complex session, send the same record to three read-only reviewers covering judgment, tooling, and a deliberately divergent interpretation.
3. Synthesize candidate lessons. Each must name the evidence, when it applies, where it belongs, and how it would change future behavior.
4. Route mechanical lessons to tests, scripts, metadata, or lint rules before adding prose instructions.
5. Reject lessons that are already covered, too specific, speculative, or better represented by the code itself.
6. Present proposed changes as Accepted, Rejected, and Backlog. A request to reflect only requires a second approval before edits. A request that explicitly says to reflect and update or apply the lessons already authorizes supported, in-scope edits; show the routing and apply them without an extra approval turn.

Route each edit to its source of truth. Repository skills change in that repository. Marketplace-backed plugin skills change in the plugin source, never in the installed cache; use the plugin creator's cachebuster and reinstall flow afterward. If "our skills" could refer to materially different targets, show the routing and ask before writing.

After authorization, use the available skill-creation workflow for substantive skill changes and validate every touched skill. Summarize what changed and what was deliberately dropped.
