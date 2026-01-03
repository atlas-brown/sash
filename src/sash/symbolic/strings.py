from dataclasses import dataclass, field, replace
from enum import Enum
from typing import TYPE_CHECKING

from sash.frozen import FrozenAst

if TYPE_CHECKING:
    from sash.symbolic.state import State


@dataclass(frozen=True)
class SymVar:
    name: str


@dataclass(frozen=True)
class SymStr:
    parts: tuple[str | SymVar, ...] = field(default_factory=tuple)

    def is_simple(self) -> bool:
        """Return true if there are no adjacent strings in `parts`."""
        last_was_str = False
        for p in self.parts:
            if isinstance(p, str):
                if last_was_str:
                    return False
                last_was_str = True
            else:
                last_was_str = False
        return True

    def simplify(self) -> "SymStr":
        if self.is_simple():
            return self

        # collapse all adjacent strings into one string
        new_parts = []
        this_str = ""
        for part in self.parts:
            if isinstance(part, str):
                this_str += part
            else:
                if this_str != "":
                    new_parts.append(this_str)
                    this_str = ""
                new_parts.append(part)
        if this_str != "":
            new_parts.append(this_str)
        return SymStr(tuple(new_parts))

    def try_to_str(self) -> str | None:
        nls : list[str] = []
        for i in self.parts:
            if isinstance(i,str):
                nls.append(i)
            else:
                return None
        return "".join(nls)


class ArbitraryType(Enum):
    APPROXIMATION = 0
    ENVIRONMENT = 1


@dataclass(frozen=True)
class CompletelyArbitrary:
    source: FrozenAst
    kind: ArbitraryType
    producing_state: "State | None" # shouldn't ever result in cyclic data, because the state that is used to compute an arbitrary value should only ever be an ancester of the state the stores it, but beware
    prefix: SymStr | None = None
    suffix: SymStr | None = None
    quoted: bool = False
    maybe_empty: bool = True

    def __eq__(self, other):
        # If the state producing this is unknown, conservatively say it can't be equal to any other
        # Another twist here, the producing state is only relevant for the APPROXIMATION kind, because
        # arbitrariness due to the environment should be the same regardless of state
        return isinstance(other, CompletelyArbitrary) \
            and self.source == other.source \
            and self.kind == other.kind \
            and (self.kind == ArbitraryType.ENVIRONMENT or self.producing_state == other.producing_state) \
            and self.producing_state is not None \
            and self.prefix == other.prefix \
            and self.suffix == other.suffix \
            and self.quoted == other.quoted \
            and self.maybe_empty == other.maybe_empty

    def __hash__(self):
        return hash((self.source, self.kind, self.producing_state if self.kind == ArbitraryType.APPROXIMATION else None, self.prefix, self.suffix, self.quoted, self.maybe_empty))

    def __repr__(self):
        return f"CompletelyArbitrary(s`{repr(self.source)[:30]}`, {self.kind}, state<{hash(self.producing_state)}>, pre:{self.prefix}, suf:{self.suffix}, quoted:{self.quoted}, maybe_empty:{self.maybe_empty})"


@dataclass(frozen=True)
class WordCount:
    min: int
    max: int | float  # use `math.inf` for infinity


@dataclass(frozen=True)
class Field:
    content: SymStr | CompletelyArbitrary
    count: WordCount

    def quote(self) -> "Field":
        content = self.content
        if isinstance(content, CompletelyArbitrary) and not content.quoted:
            content = replace(content, quoted=True)
        if isinstance(content, CompletelyArbitrary) and self.count.min > 0:
            content = replace(content, maybe_empty=False)
        max_words = min(self.count.max, 1)
        min_words = min(self.count.min, 1)
        if isinstance(content, CompletelyArbitrary) and content.quoted:
            if max_words > 0:
                min_words = max(min_words, 1)
        return Field(content, WordCount(min_words, max_words))

    def is_constant(self) -> bool:
        return isinstance(self.content, SymStr) and all(isinstance(p, str) for p in self.content.parts) and self.count.min == self.count.max

    def try_to_str(self) -> str | None:
        match self.content:
            case SymStr():
                return self.content.try_to_str()
            case _:
                return None

    def try_without_trailing_slash(self) -> "Field":
        if isinstance(self.content, SymStr) and isinstance(self.content.parts[-1], str):
            # only remove trailing slash if it's part of a string literal at the end
            first_parts = self.content.parts[:-1]
            last_part = self.content.parts[-1]
            if last_part.endswith("/"):
                new_path = Field(SymStr(first_parts + (last_part[:-1],)), self.count)
                return new_path

        # otherwise, do nothing
        return self

    @staticmethod
    def create_constant(s: str, words: int = 1) -> "Field":
        return Field(SymStr((s,)), WordCount(words, words))
