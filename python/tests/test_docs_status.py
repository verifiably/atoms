"""One roadmap fact, derived everywhere -- the corpus-wide status guard.

Four hand-written guards preceded this file, one per sub-plan, each freezing the
roadmap of its own day in an exact-equality assertion. Two of them ended up
*enforcing* claims the next sub-plan falsified: `test_a4a_...` pinned "A4b and A5-A8
remain unimplemented" and `test_a4b_...` pinned "A5-A8 remain unimplemented" long
after A4b, A5, and A6 had landed, so correcting either header broke the suite. A
guard whose expected value is a copy of the text it guards cannot outlive that text,
and the third guard's comment asserted A5a's design "carries no Status line" when it
carried the most wrong one in the corpus.

So this file states the roadmap once, in `STAGES` and `IMPLEMENTED`, and derives
every check from it. Landing a sub-plan means moving one boundary here; the guard
then names every document that still disagrees.

**Scope is each document's status claim, never its whole body.** A design records
amendments by quoting the wording it replaced, and A6 §3.3 does exactly that. A
whole-file scan would conflate the claim with the record of the claim changing, and
the only ways to green it would be to delete the record or stop guarding the file.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parents[2]
PLANS = ROOT / "docs" / "plans"

#: Every sub-plan of authority §14's Plan A roadmap, in delivery order.
STAGES = ("A1", "A2", "A3", "A4a", "A4b", "A5a", "A5b", "A6", "A7", "A8", "A9")

#: The one fact this file exists to hold. Landing a sub-plan moves this boundary.
FIRST_UNIMPLEMENTED = "A7"

IMPLEMENTED = STAGES[: STAGES.index(FIRST_UNIMPLEMENTED)]
UNIMPLEMENTED = STAGES[STAGES.index(FIRST_UNIMPLEMENTED) :]
LAST_IMPLEMENTED = IMPLEMENTED[-1]

#: Retained as the historical record of the review that hardened the contracts; its
#: own header says so, and its roadmap claims are deliberately frozen.
SUPERSEDED = "2026-07-20-"

AUTHORITY = PLANS / "2026-07-23-recoverable-fs-effect-engine-design.md"

_LABEL = r"A[1-9](?:[ab](?:-[12])?)?"
# No ASCII hyphen: it is part of a label (`A4b-2`), never a connector between two.
_CONNECTOR = r"\s*(?:[–—,]|and)\s*"
_SPAN = re.compile(rf"{_LABEL}(?:{_CONNECTOR}{_LABEL})*")
_CLAIM = re.compile(r"\b(?:remain|remains|unimplemented)\b")
# A roadmap bullet declaring one sub-plan done. `\b` before "implemented" is what keeps
# "remain unimplemented" from reading as a declaration that it is.
_BULLET = re.compile(rf"\*\*({_LABEL}) —[^*]*?\bimplemented")

# An ASCII-hyphen range ("A5-A8") would parse as two unrelated labels and silently
# drop the stages between them. The corpus spells ranges with an en dash; assert it
# rather than half-supporting the other spelling.
_ASCII_RANGE = re.compile(r"A[1-9][ab]?-A[1-9]")


def _stages_of(label: str) -> tuple[str, ...]:
    """The stages one label covers. `A4b-2` is a stage of A4b; `A4` is both A4a and A4b."""
    base = label.split("-")[0]
    if base in STAGES:
        return (base,)
    covered = tuple(stage for stage in STAGES if stage.startswith(base))
    assert covered, f"unknown stage label {label!r}"
    return covered


def _expand(span: str) -> set[str]:
    """The stages a label span names, resolving en-dash ranges inclusively."""
    parts = re.split(rf"({_CONNECTOR})", span)
    labels, separators = parts[0::2], [part.strip() for part in parts[1::2]]
    covered: set[str] = set()
    for index, label in enumerate(labels):
        covered.update(_stages_of(label))
        if index < len(separators) and separators[index] in "–—":
            left = STAGES.index(_stages_of(label)[0])
            right = STAGES.index(_stages_of(labels[index + 1])[-1])
            covered.update(STAGES[left : right + 1])
    return covered


def unimplemented_claims(region: str) -> set[str]:
    """Every stage this region claims is not implemented.

    A span owns the text after it up to the sentence end or the next span, whichever
    comes first: "A5 and A6 are implemented; A7-A8 remain unimplemented" must attach
    the claim to A7-A8 and not to A5.
    """
    assert not _ASCII_RANGE.search(region), f"ASCII-hyphen stage range in {region!r}"
    spans = list(_SPAN.finditer(region))
    claimed: set[str] = set()
    for index, span in enumerate(spans):
        sentence_end = region.find(".", span.end())
        limit = len(region) if sentence_end == -1 else sentence_end
        if index + 1 < len(spans):
            limit = min(limit, spans[index + 1].start())
        if _CLAIM.search(region[span.end() : limit]):
            claimed |= _expand(span.group())
    return claimed


def _flat(lines: list[str]) -> str:
    return " ".join(" ".join(lines).split())


def status_field(text: str) -> str | None:
    """A document's `**Status:**` header field, or None if it declares no status.

    Bounded by a blank line or the next `**Field:**`, and flattened, because these
    headers wrap across lines and a reflow must not silently disable the guard.
    """
    lines = text.splitlines()
    start = next(
        (index for index, line in enumerate(lines) if line.startswith("**Status:**")),
        None,
    )
    if start is None:
        return None
    field = [lines[start]]
    for line in lines[start + 1 :]:
        if not line.strip() or re.match(r"\*\*[A-Za-z][^*]*:\*\*", line):
            break
        field.append(line)
    return _flat(field)


def status_section(text: str) -> str:
    """The `## Status...` heading through the next `## ` heading, flattened."""
    lines = text.splitlines()
    start = next(index for index, line in enumerate(lines) if line.startswith("## Status"))
    end = next(
        (index for index in range(start + 1, len(lines)) if lines[index].startswith("## ")),
        len(lines),
    )
    return _flat(lines[start:end])


def live_plan_documents() -> list[Path]:
    return sorted(path for path in PLANS.glob("*.md") if SUPERSEDED not in path.name)


def _documented_stage(path: Path) -> str | None:
    """The stage a plan or design filename names, normalized onto `STAGES`."""
    match = re.search(r"-(?:plan-)?(a[1-9][ab]?)[12]?-", path.name)
    if match is None:
        return None
    return match.group(1).replace("a", "A", 1)


def status_regions() -> dict[str, str]:
    """Every live status claim in the repository, keyed by a readable source name."""
    regions = {
        "AGENTS.md": status_section((ROOT / "AGENTS.md").read_text(encoding="utf-8")),
        "README.md": status_section((ROOT / "README.md").read_text(encoding="utf-8")),
    }
    for path in live_plan_documents():
        field = status_field(path.read_text(encoding="utf-8"))
        if field is not None:
            regions[path.name] = field
    return regions


def test_no_live_document_calls_an_implemented_sub_plan_unimplemented():
    stale = {
        name: sorted(unimplemented_claims(region) & set(IMPLEMENTED))
        for name, region in status_regions().items()
    }
    assert {name: claims for name, claims in stale.items() if claims} == {}


def test_every_implemented_sub_plan_has_a_document_declaring_it_implemented():
    declared = {
        _documented_stage(path)
        for path in live_plan_documents()
        if (status_field(path.read_text(encoding="utf-8")) or "").startswith(
            "**Status:** Implemented"
        )
    }
    assert set(IMPLEMENTED) - declared == set()


def test_every_design_document_declares_a_status():
    silent = [
        path.name
        for path in live_plan_documents()
        if path.name.endswith("-design.md")
        and status_field(path.read_text(encoding="utf-8")) is None
    ]
    assert silent == []


def test_the_roadmap_boundary_is_stated_where_the_reader_looks_first():
    """Silence about A7-A9 reads as completeness. Both entry documents must say it."""
    for name in ("AGENTS.md", "README.md"):
        region = status_regions()[name]
        assert unimplemented_claims(region) == set(UNIMPLEMENTED), name
        assert LAST_IMPLEMENTED in region, name


def test_the_entry_documents_list_every_implemented_sub_plan():
    """The roadmap lists in AGENTS.md and README.md are the reader's map of what exists.

    README groups A4 and A5 as families where AGENTS splits A4a/A4b, so the label a
    bullet carries is expanded rather than matched stage-for-stage.
    """
    for name in ("AGENTS.md", "README.md"):
        listed: set[str] = set()
        for label in _BULLET.findall(status_regions()[name]):
            listed.update(_stages_of(label))
        assert listed == set(IMPLEMENTED), name


def test_no_status_region_claims_the_repository_writes_nothing():
    """A4a, A5a, and A6 all write under `metadata_root`. Only project paths are untouched."""
    for name, region in status_regions().items():
        assert "no filesystem mutation code has landed" not in region, name
        assert "mutates a filesystem path" not in region, name


def test_the_authority_header_names_the_implemented_prefix():
    """The authority outranks every other document, and spells its remainder its own way.

    It says "A1-A6 are implemented ... A7-A9 (...) remain" rather than the sentence the
    sub-plans share, so the shared parse is backed up by a positive check here.
    """
    field = status_field(AUTHORITY.read_text(encoding="utf-8"))
    assert field is not None
    assert f"A1–{LAST_IMPLEMENTED} are implemented" in field
    assert unimplemented_claims(field) == set(UNIMPLEMENTED)


def test_the_claim_parser_attaches_a_claim_to_the_span_that_owns_it():
    """The parse this file rests on, against the shapes the corpus actually uses."""
    assert unimplemented_claims("A5 and A6 are implemented; A7–A8 remain unimplemented.") == {
        "A7",
        "A8",
    }
    assert unimplemented_claims(
        "A7–A8 (effect/recovery execution, synthetic exerciser) remain."
    ) == {"A7", "A8"}
    assert unimplemented_claims("A4b-2 and A5–A8 remain unimplemented.") == {
        "A4b",
        "A5a",
        "A5b",
        "A6",
        "A7",
        "A8",
    }
    # A requirement about a stage is not a claim that the stage is unimplemented.
    assert unimplemented_claims("A4b must produce the proof before A5–A8.") == set()
    # The retired spellings this sweep removed, which must parse as failures.
    assert unimplemented_claims("A4–A8 remain unimplemented.") & set(IMPLEMENTED)
    assert unimplemented_claims("A4b and A5–A8 remain unimplemented.") & set(IMPLEMENTED)
