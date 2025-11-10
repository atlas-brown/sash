from __future__ import annotations  # for postponed evaluation of annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from sash.state import *
from collections.abc import Iterable
import z3
from dataclasses import dataclass, field, replace
from enum import Enum
from sash.frozen import FrozenDict
import logging
import functools
from enum import Enum, auto

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
    def __rshift__(self, other: Constraint) -> Or:
        return (~self) | other

@dataclass(frozen=True)
class Empty(Constraint):
    pass

@dataclass(frozen=True)
class Not(Constraint):
    constraint: Constraint

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
class IsUnread(Constraint):
    path: Field

@dataclass(frozen=True)
class Reads(Constraint):
    path: Field

@dataclass(frozen=True)
class Writes(Constraint):
    path: Field

@dataclass(frozen=True)
class CommandExists(Constraint):
    name: Field

class IOType(Enum):
    NONE = auto()
    STDIN = auto()
    STDOUT = auto()
    BOTH = auto()

@dataclass(frozen=True)
class HasStdout(Constraint):
    command: Field

@dataclass(frozen=True)
class ExpectsStdin(Constraint):
    command: Field

@dataclass(frozen=True)
class Description(Constraint):
    text: str

@dataclass(frozen=True)
class FSModel():
    def apply_postcondition(self, constraints: Constraint) -> FSModel:
        return self

    def is_file_z3(self, path_z3) -> 'z3.ExprRef':
        return z3.BoolVal(False)

    def is_dir_z3(self, path_z3) -> 'z3.ExprRef':
        return z3.BoolVal(False)

    def is_deleted_z3(self, path_z3) -> 'z3.ExprRef':
        return z3.BoolVal(False)

    def state_to_z3(self, field_to_z3: Callable) -> 'z3.ExprRef':
        return z3.BoolVal(True)

@dataclass(frozen=True)
class FSModelSimple(FSModel):
    """A file system model mapping paths (`Field`s) to path states (`PathState`s)."""

    class PathType(Enum):
        FILE = 1
        DIR = 2
        DELETED = 3

    @dataclass(frozen=True)
    class PathState():
        path_type: FSModelSimple.PathType
        is_read: bool = False

        @property
        def exists(self):
            return self.path_type != FSModelSimple.PathType.DELETED

    state: FrozenDict[Field, PathState] = field(default_factory=FrozenDict)
    state_z3: z3.ArrayRef = field(default_factory=lambda: z3.Array('fs', z3.StringSort(), z3.IntSort()))

    def _delete(self, path: Field) -> FSModelSimple:
        """Return a new abstract file system after removing the given path."""
        return self._set(path, FSModelSimple.PathState(FSModelSimple.PathType.DELETED))

    def _create_file(self, path: Field) -> FSModelSimple:
        """Return a new abstract file system after writing to the given path."""
        new_state = FSModelSimple.PathState(path_type=FSModelSimple.PathType.FILE)
        return self._set(path, new_state)

    def _create_dir(self, path: Field) -> FSModelSimple:
        """Return a new abstract file system after writing to the given path."""
        new_state = FSModelSimple.PathState(path_type=FSModelSimple.PathType.DIR)
        return self._set(path, new_state)

    def _get(self, path: Field) -> PathState:
        """Get the abstract state for a given path, or None if not present."""
        try:
            return self.state[path]
        except KeyError:
            return FSModelSimple.PathState(FSModelSimple.PathType.DELETED)

    def _set(self, path: Field, state: PathState) -> FSModelSimple:
        """Return a new abstract file system with the given path set to the given state."""
        new_entries = self.state.set(path, state)
        return replace(self, state=new_entries)

    def apply_postcondition(self, constraints: Constraint) -> FSModelSimple:
        logging.debug(f"Applying FS postcondition: {constraints}")
        match constraints:
            case Empty():
                return self
            case And(lhs, rhs):
                fs_after_lhs = self.apply_postcondition(lhs)
                return fs_after_lhs.apply_postcondition(rhs)
            case Or(lhs, rhs):
                fs_after_lhs = self.apply_postcondition(lhs)
            case IsDeleted(path):
                return self._delete(path)
            case IsFile(path):
                return self._create_file(path)
            case IsDir(path):
                return self._create_dir(path)
            case Writes(path):
                # For simplicity, assume writing creates a file
                return self._create_file(path)
            case _:
                assert False, f"Unhandled FS postcondition: {constraints}"
        return self

    def is_file_z3(self, path_z3) -> 'z3.ExprRef':
        logging.debug(f"Checking is_file_z3 for path: {path_z3}")
        return self.state_z3[path_z3] == z3.IntVal(FSModelSimple.PathType.FILE.value)

    def is_dir_z3(self, path_z3) -> 'z3.ExprRef':
        logging.debug(f"Checking is_dir_z3 for path: {path_z3}")
        return self.state_z3[path_z3] == z3.IntVal(FSModelSimple.PathType.DIR.value)

    def is_deleted_z3(self, path_z3) -> 'z3.ExprRef':
        logging.debug(f"Checking is_deleted_z3 for path: {path_z3}")
        return self.state_z3[path_z3] == z3.IntVal(FSModelSimple.PathType.DELETED.value)

    def state_to_z3(self, field_to_z3: Callable) -> 'z3.ExprRef':
        logging.debug(f"FS state: {self.state}")
        logging.debug(f"Z3 FS state: {self.state_z3}")
        constraints = []
        for path, st in self.state.items():
            path_z3 = field_to_z3(path.content)
            constraints.append(self.state_z3[path_z3] == z3.IntVal(st.path_type.value))
        return z3.And(constraints) if constraints else z3.BoolVal(True)
