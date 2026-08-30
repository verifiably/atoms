---
id: atoms-38887b
title: Deliver the public preimage blob-read seam
status: todo
priority: 2
size: l
created: 2026-08-30T18:18:54Z
updated: 2026-08-30T20:28:33Z
depends: []
tags: [migration, beliefs, chain]
---

Outcome: Atoms exposes the narrow lease-held public command Beliefs needs to read and verify an indexed transaction preimage without exposing Store, Lease, or a private blob descriptor API.

Acceptance evidence: Approve an Atoms-local design that fixes authorization, lifecycle, digest, descriptor/bytes ownership, and corruption behavior; implement the command through the existing verified Store.open_blob path under the correct read boundary; add architecture, corruption, lifetime, and consumer-contract tests; run the complete Python gate; and provide the stable seam Beliefs can use to discharge L13's preimage-backed classification.

Sources: docs/plans/2026-07-31-a5a-metadata-store-design.md and its blob tests; docs/plans/2026-08-22-chain-inspection-design.md §3; ~/d/beliefs/docs/designs/2026-08-03-redesign-adoption-ledger.md row 5; and ~/d/beliefs/docs/plans/2026-08-29-implementation-roadmap.md l13-preimage row.

Uncertainty: The verified internal blob reader exists, but Atoms has not designed the public authorization and return boundary and the deferred-obligation ledger currently admits no such shape.
