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
    from atoms.chain import inspect as chain_inspect
    from atoms.chain.model import Entry as ChainEntry
    from atoms.coordinator import commands

    assert commands.__all__ == (
        "AbsentChain",
        "ChainDefect",
        "ChainInspection",
        "ChainView",
        "DefectKind",
        "Entry",
        "MalformedChain",
        "TransactionOutcome",
        "WellFormedChain",
        "append_intent",
        "capture_states",
        "inspect_chain",
        "inspect_chain_detached",
        "read_chain",
        "register_root",
        "run_transaction",
    )
    missing = [name for name in commands.__all__ if not hasattr(commands, name)]
    assert missing == []
    # `Entry` is promoted, not redeclared: the exported union IS the chain model's.
    assert commands.Entry is ChainEntry
    # So are the inspection arms: they are re-exported unchanged from the core, never
    # copied, narrowed, or wrapped. Adding a second spelling is how a vocabulary forks.
    for name in ("AbsentChain", "ChainDefect", "DefectKind", "MalformedChain", "WellFormedChain"):
        assert getattr(commands, name) is getattr(chain_inspect, name)
    # `PathState` is deliberately NOT re-exported: it stays `atoms.core.fingerprint`'s,
    # which is how this module's `Entry` re-export leaves the four entry arms behind too.
    assert "PathState" not in commands.__all__
