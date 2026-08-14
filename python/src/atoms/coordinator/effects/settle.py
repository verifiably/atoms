"""A3-authorized recovery filesystem mutations."""

from __future__ import annotations

import os
from typing import cast

from atoms.coordinator.admission import _require_admitted
from atoms.coordinator.descriptors import DescriptorTable, _directory_paths
from atoms.coordinator.effects.common import EffectMismatch, run_determinate
from atoms.coordinator.effects.sites import (
    CreateFileSite,
    DeleteSite,
    MkdirSite,
    MoveSite,
    ReplaceSite,
    _site_for,
)
from atoms.coordinator.lease import Lease
from atoms.core.effects import (
    CreateDirectory,
    CreateFileNoClobber,
    DeletePath,
    Effect,
    MoveNoClobber,
    ReplaceFile,
)
from atoms.core.errors import ProtocolError
from atoms.core.recovery.authorization import _authorization_projection
from atoms.core.recovery.model import (
    ObservedAbsent,
    ObservedDirectory,
    ObservedFile,
    ObservedSymlink,
    PersistentObservation,
    ScratchObservation,
)
from atoms.core.recovery.plan import (
    AuthorizedStep,
    EffectVariant,
    JointObservation,
    ParentOccupancy,
    RemoveScratch,
    SettlementKind,
    TransformEffectTuple,
)
from atoms.core.recovery.snapshot import (
    PersistentNode,
    ScratchNode,
    TopologyDirectory,
    TopologyNode,
)
from atoms.fs.approval import ProjectApprovedSpec
from atoms.fs.audit import AuditedBackend, Provenance, RootKind
from atoms.fs.observe import Observation
from atoms.store.blobs import BLOBS_PARENT, digest_to_leaf

_CELLS = {
    (EffectVariant.REPLACE_FILE, SettlementKind.RESTORE_PRE): "replace_restore",
    (
        EffectVariant.CREATE_FILE_NO_CLOBBER,
        SettlementKind.REMOVE_ATTRIBUTABLE_CREATION,
    ): "create_remove",
    (EffectVariant.DELETE_PATH, SettlementKind.RESTORE_PRE): "delete_restore",
    (EffectVariant.MOVE_NO_CLOBBER, SettlementKind.RESTORE_PRE): "move_restore",
    (
        EffectVariant.MOVE_NO_CLOBBER,
        SettlementKind.REPAIR_INTERMEDIATE,
    ): "move_repair",
    (
        EffectVariant.CREATE_DIRECTORY,
        SettlementKind.REMOVE_ATTRIBUTABLE_CREATION,
    ): "mkdir_remove",
}


