# Public transaction preimage reader

**Status:** Revised draft, 2026-09-11; review corrections incorporated, consumer
lifecycle scope unresolved (§1.1). No implementation or implementation plan.
**Task:** `atoms-38887b`. **Inspected Atoms base:** `32edc7e`.

**Authority:** [engine design](2026-07-23-recoverable-fs-effect-engine-design.md)
§6, §7, §11, §13.5; [root lifecycle](../2026-08-23-root-lifecycle-commands-design.md)
§7; [chain inspection](2026-08-22-chain-inspection-design.md) §3.
The roadmap remains at A9. This command adds no transaction effect or schema.

## 1. Decision and consumer need

Add one public `read_preimage` command to `atoms.coordinator.commands`. It
returns owned, verified bytes for one regular-file initial state of a locally
retained, settled transaction, selected by transaction ID and registered path.
It accepts the existing root/backend/storage arguments and an explicit byte
budget. It exposes no store, lease, file descriptor, iterator, or callback.

Beliefs' `beliefs-a7df71` is the intended consumer to strengthen L13's classification
of removed verification records. That remains a separate consumer task:
Atoms establishes historical bytes, while Beliefs decides their meaning and
which committed removal they explain. The current consumer already exposes
`LogSeam.state_facts`; this work adds no second state-to-digest API.

The first implementation serves writable roots through the existing recovery
lease. Other lifecycle states refuse. A read-only historical-store API is
outside this design: the current quiescent read helper exposes the chain and
root descriptor, not a live `Store`, and constructing the latter would require
additional admission and sidecar rules. No read-only root gains write authority
merely because this command needs a blob.

### 1.1 Consumer check and remaining scope decision

Checked Beliefs at `dbfea1f`: `python/src/beliefs/root.py` wires
`LogSeam.state_facts` to Atoms' `state_to_json`. However,
`python/src/beliefs/world/verify.py::_admit_arrival` rejects writable arrivals
and selects registered inspection for `read-only-serviceable` replicas before
calling the same evaluator that classifies committed removals. Both audit and
arrival accept caller-supplied `history: Mapping[str, bytes]`; current replay
matches those bytes by claimed path, not by the removed state's digest.

L13 in Beliefs' `docs/designs/2026-08-03-tamper-evident-log-design.md` requires
classification where historical content resolves through a held copy **or
surviving preimage bytes**. Thus writable-only retrieval does not by itself
cover the intended consumer: a serviceable replica can retain preimages that
this command would refuse to read. Supplied history can cover the held-copy
arm after Beliefs implements exact state matching, but is not automatic
retrieval from the replica's local store. The existing `state_facts` accessor
does not close that gap.

**Recommendation before planning:** extend the design to include retrieval
from `READ_ONLY_SERVICEABLE` roots. That requires a reviewed read-only record
and blob boundary under the existing lock, lifecycle and sidecar rules; the
writable `Store` and recovery lease must not be used on that arm. The detailed
read-only mechanism is not designed here, and the writable-only contract
below must not be treated as approval of that extension. Alternatively, retain
this bounded command and explicitly accept a remaining replica-local retrieval
gap in the consumer scope. Until that choice is settled, do not write the
implementation plan or claim the seam suffices to discharge `beliefs-a7df71`.

## 2. Alternatives considered

| Approach | Decision |
| --- | --- |
| Return bounded `bytes` after reading under the lease | Chosen: no resource ownership escapes; the consumer already parses historical bytes. |
| Yield a descriptor or stream while holding the lease | Rejected here: extends the public resource lifetime and lets a consumer hold the project lock indefinitely. |
| Expose a digest-addressed blob reader | Rejected here: proves store membership, but does not bind the bytes to the transaction and path being explained. |

## 3. Seam review against the deferred-obligation ledger

The ledger has no open obligations at the inspected base. Implementation must
execute or refuse every admitted case below in the same change; no admission
is left for an unnamed later owner.

| Candidate shape | Required disposition |
| --- | --- |
| Valid digest indexed for some other transaction or only a postimage | Never selectable by digest; require the requested path in this registration's initial surface and agreement with its local record. |
| Initial file state outside `registered_paths` | Refuse; the public command serves the chain's declared surface, not every private effect occurrence. |
| Non-writable, metadata-less, mismatched, or pre-lifecycle root | Refuse through the existing lifecycle gate without bootstrapping, granting, or activating recovery. |
| Missing local transaction record | Refuse as unavailable; no search of other stores or reconstruction from the current path. |
| Indexed preimage with missing or corrupt bytes | Raise `MetadataStoreInvalid`; never downgrade corruption to unavailable history. |
| Returned object outlives the lease | Safe by construction: only immutable, owned `bytes` escape. |
| Beliefs treats bytes as a scientific verdict | Outside Atoms' ownership; consumer interpretation and digest matching belong to `beliefs-a7df71`. |

