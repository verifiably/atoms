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
| `src/atoms/fs/topology.py` | **Create.** The `Approved*` value types, `ResolvedTopology`, `build_topology`, `require_resolved_surface_and_ordering`. Pure. Built before any judgment, and every judgment keys on its nodes. |
| `src/atoms/fs/judgment.py` | **Create.** `require_ancestors_legal`, `require_endpoints_distinct`, `bind_scratch`. Pure. |
| `src/atoms/fs/approval.py` | **Create.** `ProjectContext`, `ProjectApprovedSpec`, the construction token, `approve_for_project`. |
| `tests/fs_support.py` | **Modify.** Synthetic `ResolvedPrefix` and `CompiledSpec` builders shared by every pure tier. |
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

**Topology construction is Task 2 and ancestor legality is Task 3, not the reverse.** Judgment is
keyed on topology nodes, so `judgment.py` imports `topology.py` and not the other way round; the
dependency runs `approval -> judgment -> topology` throughout.

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

## Task 2: Topology construction

**Files:**
- Create: `src/atoms/fs/topology.py`
- Modify: `tests/fs_support.py`
- Modify: `tests/conftest.py`
- Create: `tests/test_fs_topology.py`

**Why construction precedes every judgment.** Design §5.4's motivating case is
`CreateDirectory("A")` with an effect on `a/x`: on a folding parent those are one directory, and
the creation is what supplies the ancestor. A judgment keyed on path *spelling* cannot see that —
it looks for a creator of the string `"a"`, finds none, and refuses a legal specification. So the
node keying runs first and every later phase is keyed on nodes. `build_topology` therefore decides
only *which directories exist and which of them are the same directory*; it decides nothing about
legality.

That also fixes what counts as a directory candidate. A candidate is every proper prefix of a
declared path **plus every `CreateDirectory` endpoint** — without the second half `"A"` is never a
directory at all and cannot merge with `"a"`.

**Interfaces:**
- Consumes: `CompiledSpec`, `ResolvedPrefix`, `DirectoryConstraints`, `inherited_constraints`,
  `lookup_equivalence_key`, A3's `ProjectRoot`, `WorkRoot`, `TopologyDirectory`, `PersistentNode`,
  `ScratchNode`, `TopologyParent`, `TopologyNode`, `RecoveryTopology`, `ScratchRole`, and
  `required_scratch_role`.
- Produces: `ApprovedExistingDirectory`, `ApprovedPlannedDirectory`, `ApprovedDirectory`,
  `ApprovedPath`, `ApprovedScratch`, `ApprovedWorkBase`, `ResolvedTopology`,
  `build_topology(compiled, prefixes, filesystem_type, work_constraints) -> ResolvedTopology`;
  the `resolved_prefix`, `directory_facts`, `compiled_for`, and `file_state` helpers plus the
  `EXT4` and `WORK_CONSTRAINTS` constants in `tests/fs_support.py`.

**`required_scratch_role` is imported from `atoms.core.recovery.snapshot`, not from
`atoms.core.recovery`.** It is not in the package's `__all__`, and it may not be added:
`test_public_surface_has_exactly_five_operations` in `tests/test_recovery_architecture.py` asserts
that the *functions* exported by `atoms.core.recovery` are exactly the five A3 operations. A3's own
`variants.py` and `reducer.py` import it from the submodule for the same reason.

- [ ] **Step 1: Add the synthetic builders**

Append to `tests/fs_support.py`. Every pure tier builds `ResolvedPrefix` values and compiled specs
by hand, so the builders belong beside the other test support rather than in fixtures — they take
arguments and return values, which a fixture cannot do without an extra factory layer. `EXT4` is
already defined at `fs_support.py:25` and is used as-is — do not redefine it:

```python
WORK_CONSTRAINTS = DirectoryConstraints(
    lookup_proof=LookupProof.EXACT_BYTES, name_max=255
)
EMPTY_DIGEST = "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def file_state(mode: int = 0o644) -> FileState:
    return FileState(content_hash=EMPTY_DIGEST, mode=mode, byte_len=0)


def nonempty_state(content: bytes, mode: int = 0o644) -> FileState:
    """A2 refuses a FileState whose byte_len is non-zero under the empty-content hash and
    vice versa, so ReplaceFile's two distinct states need real digests."""
    digest = hashlib.sha256(content).hexdigest()
    return FileState(
        content_hash=f"sha256:{digest}", mode=mode, byte_len=len(content)
    )


def compiled_for(*effects: Effect) -> CompiledSpec:
    """Compile a spec whose surfaces are derived from the effects, so every test states
    only what it is about."""
    return compile_spec(_spec_for(effects))


def _spec_for(effects: tuple[Effect, ...]) -> TransactionSpec:
    initial: dict[str, PathState] = {}
    final: dict[str, PathState] = {}
    for effect in effects:
        for occurrence in occurrences(effect):
            initial.setdefault(occurrence.path, occurrence.pre)
            final[occurrence.path] = occurrence.post
    return build_spec(
        consumer_tag="test",
        intent_digest=EMPTY_DIGEST,
        initial_surface=initial,
        final_surface=final,
        effects=effects,
    )


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
    """Stable per prefix string, and never the root's inode.

    `crc32` rather than `hash`, whose string salt is randomized per process — a test that
    passes only within one interpreter run is not a test.
    """
    return 1000 + zlib.crc32(prefix.encode("utf-8"))


def prefixes_for(compiled: CompiledSpec) -> dict[str, ResolvedPrefix]:
    """The resolution table a walk produces when every ancestor exists except the ones
    this transaction creates.

    The created check compares whole prefix strings, not `startswith`: `d/newer` starts
    with `d/new` and is not beneath it.
    """
    created = {
        effect.path
        for effect in compiled.spec.effects
        if isinstance(effect, CreateDirectory)
    }
    table: dict[str, ResolvedPrefix] = {}
    for timeline in compiled.timelines:
        components = timeline.path.split("/")
        depth = len(components) - 1
        for index in range(len(components) - 1):
            if "/".join(components[: index + 1]) in created:
                depth = index
                break
        table[timeline.path] = resolved_prefix(timeline.path, existing_depth=depth)
    return table


GENERATOR_PATHS = ("a", "a/b", "d/one", "d/two", "p")
GENERATOR_MOVES = (("d/one", "d/two"), ("a", "p"), ("d/one", "a/b"))
GENERATED_SPECIFICATION_COUNT = 4841
"""How many of the 12719 candidate sequences A2 admits, measured on this checkout.

Asserted exactly by the §7.4 property test, so a generator that silently narrows fails
rather than passing on a smaller matrix. Change it only alongside a pool change."""


def _generated_effect(tag: str, argument, index: int) -> Effect:
    effect_id = f"e{index}"
    if tag == "cf":
        return CreateFileNoClobber(effect_id, argument, file_state())
    if tag == "mk":
        return CreateDirectory(effect_id, argument, DirectoryState(mode=0o755))
    if tag == "rm":
        return DeletePath(effect_id, argument, file_state())
    if tag == "rp":
        return ReplaceFile(
            effect_id, argument, nonempty_state(b"old"), nonempty_state(b"new")
        )
    return MoveNoClobber(effect_id, argument[0], argument[1], file_state())


def generated_specifications():
    """Yield `(label, effects)` for every effect sequence A2 admits, over a fixed pool.

    All ordered sequences of length 1-3 over 23 candidate effects: each of the four
    single-path variants against each of five paths, plus three moves. Sequences A2
    refuses are skipped rather than reported -- an input that does not compile is not an
    input to this layer.

    `product`, not `permutations`: a candidate may repeat. `permutations` draws without
    replacement and so silently omits every sequence that touches one path twice with the
    same variant -- including create -> delete -> create, which A2 admits and which is
    exactly the ancestor-type churn ledger entry #3 is about. That is 16 sequences.
    """
    pool = [
        (tag, path)
        for path in GENERATOR_PATHS
        for tag in ("cf", "mk", "rm", "rp")
    ]
    pool += [("mv", pair) for pair in GENERATOR_MOVES]
    for size in (1, 2, 3):
        for combination in itertools.product(pool, repeat=size):
            effects = tuple(
                _generated_effect(tag, argument, index)
                for index, (tag, argument) in enumerate(combination)
            )
            try:
                compile_spec(_spec_for(effects))
            except SpecValidationError:
                continue
            yield "|".join(f"{tag}:{argument}" for tag, argument in combination), effects


def work_for(compiled: CompiledSpec) -> DirectoryConstraints | None:
    """The work-root constraints approval derives, present exactly when a CreateDirectory
    is, which is what A3's WorkRoot rule requires."""
    return (
        WORK_CONSTRAINTS
        if any(
            isinstance(effect, CreateDirectory) for effect in compiled.spec.effects
        )
        else None
    )
```

Add to `tests/fs_support.py`'s imports:

```python
import hashlib
import itertools
import zlib

from atoms.core.compiler import CompiledSpec, compile_spec
from atoms.core.effects import (
    CreateDirectory,
    CreateFileNoClobber,
    DeletePath,
    Effect,
    MoveNoClobber,
    ReplaceFile,
    occurrences,
)
from atoms.core.errors import SpecValidationError
from atoms.core.fingerprint import DirectoryState, FileState, PathState
from atoms.core.spec import TransactionSpec, build_spec
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

If `atoms.core.effects` exports no `Effect` union alias, annotate `*effects` as `object` and add
`# type: ignore[arg-type]` on the `effects=effects` argument instead; do not invent the alias.

