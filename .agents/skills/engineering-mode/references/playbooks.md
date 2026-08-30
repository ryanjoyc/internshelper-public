# Engineering-mode playbooks

Select one primary playbook. Add another only when the task genuinely crosses modes.

## Investigation

1. State the question and scope.
2. Trace evidence from entry point to outcome.
3. Test the key uncertain claim when it is observable.
4. Return the answer with file or source evidence and unresolved uncertainty.

## Bug fix

1. Reproduce the reported behavior on the closest real surface.
2. Trace the symptom to its root cause and check for sibling instances.
3. Add a failing regression check when the path is cheap and meaningful.
4. Implement the smallest complete root-cause fix.
5. Prove the original reproduction is fixed and run adjacent checks.
6. Inspect the diff for accidental scope growth.

## Feature

1. Name the user-visible outcome and acceptance criteria.
2. Locate the owning data shape, boundaries, and existing conventions.
3. Implement a thin end-to-end path before optional polish.
4. Verify the main path, error path, and relevant accessibility or compatibility behavior.
5. Update maintained documentation and state.

## Refactoring

1. State the behavior that must remain unchanged.
2. Establish a baseline check.
3. Remove dead or redundant structure before adding abstractions.
4. Make small, verifiable transformations.
5. Rerun the baseline and inspect public contracts.

## UI hardening

1. Reproduce at the reported viewport and interaction state.
2. Identify the shared layout, state, focus, or event contract that owns the defect.
3. Fix the contract rather than adding a one-off visual patch.
4. Verify representative breakpoints, keyboard behavior, error states, and the supplied screenshot case.
5. Capture automated regression coverage when stable and affordable.

## Planning

1. Inspect the actual system before proposing work.
2. Resolve factual forks with code, tests, or prototypes.
3. Divide work into ordered units with an observable completion check for each.
4. Name dependencies, risks, approval boundaries, and stopping conditions.
5. Keep the plan implementable by a cold-start agent.

## Skill authoring

1. Use the available skill-creator guidance.
2. Define precise triggers and boundaries.
3. Keep the entry point lean and route conditional detail to references.
4. Validate structure and run a realistic forward test when complexity warrants it.
5. Preserve attribution and upstream licensing for adaptations.
