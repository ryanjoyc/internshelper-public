---
name: principle-boundary-discipline
description: Apply when designing validation, error handling, adapters, or framework wiring. Parse and defend at external boundaries, then keep internal business logic typed, direct, and testable.
---

# Boundary discipline

- Validate CLI input, configuration, network data, files, and external API responses when they enter the system.
- Convert raw representations into domain types once.
- Trust validated internal values instead of repeating guards throughout the call chain.
- Keep framework and transport code thin. Put decisions in pure or narrowly stateful domain functions.
- Expose domain concepts across a boundary instead of leaking storage, wire, or framework types.
- Handle expected external failures at the boundary and let unexpected internal failures remain visible.
