import math

import pytest
import shasta.ast_node as AST

from sash.frozen import freeze
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


def _make_literal_word(text: str, quoted: bool = False) -> PreSplitWord:
    return PreSplitWord([LiteralChunk(text, is_quoted=quoted)])


def _make_expanded_word(content: str | CompletelyArbitrary,
                        quoted: bool = False,
                        count: WordCount | None = None) -> PreSplitWord:
    return PreSplitWord([
        ExpandedChunk(
            content=content,
            is_quoted=quoted,
            count=count or WordCount(1, 1),
        )
    ])


def _combine_words(*parts: PreSplitWord | LiteralChunk | ExpandedChunk) -> PreSplitWord:
    chunks = []
    for part in parts:
        if isinstance(part, PreSplitWord):
            chunks.extend(part.chunks)
        else:
            chunks.append(part)
    return PreSplitWord(chunks)


def _field_texts(fields: list[Field]) -> list[str]:
    texts = []
    for field in fields:
        if isinstance(field.content, SymStr):
            texts.append(field.content.try_to_str() or "")
        else:
            texts.append("<arb>")
    return texts


def _expand_word(word: PreSplitWord,
                 ifs_value: str = DEFAULT_IFS) -> list[Field]:
    return word.split_into_fields(ifs_value)


def _expand_command(words: list[PreSplitWord],
                    ifs_value: str = DEFAULT_IFS) -> list[Field]:
    expanded: list[Field] = []
    for word in words:
        expanded.extend(_expand_word(word, ifs_value=ifs_value))
    return expanded


def _stored_literal(value: str) -> PreSplitWord:
    return _make_literal_word(value).prepare_for_storage()


def _symbolic_var(name: str, count: WordCount) -> PreSplitWord:
    var_ast = AST.VArgChar(fmt="Normal", null=False, var=name, arg=[])
    arbitrary = CompletelyArbitrary(freeze(var_ast), ArbitraryType.ENVIRONMENT, None)
    return _make_expanded_word(arbitrary, quoted=False, count=count).prepare_for_storage()


@pytest.fixture
def base_env():
    """Shared environment values for the expansion tests."""
    return {
        "VAR": _stored_literal("x y"),
        "IFS": DEFAULT_IFS,
        "SYM_VAR": _symbolic_var("SYM_VAR", WordCount(0, math.inf)),
        "GLOB_VAR": _stored_literal("*.txt"),
        "EMPTY_VAR": _stored_literal(""),
        "QUOTE_VAR": _stored_literal('"a b"'),
    }


@pytest.mark.parametrize(
    "script, words, expected",
    [
        pytest.param(
            "echo a b",
            [_make_literal_word("echo"), _make_literal_word("a"), _make_literal_word("b")],
            ["echo", "a", "b"],
            id="echo a b: two separate literal words",
        ),
        pytest.param(
            "echo \"a b\"",
            [_make_literal_word("echo"), _make_literal_word("a b", quoted=True)],
            ["echo", "a b"],
            id="echo \"a b\": quoted literal stays one field",
        ),
        pytest.param(
            "echo 'a b'",
            [_make_literal_word("echo"), _make_literal_word("a b", quoted=True)],
            ["echo", "a b"],
            id="echo 'a b': single-quoted literal stays one field",
        ),
    ],
)
def test_basic_sanity_command_context(script, words, expected):
    """Basic sanity checks for literal words and quote removal."""
    fields = _expand_command(words)
    assert _field_texts(fields) == expected


