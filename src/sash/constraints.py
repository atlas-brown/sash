from __future__ import annotations  # for postponed evaluation of annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from sash.state import SymStr

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
    lhs: SymStr
    rhs: SymStr

@dataclass(frozen=True)
class IsFile(Constraint):
    path: SymStr

@dataclass(frozen=True)
class IsDir(Constraint):
    path: SymStr

@dataclass(frozen=True)
class IsDeleted(Constraint):
    path: SymStr

@dataclass(frozen=True)
class ReadsPath(Constraint):
    path: SymStr

@dataclass(frozen=True)
class WritesPath(Constraint):
    path: SymStr

@dataclass(frozen=True)
class CommandExists(Constraint):
    name: SymStr

@dataclass(frozen=True)
class HasStdout(Constraint):
    command: SymStr

@dataclass(frozen=True)
class ExpectsStdin(Constraint):
    command: SymStr
