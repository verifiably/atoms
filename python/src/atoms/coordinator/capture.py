"""Authority §7.3 step 1 -- coherent capture (design §7, §8, §9).

Everything here happens BEFORE the durable record exists, so no path halts. Errnos with a
defined domain meaning translate (`translated_lookup`); every other OSError propagates
with its own class and traceback.
"""

from __future__ import annotations

import hashlib
import os
from typing import IO, Protocol, Self, cast

from atoms.coordinator.admission import _require_admitted
from atoms.coordinator.descriptors import DescriptorTable, WalkStop, _build_descriptor_table
from atoms.coordinator.lease import Lease
from atoms.core.errors import PreconditionRefused, ProtocolError
from atoms.core.fingerprint import ABSENT, DirectoryState, FileState, SymlinkState
from atoms.core.recovery.model import (
    ObservedAbsent,
    ObservedDirectory,
    ObservedEntry,
    ObservedFile,
    ObservedSymlink,
)
from atoms.fs.approval import ProjectApprovedSpec
from atoms.fs.observe import Observation
from atoms.store.blobs import StagedBlob, digest_to_leaf
from atoms.store.records import referenced_digests
from atoms.store.workspace import Workspace

_READ_CHUNK = 1 << 20


class PayloadSource(Protocol):
    """The consumer's planned-postimage bytes (design §7.1).

    Authority §4.1 forbids the engine from reaching back into consumer plan formats and
    §4.2 reserves staging-path derivation to the engine, so the bytes arrive
    content-addressed: two effects writing identical content are supplied once, and the
    consumer never learns a staging path.

    `open` returns a FRESH binary stream, owned by capture, which closes it whether the
    stream is consumed, refused, or abandoned by an earlier failure. It raises `KeyError`
    -- and only `KeyError` -- when it has no binding for a digest. That is the one signal
    capture translates; every other exception is the source's own and propagates.
    """

    def open(self, digest: str) -> IO[bytes]: ...


class Captured:
    """A live resource: the manifest, and the descriptor table that outlives capture."""

    __slots__ = ("_closed", "descriptors", "manifest")

    def __init__(
        self, *, manifest: tuple[StagedBlob, ...], descriptors: DescriptorTable
    ) -> None:
        self.manifest = manifest
        self.descriptors = descriptors
        self._closed = False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.descriptors.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def capture_initial_surface(
    lease: Lease,
    approved: ProjectApprovedSpec,
    workspace: Workspace,
    payloads: PayloadSource,
) -> Captured:
    """Authority §7.3 step 1 (design §7)."""
    _require_admitted(lease, approved)
    # The workspace is authenticated before anything is written into it. `promote_staging`
    # already makes exactly these two checks (`blobs.py:332-335`), but it runs inside
    # `prepare_transaction`, long after capture has streamed preimages and payloads into
    # `workspace.staging_fd`. A duck-typed value, or a real Workspace issued by a
    # different Store with the same txid, would receive those bytes and only be rejected
    # afterwards -- so the check belongs at the first function that writes.
    if type(workspace) is not Workspace:
        raise ProtocolError(
            f"expected exactly Workspace, got {type(workspace).__name__}"
        )
    if workspace._store is not lease._store:
        raise ProtocolError("this workspace belongs to a different Store")
    if workspace.txid != approved.txid:
        raise ProtocolError(
            f"workspace txid {workspace.txid!r} does not match the proof's "
            f"{approved.txid!r}"
        )

    lengths = require_one_length_per_digest(referenced_digests(approved.compiled.spec))
    backend = lease._binding.backend
    manifest: list[StagedBlob] = []
    table: DescriptorTable | None = None
    try:
        with Observation(backend) as observation:
            table = _build_descriptor_table(lease, approved, workspace, observation)
            _verify_stops(table, approved)
            manifest.extend(
                _stage_preimages(observation, table, approved, workspace, backend)
            )
        manifest.extend(
            _stage_payloads(
                backend, workspace, payloads, lengths, {e.digest for e in manifest}
            )
        )
        _require_staging_matches(workspace, manifest)
    except BaseException:
        if table is not None:
            table.close()
        raise
    return Captured(manifest=tuple(manifest), descriptors=table)


