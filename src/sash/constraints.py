import functools
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import Enum, auto

from sash.symbolic.strings import Field


@dataclass(frozen=True)
class Constraint:

    # A & B (and)
    def __and__(self, other: "Constraint") -> "And":
        return And(self, other)

    # A | B (or)
    def __or__(self, other: "Constraint") -> "Or":
        return Or(self, other)

    # ~A (not)
    def __invert__(self) -> "Not":
        return Not(self)

    # A >> B (implies)
    def __rshift__(self, other: "Constraint") -> "Implies":
        return Implies(self, other)

    def normalized(self) -> "NormalizedConstraint":
        return NormalizedConstraint(self)


@dataclass(frozen=True)
class NormalizedConstraint(Constraint):
    constraint: Constraint

    def __post_init__(self):
        def normalize(constraint: Constraint) -> Constraint:
            match constraint:
                # Already normalized
                case Empty() | Description(_) | CommandExists(_):
                    return constraint

                # Unwrap nested normalized constraints
                case NormalizedConstraint(norm):
                    return norm

                # Push normalization recursively down the constraint tree
                case And(c1, c2) | Or(c1, c2) | Implies(premise=c1, conclusion=c2):
                    norm_c1 = normalize(c1)
                    norm_c2 = normalize(c2)
                    return type(constraint)(norm_c1, norm_c2)

                # Double negation elimination
                case Not(Not(c)):
                    return normalize(c)

                # Negation of basic FS constraints
                case Not(IsDeleted(path)):
                    norm_path = path.try_without_trailing_slash()
                    return IsFile(norm_path) | IsDir(norm_path)
                case Not(IsFile(path)):
                    norm_path = path.try_without_trailing_slash()
                    return IsDir(norm_path) | IsDeleted(norm_path)
                case Not(IsDir(path)):
                    norm_path = path.try_without_trailing_slash()
                    return IsFile(norm_path) | IsDeleted(norm_path)

                # De Morgan's laws
                case Not(Or(c1, c2)):
                    return normalize(Not(c1) & Not(c2))
                case Not(And(c1, c2)):
                    return normalize(Not(c1) | Not(c2))

                # Negation of other constraints
                case Not(c):
                    return Not(normalize(c))

                # Normalize paths by removing trailing slashes
                case IsFile(path) | IsRead(path) | IsDir(path) | IsDeleted(path):
                    norm_path = path.try_without_trailing_slash()
                    return type(constraint)(norm_path)
                case StringEq(c1, c2):
                    norm_c1 = c1.try_without_trailing_slash()
                    norm_c2 = c2.try_without_trailing_slash()
                    return StringEq(norm_c1, norm_c2)

            assert False, f"Unhandled constraint: {constraint}"

        object.__setattr__(self, "constraint", normalize(self.constraint))


@dataclass(frozen=True)
class Empty(Constraint):
    pass


@dataclass(frozen=True)
class And(Constraint):
    lhs: Constraint
    rhs: Constraint

    @staticmethod
    def from_iter(cons: Iterable[Constraint]) -> Constraint:
        it = iter(cons)
        try:
            first = next(it)
        except StopIteration:
            return Empty()  # iterable is empty
        return functools.reduce(And, it, first) # returns first if only one element, othwise reduces with And

    @staticmethod
    def from_field_iter(cons: Iterable[Field], tfm: Callable[[Field], Constraint]) -> Constraint:
        return And.from_iter((tfm(c) for c in cons))


@dataclass(frozen=True)
class Or(Constraint):
    lhs: Constraint
    rhs: Constraint

    @staticmethod
    def from_iter(cons: Iterable[Constraint]) -> Constraint:
        it = iter(cons)
        try:
            first = next(it)
        except StopIteration:
            return Empty()  # iterable is empty
        return functools.reduce(Or, it, first) # returns first if only one element, othwise reduces with Or

    @staticmethod
    def from_field_iter(cons: Iterable[Field], tfm: Callable[[Field], Constraint]) -> Constraint:
        return Or.from_iter((tfm(c) for c in cons))


@dataclass(frozen=True)
class Not(Constraint):
    constraint: Constraint


@dataclass(frozen=True)
class Implies(Constraint):
    premise: Constraint
    conclusion: Constraint


@dataclass(frozen=True)
class StringEq(Constraint):
    lhs: Field
    rhs: Field


@dataclass(frozen=True)
class IsFile(Constraint):
    path: Field


@dataclass(frozen=True)
class IsDir(Constraint):
    path: Field


@dataclass(frozen=True)
class IsDeleted(Constraint):
    path: Field


@dataclass(frozen=True)
class IsRead(Constraint):
    path: Field


@dataclass(frozen=True)
class CommandExists(Constraint):
    name: Field


class IOType(Enum):
    NONE = auto()
    STDIN = auto()
    STDOUT = auto()
    BOTH = auto()
    UNKNOWN = auto()

    @staticmethod
    def add_stdin(io: "IOType") -> "IOType":
        match io:
            case IOType.NONE | IOType.UNKNOWN:
                return IOType.STDIN
            case IOType.STDOUT:
                return IOType.BOTH
            case IOType.STDIN | IOType.BOTH:
                return io

    @staticmethod
    def add_stdout(io: "IOType") -> "IOType":
        match io:
            case IOType.NONE | IOType.UNKNOWN:
                return IOType.STDOUT
            case IOType.STDIN:
                return IOType.BOTH
            case IOType.STDOUT | IOType.BOTH:
                return io

    @staticmethod
    def remove_stdin(io: "IOType") -> "IOType":
        match io:
            case IOType.STDIN:
                return IOType.NONE
            case IOType.BOTH:
                return IOType.STDOUT
            case IOType.STDOUT | IOType.NONE | IOType.UNKNOWN:
                return io

    @staticmethod
    def remove_stdout(io: "IOType") -> "IOType":
        match io:
            case IOType.STDOUT:
                return IOType.NONE
            case IOType.BOTH:
                return IOType.STDIN
            case IOType.STDIN | IOType.NONE | IOType.UNKNOWN:
                return io


@dataclass(frozen=True)
class Description(Constraint):
    text: str
