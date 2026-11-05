from __future__ import annotations

import os
import re
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_HEADING_RE = re.compile(r"^ {0,3}#{1,6}\s+(.*?)\s*#*\s*$")
_INLINE_CODE_RE = re.compile(r"`+[^`]*`+")
_LINK_RE = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
_MD_LINK_TEXT_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_SLUG_STRIP_RE = re.compile(r"[^a-z0-9 _-]")
_EXTERNAL_PREFIXES = ("http://", "https://", "mailto:")


def heading_slug(title: str) -> str:
    cleaned = _MD_LINK_TEXT_RE.sub(r"\1", title)
    cleaned = cleaned.replace("`", "").strip().lower()
    cleaned = _SLUG_STRIP_RE.sub("", cleaned)
    return cleaned.replace(" ", "-")


def _extract_heading_slugs(markdown_path: Path) -> set[str]:
    slugs: set[str] = set()
    counts: dict[str, int] = {}
    fence: str | None = None
    for raw_line in markdown_path.read_text(encoding="utf-8").splitlines():
        fence_match = _FENCE_RE.match(raw_line)
        if fence_match is not None:
            marker = fence_match.group(1)
            if fence is None:
                fence = marker
            elif raw_line.lstrip().startswith(fence[0] * len(fence)):
                fence = None
            continue
        if fence is not None:
            continue
        heading_match = _HEADING_RE.match(raw_line)
        if heading_match is None:
            continue
        base = heading_slug(heading_match.group(1))
        if not base:
            continue
        seen = counts.get(base, 0)
        counts[base] = seen + 1
        slugs.add(base if seen == 0 else f"{base}-{seen}")
    return slugs


def _tracked_files(root: Path) -> set[str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("GIT_")
    }
    env["GIT_CONFIG_GLOBAL"] = "/dev/null"
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    output = subprocess.check_output(
        ["git", "-C", str(root), "ls-files", "-z"],
        env=env,
        text=True,
    )
    return {entry for entry in output.split("\x00") if entry}


def _candidate_doc_files(root: Path) -> list[Path]:
    candidates: list[Path] = []
    for rel in ("README.md", "AGENTS.md", "python/README.md"):
        path = root / rel
        if path.is_file():
            candidates.append(path)
    docs_dir = root / "docs"
    if docs_dir.is_dir():
        candidates.extend(sorted(p for p in docs_dir.glob("*.md") if p.is_file()))
    return candidates


def check_repository_links(root: Path) -> list[str]:
    root = root.resolve()
    tracked = _tracked_files(root)
    slug_cache: dict[Path, set[str]] = {}
    findings: list[str] = []

    def slugs_for(path: Path) -> set[str]:
        if path not in slug_cache:
            slug_cache[path] = _extract_heading_slugs(path)
        return slug_cache[path]

    for doc_path in _candidate_doc_files(root):
        rel_doc = doc_path.relative_to(root).as_posix()
        fence: str | None = None
        for lineno, raw_line in enumerate(
            doc_path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            fence_match = _FENCE_RE.match(raw_line)
            if fence_match is not None:
                marker = fence_match.group(1)
                if fence is None:
                    fence = marker
                elif raw_line.lstrip().startswith(fence[0] * len(fence)):
                    fence = None
                continue
            if fence is not None:
                continue

            scrubbed_line = _INLINE_CODE_RE.sub("", raw_line)
            for match in _LINK_RE.finditer(scrubbed_line):
                raw_target = match.group(1).strip()
                if not raw_target or raw_target.startswith(_EXTERNAL_PREFIXES):
                    continue
                target_url = raw_target.split()[0]
                if target_url.startswith(_EXTERNAL_PREFIXES):
                    continue

                path_part, sep, anchor = target_url.partition("#")
                if not path_part:
                    target_path = doc_path
                else:
                    target_path = (doc_path.parent / path_part).resolve()

                if not target_path.is_relative_to(root):
                    findings.append(
                        f"{rel_doc}:{lineno}: broken link {target_url} "
                        "(escapes repository root)"
                    )
                    continue
                if not target_path.exists():
                    findings.append(
                        f"{rel_doc}:{lineno}: broken link {target_url} "
                        "(target does not exist)"
                    )
                    continue
                rel_target = target_path.relative_to(root).as_posix()
                if target_path.is_file() and rel_target not in tracked:
                    findings.append(
                        f"{rel_doc}:{lineno}: broken link {target_url} "
                        "(target is not tracked by git)"
                    )
                    continue
                if (
                    sep
                    and target_path.suffix.lower() == ".md"
                    and target_path.is_file()
                    and anchor not in slugs_for(target_path)
                ):
                    findings.append(
                        f"{rel_doc}:{lineno}: broken link {target_url} "
                        f"(missing heading anchor #{anchor})"
                    )
    return findings


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    root = Path(args[0]).resolve() if args else ROOT
    findings = check_repository_links(root)
    if findings:
        for item in findings:
            print(item, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
