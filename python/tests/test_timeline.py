import pytest

from atoms.core.effects import (
    CreateDirectory,
    CreateFileNoClobber,
    DeletePath,
    MoveNoClobber,
    ReplaceFile,
)
from atoms.core.errors import SpecValidationError
from atoms.core.fingerprint import ABSENT, DirectoryState, FileState, SymlinkState
from atoms.core.timeline import PathTimeline, TimelineOccurrence, build_timelines

F = FileState(content_hash="sha256:" + "1" * 64, mode=0o644, byte_len=3)
G = FileState(content_hash="sha256:" + "2" * 64, mode=0o644, byte_len=5)
D = DirectoryState(mode=0o755)
L = SymlinkState(target="../t", mode=0o777)


def test_single_effect_yields_a_one_occurrence_timeline():
    timelines = build_timelines([ReplaceFile(effect_id="e1", path="a", pre=F, post=G)])
    assert timelines == (
        PathTimeline(
            path="a",
            occurrences=(
                TimelineOccurrence(effect_id="e1", effect_index=0, role="target", pre=F, post=G),
            ),
        ),
    )


def test_timelines_are_sorted_by_path():
    timelines = build_timelines([
        CreateFileNoClobber(effect_id="e1", path="z", post=F),
        CreateFileNoClobber(effect_id="e2", path="a", post=F),
        CreateFileNoClobber(effect_id="e3", path="m", post=F),
    ])
    assert [t.path for t in timelines] == ["a", "m", "z"]


def test_repeated_path_absent_file_absent_file_is_continuous():
    # The design §5.3 case: one path through four states across four effects.
    effects = [
        CreateFileNoClobber(effect_id="e1", path="p", post=F),
        DeletePath(effect_id="e2", path="p", pre=F),
        CreateFileNoClobber(effect_id="e3", path="p", post=G),
        DeletePath(effect_id="e4", path="p", pre=G),
    ]
    (timeline,) = build_timelines(effects)
    assert timeline.path == "p"
    assert [(o.effect_id, o.effect_index) for o in timeline.occurrences] == [
        ("e1", 0), ("e2", 1), ("e3", 2), ("e4", 3),
    ]
    assert timeline.occurrences[0].pre is ABSENT
    assert timeline.occurrences[-1].post is ABSENT


def test_discontinuity_between_consecutive_occurrences_is_rejected():
    # e2 declares pre=G, but e1 left the path at F.
    effects = [
        CreateFileNoClobber(effect_id="e1", path="p", post=F),
        DeletePath(effect_id="e2", path="p", pre=G),
    ]
    with pytest.raises(SpecValidationError, match="p"):
        build_timelines(effects)


def test_discontinuity_across_kinds_is_rejected():
    effects = [
        CreateDirectory(effect_id="e1", path="p", post=D),
        DeletePath(effect_id="e2", path="p", pre=F),
    ]
    with pytest.raises(SpecValidationError, match="p"):
        build_timelines(effects)


def test_move_contributes_source_before_destination():
    (dst, src) = build_timelines([
        MoveNoClobber(effect_id="e1", source="s", destination="d", source_pre=F)
    ])
    assert dst.path == "d" and src.path == "s"
    assert src.occurrences[0].role == "source"
    assert dst.occurrences[0].role == "destination"
    assert src.occurrences[0].effect_index == dst.occurrences[0].effect_index == 0


def test_ancestor_type_change_timeline_is_continuous():
    # FILE -> ABSENT -> DIRECTORY on p, with a child created afterwards.
    effects = [
        DeletePath(effect_id="e1", path="p", pre=F),
        CreateDirectory(effect_id="e2", path="p", post=D),
        CreateFileNoClobber(effect_id="e3", path="p/q", post=F),
    ]
    timelines = {t.path: t for t in build_timelines(effects)}
    assert [o.pre for o in timelines["p"].occurrences] == [F, ABSENT]
    assert [o.post for o in timelines["p"].occurrences] == [ABSENT, D]
    assert len(timelines["p/q"].occurrences) == 1


def test_symlink_delete_forms_a_timeline():
    (timeline,) = build_timelines([DeletePath(effect_id="e1", path="lnk", pre=L)])
    assert timeline.occurrences[0].pre == L
    assert timeline.occurrences[0].post is ABSENT


def test_empty_effect_sequence_yields_no_timelines():
    # build_timelines is total on an empty sequence; refusing an empty spec is
    # compiler phase 1's job, not this module's.
    assert build_timelines([]) == ()