@pytest.mark.parametrize(
    "script, make_words, expected",
    [
        pytest.param(
            "echo $VAR",
            lambda env: [_make_literal_word("echo"), env["VAR"].expand_from_storage(False)],
            ["echo", "x", "y"],
            id="echo $VAR: unquoted expansion splits on IFS",
        ),
        pytest.param(
            "echo \"$VAR\"",
            lambda env: [_make_literal_word("echo"), env["VAR"].expand_from_storage(True)],
            ["echo", "x y"],
            id="echo \"$VAR\": quoted expansion suppresses splitting",
        ),
        pytest.param(
            "echo prefix_${VAR}_suffix",
            lambda env: [
                _make_literal_word("echo"),
                _combine_words(
                    LiteralChunk("prefix_", is_quoted=False),
                    env["VAR"].expand_from_storage(False),
                    LiteralChunk("_suffix", is_quoted=False),
                ),
            ],
            ["echo", "prefix_x", "y_suffix"],
            id="echo prefix_${VAR}_suffix: split inside expansion only",
        ),
        pytest.param(
            "echo \"prefix_${VAR}_suffix\"",
            lambda env: [
                _make_literal_word("echo"),
                _combine_words(
                    LiteralChunk("prefix_", is_quoted=True),
                    env["VAR"].expand_from_storage(True),
                    LiteralChunk("_suffix", is_quoted=True),
                ),
            ],
            ["echo", "prefix_x y_suffix"],
            id="echo \"prefix_${VAR}_suffix\": fully quoted word stays whole",
        ),
    ],
)
def test_variable_expansion_and_field_splitting(script, make_words, expected, base_env):
    """Variable expansion tests that distinguish quoted vs unquoted behavior."""
    fields = _expand_command(make_words(base_env), ifs_value=base_env["IFS"])
    assert _field_texts(fields) == expected


@pytest.mark.parametrize(
    "script, word_builder, expected",
    [
        pytest.param(
            "NEW_VAR=$VAR",
            lambda env: env["VAR"].expand_from_storage(False).prepare_for_storage(),
            "x y",
            id="NEW_VAR=$VAR: assignment bypasses splitting",
        ),
        pytest.param(
            "NEW_VAR=\"$VAR\"",
            lambda env: env["VAR"].expand_from_storage(True).prepare_for_storage(),
            "x y",
            id="NEW_VAR=\"$VAR\": quotes removed in storage",
        ),
        pytest.param(
            "NEW_VAR=*.txt",
            lambda env: _make_literal_word("*.txt").prepare_for_storage(),
            "*.txt",
            id="NEW_VAR=*.txt: globbing bypassed in assignment",
        ),
        pytest.param(
            "NEW_VAR=\"pre_\"${VAR}\"_suf\"",
            lambda env: _combine_words(
                LiteralChunk("pre_", is_quoted=True),
                env["VAR"].expand_from_storage(False),
                LiteralChunk("_suf", is_quoted=True),
            ).prepare_for_storage(),
            "pre_x y_suf",
            id="NEW_VAR=\"pre_\"${VAR}\"_suf\": concatenation without splitting",
        ),
    ],
)
def test_assignment_context_storage(script, word_builder, expected, base_env):
    """Assignment context should preserve spaces and strip quotes before storage."""
    stored = word_builder(base_env)
    stored_text = "".join(chunk.content for chunk in stored.chunks if isinstance(chunk.content, str))
    assert stored_text == expected


@pytest.mark.parametrize(
    "script, word_builder, expected, expects_arbitrary",
    [
        pytest.param(
            "ls *.txt",
            lambda env: [_make_literal_word("ls"), _make_literal_word("*.txt")],
            ["ls", "<arb>"],
            True,
            id="ls *.txt: unquoted literal glob expands",
        ),
        pytest.param(
            "ls \"*.txt\"",
            lambda env: [_make_literal_word("ls"), _make_literal_word("*.txt", quoted=True)],
            ["ls", "*.txt"],
            False,
            id="ls \"*.txt\": quoted literal suppresses globbing",
        ),
        pytest.param(
            "ls $GLOB_VAR",
            lambda env: [_make_literal_word("ls"), env["GLOB_VAR"].expand_from_storage(False)],
            ["ls", "<arb>"],
            True,
            id="ls $GLOB_VAR: unquoted expansion allows globbing",
        ),
        pytest.param(
            "ls pre_${GLOB_VAR}",
            lambda env: [
                _make_literal_word("ls"),
                _combine_words(
                    LiteralChunk("pre_", is_quoted=False),
                    env["GLOB_VAR"].expand_from_storage(False),
                ),
            ],
            ["ls", "<arb>"],
            True,
            id="ls pre_${GLOB_VAR}: unquoted multi-chunk globbing",
        ),
        pytest.param(
            "ls \"$GLOB_VAR\"",
            lambda env: [_make_literal_word("ls"), env["GLOB_VAR"].expand_from_storage(True)],
            ["ls", "*.txt"],
            False,
            id="ls \"$GLOB_VAR\": quoted expansion suppresses globbing",
        ),
        pytest.param(
            "ls \"pre_${GLOB_VAR}\"",
            lambda env: [
                _make_literal_word("ls"),
                _combine_words(
                    LiteralChunk("pre_", is_quoted=True),
                    env["GLOB_VAR"].expand_from_storage(True),
                ),
            ],
            ["ls", "pre_*.txt"],
            False,
            id="ls \"pre_${GLOB_VAR}\": quoted multi-chunk keeps literal",
        ),
    ],
)
@pytest.mark.skip(reason="Globs only affect word count; they do not expand to arbitraries. These tests are kept here to make this obvious. If the semantics ever change, these can be integrated.")
def test_globbing_pathname_expansion(script, word_builder, expected, expects_arbitrary, base_env):
    """Globbing yields an arbitrary field for unquoted patterns and literals for quoted ones."""
    fields = _expand_command(word_builder(base_env))
    assert _field_texts(fields) == expected
    if expects_arbitrary:
        assert isinstance(fields[1].content, CompletelyArbitrary)
        assert fields[1].count.min == 0
        assert fields[1].count.max == math.inf


