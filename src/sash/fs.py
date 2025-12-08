import logging
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

import z3

from sash.constraints import (
    And,
    CommandExists,
    Constraint,
    Description,
    Empty,
    Implies,
    IsDeleted,
    IsDir,
    IsFile,
    IsRead,
    IsUnread,
    IsWritten,
    NormalizedFSConstraint,
    Not,
    Or,
    StringEq,
)
from sash.symbolic.strings import Field


@dataclass(frozen=True)
class FSModel():
    def apply_postcondition(self, norm_constraints: NormalizedFSConstraint) -> "FSModel":
        return self

    def is_file_z3(self, path_z3) -> 'z3.ExprRef':
        return z3.BoolVal(False)

    def is_dir_z3(self, path_z3) -> 'z3.ExprRef':
        return z3.BoolVal(False)

    def is_deleted_z3(self, path_z3) -> 'z3.ExprRef':
        return z3.BoolVal(False)

    def is_read_z3(self, path_z3) -> 'z3.ExprRef':
        return z3.BoolVal(False)

    def is_unread_z3(self, path_z3) -> 'z3.ExprRef':
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
FileInfo: Any = z3.Datatype('FileInfo')


# Add one 'constructor' called 'mk_pair'
# It takes two 'fields': 'state' and 'status'
FileInfo.declare(
    'mk_pair',
    ('state', StateSort),    # Accessor 'state' returns a StateSort
    ('status', ReadStatus)   # Accessor 'status' returns a ReadStatus
)


