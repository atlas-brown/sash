from __future__ import annotations  # for postponed evaluation of annotations
from abc import ABC
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from sash.state import *

from dataclasses import dataclass

class Constraint:
    pass

class Empty(Constraint):
    pass

@dataclass(frozen=True)
class Not(Constraint):
    constraint: Constraint

@dataclass(frozen=True)
class And(Constraint):
    lhs: Constraint
    rhs: Constraint

@dataclass(frozen=True)
class Or(Constraint):
    lhs: Constraint
    rhs: Constraint

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

@dataclass(frozen=True)
class HasStdout(Constraint):
    command: Field

@dataclass(frozen=True)
class ExpectsStdin(Constraint):
    command: Field

@dataclass(frozen=True)
class FSModel(ABC):
    def apply_postcondition(self, constraints: Constraint) -> FSModel:
        return self

class DumbFsModel(FSModel):
    pass
