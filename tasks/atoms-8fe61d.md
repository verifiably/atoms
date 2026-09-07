---
id: atoms-8fe61d
title: Publish atoms-core to PyPI
status: todo
priority: 2
size: m
created: 2026-09-07T14:29:39Z
updated: 2026-09-07T14:29:49Z
depends: [ops-f1a933]
tags: [hygiene]
---

atoms-core 0.1.0 is declared in python/pyproject.toml but is not on PyPI, and atoms has no release workflow. beliefs declares atoms-core as a dependency and resolves it from ../../atoms/python as an editable sibling checkout, so beliefs cannot be published to PyPI until this is.

nodes is the working template: .github/workflows/release.yml triggers on a core/v* tag, runs the CI workflow as a gate, and refuses to build when pyproject and package.json versions disagree. atoms is Python-only, so the version-consistency step has nothing to compare and drops out.

atoms has no CI either. Add that first, the same shape as beliefs' (.github/workflows/ci.yml running the just recipes); atoms is public as of 2026-09-07, so standard runners are free.

Gated on ops-f1a933, which decides the naming scheme: verifiably-atoms is free on PyPI and atoms-core is not yet taken, so this task should not claim a name before that decision.