# Finalize the Datatype creation
FileInfo = FileInfo.create()


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

    def _next_state(self, z3array: z3.ExprRef) -> "FSModelSimple":
        return replace(self, id=self.id + 1, history=self.history + ((z3.Array(f'fs{self.id + 1}', z3.StringSort(), FileInfo), z3array),))

    # ASSUMPTION: `intermediate_history` ids do not overlap with self ids
    def _extend_history(self, z3array: z3.ExprRef, intermediate_history: tuple[tuple[z3.ExprRef, z3.ExprRef], ...] | None = None) -> "FSModelSimple":
        return replace(self, id=self.id + 1, history=self.history + (intermediate_history or ()) + ((z3.Array(f'fs{self.id + 1}', z3.StringSort(), FileInfo), z3array),))

    def _set(self, path: Field, state: z3.ExprRef, status: z3.ExprRef | None = Unread) -> "FSModelSimple":
        """Return a new abstract file system with the given path set to the given state."""
        return self._next_state(z3.Store(self.history[-1][0], self.field_to_z3(path), FileInfo.mk_pair(state, status)))

    def _delete(self, path: Field) -> "FSModelSimple":
        """Return a new abstract file system after removing the given path."""
        return self._set(path, Del, Unread)

    def _create_file(self, path: Field, status: z3.ExprRef | None = Unread) -> "FSModelSimple":
        """Return a new abstract file system after writing to the given path."""
        return self._set(path, File, status)

    def _create_dir(self, path: Field, status: z3.ExprRef | None = Unread) -> "FSModelSimple":
        """Return a new abstract file system after writing to the given path."""
        return self._set(path, Dir, status)

    def _apply_postcondition(self, constraints: Constraint) -> "FSModelSimple":
        logging.debug("Applying FS postcondition: %s", constraints)
        match constraints:
            case Empty():
                return self
            case And(lhs, rhs):
                fs_after_lhs = self._apply_postcondition(lhs)
                return fs_after_lhs._apply_postcondition(rhs)
            case Or(lhs, rhs):
                fs_after_lhs = self._apply_postcondition(lhs)
                fs_after_rhs = self._apply_postcondition(rhs)
                lhs_new_history = fs_after_lhs.history[len(self.history):]
                rhs_new_history = fs_after_rhs.history[len(self.history):]
                lhs_new_history_renamed = tuple((self._rename_fs_var_append(var, "_lhs"), array_expr) for var, array_expr in lhs_new_history)
                rhs_new_history_renamed = tuple((self._rename_fs_var_append(var, "_rhs"), array_expr) for var, array_expr in rhs_new_history)
                lhs_last_fs = lhs_new_history_renamed[-1][0]
                rhs_last_fs = rhs_new_history_renamed[-1][0]
                new_state = z3.If(z3.FreshBool("postcond_or"), lhs_last_fs, rhs_last_fs)
                return self._extend_history(new_state, lhs_new_history_renamed + rhs_new_history_renamed) # type: ignore
            case IsFile(path):
                return self._create_file(path)
            case IsDir(path):
                return self._create_dir(path)
            case IsDeleted(path):
                return self._delete(path)
            case IsRead(path):
                return self._create_file(path, Read)
            case IsWritten(path):
                # For simplicity, say that writing creates an unread file
                return self._create_file(path)
            case StringEq() | Not(StringEq()) | CommandExists() | Description() | IsUnread():
                # These constraints do not affect the FS model
                return self
            case Implies(premise, conclusion):
                fs_after_conclusion = self._apply_postcondition(conclusion)
                # Apply the postcondition, so we get the final state if the premise is true, and then add a new final state
                # that chooses between the old final state and the new final state based on the premise
                new_state = z3.If(self._fs_constraint_z3(premise), fs_after_conclusion.history[-1][0], self.history[-1][0])
                return fs_after_conclusion._next_state(new_state) # type: ignore
            case CommandExists() | Not(CommandExists()):
                # Does not affect FS model
                return self
            case Not(constraint):
                assert False, f"Unclear what Not means in postcond (is it un-normalized?): {constraint}"
                return self
            case _:
                assert False, f"Unhandled FS postcondition: {constraints}"
        return self

    def apply_postcondition(self, norm_constraints: NormalizedFSConstraint) -> FSModel:
        return self._apply_postcondition(norm_constraints.constraint)

    def is_file_z3(self, path_z3) -> 'z3.ExprRef':
        return z3.Or(FileInfo.state(z3.Select(self.history[-1][0], path_z3)) == File, FileInfo.state(z3.Select(self.history[-1][0], path_z3)) == Unknown)

    def is_dir_z3(self, path_z3) -> 'z3.ExprRef':
        return z3.Or(FileInfo.state(z3.Select(self.history[-1][0], path_z3)) == Dir, FileInfo.state(z3.Select(self.history[-1][0], path_z3)) == Unknown)

    def is_deleted_z3(self, path_z3) -> 'z3.ExprRef':
        return FileInfo.state(z3.Select(self.history[-1][0], path_z3)) == Del

    def is_read_z3(self, path_z3) -> 'z3.ExprRef':
        is_file_and_read = z3.And(FileInfo.state(z3.Select(self.history[-1][0], path_z3)) == File,
                      FileInfo.status(z3.Select(self.history[-1][0], path_z3)) == Read)
        is_unknown = FileInfo.state(z3.Select(self.history[-1][0], path_z3)) == Unknown
        return z3.Or(is_file_and_read, is_unknown)
    def is_unread_z3(self, path_z3) -> 'z3.ExprRef':
        return z3.And(FileInfo.state(z3.Select(self.history[-1][0], path_z3)) == File,
                      FileInfo.status(z3.Select(self.history[-1][0], path_z3)) == Unread)

    def _fs_constraint_z3(self, constraint: Constraint) -> 'z3.ExprRef':
        match constraint:
            case IsFile(path):
                return self.is_file_z3(self.field_to_z3(path))
            case IsDir(path):
                return self.is_dir_z3(self.field_to_z3(path))
            case IsDeleted(path):
                return self.is_deleted_z3(self.field_to_z3(path))
            case IsUnread(path):
                return self.is_unread_z3(self.field_to_z3(path))
            case And(lhs, rhs):
                return z3.And(self._fs_constraint_z3(lhs), self._fs_constraint_z3(rhs))
            case Or(lhs, rhs):
                return z3.Or(self._fs_constraint_z3(lhs), self._fs_constraint_z3(rhs))
            case Not(c):
                return z3.Not(self._fs_constraint_z3(c))
            case Empty():
                return z3.BoolVal(True)
            case StringEq(lhs, rhs):
                return self.field_to_z3(lhs) == self.field_to_z3(rhs)
            case _:
                assert False, f"FSModelSimple cannot evaluate constraint: {constraint}"

    def state_to_z3(self) -> 'z3.ExprRef':
        return z3.And([fsvar == array_expr for fsvar, array_expr in self.history])
