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
