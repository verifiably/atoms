"""Command line entry point for host certification tooling."""

from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

from . import guest, prerequisites
from .images import build_log_image
from .replay import ensure_replay_log


def _self_test() -> int:
    missing = prerequisites.check()
    if missing:
        raise RuntimeError(f"missing certification prerequisites: {', '.join(missing)}")
    with tempfile.TemporaryDirectory(prefix="atoms-certify-host-") as temporary:
        work = Path(temporary)
        initramfs = guest.build_initramfs(work / "init")
        data = build_log_image(work / "data.img", 256)
        log = build_log_image(work / "log.img", 256)
        result = guest.run(
            Path("/boot/vmlinuz-linux"),
            initramfs,
            data,
            log,
            shared_root=Path("/"),
            guest_arguments=("--self-test",),
        )
        print(result.serial, end="")
        if not result.records or result.records[-1] != {"self_test": "ok"}:
            raise guest.GuestRunError("guest did not report a successful self-test")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("check", "build-replay", "self-test"))
    args = parser.parse_args()
    if args.command == "build-replay":
        print(ensure_replay_log(Path(__file__).resolve().parents[2] / ".certify"))
        return 0
    if args.command == "self-test":
        return _self_test()

    missing = prerequisites.check()
    missing_set = set(missing)
    for name in prerequisites.names():
        print(f"{'MISSING' if name in missing_set else 'ok'}: {name}")
    return int(bool(missing))


if __name__ == "__main__":
    raise SystemExit(main())
