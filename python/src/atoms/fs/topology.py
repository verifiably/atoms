"""The approved value types and the resolved topology (A4b-2 design §7, §8).

Pure. No filesystem access, no descriptor, no syscall. Every name comparison routes
through lookup_equivalence_key rather than comparing raw components, so widening the
lookup floor changes that function and nothing here.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from atoms.core.compiler import CompiledSpec
from atoms.core.effects import CreateDirectory, MoveNoClobber, occurrences
from atoms.core.errors import PreconditionRefused, ProjectApprovalRefused, ProtocolError
from atoms.core.fingerprint import AbsentState, DirectoryState
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
from atoms.fs.resolve import (
    DirectoryFacts,
    EntryKind,
    FilesystemIdentity,
    Frontier,
    PresentFrontier,
    ResolvedPrefix,
)

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

    parents = {edge.parent for edge in ordered}
    directories = _directory_entries(facts, keys, nodes, parents, created)
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
    observations: dict[str, DirectoryFacts | Frontier] = {}
    for path in sorted(prefixes):
        prefix = prefixes[path]
        _record_observation(observations, ROOT_PREFIX, prefix.root)
        facts.setdefault(ROOT_PREFIX, (prefix.root.identity, prefix.root.constraints))
        components = path.split("/")
        for index, hop in enumerate(prefix.hops):
            candidate = "/".join(components[: index + 1])
            _record_observation(observations, candidate, hop.facts)
            facts.setdefault(candidate, (
                hop.facts.identity,
                hop.facts.constraints,
            ))
        frontier = "/".join(components[: len(prefix.hops) + 1])
        _record_observation(observations, frontier, prefix.frontier)

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
        parent = "/".join(candidate.split("/")[:-1])
        _, parent_constraints = facts[parent]
        inherited = inherited_constraints(parent_constraints, filesystem_type)
        if candidate in created:
            identity = facts[candidate][0] if candidate in facts else None
            facts[candidate] = (identity, inherited)
        elif candidate not in facts:
            facts[candidate] = (None, inherited)
    return facts


def _record_observation(
    observations: dict[str, DirectoryFacts | Frontier],
    prefix: str,
    observed: DirectoryFacts | Frontier,
) -> None:
    previous = observations.get(prefix)
    if previous is None:
        observations[prefix] = observed
        return
    if previous == observed:
        return
    if isinstance(previous, DirectoryFacts) and _is_same_directory(observed, previous):
        return
    if isinstance(observed, DirectoryFacts) and _is_same_directory(previous, observed):
        observations[prefix] = observed
        return
    raise PreconditionRefused(
        f"prefix {prefix!r} changed between declared-path resolutions: "
        f"{previous!r} then {observed!r}; the engine will not assemble one topology "
        "from different filesystem moments"
    )


def _is_same_directory(observed: DirectoryFacts | Frontier, facts: DirectoryFacts) -> bool:
    return (
        isinstance(observed, PresentFrontier)
        and observed.kind is EntryKind.DIRECTORY
        and observed.identity == facts.identity
    )


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
    created: frozenset[str] | set[str],
) -> tuple[ApprovedDirectory, ...]:
    created_constraints = {
        nodes[keys[prefix]]: facts[prefix][1] for prefix in created
    }
    entries: dict[TopologyNode, ApprovedDirectory] = {}
    for prefix in sorted(facts, key=_depth_then_name):
        identity, constraints = facts[prefix]
        node = nodes[keys[prefix]]
        if node not in parents:
            continue
        if node in created_constraints:
            entries[node] = ApprovedPlannedDirectory(
                node=node, constraints=created_constraints[node]
            )
        elif identity is None:
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
