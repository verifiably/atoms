# Public Preimage Read Implementation Plan

> **For agentic workers:** Use `superpowers:executing-plans` to implement this
> plan inline, task by task. Steps use checkboxes for tracking.

**Status:** Implemented and verified, 2026-09-11. Starting tree: `9286574`.
Evidence: [design §9](2026-09-11-public-preimage-read-design.md#9-implementation-evidence).

**Goal:** Return owned, verified bytes for a settled transaction's registered
regular-file preimage on its writable source root.

**Architecture:** One coordinator command enters the existing-only writable
recovery lease, authorizes against the chain and local record, and consumes
`Store.open_blob` under that lease. A private bounded reader hashes the exact
returned buffer. No new store, lifecycle, schema, or recovery mechanism.

**Tech Stack:** Python 3.11+, stdlib; existing pytest fixtures, ruff, pyright,
and `tools/tt` gate recipes.

**Spec:** [approved design](2026-09-11-public-preimage-read-design.md).
**Task:** `atoms-38887b`; implementation child `atoms-87c2e4` owns Task 1 below.

## Global constraints

- Writable roots only. Engine-produced serviceable replicas have fresh
  bookkeeping, not source transaction records or blobs. Beliefs selects a
  reachable writable source or supplied history in `beliefs-a7df71`.
- Exact string txid/path; exact integer budget, excluding bool. Wrong types
  raise `ProtocolError`; invalid grammar/negative budget raise
  `PreconditionRefused`, before any lease entry. Zero permits empty bytes.
- Authorize by txid and registered initial path, never by caller-supplied
  digest. Only fully bound COMMITTED/ROLLED_BACK history is readable.
- Preserve `read_record` coherence failures and `open_blob` leaf verification.
  Explicitly compare the whole registration; leave reconciliation unchanged.
- A halted active record blocks all history through normal lease entry.
  No forensic bypass, recovery callback, iterator, or descriptor escapes.
- No dependencies, compatibility layers, or new public exception/result types.
- Run tests through `tools/tt` or just. Do not change `FIRST_UNIMPLEMENTED`.
- Paths below are relative to the main checkout. Run shell blocks there;
  their subshell first enters `.worktrees/atoms-38887b`.
- Use only the tasks CLI for task records. Close the implementation child and
  parent with the implementation commit after verification; never infer status
  from an unchecked box without inspecting the tree.

## Files and existing mechanisms

| File under `.worktrees/atoms-38887b/` | Responsibility |
| --- | --- |
| `python/src/atoms/coordinator/commands.py` | `read_preimage`, one private buffer reader, imports and `__all__` |
| `python/tests/test_read_preimage.py` (new) | Public authorization, bytes, corruption, lifecycle and resource checks |
| `python/tests/test_fs_architecture.py` | Public inventories and clause-1 writable/existing-only lease assertions |
| `python/tests/test_packaging.py` | The second public-export inventory |
| `docs/plans/2026-09-11-public-preimage-read-design.md` | Mark implemented with verified evidence in the landing change |
| `README.md` | Brief source-root historical-read contract and link to the design |

Read `root.py::_writable_recovery_lease`, `recover.py::_registered_root`,
`_registration_entry`, `_derive_reconciliation`, `store/records.py::load_record`
and `coherence_findings`, and `store/blobs.py::open_blob` before implementation.
Their signatures and exceptions are the integration boundary, not refactor targets.

Test reuse: `coordinator_on`, `_enable_commands` and `_register` from
`test_coordinator_commands.py`; `DictPayloads` from `capture_support.py`;
`file_state`/`digest_of` from `store_support.py`; `_contend` from
`test_coordinator_lease.py`. Keep setup as plain functions: fixtures belong only
in `conftest.py`, and this task needs no new fixture registration.

### Task 1: Implement and verify the writable preimage command

**Files:** the six files above, this plan, and CLI-managed task records.

**Interfaces:** Consumes the existing lease, validated chain, StoredRecord and
verified blob descriptor. Produces exactly:

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
) -> bytes:
```

- [x] **1. Start the implementation child and establish the public positive case.**

Import the command into the new test module. Use this reusable setup and first
check; the explicit `registered_paths` is essential because `build_spec` defaults
to an empty registration projection. Imports for this excerpt: `Path`, `pytest`,
`ABSENT`, `DeletePath`, `build_spec`, `run_transaction`, `read_preimage`, and the
test helpers listed above, from their existing modules.

```python
def _remove(ingredients, payload: bytes):
    target = Path(ingredients[1]) / "d/f.txt"
    target.write_bytes(payload)
    target.chmod(0o644)
    state = file_state(payload)
    spec = build_spec(
        consumer_tag="test", intent_digest=digest_of(b"remove"),
        initial_surface={"d/f.txt": state},
        final_surface={"d/f.txt": ABSENT},
        effects=[DeletePath(effect_id="e1", path="d/f.txt", pre=state)],
        registered_paths=("d/f.txt",),
    )
    return run_transaction(*ingredients, spec, DictPayloads({}))


