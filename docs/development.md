# Development and Verification Guide

Type: guide; document types 1. This guide covers setting up a development environment, running the local hygiene and test gates, configuring the test volume, understanding hosted CI coverage, and running the QEMU crash-certification harness. For the authoritative contracts, see [records.md](records.md), [protocol.md](protocol.md), and [durability.md](durability.md).

## Setup and layout

Install the locked Python environment and enable the repository git hooks from a checkout or
worktree root:

```sh
cd python && uv sync --frozen
git config core.hooksPath .githooks
```

The package lives under `python/src/atoms/` with strict downward dependency layers
(`coordinator -> {store, chain, fs, core}` and `{store, chain, fs} -> core`), accompanied by
the QEMU crash-certification harness under `python/tools/certify/`:

| Path | Responsibility |
|---|---|
| `python/src/atoms/core/` | Pure domain types and algorithms: relative paths (`paths.py`), `PathState` fingerprints (`fingerprint.py`), `.#~` scratch naming (`scratch.py`), primitive effects (`effects.py`), `TransactionSpec` and `compile_spec` (`spec.py`, `compile.py`), RFC 8785 JCS (`canonical.py`), `Capability` (`capabilities.py`), and pure recovery state-table classification (`recovery/`). |
| `python/src/atoms/fs/` | Capability backend protocol and `AuditedBackend` descriptor provenance tracker (`backend.py`), Linux syscall backend (`linux.py`), volume configuration and `CERTIFIED_ALLOWLIST` (`volume.py`), runtime volume and SQLite-WAL probes (`probe.py`), anchored path resolution (`resolve.py`), and project approval (`approval.py`). |
| `python/src/atoms/store/` | SQLite 3 WAL metadata store connection and pragma enforcement (`connection.py`), `SCHEMA_VERSION = 3` DDL (`schema.py`), typed row accessors (`records.py`), and content-addressed preimage/payload workspace (`workspace.py`). |
| `python/src/atoms/chain/` | Project-local `.#~chain/` entry schemas (`model.py`), canonical reader and DAG validator (`read.py`), staged atomic publication (`append.py`), and consumer inspection (`inspect.py`). |
| `python/src/atoms/coordinator/` | Advisory project lock (`lease.py`), root binding and lease context managers (`root.py`), root lifecycle state machine (`lifecycle.py`), admission (`admission.py`), coherent initial-surface capture (`capture.py`), SQLite preparation (`prepare.py`), effect execution and rollback (`execute.py`), commit finalization (`commit.py`), crash recovery (`recover.py`), and public entry points (`commands.py`). |
| `python/tools/certify/` | QEMU/KVM `dm-log-writes` power-cut recording, prefix replay harness, self-check suite (`self_check.py`), and canonical JSON evidence writer (`record.py`). |
| `python/tools/check_doc_links.py` | Markdown relative-link and heading-anchor verifier run by `just check-hygiene` and CI. |
| `python/tests/` | Pytest suites covering unit, property, subprocess kill-matrix, persistence-cut matrix, exerciser, and certification-record verification. |

## Gates and test policy

Every `just` recipe runs through the timing and policy wrapper `tools/tt`, configured by
`.tt.toml` at the repository root (`automatic_broad = false`). Under that policy, mandatory
hygiene always runs locally while broad code checks and full-suite runs are skipped unless
forced or run in hosted CI:

| Command | Scope | Policy behavior |
|---|---|---|
| `just check-hygiene` | Runs `python3 tools/ops-check`, `python3 tools/ops-docs check`, `tasks check`, and `python3 python/tools/check_doc_links.py`. | Always runs (mandatory on every `pre-commit` and `pre-push`). |
| `just test-one <pytest args>` | Runs `pytest` on focused files, tests, or `-k` expressions (paths relative to `python/`, e.g. `just test-one tests/test_compile.py`). | Always runs; primary inner-loop verification command. |
| `just check-code` | Runs `ruff check` and `pyright` inside `python/`. | Broad target; force locally with `TT_FORCE=1 just check-code` or `TT_FORCE=1 just check`. |
| `just test-fast` | Runs `pytest --testmon` excluding the three long subprocess suites (`test_coordinator_kill_matrix.py`, `test_persistence_cut_matrix.py`, `test_exerciser.py`). | Broad target; force with `TT_FORCE=1 just test-fast`. |
| `just test` / `just test-full` | Runs the complete pytest suite across `xdist` workers sized by `host-budget run` (`-n auto --dist worksteal`). | `just test` obeys `.tt.toml`; `just test-full [pytest args]` forces the full suite unconditionally. |
| `just docs` | Runs `python3 tools/ops-docs write` to regenerate the `ops:identity` and `ops:family` regions in `README.md` and `AGENTS.md` from `identity.toml` and `tools/family.toml`. | Always runs when invoked. |

## Test volume and capability tests

Filesystem capability and coordinator integration tests require a real Linux filesystem
(`ext4`, `xfs`, or `btrfs` for low-level backend tests, and non-casefold `ext4` for project
approval and coordinator tests) rather than `tmpfs`. Test volume resolution in
`python/tests/fs_support.py` (`resolve_test_volume`) follows two rules:

