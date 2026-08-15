"""Raw ext4 images used by the certification guest."""

from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from pathlib import Path

from atoms.fs.volume import FeatureMasks

_NEEDS_RECOVERY = 0x4
_FEATURES = (
    ("compat", 0x4, "has_journal"),
    ("compat", 0x8, "ext_attr"),
    ("compat", 0x10, "resize_inode"),
    ("compat", 0x20, "dir_index"),
    ("compat", 0x400, "fast_commit"),
    ("compat", 0x1000, "orphan_file"),
    ("incompat", 0x2, "filetype"),
    ("incompat", _NEEDS_RECOVERY, "needs_recovery"),
    ("incompat", 0x40, "extent"),
    ("incompat", 0x80, "64bit"),
    ("incompat", 0x200, "flex_bg"),
    ("incompat", 0x2000, "metadata_csum_seed"),
    ("ro_compat", 0x1, "sparse_super"),
    ("ro_compat", 0x2, "large_file"),
    ("ro_compat", 0x8, "huge_file"),
    ("ro_compat", 0x20, "dir_nlink"),
    ("ro_compat", 0x40, "extra_isize"),
    ("ro_compat", 0x400, "metadata_csum"),
)


class UnreproducibleFeatureSet(ValueError):
    """The host e2fsprogs feature vocabulary cannot reproduce a target mask."""


def _validate_masks(masks: FeatureMasks) -> None:
    if not masks.incompat & _NEEDS_RECOVERY:
        raise UnreproducibleFeatureSet("target incompat mask lacks needs_recovery bit 2")
    known = {field: 0 for field in ("compat", "incompat", "ro_compat")}
    for field, bit, _ in _FEATURES:
        known[field] |= bit
    for field, known_bits in known.items():
        unknown = getattr(masks, field) & ~known_bits
        if unknown:
            bit = unknown & -unknown
            raise UnreproducibleFeatureSet(
                f"unknown {field} feature bit {bit.bit_length() - 1} (0x{bit:x})"
            )


def _mkfs_features(masks: FeatureMasks) -> str:
    _validate_masks(masks)
    return ",".join(
        name if getattr(masks, field) & bit else f"^{name}"
        for field, bit, name in _FEATURES
        if name != "needs_recovery"
    )


def _create_raw_image(path: Path, size_mib: int) -> None:
    if not isinstance(size_mib, int) or isinstance(size_mib, bool) or size_mib <= 0:
        raise ValueError("size_mib must be a positive integer")
    if path.exists() and not path.is_file():
        raise ValueError(f"image path is not a regular file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as image:
        image.truncate(size_mib * 1024 * 1024)


def _read_masks(path: Path) -> FeatureMasks:
    with path.open("rb") as image:
        image.seek(1024 + 0x5C)
        raw = image.read(12)
    if len(raw) != 12:
        raise RuntimeError(f"short ext4 superblock read from {path}")
    return FeatureMasks(
        compat=int.from_bytes(raw[0:4], "little"),
        incompat=int.from_bytes(raw[4:8], "little"),
        ro_compat=int.from_bytes(raw[8:12], "little"),
    )


def build_data_image(
    path: Path, *, size_mib: int, feature_masks: FeatureMasks, mount_options: str
) -> Path:
    """Build one unmounted ext4 image matching the requested feature masks."""
    if not isinstance(mount_options, str) or "\0" in mount_options:
        raise ValueError("mount_options must be a string without NUL bytes")
    image = Path(path)
    features = _mkfs_features(feature_masks)
    _create_raw_image(image, size_mib)
    result = subprocess.run(
        ["mkfs.ext4", "-F", "-O", features, os.fspath(image)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"mkfs.ext4 failed: {result.stderr.strip()}")
    expected = FeatureMasks(
        compat=feature_masks.compat,
        incompat=feature_masks.incompat & ~_NEEDS_RECOVERY,
        ro_compat=feature_masks.ro_compat,
    )
    actual = _read_masks(image)
    if actual != expected:
        raise UnreproducibleFeatureSet(
            f"mkfs.ext4 produced {actual!r}; expected {expected!r} with needs_recovery cleared"
        )
    return image


def build_log_image(path: Path, size_mib: int) -> Path:
    """Build a blank raw log image for dm-log-writes."""
    image = Path(path)
    _create_raw_image(image, size_mib)
    return image


def clone(path: Path) -> Path:
    """Copy an image to a unique, never-mounted replay target."""
    source = Path(path)
    if not source.is_file():
        raise ValueError(f"image path is not a regular file: {source}")
    target = source.with_name(f"{source.name}.clone-{uuid.uuid4().hex}")
    shutil.copyfile(source, target)
    return target
