from typing import List, TypeVar
from hypothesis import given, strategies as st
from sash.util import split_at

T = TypeVar("T")


def join_with_elementent(sublists: List[List[T]], elementent: T) -> List[T]:
    """Reverse of `split_at` for reconstruction: intersperse `elementent`s between sublists and flatten them."""
    if not sublists:
        return []
    output: List[T] = []
    for i, sublist in enumerate(sublists):
        if i > 0:
            output.append(elementent)
        output.extend(sublist)
    return output


@given(l=st.lists(st.integers()), element=st.integers())
def test_split_at_reconstructs_ints(l: list[int], element: int) -> None:
    """Property-based test for `split_at` with integers."""
    sublists = split_at(l, element)
    # Inserting the separator between sublists yields the original list.
    assert join_with_elementent(sublists, element) == l

    # None of the sublists contain the separator elementent.
    assert all(element not in sublist for sublist in sublists)

    # Number of sublists equals number of separators + 1.
    assert len(sublists) == l.count(element) + 1


@given(l=st.lists(st.text()), element=st.text())
def test_split_at_reconstructs_text(l: list[str], element: str) -> None:
    """Property-based test for `split_at` with strings."""
    sublists = split_at(l, element)
    # Inserting the separator between sublists yields the original list.
    assert join_with_elementent(sublists, element) == l

    # None of the sublists contain the separator elementent.
    assert all(element not in sublist for sublist in sublists)

    # Number of sublists equals number of separators + 1.
    assert len(sublists) == l.count(element) + 1
