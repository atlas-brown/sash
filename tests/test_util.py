from collections.abc import Iterable

import pytest

from sash.symbolic.strings import SymStr
from sash.util import create_fresh_varname


@pytest.mark.parametrize(
    "parts",
    [
        [],
        ["a"],
        ["a", "b", "c"],
        ["hello", " ", "world"],
    ],
)
def test_symbstr_to_str_with_strings(parts: list[str]) -> None:
    symbstr = SymStr(tuple(parts))

    # If all components are strings, the result should be their concatenation.
    assert symbstr.try_to_str() == "".join(parts)


def test_symbstr_to_str_empty_iterable() -> None:
    symbstr: Iterable[str] = []

    # The result should be an empty string.
    assert SymStr(tuple(symbstr)).try_to_str() == ""


PREFIX_CASES: list[str | None] = [None, "", "x", "prefix", "_tmp", "Alpha1"]


def test_create_fresh_varname_default_prefix() -> None:
    name = create_fresh_varname()

    # The generated name should start with "vr".
    assert name.startswith("vr")


@pytest.mark.parametrize("prefix", PREFIX_CASES)
def test_create_fresh_varname_with_prefix(prefix: str | None) -> None:
    name = create_fresh_varname(prefix)
    expected_prefix = "vr" if prefix is None else prefix

    # The generated name should start with the expected prefix.
    assert name.startswith(expected_prefix)


@pytest.mark.parametrize("prefix", PREFIX_CASES)
def test_create_fresh_varname_is_unique(prefix: str | None) -> None:
    names = [create_fresh_varname(prefix) for _ in range(10)]

    # All generated names should be unique.
    assert len(set(names)) == len(names)


@pytest.mark.parametrize("prefix", PREFIX_CASES)
def test_create_fresh_var_shape_and_uniqueness(prefix: str | None) -> None:
    v1 = create_fresh_varname(prefix)
    v2 = create_fresh_varname(prefix)
    expected_prefix = "vr" if prefix is None else prefix

    # Both variable names should start with the expected prefix.
    assert v1.startswith(expected_prefix)
    assert v2.startswith(expected_prefix)

    # The two created variables should be distinct.
    assert v1 != v2
