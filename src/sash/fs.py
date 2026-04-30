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
    NormalizedConstraint,
    Not,
    Or,
    StringConcat,
    StringEq,
)
from sash.util import _as_boolref
from sash.symbolic.strings import Field


@dataclass(frozen=True)
class FSModel():
    def apply_postcondition(self, norm_constraints: NormalizedConstraint) -> "FSModel":
        return self

    def is_file_z3(self, path_z3) -> z3.BoolRef:
        return z3.BoolVal(False)

    def is_dir_z3(self, path_z3) -> z3.BoolRef:
        return z3.BoolVal(False)

    def is_deleted_z3(self, path_z3) -> z3.BoolRef:
        return z3.BoolVal(False)

    def is_read_z3(self, path_z3) -> z3.BoolRef:
        return z3.BoolVal(False)

    def state_to_z3(self) -> z3.BoolRef:
        return z3.BoolVal(True)


StateSort, (File, Dir, Del) = z3.EnumSort(
    "State", ["File", "Dir", "Del"]
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
    history: tuple[tuple[z3.ArrayRef, z3.ExprRef | None], ...] = field(default_factory=lambda: ((z3.Array('fs0', z3.StringSort(), FileInfo), None),))

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

    def _apply_postcondition(self, constraints: Constraint, reference_state: "FSModelSimple") -> "FSModelSimple":
        logging.debug("Applying FS postcondition: %s", constraints)
        match constraints:
            case Empty():
                return self
            case And(lhs, rhs):
                fs_after_lhs = self._apply_postcondition(lhs, reference_state)
                return fs_after_lhs._apply_postcondition(rhs, reference_state)
            case Or(lhs, rhs):
                fs_after_lhs = self._apply_postcondition(lhs, reference_state)
                fs_after_rhs = self._apply_postcondition(rhs, reference_state)
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
            case Not(IsRead(path)):
                return self._create_file(path, Unread) # treat Not(IsRead) as making the file unreadable
            case StringEq() | Not(StringEq()) | StringConcat() | Not(StringConcat()) | CommandExists() | Description():
                # These constraints do not affect the FS model
                return self
            case Implies(premise, conclusion):
                fs_after_conclusion = self._apply_postcondition(conclusion, reference_state)
                # Apply the postcondition, so we get the final state if the premise is true, and then add a new final state
                # that chooses between the old final state and the new final state based on the premise
                new_state = z3.If(reference_state._fs_constraint_z3(premise), fs_after_conclusion.history[-1][0], self.history[-1][0])
                return fs_after_conclusion._next_state(new_state) # type: ignore
            case CommandExists() | Not(CommandExists()):
                # Does not affect FS model
                return self
            case Not(constraint):
                assert False, f"'Not' should not appear in normalized constraints (got {constraint})"
            case _:
                assert False, f"all constraints should be handled (got {constraints})"
        return self

    def apply_postcondition(self, norm_constraints: NormalizedConstraint) -> FSModel:
        return self._apply_postcondition(norm_constraints.constraint, self)

    def is_file_z3(self, path_z3) -> z3.BoolRef:
        return _as_boolref(FileInfo.state(z3.Select(self.history[-1][0], path_z3)) == File)

    def is_dir_z3(self, path_z3) -> z3.BoolRef:
        return _as_boolref(FileInfo.state(z3.Select(self.history[-1][0], path_z3)) == Dir)

    def is_deleted_z3(self, path_z3) -> z3.BoolRef:
        return _as_boolref(FileInfo.state(z3.Select(self.history[-1][0], path_z3)) == Del)

    def is_read_z3(self, path_z3) -> z3.BoolRef:
        is_file_and_read = z3.And(
            FileInfo.state(z3.Select(self.history[-1][0], path_z3)) == File,
            FileInfo.status(z3.Select(self.history[-1][0], path_z3)) == Read,
        )
        return _as_boolref(is_file_and_read)

    def _fs_constraint_z3(self, constraint: Constraint) -> z3.BoolRef:
        match constraint:
            case IsFile(path):
                return self.is_file_z3(self.field_to_z3(path))
            case IsRead(path):
                return self.is_read_z3(self.field_to_z3(path))
            case IsDir(path):
                return self.is_dir_z3(self.field_to_z3(path))
            case IsDeleted(path):
                return self.is_deleted_z3(self.field_to_z3(path))
            case And(lhs, rhs):
                return _as_boolref(z3.And(self._fs_constraint_z3(lhs), self._fs_constraint_z3(rhs)))
            case Or(lhs, rhs):
                return _as_boolref(z3.Or(self._fs_constraint_z3(lhs), self._fs_constraint_z3(rhs)))
            case Not(c):
                return _as_boolref(z3.Not(self._fs_constraint_z3(c)))
            case Empty():
                return z3.BoolVal(True)
            case StringEq(lhs, rhs):
                return _as_boolref(self.field_to_z3(lhs) == self.field_to_z3(rhs))
            case StringConcat(result, parts):
                return _as_boolref(self.field_to_z3(result) == z3.Concat(*[self.field_to_z3(p) for p in parts]))
            case _:
                assert False, f"all constraints should be handled (got {constraint})"

    def state_to_z3(self) -> z3.BoolRef:
        exprs: list[z3.BoolRef] = []
        logging.debug("Converting FSModelSimple to z3")
        for fsvar, arr_expr in self.history:
            logging.debug(f"s2z3: fsvar={fsvar}, arr_expr={arr_expr}")
            if arr_expr is not None:
                exprs.append(_as_boolref(fsvar == arr_expr))
        return _as_boolref(z3.And(*exprs))

    def set_default_path_state(self, default: z3.ExprRef) -> "FSModelSimple":
        """Return a new FSModelSimple where any unknown paths default to the given state."""
        base_fs_array = z3.K(z3.StringSort(), default)
        logging.debug(f"Setting default path state to {base_fs_array}")
        new_history = ((self.history[0][0], base_fs_array),) + self.history[1:]
        return replace(self, history=new_history)
