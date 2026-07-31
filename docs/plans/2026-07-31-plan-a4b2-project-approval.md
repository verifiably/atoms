# A4b-2 Rooted Project Approval Implementation Plan

**Status:** Planned 2026-07-31; unimplemented. A5–A8 remain unimplemented. A4b-2 reads project
space and never writes to it.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `approve_for_project`, the rooted proof that consumes A2's `CompiledSpec` and one held
project context and issues the `ProjectApprovedSpec` that A5–A8 accept in place of a raw specification.

**Architecture:** Three new modules under `atoms/fs/`. `topology.py` owns the approved value types,
node assignment, edges, the fact table, and the re-run of A2's surface and ordering rules over resolved
nodes. `judgment.py` owns ancestor legality, endpoint distinctness, and scratch binding. `approval.py`
owns `ProjectContext`, `ProjectApprovedSpec`, its construction token, and the four-phase pipeline —
the only module of the three that touches a filesystem, and only in phase B.

**Tech Stack:** Python 3.13, stdlib only (`os`, `dataclasses`, `itertools`), `pytest`, `ruff`,
`pyright`. Builds on A4b-1's `PathResolver`, `ResolvedPrefix`, `DirectoryConstraints`, and
`inherited_constraints`; A4a's `ProjectBinding`; A3's `RecoveryTopology` and `build_recovery_snapshot`;
A2's `CompiledSpec`.

**Design:** [`2026-07-31-a4b2-project-approval-design.md`](2026-07-31-a4b2-project-approval-design.md).
**Authority:** [`2026-07-23-recoverable-fs-effect-engine-design.md`](2026-07-23-recoverable-fs-effect-engine-design.md).
Where this plan and either document disagree, the design wins over this plan and the authority wins
over both.

## Global Constraints

- Work from `~/d/atoms/python`. Gates are `uv run pytest`, `uv run ruff check`, `uv run pyright`.
- **Ruff is pinned at 0.16.0 and its default rule set is much broader than `E4/E7/E9/F`** — on this
  checkout it includes isort (`I001`), bugbear (`B017`, `B018`, …), flake8-simplify (`SIM117`),
  bandit (`S`), `RUF012`, and blind-except (`BLE001`), alongside `F401`/`F811`/`F821`. The code below
  is written to pass it as given. Run `uv run ruff check <file>` right after creating each file rather
  than only at the task gate.
- **One expected transient:** ruff's isort classifies a module as first-party by *path existence*, so a
  test importing `atoms.fs.topology`, `atoms.fs.judgment`, or `atoms.fs.approval` before that module
  exists reports `I001` and proposes an order that becomes wrong once it does. Between "write the
  failing test" and "write the module" this is expected; **do not reorder imports to satisfy it.** It
  disappears at the step that creates the module, which is where each task's ruff gate sits.
- **pyright type-checks the tests** — `[tool.pyright]` sets no `include`. A `None` passed where a union
  is declared fails the gate in a test just as in production code, and a union-typed field must be
  narrowed with `isinstance` before a variant-only attribute is read.
- **The two gates conflict on frozen-value tests.** pyright rejects `value.field = x` on a frozen
  dataclass (`reportAttributeAccessIssue`); ruff's `B010` rejects `setattr(value, "field", x)` with a
  *constant* name. Only a variable name passes both, so every such test takes its field through
  `@pytest.mark.parametrize`.
- **Every fixture lands in `tests/conftest.py`.** `test_fs_fixture_registry_covers_every_test_argument`
  reads fixture names from `conftest.py` alone and reports a module-local `@pytest.fixture` in any
  `test_fs_*.py` as an unregistered test argument.
