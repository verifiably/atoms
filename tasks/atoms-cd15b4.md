---
id: atoms-cd15b4
title: Adopt the verifiably README template (rollout 4)
status: done
priority: 2
size: m
complexity: mid
process: direct
owner: docs/readme-template
created: 2026-10-09T23:22:19Z
updated: 2026-10-10T12:10:50Z
started: 2026-10-10T12:02:23Z
completed: 2026-10-10T12:10:50Z
depends: []
tags: [docs]
model: claude-opus-5-5
agent: claude-code/claude-fable-5-1
---

Adopt the verifiably family README template as verifiably/docs docs/plans/2026-10-09-readme-template.md, section 'Rollout 4: atoms', specifies: tools, identity.toml, both guides, the family copy, the Plan A roadmap index at docs/plans/README.md with test_docs_status.py following it. Spec: verifiably/docs docs/specs/2026-10-09-readme-template-design.md.

## Notes

- 2026-10-10T11:55:45Z (main): sci-fdacb1 found: ops-docs check's refusal says 'run just docs', which the plan never adds; science added a docs recipe ({{tt}} docs -- python3 tools/ops-docs write), as autonomy has. Add one here too unless the justfile already has it
- 2026-10-10T12:02:23Z (main): started
  provenance: {"harness_session":"claude-code:eb06a0c2-1509-44fb-82c7-dd534e8dd245","harness_session_source":"CLAUDE_CODE_SESSION_ID"}
- 2026-10-10T12:03:13Z (docs/readme-template): resumed
  provenance: {"harness_session":"claude-code:eb06a0c2-1509-44fb-82c7-dd534e8dd245","harness_session_source":"CLAUDE_CODE_SESSION_ID"}
- 2026-10-10T12:09:57Z (docs/readme-template): plan gap: test_fs_architecture.py's ledger #9 test asserted three A4b sentences in AGENTS.md that Rollout 4 drops; removed those assertions (the ledger row assertions remain, and the docstring already defers status wording to test_docs_status.py)
- 2026-10-10T12:09:57Z (docs/readme-template): bounded choices: ci_check_cmd also runs ops-docs check (stdlib; needs tasks only with a status region); added the docs recipe per the sci-fdacb1 note; the index's authority bullet drops 'described below', whose referent (README's Relationship to Beliefs) is gone
- 2026-10-10T12:10:50Z (docs/readme-template): done
  provenance: {"harness_session":"claude-code:eb06a0c2-1509-44fb-82c7-dd534e8dd245","harness_session_source":"CLAUDE_CODE_SESSION_ID"}
- 2026-10-10T12:10:50Z (docs/readme-template): Adopted the verifiably README template: ops-docs v3 + family copy + identity.toml, both guides rendered, Plan A roadmap moved to docs/plans/README.md with test_docs_status.py guarding it, test-one and docs recipes, ops-docs check in check and CI
  provenance: {"harness_session":"claude-code:eb06a0c2-1509-44fb-82c7-dd534e8dd245","harness_session_source":"CLAUDE_CODE_SESSION_ID"}