A draft document admits no executable shape. No ledger entry is added merely
for writing this proposal. If implementation leaves any admitted case for later,
it must add an entry naming the owner and verification before landing.

## 4. Public contract

```python
def read_preimage(
    backend: Backend,
    project_root: str,
    metadata_root: str,
    storage: StorageProfile,
    txid: str,
    path: str,
    *,
    max_bytes: int,
) -> bytes: ...
```

`txid` and `path` must be exact strings. Reuse the existing safe transaction-ID
and relative-path grammars. `max_bytes` must be an exact, nonnegative integer;
booleans are not integers at this boundary. A zero budget can read an empty
file. Wrong types raise `ProtocolError`; invalid transaction-ID or path grammar
and a negative budget raise `PreconditionRefused`, all before lease entry.
After checking types, translate the identifier/path validators'
`SpecValidationError` to `PreconditionRefused`, following the public
registration/capture grammar convention rather than the store-internal
`require_identifier` error convention. Root/backend/storage inputs retain the
existing public command conventions.

The caller names a historical transaction and path, not an expected digest.
The engine derives the digest and length from verified local evidence. The
file may now be absent or contain different bytes; the current project path is
never a source for this read.

## 5. Admission and authorization

1. Validate the request arguments, then enter `_writable_recovery_lease`.
   Its existing-only lock and lifecycle check decide admission under the lock;
   do not perform a detached check followed by a create-capable lease.
2. Resolve recovery as that lease already does, then enter `_registered_root`
   to obtain the validated chain. No new resolution or validation algorithm.
3. Find the chain's unique `RegisteredEntry` for `txid`. Unknown transactions
   and paths absent from its initial projection raise `PreconditionRefused`.
4. Read the local record through `Store.read_record(txid)`. No local record
   raises `PreconditionRefused`; malformed records and absent/inconsistent blob
   index entries keep the store's `MetadataStoreInvalid` failure.
   Specifically, `read_record` invokes `records.coherence_findings` and checks
   `RULE_BLOB_ROW_PRESENT` and `RULE_BLOB_BYTE_LEN` before returning a record.
   Do not rely on `open_blob` for this classification: a missing index row at
   that lower-level API raises `ProtocolError`.
5. Require a terminal `COMMITTED` or `ROLLED_BACK` record with its registration
   and settlement fully bound. A nonterminal selected transaction refuses.
   Use `_derive_reconciliation` to check binding and settlement agreement;
   any required backfill/append is refused here rather than performed by this
   reader. Contradictions raise `ChainStateInvalid`.
   A halted active record never reaches this step: even a committed halt
   (`pre_halt_state COMMITTED` with a `COMMITTED` chain settlement) raises
   `TransactionHalted` during lease entry.
6. Require that the selected registration digest equals the record's binding
   and its entire entry equals `_registration_entry(record.spec, txid)`.
   The existing reconciliation helper does not compare the whole registration
   projection when a binding already exists; the reader must make this check
   explicitly. A mismatch raises `ChainStateInvalid`.
   This is intentionally stricter than today's reconciliation, whose
   registration backfill also does not compare the existing entry's content.
   That asymmetry stays unchanged in this work. No unmodified engine-produced
   registration fails this check: `RegisteredEntry`'s shape is unchanged since
   ancestor `284178a`, and production registration appends in `execute.py` and
   `recover.py` use `_registration_entry`. This adds an authorization check
   against inconsistent evidence, not a new encoding or migration requirement.
7. Select the corresponding initial `FileState` from the record. An absence,
   directory, or symlink is not a file preimage and raises `PreconditionRefused`.
   Compare `FileState.byte_len` with `max_bytes` and refuse an excess before
   opening the blob. Step 4 already proved that length equals the index row.

Both committed and rolled-back transactions can supply historical bytes;
returning a preimage does not claim that an effect committed or that a removal
occurred. Beliefs selects committed removals from the chain. Unrelated pending
registrations do not impose a new whole-chain write gate on this reader.

"Locally retained" describes available structure, not an indefinite retention
guarantee. No command currently deletes blob or transaction-record rows; ledger
entry #28 records the structural settlement gate on future collection. Any
future collector must explicitly honor this reader's availability and corruption
contract: absent local history is unavailable, while a retained record naming
a missing index row or leaf is corrupt. No collector or unnamed future owner
is admitted by this design, so this observation adds no ledger entry.

## 6. Byte verification and ownership

Open the derived digest only through `Store.open_blob`. It already requires
an indexed, no-follow regular leaf, checks its indexed length and SHA-256,
rewinds the descriptor, and detaches audit provenance for caller ownership.
The command becomes that descriptor's sole owner and closes it in `finally`,
including allocation, read, verification, and cleanup failure paths.