- **`approve_for_project` contains no `except` clause enclosing a resolver call.** Not a discriminating
  one, not a re-raising one. The correct count is zero (design §9, ledger #20).
- **`topology.py` and `judgment.py` are pure.** No `os`, no syscall, no descriptor. Every name
  comparison routes through `lookup_equivalence_key`; neither module compares a raw path component with
  `==`, `!=`, `in`, or set/dict membership.
- **A4b-2 never writes to project space.** No `mkdir`, `open(O_CREAT)`, `symlink`, or `unlink` under
  the project root in production code. Tests may create fixtures in their own temporary volume.
- `resolve.py` and `lookup.py` must continue to import none of `atoms.core.compiler`,
  `atoms.core.spec`, or `atoms.core.recovery`. The new modules may import all three.
- Non-casefold ext4 is the only approvable lookup proof, inherited unchanged from A4b-1.
- Filepaths in docs and comments use `~/d/atoms/...`.
- Conventional commits. No AI-attribution trailer or footer.

---

## File Structure

| File | Responsibility |
| --- | --- |
| `src/atoms/fs/lookup.py` | **Modify.** Add `lookup_equivalence_key`, beside `read_lookup_constraints` and `inherited_constraints`. |
| `src/atoms/fs/topology.py` | **Create.** The `Approved*` value types, `ResolvedTopology`, `build_topology`, `require_resolved_surface_and_ordering`. Pure. |
| `src/atoms/fs/judgment.py` | **Create.** `require_ancestors_legal`, `require_endpoints_distinct`, `bind_scratch`. Pure. |
| `src/atoms/fs/approval.py` | **Create.** `ProjectContext`, `ProjectApprovedSpec`, the construction token, `approve_for_project`. |
| `tests/fs_support.py` | **Modify.** Synthetic `ResolvedPrefix` builders for the pure tiers. |
| `tests/conftest.py` | **Modify.** `injected_equivalence` (a factory), `truncate_to_eight`, `approval_context`. |
| `tests/test_fs_lookup.py` | **Modify.** `lookup_equivalence_key` cases. |
| `tests/test_fs_judgment.py` | **Create.** Tier 1 — ancestor legality, endpoint distinctness, scratch binding. |
| `tests/test_fs_topology.py` | **Create.** Tier 2 and Tier 3 — construction, the partition, A3 acceptance. |
| `tests/test_fs_approval.py` | **Create.** Phase A gates, pipeline ordering, refusal propagation. |
| `tests/test_fs_approval_conformance.py` | **Create.** Tier 4 — real ext4 end to end. |
| `tests/test_fs_architecture.py` | **Modify.** Tier 6 guards. |
| `AGENTS.md` | **Modify.** A4b status line. |

**The `Approved*` value types live in `topology.py`, not `approval.py`.** Design §4.1 assigns modules
by responsibility and does not place these types; putting them in `approval.py` would make
`topology.py` import `approval.py` while `approval.py` imports `topology.py`. The dependency runs
`approval → judgment → topology` with no cycle.

Eight tasks. Each ends with a deliverable a reviewer could reject while approving its neighbour.

---

## Task 1: The lookup equivalence key

**Files:**
- Modify: `src/atoms/fs/lookup.py`
- Modify: `tests/test_fs_lookup.py`

**Interfaces:**
- Consumes: `LookupProof`, `DirectoryConstraints`, `CapabilityUnavailable`.
- Produces: `lookup_equivalence_key(constraints: DirectoryConstraints, name: str) -> str`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_fs_lookup.py`:

```python
def test_the_equivalence_key_is_the_identity_under_exact_bytes():
    constraints = DirectoryConstraints(
        lookup_proof=LookupProof.EXACT_BYTES, name_max=255
    )
    for name in ("a", "A", "é", ".hidden", "x" * 255):
        assert lookup_equivalence_key(constraints, name) == name


@pytest.mark.parametrize(
    "proof", [proof for proof in LookupProof if proof is not LookupProof.EXACT_BYTES]
)
def test_the_equivalence_key_refuses_every_unreproducible_proof(proof):
    """Parametrized over the enum, not over the one member that exists today, so a
    future LookupProof fails this suite until someone decides what its key is."""
    constraints = DirectoryConstraints(lookup_proof=proof, name_max=255)
    with pytest.raises(CapabilityUnavailable) as caught:
        lookup_equivalence_key(constraints, "a")
    assert proof.value in str(caught.value)


def test_the_vocabulary_still_has_exactly_one_reproducible_proof():
    """Guards the design's claim that the folding path has no production route: if a
    second reproducible proof lands, the test that asserts one must be revisited."""
    reproducible = [
        proof
        for proof in LookupProof
        if proof is LookupProof.EXACT_BYTES
    ]
    assert len(reproducible) == 1
```

Add `lookup_equivalence_key` to the module's existing import line and `CapabilityUnavailable` if it is
not already imported.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_fs_lookup.py -k equivalence -v`
Expected: FAIL with `ImportError: cannot import name 'lookup_equivalence_key'`.

- [ ] **Step 3: Add the function**

Append to `src/atoms/fs/lookup.py`:

```python
def lookup_equivalence_key(constraints: DirectoryConstraints, name: str) -> str:
    """Return the key under which ``name`` collides with another name in this directory.

    Two names reach the same entry iff their keys are equal. For EXACT_BYTES the key is
    the name itself. Any other proof raises: a policy whose relation the engine cannot
    reproduce has no equivalence key either, and inventing one would be exactly the
    silent fallback this engine refuses. This mirrors inherited_constraints' treatment
    of an unapproved filesystem type.
    """
    if constraints.lookup_proof is LookupProof.EXACT_BYTES:
        return name
    raise CapabilityUnavailable(
        f"lookup proof {constraints.lookup_proof.value!r} has no reproducible name "
        f"equivalence, so {name!r} cannot be compared against sibling names"
    )
```

- [ ] **Step 4: Run the tests and the gates**

```bash
uv run pytest tests/test_fs_lookup.py -v
uv run ruff check src/atoms/fs/lookup.py tests/test_fs_lookup.py
uv run pyright
```
Expected: PASS, `All checks passed!`, `0 errors`.

- [ ] **Step 5: Commit**

```bash
git add src/atoms/fs/lookup.py tests/test_fs_lookup.py
git commit -m "feat(fs): derive name equivalence from a directory's lookup proof"
```

---

## Task 2: Ancestor legality

**Files:**
- Create: `src/atoms/fs/judgment.py`
- Modify: `tests/fs_support.py`
- Create: `tests/test_fs_judgment.py`

**Interfaces:**
- Consumes: `CompiledSpec`, `ResolvedPrefix`, `PresentFrontier`, `EntryKind`, `CreateDirectory`,
  `DeletePath`, `occurrences`, `ProjectApprovalRefused`, `ProtocolError`.
- Produces: `require_ancestors_legal(compiled: CompiledSpec, prefixes: Mapping[str, ResolvedPrefix]) -> None`;
  the `resolved_prefix` helper in `tests/fs_support.py`.

- [ ] **Step 1: Add the synthetic prefix builder**

Append to `tests/fs_support.py`. Every pure tier builds `ResolvedPrefix` values by hand, so the builder
belongs beside the other test support rather than in a fixture — it takes arguments and returns a
value, which a fixture cannot do without an extra factory layer:

```python
def directory_facts(inode: int, *, name_max: int = 255, device: int = 41) -> DirectoryFacts:
    """A synthetic existing directory. Distinct inodes give distinct identities."""
    return DirectoryFacts(
        identity=FilesystemIdentity(device=device, inode=inode),
        constraints=DirectoryConstraints(
            lookup_proof=LookupProof.EXACT_BYTES, name_max=name_max
        ),
    )


def resolved_prefix(
    path: str,
    *,
    existing_depth: int,
    frontier: Frontier | None = None,
    root_inode: int = 2,
    name_max: int = 255,
) -> ResolvedPrefix:
    """Build the ResolvedPrefix a real walk of ``path`` would produce.

    ``existing_depth`` is how many ancestor components resolved to directories. The
    frontier is the next component; everything after it is the remainder. Inodes are
    derived from the prefix string so two paths sharing an ancestor share its identity,
    which is what the topology's identity keying depends on.
    """
    components = path.split("/")
    hops = tuple(
        ResolvedHop(
            declared_component=components[index],
            facts=directory_facts(
                _synthetic_inode("/".join(components[: index + 1])), name_max=name_max
            ),
        )
        for index in range(existing_depth)
    )
    return ResolvedPrefix(
        root=directory_facts(root_inode, name_max=name_max),
        hops=hops,
        frontier_name=components[existing_depth],
        frontier=AbsentFrontier() if frontier is None else frontier,
        remainder=tuple(components[existing_depth + 1 :]),
    )


def _synthetic_inode(prefix: str) -> int:
    """Stable per prefix string, and never the root's inode."""
    return 1000 + (hash(prefix) % 100000)
```

Add to `tests/fs_support.py`'s imports:

```python
from atoms.fs.lookup import DirectoryConstraints, LookupProof
from atoms.fs.resolve import (
    AbsentFrontier,
    DirectoryFacts,
    FilesystemIdentity,
    Frontier,
    ResolvedHop,
    ResolvedPrefix,
)
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_fs_judgment.py`:

```python
"""Tier 1 — pure judgment over hand-built resolution tables (design §11.1)."""

from __future__ import annotations

import pytest

from atoms.core.compiler import compile_spec
from atoms.core.effects import (
    CreateDirectory,
    CreateFileNoClobber,
    DeletePath,
)
from atoms.core.errors import ProjectApprovalRefused, ProtocolError
from atoms.core.fingerprint import ABSENT, DirectoryState, FileState
from atoms.core.spec import build_spec
from atoms.fs.judgment import require_ancestors_legal
from atoms.fs.resolve import EntryKind, FilesystemIdentity, PresentFrontier
from tests.fs_support import resolved_prefix

EMPTY = "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def file_state(mode: int = 0o644) -> FileState:
    return FileState(content_hash=EMPTY, mode=mode, byte_len=0)


def compiled_for(*effects):
    """Compile a spec whose surfaces are derived from the effects, so every test states
    only what it is about."""
    initial: dict[str, object] = {}
    final: dict[str, object] = {}
    from atoms.core.effects import occurrences

    for effect in effects:
        for occurrence in occurrences(effect):
            initial.setdefault(occurrence.path, occurrence.pre)
            final[occurrence.path] = occurrence.post
    return compile_spec(
        build_spec(
            consumer_tag="test",
            intent_digest=EMPTY,
            initial_surface=initial,  # type: ignore[arg-type]
            final_surface=final,  # type: ignore[arg-type]
            effects=effects,
        )
    )


def test_a_fully_resolved_path_needs_no_ancestor_proof():
    compiled = compiled_for(CreateFileNoClobber("e1", "a/b/leaf", file_state()))
    prefixes = {"a/b/leaf": resolved_prefix("a/b/leaf", existing_depth=2)}
    require_ancestors_legal(compiled, prefixes)


def test_a_missing_ancestor_no_effect_creates_is_refused():
    compiled = compiled_for(CreateFileNoClobber("e1", "a/b/leaf", file_state()))
    prefixes = {"a/b/leaf": resolved_prefix("a/b/leaf", existing_depth=1)}
    with pytest.raises(ProjectApprovalRefused) as caught:
        require_ancestors_legal(compiled, prefixes)
    assert "a/b" in str(caught.value)


def test_a_created_ancestor_ordered_before_its_descendant_is_admitted():
    compiled = compiled_for(
        CreateDirectory("mk", "a/b", DirectoryState(mode=0o755)),
        CreateFileNoClobber("e1", "a/b/leaf", file_state()),
    )
    prefixes = {
        "a/b": resolved_prefix("a/b", existing_depth=1),
        "a/b/leaf": resolved_prefix("a/b/leaf", existing_depth=1),
    }
    require_ancestors_legal(compiled, prefixes)


def test_a_created_ancestor_ordered_after_its_descendant_is_refused():
    """A2 phase 13 refuses this lexically; the resolved re-check must not depend on that.
    The spec is therefore assembled without compile_spec's ordering phase having a say —
    it would refuse first — so the effects are ordered legally and the prefixes describe
    a deeper missing ancestor that only the later CreateDirectory could supply."""
    compiled = compiled_for(
        CreateFileNoClobber("e1", "a/b/leaf", file_state()),
        CreateDirectory("mk", "a/c", DirectoryState(mode=0o755)),
    )
    prefixes = {
        "a/b/leaf": resolved_prefix("a/b/leaf", existing_depth=1),
        "a/c": resolved_prefix("a/c", existing_depth=1),
    }
    with pytest.raises(ProjectApprovalRefused) as caught:
        require_ancestors_legal(compiled, prefixes)
    assert "a/b" in str(caught.value)


def test_a_regular_file_ancestor_the_timeline_converts_is_admitted():
    compiled = compiled_for(
        DeletePath("rm", "p", file_state()),
        CreateDirectory("mk", "p", DirectoryState(mode=0o755)),
        CreateFileNoClobber("e1", "p/q", file_state()),
    )
    blocked = resolved_prefix(
        "p/q",
        existing_depth=0,
        frontier=PresentFrontier(
            identity=FilesystemIdentity(device=41, inode=77),
            kind=EntryKind.REGULAR_FILE,
        ),
    )
    prefixes = {"p": resolved_prefix("p", existing_depth=0), "p/q": blocked}
    require_ancestors_legal(compiled, prefixes)


def test_a_regular_file_ancestor_the_timeline_leaves_alone_is_refused():
    compiled = compiled_for(
        CreateDirectory("mk", "p", DirectoryState(mode=0o755)),
        CreateFileNoClobber("e1", "p/q", file_state()),
    )
    blocked = resolved_prefix(
        "p/q",
        existing_depth=0,
        frontier=PresentFrontier(
            identity=FilesystemIdentity(device=41, inode=77),
            kind=EntryKind.REGULAR_FILE,
        ),
    )
    prefixes = {"p": resolved_prefix("p", existing_depth=0), "p/q": blocked}
    with pytest.raises(ProjectApprovalRefused) as caught:
        require_ancestors_legal(compiled, prefixes)
    assert "removes" in str(caught.value)


def test_an_other_ancestor_is_refused_whatever_the_timeline_says():
    """No closed effect variant accepts an OTHER precondition — DeletePath.pre is a file
    or a symlink — so no admissible timeline can turn a socket into a directory."""
    compiled = compiled_for(
        CreateDirectory("mk", "p", DirectoryState(mode=0o755)),
        CreateFileNoClobber("e1", "p/q", file_state()),
    )
    blocked = resolved_prefix(
        "p/q",
        existing_depth=0,
        frontier=PresentFrontier(
            identity=FilesystemIdentity(device=41, inode=77), kind=EntryKind.OTHER
        ),
    )
    prefixes = {"p": resolved_prefix("p", existing_depth=0), "p/q": blocked}
    with pytest.raises(ProjectApprovalRefused) as caught:
        require_ancestors_legal(compiled, prefixes)
    assert "other" in str(caught.value)


def test_a_directory_frontier_with_a_remainder_is_a_protocol_error():
    """open_child_directory succeeds on a directory, so the walk would not have stopped.
    Asserted rather than assumed, because it is a claim about openat2 and not about this
    module."""
    compiled = compiled_for(CreateFileNoClobber("e1", "p/q", file_state()))
    blocked = resolved_prefix(
        "p/q",
        existing_depth=0,
        frontier=PresentFrontier(
            identity=FilesystemIdentity(device=41, inode=77), kind=EntryKind.DIRECTORY
        ),
    )
    with pytest.raises(ProtocolError):
        require_ancestors_legal(compiled, {"p/q": blocked})
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_fs_judgment.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'atoms.fs.judgment'`.

- [ ] **Step 4: Write `judgment.py`**

Create `src/atoms/fs/judgment.py`:

```python
"""Pure judgment over a resolution table (A4b-2 design §6.3).

No filesystem access, no descriptor, no syscall. Every function here takes values the
resolution phase already produced and either returns or raises.
"""

from __future__ import annotations

from collections.abc import Mapping

from atoms.core.compiler import CompiledSpec
from atoms.core.effects import CreateDirectory, DeletePath, occurrences
from atoms.core.errors import ProjectApprovalRefused, ProtocolError
from atoms.fs.resolve import EntryKind, PresentFrontier, ResolvedPrefix

# The frontier kinds a closed effect variant can remove. DeletePath.pre is typed
# FileState | SymlinkState, so no admissible timeline removes anything else — which is
# why OTHER is refused on a model ground rather than on an observation.
_REMOVABLE_KINDS = frozenset({EntryKind.REGULAR_FILE, EntryKind.SYMLINK})


def require_ancestors_legal(
    compiled: CompiledSpec, prefixes: Mapping[str, ResolvedPrefix]
) -> None:
    """Every component past the frontier is a directory this transaction creates first.

    Two routes reach the same rule. A missing component simply does not exist yet. A
    component that exists as a file or symlink is the authority §6 case where absence is
    inferred from the ancestor's verified state rather than probed; it is admitted only
    when the timeline removes it and creates a directory in its place.
    """
    creators = {
        effect.path: index
        for index, effect in enumerate(compiled.spec.effects)
        if isinstance(effect, CreateDirectory)
    }
    removers = {
        effect.path: index
        for index, effect in enumerate(compiled.spec.effects)
        if isinstance(effect, DeletePath)
    }
    first_touch: dict[str, int] = {}
    for index, effect in enumerate(compiled.spec.effects):
        for occurrence in occurrences(effect):
            first_touch.setdefault(occurrence.path, index)

    for path in sorted(prefixes):
        prefix = prefixes[path]
        if not prefix.remainder:
            continue
        components = path.split("/")
        depth = len(prefix.hops)
        _require_frontier_convertible(path, prefix, components[depth])
        for index in range(depth, len(components) - 1):
            ancestor = "/".join(components[: index + 1])
            _require_created_first(ancestor, path, creators, first_touch)
        if isinstance(prefix.frontier, PresentFrontier):
            _require_removed_before_creation(
                components[depth], path, creators, removers, len(prefix.hops)
            )


def _require_frontier_convertible(
    path: str, prefix: ResolvedPrefix, component: str
) -> None:
    frontier = prefix.frontier
    if not isinstance(frontier, PresentFrontier):
        return
    if frontier.kind is EntryKind.DIRECTORY:
        raise ProtocolError(
            f"resolution of {path!r} stopped at directory {component!r} with "
            f"{len(prefix.remainder)} components remaining; open_child_directory "
            "succeeds on a directory, so the walk should have continued"
        )
    if frontier.kind not in _REMOVABLE_KINDS:
        raise ProjectApprovalRefused(
            f"component {component!r} of {path!r} is {frontier.kind.value}, which no "
            "effect variant can remove, so no timeline can make it a directory"
        )


def _require_created_first(
    ancestor: str,
    path: str,
    creators: Mapping[str, int],
    first_touch: Mapping[str, int],
) -> None:
    creator = creators.get(ancestor)
    if creator is None:
        raise ProjectApprovalRefused(
            f"{path!r} needs directory {ancestor!r}, which does not exist and which no "
            "CreateDirectory effect creates; a parent that neither exists nor is "
            "created by this transaction cannot be captured"
        )
    if creator >= first_touch[path]:
        raise ProjectApprovalRefused(
            f"{path!r} is touched by effect {first_touch[path]} but its ancestor "
            f"{ancestor!r} is created by effect {creator}; outer directory creation "
            "must precede every affected descendant"
        )


def _require_removed_before_creation(
    component: str,
    path: str,
    creators: Mapping[str, int],
    removers: Mapping[str, int],
    depth: int,
) -> None:
    """A blocking non-directory must be removed, then re-created as a directory.

    This is design §5.2's one bounded exception to "approval does not compare live state
    against a declared precondition": resolution stopped here, so the topology cannot be
    built without deciding whether this path becomes a directory, and the observation is
    already in hand.
    """
    components = path.split("/")
    blocking = "/".join(components[: depth + 1])
    remover = removers.get(blocking)
    creator = creators[blocking]  # _require_created_first already proved it exists
    if remover is None:
        raise ProjectApprovalRefused(
            f"{blocking!r} exists and is not a directory, but no effect removes it "
            f"before effect {creator} creates a directory there"
        )
    if remover >= creator:
        raise ProjectApprovalRefused(
            f"{blocking!r} is removed by effect {remover} and created by effect "
            f"{creator}; the removal must come first"
        )
```

- [ ] **Step 5: Run the tests and the gates**

```bash
uv run pytest tests/test_fs_judgment.py -v
uv run ruff check src/atoms/fs/judgment.py tests/
uv run pyright
```
Expected: PASS, `All checks passed!`, `0 errors`.

- [ ] **Step 6: Commit**

```bash
git add src/atoms/fs/judgment.py tests/test_fs_judgment.py tests/fs_support.py
git commit -m "feat(fs): prove every unresolved ancestor is created in order"
```

---

## Task 3: Topology construction

**Files:**
- Create: `src/atoms/fs/topology.py`
- Create: `tests/test_fs_topology.py`

**Interfaces:**
- Consumes: `CompiledSpec`, `ResolvedPrefix`, `DirectoryConstraints`, `inherited_constraints`,
  `lookup_equivalence_key`, A3's `ProjectRoot`, `WorkRoot`, `TopologyDirectory`, `PersistentNode`,
  `ScratchNode`, `TopologyParent`, `RecoveryTopology`, `required_scratch_role`, `ScratchRole`.
- Produces: `ApprovedExistingDirectory`, `ApprovedPlannedDirectory`, `ApprovedDirectory`,
  `ApprovedPath`, `ApprovedScratch`, `ApprovedWorkBase`, `ResolvedTopology`,
  `build_topology(compiled, prefixes, filesystem_type, work_constraints) -> ResolvedTopology`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_fs_topology.py`:

```python
"""Tiers 2 and 3 — topology construction and A3 acceptance (design §11.2, §11.3)."""

from __future__ import annotations

import pytest

from atoms.core.effects import CreateDirectory, CreateFileNoClobber, MoveNoClobber
from atoms.core.fingerprint import DirectoryState
from atoms.core.recovery import (
    PersistentNode,
    ProjectRoot,
    ScratchNode,
    ScratchRole,
    TopologyDirectory,
    WorkRoot,
)
from atoms.fs.lookup import DirectoryConstraints, LookupProof
from atoms.fs.topology import (
    ApprovedExistingDirectory,
    ApprovedPlannedDirectory,
    build_topology,
)
from tests.fs_support import resolved_prefix
from tests.test_fs_judgment import compiled_for, file_state

EXT4 = "ext4"
WORK_CONSTRAINTS = DirectoryConstraints(
    lookup_proof=LookupProof.EXACT_BYTES, name_max=255
)


def test_an_undeclared_intermediate_becomes_a_topology_directory():
    compiled = compiled_for(CreateFileNoClobber("e1", "a/b/leaf", file_state()))
    prefixes = {"a/b/leaf": resolved_prefix("a/b/leaf", existing_depth=2)}
    resolved = build_topology(compiled, prefixes, EXT4, None)

    parent = resolved.parent_of("a/b/leaf")
    assert isinstance(parent, TopologyDirectory)
    grandparent = resolved.parent_node_of(parent)
    assert isinstance(grandparent, TopologyDirectory)
    assert resolved.parent_node_of(grandparent) == ProjectRoot()


def test_a_declared_intermediate_keeps_its_persistent_node():
    """A3 requires exact persistent coverage, so a declared directory may not also get a
    TopologyDirectory — that would be a second node for one directory."""
    compiled = compiled_for(
        CreateDirectory("mk", "a", DirectoryState(mode=0o755)),
        CreateFileNoClobber("e1", "a/leaf", file_state()),
    )
    prefixes = {
        "a": resolved_prefix("a", existing_depth=0),
        "a/leaf": resolved_prefix("a/leaf", existing_depth=0),
    }
    resolved = build_topology(compiled, prefixes, EXT4, WORK_CONSTRAINTS)
    assert resolved.parent_of("a/leaf") == PersistentNode("a")


def test_the_work_root_exists_exactly_when_a_create_directory_does():
    with_dir = compiled_for(CreateDirectory("mk", "a", DirectoryState(mode=0o755)))
    nodes = build_topology(
        with_dir, {"a": resolved_prefix("a", existing_depth=0)}, EXT4, WORK_CONSTRAINTS
    ).topology.parents
    assert any(edge.node == WorkRoot() for edge in nodes)

    without = compiled_for(CreateFileNoClobber("e1", "leaf", file_state()))
    nodes = build_topology(
        without, {"leaf": resolved_prefix("leaf", existing_depth=0)}, EXT4, None
    ).topology.parents
    assert not any(edge.node == WorkRoot() for edge in nodes)


def test_work_scratch_parents_to_the_work_root_and_others_to_their_path():
    compiled = compiled_for(
        CreateDirectory("mk", "a", DirectoryState(mode=0o755)),
        CreateFileNoClobber("e1", "b/leaf", file_state()),
    )
    prefixes = {
        "a": resolved_prefix("a", existing_depth=0),
        "b/leaf": resolved_prefix("b/leaf", existing_depth=1),
    }
    resolved = build_topology(compiled, prefixes, EXT4, WORK_CONSTRAINTS)
    assert resolved.parent_node_of(ScratchNode("mk", ScratchRole.WORK)) == WorkRoot()
    assert resolved.parent_node_of(
        ScratchNode("e1", ScratchRole.STAGING)
    ) == resolved.parent_of("b/leaf")


def test_move_scratch_parents_to_the_source_not_the_destination():
    """A3's _validate_topology compares against the source's parent specifically."""
    compiled = compiled_for(MoveNoClobber("mv", "src/a", "dst/b", file_state()))
    prefixes = {
        "src/a": resolved_prefix("src/a", existing_depth=1),
        "dst/b": resolved_prefix("dst/b", existing_depth=1),
    }
    resolved = build_topology(compiled, prefixes, EXT4, None)
    assert resolved.parent_node_of(
        ScratchNode("mv", ScratchRole.ANCHOR)
    ) == resolved.parent_of("src/a")


def test_the_directory_partition_is_exactly_existing_root_and_intermediates():
    """Design §7.3's two invariants, asserted as a partition: no declared path can be an
    existing directory (no effect declares a DirectoryState precondition), and no
    undeclared intermediate can be planned (it would need a CreateDirectory naming it,
    which would make it declared)."""
    compiled = compiled_for(
        CreateDirectory("mk", "a", DirectoryState(mode=0o755)),
        CreateFileNoClobber("e1", "x/y/leaf", file_state()),
    )
    prefixes = {
        "a": resolved_prefix("a", existing_depth=0),
        "x/y/leaf": resolved_prefix("x/y/leaf", existing_depth=2),
    }
    resolved = build_topology(compiled, prefixes, EXT4, WORK_CONSTRAINTS)

    existing = {
        entry.node
        for entry in resolved.directories
        if isinstance(entry, ApprovedExistingDirectory)
    }
    planned = {
        entry.node
        for entry in resolved.directories
        if isinstance(entry, ApprovedPlannedDirectory)
    }
    assert all(
        isinstance(node, (ProjectRoot, TopologyDirectory)) for node in existing
    )
    assert all(isinstance(node, (WorkRoot, PersistentNode)) for node in planned)
    assert not existing & planned


def test_node_ids_are_reproducible_across_repeated_construction():
    compiled = compiled_for(
        CreateFileNoClobber("e1", "a/b/one", file_state()),
        CreateFileNoClobber("e2", "c/d/two", file_state()),
    )
    prefixes = {
        "a/b/one": resolved_prefix("a/b/one", existing_depth=2),
        "c/d/two": resolved_prefix("c/d/two", existing_depth=2),
    }
    first = build_topology(compiled, prefixes, EXT4, None)
    second = build_topology(compiled, prefixes, EXT4, None)
    assert first.topology == second.topology


def test_planned_directories_merge_under_a_folding_key(injected_equivalence):
    """CreateDirectory("A") with an effect on a/x is one directory under a folding
    parent. An exact-bytes key passes every other test in this module and fails only
    this one. No LookupProof member is both insensitive and reproducible, so the double
    is the only route to the behaviour (design §6.3.2).

    A2 admits the pair: its phase 4 key is the whole path, and "a" differs from "a/x"."""
    injected_equivalence(str.casefold)
    compiled = compiled_for(
        CreateDirectory("mk", "A", DirectoryState(mode=0o755)),
        CreateFileNoClobber("e1", "a/x", file_state()),
    )
    prefixes = {
        "A": resolved_prefix("A", existing_depth=0),
        "a/x": resolved_prefix("a/x", existing_depth=0),
    }
    resolved = build_topology(compiled, prefixes, EXT4, WORK_CONSTRAINTS)
    assert resolved.parent_of("a/x") == PersistentNode("A")


def test_planned_directories_stay_distinct_under_exact_bytes():
    compiled = compiled_for(
        CreateDirectory("mk", "A", DirectoryState(mode=0o755)),
        CreateDirectory("mk2", "a", DirectoryState(mode=0o755)),
        CreateFileNoClobber("e1", "a/x", file_state()),
    )
    prefixes = {
        "A": resolved_prefix("A", existing_depth=0),
        "a": resolved_prefix("a", existing_depth=0),
        "a/x": resolved_prefix("a/x", existing_depth=0),
    }
    resolved = build_topology(compiled, prefixes, EXT4, WORK_CONSTRAINTS)
    assert resolved.parent_of("a/x") == PersistentNode("a")
```

- [ ] **Step 2: Register the folding fixtures in conftest**

Append to `tests/conftest.py`. Both go here because the fixture-registry guard reads `conftest.py`
alone:

```python
@pytest.fixture
def injected_equivalence(monkeypatch):
    """Install a chosen name-equivalence double on the modules that consume it.

    Patched by consuming-module path, like injected_lookup patches
    atoms.fs.resolve.read_lookup_constraints, because both modules bind the name at
    import time.

    A factory rather than a fixed double, because the two behaviours under test need
    different relations. Case folding merges directories (`A` and `a`), which A2 admits
    because its phase 4 key is the whole path and `A` differs from `a/x`. Case folding
    can NOT exercise endpoint distinctness: two leaves fold in one parent only when their
    whole paths fold too, and A2 phase 4 already refuses that pair before approval sees
    it. Endpoint tests therefore use a truncating relation — a real filesystem
    equivalence class that A2's key does not subsume.

    This is a double either way. It proves the call sites route through the function and
    merge whatever it merges; it proves nothing about any real relation, all of which
    stay unreproducible and refused. It also cannot detect a call site that bypasses the
    function — under an identity key a direct comparison behaves identically — which is
    what the AST guard in test_fs_architecture.py covers instead.
    """

    def install(key):
        for module in ("atoms.fs.topology", "atoms.fs.judgment"):
            monkeypatch.setattr(
                f"{module}.lookup_equivalence_key",
                lambda constraints, name: key(name),
            )

    return install


def truncate_to_eight(name: str) -> str:
    """A truncating name equivalence, in conftest so the registry guard sees it."""
    return name[:8]
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_fs_topology.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'atoms.fs.topology'`.

- [ ] **Step 4: Write `topology.py`**

Create `src/atoms/fs/topology.py`:

```python
"""The approved value types and the resolved topology (A4b-2 design §7, §8).

Pure. No filesystem access, no descriptor, no syscall. Every name comparison routes
through lookup_equivalence_key rather than comparing raw components, so widening the
lookup floor changes that function and nothing here.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from atoms.core.compiler import CompiledSpec
from atoms.core.effects import CreateDirectory, MoveNoClobber
from atoms.core.errors import ProtocolError
from atoms.core.recovery import (
    PersistentNode,
    ProjectRoot,
    RecoveryTopology,
    ScratchNode,
    ScratchRole,
    TopologyDirectory,
    TopologyNode,
    TopologyParent,
    WorkRoot,
    required_scratch_role,
)
from atoms.fs.lookup import (
    DirectoryConstraints,
    inherited_constraints,
    lookup_equivalence_key,
)
from atoms.fs.resolve import FilesystemIdentity, ResolvedPrefix

ROOT_PREFIX = ""


@dataclass(frozen=True, slots=True)
class ApprovedExistingDirectory:
    node: TopologyNode
    identity: FilesystemIdentity
    constraints: DirectoryConstraints


@dataclass(frozen=True, slots=True)
class ApprovedPlannedDirectory:
    node: TopologyNode
    constraints: DirectoryConstraints


ApprovedDirectory = ApprovedExistingDirectory | ApprovedPlannedDirectory


@dataclass(frozen=True, slots=True)
class ApprovedPath:
    path: str
    parent_node: TopologyNode
    leaf: str


@dataclass(frozen=True, slots=True)
class ApprovedScratch:
    effect_id: str
    role: ScratchRole
    parent_node: TopologyNode
    leaf: str


@dataclass(frozen=True, slots=True)
class ApprovedWorkBase:
    """The observed facts of physical metadata_root/work.

    Retained rather than consumed: ledger #19 requires A5 to re-resolve this namespace
    under the held lock before creating work/<txid>, and a re-resolution with no
    approved baseline is a fresh observation authorizing itself.
    """

    identity: FilesystemIdentity
    constraints: DirectoryConstraints


@dataclass(frozen=True, slots=True)
class ResolvedTopology:
    topology: RecoveryTopology
    directories: tuple[ApprovedDirectory, ...]
    paths: tuple[ApprovedPath, ...]

    def constraints_of(self, node: TopologyNode) -> DirectoryConstraints:
        """Derived from `directories` rather than stored twice, so the two cannot drift."""
        for entry in self.directories:
            if entry.node == node:
                return entry.constraints
        raise ProtocolError(f"no approved directory facts for topology node {node!r}")

    def parent_of(self, path: str) -> TopologyNode:
        for entry in self.paths:
            if entry.path == path:
                return entry.parent_node
        raise ProtocolError(f"no approved path entry for {path!r}")

    def parent_node_of(self, node: TopologyNode) -> TopologyNode:
        for edge in self.topology.parents:
            if edge.node == node:
                return edge.parent
        raise ProtocolError(f"topology node {node!r} has no parent edge")


def build_topology(
    compiled: CompiledSpec,
    prefixes: Mapping[str, ResolvedPrefix],
    filesystem_type: str,
    work_constraints: DirectoryConstraints | None,
) -> ResolvedTopology:
    """Build A3's production topology plus the node-keyed fact table.

    Caller guarantees require_ancestors_legal has already passed, so every prefix that
    does not exist is one this transaction creates.
    """
    declared = {timeline.path for timeline in compiled.timelines}
    facts = _facts_by_prefix(prefixes, filesystem_type)
    keys = _keys_by_prefix(facts)
    nodes = _nodes_by_key(keys, declared)

    directories = _directory_entries(facts, keys, nodes)
    edges: list[TopologyParent] = []
    for prefix in sorted(facts, key=_depth_then_name):
        if prefix == ROOT_PREFIX:
            continue
        parent = "/".join(prefix.split("/")[:-1])
        edges.append(
            TopologyParent(node=nodes[keys[prefix]], parent=nodes[keys[parent]])
        )

    paths = tuple(
        ApprovedPath(
            path=path,
            parent_node=nodes[keys["/".join(path.split("/")[:-1])]],
            leaf=path.split("/")[-1],
        )
        for path in sorted(declared)
    )
    for entry in paths:
        node = PersistentNode(entry.path)
        if node not in {edge.node for edge in edges}:
            edges.append(TopologyParent(node=node, parent=entry.parent_node))

    if work_constraints is not None:
        directories = (
            *directories,
            ApprovedPlannedDirectory(node=WorkRoot(), constraints=work_constraints),
        )
        edges.append(TopologyParent(node=WorkRoot(), parent=ProjectRoot()))

    edges.extend(_scratch_edges(compiled, paths))
    return ResolvedTopology(
        topology=RecoveryTopology(parents=tuple(edges)),
        directories=directories,
        paths=paths,
    )


def _depth_then_name(prefix: str) -> tuple[int, str]:
    """Parents before children, so a key is always available when a child needs it."""
    return (0, "") if prefix == ROOT_PREFIX else (len(prefix.split("/")), prefix)


def _facts_by_prefix(
    prefixes: Mapping[str, ResolvedPrefix], filesystem_type: str
) -> dict[str, tuple[FilesystemIdentity | None, DirectoryConstraints]]:
    """Attribute observed or derived facts to every directory prefix on every path.

    A prefix that some path resolved through is existing; one that no path resolved
    through is created by this transaction and inherits its parent's constraints.
    """
    facts: dict[str, tuple[FilesystemIdentity | None, DirectoryConstraints]] = {}
    for path in sorted(prefixes):
        prefix = prefixes[path]
        facts[ROOT_PREFIX] = (prefix.root.identity, prefix.root.constraints)
        components = path.split("/")
        for index, hop in enumerate(prefix.hops):
            key = "/".join(components[: index + 1])
            facts[key] = (hop.facts.identity, hop.facts.constraints)

    for path in sorted(prefixes):
        components = path.split("/")
        for index in range(len(components) - 1):
            key = "/".join(components[: index + 1])
            if key in facts:
                continue
            parent = "/".join(components[:index])
            _, parent_constraints = facts[parent]
            facts[key] = (
                None,
                inherited_constraints(parent_constraints, filesystem_type),
            )
    return facts


def _keys_by_prefix(
    facts: Mapping[str, tuple[FilesystemIdentity | None, DirectoryConstraints]],
) -> dict[str, object]:
    """One key per directory. Equal keys mean one directory reached two ways."""
    keys: dict[str, object] = {}
    for prefix in sorted(facts, key=_depth_then_name):
        identity, _ = facts[prefix]
        if identity is not None:
            keys[prefix] = identity
            continue
        parent = "/".join(prefix.split("/")[:-1])
        _, parent_constraints = facts[parent]
        leaf = prefix.split("/")[-1]
        keys[prefix] = (keys[parent], lookup_equivalence_key(parent_constraints, leaf))
    return keys


def _nodes_by_key(
    keys: Mapping[str, object], declared: frozenset[str] | set[str]
) -> dict[object, TopologyNode]:
    nodes: dict[object, TopologyNode] = {}
    next_id = 0
    for prefix in sorted(keys, key=_depth_then_name):
        key = keys[prefix]
        if prefix == ROOT_PREFIX:
            nodes[key] = ProjectRoot()
            continue
        if prefix in declared:
            existing = nodes.get(key)
            if isinstance(existing, PersistentNode) and existing.path != prefix:
                raise ProtocolError(
                    f"declared paths {existing.path!r} and {prefix!r} name one "
                    "directory; endpoint distinctness should have refused first"
                )
            nodes[key] = PersistentNode(prefix)
            continue
        if key not in nodes:
            nodes[key] = TopologyDirectory(next_id)
            next_id += 1
    return nodes


def _directory_entries(
    facts: Mapping[str, tuple[FilesystemIdentity | None, DirectoryConstraints]],
    keys: Mapping[str, object],
    nodes: Mapping[object, TopologyNode],
) -> tuple[ApprovedDirectory, ...]:
    entries: dict[TopologyNode, ApprovedDirectory] = {}
    for prefix in sorted(facts, key=_depth_then_name):
        identity, constraints = facts[prefix]
        node = nodes[keys[prefix]]
        if identity is None:
            entries[node] = ApprovedPlannedDirectory(node=node, constraints=constraints)
        else:
            entries[node] = ApprovedExistingDirectory(
                node=node, identity=identity, constraints=constraints
            )
    return tuple(entries.values())


def _scratch_edges(
    compiled: CompiledSpec, paths: tuple[ApprovedPath, ...]
) -> list[TopologyParent]:
    parent_by_path = {entry.path: entry.parent_node for entry in paths}
    edges: list[TopologyParent] = []
    for effect in compiled.spec.effects:
        role = required_scratch_role(effect)
        node = ScratchNode(effect.effect_id, role)
        if role is ScratchRole.WORK:
            edges.append(TopologyParent(node=node, parent=WorkRoot()))
            continue
        anchor = (
            effect.source if isinstance(effect, MoveNoClobber) else effect.path
        )
        edges.append(TopologyParent(node=node, parent=parent_by_path[anchor]))
    return edges
```

`CreateDirectory` is imported for `require_resolved_surface_and_ordering` in Task 4, not for this step;
if ruff reports `F401` on it here, leave the import out until Task 4 adds it.

- [ ] **Step 5: Run the tests and the gates**

```bash
uv run pytest tests/test_fs_topology.py -v
uv run ruff check src/atoms/fs/topology.py tests/
uv run pyright
```
Expected: PASS, `All checks passed!`, `0 errors`.

- [ ] **Step 6: Commit**

```bash
git add src/atoms/fs/topology.py tests/test_fs_topology.py tests/conftest.py
git commit -m "feat(fs): build the resolved topology over directory identity"
```

---

## Task 4: A3 acceptance and the resolved re-run

**Files:**
- Modify: `src/atoms/fs/topology.py`
- Modify: `tests/test_fs_topology.py`

**Interfaces:**
- Consumes: `build_recovery_snapshot`, `TransactionState`, `CommitDecision`, `EffectJournalState`,
  `JournalState`, `PersistentObservation`, `ScratchObservation`, `ObservedAbsent`.
- Produces: `require_resolved_surface_and_ordering(compiled: CompiledSpec, resolved: ResolvedTopology) -> None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_fs_topology.py`:

```python
def _prepared_snapshot(compiled, resolved):
    """Feed a produced topology to A3's own validator.

    This is the strongest single test in the design: _validate_topology independently
    encodes exact persistent and scratch coverage, single-parent-ness, the WorkRoot
    parent rule, the scratch/persistent shared-parent equality, and acyclicity — written
    before A4b-2 existed.
    """
    from atoms.core.recovery import (
        CommitDecision,
        EffectJournalState,
        JournalState,
        ObservedAbsent,
        PersistentObservation,
        ScratchObservation,
        TransactionState,
        build_recovery_snapshot,
        required_scratch_role,
    )

    return build_recovery_snapshot(
        compiled=compiled,
        topology=resolved.topology,
        transaction_state=TransactionState.PREPARED,
        commit_decision=CommitDecision.UNDECIDED,
        rollback_result=None,
        halt_diagnostic=None,
        active=True,
        journals=tuple(
            EffectJournalState(effect_id=effect.effect_id, state=JournalState.PENDING)
            for effect in compiled.spec.effects
        ),
        persistent_observations=tuple(
            PersistentObservation(path=timeline.path, entry=ObservedAbsent())
            for timeline in compiled.timelines
        ),
        scratch_observations=tuple(
            ScratchObservation(
                effect_id=effect.effect_id,
                role=required_scratch_role(effect),
                entry=ObservedAbsent(),
                file_build_relation=None,
            )
            for effect in compiled.spec.effects
        ),
    )


@pytest.mark.parametrize(
    "effects",
    [
        (CreateFileNoClobber("e1", "a/b/leaf", file_state()),),
        (
            CreateDirectory("mk", "a", DirectoryState(mode=0o755)),
            CreateFileNoClobber("e1", "a/leaf", file_state()),
        ),
        (MoveNoClobber("mv", "src/a", "dst/b", file_state()),),
    ],
)
def test_every_produced_topology_validates_through_a3(effects):
    compiled = compiled_for(*effects)
    prefixes = {}
    for timeline in compiled.timelines:
        depth = len(timeline.path.split("/")) - 1
        created = any(
            isinstance(effect, CreateDirectory) and timeline.path.startswith(effect.path)
            for effect in compiled.spec.effects
        )
        prefixes[timeline.path] = resolved_prefix(
            timeline.path, existing_depth=0 if created else depth
        )
    work = WORK_CONSTRAINTS if any(
        isinstance(effect, CreateDirectory) for effect in effects
    ) else None
    resolved = build_topology(compiled, prefixes, EXT4, work)
    snapshot = _prepared_snapshot(compiled, resolved)
    assert snapshot.topology == resolved.topology


def test_the_rerun_refuses_a_descendant_of_a_non_directory_surface_node():
    from atoms.fs.topology import require_resolved_surface_and_ordering

    compiled = compiled_for(
        CreateFileNoClobber("e1", "p", file_state()),
        CreateFileNoClobber("e2", "p/q", file_state()),
    )
    prefixes = {
        "p": resolved_prefix("p", existing_depth=0),
        "p/q": resolved_prefix("p/q", existing_depth=0),
    }
    resolved = build_topology(compiled, prefixes, EXT4, None)
    with pytest.raises(ProjectApprovalRefused):
        require_resolved_surface_and_ordering(compiled, resolved)


def test_the_rerun_reaches_a2s_verdict_on_every_compiled_input():
    """Under today's floor the resolved topology is provably identical to the lexical
    one, so these cannot disagree. A failure means the re-run drifted or the floor moved
    (design §7.4)."""
    from atoms.fs.topology import require_resolved_surface_and_ordering

    for effects in (
        (CreateFileNoClobber("e1", "a/b/leaf", file_state()),),
        (
            CreateDirectory("mk", "a", DirectoryState(mode=0o755)),
            CreateFileNoClobber("e1", "a/leaf", file_state()),
        ),
        (MoveNoClobber("mv", "src/a", "dst/b", file_state()),),
    ):
        compiled = compiled_for(*effects)
        prefixes = {
            timeline.path: resolved_prefix(
                timeline.path,
                existing_depth=0
                if any(
                    isinstance(effect, CreateDirectory)
                    and timeline.path.startswith(effect.path)
                    for effect in effects
                )
                else len(timeline.path.split("/")) - 1,
            )
            for timeline in compiled.timelines
        }
        work = WORK_CONSTRAINTS if any(
            isinstance(effect, CreateDirectory) for effect in effects
        ) else None
        resolved = build_topology(compiled, prefixes, EXT4, work)
        require_resolved_surface_and_ordering(compiled, resolved)
```

Add `from atoms.core.errors import ProjectApprovalRefused` to the module's imports.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_fs_topology.py -k "a3 or rerun" -v`
Expected: FAIL with `ImportError: cannot import name 'require_resolved_surface_and_ordering'`.

- [ ] **Step 3: Implement the re-run**

Append to `src/atoms/fs/topology.py`:

```python
def require_resolved_surface_and_ordering(
    compiled: CompiledSpec, resolved: ResolvedTopology
) -> None:
    """Re-derive A2's surface and ordering rules from resolved parentage.

    Authority §5.4 forbids reusing A2's lexical verdict, and gives the reason: on an
    insensitive parent, declared `A` and declared descendant `a/x` do not collide as
    endpoints yet `A` is genuinely the ancestor of `a/x`.
    """
    node_by_path = {entry.path: PersistentNode(entry.path) for entry in resolved.paths}
    parent_by_node = {edge.node: edge.parent for edge in resolved.topology.parents}

    for label in ("initial_surface", "final_surface"):
        states = {
            node_by_path[entry.path]: entry.state
            for entry in getattr(compiled.spec, label)
        }
        for node, state in states.items():
            ancestor = parent_by_node.get(node)
            while ancestor is not None:
                blocker = states.get(ancestor)
                if blocker is not None and not isinstance(blocker, AbsentState):
                    if not isinstance(blocker, DirectoryState) and not isinstance(
                        state, AbsentState
                    ):
                        raise ProjectApprovalRefused(
                            f"{label} places {node!r} beneath {ancestor!r}, which is "
                            f"{type(blocker).__name__} and cannot contain entries; "
                            "the descendant must be declared absent"
                        )
                ancestor = parent_by_node.get(ancestor)

    creators = {
        node_by_path[effect.path]: index
        for index, effect in enumerate(compiled.spec.effects)
        if isinstance(effect, CreateDirectory)
    }
    for index, effect in enumerate(compiled.spec.effects):
        for occurrence in occurrences(effect):
            ancestor = parent_by_node.get(node_by_path[occurrence.path])
            while ancestor is not None:
                creator = creators.get(ancestor)
                if creator is not None and creator >= index:
                    raise ProjectApprovalRefused(
                        f"effect {effect.effect_id!r} touches {occurrence.path!r} "
                        f"beneath a directory this transaction creates at effect "
                        f"{creator}; creation must come first"
                    )
                ancestor = parent_by_node.get(ancestor)
```

Add these imports to the module:

```python
from atoms.core.effects import CreateDirectory, MoveNoClobber, occurrences
from atoms.core.errors import ProjectApprovalRefused, ProtocolError
from atoms.core.fingerprint import AbsentState, DirectoryState
```

- [ ] **Step 4: Run the tests and the gates**

```bash
uv run pytest tests/test_fs_topology.py -v
uv run ruff check src/atoms/fs/topology.py tests/
uv run pyright
```
Expected: PASS, `All checks passed!`, `0 errors`.

- [ ] **Step 5: Commit**

```bash
git add src/atoms/fs/topology.py tests/test_fs_topology.py
git commit -m "feat(fs): re-run surface and ordering over resolved nodes"
```

---

## Task 5: Endpoint distinctness and scratch binding

**Files:**
- Modify: `src/atoms/fs/judgment.py`
- Modify: `tests/test_fs_judgment.py`

**Interfaces:**
- Consumes: `ResolvedTopology`, `ApprovedScratch`, `lookup_equivalence_key`, `scratch_leaf`,
  `required_scratch_role`, `WorkRoot`, `MoveNoClobber`.
- Produces: `require_endpoints_distinct(resolved: ResolvedTopology) -> None`;
  `bind_scratch(compiled: CompiledSpec, txid: str, resolved: ResolvedTopology) -> tuple[ApprovedScratch, ...]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_fs_judgment.py`:

```python
def test_distinct_leaves_in_one_parent_are_admitted():
    from atoms.fs.judgment import require_endpoints_distinct
    from atoms.fs.topology import build_topology

    compiled = compiled_for(
        CreateFileNoClobber("e1", "d/one", file_state()),
        CreateFileNoClobber("e2", "d/two", file_state()),
    )
    prefixes = {
        "d/one": resolved_prefix("d/one", existing_depth=1),
        "d/two": resolved_prefix("d/two", existing_depth=1),
    }
    require_endpoints_distinct(build_topology(compiled, prefixes, "ext4", None))


def test_two_paths_colliding_in_one_parent_are_refused(injected_equivalence):
    """Case folding cannot reach this check: two leaves fold in one parent only when
    their whole paths fold too, and A2 phase 4 refuses that pair before approval runs.
    A truncating relation — a real filesystem equivalence class A2's key does not
    subsume — is what makes the check observable."""
    from tests.conftest import truncate_to_eight

    from atoms.fs.judgment import require_endpoints_distinct
    from atoms.fs.topology import build_topology

    injected_equivalence(truncate_to_eight)
    compiled = compiled_for(
        CreateFileNoClobber("e1", "d/onelongname", file_state()),
        CreateFileNoClobber("e2", "d/onelongother", file_state()),
    )
    prefixes = {
        "d/onelongname": resolved_prefix("d/onelongname", existing_depth=1),
        "d/onelongother": resolved_prefix("d/onelongother", existing_depth=1),
    }
    resolved = build_topology(compiled, prefixes, "ext4", None)
    with pytest.raises(ProjectApprovalRefused) as caught:
        require_endpoints_distinct(resolved)
    assert "one entry" in str(caught.value)


def test_scratch_leaves_bind_to_their_effects_parent():
    from atoms.fs.judgment import bind_scratch
    from atoms.fs.topology import build_topology

    compiled = compiled_for(CreateFileNoClobber("e1", "d/leaf", file_state()))
    prefixes = {"d/leaf": resolved_prefix("d/leaf", existing_depth=1)}
    resolved = build_topology(compiled, prefixes, "ext4", None)
    bound = bind_scratch(compiled, "tx01", resolved)

    assert len(bound) == 1
    assert bound[0].effect_id == "e1"
    assert bound[0].leaf == ".#~tx01.e1.staging"
    assert bound[0].parent_node == resolved.parent_of("d/leaf")


def test_a_work_scratch_leaf_binds_to_the_work_root():
    from atoms.core.recovery import WorkRoot
    from atoms.fs.judgment import bind_scratch
    from atoms.fs.topology import build_topology
    from atoms.fs.lookup import DirectoryConstraints, LookupProof

    compiled = compiled_for(CreateDirectory("mk", "a", DirectoryState(mode=0o755)))
    prefixes = {"a": resolved_prefix("a", existing_depth=0)}
    resolved = build_topology(
        compiled,
        prefixes,
        "ext4",
        DirectoryConstraints(lookup_proof=LookupProof.EXACT_BYTES, name_max=255),
    )
    bound = bind_scratch(compiled, "tx01", resolved)
    assert bound[0].parent_node == WorkRoot()
    assert bound[0].leaf == ".#~tx01.mk.work"


def test_a_scratch_leaf_over_its_parents_name_max_is_refused():
    from atoms.fs.judgment import bind_scratch
    from atoms.fs.topology import build_topology

    compiled = compiled_for(CreateFileNoClobber("e1", "d/leaf", file_state()))
    prefixes = {"d/leaf": resolved_prefix("d/leaf", existing_depth=1, name_max=8)}
    resolved = build_topology(compiled, prefixes, "ext4", None)
    with pytest.raises(ProjectApprovalRefused) as caught:
        bind_scratch(compiled, "tx01", resolved)
    assert "name limit" in str(caught.value)


def test_colliding_scratch_leaves_are_refused(injected_equivalence):
    """Effect IDs are exact-string and portability-key unique after A2 phase 6, so under
    case folding no two scratch leaves can collide. Truncation can: every leaf shares the
    `.#~tx01.` prefix, so an eight-byte key collapses the whole set. Regeneration is
    explicitly not a remedy — an intrinsic collision recurs under every txid."""
    from tests.conftest import truncate_to_eight

    from atoms.fs.judgment import bind_scratch
    from atoms.fs.topology import build_topology

    compiled = compiled_for(
        CreateFileNoClobber("e1", "d/one", file_state()),
        CreateFileNoClobber("e2", "d/two", file_state()),
    )
    prefixes = {
        "d/one": resolved_prefix("d/one", existing_depth=1),
        "d/two": resolved_prefix("d/two", existing_depth=1),
    }
    resolved = build_topology(compiled, prefixes, "ext4", None)
    injected_equivalence(truncate_to_eight)
    with pytest.raises(ProjectApprovalRefused) as caught:
        bind_scratch(compiled, "tx01", resolved)
    assert "not a remedy" in str(caught.value)


def test_distinct_scratch_leaves_survive_under_exact_bytes():
    from atoms.fs.judgment import bind_scratch
    from atoms.fs.topology import build_topology

    compiled = compiled_for(
        CreateFileNoClobber("e1", "d/one", file_state()),
        CreateFileNoClobber("e2", "d/two", file_state()),
    )
    prefixes = {
        "d/one": resolved_prefix("d/one", existing_depth=1),
        "d/two": resolved_prefix("d/two", existing_depth=1),
    }
    resolved = build_topology(compiled, prefixes, "ext4", None)
    bound = bind_scratch(compiled, "tx01", resolved)
    assert len({entry.leaf for entry in bound}) == len(bound) == 2
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_fs_judgment.py -k "distinct or scratch" -v`
Expected: FAIL with `ImportError: cannot import name 'require_endpoints_distinct'`.

- [ ] **Step 3: Implement both functions**

Append to `src/atoms/fs/judgment.py`:

```python
def require_endpoints_distinct(resolved: ResolvedTopology) -> None:
    """No two declared paths name one entry under their parent's actual policy.

    Covers paths declared ABSENT, where identity comparison does not apply and is
    therefore not what is compared. Authority §5.4 gives the reason mutation-time
    no-clobber is not a substitute: `create x`, `delete x`, `create y` can make every
    no-clobber operation succeed while x and y aliasing leaves the declared final states
    unsatisfiable.
    """
    seen: dict[tuple[TopologyNode, str], str] = {}
    for entry in resolved.paths:
        constraints = resolved.constraints_of(entry.parent_node)
        key = (entry.parent_node, lookup_equivalence_key(constraints, entry.leaf))
        previous = seen.setdefault(key, entry.path)
        if previous != entry.path:
            raise ProjectApprovalRefused(
                f"declared paths {previous!r} and {entry.path!r} name one entry under "
                f"the actual lookup policy of their shared parent {entry.parent_node!r}"
            )


def bind_scratch(
    compiled: CompiledSpec, txid: str, resolved: ResolvedTopology
) -> tuple[ApprovedScratch, ...]:
    """Instantiate the complete scratch set and prove it pairwise distinct (ledger #11).

    Placement matches A3's _validate_topology exactly — WORK under the work root, every
    other role beside its effect's persistent path, with MoveNoClobber anchored to its
    source — so instantiation and topology construction cannot drift apart.
    """
    bound: list[ApprovedScratch] = []
    for effect in compiled.spec.effects:
        role = required_scratch_role(effect)
        parent = (
            WorkRoot()
            if role is ScratchRole.WORK
            else resolved.parent_of(
                effect.source if isinstance(effect, MoveNoClobber) else effect.path
            )
        )
        bound.append(
            ApprovedScratch(
                effect_id=effect.effect_id,
                role=role,
                parent_node=parent,
                leaf=scratch_leaf(txid, effect.effect_id, role.value),
            )
        )

    seen: dict[tuple[TopologyNode, str], str] = {}
    for entry in bound:
        constraints = resolved.constraints_of(entry.parent_node)
        width = len(entry.leaf.encode("utf-8"))
        if width > constraints.name_max:
            raise ProjectApprovalRefused(
                f"scratch leaf {entry.leaf!r} is {width} bytes, over its parent's "
                f"name limit of {constraints.name_max}"
            )
        key = (entry.parent_node, lookup_equivalence_key(constraints, entry.leaf))
        previous = seen.setdefault(key, entry.leaf)
        if previous != entry.leaf:
            raise ProjectApprovalRefused(
                f"scratch leaves {previous!r} and {entry.leaf!r} collide in one parent "
                "under its actual lookup policy; regenerating the txid is not a remedy "
                "because an intrinsic collision recurs under every txid"
            )
    return tuple(bound)
```

Add to `judgment.py`'s imports:

```python
from atoms.core.effects import CreateDirectory, DeletePath, MoveNoClobber, occurrences
from atoms.core.recovery import ScratchRole, TopologyNode, WorkRoot, required_scratch_role
from atoms.core.scratch import scratch_leaf
from atoms.fs.lookup import lookup_equivalence_key
from atoms.fs.topology import ApprovedScratch, ResolvedTopology
```

- [ ] **Step 4: Run the tests and the gates**

```bash
uv run pytest tests/test_fs_judgment.py -v
uv run ruff check src/atoms/fs/judgment.py tests/
uv run pyright
```
Expected: PASS, `All checks passed!`, `0 errors`.

- [ ] **Step 5: Commit**

```bash
git add src/atoms/fs/judgment.py tests/test_fs_judgment.py
git commit -m "feat(fs): prove endpoints and scratch leaves pairwise distinct"
```

---

## Task 6: The proof type and phase A

**Files:**
- Create: `src/atoms/fs/approval.py`
- Modify: `tests/conftest.py`
- Create: `tests/test_fs_approval.py`

**Interfaces:**
- Consumes: `ProjectBinding`, `CompiledSpec`, `require_valid_identifier`, `ResolvedTopology` members.
- Produces: `ProjectContext(binding: ProjectBinding, txid: str)`;
  `ProjectApprovedSpec(compiled, binding, txid, topology, directories, paths, scratch, work_base)`;
  `approve_for_project(compiled: CompiledSpec, context: ProjectContext) -> ProjectApprovedSpec`.

- [ ] **Step 1: Register the approval fixture**

Append to `tests/conftest.py`:

```python
@pytest.fixture
def approval_context(ext4_bound_volume):
    """A ProjectContext over a real ext4 binding, with a fixed txid."""
    import contextlib

    from atoms.fs.approval import ProjectContext

    @contextlib.contextmanager
    def build(txid: str = "tx01"):
        with ext4_bound_volume() as binding:
            yield ProjectContext(binding=binding, txid=txid), binding

    return build
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_fs_approval.py`:

```python
"""Phase A gates, pipeline ordering, and refusal propagation (design §6.1, §11.5)."""

from __future__ import annotations

import dataclasses

import pytest

from atoms.core.errors import (
    CapabilityUnavailable,
    PreconditionRefused,
    ProjectApprovalRefused,
    ProtocolError,
    SpecValidationError,
)
from atoms.fs.approval import ProjectApprovedSpec, ProjectContext, approve_for_project
from tests.test_fs_judgment import compiled_for, file_state
from atoms.core.effects import CreateFileNoClobber


class _FakeBinding:
    """Duck-types every attribute PathResolver reads. Must never reach proof issuance."""

    @property
    def backend(self):
        raise AssertionError("liveness must be checked before this is trusted")

    @property
    def project_root_fd(self):
        return 0

    @property
    def evidence(self):
        raise AssertionError("evidence must not be read before liveness")


def test_a_non_compiled_spec_is_refused(approval_context):
    with approval_context() as (context, _binding):
        with pytest.raises(ProtocolError):
            approve_for_project(object(), context)  # type: ignore[arg-type]


def test_a_duck_typed_binding_is_refused():
    compiled = compiled_for(CreateFileNoClobber("e1", "leaf", file_state()))
    context = ProjectContext(binding=_FakeBinding(), txid="tx01")  # type: ignore[arg-type]
    with pytest.raises(ProtocolError) as caught:
        approve_for_project(compiled, context)
    assert "ProjectBinding" in str(caught.value)


@pytest.mark.parametrize("txid", [3, None, b"tx01"])
def test_a_non_string_txid_raises_protocol_error_not_type_error(approval_context, txid):
    """require_valid_identifier reaches re.Pattern.fullmatch, which raises TypeError on a
    non-string. The exact-type gate is what makes §5.1's promised ProtocolError reachable."""
    compiled = compiled_for(CreateFileNoClobber("e1", "leaf", file_state()))
    with approval_context() as (context, binding):
        bad = ProjectContext(binding=binding, txid=txid)  # type: ignore[arg-type]
        with pytest.raises(ProtocolError):
            approve_for_project(compiled, bad)


def test_a_malformed_txid_carries_the_validation_error_as_its_cause(approval_context):
    compiled = compiled_for(CreateFileNoClobber("e1", "leaf", file_state()))
    with approval_context(txid="not a txid") as (context, _binding):
        with pytest.raises(ProtocolError) as caught:
            approve_for_project(compiled, context)
        assert isinstance(caught.value.__cause__, SpecValidationError)


def test_a_closed_binding_refuses_before_capabilities_are_compared(approval_context):
    """Both exceptions are reachable; only the §6.1 order distinguishes them. evidence
    performs no liveness check by design, so reading it first would report
    CapabilityUnavailable for a lease that is simply gone."""
    compiled = compiled_for(CreateFileNoClobber("e1", "leaf", file_state()))
    with approval_context() as (context, binding):
        binding.__exit__()
        with pytest.raises(ProtocolError):
            approve_for_project(compiled, context)


def test_the_proof_refuses_ordinary_construction_and_replace(approval_context):
    compiled = compiled_for(CreateFileNoClobber("e1", "leaf", file_state()))
    with approval_context() as (context, _binding):
        proof = approve_for_project(compiled, context)
        with pytest.raises(TypeError):
            ProjectApprovedSpec(
                compiled=proof.compiled,
                binding=proof.binding,
                txid=proof.txid,
                topology=proof.topology,
                directories=proof.directories,
                paths=proof.paths,
                scratch=proof.scratch,
                work_base=proof.work_base,
            )
        with pytest.raises(TypeError):
            dataclasses.replace(proof, txid="tx02")


def test_the_proof_retains_the_binding_object_it_approved(approval_context):
    compiled = compiled_for(CreateFileNoClobber("e1", "leaf", file_state()))
    with approval_context() as (context, binding):
        proof = approve_for_project(compiled, context)
        assert proof.binding is binding


@pytest.mark.parametrize(
    "raised",
    [
        ProjectApprovalRefused("refused"),
        PreconditionRefused("drifted"),
        CapabilityUnavailable("unavailable"),
        ProtocolError("contract"),
        OSError(5, "EIO"),
    ],
)
def test_every_resolver_exception_reaches_the_caller_unchanged(
    approval_context, monkeypatch, raised
):
    """Ledger #20 is categorical. Identity rather than type, because a type assertion is
    satisfied by any same-class exception the code might raise on its own."""
    compiled = compiled_for(CreateFileNoClobber("e1", "leaf", file_state()))

    def failing(self, rel_path):
        raise raised

    monkeypatch.setattr("atoms.fs.approval.PathResolver.resolve", failing)
    with approval_context() as (context, _binding):
        with pytest.raises(type(raised)) as caught:
            approve_for_project(compiled, context)
        assert caught.value is raised
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_fs_approval.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'atoms.fs.approval'`.

- [ ] **Step 4: Write `approval.py`**

Create `src/atoms/fs/approval.py`:

```python
"""Rooted project approval (A4b-2 design §6).

approve_for_project is the sole public construction authority for ProjectApprovedSpec.
No except clause in this module encloses a resolver call: ledger #20 is categorical, and
the correct count is zero rather than "no blanket handler".
"""

from __future__ import annotations

from dataclasses import dataclass

from atoms.core.compiler import CompiledSpec
from atoms.core.effects import CreateDirectory
from atoms.core.errors import CapabilityUnavailable, ProtocolError
from atoms.core.identifiers import require_valid_identifier
from atoms.core.recovery import RecoveryTopology
from atoms.fs.binding import ProjectBinding
from atoms.fs.judgment import (
    bind_scratch,
    require_ancestors_legal,
    require_endpoints_distinct,
)
from atoms.fs.lookup import inherited_constraints
from atoms.fs.resolve import PathResolver, ResolvedPrefix
from atoms.fs.topology import (
    ApprovedDirectory,
    ApprovedPath,
    ApprovedScratch,
    ApprovedWorkBase,
    build_topology,
    require_resolved_surface_and_ordering,
)

_TOKEN = object()


@dataclass(frozen=True, slots=True)
class ProjectContext:
    """Approval's input. Not a proof, so no construction token: a caller who can build
    one can call approve_for_project anyway, and guarding it would protect nothing."""

    binding: ProjectBinding
    txid: str


@dataclass(frozen=True, slots=True, init=False)
class ProjectApprovedSpec:
    """A4's factory-issued rooted proof.

    Guarded exactly like CompiledSpec, VolumeEvidence, and RecoverySnapshot: an explicit
    __init__ demanding the module-private token, so ordinary construction AND
    dataclasses.replace both refuse — replace() re-enters this __init__ without it.
    """

    compiled: CompiledSpec
    binding: ProjectBinding
    txid: str
    topology: RecoveryTopology
    directories: tuple[ApprovedDirectory, ...]
    paths: tuple[ApprovedPath, ...]
    scratch: tuple[ApprovedScratch, ...]
    work_base: ApprovedWorkBase | None

    def __init__(
        self,
        *,
        compiled: CompiledSpec,
        binding: ProjectBinding,
        txid: str,
        topology: RecoveryTopology,
        directories: tuple[ApprovedDirectory, ...],
        paths: tuple[ApprovedPath, ...],
        scratch: tuple[ApprovedScratch, ...],
        work_base: ApprovedWorkBase | None,
        _construction_token: object | None = None,
    ) -> None:
        if _construction_token is not _TOKEN:
            raise TypeError(
                "ProjectApprovedSpec values are created only by approve_for_project"
            )
        object.__setattr__(self, "compiled", compiled)
        object.__setattr__(self, "binding", binding)
        object.__setattr__(self, "txid", txid)
        object.__setattr__(self, "topology", topology)
        object.__setattr__(self, "directories", directories)
        object.__setattr__(self, "paths", paths)
        object.__setattr__(self, "scratch", scratch)
        object.__setattr__(self, "work_base", work_base)


def _require_exact(value: object, expected: type, label: str) -> None:
    if type(value) is not expected:
        raise ProtocolError(
            f"{label} must be exactly {expected.__name__}, got "
            f"{type(value).__name__}; a subclass would pass an isinstance gate and "
            "then break a later phase"
        )


def approve_for_project(
    compiled: CompiledSpec, context: ProjectContext
) -> ProjectApprovedSpec:
    """Prove the rooted rules and issue the proof A5-A8 accept."""
    # Phase A: context, no path I/O.
    _require_exact(compiled, CompiledSpec, "compiled")
    _require_exact(context, ProjectContext, "context")
    _require_exact(context.binding, ProjectBinding, "context.binding")
    _require_exact(context.txid, str, "context.txid")

    binding = context.binding
    backend = binding.backend  # liveness BEFORE evidence, which never checks it
    del backend

    try:
        require_valid_identifier("txid", context.txid)
    except SpecValidationError as caught:
        raise ProtocolError(
            f"approve_for_project requires a well-formed txid: {caught}"
        ) from caught

    evidence = binding.evidence
    missing = compiled.spec.required_capabilities() - evidence.supplied_capabilities
    if missing:
        raise CapabilityUnavailable(
            "the bound volume does not supply required capabilities: "
            + ", ".join(sorted(item.value for item in missing))
        )

    resolver = PathResolver(binding)
    filesystem_type = evidence.configuration.filesystem_type

    # Phase B: resolution, the only I/O.
    prefixes: dict[str, ResolvedPrefix] = {
        timeline.path: resolver.resolve(timeline.path)
        for timeline in sorted(compiled.timelines, key=lambda item: item.path)
    }
    work_base: ApprovedWorkBase | None = None
    work_constraints = None
    if any(isinstance(effect, CreateDirectory) for effect in compiled.spec.effects):
        facts = resolver.work_base_facts()
        work_base = ApprovedWorkBase(
            identity=facts.identity, constraints=facts.constraints
        )
        width = len(context.txid.encode("utf-8"))
        if width > facts.constraints.name_max:
            raise ProjectApprovalRefused(
                f"txid {context.txid!r} is {width} bytes, over the work base name "
                f"limit of {facts.constraints.name_max}"
            )
        work_constraints = inherited_constraints(facts.constraints, filesystem_type)

    # Phase C: judgment, pure.
    require_ancestors_legal(compiled, prefixes)
    resolved = build_topology(compiled, prefixes, filesystem_type, work_constraints)
    require_endpoints_distinct(resolved)
    require_resolved_surface_and_ordering(compiled, resolved)
    scratch = bind_scratch(compiled, context.txid, resolved)

    # Phase D: issue.
    return ProjectApprovedSpec(
        compiled=compiled,
        binding=binding,
        txid=context.txid,
        topology=resolved.topology,
        directories=resolved.directories,
        paths=resolved.paths,
        scratch=scratch,
        work_base=work_base,
        _construction_token=_TOKEN,
    )
```

Add `ProjectApprovalRefused` and `SpecValidationError` to the `atoms.core.errors` import line.

Note the one `try`/`except` in the module wraps `require_valid_identifier`, a pure A1 call — **not** a
resolver call. `PathResolver(...)`, `resolve(...)`, and `work_base_facts()` are all outside any
handler, which is what the AST guard in Task 8 asserts.

- [ ] **Step 5: Run the tests and the gates**

```bash
uv run pytest tests/test_fs_approval.py -v
uv run ruff check src/atoms/fs/approval.py tests/
uv run pyright
```
Expected: PASS, `All checks passed!`, `0 errors`.

- [ ] **Step 6: Commit**

```bash
git add src/atoms/fs/approval.py tests/test_fs_approval.py tests/conftest.py
git commit -m "feat(fs): issue the rooted project approval proof"
```

---

## Task 7: Real-filesystem conformance

**Files:**
- Create: `tests/test_fs_approval_conformance.py`

**Interfaces:**
- Consumes: `approve_for_project`, `ProjectContext`, the `ext4_*` fixtures A4b-1 added.
- Produces: nothing importable; this task is Tier 4.

- [ ] **Step 1: Write the conformance tests**

Create `tests/test_fs_approval_conformance.py`:

```python
"""Tier 4 — approve_for_project end to end against a real ext4 volume (design §11.4)."""

from __future__ import annotations

import os

import pytest

from atoms.core.effects import CreateDirectory, CreateFileNoClobber, DeletePath
from atoms.core.errors import ProjectApprovalRefused
from atoms.core.fingerprint import DirectoryState
from atoms.fs.approval import approve_for_project
from atoms.fs.lookup import read_lookup_constraints
from tests.test_fs_judgment import compiled_for, file_state


def test_a_wholly_resolvable_specification_approves(approval_context):
    with approval_context() as (context, binding):
        root = binding.project_root_fd
        os.mkdir("d", dir_fd=root)
        compiled = compiled_for(CreateFileNoClobber("e1", "d/leaf", file_state()))
        proof = approve_for_project(compiled, context)

        assert proof.txid == "tx01"
        assert len(proof.paths) == 1
        assert proof.paths[0].leaf == "leaf"
        assert proof.work_base is None


def test_work_base_is_retained_and_matches_an_independent_observation(approval_context):
    """Asserted against a fresh fstat and constraints read on metadata_root/work, so the
    retained baseline is checked against the filesystem rather than against the same call
    that produced it."""
    with approval_context() as (context, binding):
        compiled = compiled_for(
            CreateDirectory("mk", "made", DirectoryState(mode=0o755))
        )
        proof = approve_for_project(compiled, context)

        assert proof.work_base is not None
        fd = os.open(
            "work",
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC,
            dir_fd=binding.metadata_root_fd,
        )
        try:
            info = os.fstat(fd)
            constraints = read_lookup_constraints(
                fd, binding.evidence.configuration.filesystem_type
            )
        finally:
            os.close(fd)

        assert proof.work_base.identity.device == info.st_dev
        assert proof.work_base.identity.inode == info.st_ino
        assert proof.work_base.constraints == constraints


def test_a_file_ancestor_the_timeline_converts_approves_on_disk(approval_context):
    with approval_context() as (context, binding):
        root = binding.project_root_fd
        fd = os.open("p", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644, dir_fd=root)
        os.close(fd)
        compiled = compiled_for(
            DeletePath("rm", "p", file_state()),
            CreateDirectory("mk", "p", DirectoryState(mode=0o755)),
            CreateFileNoClobber("e1", "p/q", file_state()),
        )
        approve_for_project(compiled, context)


def test_a_component_over_name_max_is_refused(approval_context):
    with approval_context() as (context, _binding):
        compiled = compiled_for(
            CreateFileNoClobber("e1", "x" * 300, file_state())
        )
        with pytest.raises(ProjectApprovalRefused) as caught:
            approve_for_project(compiled, context)
        assert "NAME_MAX" in str(caught.value)


def test_a_path_over_path_max_is_refused(approval_context):
    with approval_context() as (context, _binding):
        deep = "/".join(["d"] * 3000)
        compiled = compiled_for(CreateFileNoClobber("e1", deep, file_state()))
        with pytest.raises(ProjectApprovalRefused) as caught:
            approve_for_project(compiled, context)
        assert "PATH_MAX" in str(caught.value)


def test_a_nested_metadata_root_is_refused_through_approval(
    ext4_nested_bound_volume,
):
    """Reached through approve_for_project rather than the resolver, which is what
    ledger #5 asks for."""
    from atoms.fs.approval import ProjectContext

    with ext4_nested_bound_volume() as binding:
        context = ProjectContext(binding=binding, txid="tx01")
        compiled = compiled_for(
            CreateFileNoClobber("e1", "metadata/leaf", file_state())
        )
        with pytest.raises(ProjectApprovalRefused) as caught:
            approve_for_project(compiled, context)
        assert "metadata root" in str(caught.value)
```

- [ ] **Step 2: Run the suite and the gates**

```bash
uv run pytest tests/test_fs_approval_conformance.py -v
uv run ruff check tests/
uv run pyright
```
Expected: PASS (or skip, on a non-ext4 checkout), `All checks passed!`, `0 errors`.

- [ ] **Step 3: Commit**

```bash
git add tests/test_fs_approval_conformance.py
git commit -m "test(fs): approve specifications against a real ext4 volume"
```

---

## Task 8: Architecture guards and documentation

**Files:**
- Modify: `tests/test_fs_architecture.py`
- Modify: `AGENTS.md`

**Interfaces:**
- Consumes: the existing `_resolved_imports` and `FORBIDDEN_FOR_RESOLUTION` helpers.
- Produces: nothing importable; this task is Tier 6.

- [ ] **Step 1: Add the guards**

Append to `tests/test_fs_architecture.py`:

```python
APPROVAL_MODULES = ("approval", "judgment", "topology")
PURE_MODULES = ("judgment", "topology")


def test_the_resolution_guard_still_names_exactly_the_two_mechanism_modules():
    """The seam is the guard's module list. If A4b-2's modules were added to it they
    could not import a CompiledSpec; if resolve.py were removed from it, the mechanism
    could start judging."""
    source = (Path(__file__)).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.name != "test_resolution_modules_judge_no_specification":
            continue
        marks = [
            decorator
            for decorator in node.decorator_list
            if isinstance(decorator, ast.Call)
        ]
        names = ast.literal_eval(marks[0].args[1])
        assert names == ["resolve", "lookup"]
        return
    raise AssertionError("the resolution guard is missing")


@pytest.mark.parametrize("module_name", APPROVAL_MODULES)
def test_approval_modules_may_judge_a_specification(module_name):
    """The complement of the resolution guard: these modules exist to see a spec."""
    source = (SOURCE_ROOT / "fs" / f"{module_name}.py").read_text(encoding="utf-8")
    assert ast.parse(source) is not None


@pytest.mark.parametrize("module_name", PURE_MODULES)
def test_the_pure_modules_issue_no_syscall(module_name):
    source = (SOURCE_ROOT / "fs" / f"{module_name}.py").read_text(encoding="utf-8")
    imported = _resolved_imports(ast.parse(source), package="atoms.fs")
    assert not any(name.split(".")[0] in {"os", "fcntl", "ctypes"} for name in imported)


@pytest.mark.parametrize("module_name", PURE_MODULES)
def test_the_pure_modules_never_compare_a_raw_component(module_name):
    """What the injected_equivalence double cannot catch: under an identity key a direct
    comparison behaves exactly like the function it bypasses, so every EXACT_BYTES case
    still passes. Only `leaf` and `declared_component` are guarded — the names the
    modules bind a raw path component to."""
    source = (SOURCE_ROOT / "fs" / f"{module_name}.py").read_text(encoding="utf-8")
    raw = {"leaf", "declared_component"}
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Compare):
            operands = [node.left, *node.comparators]
            for operand in operands:
                if isinstance(operand, ast.Attribute) and operand.attr in raw:
                    raise AssertionError(
                        f"{module_name}.py compares {operand.attr} directly; route it "
                        "through lookup_equivalence_key"
                    )


def test_approval_catches_nothing_a_resolver_raises():
    """Ledger #20 is categorical. The correct number of handlers enclosing a resolver
    call is zero — not "no blanket handler", which is the weaker rule resolve.py has."""
    source = (SOURCE_ROOT / "fs" / "approval.py").read_text(encoding="utf-8")
    calls = {"resolve", "work_base_facts", "PathResolver"}
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Try):
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Call):
                func = inner.func
                name = (
                    func.attr
                    if isinstance(func, ast.Attribute)
                    else getattr(func, "id", "")
                )
                assert name not in calls, (
                    f"a try block in approval.py encloses {name}(); ledger #20 requires "
                    "every A4b-1 refusal to reach the caller unhandled"
                )