- [ ] **Step 2: Register the equivalence double in conftest**

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

    `install` returns the list of names the double was asked about, in call order. That
    is the positive half of the bypass check: a call site that decides a name without the
    helper contributes nothing to the list, whatever shape the bypass takes. Under an
    identity key the *behaviour* is unchanged, so only the recording distinguishes the
    two. The AST guard in test_fs_architecture.py is the negative half and catches the
    specific shapes it names; neither alone is a proof, and the pair is what the design
    asks for.

    This is a double either way. It proves the call sites route through the function and
    merge whatever it merges; it proves nothing about any real relation, all of which
    stay unreproducible and refused.
    """

    def install(key):
        calls: list[str] = []

        def recording(constraints, name):
            calls.append(name)
            return key(name)

        for module in _EQUIVALENCE_CONSUMERS:
            monkeypatch.setattr(f"{module}.lookup_equivalence_key", recording)
        return calls

    return install


_EQUIVALENCE_CONSUMERS = ("atoms.fs.topology",)


def truncate_to_eight(name: str) -> str:
    """A truncating name equivalence, in conftest so the registry guard sees it."""
    return name[:8]
```

`_EQUIVALENCE_CONSUMERS` holds one module here because `atoms.fs.judgment` does not exist yet and
`monkeypatch.setattr` with a string target imports what it names. Task 5 adds
`"atoms.fs.judgment"` to it, at the step that gives that module its first call to
`lookup_equivalence_key`. Task 3's `require_ancestors_legal` never calls it — it is keyed on
topology nodes, so the equivalence relation has already been applied by the time it runs.

- [ ] **Step 3: Write the failing tests**

Create `tests/test_fs_topology.py`:

```python
"""Tier 2 — topology construction (design §11.2)."""

from __future__ import annotations

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
from atoms.fs.topology import (
    ApprovedExistingDirectory,
    ApprovedPlannedDirectory,
    build_topology,
)
from tests.fs_support import (
    EXT4,
    WORK_CONSTRAINTS,
    compiled_for,
    file_state,
    resolved_prefix,
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


def test_a_create_directory_endpoint_is_a_directory_candidate():
    """The half of the candidate set that makes design §5.4's case representable. `A` is
    nobody's lexical prefix, so only its being a CreateDirectory endpoint puts it in the
    key space where `a` can merge into it."""
    compiled = compiled_for(CreateDirectory("mk", "A", DirectoryState(mode=0o755)))
    prefixes = {"A": resolved_prefix("A", existing_depth=0)}
    resolved = build_topology(compiled, prefixes, EXT4, WORK_CONSTRAINTS)
    assert resolved.directory_node("A") == PersistentNode("A")


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
    parents = {edge.parent for edge in resolved.topology.parents}

    # Set equality, not a type predicate. A predicate passes when a node is missing and
    # when an extra one is retained; the first draft retained a lone CreateDirectory
    # endpoint that parents nothing, and a type check said nothing about it.
    assert existing == {
        node for node in parents if isinstance(node, (ProjectRoot, TopologyDirectory))
    }
    assert planned == {
        node for node in parents if isinstance(node, (WorkRoot, PersistentNode))
    }
    assert not existing & planned


def test_a_directory_that_parents_nothing_is_not_retained():
    """§7.3's partition names the *parent* PersistentNodes. A lone CreateDirectory has
    nothing declared beneath it, so its constraints bound no name and retaining them would
    put a fact in the proof that no later stage can act on."""
    compiled = compiled_for(CreateDirectory("mk", "a", DirectoryState(mode=0o755)))
    resolved = build_topology(
        compiled, {"a": resolved_prefix("a", existing_depth=0)}, EXT4, WORK_CONSTRAINTS
    )
    assert {entry.node for entry in resolved.directories} == {
        ProjectRoot(),
        WorkRoot(),
    }


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


def test_two_paths_through_one_physical_directory_share_its_node():
    """Identity keying, which needs no double: two prefixes that resolved to the same
    inode are the same directory whatever they are spelled."""
    compiled = compiled_for(
        CreateFileNoClobber("e1", "d/one", file_state()),
        CreateFileNoClobber("e2", "d/two", file_state()),
    )
    prefixes = {
        "d/one": resolved_prefix("d/one", existing_depth=1),
        "d/two": resolved_prefix("d/two", existing_depth=1),
    }
    resolved = build_topology(compiled, prefixes, EXT4, None)
    assert resolved.parent_of("d/one") == resolved.parent_of("d/two")


def test_every_planned_component_is_asked_about(injected_equivalence):
    """The positive half of the bypass check for construction: each planned prefix must
    be keyed through the helper, so `a` and `b` both appear. The root and any existing
    prefix key by identity and correctly do not."""
    calls = injected_equivalence(lambda name: name)
    compiled = compiled_for(CreateFileNoClobber("e1", "a/b/leaf", file_state()))
    prefixes = {"a/b/leaf": resolved_prefix("a/b/leaf", existing_depth=0)}
    build_topology(compiled, prefixes, EXT4, None)
    assert calls == ["a", "b"]


def test_planned_directories_merge_under_a_folding_key(injected_equivalence):
    """Design §5.4's case. CreateDirectory("A") with an effect on a/x is one directory
    under a folding parent. No LookupProof member is both insensitive and reproducible,
    so the double is the only route to the behaviour (design §6.3.2).

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
    assert resolved.directory_node("a") == PersistentNode("A")


def test_the_same_pair_stays_two_directories_under_exact_bytes():
    """The counterpart, without the double: `A` and `a` are two directories, so `a/x`
    hangs off an undeclared intermediate instead. The pair proves the merge is the
    equivalence function's doing rather than an accident of construction — and Task 3
    refuses this one, because nothing creates that intermediate."""
    compiled = compiled_for(
        CreateDirectory("mk", "A", DirectoryState(mode=0o755)),
        CreateFileNoClobber("e1", "a/x", file_state()),
    )
    prefixes = {
        "A": resolved_prefix("A", existing_depth=0),
        "a/x": resolved_prefix("a/x", existing_depth=0),
    }
    resolved = build_topology(compiled, prefixes, EXT4, WORK_CONSTRAINTS)
    assert isinstance(resolved.parent_of("a/x"), TopologyDirectory)
    assert resolved.directory_node("A") == PersistentNode("A")
```

**There is deliberately no test for two declared directories that name one entry.** That is the
directory half of ledger #2, and it is unreachable through `compile_spec`: A2 phase 4 applies
`portability_equivalence_key` to the whole path, so `CreateDirectory("A")` alongside
`CreateDirectory("a")` is refused at compilation with `declared paths 'A' and 'a' alias one another
under Unicode caseless matching`. The refusal branch is still written in Step 5 as a fail-closed
guard against a wider floor; a test whose input cannot compile is not a test.

- [ ] **Step 4: Run the tests to verify they fail**

Run: `uv run pytest tests/test_fs_topology.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'atoms.fs.topology'`.

- [ ] **Step 5: Write `topology.py`**

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
from atoms.core.errors import ProjectApprovalRefused, ProtocolError
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
)
from atoms.core.recovery.snapshot import required_scratch_role
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
    directory_nodes: tuple[tuple[str, TopologyNode], ...]

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

    def directory_node(self, prefix: str) -> TopologyNode | None:
        """The node of the directory at ``prefix``, or None if none sits there.

        Two prefixes that fold together under their parent's policy return the same node.
        This is the lookup every judgment uses in place of a path-string comparison.
        """
        for candidate, node in self.directory_nodes:
            if candidate == prefix:
                return node
        return None


def build_topology(
    compiled: CompiledSpec,
    prefixes: Mapping[str, ResolvedPrefix],
    filesystem_type: str,
    work_constraints: DirectoryConstraints | None,
) -> ResolvedTopology:
    """Build A3's production topology plus the node-keyed fact table.

    Runs before every judgment and judges nothing itself. It decides only which
    directories exist and which of them are the same directory; ancestor legality,
    endpoint distinctness, and the surface re-run all need that answer first.
    """
    declared = {timeline.path for timeline in compiled.timelines}
    created = {
        effect.path
        for effect in compiled.spec.effects
        if isinstance(effect, CreateDirectory)
    }
    facts = _facts_by_prefix(prefixes, created, filesystem_type)
    keys = _keys_by_prefix(facts)
    nodes = _nodes_by_key(keys, declared)

    # Keyed by node, not appended: two prefixes that fold together are one directory and
    # take one edge. A3 fails a node appearing twice in topology.parents even when both
    # edges name the same parent.
    edges: dict[TopologyNode, TopologyParent] = {}
    for prefix in sorted(facts, key=_depth_then_name):
        if prefix == ROOT_PREFIX:
            continue
        parent = "/".join(prefix.split("/")[:-1])
        node = nodes[keys[prefix]]
        edges.setdefault(node, TopologyParent(node=node, parent=nodes[keys[parent]]))

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
        edges.setdefault(node, TopologyParent(node=node, parent=entry.parent_node))

    ordered = list(edges.values())
    if work_constraints is not None:
        ordered.append(TopologyParent(node=WorkRoot(), parent=ProjectRoot()))
    ordered.extend(_scratch_edges(compiled, paths))

    # Facts are retained only for nodes that actually parent something. A candidate that
    # parents nothing -- a lone CreateDirectory endpoint, say -- is a directory this
    # transaction creates and nothing is declared beneath, so its constraints bound no
    # name. Retaining it would break §7.3's partition and criterion 14, which say
    # ApprovedPlannedDirectory is exactly WorkRoot plus the *parent* PersistentNodes.
    parents = {edge.parent for edge in ordered}
    directories = _directory_entries(facts, keys, nodes, parents)
    if work_constraints is not None:
        directories = (
            *directories,
            ApprovedPlannedDirectory(node=WorkRoot(), constraints=work_constraints),
        )
    return ResolvedTopology(
        topology=RecoveryTopology(parents=tuple(ordered)),
        directories=directories,
        paths=paths,
        directory_nodes=tuple(
            (prefix, nodes[keys[prefix]])
            for prefix in sorted(facts, key=_depth_then_name)
        ),
    )


def _depth_then_name(prefix: str) -> tuple[int, str]:
    """Parents before children, so a key is always available when a child needs it."""
    return (0, "") if prefix == ROOT_PREFIX else (len(prefix.split("/")), prefix)


def _facts_by_prefix(
    prefixes: Mapping[str, ResolvedPrefix],
    created: frozenset[str] | set[str],
    filesystem_type: str,
) -> dict[str, tuple[FilesystemIdentity | None, DirectoryConstraints]]:
    """Attribute observed or derived facts to every directory candidate.

    A candidate is every proper prefix of a declared path plus every CreateDirectory
    endpoint. The second half is what makes design §5.4's case representable: `A` is
    nobody's lexical prefix, so without it there is no directory for `a` to merge into.

    A candidate some path resolved through is existing; one no path resolved through is
    created by this transaction and inherits its parent's constraints.
    """
    facts: dict[str, tuple[FilesystemIdentity | None, DirectoryConstraints]] = {}
    for path in sorted(prefixes):
        prefix = prefixes[path]
        facts[ROOT_PREFIX] = (prefix.root.identity, prefix.root.constraints)
        components = path.split("/")
        for index, hop in enumerate(prefix.hops):
            facts["/".join(components[: index + 1])] = (
                hop.facts.identity,
                hop.facts.constraints,
            )

    candidates: set[str] = set()
    for path in prefixes:
        components = path.split("/")
        candidates.update(
            "/".join(components[: index + 1]) for index in range(len(components) - 1)
        )
    for path in created:
        components = path.split("/")
        candidates.update(
            "/".join(components[: index + 1]) for index in range(len(components))
        )

    for candidate in sorted(candidates, key=_depth_then_name):
        if candidate in facts:
            continue
        parent = "/".join(candidate.split("/")[:-1])
        _, parent_constraints = facts[parent]
        facts[candidate] = (
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
    """Assign one node per key. A declared prefix always wins over an undeclared one, so
    the pass is order-independent: whichever arrives second overwrites or defers."""
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
                raise ProjectApprovalRefused(
                    f"declared directories {existing.path!r} and {prefix!r} name one "
                    "entry under the actual lookup policy of their shared parent; "
                    "construction cannot choose which of them the directory is"
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
    parents: frozenset[TopologyNode] | set[TopologyNode],
) -> tuple[ApprovedDirectory, ...]:
    entries: dict[TopologyNode, ApprovedDirectory] = {}
    for prefix in sorted(facts, key=_depth_then_name):
        identity, constraints = facts[prefix]
        node = nodes[keys[prefix]]
        if node not in parents:
            continue
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
        anchor = effect.source if isinstance(effect, MoveNoClobber) else effect.path
        edges.append(TopologyParent(node=node, parent=parent_by_path[anchor]))
    return edges
```

The `ProjectApprovalRefused` branch in `_nodes_by_key` has no reachable test: A2 phase 4 applies
`portability_equivalence_key` to the whole path, so `CreateDirectory("A")` alongside
`CreateDirectory("a")` is refused at compilation with `declared paths 'A' and 'a' alias one
another under Unicode caseless matching`. Keep the branch — it fails closed if the floor or A2's
filter ever widens — and do not write a test that cannot compile its own input.

- [ ] **Step 6: Run the tests and the gates**

```bash
uv run pytest tests/test_fs_topology.py -v
uv run ruff check src/atoms/fs/topology.py tests/
uv run pyright
```
Expected: PASS, `All checks passed!`, `0 errors`.

