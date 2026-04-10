"""
AST-level expansion tests for the new expansion API.

These tests exercise expand_to_word_simple (word IR) and expand_simple (command context)
without using the parser. PreSplitWord semantics are covered in test_expansion_new.py.
"""

import math

import pytest
import shasta.ast_node as AST

import sash.reporter as reporter
from sash.constraints import Not, StringEq
from sash.symb import (
    expand,
    expand_args,
    expand_args_dumb,
    expand_simple,
    expand_to_word,
    expand_to_word_simple,
    starting_state,
)
from sash.frozen import FrozenAst, freeze
from sash.interpreter_config import InterpConfig, UnboundVariablePolicy
from sash.symbolic.state import ShellVar, Trace
from sash.symbolic.strings import (
    ArbitraryType,
    CompletelyArbitrary,
    ExpandedChunk,
    Field,
    LiteralChunk,
    PreSplitWord,
    SymStr,
    WordCount,
)

DEFAULT_IFS = " \t\n"


def lit(text: str) -> list[AST.ArgChar]:
    return [AST.CArgChar(ord(ch)) for ch in text]


def q(argchars: list[AST.ArgChar]) -> list[AST.ArgChar]:
    return [AST.QArgChar(argchars)]


def var(
    name: str,
    fmt: str = "Normal",
    null: bool = False,
    arg: list[AST.ArgChar] | None = None,
) -> list[AST.ArgChar]:
    return [AST.VArgChar(fmt=fmt, null=null, var=name, arg=arg or [])]


def tilde(token: str | None = None) -> list[AST.ArgChar]:
    if token is None:
        return [AST.TArgChar("None")]
    return [AST.TArgChar(("Some", token))]


def param(
    name: str, fmt: str, null: bool = False, arg_text: str | None = ""
) -> list[AST.ArgChar]:
    argchars = [] if arg_text is None else lit(arg_text)
    return var(name, fmt=fmt, null=null, arg=argchars)


def word(*parts: list[AST.ArgChar]) -> list[AST.ArgChar]:
    return [ch for part in parts for ch in part]


def literal_word(text: str, quoted: bool = False) -> PreSplitWord:
    return PreSplitWord([LiteralChunk(text, is_quoted=quoted)])


def expanded_word(
    content: str | CompletelyArbitrary,
    quoted: bool = False,
    count: WordCount | None = None,
) -> PreSplitWord:
    return PreSplitWord(
        [
            ExpandedChunk(
                content=content,
                is_quoted=quoted,
                count=count or WordCount(1, 1),
            )
        ]
    )


def stored_literal(text: str) -> PreSplitWord:
    return literal_word(text, quoted=False).prepare_for_storage()


def stored_arbitrary(
    name: str, min_words: int = 0, max_words: int | float = math.inf
) -> PreSplitWord:
    var_ast = AST.VArgChar(fmt="Normal", null=False, var=name, arg=[])
    arbitrary = CompletelyArbitrary(freeze(var_ast), ArbitraryType.ENVIRONMENT, None)
    return expanded_word(
        arbitrary, quoted=False, count=WordCount(min_words, max_words)
    ).prepare_for_storage()


def make_state(
    env: dict[str, PreSplitWord] | None = None, ifs_value: str | None = DEFAULT_IFS
):
    state = starting_state()
    if ifs_value is not None:
        state = state.set_env("IFS", ShellVar(stored_literal(ifs_value)))
    else:
        state = state.unset_env("IFS")
    if env:
        for name, value in env.items():
            state = state.set_env(name, ShellVar(value))
    return state


def field_texts(fields: list[Field]) -> list[str]:
    texts = []
    for field in fields:
        if isinstance(field.content, SymStr):
            texts.append(field.content.try_to_str() or "")
        else:
            texts.append("<arb>")
    return texts


def assert_symstr_field(
    field: Field, text: str, min_words: int, max_words: int | float
) -> None:
    assert isinstance(field.content, SymStr)
    assert field.content.try_to_str() == text
    assert field.count == WordCount(min_words, max_words)


def assert_arbitrary_field(
    field: Field,
    kind: ArbitraryType,
    min_words: int,
    max_words: int | float,
    prefix: str | None = None,
    suffix: str | None = None,
    quoted: bool | None = None,
) -> None:
    assert isinstance(field.content, CompletelyArbitrary)
    assert field.content.kind == kind
    assert field.count == WordCount(min_words, max_words)
    if prefix is not None:
        assert field.content.prefix == SymStr((prefix,))
    if suffix is not None:
        assert field.content.suffix == SymStr((suffix,))
    if quoted is not None:
        assert field.content.quoted == quoted


def expand_simple_one(
    stuff: list[AST.ArgChar], state, config: InterpConfig
) -> list[Field]:
    expansions = expand_simple(stuff, state, config)
    assert len(expansions) == 1
    return expansions[0][0]


def expand_command(
    args: list[list[AST.ArgChar]], state, config: InterpConfig
) -> list[Field]:
    expanded: list[Field] = []
    for arg in args:
        expanded.extend(expand_simple_one(arg, state, config))
    return expanded


