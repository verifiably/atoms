"""Command line entry point for host certification tooling."""

from __future__ import annotations

import argparse

from . import prerequisites


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("check",))
    args = parser.parse_args()
    if args.command != "check":  # pragma: no cover - argparse owns this boundary.
        raise AssertionError("unreachable command")

    missing = prerequisites.check()
    missing_set = set(missing)
    for name in prerequisites.names():
        print(f"{'MISSING' if name in missing_set else 'ok'}: {name}")
    return int(bool(missing))


if __name__ == "__main__":
    raise SystemExit(main())