def test_the_approved_spec_is_not_exported():
    import atoms.fs as package

    assert "ProjectApprovedSpec" not in package.__all__
    assert not hasattr(package, "ProjectApprovedSpec")


def test_no_consumer_of_the_approved_spec_exists_yet():
    """Arms the A5-A8 boundary before there is anything to guard, as A4a armed
    test_no_production_caller_of_bind_exists_yet. When A5 lands, this test is replaced by
    one asserting A5-A8 accept only this proof."""
    consumers = []
    for path in sorted((SOURCE_ROOT).rglob("*.py")):
        if path.name in {"approval.py"}:
            continue
        if "ProjectApprovedSpec" in path.read_text(encoding="utf-8"):
            consumers.append(str(path.relative_to(SOURCE_ROOT)))
    assert consumers == []


def test_a4b_status_is_synchronized_across_authority_documents():
    root = Path(__file__).parents[2]
    agents = (root / "AGENTS.md").read_text(encoding="utf-8")
    assert "**A4b — rooted project approval: implemented on 2026-07-31.**" in agents
    assert "It admits #21, the txid binding, owned by A5." in agents
```

- [ ] **Step 2: Run them to verify the status guard fails**

Run: `uv run pytest tests/test_fs_architecture.py -k a4b_status -v`
Expected: FAIL — `AGENTS.md` still says "A4b-2 designed and unimplemented".

- [ ] **Step 3: Update the status note**

In `AGENTS.md`, replace the A4b bullet's first line:

```markdown
- **A4b — rooted project approval: implemented on 2026-07-31.** A4b-1 owns
```

and replace `A4b-2 owns the judgment:` through the end of that bullet with:

```markdown
  A4b-2 owns the judgment in `atoms/fs/approval.py`, `atoms/fs/judgment.py`, and
  `atoms/fs/topology.py`: `approve_for_project`, `ProjectApprovedSpec`, and ledger entries #2,
  #3 (its part), #4, #5, #6, #9, #10, #11, #16, and #20, all discharged. It admits #21, the txid
  binding, owned by A5. A4b-1 approves only non-casefold ext4; XFS, Btrfs, and casefold
  directories fail closed.
