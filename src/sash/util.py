import collections.abc
from enum import Enum
import logging

def make_hashable(obj: any) -> any:
    """Recursively traverses a data structure to make it hashable."""
    if isinstance(obj, Enum):
        return str(obj)
    if isinstance(obj, (int, float, str, bool, type(None))):
        return obj
    if isinstance(obj, tuple) and all(isinstance(x, collections.abc.Hashable) for x in obj):
        return obj

    if isinstance(obj, (list, set)):
        return (type(obj), tuple(make_hashable(e) for e in obj))

    if isinstance(obj, dict):
        return frozenset((make_hashable(k), make_hashable(v)) for k, v in obj.items())

    if hasattr(obj, '__dict__'):
        class_name = type(obj).__name__
        hashable_dict = make_hashable(obj.__dict__)
        return (class_name, hashable_dict)

    # If we got here, we don't know how to handle the type.
    raise TypeError(f"Unhashable type: {type(obj)}")
