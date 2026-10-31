---
id: atoms-eaadf0
title: "Memoize chain-entry decoding: every chain read re-decodes every durable entry"
status: todo
priority: 2
size: s
complexity: mid
process: direct
created: 2026-10-31T19:01:07Z
updated: 2026-10-31T19:01:07Z
depends: []
tags: [performance]
source: sci-0bea23
agent: claude-code/claude-opus-5-5
origin: agent
---

Filed from sci-0bea23 (science test-latency halt, 2026-10-31). A cProfile of one science test (test_cmd_adopt::test_a_refused_sibling_stays_fetched_and_does_not_change_later_evaluation, 84 s on titan) spent 18.5 s cumulative in `atoms.chain.model.decode_entry` (36,376 calls; `_state_from_json` 258,734 calls) and 20.8 s in `atoms.chain.inspect.scan_chain_directory` (2,148 calls; `validate_chain` 1,934 calls). Each `read_chain`/`validate_chain` re-reads and re-decodes every durable entry, so a session of n transactions decodes O(n^2) entries.

`decode_entry(data: bytes)` is a pure function of its bytes and the entries are frozen dataclasses, so a bounded per-process cache keyed by the exact envelope bytes (successful decodes only; the read, the digest-name check and every scan-level check stay as they are) would remove most of that work for atoms, beliefs and science alike.

Acceptance: confirm the decoded values are deeply immutable (a payload holding a dict or list that callers could mutate needs freezing or a copy-on-return, or this becomes planned work); a test that a cached decode returns an equal value and that a non-canonical or corrupt envelope still raises `ChainStateInvalid` on every call; a before/after timing of the atoms suite and of the science test named above.

## Notes

- 2026-10-31T19:01:07Z (add): created
  provenance: {"harness_session":"claude-code:e1c106c7-398b-4f30-8ca8-d851dbb5e76b","harness_session_source":"CLAUDE_CODE_SESSION_ID","origin":"agent"}
