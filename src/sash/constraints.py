from __future__ import annotations  # for postponed evaluation of annotations

import functools
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import Enum, auto

from sash.symbolic.strings import Field


@dataclass(frozen=True)
class Constraint:

    # A & B (and)
    def __and__(self, other: Constraint) -> And:
        return And(self, other)

    # A | B (or)
    def __or__(self, other: Constraint) -> Or:
        return Or(self, other)

    # ~A (not)
    def __invert__(self) -> Not:
        return Not(self)

    # A >> B (implies)
    def __rshift__(self, other: Constraint) -> Implies:
        return Implies(self, other)


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
class IsUnread(Constraint):
    path: Field


@dataclass(frozen=True)
class IsWritten(Constraint):
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
    def add_stdin(io: IOType) -> "IOType":
        match io:
            case IOType.NONE | IOType.UNKNOWN:
                return IOType.STDIN
            case IOType.STDOUT:
                return IOType.BOTH
            case IOType.STDIN | IOType.BOTH:
                return io

    @staticmethod
    def add_stdout(io: IOType) -> "IOType":
        match io:
            case IOType.NONE | IOType.UNKNOWN:
                return IOType.STDOUT
            case IOType.STDIN:
                return IOType.BOTH
            case IOType.STDOUT | IOType.BOTH:
                return io

    @staticmethod
    def remove_stdin(io: IOType) -> "IOType":
        match io:
            case IOType.STDIN:
                return IOType.NONE
            case IOType.BOTH:
                return IOType.STDOUT
            case IOType.STDOUT | IOType.NONE | IOType.UNKNOWN:
                return io

    @staticmethod
    def remove_stdout(io: IOType) -> "IOType":
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


def normalize_fs_constraints(constraints: Constraint) -> Constraint:
    match constraints:
        case And(lhs, rhs):
            return And(normalize_fs_constraints(lhs), normalize_fs_constraints(rhs))
        case Or(lhs, rhs):
            return Or(normalize_fs_constraints(lhs), normalize_fs_constraints(rhs))
        case Implies(premise, conclusion):
            return Implies(normalize_fs_constraints(premise), normalize_fs_constraints(conclusion))
        case Not(Not(c)):
            return normalize_fs_constraints(c)
        case Not(IsDeleted(path)):
            return IsFile(path) | IsDir(path)
        case Not(IsFile(path)):
            return IsDeleted(path) | IsDir(path)
        case Not(IsDir(path)):
            return IsDeleted(path) | IsFile(path)
        case Not(IsUnread(path)):
            return IsRead(path)
        case Not(Or(lhs, rhs)):
            return normalize_fs_constraints(Not(lhs)) & normalize_fs_constraints(Not(rhs))
        case Not(And(lhs, rhs)):
            return normalize_fs_constraints(Not(lhs)) | normalize_fs_constraints(Not(rhs))
        case Not(c):
            return Not(normalize_fs_constraints(c))
        case IsFile() | IsDir() | IsDeleted() | IsUnread() | IsRead() | IsWritten():
            normalized_path = constraints.path.try_without_trailing_slash()
            return type(constraints)(normalized_path)
        case StringEq(lhs, rhs):
            normalized_lhs = lhs.try_without_trailing_slash()
            normalized_rhs = rhs.try_without_trailing_slash()
            return StringEq(normalized_lhs, normalized_rhs)
        case CommandExists() | Description():
            return constraints
        case Empty():
            return constraints
        case _:
            assert False, f"Unhandled constraint: {constraints}"
            return constraints


@dataclass(frozen=True)
class NormalizedFSConstraint(Constraint):
    constraint: Constraint

    def __post_init__(self):
        normalized = normalize_fs_constraints(self.constraint)
        object.__setattr__(self, 'constraint', normalized)
