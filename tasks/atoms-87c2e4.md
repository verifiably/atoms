---
id: atoms-87c2e4
title: Implement and verify the writable preimage command
status: todo
priority: 2
size: m
created: 2026-09-11T14:38:44Z
updated: 2026-09-11T14:38:44Z
depends: []
parent: atoms-38887b
tags: [beliefs, chain]
plan: docs/plans/2026-09-11-public-preimage-read-plan.md
step: "Task 1: Implement and verify the writable preimage command"
---

Implement read_preimage under the existing-only writable recovery lease with txid/registered-path authorization and bounded owned-byte verification. Follow the approved design and linked plan; cover record/chain/blob corruption, lifecycle refusal, halts, lock and descriptor lifetime, source-shaped history, and architecture assertions. Run focused checks plus just check and just test; update status and close with the implementation commit. No read-only store or replica-history transport.
