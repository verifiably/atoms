"""A6 tier 3 -- capture: absence, staging, flush, and refusals (design §7, §8, §9)."""

from __future__ import annotations

import itertools
import os

import pytest

from atoms.core.errors import PreconditionRefused, ProtocolError
from atoms.core.fingerprint import SymlinkState
from atoms.store.blobs import digest_to_leaf
from tests.capture_support import (
    AFTER,
    BEFORE,
    DictPayloads,
    approved_blocked,
    approved_delete_symlink,
    approved_replace,
    digest_of,
    state_of,
    write_project_file,
)
from tests.coordinator_support import compiled_creating_a_directory


def payloads_for_replace() -> DictPayloads:
    return DictPayloads({digest_of(AFTER): AFTER})


# --- the digest-length precheck (design §7.2) ---------------------------------------


def test_one_digest_at_two_lengths_refuses_before_anything_is_written():
    """Measured: `compile_spec` accepts the contradiction.

    The staging name is `digest_to_leaf(digest)` -- one name per digest -- so two lengths
    collide and whichever wrote second would publish a blob one effect's FileState
    disagrees with. `_preflight` catches it too, but only after both files exist.
    """
    from atoms.coordinator.capture import require_one_length_per_digest

    with pytest.raises(ProtocolError, match="two byte_len"):
        require_one_length_per_digest(
            (("sha256:" + "a" * 64, 3), ("sha256:" + "a" * 64, 99))
        )


def test_one_length_per_digest_passes_through():
    from atoms.coordinator.capture import require_one_length_per_digest

    pairs = (("sha256:" + "a" * 64, 3), ("sha256:" + "b" * 64, 99))
    assert require_one_length_per_digest(pairs) == {
        "sha256:" + "a" * 64: 3,
        "sha256:" + "b" * 64: 99,
    }


# --- absence inference (design §8) ---------------------------------------------------


def test_a_missing_ancestor_justifies_its_descendants_absence(leased):
    from atoms.coordinator.admission import admit
    from atoms.coordinator.capture import capture_initial_surface
    from atoms.coordinator.prepare import open_workspace

    with leased() as lease:
        approved = admit(lease, compiled_creating_a_directory(lease))
        with open_workspace(lease, approved) as workspace, capture_initial_surface(
            lease, approved, workspace, payloads_for_replace()
        ) as captured:
            assert captured.manifest


def test_a_regular_file_blocker_matching_its_declared_state_justifies_absence(leased):
    from atoms.coordinator.capture import capture_initial_surface
    from atoms.coordinator.prepare import open_workspace

    with leased() as lease:
        write_project_file(lease, "p", BEFORE)
        approved = approved_blocked(lease, state_of(BEFORE))
        with open_workspace(lease, approved) as workspace, capture_initial_surface(
            lease, approved, workspace, payloads_for_replace()
        ) as captured:
            # The blocker's own content (BEFORE) plus the payload capture stages
            # (AFTER) -- proving the file branch actually ran, not just that
            # something did.
            assert {e.digest for e in captured.manifest} == {
                digest_of(BEFORE),
                digest_of(AFTER),
            }


def test_a_drifted_regular_file_blocker_refuses(leased):
    from atoms.coordinator.capture import capture_initial_surface
    from atoms.coordinator.prepare import open_workspace

    with leased() as lease:
        write_project_file(lease, "p", BEFORE)
        approved = approved_blocked(lease, state_of(BEFORE))
        write_project_file(lease, "p", b"drifted")
        with (
            open_workspace(lease, approved) as workspace,
            pytest.raises(
                PreconditionRefused, match="blocks traversal but is not the declared"
            ),
            capture_initial_surface(lease, approved, workspace, payloads_for_replace()),
        ):
            pass


