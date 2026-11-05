from __future__ import annotations

import os
import subprocess
from pathlib import Path

from tools.check_doc_links import check_repository_links, heading_slug, main

ROOT = Path(__file__).parents[2]


def _git_env(home: Path) -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("GIT_")
    }
    env.update(
        {
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(home / ".config"),
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "test@example.com",
        }
    )
    return env


def _init_git_repo(repo: Path, tracked_files: dict[str, str]) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    env = _git_env(repo.parent)
    subprocess.run(["git", "-C", str(repo), "init", "-q"], env=env, check=True)
    top = Path(
        subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "--show-toplevel"],
            env=env,
            text=True,
        ).strip()
    ).resolve()
    git_dir = Path(
        subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "--absolute-git-dir"],
            env=env,
            text=True,
        ).strip()
    ).resolve()
    assert top == repo.resolve()
    assert git_dir.is_relative_to(repo.resolve())

    for rel, content in tracked_files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    if tracked_files:
        subprocess.run(
            ["git", "-C", str(repo), "add", "--", *tracked_files.keys()],
            env=env,
            check=True,
        )


def test_valid_relative_file_and_anchor_links_pass(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_git_repo(
        repo,
        {
            "README.md": (
                "# Project\n\n"
                "See [records](docs/records.md#roots-and-reserved-names), "
                "[repeat](docs/records.md#notes-1), and [self](#project).\n"
            ),
            "AGENTS.md": "# Agents\n\nSee [README](README.md#project).\n",
            "python/README.md": "# Python\n\nSee [records](../docs/records.md).\n",
            "docs/records.md": (
                "# Records\n\n"
                "## `Roots` and reserved names\n\n"
                "## Notes\n\n"
                "## Notes\n"
            ),
        },
    )
    assert heading_slug("`Roots` and reserved names") == "roots-and-reserved-names"
    assert check_repository_links(repo) == []
    assert main([str(repo)]) == 0


def test_missing_target_file_is_reported(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_git_repo(
        repo,
        {
            "README.md": "# Project\n\nBroken [link](docs/missing.md).\n",
        },
    )
    findings = check_repository_links(repo)
    assert len(findings) == 1
    assert "README.md:3:" in findings[0]
    assert "docs/missing.md" in findings[0]
    assert "does not exist" in findings[0]
    assert main([str(repo)]) == 1


def test_untracked_target_file_is_reported(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_git_repo(
        repo,
        {
            "README.md": "# Project\n\nUntracked [target](docs/untracked.md).\n",
        },
    )
    untracked = repo / "docs" / "untracked.md"
    untracked.parent.mkdir(parents=True, exist_ok=True)
    untracked.write_text("# Untracked\n", encoding="utf-8")

    findings = check_repository_links(repo)
    assert len(findings) == 1
    assert "README.md:3:" in findings[0]
    assert "docs/untracked.md" in findings[0]
    assert "target is not tracked by git" in findings[0]


def test_missing_heading_anchor_is_reported(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_git_repo(
        repo,
        {
            "README.md": (
                "# Project\n\n"
                "Bad [same](#no-such-heading) and [cross](docs/records.md#absent).\n"
            ),
            "docs/records.md": "# Records\n\n## Present\n",
        },
    )
    findings = check_repository_links(repo)
    assert len(findings) == 2
    assert any("#no-such-heading" in item for item in findings)
    assert any("docs/records.md#absent" in item for item in findings)


def test_links_inside_fenced_code_blocks_and_external_urls_are_ignored(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _init_git_repo(
        repo,
        {
            "README.md": (
                "# Project\n\n"
                "External [web](https://example.com/missing.md) and "
                "[mail](mailto:test@example.com).\n\n"
                "```markdown\n"
                "Ignored [fenced](docs/nonexistent.md#nope)\n"
                "```\n\n"
                "~~~markdown\n"
                "Ignored [tilde](docs/also-missing.md)\n"
                "~~~\n\n"
                "Inline `[code](docs/not-a-link.md)` is also ignored.\n"
            ),
        },
    )
    assert check_repository_links(repo) == []


def test_repository_public_docs_have_no_broken_links() -> None:
    assert check_repository_links(ROOT) == []


def test_inline_code_link_label_is_checked(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_git_repo(
        repo,
        {
            "README.md": "# Title\n\nSee [`MissingSymbol`](docs/missing.md) and `[ignored](docs/ignored.md)`.\n",
        },
    )
    errors = check_repository_links(repo)
    assert len(errors) == 1
    assert "docs/missing.md" in errors[0]