def apply_transform(
    lease: Lease,
    approved: ProjectApprovedSpec,
    table: DescriptorTable,
    authorized: AuthorizedStep,
) -> None:
    _require_admitted(lease, approved)
    if type(authorized) is not AuthorizedStep or type(authorized.step) is not TransformEffectTuple:
        raise ProtocolError("apply_transform requires an exact AuthorizedStep for a transform")
    step = cast(TransformEffectTuple, authorized.step)
    try:
        cell = _CELLS[(step.variant, step.settlement)]
    except KeyError as caught:
        raise ProtocolError(
            f"A3 does not emit {step.variant.value} × {step.settlement.value}"
        ) from caught
    effect = _effect(approved, step.effect_id)
    backend = cast(AuditedBackend, lease._binding.backend)
    site = _site_for(approved, table, effect)

    if cell == "replace_restore" and type(effect) is ReplaceFile and type(site) is ReplaceSite:
        run_determinate(
            "exchange",
            str(effect.path),
            lambda: backend.exchange(site.parent_fd, site.live_leaf, site.staging_leaf),
        )
        parents = (site.parent_fd,)
    elif cell == "create_remove" and type(effect) is CreateFileNoClobber and type(site) is CreateFileSite:
        run_determinate(
            "transfer_noclobber",
            str(effect.path),
            lambda: backend.transfer_noclobber(
                site.parent_fd, site.live_leaf, site.parent_fd, site.staging_leaf
            ),
        )
        parents = (site.parent_fd,)
    elif cell == "delete_restore" and type(effect) is DeletePath and type(site) is DeleteSite:
        run_determinate(
            "transfer_noclobber",
            str(effect.path),
            lambda: backend.transfer_noclobber(
                site.parent_fd, site.tombstone_leaf, site.parent_fd, site.live_leaf
            ),
        )
        parents = (site.parent_fd,)
    elif cell == "move_restore" and type(effect) is MoveNoClobber and type(site) is MoveSite:
        run_determinate(
            "transfer_noclobber",
            str(effect.source),
            lambda: backend.transfer_noclobber(
                site.destination_fd,
                site.destination_leaf,
                site.source_fd,
                site.source_leaf,
            ),
        )
        parents = (site.source_fd, site.destination_fd)
    elif cell == "move_repair" and type(effect) is MoveNoClobber and type(site) is MoveSite:
        source, destination = step.expected_before.persistent
        anchor = step.expected_before.scratch[0]
        if (
            type(source.entry) is ObservedAbsent
            and type(destination.entry) is ObservedAbsent
            and type(anchor.entry) is ObservedFile
        ):
            run_determinate(
                "link_anchor",
                str(effect.source),
                lambda: backend.link_anchor(
                    site.source_fd, site.anchor_leaf, site.source_fd, site.source_leaf
                ),
            )
            parents = (site.source_fd,)
        elif type(destination.entry) is ObservedFile and type(anchor.entry) is ObservedFile:
            run_determinate(
                "unlink_child",
                str(effect.destination),
                lambda: backend.unlink_child(site.destination_fd, site.destination_leaf),
            )
            parents = (site.destination_fd,)
        else:
            raise ProtocolError("move repair transform has an unrecognized A3 tuple")
    elif cell == "mkdir_remove" and type(effect) is CreateDirectory and type(site) is MkdirSite:
        run_determinate(
            "rmdir_child",
            str(effect.path),
            lambda: backend.rmdir_child(site.parent_fd, site.live_leaf),
        )
        parents = (site.parent_fd,)
    else:
        raise ProtocolError("transform variant does not match the durable effect")

    _require_result(lease, approved, table, step.result_after)
    for parent_fd in parents:
        backend.flush_directory(parent_fd)


def apply_remove_scratch(
    lease: Lease,
    approved: ProjectApprovedSpec,
    table: DescriptorTable,
    authorized: AuthorizedStep,
) -> None:
    _require_admitted(lease, approved)
    if type(authorized) is not AuthorizedStep or type(authorized.step) is not RemoveScratch:
        raise ProtocolError(
            "apply_remove_scratch requires an exact AuthorizedStep for RemoveScratch"
        )
    step = cast(RemoveScratch, authorized.step)
    row = next(
        (
            item
            for item in approved.scratch
            if item.effect_id == step.effect_id and item.role is step.role
        ),
        None,
    )
    if row is None:
        raise ProtocolError("the approved proof has no row for this scratch slot")
    parent_fd = table.fd_for(row.parent_node)
    expected = step.expected_before.scratch[0].entry
    backend = cast(AuditedBackend, lease._binding.backend)
    if type(expected) is ObservedDirectory:
        run_determinate(
            "rmdir_child",
            row.leaf,
            lambda: backend.rmdir_child(parent_fd, row.leaf),
        )
    elif type(expected) in {ObservedFile, ObservedSymlink}:
        run_determinate(
            "unlink_child",
            row.leaf,
            lambda: backend.unlink_child(parent_fd, row.leaf),
        )
    else:
        raise ProtocolError("RemoveScratch expected a present file, symlink, or directory")
    _require_result(lease, approved, table, step.result_after)
    backend.flush_directory(parent_fd)


def _effect(approved: ProjectApprovedSpec, effect_id: str) -> Effect:
    try:
        return next(
            effect
            for effect in approved.compiled.spec.effects
            if effect.effect_id == effect_id
        )
    except StopIteration as caught:
        raise ProtocolError(f"the durable spec has no effect {effect_id!r}") from caught


