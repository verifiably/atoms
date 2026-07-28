import pytest

from atoms.core.canonical import canonical_bytes, from_canonical_bytes
from atoms.core.compiler import compile_spec
from atoms.core.effects import (
    CreateDirectory,
    CreateFileNoClobber,
    DeletePath,
    MoveNoClobber,
    ReplaceFile,
)
from atoms.core.errors import SpecValidationError
from atoms.core.fingerprint import ABSENT, SymlinkState
from atoms.core.spec import TransactionSpec, build_spec
from tests.support import DIGEST, EMPTY, D, F, G, L


def _spec(initial, final, effects, dependencies=()):
    return build_spec(
        consumer_tag="cnsmr",
        intent_digest=DIGEST,
        initial_surface=initial,
        final_surface=final,
        effects=effects,
        dependencies=dependencies,
    )


def _all_variants_spec():
    return _spec(
        {
            "dir": ABSENT, "repl.txt": F, "new.txt": ABSENT, "empty.txt": ABSENT,
            "del.txt": F, "lnk": L, "src": F, "dst": ABSENT, "café.txt": ABSENT,
        },
        {
            "dir": D, "repl.txt": G, "new.txt": F, "empty.txt": EMPTY,
            "del.txt": ABSENT, "lnk": ABSENT, "src": ABSENT, "dst": F, "café.txt": F,
        },
        (
            CreateDirectory(effect_id="e1", path="dir", post=D),
            ReplaceFile(effect_id="e2", path="repl.txt", pre=F, post=G),
            CreateFileNoClobber(effect_id="e3", path="new.txt", post=F),
            CreateFileNoClobber(effect_id="e4", path="empty.txt", post=EMPTY),
            DeletePath(effect_id="e5", path="del.txt", pre=F),
            DeletePath(effect_id="e6", path="lnk", pre=L),
            MoveNoClobber(effect_id="e7", source="src", destination="dst", source_pre=F),
            CreateFileNoClobber(effect_id="e8", path="café.txt", post=F),
        ),
        [("e1", "e3")],
    )


def _repeated_path_spec():
    return _spec(
        {"p": ABSENT},
        {"p": G},
        (
            CreateFileNoClobber(effect_id="e1", path="p", post=F),
            DeletePath(effect_id="e2", path="p", pre=F),
            CreateFileNoClobber(effect_id="e3", path="p", post=G),
        ),
    )


def _ancestor_change_spec():
    return _spec(
        {"p": F, "p/q": ABSENT},
        {"p": D, "p/q": G},
        (
            DeletePath(effect_id="e1", path="p", pre=F),
            CreateDirectory(effect_id="e2", path="p", post=D),
            CreateFileNoClobber(effect_id="e3", path="p/q", post=G),
        ),
    )


CORPUS = [_all_variants_spec, _repeated_path_spec, _ancestor_change_spec]


# --- totality: nothing accepted can fail downstream ---
#
# The property the UTF-8 rule exists to protect: "compilation succeeded" must mean the
# specification is usable, not merely well-shaped. Three handcrafted specifications
# demonstrate that across the structural variety; they do not establish it. The matrix
# below does the establishing, by driving a corpus of adversarial strings through every
# position where a caller supplies one freely.

@pytest.mark.parametrize("make_spec", CORPUS)
def test_structurally_varied_specs_are_durably_serializable(make_spec):
    compiled = compile_spec(make_spec())
    assert from_canonical_bytes(canonical_bytes(compiled.spec)) == compiled.spec


# Written as escapes, not literal characters: several of these samples are visually
# identical to one another, and the difference between them is the whole point.
UNICODE_SAMPLES = [
    "plain",
    "caf\u00e9",                # precomposed (NFC)
    "cafe\u0301",               # decomposed (NFD) - same text, different code points
    "\u00df",                   # sharp s, whose casefold is longer than itself
    "\u0130",                   # dotted capital I, whose casefold crosses normal forms
    "\U0001f600",               # astral plane, correctly paired
    "\ufeff",                   # zero-width no-break space as an entire name
    "\ufffd",                   # the replacement character, arriving as real content
    "\ufffe",                   # a noncharacter
    "\u202e",                   # right-to-left override
    "\ud800",                   # lone high surrogate - not UTF-8 encodable
    "\udfff",                   # lone low surrogate - not UTF-8 encodable
    "\ud800\udc00",             # a surrogate pair spelled as two lone code points
    "a" * 300,                  # longer than any real NAME_MAX; admitted here (ledger #4)
    " leading and trailing ",
    "tab\tnewline\n",
    ".#nottilde",               # near the reserved scratch sigil without matching it
    "..dotdot",
]


def _site_leaf_path(sample):
    return _spec(
        {sample: ABSENT},
        {sample: F},
        (CreateFileNoClobber(effect_id="e1", path=sample, post=F),),
    )


def _site_directory_component(sample):
    leaf = f"{sample}/leaf.txt"
    return _spec(
        {sample: ABSENT, leaf: ABSENT},
        {sample: D, leaf: F},
        (
            CreateDirectory(effect_id="e1", path=sample, post=D),
            CreateFileNoClobber(effect_id="e2", path=leaf, post=F),
        ),
    )


def _site_move_source(sample):
    return _spec(
        {sample: F, "dst": ABSENT},
        {sample: ABSENT, "dst": F},
        (MoveNoClobber(effect_id="e1", source=sample, destination="dst", source_pre=F),),
    )


def _site_move_destination(sample):
    return _spec(
        {"src": F, sample: ABSENT},
        {"src": ABSENT, sample: F},
        (MoveNoClobber(effect_id="e1", source="src", destination=sample, source_pre=F),),
    )


