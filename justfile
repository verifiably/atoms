# Front door for tests. Focused: `just test-one <pytest args>`. Inner loop: `just
# test-fast` (affected-only via pytest-testmon). Full suite: `just test [pytest args]`;
# `just test-full` forces it past policy. Gates: `just check` (check-hygiene, then
# check-code) and `just gate` (check, then test).
# Execution policy comes from .tt.toml (absent: enabled). Broad targets (test,
# test-fast, check-code, ci-python) may be skipped by it and are recorded as skipped;
# check-hygiene always runs. Hosted CI runs the forced suite on every pushed branch.
# The git hooks in .githooks/ call the `hook-*` recipes, which time the hygiene under
# their own target names and then run the broad children, each recorded on its own.
# Every recipe runs through the vendored timing wrapper tools/tt (source of truth: ops
# bin/tt) so the run is recorded. Design: ops docs/specs/2026-09-04-test-ci-audit-design.md,
# ops docs/specs/2026-10-29-test-execution-policy-design.md.

set quiet
set positional-arguments

tt := "python3 tools/tt"

# The three commands, each written once. Recipes and hooks all run these, so a hook can
# never drift from the gate it is supposed to be. Avoid single quotes inside them.
# The package lives under python/; `uv run --frozen` syncs the dev group from the lock
# file (pytest, pytest-testmon, pytest-xdist, ruff, pyright) and never rewrites it from a
# hook. testmon keeps its selection data in python/.testmondata, per checkout and ignored.
# The full suite runs on xdist workers: serially it took a median 383 s, and 8 workers
# with work-stealing took it to 133 s with no fixture change (the development host,
# 2026-10-17, atoms-286555); the exerciser's ~80 s SIGKILL arm is now the critical path.
# The worker count is the host's budget: local runs go through `host-budget run` (ops),
# which sets PYTEST_XDIST_AUTO_NUM_WORKERS for `-n auto` (design: ops
# docs/specs/2026-09-24-host-budget-design.md). A CI runner has no host-budget, so the
# workflow sets the count explicitly, and `ci-python` refuses to run without one.
# test-fast also leaves out the three fresh-process suites (the SIGKILL matrix, the
# persistence-cut matrix, the exerciser): on 2026-09-05 (atoms-83ae5c) they were ~400 of
# the serial suite's ~500 s and depended on nearly every source module, so with them in,
# any source edit reselected them and the loop ran 291-506s; without them the whole
# in-process suite was 106s and a single module's tests took seconds. testmon's
# tracing also sees only the parent process, so a change to code the children alone
# run (tests/execute_child.py, tests/coordinator_child.py) selects nothing. Both
# omissions are deterministic and named here; `just test` runs everything, and on the
# certified host it is the certification-tuple suite run (AGENTS.md, Gates).
fast_cmd := "cd python && uv run --frozen pytest --testmon --ignore=tests/test_coordinator_kill_matrix.py --ignore=tests/test_persistence_cut_matrix.py --ignore=tests/test_exerciser.py"
xdist := "-n auto --dist worksteal"
test_cmd := "cd python && uv run --frozen pytest " + xdist
one_cmd := "cd python && uv run --frozen pytest"
# Hygiene always runs; the code checks (ruff, pyright) are a broad target that tt policy
# governs. They are kept apart so a policy skip never suppresses hygiene.
check_hygiene_cmd := "python3 tools/ops-check && python3 tools/ops-docs check && tasks check"
check_code_cmd := "cd python && uv run --frozen ruff check && uv run --frozen pyright"
# Hygiene minus `tasks check`, whose binary is not on a runner, then the code checks;
# ops-check keeps working there because its registry lookup is empty when absent.
ci_check_cmd := "python3 tools/ops-check && python3 tools/ops-docs check && (" + check_code_cmd + ")"

# Remote refs whose push makes CI run the full suite, as space-separated fnmatch globs
# (`*` crosses `/`). The pre-push hook takes hook-pre-push-fast only when a push goes to
# ci_remote and every pushed ref matches. ci.yml runs on every branch push, and release.yml
# runs it through its gates job on every v* tag push. Setting this back to "" (with
# automatic_broad = true in .tt.toml) restores the full local gate at every push.
ci_suite_refs := "refs/heads/* refs/tags/v*"
ci_remote := "origin"

# Affected-only: the inner loop. An empty selection is a result, not a failure.
test-fast:
    {{tt}} test-fast -- sh -c '{{fast_cmd}}'

# One file, one test, or a filter, while working on it. Needs at least one argument;
# the whole suite is `test`. The sh -c string never holds the arguments, so quotes, $
# and globs in them reach the runner untouched.
test-one +args:
    {{tt}} test-one -- sh -c '{{one_cmd}} "$@" 2>&1' test-one "$@"

