"""Tier 3: A4b-1 against a real ext4 volume (design §9.3)."""

from __future__ import annotations

import builtins
import os
import shutil
import subprocess
import sys
import unicodedata

import pytest

from atoms.core.errors import ProjectApprovalRefused
from atoms.fs.lookup import LookupProof, inherited_constraints, read_lookup_constraints
from atoms.fs.resolve import PathResolver, PresentFrontier
from tests.fs_support import descriptor_count

# Both children follow A4a's _BIND_MOUNT_CHILD convention: script text plus sys.argv,
# exit 77 for "namespace or mount unavailable, skip". Each builds its own single-entry
# allowlist inline, so it needs nothing from the `tests` package on its path. Both
# construct a PathResolver and call resolve(): asserting the errno from
# open_child_directory, or st_dev/mnt_id from two bare descriptors, would prove a
# property of A4a and of the kernel while leaving A4b-1's translation of it untested.
_BIND_PREAMBLE = r"""
import os
import subprocess
import sys

from atoms.core.errors import ProjectApprovalRefused
from atoms.fs.binding import bind_project_volume
from atoms.fs.linux import LinuxBackend
from atoms.fs.lock import acquire_project_lock
from atoms.fs.resolve import PathResolver
from atoms.fs.volume import (
    AllowlistEntry,
    DurabilityAllowlist,
    StorageProfile,
    build_configuration,
    kernel_identifier,
    read_mountinfo,
    resolve_mount_entry,
)

project, source, target, metadata_root, mount_program = sys.argv[1:]
mounted = subprocess.run(
    [mount_program, "--bind", source, target], capture_output=True, text=True
)
if mounted.returncode != 0:
    print(mounted.stderr.strip(), file=sys.stderr)
    sys.exit(77)

storage = StorageProfile(profile_id="a4b1-bind-test")
backend = LinuxBackend()


def resolver_for(lock):
    fd = backend.open_root(project)
    try:
        entry = resolve_mount_entry(fd, read_mountinfo())
        configuration = build_configuration(
            entry, kernel_identifier(), directory_fd=fd
        )
    finally:
        os.close(fd)
    allowlist = DurabilityAllowlist(
        entries=frozenset(
            {
                AllowlistEntry(
                    configuration=configuration,
                    storage=storage,
                    certification_ref="test-injected-not-crash-certified",
                )
            }
        )
    )
    return bind_project_volume(project, lock, allowlist=allowlist, storage=storage)
"""

_ANCESTOR_BIND_CHILD = _BIND_PREAMBLE + r"""
with acquire_project_lock(backend, metadata_root) as lock:
    with resolver_for(lock) as binding:
        resolver = PathResolver(binding)
        try:
            resolver.resolve("ancestor/leaf")
        except ProjectApprovalRefused as caught:
            if "mount boundary" not in str(caught):
                print(f"wrong refusal: {caught}", file=sys.stderr)
                sys.exit(10)
        else:
            print("resolve() admitted a bind-mounted ancestor", file=sys.stderr)
            sys.exit(11)
"""

_LEAF_BIND_CHILD = _BIND_PREAMBLE + r"""
if os.stat(source).st_dev != os.stat(target).st_dev:
    print("the bind mount did not preserve st_dev", file=sys.stderr)
    sys.exit(10)

with acquire_project_lock(backend, metadata_root) as lock:
    with resolver_for(lock) as binding:
        resolver = PathResolver(binding)
        try:
            resolver.resolve("leaf")
        except ProjectApprovalRefused as caught:
            if "mount" not in str(caught):
                print(f"wrong refusal: {caught}", file=sys.stderr)
                sys.exit(11)
        else:
            print("resolve() admitted a bind-mounted leaf", file=sys.stderr)
            sys.exit(12)
"""


