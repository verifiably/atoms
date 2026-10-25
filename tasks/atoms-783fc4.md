---
id: atoms-783fc4
title: "README lead's 'as one step' can be read as all files changing at once, which design §3.2 denies"
status: todo
priority: 3
size: xs
complexity: low
process: direct
created: 2026-10-25T17:14:22Z
updated: 2026-10-25T17:14:22Z
depends: []
tags: [docs]
source: vdocs-e4318e
agent: claude-code
---

Found by the second guided tour (vdocs-e4318e, 2026-10-25): the learner asked whether the new lead contradicts the non-ACID contract. It does not in substance ('finished, undone or halted' describes outcomes), but 'applies a change to many files on disk as one step' reads naturally as atomic visibility, and §3.2 says 'Multiple persistent paths are not instantaneously visible as one atomic update'. Say that another reader can see the change partway, or drop 'as one step'.

## Notes

- 2026-10-25T17:14:22Z (add): created
  provenance: {"harness_session":"claude-code:1b29140b-c89c-4277-9bf0-a637ec403f80","harness_session_source":"CLAUDE_CODE_SESSION_ID"}