def _require_result(
    lease: Lease,
    approved: ProjectApprovedSpec,
    table: DescriptorTable,
    expected: JointObservation,
) -> None:
    observed = _observe_joint(lease, approved, table, expected)
    if _authorization_projection(observed) != _authorization_projection(expected):
        raise EffectMismatch("verify_settlement", "covered tuple")


def _observe_joint(
    lease: Lease,
    approved: ProjectApprovedSpec,
    table: DescriptorTable,
    coverage: JointObservation,
) -> JointObservation:
    backend = cast(AuditedBackend, lease._binding.backend)
    paths = {row.path: row for row in approved.paths}
    scratch = {(row.effect_id, row.role): row for row in approved.scratch}
    children = _children(approved)
    with Observation(backend) as observation:
        persistent = tuple(
            PersistentObservation(
                item.path,
                observation.observe(
                    table.fd_for(paths[item.path].parent_node),
                    paths[item.path].leaf,
                    modeled=frozenset(name for _, name in children.get(PersistentNode(item.path), ())),
                ),
            )
            for item in coverage.persistent
        )
        scratch_observations: list[ScratchObservation] = []
        for item in coverage.scratch:
            row = scratch[(item.effect_id, item.role)]
            entry = observation.observe(
                table.fd_for(row.parent_node),
                row.leaf,
                modeled=frozenset(
                    name
                    for _, name in children.get(
                        ScratchNode(item.effect_id, item.role), ()
                    )
                ),
            )
            relation = None
            if item.file_build_relation is not None and type(entry) is ObservedFile:
                effect = _effect(approved, item.effect_id)
                state = cast(ReplaceFile | CreateFileNoClobber, effect).post
                blob_fd = lease._store.open_blob(state.content_hash)
                backend.register(
                    blob_fd,
                    Provenance(
                        RootKind.METADATA,
                        f"{BLOBS_PARENT}/{digest_to_leaf(state.content_hash)}",
                    ),
                )
                try:
                    relation = observation.build_relation(
                        observation.pinned_descriptor(entry.identity), blob_fd
                    )
                finally:
                    backend.close_fd(blob_fd)
            scratch_observations.append(
                ScratchObservation(item.effect_id, item.role, entry, relation)
            )
        occupancy = tuple(
            _observe_occupancy(table, children, item) for item in coverage.parent_occupancy
        )
    return JointObservation(persistent, tuple(scratch_observations), occupancy)


def _children(
    approved: ProjectApprovedSpec,
) -> dict[TopologyNode, tuple[tuple[TopologyNode, str], ...]]:
    paths = {row.path: row for row in approved.paths}
    scratch = {(row.effect_id, row.role): row for row in approved.scratch}
    directory_paths = _directory_paths(approved)
    result: dict[TopologyNode, list[tuple[TopologyNode, str]]] = {}
    for edge in approved.topology.parents:
        child = edge.node
        if type(child) is PersistentNode:
            name = paths[child.path].leaf
        elif type(child) is ScratchNode:
            name = scratch[(child.effect_id, child.role)].leaf
        elif type(child) is TopologyDirectory:
            name = directory_paths[child].rsplit("/", 1)[-1]
        else:
            continue
        result.setdefault(edge.parent, []).append((child, name))
    return {parent: tuple(rows) for parent, rows in result.items()}


def _observe_occupancy(
    table: DescriptorTable,
    children: dict[TopologyNode, tuple[tuple[TopologyNode, str], ...]],
    expected: ParentOccupancy,
) -> ParentOccupancy:
    names = set(os.listdir(table.fd_for(expected.parent)))
    modeled = children.get(expected.parent, ())
    return ParentOccupancy(
        parent=expected.parent,
        present_children=tuple(node for node, name in modeled if name in names),
        has_unmodeled_child=any(name not in {leaf for _, leaf in modeled} for name in names),
    )