- [ ] **Step 7: Commit**

```bash
git add src/atoms/fs/topology.py tests/test_fs_topology.py tests/fs_support.py tests/conftest.py
git commit -m "feat(fs): build the resolved topology over directory identity"
```

---

## Task 3: Ancestor legality

**Files:**
- Create: `src/atoms/fs/judgment.py`
- Create: `tests/test_fs_judgment.py`

**Interfaces:**
- Consumes: `CompiledSpec`, `ResolvedPrefix`, `PresentFrontier`, `EntryKind`, `ResolvedTopology`,
  `TopologyNode`, `CreateDirectory`, `DeletePath`, `occurrences`, `ProjectApprovalRefused`,
  `ProtocolError`.
- Produces: `require_ancestors_legal(compiled: CompiledSpec, prefixes: Mapping[str, ResolvedPrefix],
  resolved: ResolvedTopology) -> None`.

**Judged over nodes, never over spellings.** Every creator, remover, and ancestor is looked up as a
`TopologyNode` through `resolved.directory_node`. Under a folding parent `CreateDirectory("A")` is
what creates the ancestor of `a/x`; a check keyed on the string `"a"` refuses a legal
specification, which is exactly the defect this ordering exists to prevent.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_fs_judgment.py`:

```python
"""Tier 1 — pure judgment over hand-built resolution tables (design §11.1)."""

from __future__ import annotations

import pytest

from atoms.core.effects import (
    CreateDirectory,
    CreateFileNoClobber,
    DeletePath,
    MoveNoClobber,
)
from atoms.core.errors import ProjectApprovalRefused, ProtocolError
from atoms.core.fingerprint import DirectoryState, SymlinkState
from atoms.fs.judgment import require_ancestors_legal
from atoms.fs.resolve import EntryKind, FilesystemIdentity, PresentFrontier
from atoms.fs.topology import build_topology
from tests.fs_support import (
    EXT4,
    WORK_CONSTRAINTS,
    compiled_for,
    file_state,
    resolved_prefix,
)

BLOCKING_FILE = PresentFrontier(
    identity=FilesystemIdentity(device=41, inode=77), kind=EntryKind.REGULAR_FILE
)
BLOCKING_SYMLINK = PresentFrontier(
    identity=FilesystemIdentity(device=41, inode=88), kind=EntryKind.SYMLINK
)


def test_a_fully_resolved_path_needs_no_ancestor_proof():
    compiled = compiled_for(CreateFileNoClobber("e1", "a/b/leaf", file_state()))
    prefixes = {"a/b/leaf": resolved_prefix("a/b/leaf", existing_depth=2)}
    resolved = build_topology(compiled, prefixes, EXT4, None)
    require_ancestors_legal(compiled, prefixes, resolved)


def test_a_missing_ancestor_no_effect_creates_is_refused():
    compiled = compiled_for(CreateFileNoClobber("e1", "a/b/leaf", file_state()))
    prefixes = {"a/b/leaf": resolved_prefix("a/b/leaf", existing_depth=1)}
    resolved = build_topology(compiled, prefixes, EXT4, None)
    with pytest.raises(ProjectApprovalRefused) as caught:
        require_ancestors_legal(compiled, prefixes, resolved)
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
    resolved = build_topology(compiled, prefixes, EXT4, WORK_CONSTRAINTS)
    require_ancestors_legal(compiled, prefixes, resolved)


def test_a_folding_ancestor_created_first_is_admitted(injected_equivalence):
    """Design §5.4's case, end to end through the two phases that decide it. A2 admits
    the pair — its phase 4 key is the whole path, and `A` differs from `a/x` — so the
    resolved judgment is the only thing that can accept or refuse it."""
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
    require_ancestors_legal(compiled, prefixes, resolved)


def test_the_same_pair_is_refused_under_exact_bytes():
    """Without the folding key `a` is a second directory that nothing creates. The pair
    proves the acceptance above is the equivalence function's doing."""
    compiled = compiled_for(
        CreateDirectory("mk", "A", DirectoryState(mode=0o755)),
        CreateFileNoClobber("e1", "a/x", file_state()),
    )
    prefixes = {
        "A": resolved_prefix("A", existing_depth=0),
        "a/x": resolved_prefix("a/x", existing_depth=0),
    }
    resolved = build_topology(compiled, prefixes, EXT4, WORK_CONSTRAINTS)
    with pytest.raises(ProjectApprovalRefused) as caught:
        require_ancestors_legal(compiled, prefixes, resolved)
    assert "no CreateDirectory effect creates" in str(caught.value)


def test_a_created_ancestor_ordered_after_its_descendant_is_refused(
    injected_equivalence,
):
    """A2 phase 13 is lexical: it sees CreateDirectory("A") and an effect on "a/x" as
    unrelated paths and admits this. Under a folding parent they are one directory and
    the creation is too late. This is the only shape that reaches the ordering branch —
    under exact bytes A2's verdict and the resolved verdict provably agree (design §7.4),
    so a test that compiles cannot disagree with A2 unless the key relation differs."""
    injected_equivalence(str.casefold)
    compiled = compiled_for(
        CreateFileNoClobber("e1", "a/x", file_state()),
        CreateDirectory("mk", "A", DirectoryState(mode=0o755)),
    )
    prefixes = {
        "a/x": resolved_prefix("a/x", existing_depth=0),
        "A": resolved_prefix("A", existing_depth=0),
    }
    resolved = build_topology(compiled, prefixes, EXT4, WORK_CONSTRAINTS)
    with pytest.raises(ProjectApprovalRefused) as caught:
        require_ancestors_legal(compiled, prefixes, resolved)
    assert "must precede" in str(caught.value)


def test_a_regular_file_ancestor_the_timeline_converts_is_admitted():
    compiled = compiled_for(
        DeletePath("rm", "p", file_state()),
        CreateDirectory("mk", "p", DirectoryState(mode=0o755)),
        CreateFileNoClobber("e1", "p/q", file_state()),
    )
    prefixes = {
        "p": resolved_prefix("p", existing_depth=0, frontier=BLOCKING_FILE),
        "p/q": resolved_prefix("p/q", existing_depth=0, frontier=BLOCKING_FILE),
    }
    resolved = build_topology(compiled, prefixes, EXT4, WORK_CONSTRAINTS)
    require_ancestors_legal(compiled, prefixes, resolved)


def test_a_regular_file_ancestor_the_timeline_leaves_alone_is_refused():
    compiled = compiled_for(
        CreateDirectory("mk", "p", DirectoryState(mode=0o755)),
        CreateFileNoClobber("e1", "p/q", file_state()),
    )
    prefixes = {
        "p": resolved_prefix("p", existing_depth=0, frontier=BLOCKING_FILE),
        "p/q": resolved_prefix("p/q", existing_depth=0, frontier=BLOCKING_FILE),
    }
    resolved = build_topology(compiled, prefixes, EXT4, WORK_CONSTRAINTS)
    with pytest.raises(ProjectApprovalRefused) as caught:
        require_ancestors_legal(compiled, prefixes, resolved)
    assert "removes" in str(caught.value)


def test_a_symlink_ancestor_the_timeline_converts_is_admitted():
    """Criterion 9 names SYMLINK beside REGULAR_FILE, and the two reach _REMOVABLE_KINDS
    by different routes -- DeletePath.pre is `FileState | SymlinkState`, so a symlink is
    removable only because of the second arm."""
    compiled = compiled_for(
        DeletePath("rm", "p", SymlinkState(target="x", mode=0o777)),
        CreateDirectory("mk", "p", DirectoryState(mode=0o755)),
        CreateFileNoClobber("e1", "p/q", file_state()),
    )
    prefixes = {
        "p": resolved_prefix("p", existing_depth=0, frontier=BLOCKING_SYMLINK),
        "p/q": resolved_prefix("p/q", existing_depth=0, frontier=BLOCKING_SYMLINK),
    }
    resolved = build_topology(compiled, prefixes, EXT4, WORK_CONSTRAINTS)
    require_ancestors_legal(compiled, prefixes, resolved)


def test_a_symlink_ancestor_the_timeline_leaves_alone_is_refused():
    compiled = compiled_for(
        CreateDirectory("mk", "p", DirectoryState(mode=0o755)),
        CreateFileNoClobber("e1", "p/q", file_state()),
    )
    prefixes = {
        "p": resolved_prefix("p", existing_depth=0, frontier=BLOCKING_SYMLINK),
        "p/q": resolved_prefix("p/q", existing_depth=0, frontier=BLOCKING_SYMLINK),
    }
    resolved = build_topology(compiled, prefixes, EXT4, WORK_CONSTRAINTS)
    with pytest.raises(ProjectApprovalRefused) as caught:
        require_ancestors_legal(compiled, prefixes, resolved)
    assert "removes" in str(caught.value)


def test_a_move_carries_no_path_attribute_and_is_still_judged():
    """MoveNoClobber has `source` and `destination` and no `path`. The creator scan must
    narrow the variant before reading one; the first draft did not, and every move raised
    AttributeError before any rule ran."""
    compiled = compiled_for(MoveNoClobber("mv", "src/a", "dst/b", file_state()))
    prefixes = {
        "src/a": resolved_prefix("src/a", existing_depth=1),
        "dst/b": resolved_prefix("dst/b", existing_depth=1),
    }
    resolved = build_topology(compiled, prefixes, EXT4, None)
    require_ancestors_legal(compiled, prefixes, resolved)


def test_a_move_beneath_a_created_directory_is_ordered():
    compiled = compiled_for(
        CreateDirectory("mk", "dst", DirectoryState(mode=0o755)),
        MoveNoClobber("mv", "src/a", "dst/b", file_state()),
    )
    prefixes = {
        "dst": resolved_prefix("dst", existing_depth=0),
        "dst/b": resolved_prefix("dst/b", existing_depth=0),
        "src/a": resolved_prefix("src/a", existing_depth=1),
    }
    resolved = build_topology(compiled, prefixes, EXT4, WORK_CONSTRAINTS)
    require_ancestors_legal(compiled, prefixes, resolved)


def test_an_other_ancestor_is_refused_whatever_the_timeline_says():
    """No closed effect variant accepts an OTHER precondition — DeletePath.pre is a file
    or a symlink — so no admissible timeline can turn a socket into a directory."""
    compiled = compiled_for(
        CreateDirectory("mk", "p", DirectoryState(mode=0o755)),
        CreateFileNoClobber("e1", "p/q", file_state()),
    )
    blocked = PresentFrontier(
        identity=FilesystemIdentity(device=41, inode=77), kind=EntryKind.OTHER
    )
    prefixes = {
        "p": resolved_prefix("p", existing_depth=0, frontier=blocked),
        "p/q": resolved_prefix("p/q", existing_depth=0, frontier=blocked),
    }
    resolved = build_topology(compiled, prefixes, EXT4, WORK_CONSTRAINTS)
    with pytest.raises(ProjectApprovalRefused) as caught:
        require_ancestors_legal(compiled, prefixes, resolved)
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
    prefixes = {"p/q": blocked}
    resolved = build_topology(compiled, prefixes, EXT4, None)
    with pytest.raises(ProtocolError):
        require_ancestors_legal(compiled, prefixes, resolved)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_fs_judgment.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'atoms.fs.judgment'`.

- [ ] **Step 3: Write `judgment.py`**

Create `src/atoms/fs/judgment.py`:

```python
"""Pure judgment over a resolution table and its topology (A4b-2 design §6.3).

No filesystem access, no descriptor, no syscall. Every function here takes values the
resolution and construction phases already produced and either returns or raises. Every
directory is identified by its TopologyNode; no function compares a path component.
"""

