---
id: atoms-46cc25
title: "Package descriptions lag the code: README layout omits atoms.chain, pyproject says pure model, coordinator docstring says nothing is exported"
status: todo
priority: 2
size: xs
complexity: low
process: direct
created: 2026-10-25T17:14:09Z
updated: 2026-10-25T17:14:09Z
depends: []
tags: [docs]
source: vdocs-e4318e
agent: claude-code
---

Found by the second guided tour (vdocs-e4318e, 2026-10-25). README.md Layout lists four packages (core, fs, store, coordinator), but python/src/atoms/ also holds atoms.chain ('Tamper-evident project-local history'). python/pyproject.toml's description is 'Atoms core: a recoverable filesystem effect engine (pure model)', though the distribution ships the whole engine. python/src/atoms/coordinator/__init__.py says 'Nothing is exported. A5b ships no consumer-facing command … __all__ stays empty until one exists', while the README names atoms.coordinator.commands as the public surface. python/README.md is already atoms-4368ba.

## Notes

- 2026-10-25T17:14:09Z (add): created
  provenance: {"harness_session":"claude-code:1b29140b-c89c-4277-9bf0-a637ec403f80","harness_session_source":"CLAUDE_CODE_SESSION_ID"}
