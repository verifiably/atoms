from __future__ import annotations

import json

from tools.certify import guest_init


def test_recovery_child_uses_identity_verified_parent(monkeypatch, capsys) -> None:
    monkeypatch.setattr(guest_init.sys, "argv", ["guest_init", "--recover", "/volume"])
    monkeypatch.setattr(
        guest_init,
        "verify_identity",
        lambda _parameters: (_ for _ in ()).throw(AssertionError("redundant identity scan")),
    )
    monkeypatch.setattr(
        guest_init,
        "_recover_cell",
        lambda volume: {"classified": int(str(volume) == "/volume"), "violations": []},
    )

    assert guest_init.main() == 0
    assert json.loads(capsys.readouterr().out) == {"classified": 1, "violations": []}
