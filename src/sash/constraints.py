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

    def state_to_z3(self) -> 'z3.ExprRef':
        return z3.BoolVal(True)






StateSort, (File, Dir, Del, Unknown) = z3.EnumSort(
    "State", ["File", "Dir", "Del", "Unknown"]
)
ReadStatus, (Read, Unread) = z3.EnumSort(
    "ReadStatus", ["Read", "Unread"]
)

# 2. Define the "pair" Datatype
# This creates a new sort called 'FileInfo'
FileInfo = z3.Datatype('FileInfo')

# Add one 'constructor' called 'mk_pair'
# It takes two 'fields': 'state' and 'status'
FileInfo.declare(
    'mk_pair',
    ('state', StateSort),    # Accessor 'state' returns a StateSort
    ('status', ReadStatus)  # Accessor 'status' returns a ReadStatus
)

# Finalize the Datatype creation
FileInfo = FileInfo.create()

# z3_is_file = z3.Function('is_file', z3.ArraySort(z3.StringSort(), FileInfo), z3.StringSort(), z3.BoolSort())
# fs = z3.Const('fs', z3.ArraySort(z3.StringSort(), FileInfo))
# path = z3.Const('path', z3.StringSort())
# z3_is_file_axiom_body = z3.ForAll([fs, path],
#     z3_is_file(fs, path) == \
#     (fs.select(path).state == File) | (fs.select(path).state == Unknown)
# )

# z3_is_dir = z3.Function('is_dir', z3.ArraySort(z3.StringSort(), FileInfo), z3.StringSort(), z3.BoolSort())
# z3_is_dir_axiom_body = z3.ForAll([fs, path],
#     z3_is_dir(fs, path) == \
#     (fs.select(path).state == Dir) | (fs.select(path).state == Unknown)
# )

# z3_is_deleted = z3.Function('is_deleted', z3.ArraySort(z3.StringSort(), FileInfo), z3.StringSort(), z3.BoolSort())
# # important, is_deleted is only true if state is Del, not Unknown
# z3_is_deleted_axiom_body = z3.ForAll([fs, path],
#     z3_is_deleted(fs, path) == fs.select(path).state == Del)

