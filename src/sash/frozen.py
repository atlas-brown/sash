from dataclasses import dataclass
from typing import Any, TypeVar, Generic
import collections
import shasta.ast_node as AST

@dataclass(frozen=True)
class FrozenAst:
    ast: Any
    kind: str
    fields: tuple[Any, ...]

    def __hash__(self):
        return hash((self.kind, self.fields))
    def __eq__(self, other):
        return isinstance(other, FrozenAst) and self.kind == other.kind and self.fields == other.fields

    def __str__(self) -> str:
        return f"Frozen({str(self.ast)})"
    def __repr__(self) -> str:
        return f"Frozen({self.ast.__repr__()})"
    def pretty(self) -> str:
        return self.ast.pretty()

def freeze(ast: AST.AstNode) -> FrozenAst:
    return FrozenAst(
        ast,
        type(ast).__name__,
        tuple((field_name, freeze_thing(value)) for field_name, value in ast.__dict__.items())
        )

def freeze_thing(v):
    match v:
        case AST.AstNode():
            return freeze(v)
        case list() | tuple() | set():
            return tuple(freeze_thing(x) for x in v)
        case dict():
            return tuple((k, freeze_thing(v[k])) for k in sorted(v.keys()))
        case _:
            return v

K = TypeVar('K')
V = TypeVar('V')

class FrozenDict(Generic[K, V]):
    def __init__(self, *args, **kwargs):
        self._d = dict(*args, **kwargs)
        self._hash = None

    def __iter__(self):
        return iter(self._d)

    def __len__(self):
        return len(self._d)

    def __getitem__(self, key):
        return self._d[key]

    def __contains__(self, key):
        return key in self._d

    def __hash__(self):
        if self._hash is None:
            self._hash = hash(tuple((k, freeze_thing(self._d[k])) for k in sorted(self._d.keys())))
        return self._hash
    
    def __eq__(self, other):
        return isinstance(other, FrozenDict) and self._d == other._d
    
    def __repr__(self):
        return f"FrozenDict({self._d})"

    def set(self, k, v):
        return FrozenDict(**(self._d | {k: v}))

    def get(self, k, default=None):
        return self._d.get(k, default)

    def __or__(self, other):
        if not isinstance(other, (dict, FrozenDict)):
            return NotImplemented
        if isinstance(other, FrozenDict):
            other = other._d
        return FrozenDict({**self._d, **other})

    def __ror__(self, other):
        return self.__or__(other)

    def __bool__(self):
        return bool(self._d)