def word_signature(word_obj: PreSplitWord) -> tuple:
    if len(word_obj.chunks) == 1:
        chunk = word_obj.chunks[0]
        if isinstance(chunk, LiteralChunk):
            return ("lit", chunk.content, chunk.is_quoted)
        if isinstance(chunk, ExpandedChunk) and isinstance(
            chunk.content, CompletelyArbitrary
        ):
            return (
                "arb",
                chunk.content.kind,
                chunk.count.min,
                chunk.count.max,
                chunk.is_quoted,
            )
        if isinstance(chunk, ExpandedChunk):
            return (
                "exp",
                chunk.content,
                chunk.is_quoted,
                chunk.count.min,
                chunk.count.max,
            )
    return ("complex", tuple(word_obj.chunks))


def _arbitrary_var_name(content: CompletelyArbitrary) -> str | None:
    source = content.source
    if isinstance(source, FrozenAst) and source.kind == "VArgChar":
        return dict(source.fields).get("var")
    return None


def _field_var_name(field: Field) -> str | None:
    if isinstance(field.content, CompletelyArbitrary):
        return _arbitrary_var_name(field.content)
    return None


def _is_empty_field(field: Field) -> bool:
    return field.try_to_str() == ""


def _has_empty_constraint(state, var_name: str) -> bool:
    for cond in state.pathcond:
        match cond.constraint:
            case StringEq(lhs, rhs):
                if _field_var_name(lhs) == var_name and _is_empty_field(rhs):
                    return True
                if _field_var_name(rhs) == var_name and _is_empty_field(lhs):
                    return True
    return False


def _has_non_empty_constraint(state, var_name: str) -> bool:
    for cond in state.pathcond:
        match cond.constraint:
            case Not(StringEq(lhs, rhs)):
                if _field_var_name(lhs) == var_name and _is_empty_field(rhs):
                    return True
                if _field_var_name(rhs) == var_name and _is_empty_field(lhs):
                    return True
    return False


@pytest.fixture(autouse=True)
def _reset_reporter():
    reporter.Reporter.reset()
    reporter.Reporter.initialize("<test>")


@pytest.fixture()
def config() -> InterpConfig:
    return InterpConfig()


@pytest.fixture()
def base_state():
    return make_state(
        env={
            "VAR": stored_literal("x y"),
            "EMPTY": stored_literal(""),
            "SYM": stored_arbitrary("SYM", 0, math.inf),
            "GLOB": stored_literal("*.txt"),
        },
    )


# expand_to_word_simple should emit LiteralChunk/ExpandedChunk sequences with correct quoting for literals and vars.
@pytest.mark.parametrize(
    "argchars, expected_chunks",
    [
        (lit("echo"), [LiteralChunk("echo", is_quoted=False)]),
        (q(lit("a b")), [LiteralChunk("a b", is_quoted=True)]),
        (var("VAR"), [ExpandedChunk("x y", is_quoted=False, count=WordCount(1, 1))]),
        (q(var("VAR")), [ExpandedChunk("x y", is_quoted=True, count=WordCount(1, 1))]),
        (
            word(lit("pre_"), var("VAR"), lit("_suf")),
            [
                LiteralChunk("pre_", is_quoted=False),
                ExpandedChunk("x y", is_quoted=False, count=WordCount(1, 1)),
                LiteralChunk("_suf", is_quoted=False),
            ],
        ),
        (
            word(lit("pre_"), q(var("VAR")), lit("_suf")),
            [
                LiteralChunk("pre_", is_quoted=False),
                ExpandedChunk("x y", is_quoted=True, count=WordCount(1, 1)),
                LiteralChunk("_suf", is_quoted=False),
            ],
        ),
    ],
)
def test_expand_to_word_simple_literals_and_vars(
    argchars, expected_chunks, base_state, config
):
    expansions = expand_to_word_simple(argchars, base_state, config)
    assert len(expansions) == 1
    word_obj, _ = expansions[0]
    assert word_obj.chunks == expected_chunks


# Unbound $VAR should produce a single arbitrary ExpandedChunk with ENVIRONMENT kind and [0, inf] count.
def test_expand_to_word_simple_unbound_var_symbolic(config):
    state = make_state()
    expansions = expand_to_word_simple(var("MISSING"), state, config)
    assert len(expansions) == 1
    word_obj, _ = expansions[0]
    assert len(word_obj.chunks) == 1
    chunk = word_obj.chunks[0]
    assert isinstance(chunk, ExpandedChunk)
    assert isinstance(chunk.content, CompletelyArbitrary)
    assert chunk.content.kind == ArbitraryType.ENVIRONMENT
    assert chunk.count == WordCount(0, math.inf)


# ${A:-default} with A unset should fork to default literal or env arbitrary.
def test_expand_to_word_simple_minus_default_unset_forks(config):
    state = make_state()
    argchars = param("A", fmt="Minus", null=True, arg_text="default")
    expansions = expand_to_word_simple(argchars, state, config)
    signatures = {word_signature(word_obj) for word_obj, _ in expansions}
    assert signatures == {
        ("exp", "default", False, 1, 1),
        ("arb", ArbitraryType.ENVIRONMENT, 0, math.inf, False),
    }


