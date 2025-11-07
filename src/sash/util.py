from sash.state import *
from typing import Any


def split_at(l: list[Any], element: Any) -> list[list[Any]]:
    """
    Split a list at each occurrence of element, returning a list of lists, none of which contain `element`.
    Examples:
    >>> split_at([1, 2, None, 3, None, 4], None)
    [[1, 2], [3], [4]]
    >>> split_at([1, 2, 3], None)
    [[1, 2, 3]]
    >>> split_at([1, 2, None, None, 3, None, 4], None)
    [[1, 2], [], [3], [4]]
    """
    result = []
    current = []
    for item in l:
        if item == element:
            result.append(current)
            current = []
        else:
            current.append(item)
    result.append(current)
    return result

def constant_field(string: str, words: int = 1) -> Field:
    return Field(SymStr((string,)), WordCount(words, words))

def shasta_pretty(ast_node: Any) -> str:
    return ast_node.pretty() if hasattr(ast_node, 'pretty') else str(ast_node)
