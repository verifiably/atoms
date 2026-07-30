# A4a consolidated final-review fix report

## Outcome

The consolidated final-review pass closes the remaining A4a capability-backend
findings without widening A4a's authority or discharging any A4b/A5 obligation.
The implementation now fails before C-string truncation or empty-root aliasing,
keeps SQLite certification failures inside their documented refusal contract,
strictly validates descriptor-bound proc evidence, and restores the requested test
and fixture hygiene.

Base commit:

```text
0c3c30c8b74b2d0029d9eef96872fb3274444753
```

## Production corrections

### Pathname boundaries

- `establish_root` refuses empty and NUL-containing logical root spellings with
  `ProtocolError` before guarded spelling, traversal, normalization, or mutation.
- `verified_child_path` refuses a NUL-containing component before `fstat`; the
  `ProjectBinding.verified_metadata_path` boundary inherits the same contract.
- `LinuxBackend` validates every encoded pathname before calling a raw wrapper.
- The raw `openat2` and `renameat2` wrappers independently validate every pathname
  operand before selecting or invoking either libc route. Raw C-string boundary
  failures are `ValueError("embedded null byte")`.
- Empty low-level component spellings retain their existing kernel behavior; only a
  logical root spelling is required to be nonempty.

### SQLite-WAL certification

- Parent `sqlite3.OperationalError` is converted separately for connection, WAL
  selection, synchronous configuration, the initial transaction, `BEGIN`, `COMMIT`,
  and final verification. Each refusal names its phase and retains the SQLite error
  as `__cause__`.
- Non-operational programming faults are not caught by that conversion.
- The controlled contender-child test now runs the fixed production child script
  against a non-`SQLITE_BUSY` operational result and requires `WRONG_REFUSAL`.
- `certify_sqlite_wal(cleanup=True)` attempts database, WAL, and SHM cleanup after
  success, refusal, or child timeout. It attempts all three names and preserves an
  in-flight certification failure when cleanup also fails.

### Proc evidence and public architecture

- `mountinfo` validation now requires one separator, all required fields, exactly
  three post-separator fields, unsigned IDs and device numbers, unique mount IDs,
  and nonempty option tokens.
- `fdinfo` validation requires exactly one `mnt_id:` record with exactly one unsigned
  decimal token.
- Proc reads use `surrogateescape`, so unrelated non-UTF-8 pathname bytes cannot
  prevent matching the held descriptor's mount ID.
- Only `\040`, `\011`, `\012`, and `\134` are decoded, in one pass; unsupported
  backslash escapes refuse contextually through `CapabilityUnavailable`.
- `select_backend() -> Backend` imports the portable protocol eagerly while retaining
  lazy Linux/syscall loading.
- The non-Linux reload test restores the `atoms.fs.platform` package attribute, and
  the public-surface test requires every `__all__` entry to be bound.

### Resource and documentation hygiene

- Both filesystem-volume fixtures now yield from `TemporaryDirectory`, including
  skip and exceptional teardown paths.
- The early-lock-release binding test closes both the binding and lock in `finally`.
- The approved design and implementation plan record the corrected pathname,
  SQLite, proc-parser, cleanup, and architecture contracts.

## RED/GREEN evidence

The new tests were first run against the base implementation:

```text
NUL/empty-root groups:
  raw syscalls       6 failed
  backend adapter   11 failed before narrowing to the six raw-backed string cases
  root lock          3 failed
  child verifier     1 failed
  binding boundary   1 failed

SQLite focused group:
  11 failed, 2 passed
  failures covered all seven parent phases plus cleanup on refusal/timeout and
  preservation of the certification failure when cleanup also failed

proc-parser focused group:
  17 failed, 1 passed

architecture focused group:
  2 failed, 2 passed
```

After implementation, the restored mutation-related filesystem gate passed:

```text
162 passed in 0.95s
```

## Mutation evidence

Each mutation was applied to production temporarily, the narrow regression test was
run, and the mutation was immediately restored:

```text
raw NUL guard disabled:
  7 failed
  included the real existing-prefix open and both rename operands on both routes

OperationalError catch broadened to Exception:
  1 failed
  the programming fault was incorrectly converted

SQLite phase removed from the refusal:
  7 failed
  every parent phase assertion rejected the generic diagnostic

non-BUSY child classification disabled:
  1 failed
  the child exited OK instead of WRONG_REFUSAL

fdinfo one-token check weakened:
  1 failed, 4 passed
  the trailing token was incorrectly accepted

StorageProfile binding removed while left in __all__:
  1 failed
  the public-surface existence assertion named StorageProfile
```

No mutation remained in the final diff.

## Verification

The first full pass established that runtime behavior and formatting were green:

```text
$ .venv/bin/pytest -ra
4483 passed in 8.51s

$ .venv/bin/ruff check .
All checks passed!
```

That pass found one test-only Pyright error: direct static access to the package
attribute restored by the reload sentinel. The assertion was changed to inspect the
module namespace through `vars(atoms.fs)`, after which the affected test file and
Pyright passed:

```text
$ .venv/bin/pytest -ra tests/test_fs_architecture.py
26 passed in 0.31s

$ .venv/bin/pyright
0 errors, 0 warnings, 0 informations
```

The complete pytest, Ruff, Pyright, and diff gates were then rerun from the final
source state before commit.

## Deferred obligations

Deferred-obligation ledger entries 6, 16, 17, and 18 remain open under A4b/A5. This
fix pass admits no new execution shape, adds no compatibility layer, and changes no
A4a/A4b ownership boundary.

## Concerns

None blocking.