# ${A:-default} with A arbitrary should fork and constrain A in each branch.
def test_expand_to_word_simple_minus_default_arbitrary_forks_and_constrains(config):
    state = make_state(env={"A": stored_arbitrary("A", 0, math.inf)})
    argchars = param("A", fmt="Minus", null=True, arg_text="default")
    expansions = expand_to_word_simple(argchars, state, config)
    assert len(expansions) == 2
    saw_default = False
    saw_non_default = False
    for word_obj, next_state in expansions:
        if word_signature(word_obj) == ("exp", "default", False, 1, 1):
            saw_default = True
            assert _has_empty_constraint(next_state, "A")
        else:
            saw_non_default = True
            assert _has_non_empty_constraint(next_state, "A")
    assert saw_default and saw_non_default


# Inner quotes inside ${VAR:-"a b"} should be honored even when the outer word is unquoted.
def test_expand_simple_param_default_inner_quotes(config):
    config = InterpConfig(unbound_policy=UnboundVariablePolicy.EMPTY)
    state = make_state()
    argchars = var("VAR", fmt="Minus", null=True, arg=q(lit("a b")))
    fields = expand_command([lit("echo"), argchars], state, config)
    assert field_texts(fields) == ["echo", "a b"]


# ${A:-default} with A set but empty should use the default word.
def test_expand_to_word_simple_minus_default_empty_uses_default(config):
    state = make_state(env={"A": stored_literal("")})
    argchars = param("A", fmt="Minus", null=True, arg_text="default")
    expansions = expand_to_word_simple(argchars, state, config)
    assert {word_signature(word_obj) for word_obj, _ in expansions} == {
        ("exp", "default", False, 1, 1)
    }


# ${A:-default} with A non-empty should return A unchanged.
def test_expand_to_word_simple_minus_default_nonempty_uses_value(config):
    state = make_state(env={"A": stored_literal("value")})
    argchars = param("A", fmt="Minus", null=True, arg_text="default")
    expansions = expand_to_word_simple(argchars, state, config)
    assert {word_signature(word_obj) for word_obj, _ in expansions} == {
        ("exp", "value", False, 1, 1)
    }


# ${A-default} with A empty but set should keep the empty value (no colon behavior).
def test_expand_to_word_simple_minus_no_colon_empty_keeps_empty(config):
    state = make_state(env={"A": stored_literal("")})
    argchars = param("A", fmt="Minus", null=False, arg_text="default")
    expansions = expand_to_word_simple(argchars, state, config)
    assert {word_signature(word_obj) for word_obj, _ in expansions} == {
        ("exp", "", False, 1, 1)
    }


# ${A:+alt} with A empty should yield an empty word (0 fields).
def test_expand_to_word_simple_plus_colon_empty_drops_word(config):
    state = make_state(env={"A": stored_literal("")})
    argchars = param("A", fmt="Plus", null=True, arg_text="alt")
    expansions = expand_to_word_simple(argchars, state, config)
    assert {word_signature(word_obj) for word_obj, _ in expansions} == {
        ("exp", "", False, 0, 0)
    }


# ${A:+alt} with A non-empty should expand to the alt word.
def test_expand_to_word_simple_plus_colon_nonempty_uses_word(config):
    state = make_state(env={"A": stored_literal("value")})
    argchars = param("A", fmt="Plus", null=True, arg_text="alt")
    expansions = expand_to_word_simple(argchars, state, config)
    assert {word_signature(word_obj) for word_obj, _ in expansions} == {
        ("exp", "alt", False, 1, 1)
    }


# ${A+alt} with A set (even empty) should expand to alt.
def test_expand_to_word_simple_plus_no_colon_empty_uses_word(config):
    state = make_state(env={"A": stored_literal("")})
    argchars = param("A", fmt="Plus", null=False, arg_text="alt")
    expansions = expand_to_word_simple(argchars, state, config)
    assert {word_signature(word_obj) for word_obj, _ in expansions} == {
        ("exp", "alt", False, 1, 1)
    }


# ${A+alt} with A unset should expand to empty (0 fields).
def test_expand_to_word_simple_plus_no_colon_unset_empty(config):
    state = make_state()
    argchars = param("A", fmt="Plus", null=False, arg_text="alt")
    expansions = expand_to_word_simple(argchars, state, config)
    assert {word_signature(word_obj) for word_obj, _ in expansions} == {
        ("exp", "", False, 0, 0)
    }


# ${A:?err} with A unset should force a non-empty symbolic expansion to continue.
def test_expand_to_word_simple_question_unset_symbolic_nonempty(config):
    state = make_state()
    argchars = param("A", fmt="Question", null=True, arg_text="err")
    expansions = expand_to_word_simple(argchars, state, config)
    assert {word_signature(word_obj) for word_obj, _ in expansions} == {
        ("arb", ArbitraryType.ENVIRONMENT, 1, math.inf, False)
    }


# ${A:?err} with A empty should terminate the trace and yield no chunks.
def test_expand_to_word_simple_question_empty_terminates(config):
    state = make_state(env={"A": stored_literal("")})
    argchars = param("A", fmt="Question", null=True, arg_text="err")
    expansions = expand_to_word_simple(argchars, state, config)
    assert len(expansions) == 1
    word_obj, next_state = expansions[0]
    assert word_obj.chunks == []
    assert next_state.terminated


