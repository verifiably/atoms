# Public transaction preimage reader

**Status:** Draft design, 2026-09-11; awaiting approval. No implementation.
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

Beliefs' `beliefs-a7df71` consumes this seam to strengthen L13's classification
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
file. Invalid request arguments raise `ProtocolError` before lease entry;
translate only the existing path grammar's `SpecValidationError` into that
request error. Root/backend/storage inputs retain the existing public command
conventions.

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
5. Require a terminal `COMMITTED` or `ROLLED_BACK` record with its registration
   and settlement fully bound. A nonterminal selected transaction refuses.
   Use `_derive_reconciliation` to check binding and settlement agreement;
   any required backfill/append is refused here rather than performed by this
   reader. Contradictions raise `ChainStateInvalid`.
6. Require that the selected registration digest equals the record's binding
   and its entire entry equals `_registration_entry(record.spec, txid)`.
   The existing reconciliation helper does not compare the whole registration
   projection when a binding already exists; the reader must make this check
   explicitly. A mismatch raises `ChainStateInvalid`.
7. Select the corresponding initial `FileState` from the record. An absence,
   directory, or symlink is not a file preimage and raises `PreconditionRefused`.
   Refuse an indexed length above `max_bytes` before opening the blob.

Both committed and rolled-back transactions can supply historical bytes;
returning a preimage does not claim that an effect committed or that a removal
occurred. Beliefs selects committed removals from the chain. Unrelated pending
registrations do not impose a new whole-chain write gate on this reader.

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

Release the descriptor, store, and lease before the caller receives the bytes.
There is no partial result on error. The byte budget bounds the payload, not
Python's transient allocation overhead or time spent on normal lease recovery.
The existing cooperating-process threat model remains in force.

## 7. Failure and mutation semantics

| Condition | Outcome |
| --- | --- |
| Invalid request type, transaction ID, path grammar, or byte budget | `ProtocolError`, before lease entry |
| Lifecycle refuses, root unregistered, transaction/path unavailable, selected transaction nonterminal, non-file preimage, or budget too small | `PreconditionRefused` |
| Chain/record projection or binding contradiction; incomplete selected terminal reconciliation | `ChainStateInvalid` |
| Malformed local record/index; missing, nonregular, wrong-length, or wrong-hash indexed leaf/buffer | `MetadataStoreInvalid` |
| Recovery halts, capability unavailable, or ordinary I/O failure | Preserve the existing exception; no broad translation or fallback |

A writable-root read may reclaim or resolve an earlier transaction during
normal lease entry, including recovery writes. It is not a forensic no-write
operation. After that entry the preimage operation itself creates no record,
chain entry, blob, or project mutation. Non-writable lifecycle refusal happens
before the writable suffix and therefore does not probe, reclaim, or recover.

## 8. Implementation and verification boundary

Expected implementation is limited to the command and its private helper if
needed in `python/src/atoms/coordinator/commands.py`, the existing architecture
inventory in `python/tests/test_fs_architecture.py`, and focused public-command
tests using the repository's existing fixtures. Reuse `Store.read_record`,
`Store.open_blob`, `_registered_root`, `_registration_entry`, and
`_derive_reconciliation`; do not widen the store or add a generic read layer.

Verification must cover:

- A public transaction removes/replaces a file; the command returns its original
  bytes after the current path is gone or changed. Include empty bytes, exact
  budget, and rolled-back history.
- A second transaction/path with different bytes, an unregistered spec path,
  and a blob occurring only as a postimage cannot authorize the requested read.
- Invalid arguments refuse before lease entry; an over-budget file refuses
  before blob open. A non-file initial state and unavailable local history
  produce the stated refusal.
- Chain/record content mismatch, bad bindings, and missing selected settlement
  refuse; corrupt index/leaf, same-length substitution, truncation, symlink,
  FIFO, and modification between the store verification and buffer read never
  return bytes or leak descriptors.
- All non-writable lifecycle states refuse without creating or changing
  metadata/sidecars or project files. An active writable transaction resolves
  before reading; a halt propagates.
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
