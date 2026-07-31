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
