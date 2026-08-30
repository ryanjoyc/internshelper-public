---
name: technical-writing
description: Write or review technical documentation, RFCs, READMEs, operational guides, PR descriptions, and commit messages for fast, unambiguous reading. Use when documentation structure, procedural clarity, reference accuracy, or maintainer-facing prose matters.
---

# Technical writing

Write for a tired engineer who needs the right answer on the first read.

## Choose the document type

- Tutorial: teach by producing visible results in sequence.
- How-to: give the shortest reliable path to a concrete goal.
- Reference: describe facts, options, limits, and errors for lookup.
- Explanation: build understanding of one bounded design question and its tradeoffs.

Keep one file primarily in one mode. Split and link when the modes conflict.

## Sentence rules

- Use real symbols, paths, flags, commands, and product terms.
- Address the reader directly and write instructions as commands.
- Put conditions and warnings before the steps they control.
- Put the common path before exceptions.
- Keep one instruction or main thought per sentence.
- Prefer present tense, active voice, and short everyday words.
- Keep modifiers such as "only" next to the words they modify.
- Make every pronoun point to one clear noun. Repeat the noun when needed.
- Use numbered lists for sequences and bullets for unordered sets.
- Keep headings in sentence case and make task headings verb phrases.
- Verify every path, count, command, option, and generated output against the current artifact.

Apply `$unslop` without deleting necessary technical precision. PR descriptions and commit messages follow the same sentence rules. Product UI copy follows the product's own copy system instead.