def require_one_length_per_digest(
    pairs: tuple[tuple[str, int], ...],
) -> dict[str, int]:
    """One byte_len per digest, checked BEFORE anything is written (design §7.2).

    `compile_spec` validates each byte_len's range and the empty-hash correspondence but
    never cross-checks that one content_hash carries one byte_len -- measured. The staging
    name is one name per digest, so two lengths collide on it. `_preflight` refuses this
    too, but only after capture has already written both files.

    Engine misuse surfacing at the first layer that can see it, not external drift: the
    contradiction is in the frozen spec and no filesystem state is involved.
    """
    lengths: dict[str, int] = {}
    for digest, byte_len in pairs:
        previous = lengths.setdefault(digest, byte_len)
        if previous != byte_len:
            raise ProtocolError(
                f"digest {digest} is declared at two byte_len values, {previous} and "
                f"{byte_len}; both would stage under one name"
            )
    return lengths


def _verify_stops(table: DescriptorTable, approved: ProjectApprovedSpec) -> None:
    """Justify the absence of everything beneath each stop (design §8).

    Two routes, two branches, both selected by the DECLARED state and never by an errno.
    Conflating them would silently grant a symlink the file branch's coherence.
    """
    declared = _first_states(approved)
    for stop in table.stops:
        if type(stop.observed) is ObservedAbsent:
            # §8.1: the planned directory's name is genuinely free.
            continue
        expected = declared.get(stop.path)
        if type(expected) is FileState:
            # §8.2, descriptor-coherent: type, mode, and hash all from one descriptor.
            # Stronger than the negative lookup it replaces.
            _require_declared(stop, ObservedFile, expected)
        elif type(expected) is SymlinkState:
            # §8.2, NOT descriptor-coherent: lstat + readlink, no descriptor, no
            # identity. The absence inference holds because a symlink holds no entries;
            # the identity contract is deferred to A7's destructive transfer.
            _require_declared(stop, ObservedSymlink, expected)
        else:
            raise PreconditionRefused(
                f"{stop.path!r} blocks traversal but no declared file or symlink state "
                f"describes it; observed {stop.observed!r}"
            )


def _require_declared(stop: WalkStop, expected_type: type, expected) -> None:
    observed = stop.observed
    # `expected_type` is a runtime value, so pyright cannot narrow `observed` off of it;
    # the cast asserts the invariant its two call sites already hold.
    matches = type(observed) is expected_type and (
        cast(ObservedFile | ObservedSymlink, observed).state == expected
    )
    if not matches:
        raise PreconditionRefused(
            f"{stop.path!r} blocks traversal but is not the declared {expected!r}; "
            f"observed {observed!r}"
        )


def _stage_preimages(
    observation: Observation,
    table: DescriptorTable,
    approved: ProjectApprovedSpec,
    workspace: Workspace,
    backend,
) -> list[StagedBlob]:
    """Verify every reachable declared path against its timeline's FIRST precondition.

    Later occurrence-local preconditions describe intermediate states no initial capture
    can observe, and checking them here would refuse correct transactions.
    """
    declared = _first_states(approved)
    staged: list[StagedBlob] = []
    seen: set[str] = set()
    for path_entry in approved.paths:
        parent = path_entry.parent_node
        if table.is_unreachable(parent):
            continue  # §8 already justified the absence of everything below the stop.
        try:
            parent_fd = table.fd_for(parent)
        except KeyError as caught:
            raise ProtocolError(
                f"{parent!r} is neither in the descriptor table nor proved unreachable; "
                "the walk and the approved paths disagree"
            ) from caught
        expected = declared[path_entry.path]
        if type(expected) is FileState and expected.content_hash not in seen:
            seen.add(expected.content_hash)
            name = digest_to_leaf(expected.content_hash)
            sink = _open_sink(workspace, name)
            try:
                # `modeled` is passed on this route too. A declared file that drifted
                # into a directory would otherwise make `Observation` raise
                # ProtocolError for a missing argument, when the honest answer is that
                # external state diverged from the frozen spec -- PreconditionRefused.
                entry = observation.observe(
                    parent_fd,
                    path_entry.leaf,
                    sink_fd=sink,
                    modeled=_modeled_under(approved, path_entry.path),
                )
                _require_state(path_entry.path, entry, expected)
                backend.flush_file(sink)
            finally:
                os.close(sink)
            staged.append(
                StagedBlob(
                    name=name,
                    digest=expected.content_hash,
                    byte_len=expected.byte_len,
                )
            )
            continue
        entry = observation.observe(
            parent_fd,
            path_entry.leaf,
            modeled=_modeled_under(approved, path_entry.path),
        )
        _require_state(path_entry.path, entry, expected)
    return staged


