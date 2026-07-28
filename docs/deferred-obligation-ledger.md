# Deferred-obligation ledger

**Status:** Living register. Not a dated plan.

Every sub-plan that defines a trust boundary *admits* shapes it does not itself execute. Each admission
silently obliges a later sub-plan to refuse or execute that shape correctly. Three rounds of review on the
A2 compilation boundary found defects at exactly this seam and nowhere else, so the seams are tracked
here rather than rediscovered.

**Rules for this file.** An entry is added in the same commit as the admission that creates it. An entry
is removed only when its owning sub-plan lands *and* its verification suite covers it — not when the
sub-plan merely mentions it. A sub-plan is not ready for review until every entry naming it as owner has
a stated required behavior.

## Open obligations

| # | Admitted shape | Admitted by | First owner | Required behavior | Verification |
| --- | --- | --- | --- | --- | --- |
| 1 | A declared `FileState` is the postcondition; no payload bytes accompany it | A2 phase 2 | A6 | Capture and materialization hash the actual stream and compare against the frozen `FileState`; a mismatch refuses or halts | §13.2 |
| 2 | Two declared paths distinct under A2's whole-path portability key may still name one entry | A2 phase 4 | A4 | As part of `approve_for_project`, prove endpoint distinctness against each **parent directory's** actual lookup policy, covering `ABSENT`-declared paths, before any capture or mutation; successful A2 compilation is not sufficient | §13.3 surface 3 |
| 3 | An ancestor whose type changes mid-transaction (`FILE`/`SYMLINK` → `ABSENT` → `DIRECTORY`) with declared descendants | A2 phase 12 | A4, A6 | §6's second absence-capture case: infer descendant absence from the ancestor's verified fingerprint — descriptor-coherent for a file, destructive-transfer validation for a symlink — then hand §9.5's published descriptor down | §13.2, §13.4 |
| 4 | Path and component lengths are unbounded | A2 phase 3 | A4 | As part of `approve_for_project`, refuse actual-filesystem `NAME_MAX` / `PATH_MAX` violations before capture and before any transaction-record or blob write; the limits are per-filesystem and not lexically decidable | §5.4, §13.2 |
| 5 | Paths are stored verbatim; no resolution or containment is performed | A2 (whole) | A4 | Ancestor-resolved, leaf-retaining containment inside the project root, and metadata-root exclusion by `st_dev`/`st_ino` rather than spelling | §13.3 surfaces 1–2 |
| 6 | A required-capability set is derived but never checked against a backend | A2 §3 | A4 | Per-mount probe refuses a missing capability before any transaction-record write or project mutation; the §5.5 bootstrap is exempt and precedes it | §13.2 |
| 7 | A grammar-valid, intrinsically distinct concrete scratch leaf may already be occupied by external state | A1 §5.1, A2 phase 3 | A5 | After A4 has proved intrinsic concrete-name distinctness, check each bound scratch path for absence; only external occupancy may regenerate the txid, else return an external-state refusal — never use regeneration to remedy an intrinsic collision and never return `ProtocolError` for occupancy | §13.4 |
| 8 | `dependencies` are validated against the effect sequence but carry no scheduling information | A2 phase 7 | A7 | The executor follows the effect sequence and does not consult `dependencies` for ordering | §13.1 |
| 9 | `CompiledSpec` proves only A2's pure lexical/model rules and carries no project/root approval | A2 boundary | A4 | Define frozen, factory-controlled `ProjectApprovedSpec` composed with the exact `CompiledSpec`; construct it only through `approve_for_project`, make ordinary construction and `dataclasses.replace` refuse, and require A5–A8 to accept this proof rather than raw `TransactionSpec` or `CompiledSpec` | §5.4, §13.3 |
| 10 | A2's exact-spelling tree may differ from the tree after actual per-directory name equivalence is resolved (`A` versus `a/x`) | A2 phases 4, 12–13 | A4 | Build and retain the resolved per-directory equivalence topology; after endpoint-distinctness checks, re-run surface-tree consistency and created-directory-before-descendant ordering over resolved nodes before issuing `ProjectApprovedSpec` | §5.4, §13.3 surface 3 |
| 11 | A2 proves scratch grammar separation and fixed NFC/casefold effect-ID uniqueness, but not the actual distinctness of every instantiated effect/role leaf in its concrete parent | A1 §5.1, A2 phases 3 and 6 | A4 | Instantiate the complete scratch-name set and prove it pairwise distinct under each actual parent policy before issuing `ProjectApprovedSpec`; an intrinsic collision refuses approval and txid regeneration is not a remedy | §5.4, §13.3 surface 4 |

## Discharged obligations

None yet. A1's admissions were all discharged by A2; they are listed in that plan's self-review rather
than duplicated here.
