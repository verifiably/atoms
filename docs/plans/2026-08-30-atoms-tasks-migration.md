# Atoms Tasks migration ledger

**Status:** Initial migration prepared for review. Stable integration, canonical registration,
and post-registration ledger finalization remain pending; this ledger is active delivery.

## Scope and evidence

| Field | Value |
| --- | --- |
| Stable checkout HEAD | `dd658acfda829896e862a49140d061a4a894c4b0` on `main` before migration |
| Tasks source commit | `ee5174abd0d9842025c30a23e7cf79c20c77c9ed` |
| Audit date | 2026-08-30 |
| Prefix | `atoms` |
| Audit order | code, tests, and configuration; commit ancestry; branches and linked worktrees; documents |

The denominator is every tracked file under `docs/`, this ledger, and the existing root
`README.md` and `AGENTS.md`; no root `CLAUDE.md` exists. The unmodified Python gate passed:
6,234 tests passed, seven skipped, Ruff reported no findings, and Pyright reported zero
errors, warnings, or informations.

The authority design and `docs/deferred-obligation-ledger.md` are the governing Atoms
anchors. `python/tests/test_docs_status.py` independently fixes the roadmap boundary at
`FIRST_UNIMPLEMENTED = "A9"`; authored checkboxes in executed implementation plans are not
evidence of remaining work.

## Git state inspected

| Branch or worktree | Commit | Read-only disposition | Dirty paths |
| --- | --- | --- | --- |
| Stable `~/d/atoms`, `main` | `dd658acfda829896e862a49140d061a4a894c4b0` | Stable authority checkout; base of this migration | None |
| Migration `~/d/atoms/.worktrees/tasks-migration-atoms`, `chore/tasks-migration-atoms` | `dd658acfda829896e862a49140d061a4a894c4b0` before audit | Dedicated migration worktree | None before audit; ignored `python/.venv/` created by baseline setup |
| `~/d/atoms/.worktrees/chain-inspection`, `design/chain-inspection` | `99831987b24cd8ec778b3fe4ebde207c38b12a9e` | Clean completed evidence worktree; tip is an ancestor of `main`, which is 25 commits ahead | None |
| `~/d/atoms/.worktrees/holdings-commands`, `design/holdings-commands` | `5b3139f42fd5a3e4501fdda77d1793d103820952` | Clean completed evidence worktree; tip is an ancestor of `main`, which is five commits ahead | None |
| `~/d/atoms/.worktrees/public-chain-read`, `design/public-chain-read` | `2c077ed745f6eabfec6816c16803e78eefaa279c` | Clean completed evidence worktree; tip is an ancestor of `main`, which is 33 commits ahead | None |
| `~/d/atoms/.worktrees/root-lifecycle`, `design/root-lifecycle` | `fb95e1a5047dd1fe507e01290f721033446c88df` | Clean completed evidence worktree; tip is an ancestor of `main`, which is 16 commits ahead | None |

No branch name was treated as proof of active ownership. Science was inspected only as
consumer evidence: its local `main` was `16f1688773e401705bf4efa903241658a3ef53fa`,
tracked `origin/main` was `8cdb7657176e52a769aa7561bdc342e427d32828`, and holdings
integration `35be6ff` was an ancestor of both. Atoms' completed design worktrees remain
untouched.

## Document classification

