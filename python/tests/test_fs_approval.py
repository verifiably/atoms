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
    with approval_context() as (context, _binding), pytest.raises(ProtocolError):
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
                directory_paths=proof.directory_paths,
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
