"""The held, re-validated descriptor table (design §5).

`RecoveryTopology.parents` is a rooted tree, so the table is a walk of it: each directory
node is opened from its parent's held descriptor with ONE `open_child_directory` call,
which is already RESOLVE_BENEATH | RESOLVE_NO_SYMLINKS | RESOLVE_NO_XDEV. No
multi-component path is ever assembled -- passing one to a syscall would reopen the
check/use race, because the kernel re-resolves intermediate components at the syscall.

The tree gives structure, not spelling: `TopologyDirectory(node_id)` carries no name, so
the walk builds its own node-to-path table, `_directory_paths`, by climbing
`approved.topology.parents` from each declared path up to a node it has already
recorded. A5b's `_parent_paths` looks similar but is scoped to admission's own job
(design §6.4): it maps only declared paths and their direct parent, not every
undeclared intermediate directory `approved.directories` also names. Borrowing it here
left the walk unable to name an ordinary undeclared ancestor directory at all.

Nothing here classifies. Only a PLANNED directory produces a `WalkStop`, recording the
ObservedEntry actually found there, and capture adjudicates that against the timeline's
first declared state. No errno is ever mapped to a kind: ENOTDIR does not distinguish a
regular file from a socket, FIFO, or device node.

An approved-EXISTING directory that no longer opens is drift, not a stop -- a
`TopologyDirectory` has no declared state for §8 to rule against -- so it refuses through
the narrow errno translation, and an undefined errno such as EIO propagates as itself.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Self

from atoms.coordinator.lease import Lease
from atoms.core.errors import PreconditionRefused, ProtocolError
from atoms.core.recovery.model import ObservedEntry
from atoms.core.recovery.snapshot import PersistentNode, ProjectRoot, TopologyNode, WorkRoot
from atoms.fs.approval import ProjectApprovedSpec
from atoms.fs.lookup import read_lookup_constraints
from atoms.fs.observe import Observation, translated_lookup
from atoms.fs.resolve import FilesystemIdentity, filesystem_type_of
from atoms.fs.topology import ApprovedExistingDirectory, ApprovedPlannedDirectory
from atoms.fs.volume import read_mount_id
from atoms.store.workspace import Workspace


@dataclass(frozen=True, slots=True)
class WalkStop:
    """Where the walk stopped, and what was actually there.

    `observed` is an `ObservedAbsent` when nothing occupies the name, and the real
    observed entry otherwise. Neither is adjudicated here: capture verifies both against
    the timeline's first declared state (design §8).
    """

    node: TopologyNode
    path: str
    parent_fd: int
    component: str
    observed: ObservedEntry


class DescriptorTable:
    """A live resource. Borrows the roots; owns only what it opened.

    It outlives capture: §6 requires the engine to hold a descriptor to the project root
    for the transaction's lifetime, and §9.5 hands each published directory's descriptor
    down to its descendants. A table that died at capture's return would force A7 to
    re-resolve, reopening the race the whole section exists to close.
    """

    __slots__ = ("_backend", "_closed", "_fds", "_owned", "_unreachable", "stops")

    def __init__(
        self,
        *,
        backend,
        fds: dict[TopologyNode, int],
        owned: tuple[int, ...],
        stops: tuple[WalkStop, ...],
        unreachable: frozenset[TopologyNode],
    ) -> None:
        self._backend = backend
        self._fds = fds
        self._owned = owned
        self._unreachable = unreachable
        self.stops = stops
        self._closed = False

    def fd_for(self, node: TopologyNode) -> int:
        if self._closed:
            raise ProtocolError("this descriptor table is closed")
        return self._fds[node]

    def is_unreachable(self, node: TopologyNode) -> bool:
        """Proved to lie beneath a stop -- distinct from merely absent from the table."""
        if self._closed:
            raise ProtocolError("this descriptor table is closed")
        return node in self._unreachable

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for fd in self._owned:
            self._backend.close_fd(fd)
        self._fds.clear()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _build_descriptor_table(
    lease: Lease,
    approved: ProjectApprovedSpec,
    workspace: Workspace,
    observation: Observation,
) -> DescriptorTable:
    """Package-private: reached only through `capture_initial_surface`.

    Kept private deliberately. `test_no_unregistered_public_function_accepts_the_proof`
    asserts that every public coordinator function annotating a ProjectApprovedSpec is a
    registered transaction-stage entry point, and this is a step inside one, not an
    entry of its own.
    """
    binding = lease._binding
    backend = binding.backend
    filesystem_type = filesystem_type_of(binding)
    expected_mount = binding.evidence.mount_id
    paths = _directory_paths(approved)
    # One map, both kinds. Every descriptor-bearing node has an approved record in
    # `approved.directories`, including WorkRoot: A4b builds the topology with
    # ApprovedPlannedDirectory(WorkRoot(), inherited_constraints(work_base.constraints,
    # filesystem_type)) (`approval.py:155`, `topology.py:180`). `approved.work_base`
    # describes metadata_root/work -- the PARENT of work/<txid>, which is what
    # `workspace.work_fd` names -- so it is the wrong baseline to compare against here.
    directories = {entry.node: entry for entry in approved.directories}
    planned = {
        node
        for node, entry in directories.items()
        if type(entry) is ApprovedPlannedDirectory
    }

    fds: dict[TopologyNode, int] = {}
    owned: list[int] = []
    stops: list[WalkStop] = []
    stopped_nodes: set[TopologyNode] = set()

    def validate(fd: int, node: TopologyNode) -> None:
        """Ledger #19: identity, constraints, and mount, against the approved baseline.

        DirectoryConstraints carries lookup_proof and name_max only, so mount membership
        is a separate read -- a constraints comparison alone would pass a directory
        replaced by a bind mount.
        """
        constraints = read_lookup_constraints(fd, filesystem_type)
        mount = read_mount_id(fd)
        if mount != expected_mount:
            raise PreconditionRefused(
                f"{node!r} is on mount {mount}, not the bound volume's {expected_mount}"
            )
        baseline = directories.get(node)
        if baseline is None:
            raise ProtocolError(
                f"{node!r} bears a descriptor but has no record in the proof's approved "
                "directories; the topology and the approval disagree"
            )
        if constraints != baseline.constraints:
            raise PreconditionRefused(
                f"{node!r} has constraints {constraints}, not the approved "
                f"{baseline.constraints}"
            )
        # A planned directory has no approved identity -- it did not exist at approval,
        # so there is nothing to compare an inode against. Constraints and mount are the
        # whole of its baseline.
        if type(baseline) is ApprovedExistingDirectory:
            info = os.fstat(fd)
            actual = FilesystemIdentity(device=info.st_dev, inode=info.st_ino)
            if actual != baseline.identity:
                raise PreconditionRefused(
                    f"{node!r} has identity {actual}, not the approved "
                    f"{baseline.identity}; approval is not reapproved here"
                )

    try:
        # Root 1: the project root, borrowed. Retention is not discharge -- lookup_proof
        # and name_max are mutable directory properties, so it is re-validated too.
        validate(binding.project_root_fd, ProjectRoot())
        fds[ProjectRoot()] = binding.project_root_fd

        # Root 2: the work root, borrowed, and present only when the topology has one.
        # The logical WorkRoot -> ProjectRoot edge is NOT physically traversed: the work
        # root lives under metadata_root, not beneath the project root.
        if approved.work_base is not None:
            validate(workspace.work_fd, WorkRoot())
            fds[WorkRoot()] = workspace.work_fd

        for node in _walk_order(approved, paths):
            parent = _parent_of(approved, node)
            parent_fd = fds.get(parent)
            if parent_fd is None:
                # A parent may lack a descriptor for exactly one reason: it stopped.
                # `_walk_order` is shallowest-first, so any other absence is a walk-order
                # defect, and calling it a stop would manufacture an absence inference
                # out of a bug -- §8's whole basis is a *verified* blocker.
                if parent not in stopped_nodes:
                    raise ProtocolError(
                        f"{node!r} was reached before its parent {parent!r} was opened "
                        "or stopped; the walk is not visiting parents first"
                    )
                stopped_nodes.add(node)
                continue
            component = _component(paths, parent, node)
            if node in planned:
                # Looked up, not assumed. An occupied planned name must reach §8.2.
                observed = observation.observe(
                    parent_fd, component, modeled=_modeled_children(paths, node)
                )
                stops.append(
                    WalkStop(
                        node=node,
                        path=paths[node],
                        parent_fd=parent_fd,
                        component=component,
                        observed=observed,
                    )
                )
                # BOTH outcomes stop the walk. Absent, nothing is below it; occupied by
                # a file or symlink, nothing is below it either -- neither holds
                # directory entries, which is the whole basis of §8.2's inference. A
                # planned node left unstopped would leave its descendants neither
                # resolvable nor unreachable, and capture would raise ProtocolError on
                # the very case §8.2 exists to accept.
                stopped_nodes.add(node)
                continue
            # An approved-EXISTING directory that no longer opens is drift, not a stop.
            # There is no declared state for a TopologyDirectory to be adjudicated
            # against, so §8's branches could never rule on it; it refuses here.
            with translated_lookup(f"opening {component!r} for {node!r}"):
                fd = backend.open_child_directory(parent_fd, component)
            owned.append(fd)
            validate(fd, node)
            fds[node] = fd
    except BaseException:
        for fd in owned:
            backend.close_fd(fd)
        raise

    unreachable = _closure(approved, stopped_nodes)
    return DescriptorTable(
        backend=backend,
        fds=fds,
        owned=tuple(owned),
        stops=tuple(stops),
        unreachable=unreachable,
    )


def _modeled_children(paths: dict[TopologyNode, str], node: TopologyNode) -> frozenset[str]:
    """Every declared name directly beneath `node`, for occupancy evidence."""
    prefix = paths[node]
    base = f"{prefix}/" if prefix else ""
    return frozenset(
        path[len(base) :]
        for path in paths.values()
        if path.startswith(base) and path != prefix and "/" not in path[len(base) :]
    )


def _directory_paths(approved: ProjectApprovedSpec) -> dict[TopologyNode, str]:
    """Every directory node's path, total over `approved.directories` -- project space
    only; `WorkRoot` is excluded by construction, never reached by this climb (its only
    edge is `TopologyParent(WorkRoot(), ProjectRoot())`, and no declared path is ever
    parented by it).

    A5b's `_parent_paths` looks like this table but is not one: it maps only declared
    paths and their direct parent (design §6.4's scope), so an intermediate directory
    that is nobody's own declared path -- an ordinary undeclared ancestor -- has no
    entry there. `approved.directories` names every directory prefix regardless, so
    this climbs `approved.topology.parents` from each declared path's parent up to a
    node already recorded, filling in every undeclared ancestor along the way. The
    climb always terminates: the tree is rooted at `ProjectRoot()`, seeded below, and
    every non-root node has exactly one parent edge (`RecoveryTopology`'s own
    invariant).
    """
    parents = {edge.node: edge.parent for edge in approved.topology.parents}
    paths: dict[TopologyNode, str] = {ProjectRoot(): ""}
    for entry in approved.paths:
        paths[PersistentNode(entry.path)] = entry.path
        node, prefix = entry.parent_node, entry.path.rpartition("/")[0]
        while node not in paths:
            paths[node] = prefix
            node = parents[node]
            prefix = prefix.rpartition("/")[0]
    return paths


def _walk_order(
    approved: ProjectApprovedSpec, paths: dict[TopologyNode, str]
) -> tuple[TopologyNode, ...]:
    """Directory nodes, shallowest first, so every parent is open before its child."""
    directories = [
        entry.node
        for entry in approved.directories
        if entry.node not in (ProjectRoot(), WorkRoot())
    ]
    missing = [node for node in directories if node not in paths]
    if missing:
        raise ProtocolError(
            f"{missing[0]!r} has no path in the walk's directory table; the topology "
            "and the approved directories disagree"
        )
    return tuple(sorted(directories, key=lambda node: paths[node].count("/")))


def _parent_of(approved: ProjectApprovedSpec, node: TopologyNode) -> TopologyNode:
    for edge in approved.topology.parents:
        if edge.node == node:
            return edge.parent
    raise ProtocolError(f"{node!r} has no parent edge in the approved topology")


def _closure(
    approved: ProjectApprovedSpec, stopped: set[TopologyNode]
) -> frozenset[TopologyNode]:
    """Every node at or beneath a stop. The topology is a tree, so this terminates."""
    unreachable = set(stopped)
    changed = True
    while changed:
        changed = False
        for edge in approved.topology.parents:
            if edge.parent in unreachable and edge.node not in unreachable:
                unreachable.add(edge.node)
                changed = True
    return frozenset(unreachable)


def _component(
    paths: dict[TopologyNode, str], parent: TopologyNode, node: TopologyNode
) -> str:
    child_path, parent_path = paths[node], paths[parent]
    remainder = child_path[len(parent_path) :].lstrip("/")
    if not remainder or "/" in remainder:
        raise ProtocolError(
            f"{child_path!r} is not one component below {parent_path!r}; the walk "
            "would have to assemble a multi-component path"
        )
    return remainder
