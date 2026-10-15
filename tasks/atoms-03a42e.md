---
id: atoms-03a42e
title: Reconcile the WAL certification cache design with what shipped
status: done
priority: 3
size: xs
complexity: low
process: direct
owner: docs/wal-cert-reconcile
created: 2026-10-15T11:02:17Z
updated: 2026-10-15T11:12:10Z
started: 2026-10-15T11:04:04Z
completed: 2026-10-15T11:12:10Z
depends: []
tags: [performance]
agent: claude-code/claude-opus-5-5
---

Deferred minors from the atoms-257797 final review. (1) docs/plans/2026-10-15-wal-certification-cache-design.md §6 names `lazytime` as the added option for the changed-entry test; the shipped test_a_changed_mount_option_certifies_afresh uses `x-atoms-test` (an x- option never reaches the kernel's mountinfo, so it is certainly new). (2) §1's '3,298 binds / 6,596 children' disagrees with the recorded before-count of 6,912 certifications at -n 8 (atoms-257797 notes; a second counter confirmed one certification per bind): cite the recorded measurement. (3) The CertifiedVolume docstring in atoms/fs/probe.py should say why mount_id and device are kept beside entry (fdinfo-observed ID and st_dev integer are distinct facts from the parsed mountinfo fields), so nobody 'simplifies' one away. Done when the design matches the suite and test_docs_status passes.

## Notes

- 2026-10-15T11:04:04Z (main): started
  provenance: {"harness_session":"claude-code:dac2d27a-5a4a-45b9-b94e-2bedbc03bdd3","harness_session_source":"CLAUDE_CODE_SESSION_ID"}
- 2026-10-15T11:04:24Z (docs/wal-cert-reconcile): resumed
  provenance: {"harness_session":"claude-code:dac2d27a-5a4a-45b9-b94e-2bedbc03bdd3","harness_session_source":"CLAUDE_CODE_SESSION_ID"}
- 2026-10-15T11:12:10Z (docs/wal-cert-reconcile): done
  provenance: {"harness_session":"claude-code:dac2d27a-5a4a-45b9-b94e-2bedbc03bdd3","harness_session_source":"CLAUDE_CODE_SESSION_ID"}
- 2026-10-15T11:12:10Z (docs/wal-cert-reconcile): design §1 cites the recorded 6,912 → 8 measurement beside the pre-design estimate; §6 names x-atoms-test with its rationale; CertifiedVolume docstring says why mount_id and device sit beside entry (btrfs subvolume st_dev); test-fast 954 passed, docs status green
  provenance: {"harness_session":"claude-code:dac2d27a-5a4a-45b9-b94e-2bedbc03bdd3","harness_session_source":"CLAUDE_CODE_SESSION_ID"}
