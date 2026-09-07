#!/usr/bin/env python3
"""Assert wheel and sdist contents before an upload that cannot be taken back.

Adapted from the nodes script of the same name. The assertions are atoms' own: this is a
namespace package with four typed subpackages, so the two failures worth catching are a
missing `py.typed` (the package silently stops being typed for consumers) and a stray
`atoms/__init__.py` (which turns the namespace into a regular package and shadows every
sibling distribution installed beside it).
"""

import glob
import os
import sys
import tarfile
import zipfile

SUBPACKAGES = ("coordinator", "core", "fs", "store")


def fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    sys.exit(1)


def main() -> None:
    if len(sys.argv) != 2:
        fail("usage: verify_python_artifacts.py <dist-dir>")
    dist = sys.argv[1]
    entries = sorted(os.listdir(dist))
    wheels = glob.glob(os.path.join(dist, "verifiably_atoms-*-py3-none-any.whl"))
    sdists = glob.glob(os.path.join(dist, "verifiably_atoms-*.tar.gz"))
    if len(entries) != 2 or len(wheels) != 1 or len(sdists) != 1:
        fail(f"expected exactly one wheel and one sdist, found {entries}")

    with zipfile.ZipFile(wheels[0]) as z:
        names = z.namelist()
        for package in SUBPACKAGES:
            if f"atoms/{package}/py.typed" not in names:
                fail(f"wheel missing atoms/{package}/py.typed")
        if "atoms/__init__.py" in names:
            fail("wheel contains atoms/__init__.py (breaks the namespace)")
        meta_name = next(n for n in names if n.endswith(".dist-info/METADATA"))
        meta = z.read(meta_name).decode()
        required = ["Name: verifiably-atoms", "Import-Namespace: atoms", "License-Expression: MIT"]
        required += [f"Import-Name: atoms.{package}" for package in SUBPACKAGES]
        for line in required:
            if line not in meta:
                fail(f"wheel METADATA missing {line!r}")
        if not any(n.endswith(".dist-info/licenses/LICENSE") for n in names):
            fail("wheel missing dist-info/licenses/LICENSE")

    with tarfile.open(sdists[0]) as t:
        names = t.getnames()
        root = names[0].split("/")[0]
        for required_path in (
            f"{root}/pyproject.toml",
            f"{root}/README.md",
            f"{root}/LICENSE",
            *(f"{root}/src/atoms/{package}/py.typed" for package in SUBPACKAGES),
        ):
            if required_path not in names:
                fail(f"sdist missing {required_path}")
        if f"{root}/src/atoms/__init__.py" in names:
            fail("sdist contains src/atoms/__init__.py (breaks the namespace)")
        member = t.extractfile(f"{root}/PKG-INFO")
        assert member is not None
        pkg_info = member.read().decode()
        for line in ("Name: verifiably-atoms", "License-Expression: MIT"):
            if line not in pkg_info:
                fail(f"sdist PKG-INFO missing {line!r}")

    print("python artifacts ok")


if __name__ == "__main__":
    main()