# ${A:=default} with A unset should assign default and return it.
def test_expand_to_word_simple_assign_unset_sets_value(config):
    state = make_state()
    argchars = param("A", fmt="Assign", null=True, arg_text="default")
    expansions = expand_to_word_simple(argchars, state, config)
    assert len(expansions) == 1
    word_obj, next_state = expansions[0]
    assert word_signature(word_obj) == ("exp", "default", False, 1, 1)
    assert next_state.lookup("A").value == stored_literal("default")


# ${A:=default} with A empty should assign default and return it.
def test_expand_to_word_simple_assign_empty_sets_value(config):
    state = make_state(env={"A": stored_literal("")})
    argchars = param("A", fmt="Assign", null=True, arg_text="default")
    expansions = expand_to_word_simple(argchars, state, config)
    assert len(expansions) == 1
    word_obj, next_state = expansions[0]
    assert word_signature(word_obj) == ("exp", "default", False, 1, 1)
    assert next_state.lookup("A").value == stored_literal("default")


# ${A:=default} with A non-empty should keep the original value.
def test_expand_to_word_simple_assign_nonempty_preserves_value(config):
    state = make_state(env={"A": stored_literal("value")})
    argchars = param("A", fmt="Assign", null=True, arg_text="default")
    expansions = expand_to_word_simple(argchars, state, config)
    assert len(expansions) == 1
    word_obj, next_state = expansions[0]
    assert word_signature(word_obj) == ("exp", "value", False, 1, 1)
    assert next_state.lookup("A").value == stored_literal("value")


# ${#A} should yield the constant length for a literal value.
def test_expand_to_word_simple_length_constant(config):
    state = make_state(env={"A": stored_literal("abc")})
    argchars = param("A", fmt="Length", null=False, arg_text=None)
    expansions = expand_to_word_simple(argchars, state, config)
    assert len(expansions) == 1
    word_obj, _ = expansions[0]
    assert word_signature(word_obj) == ("exp", "3", False, 1, 1)


# ${#A} for symbolic A should return an approximate non-empty length.
def test_expand_to_word_simple_length_arbitrary(config):
    state = make_state(env={"A": stored_arbitrary("A", 0, math.inf)})
    argchars = param("A", fmt="Length", null=False, arg_text=None)
    expansions = expand_to_word_simple(argchars, state, config)
    assert len(expansions) == 1
    word_obj, _ = expansions[0]
    assert word_signature(word_obj) == (
        "arb",
        ArbitraryType.APPROXIMATION,
        1,
        math.inf,
        False,
    )


# ${A%pattern} over empty A should yield an empty result with 0 words.
def test_expand_to_word_simple_trim_empty_value(config):
    state = make_state(env={"A": stored_literal("")})
    argchars = param("A", fmt="TrimR", null=False, arg_text="*")
    expansions = expand_to_word_simple(argchars, state, config)
    assert len(expansions) == 1
    word_obj, _ = expansions[0]
    assert word_signature(word_obj) == ("exp", "", False, 0, 0)


# ${A%pattern} over non-empty A should trim the shortest matching suffix when both are literal.
def test_expand_to_word_simple_trim_nonempty_value_literal(config):
    state = make_state(env={"A": stored_literal("value")})
    argchars = param("A", fmt="TrimR", null=False, arg_text="*")
    expansions = expand_to_word_simple(argchars, state, config)
    assert len(expansions) == 1
    word_obj, _ = expansions[0]
    assert word_signature(word_obj) == ("exp", "value", False, 1, 1)


@pytest.mark.parametrize("fmt", ["TrimR", "TrimRMax", "TrimL", "TrimLMax"])
def test_expand_to_word_simple_trim_arbitrary_value(fmt, config):
    state = make_state(env={"A": stored_arbitrary("A", 0, math.inf)})
    argchars = param("A", fmt=fmt, null=False, arg_text="*")
    expansions = expand_to_word_simple(argchars, state, config)
    assert len(expansions) == 1
    word_obj, _ = expansions[0]
    assert word_signature(word_obj) == (
        "arb",
        ArbitraryType.APPROXIMATION,
        0,
        math.inf,
        False,
    )


def test_expand_to_word_simple_minus_default_rhs_arbitrary(config):
    state = make_state(env={"B": stored_arbitrary("B", 0, math.inf)})
    argchars = var("A", fmt="Minus", null=True, arg=var("B"))
    expansions = expand_to_word_simple(argchars, state, config)
    assert len(expansions) == 2
    sources = set()
    for word_obj, _ in expansions:
        assert len(word_obj.chunks) == 1
        chunk = word_obj.chunks[0]
        assert isinstance(chunk, ExpandedChunk)
        assert isinstance(chunk.content, CompletelyArbitrary)
        sources.add(_arbitrary_var_name(chunk.content))
    assert sources == {"A", "B"}


def test_expand_to_word_simple_plus_rhs_arbitrary(config):
    state = make_state(
        env={"A": stored_literal("value"), "B": stored_arbitrary("B", 0, math.inf)}
    )
    argchars = var("A", fmt="Plus", null=True, arg=var("B"))
    expansions = expand_to_word_simple(argchars, state, config)
    assert len(expansions) == 1
    word_obj, _ = expansions[0]
    assert len(word_obj.chunks) == 1
    chunk = word_obj.chunks[0]
    assert isinstance(chunk, ExpandedChunk)
    assert isinstance(chunk.content, CompletelyArbitrary)
    assert _arbitrary_var_name(chunk.content) == "B"


