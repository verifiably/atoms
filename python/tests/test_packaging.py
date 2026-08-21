from pathlib import Path

import atoms.core


def test_py_typed_marker_present_in_source_package():
    # Necessary-but-not-sufficient: proves the marker sits next to the package
    # source. That it actually ships in the built wheel is proven separately, by
    # building and inspecting the wheel (see the packaging-verification step).
    marker = Path(atoms.core.__file__).with_name("py.typed")
    assert marker.is_file(), "PEP 561 py.typed marker must sit in atoms.core"


def test_py_typed_marker_present_in_fs_package():
    import atoms.fs

    marker = Path(atoms.fs.__file__).with_name("py.typed")
    assert marker.is_file(), "PEP 561 py.typed marker must sit in atoms.fs"


def test_the_coordinator_command_module_exports_the_approved_public_names():
    from atoms.chain.model import Entry as ChainEntry
    from atoms.coordinator import commands

    assert commands.__all__ == (
        "ChainView",
        "Entry",
        "TransactionOutcome",
        "append_intent",
        "read_chain",
        "register_root",
        "run_transaction",
    )
    missing = [name for name in commands.__all__ if not hasattr(commands, name)]
    assert missing == []
    # `Entry` is promoted, not redeclared: the exported union IS the chain model's.
    assert commands.Entry is ChainEntry