@pytest.mark.skip(reason="Globs only affect word count; they do not expand to arbitraries. These tests are kept here to make this obvious. If the semantics ever change, these can be integrated.")
def test_glob_preserves_literal_prefix_suffix():
    """Unquoted glob should carry literal prefix and suffix constraints."""
    word = _combine_words(
        LiteralChunk("prefix_", is_quoted=False),
        LiteralChunk("*", is_quoted=False),
        LiteralChunk(".txt", is_quoted=False),
    )
    fields = _expand_command([_make_literal_word("echo"), word])
    assert _field_texts(fields) == ["echo", "<arb>"]
    assert isinstance(fields[1].content, CompletelyArbitrary)
    assert fields[1].content.prefix == SymStr(("prefix_",))
    assert fields[1].content.suffix == SymStr((".txt",))
    assert fields[1].count.min == 0
    assert fields[1].count.max == math.inf


@pytest.mark.skip(reason="Globs only affect word count; they do not expand to arbitraries. These tests are kept here to make this obvious. If the semantics ever change, these can be integrated.")
def test_glob_with_quoted_prefix_asterisk():
    """Quoted literal '*' should become the prefix when an unquoted glob follows."""
    word = _combine_words(
        LiteralChunk("*", is_quoted=True),
        LiteralChunk("*", is_quoted=False),
    )
    fields = _expand_command([_make_literal_word("echo"), word])
    assert _field_texts(fields) == ["echo", "<arb>"]
    assert isinstance(fields[1].content, CompletelyArbitrary)
    assert fields[1].content.prefix == SymStr(("*",))
    assert fields[1].content.suffix is None


@pytest.mark.skip(reason="Globs only affect word count; they do not expand to arbitraries. These tests are kept here to make this obvious. If the semantics ever change, these can be integrated.")
def test_glob_multiple_wildcards_degrades_constraints():
    """Multiple globs with a middle literal degrade to generic arbitrary."""
    word = _combine_words(
        LiteralChunk("*", is_quoted=False),
        LiteralChunk("literal", is_quoted=False),
        LiteralChunk("*", is_quoted=False),
    )
    fields = _expand_command([_make_literal_word("echo"), word])
    assert _field_texts(fields) == ["echo", "<arb>"]
    assert isinstance(fields[1].content, CompletelyArbitrary)
    assert fields[1].content.prefix is None
    assert fields[1].content.suffix is None


def test_glob_unquoted_literal_sets_wordcount_inf():
    """Unquoted glob should keep literal content but set max word count to inf."""
    words = [_make_literal_word("echo"), _make_literal_word("*.txt")]
    fields = _expand_command(words)
    assert _field_texts(fields) == ["echo", "*.txt"]
    assert fields[1].count.min == 1
    assert fields[1].count.max == math.inf


def test_glob_unquoted_entire_field_min_zero():
    """A field that is purely a glob should allow zero matches."""
    words = [_make_literal_word("echo"), _make_literal_word("*")]
    fields = _expand_command(words)
    assert _field_texts(fields) == ["echo", "*"]
    assert fields[1].count.min == 0
    assert fields[1].count.max == math.inf