def test_expand_to_word_simple_assign_rhs_arbitrary(config):
    state = make_state(env={"B": stored_arbitrary("B", 0, math.inf)})
    argchars = var("A", fmt="Assign", null=True, arg=var("B"))
    expansions = expand_to_word_simple(argchars, state, config)
    assert len(expansions) == 1
    word_obj, next_state = expansions[0]
    assert len(word_obj.chunks) == 1
    chunk = word_obj.chunks[0]
    assert isinstance(chunk, ExpandedChunk)
    assert isinstance(chunk.content, CompletelyArbitrary)
    assert _arbitrary_var_name(chunk.content) == "B"
    stored_chunks = next_state.lookup("A").value.chunks
    assert len(stored_chunks) == 1
    stored_chunk = stored_chunks[0]
    assert isinstance(stored_chunk, ExpandedChunk)
    assert isinstance(stored_chunk.content, CompletelyArbitrary)
    assert _arbitrary_var_name(stored_chunk.content) == "B"


# ${A%pattern} with no match should leave the literal unchanged.
def test_expand_to_word_simple_trim_no_match_keeps_value(config):
    state = make_state(env={"A": stored_literal("value")})
    argchars = param("A", fmt="TrimR", null=False, arg_text="foo")
    expansions = expand_to_word_simple(argchars, state, config)
    assert len(expansions) == 1
    word_obj, _ = expansions[0]
    assert word_signature(word_obj) == ("exp", "value", False, 1, 1)


# ${A%%*} should remove the longest matching suffix, yielding empty.
def test_expand_to_word_simple_trim_rmax_full_removal(config):
    state = make_state(env={"A": stored_literal("value")})
    argchars = param("A", fmt="TrimRMax", null=False, arg_text="*")
    expansions = expand_to_word_simple(argchars, state, config)
    assert len(expansions) == 1
    word_obj, _ = expansions[0]
    assert word_signature(word_obj) == ("exp", "", False, 0, 0)


# ${A##*} should remove the longest matching prefix, yielding empty.
def test_expand_to_word_simple_trim_lmax_full_removal(config):
    state = make_state(env={"A": stored_literal("value")})
    argchars = param("A", fmt="TrimLMax", null=False, arg_text="*")
    expansions = expand_to_word_simple(argchars, state, config)
    assert len(expansions) == 1
    word_obj, _ = expansions[0]
    assert word_signature(word_obj) == ("exp", "", False, 0, 0)


POSIX_OPERATOR_CASES = [
    (
        "normal",
        var("A"),
        {"A": stored_literal("value")},
        ("exp", "value", False, 1, 1),
        None,
        False,
    ),
    (
        "minus_colon_unset",
        param("A", fmt="Minus", null=True, arg_text="default"),
        None,
        ("exp", "default", False, 1, 1),
        None,
        False,
    ),
    (
        "minus_no_colon_unset",
        param("A", fmt="Minus", null=False, arg_text="default"),
        None,
        ("exp", "default", False, 1, 1),
        None,
        False,
    ),
    (
        "assign_colon_unset",
        param("A", fmt="Assign", null=True, arg_text="default"),
        None,
        ("exp", "default", False, 1, 1),
        stored_literal("default"),
        False,
    ),
    (
        "assign_no_colon_unset",
        param("A", fmt="Assign", null=False, arg_text="default"),
        None,
        ("exp", "default", False, 1, 1),
        stored_literal("default"),
        False,
    ),
    (
        "question_colon_unset",
        param("A", fmt="Question", null=True, arg_text="err"),
        None,
        None,
        None,
        True,
    ),
    (
        "question_no_colon_unset",
        param("A", fmt="Question", null=False, arg_text="err"),
        None,
        None,
        None,
        True,
    ),
    (
        "plus_colon_set",
        param("A", fmt="Plus", null=True, arg_text="alt"),
        {"A": stored_literal("value")},
        ("exp", "alt", False, 1, 1),
        None,
        False,
    ),
    (
        "plus_no_colon_set_empty",
        param("A", fmt="Plus", null=False, arg_text="alt"),
        {"A": stored_literal("")},
        ("exp", "alt", False, 1, 1),
        None,
        False,
    ),
    (
        "length",
        param("A", fmt="Length", null=False, arg_text=None),
        {"A": stored_literal("abcd")},
        ("exp", "4", False, 1, 1),
        None,
        False,
    ),
    (
        "trim_r",
        param("A", fmt="TrimR", null=False, arg_text=".*"),
        {"A": stored_literal("foo.bar")},
        ("exp", "foo", False, 1, 1),
        None,
        False,
    ),
    (
        "trim_r_max",
        param("A", fmt="TrimRMax", null=False, arg_text=".*"),
        {"A": stored_literal("foo.bar.baz")},
        ("exp", "foo", False, 1, 1),
        None,
        False,
    ),
    (
        "trim_l",
        param("A", fmt="TrimL", null=False, arg_text="*/"),
        {"A": stored_literal("dir/sub/file")},
        ("exp", "sub/file", False, 1, 1),
        None,
        False,
    ),
    (
        "trim_l_max",
        param("A", fmt="TrimLMax", null=False, arg_text="*/"),
        {"A": stored_literal("dir/sub/file")},
        ("exp", "file", False, 1, 1),
        None,
        False,
    ),
]