| Document | Classification | Audit basis |
| --- | --- | --- |
| `AGENTS.md` | authority/current | Root authority order, roadmap boundary, obligation-ledger rule, and repository gate agree with the authority, code, and tests. |
| `README.md` | authority/current | User-facing architecture, delivered stages, and Science relationship agree after the drift corrections below. |
| `docs/2026-08-23-root-lifecycle-commands-design.md` | authority/current | Implemented coordinator contract; named implementation and review commits are ancestors of `main`, and the public commands and tests exist. |
| `docs/2026-08-24-holdings-read-and-evidence-commands-design.md` | authority/current | Implemented public read/evidence contract; `038513f` and the Science consumer integration are in the inspected histories. |
| `docs/2026-08-28-filesystem-expansion-brief.md` | active delivery | Explicit exploratory handoff for a future brainstorming session; it authorizes neither design nor implementation. |
| `docs/certification/2026-08-16-ext4-linux-7.1.8-arch1-3.json` | historical/superseded | Valid zero-violation nine-scenario certification record; its configuration is no longer the singleton production allowlist row. |
| `docs/certification/2026-08-27-ext4-linux-7.1.9-arch1-2.json` | historical/superseded | Valid zero-violation recertification record superseded in the singleton allowlist by the 7.1.10 tuple. |
| `docs/certification/2026-08-28-ext4-linux-7.1.10-arch1-1.json` | authority/current | Canonical record named by the singleton `CERTIFIED_ALLOWLIST`; schema and correspondence tests pass. |
| `docs/deferred-obligation-ledger.md` | authority/current | Living trust-boundary register; no open obligation, and every discharged row names implementation and suite evidence. |
| `docs/plans/2026-07-20-recoverable-fs-effect-engine-design.md` | historical/superseded | Header explicitly yields to the 2026-07-23 standalone authority. |
| `docs/plans/2026-07-20-recoverable-fs-effect-engine-implementation.md` | historical/superseded | Superseded science-framed roadmap; its current-authority pointer is corrected below. |
| `docs/plans/2026-07-23-plan-a1-core-model.md` | historical/superseded | Executed A1 implementation plan; checked steps, implementation tree, companion status, and history prove completion. |
| `docs/plans/2026-07-23-recoverable-fs-effect-engine-design.md` | authority/current | Approved standalone authority; §14 defines Plan A/Plan B and identifies A9 as the remaining Plan A stage. |
| `docs/plans/2026-07-28-a2-compilation-validation-design.md` | authority/current | Implemented A2 contract refined under the authority; compiler modules and conformance tests exist. |
| `docs/plans/2026-07-28-a3-recovery-reference-model-design.md` | authority/current | Implemented normative recovery model; the five production authorities and architecture tests exist. |
| `docs/plans/2026-07-28-plan-a2-compilation-validation.md` | historical/superseded | Executed implementation plan; its implemented status, code, tests, and history override authored unchecked steps. |
| `docs/plans/2026-07-28-plan-a3-recovery-reference-model.md` | historical/superseded | Executed implementation plan; its implemented status, recovery package, and tests prove completion. |
| `docs/plans/2026-07-29-a4a-capability-backend-design.md` | authority/current | Implemented capability/backend contract; Linux backend, binding, probes, and tests exist. |
| `docs/plans/2026-07-29-plan-a4a-capability-backend.md` | historical/superseded | Executed A4a implementation plan; status and landed modules/tests prove completion despite unchecked authored steps. |
| `docs/plans/2026-07-30-a4b1-path-resolution-design.md` | authority/current | Implemented rooted-resolution contract; resolver/lookup modules and conformance tests exist. |
| `docs/plans/2026-07-30-plan-a4b1-path-resolution.md` | historical/superseded | Executed implementation plan with an implemented status and matching current resolver. |
| `docs/plans/2026-07-31-a4b2-project-approval-design.md` | authority/current | Implemented project-approval contract; approval/judgment/topology modules and proof-gate tests exist. |
| `docs/plans/2026-07-31-a5a-metadata-store-design.md` | authority/current | Implemented SQLite-WAL store contract; store package and durability/corruption suites exist. |
| `docs/plans/2026-07-31-plan-a4b2-project-approval.md` | historical/superseded | Executed implementation plan with an implemented status and matching current proof boundary. |
| `docs/plans/2026-08-01-plan-a5a-metadata-store.md` | historical/superseded | Executed A5a implementation plan; store code, tests, design status, and history prove completion. |
| `docs/plans/2026-08-02-a5b-recovery-lease-design.md` | authority/current | Implemented lease/admission/preparation contract; coordinator package and lease tests exist. |
| `docs/plans/2026-08-02-plan-a5b-recovery-lease.md` | historical/superseded | Executed A5b implementation plan; coordinator code, tests, companion status, and history prove completion. |
| `docs/plans/2026-08-07-a6-coherent-capture-design.md` | authority/current | Implemented capture/observation contract; capture, descriptor, observation modules and tests exist. |
| `docs/plans/2026-08-07-plan-a6-coherent-capture.md` | historical/superseded | Executed A6 implementation plan; implementation, companion status, and history prove completion. |
| `docs/plans/2026-08-13-a7-effect-recovery-execution-design.md` | authority/current | Implemented A7 contract and current source of explicit post-A8 gaps; effects, recovery, chain, and guards exist. |
| `docs/plans/2026-08-13-plan-a7a-substrate.md` | historical/superseded | Executed A7a implementation plan; chain/command commits and current code prove completion. |
| `docs/plans/2026-08-13-plan-a7b-executor.md` | historical/superseded | Executed A7b implementation plan; status, five effect modules, coordinator executor, and tests prove completion. |
| `docs/plans/2026-08-14-a8-persistence-cut-and-certification-design.md` | authority/current | Implemented A8 contract; exerciser, cut model, certification tools, records, and tests exist. |
| `docs/plans/2026-08-14-plan-a8a-cut-model.md` | historical/superseded | Executed A8a implementation plan; implemented status and test-only artifacts prove completion. |
| `docs/plans/2026-08-14-plan-a8b-certification.md` | historical/superseded | Executed A8b implementation plan; implemented status, certification tooling, allowlist, and records prove completion. |
| `docs/plans/2026-08-20-public-chain-read-design.md` | authority/current | Implemented `read_chain` contract; command and tests exist, and the branch tip is in `main`. |
| `docs/plans/2026-08-22-chain-inspection-design.md` | authority/current | Implemented inspection/capture/pending-gate contract; code/tests exist, while its explicit public blob-read non-scope remains separate. |
| `docs/plans/2026-08-30-atoms-tasks-migration.md` | active delivery | Current audit, task-creation, and pre-integration evidence; stable registration and finalization remain pending. |