def test_glob_quoted_suppresses_wordcount_inf():
    """Quoted glob should not increase the word count range."""
    words = [_make_literal_word("echo"), _make_literal_word("*.txt", quoted=True)]
    fields = _expand_command(words)
    assert _field_texts(fields) == ["echo", "*.txt"]
    assert fields[1].count.min == 1
    assert fields[1].count.max == 1


def test_glob_unquoted_expanded_sets_wordcount_inf(base_env):
    """Unquoted expanded glob should increase max word count without expanding."""
    words = [_make_literal_word("echo"), base_env["GLOB_VAR"].expand_from_storage(False)]
    fields = _expand_command(words)
    assert _field_texts(fields) == ["echo", "*.txt"]
    assert fields[1].count.min == 1
    assert fields[1].count.max == math.inf


def test_glob_unquoted_prefix_suffix_keeps_literal_content():
    """Prefix and suffix remain literal while glob affects only word count."""
    word = _combine_words(
        LiteralChunk("pre_", is_quoted=False),
        LiteralChunk("*", is_quoted=False),
        LiteralChunk(".txt", is_quoted=False),
    )
    fields = _expand_command([_make_literal_word("echo"), word])
    assert _field_texts(fields) == ["echo", "pre_*.txt"]
    assert fields[1].count.min == 1
    assert fields[1].count.max == math.inf


def test_glob_multiple_wildcards_preserve_wordcount_inf():
    """Multiple wildcards should still only expand word count, not content."""
    word = _combine_words(
        LiteralChunk("*", is_quoted=False),
        LiteralChunk("literal", is_quoted=False),
        LiteralChunk("*", is_quoted=False),
    )
    fields = _expand_command([_make_literal_word("echo"), word])
    assert _field_texts(fields) == ["echo", "*literal*"]
    assert fields[1].count.min == 1
    assert fields[1].count.max == math.inf


def test_ifs_non_whitespace_splits_with_empty_fields(base_env):
    """Non-whitespace IFS characters should preserve empty fields between separators."""
    colon_var = _stored_literal("a::b")
    words = [_make_literal_word("echo"), colon_var.expand_from_storage(False)]
    fields = _expand_command(words, ifs_value=":")
    assert _field_texts(fields) == ["echo", "a", "", "b"]


def test_ifs_non_whitespace_does_not_split_spaces(base_env):
    """When IFS is ':', spaces are treated as literal characters."""
    space_var = _stored_literal("a  b")
    words = [_make_literal_word("echo"), space_var.expand_from_storage(False)]
    fields = _expand_command(words, ifs_value=":")
    assert _field_texts(fields) == ["echo", "a  b"]


def test_ifs_whitespace_collapses_adjacent_separators(base_env):
    """Default IFS collapses adjacent whitespace separators."""
    var = _stored_literal("a  b")
    words = [_make_literal_word("echo"), var.expand_from_storage(False)]
    fields = _expand_command(words, ifs_value=DEFAULT_IFS)
    assert _field_texts(fields) == ["echo", "a", "b"]


def test_ifs_empty_disables_splitting(base_env):
    """Empty IFS disables field splitting entirely."""
    var = _stored_literal("x y")
    words = [_make_literal_word("echo"), var.expand_from_storage(False)]
    fields = _expand_command(words, ifs_value="")
    assert _field_texts(fields) == ["echo", "x y"]


def test_empty_unquoted_expansion_drops_field(base_env):
    """Unquoted empty expansion should disappear from the argument list."""
    words = [_make_literal_word("echo"), base_env["EMPTY_VAR"].expand_from_storage(False)]
    fields = _expand_command(words)
    assert _field_texts(fields) == ["echo"]


def test_empty_quoted_expansion_preserves_empty_field(base_env):
    """Quoted empty expansion yields exactly one empty field."""
    words = [_make_literal_word("echo"), base_env["EMPTY_VAR"].expand_from_storage(True)]
    fields = _expand_command(words)
    assert _field_texts(fields) == ["echo", ""]


def test_empty_expansion_with_literal_suffix(base_env):
    """Empty expansion should merge with adjacent literal text."""
    word = _combine_words(
        base_env["EMPTY_VAR"].expand_from_storage(False),
        LiteralChunk("x", is_quoted=False),
    )
    fields = _expand_command([_make_literal_word("echo"), word])
    assert _field_texts(fields) == ["echo", "x"]


