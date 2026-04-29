import shasta.ast_node as AST

from sash.frozen import freeze
from sash.symbolic.state import State
from sash.symbolic.strings import (
    ArbitraryType,
    CompletelyArbitrary,
    Field,
    SymStr,
    WordCount,
)


def arbitrary(prefix):
    return CompletelyArbitrary(
        prefix=prefix,
        kind=ArbitraryType.APPROXIMATION,
        source=freeze(AST.CArgChar(0)),
        producing_state=State(),
        quoted=False,
    )


# -----------------------
# SymStr tests
# -----------------------


def test_symstr_removes_exact_dot_slash_prefix():
    # ./ab
    s = SymStr(("./", "a", "b"))
    result = s.try_without_leading_dot_slash()
    assert result.parts == ("a", "b")

    result = s.simplify().try_without_leading_dot_slash()
    assert result.parts == ("ab",)


def test_symstr_does_nothing_if_no_prefix():
    # ab
    s = SymStr(("a", "b"))
    result = s.try_without_leading_dot_slash()
    assert result == s

    result = s.simplify().try_without_leading_dot_slash()
    assert result == s.simplify()


def test_symstr_does_nothing_for_single_dot_slash():
    # ./
    s = SymStr(("./",))
    result = s.try_without_leading_dot_slash()
    assert result == s


def test_symstr_removes_embedded_dot_slash_prefix():
    # ./foobar
    s = SymStr(("./foo", "bar"))
    result = s.try_without_leading_dot_slash()
    assert result.parts == ("foo", "bar")

    result = s.simplify().try_without_leading_dot_slash()
    assert result.parts == ("foobar",)


def test_symstr_does_not_expose_absolute_path():
    # .//absx
    s = SymStr(("./", "/abs", "x"))
    result = s.try_without_leading_dot_slash()
    assert result == s

    result = s.simplify().try_without_leading_dot_slash()
    assert result == s.simplify()


def test_symstr_idempotent():
    # ./ab
    s = SymStr(("./", "a", "b"))
    r1 = s.try_without_leading_dot_slash()
    r2 = r1.try_without_leading_dot_slash()
    assert r1.parts == r2.parts

    r1 = s.simplify().try_without_leading_dot_slash()
    r2 = r1.try_without_leading_dot_slash()
    assert r1.parts == r2.parts


# -----------------------
# Field + SymStr tests
# -----------------------


def test_field_with_symstr_removes_dot_slash():
    # ./ab
    content = SymStr(("./", "a", "b"))
    f = Field(content=content, count=WordCount(1, 1))

    result = f.try_without_leading_dot_slash()

    assert isinstance(result.content, SymStr)
    assert result.content.parts == ("a", "b")


def test_field_with_symstr_no_change_returns_same_object():
    # ab
    content = SymStr(("a", "b"))
    f = Field(content=content, count=WordCount(1, 1))

    result = f.try_without_leading_dot_slash()

    assert result == f


# -----------------------
# Field + CompletelyArbitrary tests
# -----------------------


def test_field_completely_arbitrary_prefix_removed():
    # ./a$SOMETHING
    prefix = SymStr(("./", "a"))
    content = arbitrary(prefix=prefix)

    f = Field(content=content, count=WordCount(1, 1))
    result = f.try_without_leading_dot_slash()

    assert isinstance(result.content, CompletelyArbitrary)
    assert result.content.prefix is not None and result.content.prefix.parts == ("a",)


def test_field_completely_arbitrary_prefix_none_if_becomes_dot_slash():
    # ./$SOMETHING
    prefix = SymStr(("./",))
    content = arbitrary(prefix=prefix)

    f = Field(content=content, count=WordCount(1, 1))
    result = f.try_without_leading_dot_slash()

    assert (
        isinstance(result.content, CompletelyArbitrary)
        and result.content.prefix is None
    )


def test_field_completely_arbitrary_no_change_returns_same_object():
    # $SOMETHING
    content = arbitrary(prefix=None)
    f = Field(content=content, count=WordCount(1, 1))

    result = f.try_without_leading_dot_slash()

    assert result == f


# -----------------------
# Cross-type safety tests
# -----------------------


def test_field_does_not_modify_when_prefix_invalid():
    # a$SOMETHING
    prefix = SymStr(("a", "b"))
    content = arbitrary(prefix=prefix)

    f = Field(content=content, count=WordCount(1, 1))
    result = f.try_without_leading_dot_slash()

    assert (
        isinstance(result.content, CompletelyArbitrary)
        and result.content.prefix is not None
        and result.content.prefix.parts == ("a", "b")
    )