@pytest.mark.parametrize("payload", [b"", b"before", b"x" * 65537])
def test_returns_owned_preimage_after_removal(coordinator_on, monkeypatch, payload):
    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    (Path(ingredients[1]) / "d").mkdir()
    _register(ingredients, b"root", ())
    outcome = _remove(ingredients, payload)
    assert not (Path(ingredients[1]) / "d/f.txt").exists()
    result = read_preimage(
        *ingredients, outcome.txid, "d/f.txt", max_bytes=len(payload)
    )
    assert type(result) is bytes
    assert result == payload
```

Run and confirm failure names the missing public command:

```sh
(cd .worktrees/atoms-38887b && python3 tools/tt preimage-red -- sh -c 'cd python && uv run --frozen pytest tests/test_read_preimage.py -x')
```

- [x] **2. Implement admission, authorization and the owned read.**

Add `hashlib`, `RegisteredEntry`, `FileState`, `TransactionState`,
`MetadataStoreInvalid`, `_derive_reconciliation`, `_registration_entry`, and
`require_valid_identifier`, `Provenance`, `RootKind`, `BLOBS_PARENT`, and
`digest_to_leaf` imports from their existing stdlib/Atoms modules. Retain existing import conventions.
Add `read_preimage` to `__all__`. Use this body for the public signature above:

```python
    if type(txid) is not str or type(path) is not str or type(max_bytes) is not int:
        raise ProtocolError("txid/path must be exact str and max_bytes exact int")
    try:
        require_valid_identifier("txid", txid)
        require_rel_path("preimage path", path)
    except SpecValidationError as caught:
        raise PreconditionRefused(str(caught)) from caught
    if max_bytes < 0:
        raise PreconditionRefused("max_bytes must be nonnegative")
    with _writable_recovery_lease(
        backend, project_root, metadata_root, storage
    ) as lease:
        with _registered_root(lease) as (_, validated):
            registrations = [
                (digest, entry) for digest, entry in validated.entries
                if type(entry) is RegisteredEntry and entry.txid == txid
            ]
            if not registrations:
                raise PreconditionRefused("transaction is not registered here")
            if len(registrations) != 1:
                raise ChainStateInvalid("duplicate transaction registration")
            registration_digest, registration = registrations[0]
            if path not in dict(registration.initial):
                raise PreconditionRefused("path is not in the registered initial surface")
            record = lease._store.read_record(txid)
            if record is None:
                raise PreconditionRefused("transaction history is not retained locally")
            if record.state not in (TransactionState.COMMITTED, TransactionState.ROLLED_BACK):
                raise PreconditionRefused("transaction is not terminal")
            actions = _derive_reconciliation(record, validated)
            if actions.registration is not None or actions.settlement is not None:
                raise ChainStateInvalid("terminal history requires reconciliation")
            if (
                registration_digest != record.registration_digest
                or registration != _registration_entry(record.spec, txid)
            ):
                raise ChainStateInvalid("registration contradicts the local record")
            state = next(item.state for item in record.spec.initial_surface if item.path == path)
            if type(state) is not FileState:
                raise PreconditionRefused("initial state is not a regular file")
            if state.byte_len > max_bytes:
                raise PreconditionRefused("preimage exceeds max_bytes")
            audited = _cast(AuditedBackend, lease._binding.backend)
            provenance = Provenance(
                RootKind.METADATA,
                f"{BLOBS_PARENT}/{digest_to_leaf(state.content_hash)}",
            )
            fd = lease._store.open_blob(state.content_hash)
            audited.register(fd, provenance)
            try:
                payload = _read_preimage_bytes(fd, state)
            finally:
                audited.close_fd(fd)
    return payload
