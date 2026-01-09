from collections.abc import Generator

import shasta.ast_node as AST
import z3

from sash.constraints import And, Constraint, Implies, Not, Or


def split_at(l: list, element) -> list[list]:
    """
    Split a list at each occurrence of element, returning a list of lists, none of which contain `element`.
    Examples:
    >>> split_at([1, 2, None, 3, None, 4], None)
    [[1, 2], [3], [4]]
    >>> split_at([1, 2, 3], None)
    [[1, 2, 3]]
    >>> split_at([1, 2, None, None, 3, None, 4], None)
    [[1, 2], [], [3], [4]]
    """
    result = []
    current = []
    for item in l:
        if item == element:
            result.append(current)
            current = []
        else:
            current.append(item)
    result.append(current)
    return result


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