@pytest.mark.parametrize(
    "label,argchars,env,expected_word,expected_env_value,expected_terminated",
    POSIX_OPERATOR_CASES,
)
def test_posix_parameter_operators_full_spec(
    label, argchars, env, expected_word, expected_env_value, expected_terminated
):
    config = InterpConfig(unbound_policy=UnboundVariablePolicy.EMPTY)
    state = make_state(env=env)
    expansions = expand_to_word_simple(argchars, state, config)
    assert len(expansions) == 1
    word_obj, next_state = expansions[0]
    if expected_word is not None:
        assert word_signature(word_obj) == expected_word
    if expected_env_value is not None:
        assert next_state.lookup("A").value == expected_env_value
    if expected_terminated:
        assert next_state.terminated


# expand_to_word should strip assignment quotes and return an unsplit storage-ready word.
def test_expand_to_word_assignment_strips_quotes(config):
    state = make_state()
    traces = [Trace((state,))]
    expansions = expand_to_word(traces, q(lit("a b")), config)
    assert len(expansions) == 1
    _, word_obj = expansions[0]
    assert word_obj.chunks == [LiteralChunk("a b", is_quoted=False)]


# expand_to_word should keep spaces intact for variable expansions in assignment context.
def test_expand_to_word_assignment_keeps_unsplit(config):
    state = make_state(env={"VAR": stored_literal("x y")})
    traces = [Trace((state,))]
    expansions = expand_to_word(traces, q(var("VAR")), config)
    assert len(expansions) == 1
    _, word_obj = expansions[0]
    assert len(word_obj.chunks) == 1
    chunk = word_obj.chunks[0]
    assert isinstance(chunk, ExpandedChunk)
    assert chunk.content == "x y"
    assert chunk.is_quoted is False


# expand_simple should apply command-context splitting and quoting across common patterns.
@pytest.mark.parametrize(
    "args, expected",
    [
        ([lit("echo"), lit("a"), lit("b")], ["echo", "a", "b"]),
        ([lit("echo"), q(lit("a b"))], ["echo", "a b"]),
        ([lit("echo"), var("VAR")], ["echo", "x", "y"]),
        ([lit("echo"), q(var("VAR"))], ["echo", "x y"]),
        (
            [lit("echo"), word(lit("pre_"), var("VAR"), lit("_suf"))],
            ["echo", "pre_x", "y_suf"],
        ),
        (
            [lit("echo"), q(word(lit("pre_"), var("VAR"), lit("_suf")))],
            ["echo", "pre_x y_suf"],
        ),
        (
            [lit("echo"), word(q(lit("pre_")), var("VAR"), q(lit("_suf")))],
            ["echo", "pre_x", "y_suf"],
        ),
    ],
)
def test_expand_simple_basic_command_context(args, expected, base_state, config):
    fields = expand_command(args, base_state, config)
    assert field_texts(fields) == expected


# Unquoted empty expansion should disappear from the argument list.
def test_expand_simple_empty_expansion_drops_field(base_state, config):
    fields = expand_command([lit("echo"), var("EMPTY")], base_state, config)
    assert field_texts(fields) == ["echo"]


# Quoted empty expansion should yield a single empty field.
def test_expand_simple_quoted_empty_expansion_preserves_field(base_state, config):
    fields = expand_command([lit("echo"), q(var("EMPTY"))], base_state, config)
    assert field_texts(fields) == ["echo", ""]


# Non-whitespace IFS should split and preserve empty fields between separators.
def test_expand_simple_ifs_non_whitespace_splitting(config):
    state = make_state(env={"VAR": stored_literal("a::b")}, ifs_value=":")
    fields = expand_command([lit("echo"), var("VAR")], state, config)
    assert field_texts(fields) == ["echo", "a", "", "b"]


# Empty IFS should disable splitting entirely.
def test_expand_simple_ifs_empty_disables_splitting(config):
    state = make_state(env={"VAR": stored_literal("x y")}, ifs_value="")
    fields = expand_command([lit("echo"), var("VAR")], state, config)
    assert field_texts(fields) == ["echo", "x y"]


# Unset IFS should behave as default IFS for splitting.
def test_expand_simple_ifs_unset_falls_back_to_default(config):
    state = make_state(env={"VAR": stored_literal("x y")}, ifs_value=None)
    fields = expand_command([lit("echo"), var("VAR")], state, config)
    assert field_texts(fields) == ["echo", "x", "y"]


def test_expand_simple_tilde_expands_home(config):
    state = make_state(env={"HOME": stored_literal("/home/alice")})
    fields = expand_command([lit("echo"), tilde()], state, config)
    assert field_texts(fields) == ["echo", "/home/alice"]
    assert_symstr_field(fields[1], "/home/alice", 1, 1)


# Rationale: POSIX does not specify this behavior and from a quick test it seems that different shells handle it differently
# NOTE: Maybe we could alter this behavior based on the shebang?
def test_expand_simple_tilde_unset_home_is_arbitrary(config):
    state = make_state()
    state = state.unset_env("HOME")
    fields = expand_command([lit("echo"), tilde()], state, config)
    assert field_texts(fields) == ["echo", "<arb>"]
    assert_arbitrary_field(fields[1], ArbitraryType.ENVIRONMENT, 0, math.inf)


