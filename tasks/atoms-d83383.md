---
id: atoms-d83383
title: Make verifiably/atoms public
status: todo
priority: 2
size: xs
created: 2026-09-07T12:08:25Z
updated: 2026-09-07T12:08:25Z
depends: []
tags: [hygiene]
---

verifiably/atoms is private while the rest of the stack it belongs to is public: verifiably/beliefs and verifiably/nodes are both public, and beliefs' own README describes itself as built on nodes + atoms, so the dependency is already named in public while its repository is not readable.

Two things follow from flipping it. The stack becomes readable end to end, and GitHub Actions on standard runners becomes free here the way it is for beliefs (see the CI decision in beliefs AGENTS.md), which matters for atoms-0fd403's audit piece.

Before flipping, audit the history, not just the tip: secrets, credentials, absolute paths under a home directory, and anything in the certification fixtures that should not be world-readable. Publishing exposes every commit ever pushed, and unpublishing does not unpublish what was already fetched or indexed.