def _site_symlink_target(sample):
    link = SymlinkState(target=sample, mode=0o777)
    return _spec(
        {"lnk": link},
        {"lnk": ABSENT},
        (DeletePath(effect_id="e1", path="lnk", pre=link),),
    )


def _site_effect_id(sample):
    return _spec(
        {"a.txt": ABSENT},
        {"a.txt": F},
        (CreateFileNoClobber(effect_id=sample, path="a.txt", post=F),),
    )


def _site_consumer_tag(sample):
    base = _all_variants_spec()
    return TransactionSpec(
        schema_version=base.schema_version,
        consumer_tag=sample,
        intent_digest=base.intent_digest,
        initial_surface=base.initial_surface,
        final_surface=base.final_surface,
        effects=base.effects,
        dependencies=base.dependencies,
    )


SITES = [
    _site_leaf_path,
    _site_directory_component,
    _site_move_source,
    _site_move_destination,
    _site_symlink_target,
    _site_effect_id,
    _site_consumer_tag,
]


def _compiles(site, sample) -> bool:
    try:
        compile_spec(site(sample))
    except SpecValidationError:
        return False
    return True


@pytest.mark.parametrize("sample", UNICODE_SAMPLES, ids=lambda s: ascii(s)[:28])
@pytest.mark.parametrize("site", SITES, ids=lambda f: f.__name__)
def test_every_accepted_string_survives_the_durable_format(site, sample):
    # Refusal is a valid outcome at every site. Acceptance is a promise: the value must
    # encode, and decode back to exactly the specification that was compiled.
    try:
        compiled = compile_spec(site(sample))
    except SpecValidationError:
        return
    assert from_canonical_bytes(canonical_bytes(compiled.spec)) == compiled.spec


def test_the_string_corpus_is_not_degenerate():
    # A rule that refused every sample would leave the matrix above passing vacuously.
    # Each site must still admit something, so each site is really exercising acceptance.
    for site in SITES:
        assert any(_compiles(site, sample) for sample in UNICODE_SAMPLES), (
            f"{site.__name__} accepted no sample; the matrix no longer proves anything there"
        )


# --- totality: nothing but SpecValidationError escapes ---

@pytest.mark.parametrize(
    "broken",
    [
        {"schema_version": "one"},
        {"consumer_tag": None},
        {"intent_digest": 12345},
        {"initial_surface": None},
        {"final_surface": 7},
        {"effects": None},
        {"effects": (None,)},
        {"dependencies": "e1->e2"},
    ],
)
def test_only_spec_validation_error_escapes(broken):
    base = _repeated_path_spec()
    fields = {
        "schema_version": base.schema_version,
        "consumer_tag": base.consumer_tag,
        "intent_digest": base.intent_digest,
        "initial_surface": base.initial_surface,
        "final_surface": base.final_surface,
        "effects": base.effects,
        "dependencies": base.dependencies,
    }
    fields.update(broken)
    spec = TransactionSpec(**fields)  # type: ignore[arg-type]
    with pytest.raises(SpecValidationError):
        compile_spec(spec)


def test_a_non_spec_argument_raises_spec_validation_error():
    for value in (None, 42, "spec", [], {}):
        with pytest.raises(SpecValidationError):
            compile_spec(value)  # type: ignore[arg-type]


# --- determinism and idempotence ---

@pytest.mark.parametrize("make_spec", CORPUS)
def test_compilation_is_deterministic(make_spec):
    first = compile_spec(make_spec())
    second = compile_spec(make_spec())
    assert first == second
    assert canonical_bytes(first.spec) == canonical_bytes(second.spec)


@pytest.mark.parametrize("make_spec", CORPUS)
def test_compilation_is_idempotent(make_spec):
    once = compile_spec(make_spec())
    twice = compile_spec(once.spec)
    assert once == twice


def test_canonicalization_sorts_set_like_fields_of_a_directly_built_spec():
    ordered = _all_variants_spec()
    shuffled = TransactionSpec(
        schema_version=ordered.schema_version,
        consumer_tag=ordered.consumer_tag,
        intent_digest=ordered.intent_digest,
        initial_surface=tuple(reversed(ordered.initial_surface)),
        final_surface=tuple(reversed(ordered.final_surface)),
        effects=ordered.effects,  # order is semantic — left as-is
        dependencies=tuple(reversed(ordered.dependencies)),
    )
    assert compile_spec(shuffled) == compile_spec(ordered)


def test_effect_order_is_never_canonicalized_away():
    compiled = compile_spec(_repeated_path_spec())
    assert [e.effect_id for e in compiled.spec.effects] == ["e1", "e2", "e3"]


# --- A1 interoperability ---

@pytest.mark.parametrize("make_spec", CORPUS)
def test_compiled_spec_survives_the_durable_round_trip(make_spec):
    # Fresh-process recovery reconstructs the spec from stored bytes (§8.4).
    compiled = compile_spec(make_spec())
    assert from_canonical_bytes(canonical_bytes(compiled.spec)) == compiled.spec


@pytest.mark.parametrize("make_spec", CORPUS)
def test_a_decoded_spec_recompiles_identically(make_spec):
    compiled = compile_spec(make_spec())
    decoded = from_canonical_bytes(canonical_bytes(compiled.spec))
    assert compile_spec(decoded) == compiled


# --- §13.3 alias conformance ---

def test_scratch_alias_is_refused_in_leaf_and_ancestor_position():
    for path in (".#~leaf", "dir/.#~leaf", ".#~anc/child", "a/.#~anc/child"):
        with pytest.raises(SpecValidationError, match="scratch"):
            compile_spec(_spec(
                {path: ABSENT}, {path: F},
                (CreateFileNoClobber(effect_id="e1", path=path, post=F),),
            ))