def test_a_symlink_blocker_matching_its_declared_state_justifies_absence(leased):
    """The symlink twin of the file branch's justified-absence test (design §8.2).

    Without this, `_verify_stops`'s whole `elif type(expected) is SymlinkState:`
    branch is dead code as far as the suite can tell: deleting it leaves every other
    test green.
    """
    from atoms.coordinator.capture import capture_initial_surface
    from atoms.coordinator.prepare import open_workspace

    with leased() as lease:
        root_fd = lease._binding.project_root_fd
        os.symlink("elsewhere", "p", dir_fd=root_fd)
        approved = approved_blocked(lease, SymlinkState(target="elsewhere", mode=0o777))
        with open_workspace(lease, approved) as workspace, capture_initial_surface(
            lease, approved, workspace, payloads_for_replace()
        ) as captured:
            # A symlink retains no content (design §7 step 3): only the payload
            # capture stages, never the blocker itself.
            assert {e.digest for e in captured.manifest} == {digest_of(AFTER)}


def test_a_drifted_symlink_blocker_refuses(leased):
    """A drifted symlink is a symlink but not THIS symlink."""
    from atoms.coordinator.capture import capture_initial_surface
    from atoms.coordinator.prepare import open_workspace

    with leased() as lease:
        root_fd = lease._binding.project_root_fd
        os.symlink("elsewhere", "p", dir_fd=root_fd)
        approved = approved_blocked(lease, SymlinkState(target="elsewhere", mode=0o777))
        os.unlink("p", dir_fd=root_fd)
        os.symlink("drifted", "p", dir_fd=root_fd)

        with (
            open_workspace(lease, approved) as workspace,
            pytest.raises(
                PreconditionRefused, match="blocks traversal but is not the declared"
            ),
            capture_initial_surface(lease, approved, workspace, payloads_for_replace()),
        ):
            pass


def test_a_top_level_declared_absent_path_is_accepted(leased):
    """ABSENT is `AbsentState()`, not None.

    An observed-to-declared comparison that reached for a missing `.state` attribute
    would compare None against AbsentState and refuse every correct absent path.
    """
    from atoms.coordinator.admission import admit
    from atoms.coordinator.capture import capture_initial_surface
    from atoms.coordinator.prepare import open_workspace
    from tests.coordinator_support import compiled_for

    with leased() as lease:
        approved = admit(lease, compiled_for(lease))  # CreateFileNoClobber d/f.txt
        with open_workspace(lease, approved) as workspace, capture_initial_surface(
            lease, approved, workspace, payloads_for_replace()
        ) as captured:
            # Nothing to retain: the only declared initial state is ABSENT.
            assert {e.digest for e in captured.manifest} == {digest_of(AFTER)}


# --- staging (design §7) --------------------------------------------------------------


def test_a_preimage_is_staged_under_its_digest_leaf(leased):
    from atoms.coordinator.capture import capture_initial_surface
    from atoms.coordinator.prepare import open_workspace

    with leased() as lease:
        approved = approved_replace(lease)
        with open_workspace(lease, approved) as workspace, capture_initial_surface(
            lease, approved, workspace, payloads_for_replace()
        ) as captured:
            names = {entry.name for entry in captured.manifest}
            assert digest_to_leaf(digest_of(BEFORE)) in names
            assert digest_to_leaf(digest_of(AFTER)) in names
            assert set(os.listdir(workspace.staging_fd)) == names


def test_a_drifted_preimage_refuses(leased):
    from atoms.coordinator.capture import capture_initial_surface
    from atoms.coordinator.prepare import open_workspace

    with leased() as lease:
        approved = approved_replace(lease)
        write_project_file(lease, "d/f.txt", b"drifted")
        with (
            open_workspace(lease, approved) as workspace,
            pytest.raises(PreconditionRefused),
            capture_initial_surface(lease, approved, workspace, payloads_for_replace()),
        ):
            pass


def test_a_symlink_preimage_is_verified_and_not_staged(leased):
    """§7 step 3: a symlink retains no content.

    `referenced_digests` filters on FileState and never names one, and §10's rollback
    material for a symlink is the atomically transferred tombstone, which only A7 creates.
    """
    from atoms.coordinator.capture import capture_initial_surface
    from atoms.coordinator.prepare import open_workspace

    with leased() as lease:
        approved = approved_delete_symlink(lease)
        with open_workspace(lease, approved) as workspace, capture_initial_surface(
            lease, approved, workspace, DictPayloads({})
        ) as captured:
            assert captured.manifest == ()
            assert os.listdir(workspace.staging_fd) == []


