from atoms.core.effects import (
    CreateDirectory,
    CreateFileNoClobber,
    DeletePath,
    MoveNoClobber,
    Occurrence,
    ReplaceFile,
    effect_id_of,
    occurrences,
    variant_name,
)
from atoms.core.fingerprint import (
    ABSENT,
    DirectoryState,
    FileState,
    SymlinkState,
)

F1 = FileState(content_hash="sha256:" + "1" * 64, mode=0o644, byte_len=3)
F2 = FileState(content_hash="sha256:" + "2" * 64, mode=0o644, byte_len=5)


def test_replace_file_occurrence_is_pre_to_post_on_one_path():
    e = ReplaceFile(effect_id="e1", path="a.txt", pre=F1, post=F2)
    assert occurrences(e) == (Occurrence(path="a.txt", pre=F1, post=F2, role="target"),)
    assert effect_id_of(e) == "e1"
    assert variant_name(e) == "ReplaceFile"


def test_create_file_is_absent_to_post():
    e = CreateFileNoClobber(effect_id="e2", path="new.txt", post=F1)
    assert occurrences(e) == (Occurrence(path="new.txt", pre=ABSENT, post=F1, role="target"),)


def test_delete_is_pre_to_absent_for_file_and_symlink():
    ef = DeletePath(effect_id="e3", path="gone.txt", pre=F1)
    assert occurrences(ef) == (Occurrence(path="gone.txt", pre=F1, post=ABSENT, role="target"),)
    sl = SymlinkState(target="x", mode=0o777)
    es = DeletePath(effect_id="e4", path="link", pre=sl)
    assert occurrences(es) == (Occurrence(path="link", pre=sl, post=ABSENT, role="target"),)


def test_move_enumerates_source_and_destination():
    e = MoveNoClobber(effect_id="e5", source="s.txt", destination="d.txt", source_pre=F1)
    assert occurrences(e) == (
        Occurrence(path="s.txt", pre=F1, post=ABSENT, role="source"),
        Occurrence(path="d.txt", pre=ABSENT, post=F1, role="destination"),
    )


def test_create_directory_is_absent_to_dir():
    d = DirectoryState(mode=0o755)
    e = CreateDirectory(effect_id="e6", path="sub", post=d)
    assert occurrences(e) == (Occurrence(path="sub", pre=ABSENT, post=d, role="target"),)