def _run_bind_child(script, ext4_volume, target_name):
    """Build project/metadata on the ext4 volume and run `script` in a namespace."""
    unshare = shutil.which("unshare")
    mount_program = shutil.which("mount")
    if unshare is None or mount_program is None:
        missing = "unshare" if unshare is None else "mount"
        pytest.skip(f"the bind-mount tier requires the {missing!r} program")

    namespace_probe = subprocess.run(
        [unshare, "--mount", "--map-root-user", "--", "true"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if namespace_probe.returncode != 0:
        reason = namespace_probe.stderr.strip() or "no diagnostic"
        pytest.skip(f"user/mount namespaces unavailable: {reason}")

    project = ext4_volume / "project"
    project.mkdir()
    (project / "source").mkdir()
    (project / target_name).mkdir()
    finished = subprocess.run(
        [
            unshare,
            "--mount",
            "--map-root-user",
            "--",
            sys.executable,
            "-c",
            script,
            str(project),
            str(project / "source"),
            str(project / target_name),
            str(ext4_volume / "metadata"),
            mount_program,
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if finished.returncode == 77:
        reason = finished.stderr.strip() or "no diagnostic"
        pytest.skip(f"isolated bind mount unavailable: {reason}")
    assert finished.returncode == 0, finished.stderr


@pytest.mark.parametrize(
    ("made", "sought"),
    [
        ("a", "A"),
        (unicodedata.normalize("NFC", "é"), unicodedata.normalize("NFD", "é")),
        (unicodedata.normalize("NFD", "é"), unicodedata.normalize("NFC", "é")),
    ],
)
def test_a_plain_ext4_directory_compares_names_as_exact_bytes(
    ext4_probe_fd, made, sought
):
    """EXACT_BYTES is a claim about the filesystem, so it is verified against one."""
    fd = os.open(made, os.O_CREAT | os.O_WRONLY, 0o600, dir_fd=ext4_probe_fd)
    os.close(fd)
    try:
        with pytest.raises(FileNotFoundError):
            os.stat(sought, dir_fd=ext4_probe_fd)
    finally:
        os.unlink(made, dir_fd=ext4_probe_fd)


def test_a_plain_ext4_directory_reports_exact_bytes(ext4_probe_fd):
    assert read_lookup_constraints(ext4_probe_fd, "ext4").lookup_proof is (
        LookupProof.EXACT_BYTES
    )


def test_a_created_directory_matches_the_inheritance_rule(ext4_probe_fd):
    """Without this, §5.4's rule is only prose."""
    parent = read_lookup_constraints(ext4_probe_fd, "ext4")
    os.mkdir("child", mode=0o700, dir_fd=ext4_probe_fd)
    child_fd = os.open("child", os.O_RDONLY | os.O_DIRECTORY, dir_fd=ext4_probe_fd)
    try:
        observed = read_lookup_constraints(child_fd, "ext4")
    finally:
        os.close(child_fd)
    assert observed == inherited_constraints(parent, "ext4")


def test_a_255_byte_name_is_accepted_and_256_refuses(ext4_bound_volume):
    with ext4_bound_volume() as binding:
        resolver = PathResolver(binding)
        assert resolver.resolve("a" * 255).frontier_name == "a" * 255
        with pytest.raises(ProjectApprovalRefused):
            resolver.resolve("a" * 256)


def test_two_hard_links_share_an_identity_with_distinct_provenance(
    ext4_bound_volume, ext4_project_root
):
    """A4b-1 reports the shared identity; whether topology merges them is A4b-2's."""
    (ext4_project_root / "left").write_text("x")
    os.link(ext4_project_root / "left", ext4_project_root / "right")
    with ext4_bound_volume() as binding:
        resolver = PathResolver(binding)
        left = resolver.resolve("left")
        right = resolver.resolve("right")
        # Narrowed, not assumed: `frontier` is a union and pyright type-checks tests.
        assert isinstance(left.frontier, PresentFrontier)
        assert isinstance(right.frontier, PresentFrontier)
        assert left.frontier.identity == right.frontier.identity
        assert left.frontier_name != right.frontier_name


def test_a_path_equal_to_the_metadata_root_refuses(ext4_nested_bound_volume):
    """Needs a metadata root INSIDE the project root.

    With A4a's sibling layout the metadata root's relative spelling starts with '..',
    require_rel_path rejects that, and this assertion is unreachable.
    """
    with ext4_nested_bound_volume() as binding:
        resolver = PathResolver(binding)
        with pytest.raises(ProjectApprovalRefused) as caught:
            resolver.resolve("metadata")
        assert "metadata root" in str(caught.value)


def test_a_path_beneath_the_metadata_root_refuses(ext4_nested_bound_volume):
    with ext4_nested_bound_volume() as binding:
        resolver = PathResolver(binding)
        with pytest.raises(ProjectApprovalRefused) as caught:
            resolver.resolve("metadata/staging")
        assert "metadata root" in str(caught.value)


def test_the_descriptor_peak_is_three_at_any_depth(
    monkeypatch, ext4_bound_volume, ext4_project_root
):
    """Every acquisition point samples immediately after it returns.

    Omitting the read_mount_id hook is what would let a false bound of two pass:
    its /proc/self/fdinfo handle coexists with the parent and the O_PATH descriptor.

    Both leaves must EXIST. _observe returns AbsentFrontier the moment os.open reports
    ENOENT, so an absent leaf skips the O_PATH descriptor and read_mount_id entirely,
    and the measured peak collapses to the walk's two — which is how a bound of three
    would silently go unverified.
    """
    import atoms.fs.volume as volume_module

    deep = ext4_project_root
    for index in range(512):
        deep = deep / f"d{index}"
    deep.mkdir(parents=True)
    (deep / "leaf").write_text("x")
    (ext4_project_root / "s0").mkdir()
    (ext4_project_root / "s0" / "leaf").write_text("x")

    samples: list[int] = []
    real_open = os.open

    def sampling_open(*args, **kwargs):
        fd = real_open(*args, **kwargs)
        samples.append(descriptor_count())
        return fd

    def sampling_builtin_open(*args, **kwargs):
        # builtins.open, not volume_module.open: volume.py declares no such global,
        # so reading it raises AttributeError and the measurement never runs. Setting
        # it as a module global shadows the builtin for that module alone, because
        # module globals are consulted before builtins.
        # SIM115 is suppressed because the caller owns the handle; this wrapper only
        # samples the descriptor count while it is open.
        handle = builtins.open(*args, **kwargs)  # noqa: SIM115
        samples.append(descriptor_count())
        return handle

    with ext4_bound_volume() as binding:
        resolver = PathResolver(binding)
        backend = binding.backend
        real_child = backend.open_child_directory

        def sampling_child(parent_fd, name):
            fd = real_child(parent_fd, name)
            samples.append(descriptor_count())
            return fd

        monkeypatch.setattr(
            type(backend), "open_child_directory", staticmethod(sampling_child)
        )
        monkeypatch.setattr("atoms.fs.resolve.os.open", sampling_open)
        monkeypatch.setattr(
            volume_module, "open", sampling_builtin_open, raising=False
        )

        baseline = descriptor_count()
        samples.clear()
        resolver.resolve("s0/leaf")
        shallow_peak = max(samples) - baseline

        samples.clear()
        resolver.resolve("/".join(f"d{index}" for index in range(512)) + "/leaf")
        deep_peak = max(samples) - baseline

    assert shallow_peak == deep_peak == 3


def test_a_bind_mount_at_an_ancestor_makes_resolve_refuse(ext4_volume):
    """EXDEV from RESOLVE_NO_XDEV, translated by the resolver into a refusal."""
    _run_bind_child(_ANCESTOR_BIND_CHILD, ext4_volume, "ancestor")


def test_a_bind_mount_at_the_leaf_makes_resolve_refuse(ext4_volume):
    """The pair that justifies O_PATH + read_mount_id over lstat.

    The child asserts st_dev is EQUAL across the boundary and that resolve() refuses
    anyway. With lstat the refusal would never fire while the mount went unseen.
    """
    _run_bind_child(_LEAF_BIND_CHILD, ext4_volume, "leaf")
