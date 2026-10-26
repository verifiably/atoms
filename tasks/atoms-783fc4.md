---
id: atoms-783fc4
title: Clarify atoms' recovery contract and its role alongside nodes
status: todo
priority: 3
size: s
complexity: low
process: direct
created: 2026-10-25T17:14:22Z
updated: 2026-10-26T01:29:37Z
depends: []
tags: [docs]
source: vdocs-e4318e
agent: claude-code
origin: mixed
---

Why: The user explicitly asked in this session to clarify atoms' role and relationship to nodes and beliefs after agreeing to keep beliefs composed over both libraries and retain TypeScript. The original README lead also suggests instantaneous visibility across files. Current authority sections 1, 12 and 14 still describe nodes as a direct or inevitable future consumer, while the implemented route is nodes' executor interface wired to atoms by beliefs.root. [ref:authority] supplies the recovery guarantees to preserve; [ref:identity] generates the guide lead.

Done:
- Explain atoms as the domain-neutral engine for recoverable filesystem effects, usable without node or scientific concepts. Explain nodes' logical corpus operations separately from physical execution; beliefs combines both through its composition root.
- Revise the purpose/abstract in identity.toml as needed to make that role clear without losing the newcomer-facing problem statement or contract. Keep the generator's 80-character purpose, 400-character abstract and three-sentence limits; put the fuller relationship explanation and a small dependency diagram in an existing README section, preferably Usage, rather than adding a family-contract section or forcing every detail into the lead. Regenerate README.md and AGENTS.md with tools/ops-docs write.
- Preserve the original lead fix: remove the misleading "as one step" wording or explicitly distinguish recovery outcomes from instantaneous multi-file visibility. Keep finish/undo/evidence-preserving halt, one-host/cooperating-process scope and the certified-filesystem restriction; a halt may leave a partial change.
- Reconcile the current authority's introductory framing and sections 1, 4, 12, 14 and 15 where they imply a mandatory science -> beliefs -> nodes -> atoms package chain or a commitment to direct nodes adoption. State that Beliefs' adapter is implemented and verify any finer adoption/certification status before changing it. A shared or cross-language atoms integration is a possible response to a demonstrated consumer need, not a prerequisite for nodes to be a useful logical substrate. Do not claim a shipped bridge or Mindful atoms adoption.
- Keep nodes independent of atoms and retain TypeScript; no runtime, public API or engine-protocol changes. Leave superseded historical documents as history. Coordinate with atoms-46cc25, which separately owns package-description/layout drift, and atoms-4368ba, which owns the Python package README; avoid expanding into those unrelated corrections.
- Verification at implementation: tools/ops-docs check, just check, the project test front door's focused docs-status check (just test-one tests/test_docs_status.py), and the required pre-commit fast route. Review the rendered README to confirm that arrows explicitly mean package dependencies and that the execution-through-an-injected-adapter explanation remains accurate.

Where to look: identity.toml, README.md, AGENTS.md, tools/ops-docs (generator constraints only; do not edit the vendored tool), [ref:authority], python/tests/test_docs_status.py, and the registered nodes/beliefs checkouts: nodes python/src/nodes/core/write_plan.py and corpus.py, docs/STANDARD.md section 7; beliefs python/src/beliefs/root.py::DurableExecutor/open_corpus. Evidence checked at atoms 051bca5, nodes 31c375a and beliefs 741fea1. This is a direct documentation change with the architecture settled by the user's agreement; it does not depend on atoms-495445's consumer assessment.

## Original capture

Found by the second guided tour (vdocs-e4318e, 2026-10-25): the learner asked whether the new lead contradicts the non-ACID contract. It does not in substance ('finished, undone or halted' describes outcomes), but 'applies a change to many files on disk as one step' reads naturally as atomic visibility, and §3.2 says 'Multiple persistent paths are not instantaneously visible as one atomic update'. Say that another reader can see the change partway, or drop 'as one step'.

## References

- authority (input, required, kind=spec): docs/plans/2026-07-23-recoverable-fs-effect-engine-design.md — recovery guarantees and current consumer framing to reconcile
- identity (input, required, kind=config): identity.toml — source of generated purpose and abstract
- readme (input, kind=guide): README.md — newcomer-facing role and dependency explanation

## Notes

- 2026-10-25T17:14:22Z (add): created
  provenance: {"harness_session":"claude-code:1b29140b-c89c-4277-9bf0-a637ec403f80","harness_session_source":"CLAUDE_CODE_SESSION_ID"}