def test_capture_composes_with_preparation(leased):
    """The whole seam: open_workspace -> capture -> prepare_transaction."""
    from atoms.coordinator.capture import capture_initial_surface
    from atoms.coordinator.prepare import open_workspace, prepare_transaction
    from atoms.core.recovery import TransactionState

    with leased() as lease:
        approved = approved_replace(lease)
        with open_workspace(lease, approved) as workspace, capture_initial_surface(
            lease, approved, workspace, payloads_for_replace()
        ) as captured:
            prepare_transaction(lease, approved, workspace, captured.manifest)

        record = lease._store.read_active()
        assert record is not None
        assert record.txid == approved.txid
        assert record.state is TransactionState.PREPARED


def test_the_descriptor_table_outlives_capture(leased):
    """§5.5: the table is what A7 executes against."""
    from atoms.coordinator.capture import capture_initial_surface
    from atoms.coordinator.prepare import open_workspace, prepare_transaction
    from atoms.core.recovery.snapshot import ProjectRoot

    with leased() as lease:
        approved = approved_replace(lease)
        with open_workspace(lease, approved) as workspace, capture_initial_surface(
            lease, approved, workspace, payloads_for_replace()
        ) as captured:
            prepare_transaction(lease, approved, workspace, captured.manifest)
            # Still live AFTER preparation -- this is the whole point.
            assert isinstance(captured.descriptors.fd_for(ProjectRoot()), int)


def test_a_stray_staging_entry_refuses_rather_than_surviving_success(leased):
    """§7.4 and criterion 10: on success, staging holds EXACTLY the manifest.

    `promote_staging` would refuse the stray later, but capture must not report success
    with an unrelated leaf still present.
    """
    from atoms.coordinator.capture import capture_initial_surface
    from atoms.coordinator.prepare import open_workspace

    with leased() as lease:
        approved = approved_replace(lease)
        with open_workspace(lease, approved) as workspace:
            fd = os.open(
                "stray",
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=workspace.staging_fd,
            )
            os.close(fd)
            with pytest.raises(PreconditionRefused, match="stray"), capture_initial_surface(
                lease, approved, workspace, payloads_for_replace()
            ):
                pass


# --- durability (design §7.3) ---------------------------------------------------------


def test_every_staged_file_is_flushed_before_its_sink_closes(leased, monkeypatch):
    """§7.3 ordering. `promote_staging` flushes DIRECTORIES only, so nothing else makes
    the contents durable, and a lost flush is invisible until a crash.

    Events are keyed by GENERATION, not by file descriptor: numeric descriptors are
    reused, so a closed sink and a later one that happened to get the same number would
    be conflated and the ordering assertion would pass on a broken build.
    """
    from atoms.coordinator import capture as capture_module
    from atoms.coordinator.capture import capture_initial_surface
    from atoms.coordinator.prepare import open_workspace
    from atoms.fs.linux import LinuxBackend

    generations: dict[int, int] = {}
    events: list[tuple[str, int]] = []
    counter = itertools.count()
    real_open_sink = capture_module._open_sink
    real_flush = LinuxBackend.flush_file
    real_close = os.close

    def spy_open_sink(backend, workspace, name):
        fd = real_open_sink(backend, workspace, name)
        generations[fd] = next(counter)
        events.append(("open", generations[fd]))
        return fd

    def spy_flush(self, fd):
        if fd in generations:
            events.append(("flush", generations[fd]))
        return real_flush(self, fd)

    def spy_close(fd):
        generation = generations.pop(fd, None)
        if generation is not None:
            events.append(("close", generation))
        return real_close(fd)

    monkeypatch.setattr(capture_module, "_open_sink", spy_open_sink)
    monkeypatch.setattr(LinuxBackend, "flush_file", spy_flush)
    monkeypatch.setattr(os, "close", spy_close)

    with leased() as lease:
        approved = approved_replace(lease)
        with open_workspace(lease, approved) as workspace, capture_initial_surface(
            lease, approved, workspace, payloads_for_replace()
        ) as captured:
            count = len(captured.manifest)
    monkeypatch.undo()

    opened = [g for kind, g in events if kind == "open"]
    assert len(opened) == count
    for generation in opened:
        sequence = [kind for kind, g in events if g == generation]
        assert sequence == ["open", "flush", "close"], (generation, sequence)