## Drift corrections

| Document and claim | Evidence | Correction | Outward-grep result |
| --- | --- | --- | --- |
| `README.md` still said Plan B adoption was `nodes` first, then Science. | Authority §12.2 and §14 put Science's composition root and families first and defer direct `nodes` on a language-neutral seam; Science adoption is implemented. | Replaced the stale order with the current Plan B order. | `AGENTS.md` and the authority already use the current ownership split; the superseded 2026-07-20 roadmap pointer was corrected in the same pass. |
| `README.md` called the SQLite I/O layer unsettled after A5a chose the stdlib baseline. | Authority §7 and the A5a design/code use stdlib `sqlite3` with verified-directory resolution; a custom VFS is optional hardening, not a correctness prerequisite or open obligation. | Renamed the section and stated the landed baseline plus optional hardening boundary. | Authority §7, A5a design, and the obligation ledger agree; no document claims a custom VFS shipped. |
| `docs/plans/2026-07-20-recoverable-fs-effect-engine-implementation.md` pointed readers to the current authority while repeating the old `nodes`-then-Science order and saying the superseded plan was still gated. | Current authority §12.2/§14 and implemented Science history; the document's own header marks it superseded. | Corrected the pointer and made the historical approval state past tense. | README and AGENTS now agree with the authority; the historical body remains unchanged. |
| The A5a, A5b, A6, and A7a implementation plans had no status header and retained authored unchecked steps after implementation. | Companion design statuses; `atoms/store`, coordinator/capture/observation/chain code; named implementation commits; the green full gate; every relevant commit is in `main`. | Added concise implemented/historical status headers without rewriting authored checkboxes. | README, AGENTS, authority status, companion designs, and `test_docs_status.py` all name these stages implemented. |
| `AGENTS.md`, `README.md`, and the holdings command design stopped at the 2026-08-26 statement that Science integration `35be6ff` had not been pushed. | `35be6ff` is an ancestor of inspected Science `main` and tracked `origin/main`; Atoms implementation `038513f` is also present in the consumer history. | Recorded the 2026-08-30 branch/ref evidence in all three locations. | All Atoms occurrences of the old unpushed holdings claim were replaced; later consumer status remains explicitly Science-owned. |