```

- [ ] **Step 4: Remove the discharged ledger entries**

In `docs/deferred-obligation-ledger.md`, delete rows #2, #3, #4, #5, #6, #9, #10, #11, #16, and #20
from the open table and append them to a new "Discharged obligations" table naming A4b-2 and the date.
Row #3 is deleted only if A6's part is also complete; it is not, so **row #3 stays** with its owner list
narrowed from `A3, A4b, A6` to `A6` and its required behavior trimmed to A6's clause.

- [ ] **Step 5: Run every gate**

```bash
uv run pytest
uv run ruff check
uv run pyright
```
Expected: all pass, with the casefold tier skipped as before.

- [ ] **Step 6: Commit**

```bash
git add tests/test_fs_architecture.py AGENTS.md ../docs/deferred-obligation-ledger.md
git commit -m "test(fs): guard the a4b-2 seam and discharge its ledger entries"
```

---

## Self-review

**Spec coverage.** Every design section maps to a task: §5.1 `ProjectContext` and §6.1 phase A → Task 6;
§6.2 phase B → Task 6; §6.3.1 → Task 2; §6.3.2 → Tasks 1 and 5; §6.3.3 → Task 5; §7.1–§7.3 → Task 3;
§7.4 → Task 4; §7.5 → Tasks 6 and 7; §8 → Tasks 3 and 6; §9 → Tasks 2, 5, 6; §11.1 → Tasks 2 and 5;
§11.2 → Task 3; §11.3 → Task 4; §11.4 → Task 7; §11.5 → Task 6; §11.6 → Task 8.

**A finding that changed the test doubles.** A case-folding double cannot exercise endpoint
distinctness *at all*. Two leaves fold in one parent only when their whole paths fold too, and A2 phase
4's `portability_equivalence_key` is applied to the whole path — so it already refuses every pair the
check would catch, before approval runs. The same holds for scratch leaves, whose effect IDs A2 phase 6
makes portability-key unique. The injected double is therefore a **factory**: case folding for the
topology-merge case, where A2 admits `A` alongside `a/x` because those whole paths differ, and an
eight-byte truncation — a real filesystem equivalence class A2's key does not subsume — for the
endpoint and scratch collision cases.

This is worth stating plainly rather than burying: under the current floor *plus* A2's conservative
whole-path filter, endpoint distinctness has no reachable failure mode. Ledger #2 and authority §5.4
still require it, and it is still the correct check; it simply cannot fire today, exactly as design
§7.4 says of the resolved-versus-lexical re-run. A reviewer who expects a real-volume endpoint-collision
case should know none exists to write.

**Two places a reviewer should look hardest.**

1. **Task 4's `require_resolved_surface_and_ordering` walks ancestors through `parent_by_node`, which
   contains only nodes that have edges.** A path parented by `ProjectRoot` terminates correctly because
   `ProjectRoot` has no parent edge, so `.get` returns `None`. Verify that on a single-component path
   before trusting the loop.
2. **Task 3's `_nodes_by_key` raises `ProtocolError` for two declared paths sharing a *directory* key,
   while `require_endpoints_distinct` raises `ProjectApprovalRefused` for two sharing a *leaf* key.**
   Different collisions at different levels, and the second runs after the first. A reviewer may
   reasonably argue both should be `ProjectApprovalRefused`; the plan chose `ProtocolError` because
   `build_topology` documents that its caller has already run the legality pass, so reaching it means
   the pipeline ran out of order.

**Placeholder scan.** Clean — no TBD, no "similar to Task N", no step that describes without showing.

**Type consistency.** `ResolvedTopology.parent_of` takes a path string and returns a `TopologyNode`;
`parent_node_of` takes a node and returns its parent node. Both are used with those meanings in Tasks
3, 4, and 5. `ApprovedScratch.leaf` and `ApprovedPath.leaf` are both plain `str`. `work_constraints` is
`DirectoryConstraints | None` in `build_topology` and `ApprovedWorkBase.constraints` is non-optional —
these are different values: the first is `work/<txid>/`'s derived constraints, the second is physical
`work/`'s observed ones.

---

## Execution Handoff

Plan complete and saved to `docs/plans/2026-07-31-plan-a4b2-project-approval.md`. Two execution
options:

**1. Subagent-Driven (recommended)** — a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — execute tasks in this session using `superpowers:executing-plans`, batch
execution with checkpoints for review.
