"""Killable A7b transaction child used by the crash matrix."""

from __future__ import annotations

import contextlib
import json
import os
import signal
import sqlite3
import sys
from typing import cast

from atoms.coordinator import root
from atoms.coordinator.commands import run_transaction
from atoms.core.errors import TransactionHalted
from atoms.fs.backend import Backend
from atoms.fs.linux import LinuxBackend
from atoms.fs.lock import acquire_project_lock
from atoms.store.connection import Store, _StoreTransaction
from tests.capture_support import DictPayloads, digest_of
from tests.coordinator_child import STORAGE
from tests.coordinator_support import (
    AFTER,
    create_file_spec,
    delete_spec,
    directory_spec,
    move_spec,
    replace_spec,
)
from tests.fs_support import KillingBackend, RecordingBackend, build_test_allowlist


def _spec(name: str):
    return {
        "create": create_file_spec,
        "replace": replace_spec,
        "delete": delete_spec,
        "move": move_spec,
        "mkdir": directory_spec,
    }[name]()


def _payloads(name: str) -> DictPayloads:
    return DictPayloads(
        {digest_of(AFTER): AFTER} if name in {"create", "replace", "mkdir"} else {}
    )


def _configure_store_cut(config: dict, events: list[str]) -> None:
    cut = config.get("store_cut")
    if cut is None:
        return
    seen = 0
    if cut == "after":
        transaction = Store.transaction

        @contextlib.contextmanager
        def after(self):
            nonlocal seen
            with transaction(self) as txn:
                yield txn
            seen += 1
            events.append("commit")
            if seen == config["countdown"]:
                os.kill(os.getpid(), signal.SIGKILL)

        Store.transaction = after
        return

    barrier = _StoreTransaction._run_barrier

    def before(self):
        nonlocal seen
        seen += 1
        events.append("commit")
        if cut == "before" and seen == config["countdown"]:
            os.kill(os.getpid(), signal.SIGKILL)
        return barrier(self)

    _StoreTransaction._run_barrier = before


def main(project_root: str, metadata_root: str) -> int:
    config = json.loads(os.environ["ATOMS_EXECUTE_CONFIG"])
    raw = LinuxBackend()
    events: list[str] = []
    with acquire_project_lock(raw, metadata_root) as probe:
        root.CERTIFIED_ALLOWLIST = build_test_allowlist(
            probe, project_root, STORAGE
        )
    if config.get("recover"):
        halted = False
        try:
            with root._recovery_lease(
                raw, project_root, metadata_root, STORAGE
            ):
                pass
        except TransactionHalted:
            halted = True
        with sqlite3.connect(os.path.join(metadata_root, "atoms.db")) as connection:
            row = connection.execute(
                "SELECT state, halt_diagnostic FROM transaction_record "
                "WHERE txid = (SELECT txid FROM active)"
            ).fetchone()
        print(json.dumps({"halted": halted, "active": row}))
        return 0
    if "umask" in config:
        from atoms.coordinator import execute

        apply_directory = execute.create_directory.apply

        def under_umask(*args, **kwargs):
            previous = os.umask(config["umask"])
            try:
                return apply_directory(*args, **kwargs)
            finally:
                os.umask(previous)

        execute.create_directory.apply = under_umask
    recorder = None
    if config.get("record"):
        recorder = RecordingBackend(raw)
        backend = cast(Backend, recorder)
    elif "method" in config:
        backend = cast(
            Backend,
            KillingBackend(
                raw, method=config["method"], countdown=config["countdown"]
            ),
        )
    else:
        backend = raw
    _configure_store_cut(config, events)
    outcome = run_transaction(
        backend,
        project_root,
        metadata_root,
        STORAGE,
        _spec(config["variant"]),
        _payloads(config["variant"]),
    )
    if recorder is not None:
        events = recorder.events
    print(json.dumps({"outcome": outcome.outcome.value, "events": events}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:]))