The required outward search covered status headers, A1–A9, obligation, certification,
adoption, Science, and supersession language. No other current claim contradicted code,
tests, history, or the inspected branch/worktree evidence.

## Candidate outcomes

| Outcome | Evidence | Sources | Active state | Size | Proposed status | Blockers | Disposition | Task ID |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Deliver Plan A stages A1–A8b | `FIRST_UNIMPLEMENTED = "A9"`, all named packages/suites, discharged obligation ledger, full green gate | Authority §14; AGENTS; README; stage designs/plans; `test_docs_status.py` | Completed on `main` | `xl` | — | None | no task — completed history | no task |
| Deliver root lifecycle, chain read/inspection, and holdings evidence commands | Public implementations/tests and merge commits are in `main`; all design worktree tips are ancestors | Four post-A8 command designs; coordinator/chain code and tests | Completed on `main`; evidence worktrees are clean ancestors, not active ownership | `xl` | — | None | no task — completed history | no task |
| Ship the A9 macOS backend and certification suite | Authority promises Linux and macOS with identical recovery tables; A9 remains the guarded first-unimplemented stage; no macOS backend, A9 design, certification record, branch, or owner exists | Authority §5.5, §13, §14, §16; AGENTS; README; `test_docs_status.py` | Explicit remaining Plan A delivery; no active work | `xl` | `todo` | None | create | `atoms-8be2dc` |
| Deliver the public preimage blob-read seam | Internal verified `Store.open_blob` exists, but chain-inspection §3 explicitly did not create the public command; Science's current tier-2 L13 boundary requires an Atoms blob-read seam behind its own design gate | A5a design/blob tests; chain-inspection design §3; Science adoption ledger row 5 and 2026-08-29 roadmap `l13-preimage` | Required producer seam; no Atoms public command, design, branch, or owner | `l` | `todo` | None | create | `atoms-38887b` |
| Certify the Science publication path under persistence cuts | A8 certifies engine-interior order only; Science X2 remains open and its current roadmap rejects a Science-side duplicate harness in favor of extending Atoms A8 behind an Atoms design gate | A8 design/plan/tooling; A7 known-gaps L-row split; Science cut 7, adoption ledger, and 2026-08-29 roadmap `persistence-cut` | Required cross-repository producer outcome; Science publication path is implemented, but no extension/design/owner exists | `xl` | `todo` | None | create | `atoms-f5779f` |
| Add terminal-record and unreferenced-blob garbage collection | Structural settlement gates exist; authority leaves retention to explicit consumer policy and A7 says no removal command exists | Authority §7.5/§15; A7 design §10.5/§16 | Unscheduled future policy, outside transaction correctness | `l` | — | None | no task — no approved delivery commitment | no task |
| Compact or bound the chain | A7 and chain-inspection call one-file-per-entry unbounded and say compaction is a future design “if ever” | A7 design §16; chain-inspection design §2 | Speculative future design | `xl` | — | None | no task — speculative | no task |
| Harden SQLite with a custom VFS | Stdlib is the implemented baseline; custom VFS is optional and no open obligation owns it | Authority §7; A5a design; README future-hardening note; obligation ledger | Optional hardening without a current requirement | `xl` | — | None | no task — optional | no task |
| Expand support to another filesystem | Brief is expressly exploratory and authorizes no design or implementation; XFS/Btrfs still fail closed | Filesystem-expansion brief; resolver and certification code | Future brainstorming handoff only | `xl` | — | None | no task — exploratory | no task |
| Add data-VCS composition, recursive effects, or a commit participant | Authority and README mark these as non-drivers or future extensions requiring new designs | README; authority §15 | Out of current scope | `xl` | — | None | no task — out of scope | no task |
| Complete later Science adoption and conformance boundaries | Science owns consumer policy, tasks, and status; Atoms owns only the two producer seams above | AGENTS; README; authority §12.2/§14; Science adoption ledger and roadmap | Active in Science, not duplicate Atoms delivery | `xl` | — | Science-local ordering | no task — consumer-owned | no task |

### Reviewed task body: Ship the A9 macOS backend and certification suite