# TODO: Would it be sound to substitute the value of HOME if its last component matches the tilde user? I'm not sure
def test_expand_simple_tilde_with_user_is_arbitrary(config):
    state = make_state(env={"HOME": stored_literal("/home/alice")})
    fields = expand_command([lit("echo"), tilde("alice")], state, config)
    assert field_texts(fields) == ["echo", "<arb>"]
    assert_arbitrary_field(fields[1], ArbitraryType.APPROXIMATION, 0, math.inf)


def test_expand_simple_arbitrary_ifs_gives_up_on_splitting(config):
    state = make_state(
        env={"VAR": stored_literal("a b"), "IFS": stored_arbitrary("IFS")}
    )
    fields = expand_command([lit("echo"), var("VAR")], state, config)
    assert field_texts(fields) == ["echo", "<arb>"]
    assert_arbitrary_field(fields[1], ArbitraryType.APPROXIMATION, 0, math.inf)


def test_expand_warns_inconsistent_ifs_across_traces(config):
    state1 = make_state(env={"VAR": stored_literal("a b")}, ifs_value=" ")
    state2 = make_state(env={"VAR": stored_literal("a b")}, ifs_value=":")
    _ = expand([Trace((state1,)), Trace((state2,))], var("VAR"), config)
    issues = reporter.Reporter.get_report().issues
    assert any(isinstance(issue, reporter.InconsistentIFS) for issue in issues)


def test_expand_simple_dollar_at_quoted_expands_each_positional(config):
    state = make_state(
        env={
            "1": stored_literal("a b"),
            "2": stored_literal("c"),
            "3": stored_literal("d e"),
        },
    )
    fields = expand_command([lit("echo"), q(var("@"))], state, config)
    assert field_texts(fields) == ["echo", "a b", "c", "d e"]


def test_expand_simple_dollar_at_unquoted_splits_each_param(config):
    state = make_state(
        env={
            "1": stored_literal("a b"),
            "2": stored_literal("c"),
            "3": stored_literal("d e"),
        },
    )
    fields = expand_command([lit("echo"), var("@")], state, config)
    assert field_texts(fields) == ["echo", "a", "b", "c", "d", "e"]


def test_expand_simple_dollar_star_quoted_joins_with_ifs(config):
    state = make_state(
        env={
            "1": stored_literal("a b"),
            "2": stored_literal("c"),
        },
        ifs_value=":",
    )
    fields = expand_command([lit("echo"), q(var("*"))], state, config)
    assert field_texts(fields) == ["echo", "a b:c"]


# Unquoted literal glob should widen wordcount but keep literal content.
def test_expand_simple_glob_wordcount_only(config):
    state = make_state()
    fields = expand_command([lit("echo"), lit("*")], state, config)
    assert field_texts(fields) == ["echo", "*"]
    assert_symstr_field(fields[1], "*", 0, math.inf)


# Mixed literal/glob text should keep literal content and widen wordcount.
def test_expand_simple_glob_preserves_literal_content(config):
    state = make_state()
    fields = expand_command([lit("echo"), lit("pre_*.txt")], state, config)
    assert field_texts(fields) == ["echo", "pre_*.txt"]
    assert_symstr_field(fields[1], "pre_*.txt", 1, math.inf)


# Quoted glob should suppress wordcount widening.
def test_expand_simple_glob_quoted_suppresses_wordcount(config):
    state = make_state()
    fields = expand_command([lit("echo"), q(lit("*.txt"))], state, config)
    assert field_texts(fields) == ["echo", "*.txt"]
    assert_symstr_field(fields[1], "*.txt", 1, 1)


# Unquoted expansion containing * should widen wordcount without changing text.
def test_expand_simple_glob_from_expansion(base_state, config):
    fields = expand_command([lit("echo"), var("GLOB")], base_state, config)
    assert field_texts(fields) == ["echo", "*.txt"]
    assert_symstr_field(fields[1], "*.txt", 1, math.inf)


# IFS splitting should consume '*' before glob wordcount logic runs.
def test_expand_simple_ifs_consumes_glob_character(config):
    state = make_state(env={"VAR": stored_literal("a*b")}, ifs_value="*")
    fields = expand_command([lit("echo"), var("VAR")], state, config)
    assert field_texts(fields) == ["echo", "a", "b"]


# Unquoted symbolic expansion should yield a single arbitrary field with [0, inf].
def test_expand_simple_symbolic_unquoted(base_state, config):
    fields = expand_command([lit("echo"), var("SYM")], base_state, config)
    assert field_texts(fields) == ["echo", "<arb>"]
    assert_arbitrary_field(fields[1], ArbitraryType.ENVIRONMENT, 0, math.inf)


# Quoted symbolic expansion should yield a single arbitrary field with [1, 1].
def test_expand_simple_symbolic_quoted(base_state, config):
    fields = expand_command([lit("echo"), q(var("SYM"))], base_state, config)
    assert field_texts(fields) == ["echo", "<arb>"]
    assert_arbitrary_field(fields[1], ArbitraryType.ENVIRONMENT, 1, 1, quoted=True)