from __future__ import annotations

from collections.abc import Mapping

from atoms.core.compiler import CompiledSpec
from atoms.core.effects import CreateDirectory, DeletePath, occurrences
from atoms.core.errors import ProjectApprovalRefused, ProtocolError
from atoms.core.recovery import TopologyNode
from atoms.fs.resolve import EntryKind, PresentFrontier, ResolvedPrefix
from atoms.fs.topology import ResolvedTopology

# The frontier kinds a closed effect variant can remove. DeletePath.pre is typed
# FileState | SymlinkState, so no admissible timeline removes anything else — which is
# why OTHER is refused on a model ground rather than on an observation.
_REMOVABLE_KINDS = frozenset({EntryKind.REGULAR_FILE, EntryKind.SYMLINK})


def require_ancestors_legal(
    compiled: CompiledSpec,
    prefixes: Mapping[str, ResolvedPrefix],
    resolved: ResolvedTopology,
) -> None:
    """Every component past the frontier is a directory this transaction creates first.

    Two routes reach the same rule. A missing component simply does not exist yet. A
    component that exists as a file or symlink is the authority §6 case where absence is
    inferred from the ancestor's verified state rather than probed; it is admitted only
    when the timeline removes it and creates a directory in its place.

    Judged over nodes: `resolved.directory_node` maps a prefix to the directory it
    actually names, so `CreateDirectory("A")` is recognised as the creator of `a`'s
    ancestor on a folding volume.
    """
    creators: dict[TopologyNode, int] = {}
    removers: dict[TopologyNode, int] = {}
    for index, effect in enumerate(compiled.spec.effects):
        # Narrowed before `.path` is read: MoveNoClobber has `source` and `destination`
        # and no `path`, and these are the only two variants that create or clear a
        # directory anyway.
        if not isinstance(effect, (CreateDirectory, DeletePath)):
            continue
        node = resolved.directory_node(effect.path)
        if node is None:
            continue
        if isinstance(effect, CreateDirectory):
            creators.setdefault(node, index)
        else:
            removers.setdefault(node, index)

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
            _require_created_first(
                ancestor, _node_of(resolved, ancestor), path, first_touch[path], creators
            )
        if isinstance(prefix.frontier, PresentFrontier):
            blocking = "/".join(components[: depth + 1])
            _require_removed_before_creation(
                blocking, _node_of(resolved, blocking), creators, removers
            )


def _node_of(resolved: ResolvedTopology, prefix: str) -> TopologyNode:
    node = resolved.directory_node(prefix)
    if node is None:
        raise ProtocolError(
            f"{prefix!r} is an ancestor of a declared path but the topology assigned it "
            "no directory node; construction and judgment disagree about the candidates"
        )
    return node


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
    node: TopologyNode,
    path: str,
    touched_at: int,
    creators: Mapping[TopologyNode, int],
) -> None:
    creator = creators.get(node)
    if creator is None:
        raise ProjectApprovalRefused(
            f"{path!r} needs directory {ancestor!r}, which does not exist and which no "
            "CreateDirectory effect creates; a parent that neither exists nor is "
            "created by this transaction cannot be captured"
        )
    if creator >= touched_at:
        raise ProjectApprovalRefused(
            f"{path!r} is touched by effect {touched_at} but its ancestor {ancestor!r} "
            f"is created by effect {creator}; outer directory creation must precede "
            "every affected descendant"
        )


def _require_removed_before_creation(
    blocking: str,
    node: TopologyNode,
    creators: Mapping[TopologyNode, int],
    removers: Mapping[TopologyNode, int],
) -> None:
    """A blocking non-directory must be removed, then re-created as a directory.

    This is design §5.2's one bounded exception to "approval does not compare live state
    against a declared precondition": resolution stopped here, so the topology cannot be
    built without deciding whether this path becomes a directory, and the observation is
    already in hand.
    """
    creator = creators[node]  # _require_created_first already proved it exists
    remover = removers.get(node)
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

`removers` is keyed by directory node, so a `DeletePath` on a path that is not a directory
candidate — the common case, deleting a file — is skipped by the `node is None` branch. That is
correct: only a blocking ancestor's removal is ever consulted here.

- [ ] **Step 4: Run the tests and the gates**

```bash
uv run pytest tests/test_fs_judgment.py tests/test_fs_topology.py -v
uv run ruff check src/atoms/fs/judgment.py tests/
uv run pyright
```
Expected: PASS, `All checks passed!`, `0 errors`.

- [ ] **Step 5: Commit**

```bash
git add src/atoms/fs/judgment.py tests/test_fs_judgment.py
git commit -m "feat(fs): prove every unresolved ancestor is created in order"
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

**`CommitDecision` has exactly two members — `UNCOMMITTED` and `COMMITTED`.** There is no
`UNDECIDED`; a prepared transaction is `UNCOMMITTED`. And `required_scratch_role` comes from
`atoms.core.recovery.snapshot`, for the reason Task 2 records.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_fs_topology.py`:

```python
AGREEMENT_CORPUS = {
    "one file in an existing directory": (
        CreateFileNoClobber("e1", "a/b/leaf", file_state()),
    ),
    "a created directory and its child": (
        CreateDirectory("mk", "a", DirectoryState(mode=0o755)),
        CreateFileNoClobber("e1", "a/leaf", file_state()),
    ),
    "nested created directories": (
        CreateDirectory("mk", "a", DirectoryState(mode=0o755)),
        CreateDirectory("mk2", "a/b", DirectoryState(mode=0o755)),
        CreateFileNoClobber("e1", "a/b/leaf", file_state()),
    ),
    "a move across directories": (MoveNoClobber("mv", "src/a", "dst/b", file_state()),),
    "a move within one directory": (MoveNoClobber("mv", "d/a", "d/b", file_state()),),
    "a replace": (ReplaceFile("rp", "d/leaf", nonempty_state(b"old"), nonempty_state(b"new")),),
    "a delete": (DeletePath("rm", "d/leaf", file_state()),),
    "a file ancestor converted to a directory": (
        DeletePath("rm", "p", file_state()),
        CreateDirectory("mk", "p", DirectoryState(mode=0o755)),
        CreateFileNoClobber("e1", "p/q", file_state()),
    ),
    "a symlink removed beside a create": (
        DeletePath("rm", "d/link", SymlinkState(target="x", mode=0o777)),
        CreateFileNoClobber("e1", "d/leaf", file_state()),
    ),
    "every variant at once": (
        DeletePath("rm", "d/old", file_state()),
        CreateDirectory("mk", "d/new", DirectoryState(mode=0o755)),
        CreateFileNoClobber("e1", "d/new/leaf", file_state()),
        ReplaceFile("rp", "d/keep", nonempty_state(b"old"), nonempty_state(b"new")),
        MoveNoClobber("mv", "d/from", "d/new/to", file_state()),
    ),
}
"""Every effect variant, alone and combined, plus the shapes that stress the topology:
nested creation, a move whose endpoints share a parent, and the FILE → ABSENT → DIRECTORY
conversion. Each entry is verified to compile — a case A2 refuses is not a case."""


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
    )
    from atoms.core.recovery.snapshot import required_scratch_role

    return build_recovery_snapshot(
        compiled=compiled,
        topology=resolved.topology,
        transaction_state=TransactionState.PREPARED,
        commit_decision=CommitDecision.UNCOMMITTED,
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


@pytest.mark.parametrize("label", sorted(AGREEMENT_CORPUS))
def test_every_produced_topology_validates_through_a3(label):
    compiled = compiled_for(*AGREEMENT_CORPUS[label])
    resolved = build_topology(compiled, prefixes_for(compiled), EXT4, work_for(compiled))
    snapshot = _prepared_snapshot(compiled, resolved)
    assert snapshot.topology == resolved.topology


@pytest.mark.parametrize("label", sorted(AGREEMENT_CORPUS))
def test_the_rerun_reaches_a2s_verdict_on_the_named_corpus(label):
    """The readable half of design §11.4's A2-agreement property. Under today's floor the
    resolved topology is provably identical to the lexical one, so a compiled
    specification and its re-run cannot disagree. A failure means the re-run drifted or
    the floor moved (design §7.4)."""
    from atoms.fs.topology import require_resolved_surface_and_ordering

    compiled = compiled_for(*AGREEMENT_CORPUS[label])
    resolved = build_topology(compiled, prefixes_for(compiled), EXT4, work_for(compiled))
    require_resolved_surface_and_ordering(compiled, resolved)


def test_a3_and_the_rerun_accept_every_specification_the_generator_compiles():
    """Criterion 18 says *every* compiled input, and ten named examples are a corpus, not
    a property. The generator enumerates all ordered sequences of length 1-3 over a fixed
    23-effect pool -- every variant against every path in a five-path alphabet, plus three
    moves -- and keeps the ones A2 admits. On this checkout that is 4841 specifications
    out of 12719 candidates, and it runs in about four seconds.

    Bounded and deterministic rather than random: no seed to record, no flake, and a
    failure is reproducible from its label. The exact count is asserted rather than a
    floor: `checked == compiled_count` would be tautological, since nothing between the
    two increments can skip, and `> 4000` would still pass if the generator quietly lost
    a whole variant. Update the constant deliberately when the pool changes.
    """
    from atoms.fs.topology import require_resolved_surface_and_ordering

    checked = 0
    for label, effects in generated_specifications():
        compiled = compiled_for(*effects)
        resolved = build_topology(
            compiled, prefixes_for(compiled), EXT4, work_for(compiled)
        )
        assert _prepared_snapshot(compiled, resolved).topology == resolved.topology, label
        require_resolved_surface_and_ordering(compiled, resolved)
        checked += 1

    assert checked == GENERATED_SPECIFICATION_COUNT


def test_the_rerun_refuses_a_creation_ordered_after_its_descendant(injected_equivalence):
    """The reachable half of the re-run. A2 phase 13 is lexical: it sees
    CreateDirectory("A") and an effect on "a/x" as unrelated and admits this. Under a
    folding parent they are one directory and the creation is too late.

    The surface half has no reachable case. A declared path becomes an *ancestor node*
    only by being a directory candidate, and a directory candidate is either a lexical
    proper prefix — which A2's own trie already walks — or a CreateDirectory endpoint,
    whose declared state is a DirectoryState and therefore never a blocker. A folding
    file at `A` above `a/x` is refused one phase earlier, by ancestor legality, because
    nothing creates the directory `a`. The surface branch stays as a fail-closed guard.
    """
    from atoms.fs.topology import require_resolved_surface_and_ordering

    injected_equivalence(str.casefold)
    compiled = compiled_for(
        CreateFileNoClobber("e1", "a/x", file_state()),
        CreateDirectory("mk", "A", DirectoryState(mode=0o755)),
    )
    prefixes = {
        "a/x": resolved_prefix("a/x", existing_depth=0),
        "A": resolved_prefix("A", existing_depth=0),
    }
    resolved = build_topology(compiled, prefixes, EXT4, WORK_CONSTRAINTS)
    with pytest.raises(ProjectApprovalRefused) as caught:
        require_resolved_surface_and_ordering(compiled, resolved)
    assert "creation must come first" in str(caught.value)
```

Extend the module's imports to cover the corpus:

```python
import pytest

from atoms.core.effects import DeletePath, ReplaceFile
from atoms.core.errors import ProjectApprovalRefused
from atoms.core.fingerprint import SymlinkState
from tests.fs_support import (
    GENERATED_SPECIFICATION_COUNT,
    generated_specifications,
    nonempty_state,
    prefixes_for,
    work_for,
)
```

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
    # Annotated rather than inferred: the values are inserted as PersistentNode, but every
    # lookup below feeds them to a map that is also queried with parent nodes, and a parent
    # may be ProjectRoot. Without the annotation pyright infers dict[str, PersistentNode]
    # and reports the two `.get(ancestor)` calls as reportArgumentType.
    node_by_path: dict[str, TopologyNode] = {
        entry.path: PersistentNode(entry.path) for entry in resolved.paths
    }
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
                # An ABSENT ancestor blocks exactly as a file does: A2's phase 12 sets its
                # trie constraint from any declared state that is not a DirectoryState,
                # absence included, so omitting it here would admit what A2 refuses.
                if (
                    blocker is not None
                    and not isinstance(blocker, DirectoryState)
                    and not isinstance(state, AbsentState)
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
- Modify: `tests/conftest.py`

**Interfaces:**
- Consumes: `ResolvedTopology`, `ApprovedScratch`, `lookup_equivalence_key`, `scratch_leaf`,
  `required_scratch_role`, `WorkRoot`, `MoveNoClobber`.
- Produces: `require_endpoints_distinct(resolved: ResolvedTopology) -> None`;
  `bind_scratch(compiled: CompiledSpec, txid: str, resolved: ResolvedTopology) -> tuple[ApprovedScratch, ...]`.

- [ ] **Step 1: Write the failing tests**

Extend `tests/test_fs_judgment.py`'s existing imports — Task 3 already imports `build_topology`,
`EXT4`, and `WORK_CONSTRAINTS` at module level, so these tests use them directly rather than
re-importing inside each function:

```python
from atoms.core.recovery import WorkRoot
from atoms.fs.judgment import (
    bind_scratch,
    require_ancestors_legal,
    require_endpoints_distinct,
)
from tests.conftest import truncate_to_eight
```

Then append:

```python
def test_distinct_leaves_in_one_parent_are_admitted():
    compiled = compiled_for(
        CreateFileNoClobber("e1", "d/one", file_state()),
        CreateFileNoClobber("e2", "d/two", file_state()),
    )
    prefixes = {
        "d/one": resolved_prefix("d/one", existing_depth=1),
        "d/two": resolved_prefix("d/two", existing_depth=1),
    }
    require_endpoints_distinct(build_topology(compiled, prefixes, EXT4, None))


def test_two_paths_colliding_in_one_parent_are_refused(injected_equivalence):
    """Case folding cannot reach this check: two leaves fold in one parent only when
    their whole paths fold too, and A2 phase 4 refuses that pair before approval runs.
    A truncating relation — a real filesystem equivalence class A2's key does not
    subsume — is what makes the check observable."""
    injected_equivalence(truncate_to_eight)
    compiled = compiled_for(
        CreateFileNoClobber("e1", "d/sharedprefix-one", file_state()),
        CreateFileNoClobber("e2", "d/sharedprefix-two", file_state()),
    )
    prefixes = {
        "d/sharedprefix-one": resolved_prefix("d/sharedprefix-one", existing_depth=1),
        "d/sharedprefix-two": resolved_prefix("d/sharedprefix-two", existing_depth=1),
    }
    resolved = build_topology(compiled, prefixes, EXT4, None)
    with pytest.raises(ProjectApprovalRefused) as caught:
        require_endpoints_distinct(resolved)
    assert "one entry" in str(caught.value)


def test_scratch_leaves_bind_to_their_effects_parent():
    compiled = compiled_for(CreateFileNoClobber("e1", "d/leaf", file_state()))
    prefixes = {"d/leaf": resolved_prefix("d/leaf", existing_depth=1)}
    resolved = build_topology(compiled, prefixes, EXT4, None)
    bound = bind_scratch(compiled, "tx01", resolved)

    assert len(bound) == 1
    assert bound[0].effect_id == "e1"
    assert bound[0].leaf == ".#~tx01.e1.staging"
    assert bound[0].parent_node == resolved.parent_of("d/leaf")


def test_a_work_scratch_leaf_binds_to_the_work_root():
    compiled = compiled_for(CreateDirectory("mk", "a", DirectoryState(mode=0o755)))
    prefixes = {"a": resolved_prefix("a", existing_depth=0)}
    resolved = build_topology(
        compiled,
        prefixes,
        EXT4,
        WORK_CONSTRAINTS,
    )
    bound = bind_scratch(compiled, "tx01", resolved)
    assert bound[0].parent_node == WorkRoot()
    assert bound[0].leaf == ".#~tx01.mk.work"


def test_a_scratch_leaf_over_its_parents_name_max_is_refused():
    compiled = compiled_for(CreateFileNoClobber("e1", "d/leaf", file_state()))
    prefixes = {"d/leaf": resolved_prefix("d/leaf", existing_depth=1, name_max=8)}
    resolved = build_topology(compiled, prefixes, EXT4, None)
    with pytest.raises(ProjectApprovalRefused) as caught:
        bind_scratch(compiled, "tx01", resolved)
    assert "name limit" in str(caught.value)


def test_colliding_scratch_leaves_are_refused(injected_equivalence):
    """Effect IDs are exact-string and portability-key unique after A2 phase 6, so under
    case folding no two scratch leaves can collide. Truncation can: every leaf shares the
    `.#~tx01.` prefix, so an eight-byte key collapses the whole set. Regeneration is
    explicitly not a remedy — an intrinsic collision recurs under every txid."""
    compiled = compiled_for(
        CreateFileNoClobber("e1", "d/one", file_state()),
        CreateFileNoClobber("e2", "d/two", file_state()),
    )
    prefixes = {
        "d/one": resolved_prefix("d/one", existing_depth=1),
        "d/two": resolved_prefix("d/two", existing_depth=1),
    }
    resolved = build_topology(compiled, prefixes, EXT4, None)
    injected_equivalence(truncate_to_eight)
    with pytest.raises(ProjectApprovalRefused) as caught:
        bind_scratch(compiled, "tx01", resolved)
    assert "not a remedy" in str(caught.value)


def test_every_endpoint_leaf_is_asked_about(injected_equivalence):
    """The positive bypass check. A call site that decides `one` against `two` without the
    helper leaves the corresponding name out of this list, and no behavioural assertion
    can see that under an identity key."""
    calls = injected_equivalence(lambda name: name)
    compiled = compiled_for(
        CreateFileNoClobber("e1", "d/one", file_state()),
        CreateFileNoClobber("e2", "d/two", file_state()),
    )
    prefixes = {
        "d/one": resolved_prefix("d/one", existing_depth=1),
        "d/two": resolved_prefix("d/two", existing_depth=1),
    }
    resolved = build_topology(compiled, prefixes, EXT4, None)
    calls.clear()
    require_endpoints_distinct(resolved)
    assert calls == ["one", "two"]


def test_every_scratch_leaf_is_asked_about(injected_equivalence):
    calls = injected_equivalence(lambda name: name)
    compiled = compiled_for(
        CreateFileNoClobber("e1", "d/one", file_state()),
        CreateFileNoClobber("e2", "d/two", file_state()),
    )
    prefixes = {
        "d/one": resolved_prefix("d/one", existing_depth=1),
        "d/two": resolved_prefix("d/two", existing_depth=1),
    }
    resolved = build_topology(compiled, prefixes, EXT4, None)
    calls.clear()
    bind_scratch(compiled, "tx01", resolved)
    assert calls == [".#~tx01.e1.staging", ".#~tx01.e2.staging"]


def test_distinct_scratch_leaves_survive_under_exact_bytes():
    compiled = compiled_for(
        CreateFileNoClobber("e1", "d/one", file_state()),
        CreateFileNoClobber("e2", "d/two", file_state()),
    )
    prefixes = {
        "d/one": resolved_prefix("d/one", existing_depth=1),
        "d/two": resolved_prefix("d/two", existing_depth=1),
    }
    resolved = build_topology(compiled, prefixes, EXT4, None)
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
    # `key in seen` rather than comparing the two leaves: the collision is already decided
    # by the key, and re-comparing raw leaves is the exact bypass the AST guard forbids.
    seen: dict[tuple[TopologyNode, str], str] = {}
    for entry in resolved.paths:
        constraints = resolved.constraints_of(entry.parent_node)
        key = (entry.parent_node, lookup_equivalence_key(constraints, entry.leaf))
        if key in seen:
            raise ProjectApprovalRefused(
                f"declared paths {seen[key]!r} and {entry.path!r} name one entry under "
                f"the actual lookup policy of their shared parent {entry.parent_node!r}"
            )
        seen[key] = entry.path


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
        if key in seen:
            raise ProjectApprovalRefused(
                f"scratch leaves {seen[key]!r} and {entry.leaf!r} collide in one parent "
                "under its actual lookup policy; regenerating the txid is not a remedy "
                "because an intrinsic collision recurs under every txid"
            )
        seen[key] = entry.leaf
    return tuple(bound)
```

Extend `judgment.py`'s imports. `TopologyNode` and `ResolvedTopology` are already there from Task 3,
and `MoveNoClobber` joins the existing `atoms.core.effects` line rather than starting a second one —
ruff's isort would rewrite a duplicate:

```python
from atoms.core.effects import CreateDirectory, DeletePath, MoveNoClobber, occurrences
from atoms.core.recovery import ScratchRole, TopologyNode, WorkRoot
from atoms.core.recovery.snapshot import required_scratch_role
from atoms.core.scratch import scratch_leaf
from atoms.fs.lookup import lookup_equivalence_key
from atoms.fs.topology import ApprovedScratch, ResolvedTopology
```

This step gives `judgment.py` its first call to `lookup_equivalence_key`, so extend the double's
target list in `tests/conftest.py`:

```python
_EQUIVALENCE_CONSUMERS = ("atoms.fs.topology", "atoms.fs.judgment")
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
git add src/atoms/fs/judgment.py tests/test_fs_judgment.py tests/conftest.py
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

Append to `tests/conftest.py`. It forwards `withhold` to `ext4_bound_volume`, which already knows how
to bind a fake backend supplying everything except a named set — that is the only honest lever for
arming the capability refusal, because a real ext4 volume supplies every probed capability:

```python
@pytest.fixture
def approval_context(ext4_bound_volume):
    """A ProjectContext over a real ext4 binding, with a fixed txid."""
    import contextlib

    from atoms.fs.approval import ProjectContext

    @contextlib.contextmanager
    def build(txid: str = "tx01", withhold=frozenset()):
        with ext4_bound_volume(withhold=withhold) as binding:
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

from atoms.core.capabilities import Capability
from atoms.core.compiler import CompiledSpec
from atoms.core.effects import CreateDirectory, CreateFileNoClobber
from atoms.core.errors import (
    CapabilityUnavailable,
    PreconditionRefused,
    ProjectApprovalRefused,
    ProtocolError,
    SpecValidationError,
)
from atoms.core.fingerprint import DirectoryState
from atoms.fs.approval import ProjectApprovedSpec, ProjectContext, approve_for_project
from atoms.fs.binding import ProjectBinding
from tests.fs_support import compiled_for, file_state

WITHHELD = frozenset({Capability.DURABLE_PUBLISH})
"""DURABLE_PUBLISH is in ALWAYS_REQUIRED, so withholding it makes every specification's
required set unsatisfiable — no effect variant has to be chosen to arm the refusal."""


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


class _SubContext(ProjectContext):
    """A subclass passes an isinstance gate. §6.1 requires exact types, because a
    subclass can override a property the pipeline reads after the gate."""


class _SubTxid(str):
    pass


class _SubCompiled(CompiledSpec):
    pass


class _SubBinding(ProjectBinding):
    pass


def _uninitialised(subclass):
    """A subclass instance without running __init__.

    CompiledSpec and ProjectBinding are both factory-token guarded, so a subclass cannot
    be constructed normally — which is the point: `__new__` yields an object whose every
    attribute access would fail, so a gate that admits it and reads anything afterwards
    fails loudly instead of silently accepting a subclass.
    """
    return subclass.__new__(subclass)


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


def test_a_compiled_spec_subclass_is_refused(approval_context):
    with approval_context() as (context, _binding):
        with pytest.raises(ProtocolError) as caught:
            approve_for_project(_uninitialised(_SubCompiled), context)
        assert "exactly CompiledSpec" in str(caught.value)


def test_a_binding_subclass_is_refused():
    compiled = compiled_for(CreateFileNoClobber("e1", "leaf", file_state()))
    context = ProjectContext(binding=_uninitialised(_SubBinding), txid="tx01")
    with pytest.raises(ProtocolError) as caught:
        approve_for_project(compiled, context)
    assert "exactly ProjectBinding" in str(caught.value)


def test_a_context_subclass_is_refused(approval_context):
    compiled = compiled_for(CreateFileNoClobber("e1", "leaf", file_state()))
    with approval_context() as (_context, binding):
        subclassed = _SubContext(binding=binding, txid="tx01")
        with pytest.raises(ProtocolError) as caught:
            approve_for_project(compiled, subclassed)
        assert "exactly ProjectContext" in str(caught.value)


def test_a_txid_subclass_is_refused(approval_context):
    compiled = compiled_for(CreateFileNoClobber("e1", "leaf", file_state()))
    with approval_context() as (_context, binding):
        context = ProjectContext(binding=binding, txid=_SubTxid("tx01"))
        with pytest.raises(ProtocolError) as caught:
            approve_for_project(compiled, context)
        assert "exactly str" in str(caught.value)


@pytest.mark.parametrize("txid", [3, None, b"tx01"])
def test_a_non_string_txid_raises_protocol_error_not_type_error(approval_context, txid):
    """require_valid_identifier reaches re.Pattern.fullmatch, which raises TypeError on a
    non-string. The exact-type gate is what makes §5.1's promised ProtocolError reachable."""
    compiled = compiled_for(CreateFileNoClobber("e1", "leaf", file_state()))
    with approval_context() as (_context, binding):
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
    """Discriminating only because both refusals are armed at once. While the binding is
    live this specification raises CapabilityUnavailable; once it is closed the same call
    must raise ProtocolError instead, because §6.1 reads `backend` before `evidence`.
    `evidence` performs no liveness check by design, so an evidence-first pipeline would
    still see the missing capability and report it for a lease that is simply gone.

    Without the withheld capability this test passes under either ordering, which is the
    defect it exists to catch."""
    compiled = compiled_for(CreateFileNoClobber("e1", "leaf", file_state()))
    with approval_context(withhold=WITHHELD) as (context, binding):
        with pytest.raises(CapabilityUnavailable):
            approve_for_project(compiled, context)
        binding.__exit__()
        with pytest.raises(ProtocolError) as caught:
            approve_for_project(compiled, context)
        assert "closed" in str(caught.value)


def test_a_released_lock_refuses_the_same_way(approval_context):
    """ProjectBinding._require_active has two branches. The lock outliving check is the
    one A5's lease will exercise for real, so both are armed here.

    The lock is released through its own `__exit__`, not by setting `_held` directly:
    `HeldProjectLock.__exit__` returns early when `_held` is already False, so poking the
    flag would skip `close_all` and leak the lock and metadata-root descriptors while
    leaving the flock held for the process lifetime. `__exit__` is idempotent, so the
    fixture's own teardown is unaffected. Reaching `_lock` is private access, and
    deliberate: there is no public way to outlive a lock, which is the state under test.
    """
    compiled = compiled_for(CreateFileNoClobber("e1", "leaf", file_state()))
    with approval_context(withhold=WITHHELD) as (context, binding):
        binding._lock.__exit__()
        with pytest.raises(ProtocolError) as caught:
            approve_for_project(compiled, context)
        assert "released" in str(caught.value)


def test_a_missing_capability_refuses_before_any_path_is_resolved(
    approval_context, monkeypatch
):
    """Ledger #6: A4b is the sole adjudicator, and it refuses before touching the
    project. A resolver that raises on entry proves nothing reached phase B."""

    def forbidden(self, rel_path):
        raise AssertionError("phase B ran before capabilities were adjudicated")

    monkeypatch.setattr("atoms.fs.approval.PathResolver.resolve", forbidden)
    compiled = compiled_for(CreateFileNoClobber("e1", "leaf", file_state()))
    with approval_context(withhold=WITHHELD) as (context, _binding):
        with pytest.raises(CapabilityUnavailable) as caught:
            approve_for_project(compiled, context)
        assert "durable_publish" in str(caught.value)


def test_every_declared_path_resolves_exactly_once_in_sorted_order(
    approval_context, monkeypatch
):
    """§6.2: one resolution per declared path, in sorted order, so the observation set is
    reproducible and no path is walked twice under a different lock state."""
    from atoms.fs.resolve import PathResolver

    seen: list[str] = []
    original = PathResolver.resolve

    def recording(self, rel_path):
        seen.append(rel_path)
        return original(self, rel_path)

    monkeypatch.setattr("atoms.fs.approval.PathResolver.resolve", recording)
    compiled = compiled_for(
        CreateFileNoClobber("e2", "b", file_state()),
        CreateFileNoClobber("e1", "a", file_state()),
    )
    with approval_context() as (context, _binding):
        approve_for_project(compiled, context)
    assert seen == ["a", "b"]


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


def test_the_proof_retains_the_objects_it_approved(approval_context):
    """Criterion 1 says the *exact* `CompiledSpec` passed in, and criterion 16's ledger
    entry says the live binding. Identity, not equality: `CompiledSpec` is a frozen value,
    so an equal-but-reconstructed one would compare equal while proving nothing about what
    was actually judged, and ledger entry #19 turns on approval and use naming one object.
    """
    compiled = compiled_for(CreateFileNoClobber("e1", "leaf", file_state()))
    with approval_context() as (context, binding):
        proof = approve_for_project(compiled, context)
        assert proof.compiled is compiled
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
@pytest.mark.parametrize("target", ["__init__", "resolve", "work_base_facts"])
def test_every_resolver_exception_reaches_the_caller_unchanged(
    approval_context, monkeypatch, raised, target
):
    """Ledger #20 is categorical, so every load-bearing branch is covered, not just
    `resolve`: the constructor and `work_base_facts` are also A4b-1 calls approval makes.
    Identity rather than type, because a type assertion is satisfied by any same-class
    exception the code might raise on its own.

    The specification carries a CreateDirectory so work_base_facts is reached at all."""

    seen: list[tuple] = []

    def failing(self, *args, **kwargs):
        seen.append((args, kwargs))
        raise raised

    monkeypatch.setattr(f"atoms.fs.approval.PathResolver.{target}", failing)
    compiled = compiled_for(
        CreateDirectory("mk", "made", DirectoryState(mode=0o755))
    )
    with approval_context() as (context, binding):
        with pytest.raises(type(raised)) as caught:
            approve_for_project(compiled, context)
        assert caught.value is raised

    # §11.5 requires the call count and arguments, not just the object. Exactly one call
    # reaches the injected branch: the refusal propagates rather than being caught and
    # retried, which a re-raising handler around a retry loop would not satisfy.
    assert len(seen) == 1
    arguments, keywords = seen[0]
    assert keywords == {}
    if target == "__init__":
        assert arguments == (binding,)
    elif target == "resolve":
        assert arguments == ("made",)
    else:
        assert arguments == ()
```

Patching `__init__` makes the constructor raise before it returns, which is what the
`PathResolver(binding)` branch needs. A4b-1's `PathResolver` exposes exactly three entry points
approval calls — `__init__`, `resolve`, and `work_base_facts` (`resolve.py:148`, `:235`, `:186`) —
so the three parameters are the complete set, not a sample.


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

    # Phase C: judgment, pure. Construction first — every judgment below is keyed on the
    # nodes it produces, never on path spellings. Endpoint distinctness comes next, so
    # that every later phase may key a map by declared path: once it has passed, no two
    # declared paths name one entry.
    resolved = build_topology(compiled, prefixes, filesystem_type, work_constraints)
    require_endpoints_distinct(resolved)
    require_ancestors_legal(compiled, prefixes, resolved)
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
from atoms.core.recovery import PersistentNode
from atoms.fs.approval import approve_for_project
from atoms.fs.lookup import read_lookup_constraints
from tests.fs_support import compiled_for, file_state


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


@pytest.mark.parametrize(
    ("effect", "expected"),
    [
        (CreateFileNoClobber("e1", "leaf", file_state()), 0),
        (CreateDirectory("mk", "made", DirectoryState(mode=0o755)), 1),
    ],
    ids=["no-create-directory", "create-directory"],
)
def test_work_base_facts_is_called_exactly_when_a_directory_is_created(
    approval_context, monkeypatch, effect, expected
):
    """Criterion 7 says *iff*, which is two claims, and `proof.work_base is None` proves
    only the weaker one. An implementation that observed the work base and then discarded
    the observation would satisfy that assertion while still issuing the I/O the criterion
    forbids — and that I/O is not free: it opens `metadata_root/work`, which A5 has not yet
    created at this point in the lifecycle. Counting on the method fails on the call rather
    than on the value.

    Patched on the class, not the instance: approval constructs its own `PathResolver`, so
    a test never holds the instance to patch.
    """
    from atoms.fs.resolve import PathResolver

    calls = 0
    original = PathResolver.work_base_facts

    def counting(self):
        nonlocal calls
        calls += 1
        return original(self)

    monkeypatch.setattr(PathResolver, "work_base_facts", counting)
    with approval_context() as (context, _binding):
        proof = approve_for_project(compiled_for(effect), context)

    assert calls == expected
    assert (proof.work_base is not None) is bool(expected)


def test_approval_writes_nothing_into_project_space(approval_context):
    """Criterion 24. Approval is a judgment: it opens project directories read-only and
    must leave every byte of the tree it judged alone. The specification is chosen to
    exercise the branches that are most tempted to write — a `CreateDirectory` whose
    directory does not exist yet, and a scratch set that A5 will later instantiate.

    Compares a full recursive snapshot including names, types, sizes, inodes, and mtimes
    rather than a bare `listdir`: a same-size rewrite keeps both the entry list and the
    size unchanged.
    """
    with approval_context() as (context, binding):
        root = binding.project_root_fd
        os.mkdir("d", dir_fd=root)
        fd = os.open("d/kept", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644, dir_fd=root)
        try:
            os.write(fd, b"payload")
        finally:
            os.close(fd)

        before = _tree_snapshot(root)
        compiled = compiled_for(
            CreateDirectory("mk", "d/made", DirectoryState(mode=0o755)),
            CreateFileNoClobber("e1", "d/made/leaf", file_state()),
        )
        approve_for_project(compiled, context)

        assert _tree_snapshot(root) == before


def _tree_snapshot(root_fd: int, path: str = ".") -> dict[str, tuple]:
    """Every entry beneath `root_fd`, keyed by relative path.

    `st_ino` and `st_mtime_ns` are included so a same-size rewrite or an atomic replace is
    visible, not just a change in the entry list.

    The descriptor is closed explicitly. `os.scandir(fd)` does not take ownership of the
    descriptor it is handed and exiting its context manager does not close it, so relying
    on that leaks one per directory per call.
    """
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC, dir_fd=root_fd)
    try:
        snapshot: dict[str, tuple] = {}
        with os.scandir(fd) as items:
            for entry in items:
                relative = f"{path}/{entry.name}"
                info = entry.stat(follow_symlinks=False)
                snapshot[relative] = (
                    entry.is_dir(follow_symlinks=False),
                    info.st_mode,
                    info.st_size,
                    info.st_ino,
                    info.st_mtime_ns,
                )
                if entry.is_dir(follow_symlinks=False):
                    snapshot |= _tree_snapshot(root_fd, relative)
        return snapshot
    finally:
        os.close(fd)


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


def test_two_spellings_stay_two_directories_on_a_real_volume(approval_context):
    """Criterion 15's `EXACT_BYTES` half, against a real fixture rather than a synthetic
    table. Physical `a` exists, so `a/x` resolves through it and keys by inode; `A` is a
    planned directory keyed by (root, "A"). ext4 without casefold distinguishes them, so
    approval issues two nodes — and this is the half that needs no injected double,
    because the real volume supplies the relation."""
    with approval_context() as (context, binding):
        root = binding.project_root_fd
        os.mkdir("a", dir_fd=root)
        compiled = compiled_for(
            CreateDirectory("mk", "A", DirectoryState(mode=0o755)),
            CreateFileNoClobber("e1", "a/x", file_state()),
        )
        proof = approve_for_project(compiled, context)

        parent_of_x = next(
            entry.parent_node for entry in proof.paths if entry.path == "a/x"
        )
        assert parent_of_x != PersistentNode("A")
        assert PersistentNode("A") in {edge.node for edge in proof.topology.parents}


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
- Modify: `docs/deferred-obligation-ledger.md`

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


RAW_COMPONENT_ATTRS = frozenset({"leaf", "declared_component"})
LAUNDERING_CALLS = frozenset({"lookup_equivalence_key", "len"})


def _called_name(call):
    func = call.func
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")


def _mentions_raw(node):
    """True if this expression still carries a raw path component.

    A subtree rooted at a laundering call does not: lookup_equivalence_key is the whole
    point of the rule, and len() yields a byte width, which no equality decision about a
    name can be made from.
    """
    if isinstance(node, ast.Call) and _called_name(node) in LAUNDERING_CALLS:
        return False
    if isinstance(node, ast.Attribute) and node.attr in RAW_COMPONENT_ATTRS:
        return True
    return any(_mentions_raw(child) for child in ast.iter_child_nodes(node))


def _aliases_of_raw(tree):
    """Locals bound to a raw component, so `leaf = entry.leaf` does not launder it.

    Only plain-name targets are tainted. `seen[key] = entry.leaf` stores a raw component
    in a container, which is not aliasing — tainting `seen` there would reject the
    pipeline's own idiom.
    """
    tainted = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        if node.value is None or not _mentions_raw(node.value):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if isinstance(target, ast.Name):
                tainted.add(target.id)
            elif isinstance(target, ast.Tuple):
                tainted.update(
                    element.id
                    for element in target.elts
                    if isinstance(element, ast.Name)
                )
    return tainted


@pytest.mark.parametrize("module_name", PURE_MODULES)
def test_the_pure_modules_never_decide_equality_on_a_raw_component(module_name):
    """What the injected_equivalence double cannot catch: under an identity key a direct
    comparison behaves exactly like the function it bypasses, so every EXACT_BYTES case
    still passes and the double reports nothing.

    Guarded shapes are comparisons — which covers `==`, `!=`, `in`, and `not in` — and
    mapping keys, over both a raw attribute and any local aliased from one. A value
    produced BY lookup_equivalence_key launders the taint, which is what makes the
    pipeline's `key = (parent, lookup_equivalence_key(...))` idiom legal.

    **This is a lint over the shapes it names, not a soundness proof.** Alias discovery is
    one hop, so `a = entry.leaf; b = a; b == x` slips through, as do a component recovered
    via `split`, a key built from a length, and a dict-literal key. Making it sound needs
    real dataflow analysis, which is not worth building here. The positive check is what
    carries the weight: injected_equivalence records the names it is asked about, so a
    call site that bypasses the helper contributes nothing to that list however it is
    written. This guard catches the direct shape cheaply; the recording catches omission.
    """
    tree = ast.parse(
        (SOURCE_ROOT / "fs" / f"{module_name}.py").read_text(encoding="utf-8")
    )
    tainted = _aliases_of_raw(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            checked = [node.left, *node.comparators]
        elif isinstance(node, ast.Subscript):
            checked = [node.slice]
        else:
            continue
        for expression in checked:
            for inner in ast.walk(expression):
                if isinstance(inner, ast.Attribute):
                    assert inner.attr not in RAW_COMPONENT_ATTRS, (
                        f"{module_name}.py decides {inner.attr} equality directly; "
                        "route it through lookup_equivalence_key"
                    )
                if isinstance(inner, ast.Name):
                    assert inner.id not in tainted, (
                        f"{module_name}.py decides equality on {inner.id}, aliased from "
                        "a raw path component; route it through lookup_equivalence_key"
                    )


def test_approval_catches_nothing_a_resolver_raises():
    """Ledger #20 is categorical. The correct number of handlers enclosing a resolver
    call is zero — not "no blanket handler", which is the weaker rule resolve.py has.
    `contextlib.suppress` is checked too: it swallows exactly as a handler does, and a
    guard that missed it would be satisfied by the one bypass someone would reach for."""
    tree = ast.parse((SOURCE_ROOT / "fs" / "approval.py").read_text(encoding="utf-8"))
    calls = {"resolve", "work_base_facts", "PathResolver"}
    enclosing = [node for node in ast.walk(tree) if isinstance(node, ast.Try)]
    enclosing += [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.With)
        and any(
            isinstance(item.context_expr, ast.Call)
            and _called_name(item.context_expr) == "suppress"
            for item in node.items
        )
    ]
    for node in enclosing:
        for inner in ast.walk(node):
            if isinstance(inner, ast.Call):
                assert _called_name(inner) not in calls, (
                    f"a handler in approval.py encloses {_called_name(inner)}(); "
                    "ledger #20 requires every A4b-1 refusal to reach the caller "
                    "unhandled"
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

Nine rows move and one is narrowed. This file carries authority (`AGENTS.md` §"Deferred obligations"),
so the exact Markdown is given rather than described.

**Delete** rows #2, #4, #5, #6, #9, #10, #11, #16, and #20 from the "Open obligations" table. Rows #1,
#7, #8, #12, #13, #14, #15, #17, #18, and #21 are untouched.

**Row #3 stays**, because it names three owners and only A3's and A4b's parts are complete. Replace it
with this row — owner list narrowed to `A6`, required behavior trimmed to A6's clause, and the `§13.1`
verification reference dropped with A3's clause:

```markdown
| 3 | An ancestor whose type changes mid-transaction (`FILE`/`SYMLINK` → `ABSENT` → `DIRECTORY`) with declared descendants | A2 phase 12 | A6 | §6's second absence-capture case infers descendant absence from the ancestor's verified fingerprint — descriptor-coherent for a file, destructive-transfer validation for a symlink — then hands §9.5's published descriptor down | §13.2, §13.4 |
```

**Replace** the "Discharged obligations" section — currently the "None yet" paragraph — with this,
keeping that paragraph's second sentence, which is still true:

```markdown
## Discharged obligations

| # | Admitted shape | Admitted by | Discharged by | Date | Verification suite |
| --- | --- | --- | --- | --- | --- |
| 2 | Two declared paths distinct under A2's whole-path portability key may still name one entry | A2 phase 4 | A4b-2 | 2026-07-31 | `tests/test_fs_judgment.py` |
| 4 | Path and component lengths are unbounded | A2 phase 3 | A4b-1, A4b-2 | 2026-07-31 | `tests/test_fs_resolve_walk.py`, `tests/test_fs_judgment.py`, `tests/test_fs_approval.py` |
| 5 | Paths are stored verbatim; no resolution or containment is performed | A2 (whole) | A4b-1 | 2026-07-31 | `tests/test_fs_resolve_walk.py`, `tests/test_fs_resolve_conformance.py` |
| 6 | A required-capability set is derived but never checked against a backend | A2 §3 | A4b-2 | 2026-07-31 | `tests/test_fs_approval.py` |
| 9 | `CompiledSpec` proves only A2's pure lexical/model rules and carries no project/root approval | A2 boundary | A4b-2 | 2026-07-31 | `tests/test_fs_approval.py`, `tests/test_fs_architecture.py` |
| 10 | A2's exact-spelling tree may differ from the tree after actual per-directory name equivalence is resolved (`A` versus `a/x`) | A2 phases 4, 12–13 | A3, A4b-2 | 2026-07-31 | `tests/test_fs_topology.py` |
| 11 | A2 proves scratch grammar separation and fixed NFC/casefold effect-ID uniqueness, but not the actual distinctness of every instantiated effect/role leaf in its concrete parent | A1 §5.1, A2 phases 3 and 6 | A4b-2 | 2026-07-31 | `tests/test_fs_judgment.py` |
| 16 | `VolumeEvidence` is a detached frozen value describing a volume, and authorizes no access to it | A4a binding contract | A4b-2 | 2026-07-31 | `tests/test_fs_approval.py`, `tests/test_fs_architecture.py` |
| 20 | A4b-1 refuses by raising, and nothing forces A4b-2 to surface those refusals rather than catching them | A4b-1/A4b-2 seam | A4b-2 | 2026-07-31 | `tests/test_fs_approval.py`, `tests/test_fs_architecture.py` |

A1's admissions were all discharged by A2; they are listed in that plan's self-review rather than
duplicated here.
```

The "Required behavior" column is not carried over: for a discharged entry the behavior is whatever the
suite in the last column asserts, and a frozen restatement beside it would drift.

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
§6.2 phase B → Task 6; §6.3.1 → Task 3; §6.3.2 → Tasks 1 and 5; §6.3.3 → Task 5; §7.1–§7.3 → Task 2;
§7.4 → Task 4; §7.5 → Tasks 6 and 7; §8 → Tasks 2 and 6; §9 → Tasks 3, 5, 6; §11.1 → Tasks 3 and 5;
§11.2 → Task 2; §11.3 → Task 4; §11.4 → Task 7; §11.5 → Task 6; §11.6 → Task 8.

**Construction precedes judgment, and the design was amended to say so.** The first draft ran
`require_ancestors_legal` before `build_topology` and keyed it on path strings. That cannot implement
its own motivating case: with `CreateDirectory("A")` and an effect on `a/x`, it looks for a creator of
the string `"a"`, finds none, and refuses a specification A2 admits and a folding volume makes legal.
The candidate set was wrong for the same reason — `A` is nobody's lexical prefix, so it was not a
directory in the key space at all and there was nothing for `a` to merge into.

Both are fixed structurally rather than patched: §7.1's candidates now include every `CreateDirectory`
endpoint, phase C runs construction → endpoint distinctness → ancestor legality → the re-run → scratch
binding, and every judgment is keyed on `TopologyNode` through `ResolvedTopology.directory_node`.
Design §6.3, §6.3.1, §7.1, and §7.4 were amended in the same commit; the plan does not deviate from the
design silently.

**Which refusals are reachable, stated rather than implied.** Three of this layer's checks have no
failure mode that can be written today, and a reviewer should know that before hunting for the missing
test.

| Check | Reachable? | Why |
| --- | --- | --- |
| Endpoint distinctness (leaf level) | Only under an injected key | Two leaves fold in one parent only when their whole paths fold too, and A2 phase 4 applies `portability_equivalence_key` to the whole path, so it refuses every such pair first |
| Directory-level collision in `_nodes_by_key` | No | Same subsumption, applied to two `CreateDirectory` endpoints: `A` and `a` never compile together |
| The re-run's **surface** half | No | A declared path is an ancestor node only by being a directory candidate — a lexical proper prefix (A2's trie already walks it) or a `CreateDirectory` endpoint (declared `DirectoryState`, never a blocker). A folding *file* at `A` above `a/x` is refused one phase earlier by ancestor legality |
| The re-run's **ordering** half | Yes, under an injected key | A2 phase 13 is lexical and admits `CreateDirectory("A")` after an effect on `a/x`; the resolved judgment refuses it |
| Ancestor legality, both directions | Yes, under an injected key | The folding pair is admitted when created first and refused when created second, and refused outright under exact bytes |

Each unreachable branch stays in the production code as a fail-closed guard. None of them gets a test
whose input cannot compile — that is a test that asserts nothing while looking like coverage, which is
the defect this section exists to prevent.

**The equivalence double is a factory, because one relation cannot do both jobs.** Case folding merges
directories, which A2 admits (`A` differs from `a/x` as whole paths). Case folding cannot reach
endpoint or scratch distinctness at all, for the reason in the table. Those tests use an eight-byte
truncation — a real filesystem equivalence class A2's whole-path key does not subsume. The fixtures are
`d/sharedprefix-one` / `d/sharedprefix-two`, which share `sharedpr`, and the scratch leaves
`.#~tx01.e1.staging` / `.#~tx01.e2.staging`, which share `.#~tx01.`.

**What the double cannot catch, and what covers it instead.** Under an identity key a direct comparison
behaves exactly like the function it bypasses, so no injected relation detects a call site that skips
`lookup_equivalence_key`. Task 8's AST guard covers that, and it is stronger than a comparison scan: it
taints locals aliased from a raw component, checks mapping keys as well as comparisons, and treats a
value produced *by* `lookup_equivalence_key` — or by `len`, which yields a byte width and not a name —
as laundered. It was run against the proposed `topology.py` and `judgment.py` before this plan was
committed, which is how `previous != entry.leaf` was found and replaced with `if key in seen`.

**Two production defects the extracted-fence run caught, after the plan looked right.** Both were
found by copying the plan's `topology.py` and `judgment.py` out of their fences into the package and
exercising them, not by reading.

1. **Every `MoveNoClobber` raised `AttributeError`.** The creator scan read `effect.path` before
   narrowing the variant; a move has `source` and `destination` and no `path`. The scan now narrows to
   `CreateDirectory | DeletePath` first, and Task 3 covers a resolved move and a move beneath a created
   directory.
2. **A `CreateDirectory` endpoint that parents nothing was retained in the proof.** Adding those
   endpoints to the candidate set — the fix for the previous round's P0 — also put them in the fact
   table, so a lone `CreateDirectory("a")` retained `PersistentNode("a")`. That contradicts §7.3 and
   criterion 14, which say `ApprovedPlannedDirectory` is exactly `WorkRoot` plus the **parent**
   `PersistentNode`s. Edges are now built first and facts filtered to nodes that actually parent
   something. The partition test asserted only types and disjointness, which passes for both an
   omission and an extra; it now asserts set equality.

**The bypass check is two mechanisms, and the AST guard is the weaker one.** It is a lint over the
shapes it names — comparisons and mapping keys, over a raw attribute or a one-hop alias. It does not
catch a transitive alias, a component recovered through `split`, a key built from a length, or a
dict-literal key, and making it sound needs real dataflow analysis. So `injected_equivalence` now
records the names it is asked about, and each phase asserts the exact list: `["one", "two"]` for
endpoint distinctness, the two full scratch leaves for binding, `["a", "b"]` for planned keying. A call
site that decides a name without the helper contributes nothing to that list however it is written.
Design §11.6 and criterion 16 were amended to require both.

**The agreement property is generated, not curated.** Criterion 18 says *every* compiled input, and ten
named examples are a corpus. `generated_specifications` enumerates all ordered sequences of length 1–3
over a 23-effect pool — four single-path variants against five paths, plus three moves — and keeps the
ones A2 admits. On this checkout that is **4841 specifications out of 12719 candidates**, all of which
build a topology A3 accepts and pass the re-run, in 3.6 seconds. Bounded and deterministic: no seed, no
flake, and a failure reproduces from its label. The named corpus stays for readable failure messages.
The enumeration is `itertools.product`, not `permutations`: drawing without replacement omitted the 16
sequences that touch one path twice with the same variant, `cf:a | rm:a | cf:a` among them — precisely
the ancestor-type churn of ledger entry #3. The count is asserted exactly, because the former
`checked == compiled_count` could not fail and `> 4000` would survive losing a whole variant.

**Every expected value in this round was executed before it was written down.** The extracted modules
were run against each new case: the symlink pair, both move cases, the partition equality, the lone
`CreateDirectory`, the three spy call lists, and the full 4841-entry matrix. `CompiledSpec` and
`ProjectBinding` subclasses are constructed through `__new__`, since both are token-guarded — which
strengthens the test rather than weakening it, because the resulting object has no usable attribute,
so a gate that admits it fails loudly.

**Two places a reviewer should look hardest.**

1. **Task 4's `require_resolved_surface_and_ordering` walks ancestors through `parent_by_node`, which
   contains only nodes that have edges.** A path parented by `ProjectRoot` terminates correctly because
   `ProjectRoot` has no parent edge, so `.get` returns `None`. Verify that on a single-component path
   before trusting the loop.
2. **The released-lock test releases through `HeldProjectLock.__exit__`, not by setting `_held`.**
   `__exit__` returns early when `_held` is already False, so poking the flag skips `close_all` and
   leaks the lock and metadata-root descriptors while leaving the flock held for the process lifetime.
   `__exit__` is idempotent, so the fixture's teardown still works. Confirm that reading `lock.py:194`
   before trusting it.
3. **`build_topology`'s edge set is keyed by node, not appended.** Two prefixes that fold together are
   one directory and must contribute one edge; A3's `_validate_topology` fails a node appearing twice in
   `topology.parents` even when both edges name the same parent. That deduplication is the only thing
   standing between the folding merge and an A3 rejection, and it has no dedicated test — it is covered
   only through `test_planned_directories_merge_under_a_folding_key` reaching construction at all.

**Verified against the codebase rather than assumed.** Every API this plan calls was checked on this
checkout: `CommitDecision` has exactly `UNCOMMITTED` and `COMMITTED`; `required_scratch_role` is not in
`atoms.core.recovery.__all__` and may not be added, because
`test_public_surface_has_exactly_five_operations` pins that package's exported functions to A3's five
operations; `PathResolver` exposes exactly `__init__`, `resolve`, and `work_base_facts`; `scratch_leaf`
takes `(txid, effect_id, role)`; the scratch roles are STAGING / WORK / TOMBSTONE / ANCHOR / STAGING for
the five variants; `DURABLE_PUBLISH` is in `ALWAYS_REQUIRED`, which is what arms the capability refusal
in Task 6; and `ext4_bound_volume` already accepts `withhold`. Every entry in Task 4's
`AGREEMENT_CORPUS` was run through `compile_spec` — that is how the `ReplaceFile` cases were found to
need real digests, since A2 refuses a non-zero `byte_len` under the empty-content hash.

**The third round closed five findings, and three of them were the same mistake.** Criteria 1, 7, and 24
each state a claim the plan asserted a *proxy* for. Criterion 1 says the exact `CompiledSpec`; the plan
checked only `proof.binding is binding`. Criterion 7 says `work_base_facts()` is *called* iff a
`CreateDirectory` is present; the plan checked `proof.work_base is None`, which an implementation that
observed the work base and discarded the result would also satisfy — and that call is not free, since it
opens `metadata_root/work`, which A5 has not created at this point. Criterion 24 says no write to
project space and had no test at all. All three are now asserted directly: identity, a call counter on
`PathResolver.work_base_facts` patched at the class because approval builds its own resolver, and a
recursive before/after tree snapshot carrying inode and mtime so a same-size rewrite is visible.

Writing that snapshot surfaced a real leak: `os.scandir(fd)` does not take ownership of the descriptor,
and exiting its context manager does not close it. Measured — 500 calls over a four-entry tree held 600
descriptors open. The helper closes explicitly in a `finally`.

**The property was drawing without replacement.** `itertools.permutations` cannot emit a sequence that
uses one candidate twice, so the matrix silently omitted all 16 A2-admitted sequences that touch one
path twice with the same variant — `cf:a | rm:a | cf:a` and its siblings, which is exactly the
ancestor-type churn ledger entry #3 describes. `product` raises the matrix from 4825 to 4841; all 16 pass
A3 and the re-run, so this was a coverage hole rather than a hidden defect. The count is now asserted
exactly: `checked == compiled_count` could not fail, since nothing between the two increments can skip,
and `> 4000` would have survived losing a whole variant.

**`node_by_path` needed an annotation, not a cast.** Its values go in as `PersistentNode` but every
lookup queries the resulting maps with parent nodes, and a parent may be `ProjectRoot` — two
`reportArgumentType` errors, reproduced by assembling the fence and running pyright on it. Annotating
the dict as `dict[str, TopologyNode]` clears both, and is the honest description of what it holds.

**The ledger step now contains the ledger.** It named no file and left the implementer to reconstruct
row #3's narrowing and a table that does not yet exist. Both are written out verbatim, and every copied
cell was diffed against the current ledger. The discharged table drops the "Required behavior" column:
for a discharged entry the behavior is whatever its suite asserts, and a frozen restatement beside it
would drift.

**Placeholder scan.** Clean — no TBD, no "similar to Task N", no step that describes without showing,
no test whose body is a shape to be finished later. `EXT4` is no longer redefined in Task 2; it has
existed at `fs_support.py:25` since A4a.

**Type consistency.** `ResolvedTopology.parent_of` takes a path string and returns a `TopologyNode`;
`parent_node_of` takes a node and returns its parent node; `directory_node` takes a directory prefix and
returns `TopologyNode | None`, `None` meaning no directory sits there. All three are used with those
meanings in Tasks 2 through 6. `ApprovedScratch.leaf` and `ApprovedPath.leaf` are both plain `str`.
`work_constraints` is `DirectoryConstraints | None` in `build_topology` and `ApprovedWorkBase.constraints`
is non-optional — these are different values: the first is `work/<txid>/`'s derived constraints, the
second is physical `work/`'s observed ones.

---

## Execution Handoff

Plan complete and saved to `docs/plans/2026-07-31-plan-a4b2-project-approval.md`. Two execution
options:

**1. Subagent-Driven (recommended)** — a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — execute tasks in this session using `superpowers:executing-plans`, batch
execution with checkpoints for review.
