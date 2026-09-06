# Front door for tests. Inner loop: `just test-fast` (affected-only via pytest-testmon).
# Full suite: `just test`. Gates: `just check` at pre-commit, `just gate` at pre-push.
# The git hooks in .githooks/ call `hook-pre-commit` and `hook-pre-push`, which run the
# very same commands under their own target names so the report can price the hooks.
# Every recipe runs through the vendored timing wrapper tools/tt (source of truth: ops
# bin/tt) so the run is recorded. Design: ops docs/specs/2026-09-04-test-ci-audit-design.md.

tt := "python3 tools/tt"

# The three commands, each written once. Recipes and hooks all run these, so a hook can
# never drift from the gate it is supposed to be. Avoid single quotes inside them.
# The package lives under python/; `uv run --frozen` syncs the dev group from the lock
# file (pytest, pytest-testmon, ruff, pyright) and never rewrites it from a hook.
# testmon keeps its selection data in python/.testmondata, per checkout and ignored.
# test-fast also leaves out the three fresh-process suites (the SIGKILL matrix, the
# persistence-cut matrix, the exerciser): they are ~400 of the suite's ~500 seconds and
# depend on nearly every source module, so with them in, any source edit reselected
# them and the loop ran 291-506s (measured 2026-09-05, atoms-83ae5c); without them the
# whole in-process suite is 106s and a single module's tests take seconds. testmon's
# tracing also sees only the parent process, so a change to code the children alone
# run (tests/execute_child.py, tests/coordinator_child.py) selects nothing. Both
# omissions are deterministic and named here; `just test` runs everything and is the
# certification gate.
fast_cmd := "cd python && uv run --frozen pytest --testmon --ignore=tests/test_coordinator_kill_matrix.py --ignore=tests/test_persistence_cut_matrix.py --ignore=tests/test_exerciser.py"
test_cmd := "cd python && uv run --frozen pytest"
check_cmd := "python3 tools/ops-check && (cd python && uv run --frozen ruff check && uv run --frozen pyright) && tasks check"

# Affected-only: the inner loop. An empty selection is a result, not a failure.
test-fast:
    {{tt}} test-fast -- sh -c '{{fast_cmd}}'

# The full suite: the certification gate.
test:
    {{tt}} test -- sh -c '{{test_cmd}}'

# Seconds, not minutes: lint, typecheck, task-record check.
check:
    {{tt}} check -- sh -c '{{check_cmd}}'

gate: check test

# What the pre-commit hook runs: `check`'s command under its own hook target.
hook-pre-commit:
    {{tt}} hook-pre-commit -- sh -c '{{check_cmd}}'

# What the pre-push hook runs: the same commands as `gate`, under one hook target.
hook-pre-push:
    {{tt}} hook-pre-push -- sh -c '{{check_cmd}} && {{test_cmd}}'