```

The explicit duplicate-registration refusal deliberately precedes local record
lookup; it reports `duplicate transaction registration`. Reconciliation retains
its own check for its other callers.

The `next` selection is justified by whole-entry equality and `read_record`'s
validated spec. No defensive fallback should turn a broken internal proof into
unavailable history. The private reader receives an already budget-checked state:

```python
def _read_preimage_bytes(fd: int, state: FileState) -> bytes:
    """Bound payload allocation and read one sentinel byte to detect growth."""
    buffer = bytearray()
    remaining = state.byte_len + 1
    while remaining:
        chunk = os.read(fd, min(65536, remaining))
        if not chunk:
            break
        buffer.extend(chunk)
        remaining -= len(chunk)
    if len(buffer) != state.byte_len:
        raise MetadataStoreInvalid("preimage length changed during read")
    payload = bytes(buffer)
    if "sha256:" + hashlib.sha256(payload).hexdigest() != state.content_hash:
        raise MetadataStoreInvalid("preimage hash changed during read")
    return payload
```

Do not catch lease/recovery, store or I/O exceptions broadly. `open_blob` owns
closure if it fails before returning; the command owns closure afterward.
Hash the returned bytes after conversion, not only the earlier store read.

- [x] **3. Extend authorization and request tests before accepting the implementation.**

Use the setup from step 1. Check two removals of the same path with different
payloads and assert each txid returns its own bytes. Also check replacement
using `ReplaceFile` and a registered spec: the current path contains the final
bytes while the reader returns the initial bytes.

```python
first = _remove(ingredients, b"first version")
second = _remove(ingredients, b"second version")
assert read_preimage(*ingredients, first.txid, "d/f.txt", max_bytes=64) == b"first version"
assert read_preimage(*ingredients, second.txid, "d/f.txt", max_bytes=64) == b"second version"
with pytest.raises(PreconditionRefused):
    read_preimage(*ingredients, first.txid, "d/other.txt", max_bytes=64)
```

For malformed requests patch `commands._writable_recovery_lease` with a function
that raises `AssertionError("lease entered")`. Parameterize wrong exact types
(including str/int subclasses and bool), malformed txids (`""`, `"a/b"`, 65
characters), paths (`""`, `"../x"`, `"/x"`, `"a//b"`, NUL), and budget `-1`.
Assert the design's exact exception class. For a positive integer budget too
small, patch `Store.open_blob` to raise `AssertionError("blob opened")`:

```python
def must_not_open(*args, **kwargs):
    raise AssertionError("blob opened")

monkeypatch.setattr(Store, "open_blob", must_not_open)
with pytest.raises(PreconditionRefused):
    read_preimage(*ingredients, first.txid, "d/f.txt", max_bytes=0)