def _stage_payloads(
    backend,
    workspace: Workspace,
    payloads: PayloadSource,
    lengths: dict[str, int],
    already: set[str],
) -> list[StagedBlob]:
    staged: list[StagedBlob] = []
    for digest in sorted(set(lengths) - already):
        try:
            stream = payloads.open(digest)
        except KeyError as caught:
            raise ProtocolError(
                f"the payload source has no binding for {digest}, which the frozen spec "
                "declares as a planned postimage"
            ) from caught
        # Two independent owners: a failure to open the sink must not leak the stream,
        # and a stream that raises on close must not skip closing the sink.
        try:
            sink = _open_sink(workspace, digest_to_leaf(digest))
            try:
                observed, byte_len = _stream_into(stream, sink)
                if observed != digest or byte_len != lengths[digest]:
                    raise PreconditionRefused(
                        f"the payload for {digest} hashes to {observed} at {byte_len} "
                        f"bytes, not {digest} at {lengths[digest]}"
                    )
                backend.flush_file(sink)
            finally:
                os.close(sink)
        finally:
            stream.close()
        staged.append(
            StagedBlob(
                name=digest_to_leaf(digest), digest=digest, byte_len=lengths[digest]
            )
        )
    return staged


def _require_staging_matches(
    workspace: Workspace, manifest: list[StagedBlob]
) -> None:
    """On success, staging holds EXACTLY the manifest (design §7.4, criterion 10)."""
    present = set(os.listdir(workspace.staging_fd))
    expected = {entry.name for entry in manifest}
    if present != expected:
        raise PreconditionRefused(
            f"staging/{workspace.txid}/ holds stray entries "
            f"{sorted(present - expected)} and is missing {sorted(expected - present)}"
        )


def _open_sink(workspace: Workspace, name: str) -> int:
    """The staging sink. O_EXCL, so external occupancy of the leaf surfaces as EEXIST."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        return os.open(name, flags, 0o600, dir_fd=workspace.staging_fd)
    except FileExistsError as caught:
        raise PreconditionRefused(
            f"staging/{workspace.txid}/{name} is already occupied; authority §11 names "
            "pre-existing external occupancy of an engine-derived scratch leaf a clean "
            "refusal, and it need not be concurrent"
        ) from caught


def _stream_into(stream: IO[bytes], sink_fd: int) -> tuple[str, int]:
    digest = hashlib.sha256()
    length = 0
    while True:
        chunk = stream.read(_READ_CHUNK)
        # Type BEFORE falsiness. A stream returning None or "" is malformed, not at end
        # of file, and testing falsiness first would accept it as a clean EOF -- which
        # for an empty declared file hashes to the empty digest and validates.
        if type(chunk) is not bytes:
            raise ProtocolError(
                f"a payload stream yielded {type(chunk).__name__}, not bytes"
            )
        if not chunk:
            break
        digest.update(chunk)
        length += len(chunk)
        view = memoryview(chunk)
        while view:
            view = view[os.write(sink_fd, view) :]
    return "sha256:" + digest.hexdigest(), length


def _require_state(path: str, entry: ObservedEntry, expected) -> None:
    """Compare an observation with a declared state.

    Explicit per kind. `ABSENT` is `AbsentState()`, not None, so reaching for a missing
    `.state` attribute would compare None against AbsentState and refuse every correct
    declared-absent path.
    """
    matches = (
        (type(entry) is ObservedAbsent and expected is ABSENT)
        or (type(entry) is ObservedFile and type(expected) is FileState)
        or (type(entry) is ObservedSymlink and type(expected) is SymlinkState)
        or (type(entry) is ObservedDirectory and type(expected) is DirectoryState)
    )
    if not matches:
        raise PreconditionRefused(
            f"{path!r} is {entry!r}, which is not the declared initial {expected!r}"
        )
    if type(entry) is ObservedAbsent:
        return
    # `matches` above already confines `entry` to a kind whose declared `expected`
    # matches its own; the cast asserts that in a form pyright's `or`-narrowing
    # cannot infer on its own.
    present = cast(ObservedFile | ObservedSymlink | ObservedDirectory, entry)
    if present.state != expected:
        raise PreconditionRefused(
            f"{path!r} is {present.state!r}, not the declared initial {expected!r}"
        )


def _modeled_under(approved: ProjectApprovedSpec, path: str) -> frozenset[str]:
    base = f"{path}/"
    return frozenset(
        entry.path[len(base) :]
        for entry in approved.paths
        if entry.path.startswith(base) and "/" not in entry.path[len(base) :]
    )


def _first_states(approved: ProjectApprovedSpec) -> dict[str, object]:
    """Each path's FIRST precondition -- the declared initial surface.

    Measured: `PathTimeline` is `(path, occurrences)` and `TimelineOccurrence` carries
    `pre`.
    """
    return {
        timeline.path: timeline.occurrences[0].pre
        for timeline in approved.compiled.timelines
    }
