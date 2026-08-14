"""Rooted project approval (A4b-2 design §6).

approve_for_project is the sole public construction authority for ProjectApprovedSpec.
No except clause in this module encloses a resolver call: ledger #20 is categorical, and
the correct count is zero rather than "no blanket handler".
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, cast

from atoms.core.capabilities import Capability
from atoms.core.compiler import CompiledSpec
from atoms.core.effects import CreateDirectory
from atoms.core.errors import (
    CapabilityUnavailable,
    ProjectApprovalRefused,
    ProtocolError,
    SpecValidationError,
)
from atoms.core.identifiers import require_valid_identifier
from atoms.core.paths import require_rel_path
from atoms.core.recovery import (
    PersistentNode,
    ProjectRoot,
    RecoveryTopology,
    TopologyDirectory,
    TopologyNode,
    WorkRoot,
)
from atoms.fs.binding import ProjectBinding, VolumeEvidence
from atoms.fs.judgment import (
    bind_scratch,
    require_ancestors_legal,
    require_endpoints_distinct,
)
from atoms.fs.lookup import LookupProof, inherited_constraints
from atoms.fs.resolve import PathResolver, ResolvedPrefix
from atoms.fs.topology import (
    ApprovedDirectory,
    ApprovedExistingDirectory,
    ApprovedPath,
    ApprovedPlannedDirectory,
    ApprovedScratch,
    ApprovedWorkBase,
    ResolvedTopology,
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
    directory_paths: tuple[tuple[str, TopologyNode], ...]
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
        directory_paths: tuple[tuple[str, TopologyNode], ...],
        paths: tuple[ApprovedPath, ...],
        scratch: tuple[ApprovedScratch, ...],
        work_base: ApprovedWorkBase | None,
        _construction_token: object | None = None,
    ) -> None:
        if _construction_token is not _TOKEN:
            raise TypeError(
                "ProjectApprovedSpec values are created only by approve_for_project "
                "or _approve_for_recovery"
            )
        object.__setattr__(self, "compiled", compiled)
        object.__setattr__(self, "binding", binding)
        object.__setattr__(self, "txid", txid)
        object.__setattr__(self, "topology", topology)
        object.__setattr__(self, "directories", directories)
        object.__setattr__(self, "directory_paths", directory_paths)
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


def _integer_evidence(value: object, label: str) -> int:
    if type(value) is not int:
        raise ProtocolError(
            f"{label} must be exactly int, got {type(value).__name__}"
        )
    return value


def _node_key(node: object) -> str:
    if type(node) is ProjectRoot:
        return "project_root"
    if type(node) is WorkRoot:
        return "work_root"
    if type(node) is TopologyDirectory:
        node_id = _integer_evidence(node.node_id, "node_id")
        if node_id < 0:
            raise ProtocolError("topology directory node_id is not a canonical decimal")
        return f"topology_directory:{node_id}"
    if type(node) is PersistentNode:
        if type(node.path) is not str:
            raise ProtocolError("persistent topology node path must be exactly str")
        return f"persistent:{node.path}"
    raise ProtocolError(
        f"{type(node).__name__} is not a directory topology node with a stable key"
    )


def _constraint_evidence(constraints: object) -> dict[str, object]:
    from atoms.fs.lookup import DirectoryConstraints

    if type(constraints) is not DirectoryConstraints:
        raise ProtocolError("approved directory constraints have the wrong exact type")
    if type(constraints.lookup_proof) is not LookupProof:
        raise ProtocolError("approved lookup proof has the wrong exact type")
    return {
        "lookup_proof": constraints.lookup_proof.value,
        "name_max": _integer_evidence(constraints.name_max, "name_max"),
    }


def _identity_evidence(identity: object) -> dict[str, int]:
    from atoms.fs.resolve import FilesystemIdentity

    if type(identity) is not FilesystemIdentity:
        raise ProtocolError("approved directory identity has the wrong exact type")
    return {
        "st_dev": _integer_evidence(identity.device, "st_dev"),
        "st_ino": _integer_evidence(identity.inode, "st_ino"),
    }


def _proof_directory_paths(resolved: ResolvedTopology) -> tuple[tuple[str, TopologyNode], ...]:
    routes: list[tuple[str, TopologyNode]] = []
    for entry in resolved.directories:
        if type(entry.node) is WorkRoot:
            continue
        matches = tuple(
            path for path, node in resolved.directory_nodes if node == entry.node
        )
        if len(matches) != 1:
            raise ProtocolError(
                f"approved directory {entry.node!r} has {len(matches)} project routes"
            )
        routes.append((matches[0], entry.node))
    return tuple(sorted(routes, key=lambda item: item[0]))


def encode_approval_evidence(approved: ProjectApprovedSpec) -> str:
    """Canonical A7 recovery evidence, with one stable key per directory node."""
    _require_exact(approved, ProjectApprovedSpec, "approved")
    directories: list[dict[str, object]] = []
    paths = {node: path for path, node in approved.directory_paths}
    for entry in approved.directories:
        if type(entry) is ApprovedExistingDirectory:
            identity: dict[str, int] | None = _identity_evidence(entry.identity)
        elif type(entry) is ApprovedPlannedDirectory:
            identity = None
        else:
            raise ProtocolError(
                f"approved directory has unexpected type {type(entry).__name__}"
            )
        directories.append(
            {
                "node": _node_key(entry.node),
                "path": None if type(entry.node) is WorkRoot else paths[entry.node],
                "identity": identity,
                **_constraint_evidence(entry.constraints),
            }
        )
    directories.sort(key=lambda item: str(item["node"]))

    work_base = approved.work_base
    work_root = None
    if work_base is not None:
        work_root = {
            "identity": _identity_evidence(work_base.identity),
            **_constraint_evidence(work_base.constraints),
        }
    return json.dumps(
        {
            "directories": directories,
            "mount_id": _integer_evidence(
                approved.binding.evidence.mount_id, "mount_id"
            ),
            "work_root": work_root,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


_TOPOLOGY_NODE = re.compile(r"^topology_directory:(0|[1-9][0-9]*)$")


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError(f"duplicate approval-evidence JSON key: {key!r}")
        result[key] = value
    return result


def _closed_object(value: object, keys: set[str], label: str) -> dict[str, Any]:
    if type(value) is not dict or set(cast(dict[object, object], value)) != keys:
        raise ProtocolError(f"{label} has missing or unexpected fields")
    return cast(dict[str, Any], value)


def _nonnegative_integer(value: object, label: str) -> int:
    if type(value) is not int or value < 0:
        raise ProtocolError(f"{label} must be a non-negative exact int")
    return value


def _decoded_constraints(obj: dict[str, Any], label: str) -> None:
    try:
        LookupProof(obj["lookup_proof"])
    except (TypeError, ValueError) as caught:
        raise ProtocolError(f"{label}.lookup_proof is outside its closed domain") from caught
    _nonnegative_integer(obj["name_max"], f"{label}.name_max")


def _decoded_identity(value: object, label: str) -> None:
    identity = _closed_object(value, {"st_dev", "st_ino"}, label)
    _nonnegative_integer(identity["st_dev"], f"{label}.st_dev")
    _nonnegative_integer(identity["st_ino"], f"{label}.st_ino")


def decode_approval_evidence(text: str) -> dict[str, Any]:
    """Decode the exact canonical recovery-approval document."""

    if type(text) is not str:
        raise ProtocolError("approval evidence must be an exact str")
    try:
        raw = json.loads(text, object_pairs_hook=_no_duplicate_keys)
    except ProtocolError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError) as caught:
        raise ProtocolError("approval evidence is not valid JSON") from caught
    document = _closed_object(raw, {"directories", "mount_id", "work_root"}, "approval evidence")
    _nonnegative_integer(document["mount_id"], "approval evidence.mount_id")
    raw_directories = document["directories"]
    if type(raw_directories) is not list:
        raise ProtocolError("approval evidence.directories must be an array")
    nodes: list[str] = []
    paths: list[str] = []
    for index, value in enumerate(raw_directories):
        label = f"approval evidence.directories[{index}]"
        item = _closed_object(
            value,
            {"node", "path", "identity", "lookup_proof", "name_max"},
            label,
        )
        node = item["node"]
        if type(node) is not str or not (
            node in {"project_root", "work_root"}
            or _TOPOLOGY_NODE.fullmatch(node)
            or node.startswith("persistent:")
        ):
            raise ProtocolError(f"{label}.node is outside its closed domain")
        path = item["path"]
        if node == "work_root":
            if path is not None:
                raise ProtocolError(f"{label}.path must be null for work_root")
        else:
            if type(path) is not str:
                raise ProtocolError(f"{label}.path must be an exact str")
            if node == "project_root":
                if path != "":
                    raise ProtocolError("project_root evidence must use the empty path")
            else:
                try:
                    require_rel_path("approval evidence directory path", path)
                except SpecValidationError as caught:
                    raise ProtocolError(str(caught)) from caught
            if node.startswith("persistent:") and node.removeprefix("persistent:") != path:
                raise ProtocolError(f"{label} node and path disagree")
            paths.append(path)
        if node == "work_root" and item["identity"] is not None:
            raise ProtocolError("work_root directory evidence cannot carry an identity")
        if node == "project_root" and item["identity"] is None:
            raise ProtocolError("project_root directory evidence requires an identity")
        if item["identity"] is not None:
            _decoded_identity(item["identity"], f"{label}.identity")
        _decoded_constraints(item, label)
        nodes.append(node)
    if nodes != sorted(nodes) or len(nodes) != len(set(nodes)):
        raise ProtocolError("approval evidence directory nodes must be sorted and unique")
    if len(paths) != len(set(paths)):
        raise ProtocolError("approval evidence directory paths must be unique")

    work_root = document["work_root"]
    if work_root is not None:
        work = _closed_object(
            work_root,
            {"identity", "lookup_proof", "name_max"},
            "approval evidence.work_root",
        )
        _decoded_identity(work["identity"], "approval evidence.work_root.identity")
        _decoded_constraints(work, "approval evidence.work_root")
    if nodes.count("project_root") != 1:
        raise ProtocolError("approval evidence must contain exactly one project_root")
    if (("work_root" in nodes) != (work_root is not None)):
        raise ProtocolError(
            "approval evidence work_root directory and physical facts disagree"
        )
    if json.dumps(
        document, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ) != text:
        raise ProtocolError("approval evidence is not canonical")
    return document


def _require_capabilities(
    required: frozenset[Capability], evidence: VolumeEvidence
) -> None:
    missing = required - evidence.supplied_capabilities
    if missing:
        raise CapabilityUnavailable(
            "the bound volume does not supply required capabilities: "
            + ", ".join(sorted(item.value for item in missing))
        )


def _approve(
    compiled: CompiledSpec,
    context: ProjectContext,
    recovery_evidence: dict[str, Any] | None,
) -> ProjectApprovedSpec:
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
    _require_capabilities(compiled.spec.required_capabilities(), evidence)

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
    if recovery_evidence is None:
        require_ancestors_legal(compiled, prefixes, resolved)
    require_resolved_surface_and_ordering(compiled, resolved)
    scratch = bind_scratch(compiled, context.txid, resolved)

    # Phase D: issue.
    approved = ProjectApprovedSpec(
        compiled=compiled,
        binding=binding,
        txid=context.txid,
        topology=resolved.topology,
        directories=resolved.directories,
        directory_paths=_proof_directory_paths(resolved),
        paths=resolved.paths,
        scratch=scratch,
        work_base=work_base,
        _construction_token=_TOKEN,
    )
    if (
        recovery_evidence is not None
        and decode_approval_evidence(encode_approval_evidence(approved))
        != recovery_evidence
    ):
        raise ProjectApprovalRefused(
            "the recovery approval does not match the durable approval evidence"
        )
    return approved


def approve_for_project(
    compiled: CompiledSpec, context: ProjectContext
) -> ProjectApprovedSpec:
    """Prove the rooted rules and issue the proof A5-A8 accept."""

    return _approve(compiled, context, None)


def _approve_for_recovery(
    compiled: CompiledSpec,
    context: ProjectContext,
    *,
    evidence: dict[str, Any],
) -> ProjectApprovedSpec:
    """Re-issue a proof after exact comparison with frozen approval evidence."""

    if type(evidence) is not dict:
        raise ProtocolError("recovery evidence must be a decoded exact dict")
    return _approve(compiled, context, evidence)
