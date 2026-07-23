from pathlib import Path

import atoms.core


def test_py_typed_marker_present_in_source_package():
    # Necessary-but-not-sufficient: proves the marker sits next to the package
    # source. That it actually ships in the built wheel is proven separately, by
    # building and inspecting the wheel (see the packaging-verification step).
    marker = Path(atoms.core.__file__).with_name("py.typed")
    assert marker.is_file(), "PEP 561 py.typed marker must sit in atoms.core"