# The full suite. Arguments are forwarded to pytest after the default selection, each
# one intact (`just test -rs`, `just test -k "a and b"`); with none it runs everything.
test *args:
    {{tt}} test -- host-budget run -- sh -c '{{test_cmd}} "$@"' test "$@"

# Deliberate broad verification, whatever .tt.toml says; the inner `test` is the only
# timing record. Takes the same arguments as `test`.
test-full *args:
    TT_FORCE=1 just test "$@"

# Seconds: ops-check, ops-docs check, task-record check. Always runs.
check-hygiene:
    {{tt}} check-hygiene -- sh -c '{{check_hygiene_cmd}}'

# Seconds: ruff and pyright. A broad target.
check-code:
    {{tt}} check-code -- sh -c '{{check_code_cmd}}'

# Sequential, so a hygiene failure stops before the code checks.
check:
    just check-hygiene
    just check-code

gate:
    just check
    just test

# Regenerate the identity and family regions in README.md and AGENTS.md from
# identity.toml and tools/family.toml. The only thing that edits those regions; `check`
# refuses a commit while they are out of date.
docs:
    {{tt}} docs -- python3 tools/ops-docs write

# The certification runner (python/tools/certify) under the host budget: `host-budget run`
# grants a worker count as OPS_WORKERS, and --jobs bounds the concurrent QEMU guests by it.
# The single quotes keep $OPS_WORKERS for the shell inside the scope. The runner's own
# default, half the available CPUs, stays for a direct run without --jobs.
# The full sweep: `RECORDS_DIR="$(tasks where | jq -er '.project.records // empty')" && just certify --all --accel kvm --record "$RECORDS_DIR/docs/certification"`.
certify *args:
    {{tt}} certify -- host-budget run -- sh -c 'cd python && uv run --frozen python -m tools.certify run --jobs "$OPS_WORKERS" "$@"' certify {{args}}

# Hook timers cover the mandatory hygiene only; the broad children record their own runs
# (or policy skips) under their own targets.
hook-pre-commit:
    {{tt}} hook-pre-commit -- sh -c '{{check_hygiene_cmd}}'
    just check-code

# What the pre-push hook runs: hygiene, then the same children as `gate`.
hook-pre-push:
    {{tt}} hook-pre-push -- sh -c '{{check_hygiene_cmd}}'
    just check-code
    just test

# What the pre-push hook runs when CI runs the full suite for every pushed ref: the
# testmon loop stands in as the push selection.
hook-pre-push-fast:
    {{tt}} hook-pre-push-fast -- sh -c '{{check_hygiene_cmd}}'
    just check-code
    just test-fast

# Hosted evidence: ci-python writes its JUnit into ATOMS_CI_EVIDENCE_DIR, which ci.yml
# prepares, checks against pytest's collection and uploads. The directory must be an
# absolute path, so the report cannot land relative to python/ by accident; anything
# else stops the recipe with exit 2 before the suite starts.
ci_evidence_cmd := "case \"${ATOMS_CI_EVIDENCE_DIR:-}\" in /*) ;; *) echo \"ATOMS_CI_EVIDENCE_DIR must be set to an absolute path\" >&2; exit 2;; esac"
# The full suite with each skip's reason in the log (-rs) and every testcase in the JUnit.
ci_test_cmd := "cd python && uv run --frozen pytest " + xdist + " -rs --junitxml=\"$ATOMS_CI_EVIDENCE_DIR/pytest.xml\""

# CI runs the recipe rather than `just gate` so the whole job is one recorded number and
# cannot drift from the local gate (design section 4.6). The full suite, not the fast
# loop. A hosted runner is an uncertified host: its workspace sits on ext4, so the
# capability-dependent tests run there against the runner's kernel and volume, and only
# the few needing what the runner cannot supply at all (the certified tuple, a landed
# certification record, a capability the kernel lacks) skip with their reason. A hosted
# pass is never certified-tuple evidence (AGENTS.md, Gates).
#
# ci_test_cmd is parenthesised because it is a bare `cd python && ...`: unwrapped, its cd
# leaks into whatever follows, and ops-check would be looked for under python/.
#
# The worker count must be given: the workflow sets it for the runner, and a local
# reproduction takes the host's budget with `host-budget run -- just ci-python`. Without
# it `-n auto` would guess from the CPU count, so the recipe stops before the suite starts.
#
# The evidence guard, the full suite, then ops-check, ruff and pyright.
ci-python:
    {{tt}} ci-python -- sh -c ': "${PYTEST_XDIST_AUTO_NUM_WORKERS:?set by the CI job, or run as host-budget run -- just ci-python}" && {{ci_evidence_cmd}} && ({{ci_test_cmd}}) && {{ci_check_cmd}}'
