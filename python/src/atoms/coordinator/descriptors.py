"""The held, re-validated descriptor table (design §5).

`RecoveryTopology.parents` is a rooted tree, so the table is a walk of it: each directory
node is opened from its parent's held descriptor with ONE `open_child_directory` call,
which is already RESOLVE_BENEATH | RESOLVE_NO_SYMLINKS | RESOLVE_NO_XDEV. No
multi-component path is ever assembled -- passing one to a syscall would reopen the
check/use race, because the kernel re-resolves intermediate components at the syscall.

The walk is not one pass. `_build_descriptor_table` is the initial descent, and it stops
at every planned directory -- including, by inheritance, one whose parent is itself
planned, which it cannot even look up. Two resumption points pick the walk back up once
such a directory gains a descriptor, both through `_register_planned_children`: recovery,
via `_resume_descent`, and forward execution, where A7's `_apply_effect` adopts the
descriptor a `CreateDirectory` effect retained (§9.5's descendant handoff). A planned
directory's `WalkStop` is therefore born either in the descent or at the moment its
parent becomes reachable, never anywhere else.

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

import errno
import os
from dataclasses import dataclass
from typing import Self

from atoms.coordinator.lease import Lease
from atoms.core.errors import PreconditionRefused, ProtocolError
from atoms.core.recovery.model import ObservedDirectory, ObservedEntry
from atoms.core.recovery.snapshot import PersistentNode, ProjectRoot, TopologyNode, WorkRoot
from atoms.fs.approval import ProjectApprovedSpec
from atoms.fs.lock import close_all
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

    __slots__ = ("_backend", "_closed", "_fds", "_owned", "_stops", "_unreachable")

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
        self._stops = stops
        self._closed = False

    @property
    def stops(self) -> tuple[WalkStop, ...]:
        if self._closed:
            raise ProtocolError("this descriptor table is closed")
        return self._stops

    def fd_for(self, node: TopologyNode) -> int:
        if self._closed:
            raise ProtocolError("this descriptor table is closed")
        try:
            return self._fds[node]
        except KeyError as caught:
            raise ProtocolError(f"{node!r} has no descriptor in this table") from caught

    def is_unreachable(self, node: TopologyNode) -> bool:
        """Proved to lie beneath a stop -- distinct from merely absent from the table."""
        if self._closed:
            raise ProtocolError("this descriptor table is closed")
        return node in self._unreachable

    def adopt(self, node: TopologyNode, fd: int) -> None:
        if self._closed:
            raise ProtocolError("this descriptor table is closed")
        if node in self._fds:
            raise ProtocolError(f"{node!r} already has a descriptor in this table")
        if not any(stop.node == node for stop in self._stops):
            raise ProtocolError(f"{node!r} is not a stopped planned directory")
        self._fds[node] = fd
        self._owned = (*self._owned, fd)
        self._stops = tuple(stop for stop in self._stops if stop.node != node)
        self._unreachable = self._unreachable - {node}

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            close_all(self._backend, self._owned)
        finally:
            self._fds.clear()
            self._owned = ()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _validate_open_directory(
    approved: ProjectApprovedSpec, fd: int, node: TopologyNode
) -> None:
    binding = approved.binding
    constraints = read_lookup_constraints(fd, filesystem_type_of(binding))
    mount = read_mount_id(fd)
    expected_mount = binding.evidence.mount_id
    if mount != expected_mount:
        raise PreconditionRefused(
            f"{node!r} is on mount {mount}, not the bound volume's {expected_mount}"
        )
    baseline = next((entry for entry in approved.directories if entry.node == node), None)
    if baseline is None:
        raise ProtocolError(
            f"{node!r} bears a descriptor but has no approved directory record"
        )
    if constraints != baseline.constraints:
        raise PreconditionRefused(
            f"{node!r} has constraints {constraints}, not {baseline.constraints}"
        )
    if type(baseline) is ApprovedExistingDirectory:
        info = os.fstat(fd)
        actual = FilesystemIdentity(device=info.st_dev, inode=info.st_ino)
        if actual != baseline.identity:
            raise PreconditionRefused(
                f"{node!r} has identity {actual}, not {baseline.identity}"
            )


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

    try:
        # Root 1: the project root, borrowed. Retention is not discharge -- lookup_proof
        # and name_max are mutable directory properties, so it is re-validated too.
        _validate_open_directory(approved, binding.project_root_fd, ProjectRoot())
        fds[ProjectRoot()] = binding.project_root_fd

        # Root 2: the work root, borrowed, and present only when the topology has one.
        # The logical WorkRoot -> ProjectRoot edge is NOT physically traversed: the work
        # root lives under metadata_root, not beneath the project root.
        if approved.work_base is not None:
            _validate_open_directory(approved, workspace.work_fd, WorkRoot())
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
            try:
                with translated_lookup(f"opening {component!r} for {node!r}"):
                    fd = backend.open_child_directory(parent_fd, component)
            except OSError as caught:
                if caught.errno == errno.EACCES:
                    raise PreconditionRefused(
                        f"access was denied opening {component!r} for {node!r}"
                    ) from caught
                raise
            owned.append(fd)
            _validate_open_directory(approved, fd, node)
            fds[node] = fd
    except BaseException:
        close_all(backend, owned)
        raise

    unreachable = _closure(approved, stopped_nodes)
    return DescriptorTable(
        backend=backend,
        fds=fds,
        owned=tuple(owned),
        stops=tuple(stops),
        unreachable=unreachable,
    )


def _register_planned_children(
    table: DescriptorTable,
    observation: Observation,
    approved: ProjectApprovedSpec,
    node: TopologyNode,
) -> tuple[TopologyNode, ...]:
    """Record every planned child of a NOW-ADOPTED directory as a fresh walk stop.

    The initial descent cannot do this. A planned directory whose parent is itself
    planned has no parent descriptor to be looked up from -- `_build_descriptor_table`
    marks it stopped-by-inheritance and moves on -- so nothing ever recorded a `WalkStop`
    for it, and `DescriptorTable.adopt` refuses any node that is not a stop. The stop can
    therefore only be born the moment its parent gains a descriptor, which is exactly
    what §9.5's "handed to any descendant effect as that descendant's parent descriptor"
    describes. Both directions use this: recovery, through `_resume_descent`, and forward
    execution, right after `CreateDirectory` publishes and its descriptor is adopted.

    Looked up, not assumed, on both routes -- the child's name is observed through the
    parent's own descriptor rather than declared free, because a freshly published
    directory is empty only until someone else writes into it.

    PRECONDITION: call exactly once per `adopt`, and only for a node just adopted. It
    appends unconditionally and checks no existing stop, so a second call would give one
    child two stops -- `adopt` then removes only one of them, and the survivor would
    outlive its own directory.

    `_unreachable` is recomputed rather than adjusted: every node without a descriptor is
    at or beneath a current stop, so the closure over `table._stops` IS the set. `adopt`
    subtracts only the adopted node, which would leave that node's descendants marked
    unreachable after their ancestor became reachable. The recomputation is the last
    statement, so that equality is a POST-CONDITION OF THE SUCCESS PATH ONLY: an
    `observe` that raises midway leaves the table adopt-mutated with some children
    already appended and `_unreachable` still describing the pre-adopt stops. Nothing
    repairs it, because nothing needs to -- every caller propagates, the table is torn
    down or handed to a rollback that re-observes every stop before reading one.
    """
    paths = _directory_paths(approved)
    planned = {
        entry.node
        for entry in approved.directories
        if type(entry) is ApprovedPlannedDirectory
    }
    parent_fd = table.fd_for(node)
    registered: list[TopologyNode] = []
    for child in _walk_order(approved, paths):
        if child not in planned or _parent_of(approved, child) != node:
            continue
        component = _component(paths, node, child)
        observed = observation.observe(
            parent_fd, component, modeled=_modeled_children(paths, child)
        )
        table._stops = (
            *table._stops,
            WalkStop(child, paths[child], parent_fd, component, observed),
        )
        registered.append(child)
    table._unreachable = _closure(approved, {stop.node for stop in table._stops})
    return tuple(registered)


def _resume_descent(
    table: DescriptorTable,
    backend,
    observation: Observation,
    approved: ProjectApprovedSpec,
    node: TopologyNode,
) -> None:
    """Resume a recovery walk through planned directories already created."""

    pending = [node]
    while pending:
        current = pending.pop(0)
        stop = next((item for item in table._stops if item.node == current), None)
        if stop is None or type(stop.observed) is not ObservedDirectory:
            continue
        try:
            with translated_lookup(f"resuming descent through {stop.path!r}"):
                fd = backend.open_child_directory(stop.parent_fd, stop.component)
        except OSError as caught:
            if caught.errno == errno.EACCES:
                continue
            raise
        try:
            _validate_open_directory(approved, fd, current)
            table.adopt(current, fd)
        except BaseException:
            backend.close_fd(fd)
            raise
        pending.extend(
            _register_planned_children(table, observation, approved, current)
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