# --- payload contract (design §7.1, §9) -----------------------------------------------


def test_a_payload_whose_bytes_disagree_refuses(leased):
    from atoms.coordinator.capture import capture_initial_surface
    from atoms.coordinator.prepare import open_workspace

    with leased() as lease:
        approved = approved_replace(lease)
        payloads = DictPayloads({digest_of(AFTER): b"not-after"})
        with (
            open_workspace(lease, approved) as workspace,
            pytest.raises(PreconditionRefused, match="hashes to"),
            capture_initial_surface(lease, approved, workspace, payloads),
        ):
            pass


def test_a_missing_payload_binding_is_a_protocol_error(leased):
    """§9: absent or malformed is a broken submission, not external state."""
    from atoms.coordinator.capture import capture_initial_surface
    from atoms.coordinator.prepare import open_workspace

    with leased() as lease:
        approved = approved_replace(lease)
        with (
            open_workspace(lease, approved) as workspace,
            pytest.raises(ProtocolError, match="no binding"),
            capture_initial_surface(lease, approved, workspace, DictPayloads({})),
        ):
            pass


def test_a_payload_stream_error_propagates_as_itself(leased):
    """§9.1: only the missing-binding signal is translated.

    An `except Exception` around `open()` would convert EIO, ENOSPC, and outright bugs in
    the consumer's source into ProtocolError, which is exactly the overreach A5a's rule
    forbids.
    """
    from atoms.coordinator.capture import capture_initial_surface
    from atoms.coordinator.prepare import open_workspace

    class Exploding:
        def open(self, digest):
            raise OSError(5, "EIO")

    with leased() as lease:
        approved = approved_replace(lease)
        with (
            open_workspace(lease, approved) as workspace,
            pytest.raises(OSError) as caught,
            capture_initial_surface(lease, approved, workspace, Exploding()),
        ):
            pass
        assert caught.value.errno == 5


def test_a_payload_stream_yielding_text_refuses(leased):
    from atoms.coordinator.capture import capture_initial_surface
    from atoms.coordinator.prepare import open_workspace

    class TextSource:
        def open(self, digest):
            import io

            return io.StringIO("not bytes")

    with leased() as lease:
        approved = approved_replace(lease)
        with (
            open_workspace(lease, approved) as workspace,
            pytest.raises(ProtocolError, match="bytes"),
            capture_initial_surface(
                lease, approved, workspace, TextSource()  # pyright: ignore[reportArgumentType]
            ),
        ):
            pass


@pytest.mark.parametrize("first", [None, ""])
def test_a_payload_stream_yielding_a_falsy_non_bytes_refuses(leased, first):
    """The type check must precede the falsiness check.

    `None` and `""` are both falsy, so a `if not chunk: break` placed first accepts a
    malformed stream as a clean end of file -- and for a declared EMPTY file that hashes
    to the empty digest at length 0 and validates. The bug is invisible in every test
    whose payload is non-empty.
    """
    from atoms.coordinator.capture import capture_initial_surface
    from atoms.coordinator.prepare import open_workspace

    class FalsySource:
        def open(self, digest):
            class Stream:
                def read(self, size):
                    return first

                def close(self):
                    return None

            return Stream()

    with leased() as lease:
        approved = approved_replace(lease)
        with (
            open_workspace(lease, approved) as workspace,
            pytest.raises(ProtocolError, match="bytes"),
            capture_initial_surface(
                lease, approved, workspace, FalsySource()  # pyright: ignore[reportArgumentType]
            ),
        ):
            pass


def test_a_declared_file_that_drifted_into_a_directory_refuses(leased):
    """External state diverging from frozen intent is a refusal, not a protocol error.

    `Observation` requires `modeled` for a directory, so a preimage route that omitted it
    would surface this drift as ProtocolError("modeled...") -- blaming the engine for the
    filesystem.
    """
    from atoms.coordinator.capture import capture_initial_surface
    from atoms.coordinator.prepare import open_workspace

    with leased() as lease:
        approved = approved_replace(lease)
        root_fd = lease._binding.project_root_fd
        os.unlink("d/f.txt", dir_fd=root_fd)
        os.mkdir("d/f.txt", dir_fd=root_fd)

        with (
            open_workspace(lease, approved) as workspace,
            pytest.raises(PreconditionRefused, match="declared initial"),
            capture_initial_surface(lease, approved, workspace, payloads_for_replace()),
        ):
            pass