```

The authorization cases also include an unknown txid; a public transaction with
`registered_paths=()` despite a file in its spec; and registered initial absent,
directory and symlink states. Use `CreateFileNoClobber` for the absent/postimage
case and `DeletePath` for the symlink case. A directory initial state is
unconstructible through current effects (the compiler also excludes untouched
surface paths); inject matching chain/record projection at the reader boundary
for the closed directory refusal, without claiming a public transaction made it.
An indexed postimage or another transaction's blob must never substitute for the requested preimage.

- [x] **4. Exercise record, chain and blob corruption at the public boundary.**

Use settled records produced by step 1. With the real `Store.read_record` result,
use `dataclasses.replace` only to inject contradictions after the store's own
coherence checks. This isolates the command's authorization from unrelated SQL
guards. Each case must raise the named exception before `Store.open_blob`:

| Injection or setup | Required result |
| --- | --- |
| Selected `read_record` returns None | `PreconditionRefused` |
| Selected record is PREPARED | `PreconditionRefused` |
| Bound record's spec changes only `consumer_tag` | `ChainStateInvalid` from whole-entry check |
| Registration binding names another transaction | `ChainStateInvalid` |
| Settlement binding names another entry or outcome disagrees | `ChainStateInvalid` |
| Clear terminal settlement binding with matching settlement still in chain | `ChainStateInvalid`; do not perform the derived backfill |
| Omit selected settlement in the validated test view and clear its binding | `ChainStateInvalid`; do not append the derived settlement |
| Unrelated pending registration added with the existing chain helper | Read still succeeds; no new whole-chain pending gate |

Use real on-disk corruption for the record's indexed blob. Following
`test_store_blobs.py` and `test_store_records.py`, mutate a copy of the test
database for absent/wrong-length `blob` rows (no trigger change is needed);
assert `MetadataStoreInvalid` from
`RULE_BLOB_ROW_PRESENT`/`RULE_BLOB_BYTE_LEN`, before blob open. Never weaken
production triggers. Tamper the indexed `blobs/sha256/<hex>` leaf: unlink,
truncate, extend, replace by same-length bytes, symlink, directory and FIFO.
Each must raise `MetadataStoreInvalid` without returning bytes or hanging.

For the second-hash check, wrap the real `Store.open_blob`, change the same
leaf after it returns, and let the command consume its detached descriptor:

```python
real_open = Store.open_blob
opened = []
def change_after_verification(store, digest):
    fd = real_open(store, digest)
    opened.append(fd)
    leaf = Path(ingredients[2]) / "blobs" / "sha256" / digest.removeprefix("sha256:")
    leaf.write_bytes(b"x" * len(b"first version"))
    return fd

monkeypatch.setattr(Store, "open_blob", change_after_verification)
with pytest.raises(MetadataStoreInvalid):
    read_preimage(*ingredients, first.txid, "d/f.txt", max_bytes=64)
with pytest.raises(OSError) as closed:
    os.fstat(opened[0])
assert closed.value.errno == errno.EBADF
```

Parameterize this wrapper with shorter and longer replacements to check EOF and
sentinel handling. Inject `OSError(errno.EIO, "read failure")` and `MemoryError`
at `_read_preimage_bytes`; assert propagation and the same descriptor closure.

- [x] **5. Verify lifecycle, recovery and resource duration using existing fixtures.**

Use `_replicate`, `_grant`, `_cold_copy` and `_copy_targets` in
`test_lifecycle_commands.py`, plus `fabricate_v2_store` in `lifecycle_support.py`.
Verify every non-writable lifecycle state refuses, including both a granted
replica and cold admission. Snapshot names, file bytes, modes and symlink
targets in project/metadata roots before and after refusal; include SQLite
sidecars and check no missing root or lock is recreated. Use the existing
machine-identity patch for BINDING_MISMATCHED. Do not fabricate positive history
in a serviceable store: there is no engine-produced positive case.

For rollback, inject `KeyboardInterrupt` into `execute.delete_path.apply` using
the public-run pattern in `test_coordinator_run.py`; read the resulting
ROLLED_BACK txid from its chain settlement and assert its preimage is readable.
For recovery-on-entry, prepare a real registered active record with
`test_coordinator_resolve.py::_prepare_registered` as the preparation model, then
read older settled history and verify the active record was settled and detached
first. That helper always appends genesis, so the new test module's
`_prepare_after_history` reuses its admission/capture/preparation calls and appends
only the registration to the existing chain; do not append a second genesis.
For halts, reuse the assembly-halt construction in
`test_coordinator_assembly_halt.py`; assert `TransactionHalted` for both the
active txid and unrelated settled history. The pure committed-halt record in
`test_coordinator_reconcile.py` is not an on-disk fixture: retain its existing
reconciliation coverage without claiming it exercises public lease entry. Do not
replace the lease in these recovery checks: the real gate is the behavior being tested.

Wrap the private byte reader to test the actual lock while it reads, then use
the existing contender after return and after injected errors:

```python
real_read = commands._read_preimage_bytes
def while_locked(fd, state):
    assert _contend(ingredients[2]) == 3
    result = real_read(fd, state)
    assert _contend(ingredients[2]) == 3
    return result

