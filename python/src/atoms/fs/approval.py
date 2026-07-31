"""Rooted project approval (A4b-2 design §6).

approve_for_project is the sole public construction authority for ProjectApprovedSpec.
No except clause in this module encloses a resolver call: ledger #20 is categorical, and
the correct count is zero rather than "no blanket handler".
"""

from __future__ import annotations

from dataclasses import dataclass

from atoms.core.compiler import CompiledSpec
from atoms.core.effects import CreateDirectory
from atoms.core.errors import (
    CapabilityUnavailable,
    ProjectApprovalRefused,
    ProtocolError,
    SpecValidationError,
)
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
