from collections.abc import Generator, Callable
from typing import Any

import shasta.ast_node as AST
import z3

from typing import TYPE_CHECKING

from sash.constraints import And, Constraint, Implies, Not, Or, StringEq
from dataclasses import replace

import logging

from sash.interpreter_config import PROTECTED_PATHS
from sash.symbolic.strings import CompletelyArbitrary, Field
if TYPE_CHECKING:
    from sash.symbolic.state import Trace


def shasta_pretty(ast_node) -> str:
    return ast_node.pretty() if hasattr(ast_node, 'pretty') else str(ast_node)


def iter_ast_command(node: AST.Command, skip: list[type[AST.Command]] = []) -> Generator[AST.Command]:
    if type(node) in skip:
        return

    yield node

    match node:
        case AST.PipeNode():
            for i in node.items:
                yield from iter_ast_command(i, skip)
        case AST.CommandNode():
            pass
        case AST.SubshellNode():
            yield from iter_ast_command(node.body, skip)
        case AST.AndNode():
            yield from iter_ast_command(node.left_operand, skip)
            yield from iter_ast_command(node.right_operand, skip)
        case AST.OrNode():
            yield from iter_ast_command(node.left_operand, skip)
            yield from iter_ast_command(node.right_operand, skip)
        case AST.SemiNode():
            yield from iter_ast_command(node.left_operand, skip)
            yield from iter_ast_command(node.right_operand, skip)
        case AST.NotNode():
            yield from iter_ast_command(node.body, skip)
        case AST.RedirNode():
            yield from iter_ast_command(node.node, skip)
        case AST.BackgroundNode():
            yield from iter_ast_command(node.node, skip)
        case AST.DefunNode():
            yield from iter_ast_command(node.body, skip)
        case AST.ForNode():
            yield from iter_ast_command(node.body, skip)
        case AST.WhileNode():
            yield from iter_ast_command(node.test, skip)
            yield from iter_ast_command(node.body, skip)
        case AST.IfNode():
            yield from iter_ast_command(node.cond, skip)
            yield from iter_ast_command(node.then_b, skip)
            if node.else_b:
                yield from iter_ast_command(node.else_b, skip)
        case AST.CaseNode():
            for case in node.cases:
                if case["cbody"] is not None:
                    yield from iter_ast_command(case["cbody"], skip)
        case _:
            pass


def iter_argchar_list(nodes: list[AST.ArgChar], skip: list[type[AST.ArgChar]]) -> Generator[AST.ArgChar]:
    for node in nodes:
        if type(node) in skip:
            continue

        yield node

        match node:
            case AST.CArgChar() | AST.EArgChar() | AST.TArgChar() | AST.BArgChar():
                pass
            case AST.AArgChar() | AST.VArgChar() | AST.QArgChar():
                yield from iter_argchar_list(node.arg, skip)


def iter_constraint(node: Constraint, skip: list[type[Constraint]]) -> Generator[Constraint]:
    if type(node) in skip:
        return

    yield node

    match node:
        case And():
            yield from iter_constraint(node.lhs, skip)
            yield from iter_constraint(node.rhs, skip)
        case Or():
            yield from iter_constraint(node.lhs, skip)
            yield from iter_constraint(node.rhs, skip)
        case Not():
            yield from iter_constraint(node.constraint, skip)
        case Implies():
            yield from iter_constraint(node.premise, skip)
            yield from iter_constraint(node.conclusion, skip)
        case _:
            pass


def create_fresh_varname(prefix: str | None = None) -> str:
    prefix = prefix if prefix is not None else "vr"
    return str(z3.FreshConst(z3.StringSort(),prefix))


def _as_boolref(expr: Any) -> z3.BoolRef:
    assert isinstance(expr, z3.BoolRef)
    return expr

def partition(l: list, predicate: Callable) -> tuple[list, list]:
    """Partition a sequence into two tuples based on a predicate."""
    trues = []
    falses = []
    for item in l:
        if predicate(item):
            trues.append(item)
        else:
            falses.append(item)
    return trues, falses


def field_core_key(field: Field) -> CompletelyArbitrary | None:
    match field.content:
        case CompletelyArbitrary() as content:
            return replace(content, prefix=None, suffix=None, quoted=False, maybe_empty=False)
        case _:
            return None

def is_empty_constant(field: Field) -> bool:
    return field.try_to_str() == ""

def is_non_empty_constant(field: Field) -> bool:
    field_str = field.try_to_str()
    return field_str is not None and field_str != ""

def constraint_implies_non_empty(core: CompletelyArbitrary, constraint: Constraint) -> bool:
    norm = constraint.normalized().constraint
    logging.debug("Checking if constraint %s implies non-empty for core %s", norm, core)
    match norm:
        case StringEq(lhs, rhs):
            if core == field_core_key(lhs) and is_non_empty_constant(rhs):
                return True
            if core == field_core_key(rhs) and is_non_empty_constant(lhs):
                return True
            return False
        case Not(StringEq(lhs, rhs)):
            if core == field_core_key(lhs) and is_empty_constant(rhs):
                return True
            if core == field_core_key(rhs) and is_empty_constant(lhs):
                return True
            return False
        case And(lhs, rhs):
            return constraint_implies_non_empty(core, lhs) or constraint_implies_non_empty(core, rhs)
        case Or(lhs, rhs):
            return constraint_implies_non_empty(core, lhs) and constraint_implies_non_empty(core, rhs)
        case _:
            return False

def is_definitely_non_empty(field: Field, trace: "Trace") -> bool:
    logging.debug("Checking if field %s is definitely non-empty", field)
    core = field_core_key(field)
    logging.debug("Extracted core: %s", core)
    if core is None:
        return False
    logging.debug("Checking path conditions for non-emptiness implications, have %d conditions", len(trace.latest_state.pathcond))
    return any(constraint_implies_non_empty(core, cond.constraint) for cond in trace.latest_state.pathcond)

def is_protected(path):
    return any(path in [p, p + "/", p + "/*"] for p in PROTECTED_PATHS)

def is_flag(field: Field) -> bool:
    field_str = field.try_to_str()
    return field_str is not None and field_str.startswith("-")


def is_user_directory(path: str) -> bool:
    """Return True for user home directories like /home/alice, /home/alice/, /home/alice/*."""
    normalized = path.strip()
    if normalized.endswith("/*"):
        normalized = normalized[:-2]
    if normalized.endswith("/"):
        normalized = normalized[:-1]
    parts = normalized.split("/")
    return len(parts) == 3 and parts[0] == "" and parts[1] == "home" and parts[2] not in {"", "*", ".", ".."}