monkeypatch.setattr(commands, "_read_preimage_bytes", while_locked)
result = read_preimage(*ingredients, first.txid, "d/f.txt", max_bytes=64)
assert _contend(ingredients[2]) == 0
assert result == b"first version"
```

- [x] **6. Update architecture inventories and run focused verification.**

In `test_chain_commands_keep_the_lease_and_approval_proofs_private`, add only
`read_preimage` to the expected public function and export inventories. Add it
to clause 1's `_writable_recovery_lease` loop. Add the export to
`test_packaging.py`'s matching public inventory too. Extend the existing reader's
negative assertion exactly as follows; retain the no-direct-`resolve` check:

```python
for name in ("read_path_state", "read_preimage"):
    assert not _enters(public[name], "_recovery_lease"), (
        f"{name} entered the create-capable lease"
    )
```

```sh
(cd .worktrees/atoms-38887b && python3 tools/tt preimage-focused -- sh -c 'cd python && uv run --frozen pytest tests/test_read_preimage.py tests/test_fs_architecture.py tests/test_store_blobs.py tests/test_coordinator_reconcile.py')
```

Expected: all selected tests pass on the certified host. Confirm the same-length
post-open sabotage fails if the second hash is temporarily removed; confirm the
changed-`consumer_tag` check fails if whole-entry comparison is removed. Restore
both mutations immediately and rerun their focused checks. These two mutations
target the command's new trust checks, not existing store implementations.

- [x] **7. Record implementation, verify the full gate and close the work.**

After the code exists and focused checks pass, mark the design and this plan
implemented, record exact commands/results, and add this README contract:

> `read_preimage(..., txid, path, max_bytes=...)` returns verified, owned bytes
> for a settled transaction's registered regular-file initial state on a writable
> source root. Replica metadata carries no transaction history. The command may
> run recovery and refuses all reads while an active transaction is halted.

Link to the design. Search current user-facing docs for claims that the public
reader is absent; update current claims while preserving statements scoped to
older designs. The chain-inspection design's "Not created here" is historical,
not a claim this new task should erase. Do not mark Beliefs consumption complete.

```sh
(cd .worktrees/atoms-38887b && just check)
(cd .worktrees/atoms-38887b && just test)
```

Expected: zero lint/type/task errors, no task warnings, full suite passes under
the normal certified-host environment. Record every skip and its reason; do not
set `VERIFIABLY_UNCERTIFIED_HOST` to obtain a green gate. Run the existing
requesting-code-review workflow before completion and resolve findings. No
full physical certification benchmark is required for this read command.

Use `tasks done` on the implementation child, then `atoms-38887b`, with a
one-line verified result, and run `tasks check` again. Commit only the command,
tests, documentation and CLI-managed task records with conventional message
`feat(coordinator): expose verified transaction preimage reads`. Mark checkboxes
from actual evidence, not from this plan's predictions. Integration follows the
existing session authorization; harvest timing before removing the worktree.
