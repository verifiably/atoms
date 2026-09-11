---
id: atoms-87c2e4
title: Implement and verify the writable preimage command
status: done
priority: 2
size: m
owner: feat/atoms-38887b-preimage-reader
created: 2026-09-11T14:38:44Z
updated: 2026-09-11T15:20:05Z
started: 2026-09-11T14:50:12Z
completed: 2026-09-11T15:20:05Z
depends: []
parent: atoms-38887b
tags: [beliefs, chain]
plan: docs/plans/2026-09-11-public-preimage-read-plan.md
step: "Task 1: Implement and verify the writable preimage command"
---

Implement read_preimage under the existing-only writable recovery lease with txid/registered-path authorization and bounded owned-byte verification. Follow the approved design and linked plan; cover record/chain/blob corruption, lifecycle refusal, halts, lock and descriptor lifetime, source-shaped history, and architecture assertions. Run focused checks plus just check and just test; update status and close with the implementation commit. No read-only store or replica-history transport.

## Notes

- 2026-09-11T15:02:06Z (feat/atoms-38887b-preimage-reader): Implemented the approved reader with metadata provenance re-registration and audited close. Focused suite: 256 passed in 21.57s; just check clean. Both deliberate mutations were detected (second hash removal returns substituted bytes; whole-entry removal reaches unauthorized blob open), restored checks pass. Independent read-only review found no issues. Real active recovery and persisted assembly halt are covered; full suite running.
- 2026-09-11T15:10:58Z (feat/atoms-38887b-preimage-reader): Full run: 6300 passed, 7 skipped, 1 failure in test_packaging.py public __all__ inventory; added the approved export there. Packaging plus architecture: 74 passed. Seven skips require the unset ATOMS_CASEFOLD_VOLUME fixture. Rerunning the full gate after the inventory correction; production code unchanged.
- 2026-09-11T15:20:05Z (feat/atoms-38887b-preimage-reader): Implemented writable preimage reads with audited ownership and bounded verification; 6301 tests passed, 7 casefold-fixture skips, clean gate and independent review.
