---
id: atoms-06c32b
title: Certification spawns 62 children on every store open; memoize per volume within a process?
status: idea
priority: 2
created: 2026-09-24T03:21:54Z
updated: 2026-09-24T03:21:54Z
depends: []
tags: [performance]
agent: claude-code/claude-opus-5-5
---

Observed from science's suite 2026-09-23: building one test world runs certify_sqlite_wal 31 times (62 subprocess children, about 1.5 s of a 4.5 s test), all on the same volume. Science worked around it with pytest-xdist (626 s -> 79 s). Question for atoms: is per-open certification required by design §8.3, or could a certificate keyed on the volume's configuration identity be reused within one process without weakening the refusal contract? Not a request to change it; a measured cost to weigh.