Read at most the expected length plus one sentinel byte, using bounded chunks
rather than allocating a buffer from an unchecked length. An unexpected length
raises `MetadataStoreInvalid`. Hash the exact bytes to be returned and require
the expected digest again: `open_blob` verified an earlier read of the file,
so its check alone is not a claim about a later returned buffer.
This deliberately reads and hashes the payload twice: once in `verify_leaf`
and once into the returned buffer. `max_bytes` limits the payload size, not the
combined I/O of these two passes (plus the sentinel read).

Release the descriptor, store, and lease before the caller receives the bytes.
There is no partial result on error. The byte budget bounds the payload, not
Python's transient allocation overhead or time spent on normal lease recovery.
The existing cooperating-process threat model remains in force.

## 7. Failure and mutation semantics

| Condition | Outcome |
| --- | --- |
| Wrong request type, including a boolean byte budget | `ProtocolError`, before lease entry |
| Invalid transaction-ID/path grammar or negative byte budget | `PreconditionRefused`, before lease entry |
| Lifecycle refuses, root unregistered, transaction/path unavailable, selected transaction nonterminal, non-file preimage, or budget too small | `PreconditionRefused` |
| Chain/record projection or binding contradiction; incomplete selected terminal reconciliation | `ChainStateInvalid` |
| Malformed local record/index; missing, nonregular, wrong-length, or wrong-hash indexed leaf/buffer | `MetadataStoreInvalid` |
| Recovery halts, capability unavailable, or ordinary I/O failure | Preserve the existing exception; no broad translation or fallback |

A writable-root read may reclaim or resolve an earlier transaction during
normal lease entry, including recovery writes. It is not a forensic no-write
operation. After that entry the preimage operation itself creates no record,
chain entry, blob, or project mutation. Non-writable lifecycle refusal happens
before the writable suffix and therefore does not probe, reclaim, or recover.

A halted active transaction prevents **every** preimage read on that writable
root, including reads of unrelated, settled history. Forensic history retrieval
while the root is halted is therefore unavailable through this command. A
committed halt does not qualify as readable committed history; lease entry
raises before selection of any historical transaction.

## 8. Implementation and verification boundary

Expected implementation is limited to the command and its private helper if
needed in `python/src/atoms/coordinator/commands.py`, the existing architecture
inventory in `python/tests/test_fs_architecture.py`, and focused public-command
tests using the repository's existing fixtures. Reuse `Store.read_record`,
`Store.open_blob`, `_registered_root`, `_registration_entry`, and
`_derive_reconciliation`; do not widen the store or add a generic read layer.
In clause 1 of
`test_chain_commands_keep_the_lease_and_approval_proofs_private`, add
`read_preimage` to the `_writable_recovery_lease` list and assert that it never
enters `_recovery_lease`, in addition to updating `__all__` and public inventories.
These implementation boundaries describe the writable arm only; accepting the
§1.1 recommendation requires revising them for the read-only mechanism first.

Verification must cover:

- A public transaction removes/replaces a file; the command returns its original
  bytes after the current path is gone or changed. Include empty bytes, exact
  budget, and rolled-back history.
- A second transaction/path with different bytes, an unregistered spec path,
  and a blob occurring only as a postimage cannot authorize the requested read.
- Invalid arguments refuse before lease entry; an over-budget file refuses
  before blob open. Assert the distinct type and grammar/budget error classes.
  A non-file initial state and unavailable local history
  produce the stated refusal.
- Chain/record content mismatch, bad bindings, and missing selected settlement
  refuse; corrupt index/leaf, same-length substitution, truncation, symlink,
  FIFO, and modification between the store verification and buffer read never
  return bytes or leak descriptors.
- All non-writable lifecycle states refuse without creating or changing
  metadata/sidecars or project files. An active writable transaction resolves
  before reading; a halt prevents reading unrelated settled history as well
  as the halted transaction, including a committed halt.
- Reads remain under the exclusive lock through the last byte; success and
  injected failures release it. Returned bytes remain usable afterward.
- The public inventory adds only `read_preimage`; no store, lease, descriptor,
  approval proof, raw SQL, or new backend binding escapes the coordinator.
- A consumer-shaped check reads two historical versions of one path by their
  removing transaction IDs, demonstrating that a path-only held copy cannot
  substitute for the selected preimage. Beliefs integration remains its task.

Run the focused checks through `tools/tt`, then `just check` and `just test`.
No per-sub-plan roadmap status test is added. At landing, mark this design
implemented and update any current user-facing statements that still describe
the public seam as absent. Do not mark Beliefs' L13 consumption complete merely
because this Atoms command exists.