1. **`ATOMS_TEST_VOLUME` override** — if the `ATOMS_TEST_VOLUME` environment variable is set
   to an existing writable directory on `ext4`, `xfs`, or `btrfs`, tests allocate their
   temporary directories inside it.
2. **Repository `.atoms-test-volume` directory** — otherwise, if the repository checkout
   itself sits on `ext4`, `xfs`, or `btrfs`, tests create and use
   `<repo>/.atoms-test-volume/` automatically.

Two additional optional environment variables control specialized test fixtures:

- **`ATOMS_CASEFOLD_VOLUME`** — path to a writable directory on an `ext4` volume formatted
  with `-O casefold`, used by `python/tests/test_fs_resolve_casefold.py` to test kernel
  `FS_CASEFOLD_FL` rejection on live casefold directories. When unset, those 7 tests skip
  cleanly.
- **`ATOMS_CERTIFICATION_DIR`** — path to a directory containing
  `<date>-ext4-linux-<kernel>.json` certification records, used by
  `python/tests/test_certification_record.py` when neither an in-tree certification
  directory nor a local `records` checkout is present.

On the certified developer host (whose kernel and ext4 mount match `CERTIFIED_ALLOWLIST`),
coordinator tests exercise the production volume allowlist directly. On uncertified kernels
or runner filesystems, `python/tests/conftest.py` supplies an explicit test-only allowlist
fixture where appropriate while keeping production `certify_volume` fail-closed tests
intact.

## Hosted CI

GitHub Actions (`.github/workflows/ci.yml` and `.github/workflows/release.yml`) runs `just
ci-python` forced on every branch push and `v*` release tag:

1. Runs `python3 tools/ops-check`, `python3 tools/ops-docs check`, `python3
   python/tools/check_doc_links.py`, `ruff check`, and `pyright`.
2. Runs the full pytest suite on xdist workers and writes JUnit XML (`python/junit.xml`).
3. Runs `.github/scripts/check_ci_coverage.py` to verify that the JUnit report accounts for
   every test collected by `pytest --collect-only` and that only approved
   capability/environment skips occurred.

On standard GitHub-hosted Ubuntu runners, 11 tests skip by design:

- **7 casefold-volume tests** in `python/tests/test_fs_resolve_casefold.py`
  (`ATOMS_CASEFOLD_VOLUME` unset).
- **3 bind-mount traversal tests** in `python/tests/test_fs_binding.py` and
  `python/tests/test_fs_resolve_conformance.py` (unprivileged user/mount namespaces
  unavailable on the runner).
- **1 allowlist record verification test**
  (`test_certification_record_matches_the_production_allowlist` in
  `python/tests/test_certification_record.py`) when the private `records` repository is not
  paired with the checkout. On a paired workstation where `tasks where --json` resolves
  `project.records`, this test runs and fails closed if the referenced JSON file is missing
  or invalid.

Hosted CI proves functional and crash-model correctness on the runner's kernel, but never
substitutes for running `just test-full` on the certified ext4 host tuple.

## Crash certification and recertification

The QEMU `dm-log-writes` certification harness (`python/tools/certify/`) requires a Linux
host with `/dev/kvm`, `qemu-system-x86_64`, `e2fsprogs`, and a readable host kernel image
(`/boot/vmlinuz-$(uname -r)` or `/lib/modules/$(uname -r)/vmlinuz`).

Before running a full guest sweep, verify the harness's own replay and invariant-detection
logic with its fast self-check:

```sh
(cd python && uv run --frozen python -m tools.certify self-check)
```

To run the certification harness without writing a permanent evidence file (for example, a
single scenario or an exploratory trial):

```sh
just certify --scenario minimal-replace --accel kvm
```

Recording a canonical certification artifact is always opt-in via `--record <dir>`, where
`<dir>` is an existing directory (or one whose parent directory exists) and the run covers
`--all` scenarios with `--trials 1`:

```sh
# Write into the paired records repository:
RECORDS_DIR="$(tasks where | jq -er '.project.records // empty')" && \
  just certify --all --accel kvm --record "$RECORDS_DIR/docs/certification"

# Or write into a local scratch directory on an unpaired checkout:
just certify --all --accel kvm --record /tmp/atoms-certification
```

When recertifying after a host kernel upgrade (`chore/recertify-<kernel>`):

1. Run the full 9-scenario sweep with `--record "$RECORDS_DIR/docs/certification"` and
   commit the new `<date>-ext4-linux-<kernel>.json` file in `records`.
2. Update `CERTIFIED_ALLOWLIST` in `python/src/atoms/fs/volume.py` to reference the new
   kernel identifier and `certification_ref` (`<date>-ext4-linux-<kernel>.json`).
3. Run `TT_FORCE=1 just check` and `just test-full` on the newly certified host, confirming
   `test_certification_record_matches_the_production_allowlist` passes against the new
   record before merging the branch.
