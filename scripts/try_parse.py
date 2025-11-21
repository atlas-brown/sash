import sash.parser
import pathlib
import shasta.ast_node as shn
import libdash.parser
import sys


def main(script_path: pathlib.Path) -> None:
    try:
        parsed_nodes = sash.parser.parse_shell_script(script_path.as_posix())
    except libdash.parser.ParsingException as pe:
        print(f"{script_path} | Error parsing script: {pe}", file=sys.stderr)
        sys.exit(1)

    for pn in parsed_nodes:
        try:
            traverse_ast(pn.ast_node)
        except Exception as e:
            print(
                f"{script_path} | Error traversing AST at line {pn.get_line_number()}: {e}",
                file=sys.stderr,
            )
            sys.exit(1)


def traverse_ast(root: shn.AstNode):
    match root:
        case shn.PipeNode() as p:
            for cmd in p.items:
                traverse_ast(cmd)
        case shn.CommandNode() as c:
            for assn in c.assignments:
                traverse_ast(assn)
            for args in c.arguments:
                for arg in args:
                    traverse_ast(arg)
            for redir in c.redir_list:
                traverse_ast(redir)
        case shn.SubshellNode() as s:
            traverse_ast(s.body)
            for redir in s.redir_list:
                traverse_ast(redir)
        case shn.AndNode() as a:
            traverse_ast(a.left_operand)
            traverse_ast(a.right_operand)
        case shn.OrNode() as o:
            traverse_ast(o.left_operand)
            traverse_ast(o.right_operand)
        case shn.SemiNode() as s:
            traverse_ast(s.left_operand)
            traverse_ast(s.right_operand)
        case shn.NotNode() as n:
            traverse_ast(n.body)
        case shn.RedirNode() as r:
            traverse_ast(r.node)
            for redir in r.redir_list:
                traverse_ast(redir)
        case shn.BackgroundNode() as b:
            traverse_ast(b.node)
            for redir in b.redir_list:
                traverse_ast(redir)
        case shn.DefunNode() as d:
            for n in d.name:
                traverse_ast(n)
            traverse_ast(d.body)
        case shn.ForNode() as f:
            for args in f.argument:
                for arg in args:
                    traverse_ast(arg)
            traverse_ast(f.body)
            for v in f.variable:
                traverse_ast(v)
        case shn.WhileNode() as w:
            traverse_ast(w.test)
            traverse_ast(w.body)
        case shn.IfNode() as i:
            traverse_ast(i.cond)
            traverse_ast(i.then_b)
            if i.else_b is not None:
                traverse_ast(i.else_b)
        case shn.CaseNode() as c:
            for arg in c.argument:
                traverse_ast(arg)
            for case in c.cases:
                traverse_ast(case["cbody"])
        case shn.CArgChar() as c:
            pass
        case shn.EArgChar() as e:
            pass
        case shn.TArgChar() as t:
            pass
        case shn.AArgChar() as a:
            for ar in a.arg:
                traverse_ast(ar)
        case shn.VArgChar() as v:
            for ar in v.arg:
                traverse_ast(ar)
        case shn.QArgChar() as q:
            for ar in q.arg:
                traverse_ast(ar)
        case shn.BArgChar() as b:
            traverse_ast(b.node)
        case shn.AssignNode() as a:
            for v in a.val:
                traverse_ast(v)
        case shn.FileRedirNode() as f:
            for a in f.arg:
                traverse_ast(a)
        case shn.DupRedirNode() as d:
            pass
        case shn.HeredocRedirNode() as h:
            for a in h.arg:
                traverse_ast(a)
        case _:
            raise ValueError(f"Unsupported AST node: {root}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Try parsing a shell script and traversing its AST"
    )
    parser.add_argument(
        "script", type=pathlib.Path, help="Path to the shell script to parse"
    )
    args = parser.parse_args()

    main(args.script)