def test_quotes_in_variable_data_unquoted_split(base_env):
    """Embedded quotes in data do not protect unquoted expansions."""
    words = [_make_literal_word("echo"), base_env["QUOTE_VAR"].expand_from_storage(False)]
    fields = _expand_command(words)
    assert _field_texts(fields) == ["echo", '"a', 'b"']


def test_quotes_in_variable_data_quoted_preserved(base_env):
    """Quoted expansion preserves embedded quotes as literal characters."""
    words = [_make_literal_word("echo"), base_env["QUOTE_VAR"].expand_from_storage(True)]
    fields = _expand_command(words)
    assert _field_texts(fields) == ["echo", '"a b"']


def test_ifs_consumes_glob_character_before_globbing(base_env):
    """IFS splitting should consume '*' before any globbing logic runs."""
    var = _stored_literal("a*b")
    words = [_make_literal_word("echo"), var.expand_from_storage(False)]
    fields = _expand_command(words, ifs_value="*")
    assert _field_texts(fields) == ["echo", "a", "b"]


def test_symbolic_unquoted_expansion(base_env):
    """Unquoted symbolic expansion yields a single arbitrary field with unbounded count."""
    words = [_make_literal_word("echo"), base_env["SYM_VAR"].expand_from_storage(False)]
    fields = _expand_command(words)
    assert len(fields) == 2
    assert _field_texts(fields) == ["echo", "<arb>"]
    assert isinstance(fields[1].content, CompletelyArbitrary)
    assert fields[1].count.min == 0
    assert fields[1].count.max == math.inf


def test_symbolic_quoted_expansion(base_env):
    """Quoted symbolic expansion must collapse to a single field with count (1,1)."""
    words = [_make_literal_word("echo"), base_env["SYM_VAR"].expand_from_storage(True)]
    fields = _expand_command(words)
    assert len(fields) == 2
    assert _field_texts(fields) == ["echo", "<arb>"]
    assert isinstance(fields[1].content, CompletelyArbitrary)
    assert fields[1].count.min == 1
    assert fields[1].count.max == 1


def test_symbolic_assignment_storage(base_env):
    """Assignment context stores symbolic expansions without splitting."""
    stored = base_env["SYM_VAR"].expand_from_storage(False).prepare_for_storage()
    assert len(stored.chunks) == 1
    assert isinstance(stored.chunks[0], ExpandedChunk)
    assert isinstance(stored.chunks[0].content, CompletelyArbitrary)
    assert stored.chunks[0].count.min == 0
    assert stored.chunks[0].count.max == math.inf


def test_symbolic_prefix_merging(base_env):
    """Literal prefixes should merge into the first field of symbolic expansions."""
    word = _combine_words(
        LiteralChunk("prefix_", is_quoted=False),
        base_env["SYM_VAR"].expand_from_storage(False),
    )
    fields = _expand_command([_make_literal_word("echo"), word])
    assert len(fields) == 2
    assert _field_texts(fields) == ["echo", "<arb>"]
    assert isinstance(fields[1].content, CompletelyArbitrary)
    assert fields[1].content.prefix == SymStr(("prefix_",))


def test_null_adjacent_symbolic_unquoted(base_env):
    """Empty quoted literal should merge into the first symbolic field without collapsing it."""
    word = _combine_words(
        LiteralChunk("", is_quoted=True),
        base_env["SYM_VAR"].expand_from_storage(False),
    )
    fields = _expand_command([_make_literal_word("echo"), word])
    assert len(fields) == 2
    assert _field_texts(fields) == ["echo", "<arb>"]
    assert isinstance(fields[1].content, CompletelyArbitrary)
    assert fields[1].count.min == 1
    assert fields[1].count.max == math.inf


def test_null_adjacent_symbolic_quoted(base_env):
    """Fully quoted symbolic expansion should collapse to a single field."""
    word = _combine_words(
        LiteralChunk("", is_quoted=True),
        base_env["SYM_VAR"].expand_from_storage(True),
        LiteralChunk("", is_quoted=True),
    )
    fields = _expand_command([_make_literal_word("echo"), word])
    assert len(fields) == 2
    assert _field_texts(fields) == ["echo", "<arb>"]
    assert isinstance(fields[1].content, CompletelyArbitrary)
    assert fields[1].count.min == 1
    assert fields[1].count.max == 1
