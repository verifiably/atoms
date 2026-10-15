---
id: atoms-03a42e
title: Reconcile the WAL certification cache design with what shipped
status: todo
priority: 3
size: xs
complexity: low
process: direct
created: 2026-10-15T11:02:17Z
updated: 2026-10-15T11:02:17Z
depends: []
tags: [performance]
agent: claude-code/claude-opus-5-5
---

Deferred minors from the atoms-257797 final review. (1) docs/plans/2026-10-15-wal-certification-cache-design.md §6 names `lazytime` as the added option for the changed-entry test; the shipped test_a_changed_mount_option_certifies_afresh uses `x-atoms-test` (an x- option never reaches the kernel's mountinfo, so it is certainly new). (2) §1's '3,298 binds / 6,596 children' disagrees with the recorded before-count of 6,912 certifications at -n 8 (atoms-257797 notes; a second counter confirmed one certification per bind): cite the recorded measurement. (3) The CertifiedVolume docstring in atoms/fs/probe.py should say why mount_id and device are kept beside entry (fdinfo-observed ID and st_dev integer are distinct facts from the parsed mountinfo fields), so nobody 'simplifies' one away. Done when the design matches the suite and test_docs_status passes.