@dataclass(frozen=True)
class FSModelSimple(FSModel):
    """A file system model mapping paths (`Field`s) to path states (`PathState`s)."""
    field_to_z3: Callable[[Field], 'z3.ExprRef'] = field(repr=False, compare=False, hash=False)

    id: int  = 0
    # history: (z3var for FS at time step `id`, z3 array representing FS at time step `id`)
    history: tuple[tuple[z3.ArrayRef, z3.ExprRef]] = field(default_factory=lambda: ((z3.Array('fs0', z3.StringSort(), FileInfo),
                                                                                    z3.K(z3.StringSort(), FileInfo.mk_pair(Unknown, Unread))),))

    def _rename_fs_var_append(self, var: z3.ArrayRef, suffix: str) -> z3.ExprRef:
        var_name = var.decl().name()
        return z3.Array(f'{var_name}{suffix}', z3.StringSort(), FileInfo)

    def _next_state(self, z3array: z3.ExprRef) -> FSModelSimple:
        return replace(self, id=self.id + 1, history=self.history + ((z3.Array(f'fs{self.id + 1}', z3.StringSort(), FileInfo), z3array),))

    # ASSUMPTION: `intermediate_history` ids do not overlap with self ids
    def _extend_history(self, z3array: z3.ExprRef, intermediate_history: Optional[tuple[tuple[z3.ExprRef, z3.ExprRef], ...]] = None) -> FSModelSimple:
        return replace(self, id=self.id + 1, history=self.history + (intermediate_history or ()) + ((z3.Array(f'fs{self.id + 1}', z3.StringSort(), FileInfo), z3array),))

    def _set(self, path: Field, state: z3.ExprRef, status: Optional[z3.ExprRef] = Unread) -> FSModelSimple:
        """Return a new abstract file system with the given path set to the given state."""
        return self._next_state(z3.Store(self.history[-1][0], self.field_to_z3(path), FileInfo.mk_pair(state, status)))

    def _delete(self, path: Field) -> FSModelSimple:
        """Return a new abstract file system after removing the given path."""
        return self._set(path, Del, Unread)

    def _create_file(self, path: Field, status: Optional[z3.ExprRef] = Unread) -> FSModelSimple:
        """Return a new abstract file system after writing to the given path."""
        return self._set(path, File, status)

    def _create_dir(self, path: Field, status: Optional[z3.ExprRef] = Unread) -> FSModelSimple:
        """Return a new abstract file system after writing to the given path."""
        return self._set(path, Dir, status)

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
                fs_after_rhs = self.apply_postcondition(rhs)
                lhs_new_history = fs_after_lhs.history[len(self.history):]
                rhs_new_history = fs_after_rhs.history[len(self.history):]
                lhs_new_history_renamed = tuple((self._rename_fs_var_append(var, "_lhs"), array_expr) for var, array_expr in lhs_new_history)
                rhs_new_history_renamed = tuple((self._rename_fs_var_append(var, "_rhs"), array_expr) for var, array_expr in rhs_new_history)
                lhs_last_fs = lhs_new_history_renamed[-1][0]
                rhs_last_fs = rhs_new_history_renamed[-1][0]
                new_state = z3.If(z3.FreshBool("postcond_or"), lhs_last_fs, rhs_last_fs)
                return self._extend_history(new_state, lhs_new_history_renamed + rhs_new_history_renamed) # type: ignore
            case Not(IsDeleted(path)):
                return self.apply_postcondition(IsFile(path) | IsDir(path))
            case Not(IsFile(path)):
                return self.apply_postcondition(IsDeleted(path) | IsDir(path))
            case Not(IsDir(path)):
                return self.apply_postcondition(IsDeleted(path) | IsFile(path))
            case Not(Or(lhs, rhs)):
                return self.apply_postcondition(Not(lhs) & Not(rhs))
            case Not(And(lhs, rhs)):
                return self.apply_postcondition(Not(lhs) | Not(rhs))
            case Implies(premise, conclusion):
                fs_after_conclusion = self.apply_postcondition(conclusion)
                # Apply the postcondition, so we get the final state if the premise is true, and then add a new final state
                # that chooses between the old final state and the new final state based on the premise
                new_state = z3.If(premise, fs_after_conclusion.history[-1][0], self.history[-1][0])
                return fs_after_conclusion._next_state(new_state) # type: ignore
            case IsFile(path):
                return self._create_file(path)
            case IsDir(path):
                return self._create_dir(path)
            case IsDeleted(path):
                return self._delete(path)
            case Reads(path):
                return self._create_file(path, Read)
            case Writes(path):
                # For simplicity, say that writing creates an unread file
                return self._create_file(path)
            case StringEq() | Not(StringEq()) | CommandExists() | HasStdout() | ExpectsStdin() | Description() | IsUnread():
                # These constraints do not affect the FS model
                return self
            case Not(constraint):
                assert False, f"Unclear what Not means in postcond: {constraints}"
                return self
            case _:
                assert False, f"Unhandled FS postcondition: {constraints}"
        return self

    def is_file_z3(self, path_z3) -> 'z3.ExprRef':
        return z3.Or(FileInfo.state(z3.Select(self.history[-1][0], path_z3)) == File, FileInfo.state(z3.Select(self.history[-1][0], path_z3)) == Unknown)

    def is_dir_z3(self, path_z3) -> 'z3.ExprRef':
        return z3.Or(FileInfo.state(z3.Select(self.history[-1][0], path_z3)) == Dir, FileInfo.state(z3.Select(self.history[-1][0], path_z3)) == Unknown)

    def is_deleted_z3(self, path_z3) -> 'z3.ExprRef':
        return FileInfo.state(z3.Select(self.history[-1][0], path_z3)) == Del

    def is_unread_z3(self, path_z3) -> 'z3.ExprRef':
        return FileInfo.status(z3.Select(self.history[-1][0], path_z3)) == Unread

    def state_to_z3(self) -> 'z3.ExprRef':
        return z3.And([fsvar == array_expr for fsvar, array_expr in self.history])