# Literal prefixes should be preserved on arbitrary fields when concatenated.
def test_expand_simple_symbolic_prefix_merging(base_state, config):
    fields = expand_command(
        [lit("echo"), word(lit("pre_"), var("SYM"))], base_state, config
    )
    assert field_texts(fields) == ["echo", "<arb>"]
    assert_arbitrary_field(
        fields[1], ArbitraryType.ENVIRONMENT, 1, math.inf, prefix="pre_"
    )


# expand_args_dumb should collapse differing trace expansions to a single arbitrary field.
def test_expand_args_dumb_collapses_multipath(config):
    state = make_state(env={"A": stored_literal("value1")})
    state1 = state.set_env("A", ShellVar(stored_literal("value1")))
    state2 = state.set_env("A", ShellVar(stored_literal("value2")))
    args = [lit("echo"), var("A")]
    traces, expanded = expand_args_dumb(
        [Trace((state1,)), Trace((state2,))], args, config
    )
    assert len(traces) == 2
    assert len(expanded) == 2
    assert_symstr_field(expanded[0], "echo", 1, 1)
    assert isinstance(expanded[1].content, CompletelyArbitrary)
    assert expanded[1].count.min == 0
    assert expanded[1].count.max == math.inf


# expand_args should preserve per-trace expansions with wordcount-only globs.
def test_expand_args_preserves_multipath(config):
    state1 = make_state(env={"A": stored_literal("value1")})
    state2 = make_state(env={"A": stored_literal("value2")})
    args = [lit("rm"), lit("-rf"), word(var("A"), lit("/*"))]
    expansions = expand_args([Trace((state1,)), Trace((state2,))], args, config)
    assert len(expansions) == 2
    v1 = expansions[0][1]
    v2 = expansions[1][1]
    assert_symstr_field(v1[0], "rm", 1, 1)
    assert_symstr_field(v1[1], "-rf", 1, 1)
    assert_symstr_field(v1[2], "value1/*", 1, math.inf)
    assert_symstr_field(v2[0], "rm", 1, 1)
    assert_symstr_field(v2[1], "-rf", 1, 1)
    assert_symstr_field(v2[2], "value2/*", 1, math.inf)


# expand_simple should respect IFS from state env when present.
def test_expand_simple_uses_ifs_from_env_when_set(config):
    state = make_state(env={"VAR": stored_literal("a b")}, ifs_value=",")
    fields = expand_command([lit("echo"), var("VAR")], state, config)
    assert field_texts(fields) == ["echo", "a b"]


# UnboundVariablePolicy.EMPTY should drop unbound expansions in command context.
def test_expand_simple_respects_unbound_policy_empty(config):
    config = InterpConfig(unbound_policy=UnboundVariablePolicy.EMPTY)
    state = make_state()
    fields = expand_command([lit("echo"), var("MISSING")], state, config)
    assert field_texts(fields) == ["echo"]


# Unquoted command substitution should yield an arbitrary ExpandedChunk.
def test_expand_to_word_simple_command_subst_unquoted(config):
    cmd = AST.CommandNode(0, [], [lit("foo"), lit("bar")], [])
    argchars = [AST.BArgChar(cmd)]
    expansions = expand_to_word_simple(argchars, make_state(), config)
    assert len(expansions) == 1
    word_obj, _ = expansions[0]
    assert len(word_obj.chunks) == 1
    chunk = word_obj.chunks[0]
    assert isinstance(chunk, ExpandedChunk)
    assert isinstance(chunk.content, CompletelyArbitrary)
    assert chunk.is_quoted is False


# Quoted command substitution should preserve quoted flag on the ExpandedChunk.
def test_expand_to_word_simple_command_subst_quoted(config):
    cmd = AST.CommandNode(0, [], [lit("foo")], [])
    argchars = q([AST.BArgChar(cmd)])
    expansions = expand_to_word_simple(argchars, make_state(), config)
    assert len(expansions) == 1
    word_obj, _ = expansions[0]
    assert len(word_obj.chunks) == 1
    chunk = word_obj.chunks[0]
    assert isinstance(chunk, ExpandedChunk)
    assert isinstance(chunk.content, CompletelyArbitrary)
    assert chunk.is_quoted is True


# Arithmetic expansion should be wrapped in an ExpandedChunk with appropriate quoting.
def test_expand_to_word_simple_arith_unquoted(config):
    argchars = [AST.AArgChar(lit("1+1"))]
    expansions = expand_to_word_simple(argchars, make_state(), config)
    assert len(expansions) == 1
    word_obj, _ = expansions[0]
    assert len(word_obj.chunks) == 1
    chunk = word_obj.chunks[0]
    assert isinstance(chunk, ExpandedChunk)
    assert isinstance(chunk.content, CompletelyArbitrary)
    assert chunk.is_quoted is False


# Quoted arithmetic expansion should set the quoted flag on the ExpandedChunk.
def test_expand_to_word_simple_arith_quoted(config):
    argchars = q([AST.AArgChar(lit("1+1"))])
    expansions = expand_to_word_simple(argchars, make_state(), config)
    assert len(expansions) == 1
    word_obj, _ = expansions[0]
    assert len(word_obj.chunks) == 1
    chunk = word_obj.chunks[0]
    assert isinstance(chunk, ExpandedChunk)
    assert isinstance(chunk.content, CompletelyArbitrary)
    assert chunk.is_quoted is True
