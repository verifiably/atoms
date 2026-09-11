---
id: atoms-38887b
title: Deliver the public preimage blob-read seam
status: doing
priority: 2
size: l
owner: feat/atoms-38887b-preimage-reader
created: 2026-08-30T18:18:54Z
updated: 2026-09-11T12:44:47Z
depends: []
tags: [migration, beliefs, chain]
---

Outcome: Atoms exposes the narrow lease-held public command Beliefs needs to read and verify an indexed transaction preimage without exposing Store, Lease, or a private blob descriptor API.

Draft design for review: docs/plans/2026-09-11-public-preimage-read-design.md.

Acceptance evidence: Approve an Atoms-local design that fixes authorization, lifecycle, digest, descriptor/bytes ownership, and corruption behavior; implement the command through the existing verified Store.open_blob path under the correct read boundary; add architecture, corruption, lifetime, and consumer-contract tests; run the complete Python gate; and provide the stable seam Beliefs can use to discharge L13 preimage-backed classification.

Sources: docs/plans/2026-07-31-a5a-metadata-store-design.md and its blob tests; docs/plans/2026-08-22-chain-inspection-design.md section 3; docs/2026-08-23-root-lifecycle-commands-design.md section 7; Beliefs adoption ledger and task beliefs-a7df71.

The internal verified blob reader exists. The public contract remains subject to design approval; no new production shape has been admitted.

## Notes

- 2026-09-11T12:43:56Z (feat/atoms-38887b-preimage-reader): Design inspection at 32edc7e: reuse Store.read_record/open_blob and the existing-only writable recovery lease. Beliefs already exposes LogSeam.state_facts, so this seam owes owned bytes rather than another digest accessor. Draft proposes txid + registered path authorization, whole registration/record agreement, terminal history, explicit byte budget, and refusal of non-writable roots. Draft: docs/plans/2026-09-11-public-preimage-read-design.md. No implementation before design approval.
- 2026-09-11T12:44:47Z (feat/atoms-38887b-preimage-reader): parked (waiting on user): Review and approve docs/plans/2026-09-11-public-preimage-read-design.md; then write the implementation plan and implement the public reader.
