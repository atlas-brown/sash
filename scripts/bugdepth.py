import shasta.ast_node as AST
import sash.parser as parser
from typing import Callable
from dataclasses import dataclass

# set this
is_target: Callable[[AST.AstNode], bool] = lambda x: False

@dataclass
class Found(Exception):
    conds_before: int

def interp_arg(arg: AST.ArgChar,
               conds_so_far: int) -> int:
    match arg:
        case AST.QArgChar() as q:
            res = conds_so_far
            for thing in q.arg:
                res = interp_arg(thing, res)
            return res
        case AST.BArgChar() as b:
            return interp_node(b.node, conds_so_far)
        case _:
            return conds_so_far

def interp_args(args, conds_so_far: int) -> int:
    res = conds_so_far
    for arg in args:
        res = interp_arg(arg, res)
    return res

fns = {}
stack = []

def interp_node(node: AST.AstNode,
                conds_so_far: int) -> int:
    if is_target(node):
        raise Found(conds_so_far)

    match node:
        # Branching constructs counted:
        case AST.IfNode():
            conds_so_far = interp_node(node.cond, conds_so_far)
            left = interp_node(node.then_b, conds_so_far + 1)
            right = interp_node(node.else_b, conds_so_far + 1)
            return min(left, right)

        case AST.CaseNode():
            res = []
            for acase in node.cases:
                res.append(interp_node(acase["cbody"], conds_so_far + 1))
            return min(*res)

        case AST.WhileNode():
            conds_so_far = interp_node(node.test, conds_so_far)
            return interp_node(node.body, conds_so_far + 1)

        case AST.AndNode() | AST.OrNode():
            conds_so_far = interp_node(node.left_operand, conds_so_far + 1)
            return interp_node(node.right_operand, conds_so_far)




        # Other nodes just walked in evaluation order
        case AST.CommandNode():
            if len(node.arguments) == 0:
                res = conds_so_far
                for assign in node.assignments:
                    assert isinstance(assign, AST.AssignNode)
                    res = interp_node(assign, res)
                return res

            # command (e.g. echo hello)
            # note: local assignments (e.g. LC_ALL=C sort file.txt) are ignored for now

            name = node.arguments[0]
            if str(name) in fns and str(name) not in stack:
                stack.append(str(name))
                res = interp_node(fns[str(name)], conds_so_far)
                stack.pop()
                return res
            else:
                return conds_so_far

        case AST.AssignNode():
            return interp_node(node.val, conds_so_far)

        case AST.SemiNode():
            conds_so_far = interp_node(node.left_operand, conds_so_far)
            return interp_node(node.right_operand, conds_so_far)

        case AST.ForNode():
            conds_so_far = interp_args(node.argument, conds_so_far)
            return interp_node(node.body, conds_so_far)

        case AST.FileRedirNode():
            return conds_so_far

        case AST.RedirNode():
            t1 = interp_node(node.node, conds_so_far)
            t2 = t1
            for redir in node.redir_list:
                t2 = interp_node(redir, t2)
            return t2


        case AST.DefunNode():
            # Note: the type annotation in the Shasta source code is *wrong* for node.name -- it's a string
            fns[str(node.name)] = node.body
            return conds_so_far

        case AST.NotNode():
            return interp_node(node.body, conds_so_far)

        case AST.PipeNode():
            for cmd in node.items:
                conds_so_far = interp_node(cmd, conds_so_far)
            return conds_so_far

        # todo bring other cases as needed

        case _:
            return conds_so_far



def count_conds(program: list[AST.AstNode],
                line: int) -> int:
    global is_target, fns, stack
    is_target = lambda n: getattr(n, "line_number", -1) == line
    fns = {}
    stack = []

    conds_so_far = 0
    try:
        for node in program:
            node = node.ast_node
            print(f"Conds {conds_so_far} at node {node.pretty()}")
            conds_so_far = interp_node(node, conds_so_far)
    except Found as f:
        return f.conds_before
    assert False, f"Program does not have line {line}"
    return 0

def count_conds_file(path, line) -> int:
    nodes = parser.parse_shell_script(path)
    return count_conds(nodes, line)

if __name__ == '__main__':
    # get first arg as path, second arg as line number
    import sys
    path = sys.argv[1]
    line = int(sys.argv[2])
    cs = count_conds_file(path, line)
    print(f"\n\nNumber of conditions before line {line}: {cs}\n")
