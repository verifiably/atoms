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
| 2 | Two declared paths distinct under A2's lexical key may still name one entry | A2 phase 4 | A4 | Pairwise distinctness against each **parent directory's** lookup policy, covering `ABSENT`-declared paths, established at compilation before any capture or mutation | §13.3 surface 3 |
| 3 | An ancestor whose type changes mid-transaction (`FILE`/`SYMLINK` → `ABSENT` → `DIRECTORY`) with declared descendants | A2 phase 12 | A4, A6 | §6's second absence-capture case: infer descendant absence from the ancestor's verified fingerprint — descriptor-coherent for a file, destructive-transfer validation for a symlink — then hand §9.5's published descriptor down | §13.2, §13.4 |
| 4 | Path and component lengths are unbounded | A2 phase 3 | A4 | Refuse `NAME_MAX` / `PATH_MAX` violations at capture; the limits are per-filesystem and not lexically decidable | §13.2 |
| 5 | Paths are stored verbatim; no resolution or containment is performed | A2 (whole) | A4 | Ancestor-resolved, leaf-retaining containment inside the project root, and metadata-root exclusion by `st_dev`/`st_ino` rather than spelling | §13.3 surfaces 1–2 |
| 6 | A required-capability set is derived but never checked against a backend | A2 §3 | A4 | Per-mount probe refuses a missing capability before any transaction-record write or project mutation; the §5.5 bootstrap is exempt and precedes it | §13.2 |
| 7 | Scratch and persistent paths are proven disjoint *by grammar*, not by concrete name | A1 §5.1, A2 phase 3 | A5 | After binding a txid, check each instantiated scratch path for absence; on collision regenerate the txid, else return an external-state refusal — never a `ProtocolError` | §13.4 |
| 8 | `dependencies` are validated against the effect sequence but carry no scheduling information | A2 phase 7 | A7 | The executor follows the effect sequence and does not consult `dependencies` for ordering | §13.1 |

## Discharged obligations

None yet. A1's admissions were all discharged by A2; they are listed in that plan's self-review rather
than duplicated here.
