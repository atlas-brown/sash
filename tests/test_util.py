from typing import TypeVar

from hypothesis import given, settings
from hypothesis import strategies as st

from sash.util import split_at

settings.register_profile("default", max_examples=5)
settings.load_profile("default")

T = TypeVar("T")

def join_with_element(sublists: list[list[T]], element: T) -> list[T]:
    """Reverse of `split_at` for reconstruction: intersperse `element`s between sublists and flatten them."""
    if not sublists:
        return []
    output: list[T] = []
    for i, sublist in enumerate(sublists):
        if i > 0:
            output.append(element)
        output.extend(sublist)
    return output


@given(l=st.lists(st.integers()), element=st.integers())
def test_split_at_reconstructs_integers(l: list[int], element: int) -> None:
    """Property-based test for `split_at` with integers."""
    sublists = split_at(l, element)
    # Inserting the separator between sublists yields the original list.
    assert join_with_element(sublists, element) == l

    # None of the sublists contain the separator element.
    assert all(element not in sublist for sublist in sublists)

    # Number of sublists equals number of separators + 1.
    assert len(sublists) == l.count(element) + 1


@given(l=st.lists(st.text()), element=st.text())
def test_split_at_reconstructs_text(l: list[str], element: str) -> None:
    """Property-based test for `split_at` with strings."""
    sublists = split_at(l, element)
    # Inserting the separator between sublists yields the original list.
    assert join_with_element(sublists, element) == l

    # None of the sublists contain the separator element.
    assert all(element not in sublist for sublist in sublists)

    # Number of sublists equals number of separators + 1.
    assert len(sublists) == l.count(element) + 1
