from collections.abc import Generator
from typing import Iterable, Optional

import shasta.ast_node as AST
import z3

from sash.state import Field, SymStr, SymVar


def iter_ast_commands(node: AST.Command, skip: list[type[AST.Command]] = []) -> Generator[AST.Command]:
    if type(node) in skip:
        return

    yield node

    match node:
        case AST.PipeNode():
            for i in node.items:
                yield from iter_ast_commands(i, skip)
        case AST.CommandNode():
            pass
        case AST.SubshellNode():
            yield from iter_ast_commands(node.body, skip)
        case AST.AndNode():
            yield from iter_ast_commands(node.left_operand, skip)
            yield from iter_ast_commands(node.right_operand, skip)
        case AST.OrNode():
            yield from iter_ast_commands(node.left_operand, skip)
            yield from iter_ast_commands(node.right_operand, skip)
        case AST.SemiNode():
            yield from iter_ast_commands(node.left_operand, skip)
            yield from iter_ast_commands(node.right_operand, skip)
        case AST.NotNode():
            yield from iter_ast_commands(node.body, skip)
        case AST.RedirNode():
            yield from iter_ast_commands(node.node, skip)
        case AST.BackgroundNode():
            yield from iter_ast_commands(node.node, skip)
        case AST.DefunNode():
            yield from iter_ast_commands(node.body, skip)
        case AST.ForNode():
            yield from iter_ast_commands(node.body, skip)
        case AST.WhileNode():
            yield from iter_ast_commands(node.test, skip)
            yield from iter_ast_commands(node.body, skip)
        case AST.IfNode():
            yield from iter_ast_commands(node.cond, skip)
            yield from iter_ast_commands(node.then_b, skip)
            if node.else_b:
                yield from iter_ast_commands(node.else_b, skip)
        case AST.CaseNode():
            for case in node.cases:
                yield from iter_ast_commands(case["cbody"], skip)
        case _:
            pass


def symbstr_to_str(symbstr : Iterable[str | SymVar]) -> str | None:
    nls : list[str] = []
    for i in symbstr:
        if isinstance(i,str):
            nls.append(i)
        else:
            return None
    return "".join(nls)


def is_constant(field: Field) -> bool:
    return isinstance(field.content, SymStr) and symbstr_to_str(field.content.parts) is not None


def create_fresh_varname(prefix:Optional[str] = None) -> str:
    prefix = prefix if prefix is not None else "vr"
    return str(z3.FreshConst(z3.StringSort(),prefix))


def create_fresh_var(prefix:Optional[str] = None) -> SymVar:
    return SymVar(create_fresh_varname(prefix))
