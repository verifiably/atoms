from atoms.core.capabilities import (
    ALWAYS_REQUIRED,
    Capability,
    required_capabilities,
    variant_capabilities,
)
from atoms.core.effects import (
    CreateDirectory,
    CreateFileNoClobber,
    DeletePath,
    MoveNoClobber,
    ReplaceFile,
)
from atoms.core.fingerprint import DirectoryState, FileState, SymlinkState

F = FileState(content_hash="sha256:" + "3" * 64, mode=0o644, byte_len=2)
C = Capability


def test_always_required_trio():
    assert ALWAYS_REQUIRED == frozenset(
        {C.ANCHORED_TRAVERSAL, C.DURABLE_PUBLISH, C.ADVISORY_PROJECT_LOCK}
    )


def test_replace_file_needs_exchange_and_nofollow_read():
    e = ReplaceFile(effect_id="e", path="a", pre=F, post=F)
    assert variant_capabilities(e) == frozenset({C.ATOMIC_EXCHANGE, C.NOFOLLOW_COHERENT_READ})


def test_delete_capability_branches_on_precondition_kind():
    file_delete = DeletePath(effect_id="e", path="a", pre=F)
    assert variant_capabilities(file_delete) == frozenset(
        {C.NOCLOBBER_TRANSFER, C.NOFOLLOW_COHERENT_READ}
    )
    link_delete = DeletePath(effect_id="e", path="a", pre=SymlinkState(target="x", mode=0o777))
    assert variant_capabilities(link_delete) == frozenset(
        {C.NOCLOBBER_TRANSFER, C.SYMLINK_FINGERPRINT}
    )


def test_move_needs_identity_anchor():
    e = MoveNoClobber(effect_id="e", source="s", destination="d", source_pre=F)
    assert variant_capabilities(e) == frozenset(
        {C.IDENTITY_ANCHOR, C.NOCLOBBER_TRANSFER, C.NOFOLLOW_COHERENT_READ}
    )


def test_create_variants_need_noclobber_transfer():
    cf = CreateFileNoClobber(effect_id="e", path="a", post=F)
    cd = CreateDirectory(effect_id="e", path="a", post=DirectoryState(mode=0o755))
    assert variant_capabilities(cf) == frozenset({C.NOCLOBBER_TRANSFER})
    assert variant_capabilities(cd) == frozenset({C.NOCLOBBER_TRANSFER})


def test_replace_only_spec_excludes_identity_anchor_and_noclobber():
    caps = required_capabilities([ReplaceFile(effect_id="e", path="a", pre=F, post=F)])
    assert C.IDENTITY_ANCHOR not in caps
    assert C.NOCLOBBER_TRANSFER not in caps
    assert ALWAYS_REQUIRED <= caps
    assert {C.ATOMIC_EXCHANGE, C.NOFOLLOW_COHERENT_READ} <= caps


def test_required_is_union_over_effects():
    effects = [
        ReplaceFile(effect_id="e1", path="a", pre=F, post=F),
        MoveNoClobber(effect_id="e2", source="s", destination="d", source_pre=F),
    ]
    caps = required_capabilities(effects)
    assert caps == ALWAYS_REQUIRED | frozenset(
        {C.ATOMIC_EXCHANGE, C.NOFOLLOW_COHERENT_READ, C.IDENTITY_ANCHOR, C.NOCLOBBER_TRANSFER}
    )
