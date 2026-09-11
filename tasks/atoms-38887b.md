---
id: atoms-38887b
title: Deliver the public preimage blob-read seam
status: doing
priority: 2
size: l
owner: feat/atoms-38887b-preimage-reader
created: 2026-08-30T18:18:54Z
updated: 2026-09-11T14:39:29Z
started: 2026-09-11T14:21:22Z
depends: []
tags: [migration, beliefs, chain]
plan: docs/plans/2026-09-11-public-preimage-read-plan.md
---

Outcome: Atoms exposes the narrow lease-held public command Beliefs needs to read and verify an indexed transaction preimage without exposing Store, Lease, or a private blob descriptor API.

Draft design for review: docs/plans/2026-09-11-public-preimage-read-design.md.

Acceptance evidence: Approve an Atoms-local design that fixes authorization, lifecycle, digest, descriptor/bytes ownership, and corruption behavior; implement the command through the existing verified Store.open_blob path under the correct read boundary; add architecture, corruption, lifetime, and consumer-contract tests; run the complete Python gate; and provide the stable seam Beliefs can use to discharge L13 preimage-backed classification.

Sources: docs/plans/2026-07-31-a5a-metadata-store-design.md and its blob tests; docs/plans/2026-08-22-chain-inspection-design.md section 3; docs/2026-08-23-root-lifecycle-commands-design.md section 7; Beliefs adoption ledger and task beliefs-a7df71.

The internal verified blob reader exists. The public contract remains subject to design approval; no new production shape has been admitted.

## Notes

- 2026-09-11T12:43:56Z (feat/atoms-38887b-preimage-reader): Design inspection at 32edc7e: reuse Store.read_record/open_blob and the existing-only writable recovery lease. Beliefs already exposes LogSeam.state_facts, so this seam owes owned bytes rather than another digest accessor. Draft proposes txid + registered path authorization, whole registration/record agreement, terminal history, explicit byte budget, and refusal of non-writable roots. Draft: docs/plans/2026-09-11-public-preimage-read-design.md. No implementation before design approval.
- 2026-09-11T12:44:47Z (feat/atoms-38887b-preimage-reader): parked (waiting on user): Review and approve docs/plans/2026-09-11-public-preimage-read-design.md; then write the implementation plan and implement the public reader.
- 2026-09-11T14:26:33Z (feat/atoms-38887b-preimage-reader): Review revisions: settled request errors (wrong types ProtocolError; grammar/negative budget PreconditionRefused), documented stricter registration authorization without changing reconciliation, named read_record coherence checks, clarified halted-root exclusion, retention and architecture assertions. Consumer check at Beliefs dbfea1f confirmed admit_arrival evaluates read-only-serviceable replicas and L13 includes surviving preimages; writable-only retrieval leaves a replica-local gap. Revised design section 1.1 recommends designing that read-only boundary before planning; scope decision pending.
- 2026-09-11T14:26:56Z (feat/atoms-38887b-preimage-reader): parked (waiting on user, decision): Settle revised design section 1.1: include READ_ONLY_SERVICEABLE preimage retrieval (recommended), or explicitly retain the replica-local consumer gap; then finish the design before writing the plan.
- 2026-09-11T14:34:29Z (feat/atoms-38887b-preimage-reader): Second review verified: replication excludes the non-overlapping source metadata root, mints fresh destination bookkeeping, and lifecycle transitions cannot demote writable stores. Corrected section 1.1, accepted writable-only design under the user approval, removed planning hold, and noted source-root selection on beliefs-a7df71. Proceeding to implementation plan.
- 2026-09-11T14:39:09Z (feat/atoms-38887b-preimage-reader): Implementation plan written and self-reviewed at docs/plans/2026-09-11-public-preimage-read-plan.md; one complete deliverable tracked by atoms-87c2e4. Reuses existing writable lease, record/coherence and blob verification paths; covers source history, errors, corruption, lifecycle, halts, resources and full gate. No read-only arm or planning approval hold remains.
- 2026-09-11T14:39:29Z (feat/atoms-38887b-preimage-reader): parked (waiting on agent): Execute the approved writable-only design via child atoms-87c2e4 and docs/plans/2026-09-11-public-preimage-read-plan.md; no lifecycle-scope decision remains.