def test_an_extra_payload_binding_is_not_an_error(leased):
    """§9: `open(digest)` cannot be enumerated, so capture never learns of extras.

    Detecting them would mean adding enumeration machinery for a condition that harms
    nothing -- an unrequested payload is never opened, staged, or promoted.
    """
    from atoms.coordinator.capture import capture_initial_surface
    from atoms.coordinator.prepare import open_workspace

    with leased() as lease:
        approved = approved_replace(lease)
        payloads = DictPayloads({digest_of(AFTER): AFTER, digest_of(b"junk"): b"junk"})
        with (
            open_workspace(lease, approved) as workspace,
            capture_initial_surface(lease, approved, workspace, payloads) as captured,
        ):
            assert digest_of(b"junk") not in payloads.requested
            assert captured.manifest


def test_a_refusal_leaves_reclaimable_scratch_and_no_record(leased):
    """§7.4: cleanliness is scoped to success.

    Partial workspace scratch is mutation-free, no durable record exists, and A5b's
    reclamation removes it at the next lease entry.
    """
    from atoms.coordinator.capture import capture_initial_surface
    from atoms.coordinator.prepare import open_workspace

    with leased() as lease:
        approved = approved_replace(lease)
        payloads = DictPayloads({digest_of(AFTER): b"wrong"})
        with (
            open_workspace(lease, approved) as workspace,
            pytest.raises(PreconditionRefused),
            capture_initial_surface(lease, approved, workspace, payloads),
        ):
            pass
        assert lease._store.read_active() is None


# --- workspace authentication, before the first write --------------------------------


def test_a_workspace_from_another_store_refuses_before_anything_is_staged(leased):
    """A matching txid is not a matching workspace.

    `promote_staging` makes exactly this check (`blobs.py:334`), but it runs inside
    `prepare_transaction` -- by then capture has already streamed this transaction's
    preimages into a foreign store's staging directory. Two `leased()` contexts are two
    independent projects and metadata roots, so the foreign store is real, and
    `create_workspace` takes the txid it is given: the collision is constructible.
    """
    from atoms.coordinator.capture import capture_initial_surface

    with leased() as lease:
        approved = approved_replace(lease)
        with leased() as other:
            foreign = other._store.create_workspace(approved.txid)
            try:
                assert foreign.txid == approved.txid
                assert foreign._store is not lease._store
                with pytest.raises(ProtocolError, match="different Store"):
                    capture_initial_surface(
                        lease, approved, foreign, payloads_for_replace()
                    )
                # The refusal came before the first write, not after it.
                assert os.listdir(foreign.staging_fd) == []
            finally:
                foreign.close()


def test_a_duck_typed_workspace_refuses_before_anything_is_staged(leased):
    """The impostor satisfies every duck check, so only the exact-type gate stops it.

    It carries the lease's own store, the proof's own txid, and the real descriptors --
    which is the point: `workspace._store is lease._store` and the txid comparison both
    pass, and capture would write through `staging_fd` into a workspace whose `close`
    and spent-flag discipline nothing owns.
    """
    from typing import cast

    from atoms.coordinator.capture import capture_initial_surface
    from atoms.coordinator.prepare import open_workspace
    from atoms.store.workspace import Workspace

    class Impostor:
        def __init__(self, real: Workspace) -> None:
            self._store = real._store
            self.txid = real.txid
            self.staging_fd = real.staging_fd
            self.work_fd = real.work_fd

    with leased() as lease:
        approved = approved_replace(lease)
        with open_workspace(lease, approved) as workspace:
            impostor = Impostor(workspace)
            with pytest.raises(ProtocolError, match="exactly Workspace"):
                capture_initial_surface(
                    lease,
                    approved,
                    cast(Workspace, impostor),
                    payloads_for_replace(),
                )
            assert os.listdir(workspace.staging_fd) == []