Outcome: Plan A gains a macOS backend satisfying the existing semantic capability vocabulary
and recovery tables, with unsupported volumes or capabilities continuing to fail closed.

Acceptance evidence: Approve an Atoms-local A9 design and implementation plan; implement the
macOS anchored traversal, atomic rename, no-clobber transfer, full-durability barrier, volume
probe, and configuration binding; run the model, real-filesystem, subprocess-recovery, and
persistence-cut suites on macOS; add canonical crash-certification evidence for every admitted
tuple; and move the guarded roadmap boundary past A9 only when the complete Python gate passes.

Sources: docs/plans/2026-07-23-recoverable-fs-effect-engine-design.md §5.5, §13,
§14, and §16; AGENTS.md; README.md; and python/tests/test_docs_status.py.

Uncertainty: No A9 design, backend, certification record, active branch, or verified owner
exists, and capability availability remains volume-specific.

Initial fields: priority `2`; status `todo`; size `xl`; tags `migration`, `macos`,
`plan-a`.

### Reviewed task body: Deliver the public preimage blob-read seam

Outcome: Atoms exposes the narrow lease-held public command Science needs to read and verify an
indexed transaction preimage without exposing Store, Lease, or a private blob descriptor API.

Acceptance evidence: Approve an Atoms-local design that fixes authorization, lifecycle, digest,
descriptor/bytes ownership, and corruption behavior; implement the command through the existing
verified Store.open_blob path under the correct read boundary; add architecture, corruption,
lifetime, and consumer-contract tests; run the complete Python gate; and provide the stable seam
Science can use to discharge L13's preimage-backed classification.

Sources: docs/plans/2026-07-31-a5a-metadata-store-design.md and its blob tests;
docs/plans/2026-08-22-chain-inspection-design.md §3;
~/d/science/docs/designs/2026-08-03-redesign-adoption-ledger.md row 5; and
~/d/science/docs/plans/2026-08-29-implementation-roadmap.md l13-preimage row.

Uncertainty: The verified internal blob reader exists, but Atoms has not designed the public
authorization and return boundary and the deferred-obligation ledger currently admits no such
shape.

Initial fields: priority `2`; status `todo`; size `l`; tags `migration`, `science`,
`chain`.

### Reviewed task body: Certify the Science publication path under persistence cuts

Outcome: The Atoms persistence-cut and certification machinery exercises the adopted Science
publication path end to end, so Science X2 no longer relies only on engine-interior certification.

Acceptance evidence: Approve the Atoms-local cross-repository test design; extend the existing
record-reconstruct-recover or physical certification harness through the real Science
composition-root publication path without creating a second transaction authority; cover every
consumer-side durability boundary named by X2; record reproducible zero-violation evidence or an
explicit fail-closed result; and run the complete Atoms and affected Science gates.

Sources: docs/plans/2026-08-14-a8-persistence-cut-and-certification-design.md;
docs/plans/2026-08-13-a7-effect-recovery-execution-design.md §16; Science's cut-7
X2 accounting; ~/d/science/docs/designs/2026-08-03-redesign-adoption-ledger.md; and
~/d/science/docs/plans/2026-08-29-implementation-roadmap.md persistence-cut row.

Uncertainty: Science's publication path is implemented and its roadmap assigns the prerequisite
to an Atoms design gate, but the cross-repository harness boundary, hardware matrix, and verified
owner are not yet designed.

Initial fields: priority `2`; status `todo`; size `xl`; tags `migration`, `science`,
`certification`.

## Deferred foreign dependencies

None.

The three local outcomes have no verified delivery blocker in a not-yet-migrated project.
Science's future L13 and X2 tasks consume the two Atoms producer IDs; that direction is recorded
when Science migrates and does not create a dangling Atoms dependency now.

## Verification

