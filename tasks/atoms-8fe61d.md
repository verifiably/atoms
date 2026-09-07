---
id: atoms-8fe61d
title: Publish atoms-core to PyPI
status: doing
priority: 2
size: m
owner: main
created: 2026-09-07T14:29:39Z
updated: 2026-09-07T17:09:07Z
depends: [ops-f1a933]
tags: [hygiene]
---

atoms-core 0.1.0 is declared in python/pyproject.toml but is not on PyPI, and atoms has no release workflow. beliefs declares atoms-core as a dependency and resolves it from ../../atoms/python as an editable sibling checkout, so beliefs cannot be published to PyPI until this is.

nodes is the working template: .github/workflows/release.yml triggers on a core/v* tag, runs the CI workflow as a gate, and refuses to build when pyproject and package.json versions disagree. atoms is Python-only, so the version-consistency step has nothing to compare and drops out.

atoms has no CI either. Add that first, the same shape as beliefs' (.github/workflows/ci.yml running the just recipes); atoms is public as of 2026-09-07, so standard runners are free.

Gated on ops-f1a933, which decides the naming scheme: verifiably-atoms is free on PyPI and atoms-core is not yet taken, so this task should not claim a name before that decision.

## Notes

- 2026-09-07T16:11:08Z (main): CI added first, before any release wiring: .github/workflows/ci.yml runs just ci-python across 3.11 and 3.13, reusing the pins beliefs and nodes already use. atoms is Python-only with no dependencies, so no sibling checkout is needed. ci_check_cmd drops only tasks check, whose binary is not on a runner; ops-check keeps working because its registry lookup is empty when absent. Unlike beliefs, no skip mechanism should be needed — atoms' volume fixtures already return a skip reason instead of raising when no certified volume exists.
- 2026-09-07T16:11:08Z (main): Bug found by dry-running the recipe before pushing: test_cmd here is a bare 'cd python && ...' with no subshell, so putting it first in a composed command leaked its cd into ops-check and the following cd python. The existing recipes never hit it because they put test_cmd last. Parenthesised in ci-python and noted in the justfile.
- 2026-09-07T16:20:32Z (main): Publishing mechanism for the release workflow: PyPI trusted publishing (OIDC), not a long-lived API token. It needs one action outside this repo that only the account owner can take — create a pending publisher on PyPI for verifiably-atoms naming owner verifiably, repository atoms, the release workflow's filename, and the environment if one is used. That does not reserve the name; the name is taken by the first successful publish.
- 2026-09-07T16:27:15Z (main): First CI run failed, and the prediction that atoms would skip gracefully was wrong: 700 failed, 5264 passed, 10 skipped, 269 errors in 59.4s on both 3.11 and 3.13, all from CapabilityUnavailable for EXT4_IOC_GET_TUNE_SB_PARAM. The ext4_volume and test_volume fixtures do skip, but 969 tests reach the fs layer without going through them and raise instead. Same wall beliefs hit, at roughly 16 percent of the suite rather than 5.
- 2026-09-07T16:29:27Z (main): Applied the same mechanism beliefs uses, under a stack-wide variable name: VERIFIABLY_UNCERTIFIED_HOST, since the exception both projects convert is raised from atoms and two names for one concept would be worse. beliefs renamed from BELIEFS_UNCERTIFIED_HOST in the same pass. Verified in atoms: closed by default, converts only CapabilityUnavailable under the opt-in, and a stray value does not disarm.
- 2026-09-07T16:58:50Z (main): The hook only covered the call phase, so the second CI run went 700 failed to 3, and 10 skipped to 707, while all 269 errors survived: those are CapabilityUnavailable raised inside fixtures, which is the setup phase and something pytest_runtest_call never sees. Wrapped setup, call and teardown; verified locally that a fixture-phase raise now converts and an ordinary error still fails. beliefs got the same fix — its capability failures all happen to be call-phase today, but the gap is not worth leaving.
- 2026-09-07T17:01:02Z (main): Three failures survive the hook, and both reasons are principled limits rather than defects. test_fs_binding.py's test_empty_allowlist_refuses and test_refusal_reclaims_existing_debris_but_writes_nothing_new use pytest.raises(CapabilityUnavailable, match=...) to assert a specific capability refusal; on an uncertified host a different CapabilityUnavailable fires first, the test catches it, and what fails is the regex — the exception never reaches the hook. test_exerciser.py's test_clean_and_caught_whole_cell_subprocess_placement runs its work in a child process and asserts on the child's output, so the capability failure happens where the parent's hook cannot see it. Both classes mean those tests genuinely cannot run without the certified tuple and need an explicit guard; a hand-maintained set of three is defensible where 969 was not.
- 2026-09-07T17:09:07Z (main): Option A implemented: ext4_feature_masks_or_skip_reason in tests/fs_support.py probes EXT4_IOC_GET_TUNE_SB_PARAM directly, and a certified_ext4 fixture skips on its reason. Three tests take it — the two in test_fs_binding.py that assert on a specific CapabilityUnavailable, and the exerciser one that drives a child process. Verified: all three still pass on the certified host, so the guard does not skip where the capability exists, and the helper returns a reason on a tmpfs volume. Every other capability-dependent test stays unannotated and handled by the hook.