| Exact command | Result | Commit containing result |
| --- | --- | --- |
| Linked-worktree detection, branch/base assertions, `.worktrees` ignore check, `git status --short --branch` | Clean linked worktree on `chore/tasks-migration-atoms` at the required base; `.worktrees` ignored globally. | Stable base `dd658acfda829896e862a49140d061a4a894c4b0` |
| Read-only Familiar HEAD plus normal-registry `TASKS_FORMAT=json tasks -C ~/d/familiar prime \| jq -e '.prefix == "fam"'` | Familiar was exactly `f3b2edae9de701103ffa6acc4357bcd71524950c`; canonical prefix check passed. | Pre-migration environment evidence |
| `cd python && uv run pytest` before audit | 6,234 passed, seven skipped in 484.73 seconds. | Stable base `dd658acfda829896e862a49140d061a4a894c4b0` |
| `cd python && uv run ruff check .` before audit | Passed with `All checks passed!`. | Stable base `dd658acfda829896e862a49140d061a4a894c4b0` |
| `cd python && uv run pyright` before audit | Passed with zero errors, warnings, or informations. | Stable base `dd658acfda829896e862a49140d061a4a894c4b0` |
| `git worktree list --porcelain`, `git branch --format=...`, per-worktree status, branch divergence, and ancestry checks | Six clean worktrees; all four completed design branch tips are ancestors of `main`; no active owner inferred. | Documentation reconciliation commit (this commit) |
| Certification JSON aggregation, commit-ancestry checks, and focused status/certification/allowlist tests | Three nine-scenario records, zero violations; 916/3,247, 916/3,269, and 915/3,281 marks/prefixes; every recorded source commit is an ancestor; 20 focused tests passed. | Documentation reconciliation commit (this commit) |
| Science `main`/`origin/main` ancestry check for `35be6ff` | Both checks passed; current consumer evidence replaces the dated unpushed snapshot. | Documentation reconciliation commit (this commit) |
| Required status/outward `rg`, exact document coverage `comm -3`, seven-section count, stale-phrase negative search, and `git diff --check` | 2,463 outward matches reviewed; all 38 denominator documents classified exactly; all seven required sections present; negative search, coverage comparison, and whitespace check produced no output. | Documentation reconciliation commit (this commit) |
| `cd python && uv run pytest && uv run ruff check . && uv run pyright` after reconciliation | 6,234 passed, seven skipped in 456.25 seconds; Ruff passed; Pyright reported zero errors, warnings, or informations. | Documentation reconciliation commit (this commit) |
| Normal-registry `TASKS_FORMAT=json tasks -C ~/d/atoms/.worktrees/tasks-migration-atoms prime` before initialization | Failed explicitly with `error.kind = "no_project"`; no normal-registry mutation was made. | Tasks initialization commit (this commit) |
| Temporary-registry `tasks -C ~/d/familiar init --prefix fam` and `tasks -C ~/d/atoms/.worktrees/tasks-migration-atoms init --prefix atoms` | Both initializations succeeded with empty warning arrays; `prime` resolved the migration checkout as prefix `atoms`. | Tasks initialization commit (this commit) |
| Three reviewed `tasks add` calls followed by `tasks show atoms-8be2dc`, `tasks show atoms-38887b`, and `tasks show atoms-f5779f` | All fields matched the reviewed rows and bodies; each task is `todo`, priority 2, unowned, and dependency-free, with no structured spec or plan. | Tasks initialization commit (this commit) |
| Temporary-registry `tasks -C ~/d/atoms/.worktrees/tasks-migration-atoms check >/tmp/atoms-check.json` and `jq -e '.errors == [] and .warnings == []' /tmp/atoms-check.json` | Passed with empty errors and warnings arrays. | Tasks initialization commit (this commit) |
| Temporary-registry `tasks -C ~/d/atoms/.worktrees/tasks-migration-atoms prime \| jq -e '.prefix == "atoms"'` and `tasks -C ~/d/atoms/.worktrees/tasks-migration-atoms ready` | Prefix assertion passed; all three created tasks were ready with an empty warnings array. | Tasks initialization commit (this commit) |
| `cd python && uv run pytest && uv run ruff check . && uv run pyright` after task creation | 6,234 passed, seven skipped in 457.47 seconds; Ruff passed; Pyright reported zero errors, warnings, or informations. | Tasks initialization commit (this commit) |
| `test ! -e tasks/projects.toml` and `git diff --check` | Both passed; no repository-local registry file or whitespace error exists. | Tasks initialization commit (this commit) |
