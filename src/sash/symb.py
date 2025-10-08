from dataclasses import dataclass
import json
import logging
from math import inf
import os
import sys
import traceback
from argparse import ArgumentParser
from typing import Any, Dict, List, Optional, Set
import shasta.ast_node as AST
from sash.shell_parser import *
from sash.state import *
from sash.config import Config
from sash.reporter import Reporter
import sash.reporter as reporter
import sash.symb_utils as symb_utils

def handle_commandnode(traces: Traces,
                       node: AST.CommandNode,
                       info: ScriptInfo,
                       cb: Optional[Callable[[list[Field]], None]]) -> Traces:
    logging.debug(f"Handling command node {trim_string_for_logging(node.pretty())} with {len(traces)} traces")
    t1, expanded_args = expand_args_dumb(traces, node.arguments, info)

    if expanded_args and isinstance(expanded_args[0].content, SymStr):
        cmd_name = symb_utils.symbstr_to_str(expanded_args[0].content.parts)
        logging.debug(f"Handling command {cmd_name} with args {expanded_args[1:]}")
        if cmd_name == "rm":
            for arg_field in expanded_args[1:]:
                if isinstance(arg_field.content, SymStr):
                    path = symb_utils.symbstr_to_str(arg_field.content.parts)
                    assert path is not None
                    if any(path.startswith(p) for p in Config.get("PROTECTED_PATHS")):
                        Reporter.add_error(reporter.DeleteSystemFile(path))
    t2 = t1
    for redir in node.redir_list:
        t2 = t2.extend(guarded_interp_node(t1, redir, info)) or t2

    if cb is not None:
        cb(expanded_args)
    logging.debug(f"Done with command {trim_string_for_logging(node.pretty())} after expanding its args to {expanded_args} (it had assignments: {node.assignments})")
    return t2

def record_assignment(trace: Trace, var: str, rhs: Field) -> Trace:
    return trace.extend(lambda s: s.set_env(var, ShellVar(rhs)))

def handle_while(traces: Traces,
                 node: AST.WhileNode,
                 info: ScriptInfo):
    logging.debug(f"Checking while loop for an infinite loop")
    test_cmds = []
    def get_the_test(cmd_fields):
        test_cmds.append(cmd_fields)
    logging.debug(f"Interpreting first iteration")
    t1 = guarded_interp_node(traces, node.test, info, get_the_test)
    logging.debug(f"collected test_cmds: {test_cmds}")
    # Special case: never runs
    if interpret_test(test_cmds[0]) == False:
        logging.debug(f"While loop never runs")
        return t1
    # todo extend path condition
    t2 = guarded_interp_node(t1, node.body, info)
    logging.debug(f"Interpreting second iteration")
    t3 = guarded_interp_node(t2, node.test, info, get_the_test)
    # Special case: only one iteration
    if interpret_test(test_cmds[1]) == False:
        logging.debug(f"While loop only runs once")
        return t3
    logging.debug(f"collected test_cmds: {test_cmds}")
    # todo extend path condition
    t4 = guarded_interp_node(t3, node.body, info)
    logging.debug(f"Interpreting third test")
    t5 = guarded_interp_node(t4, node.test, info, get_the_test)
    logging.debug(f"collected test_cmds: {test_cmds}")

    logging.debug(f"Checking constant test cond")
    assert len(test_cmds) == 3
    if is_constant_test(test_cmds[2], test_cmds[1]):
        Reporter.add_error(reporter.InfiniteLoop(node))

    return t5

def is_test(s):
    return s in ["test", "["]

def is_constant_test(cmd1: list[Field], cmd2: list[Field]) -> bool:
    """Return true if `cmd1` and `cmd2` are both tests that always have the same result."""

    if len(cmd1) < 1 or len(cmd2) < 1 or len(cmd1) != len(cmd2):
        return False
    match (cmd1[0].content, cmd2[0].content):
        case (SymStr([t1]), SymStr([t2])) if is_test(t1) and is_test(t2):
            # see CompletelyArbitrary __eq__, which makes this work
            return all(f1 == f2 for f1, f2 in zip(cmd1[1:], cmd2[1:]))
        case _:
            return False

def interpret_test(cmd: list[Field]) -> bool:
    """Return true if `cmd` is a test that always returns true. This only works for concrete arguments."""
    logging.debug(f"Checking if test command {cmd} is constant true/false")
    if len(cmd) < 1:
        return False

    if not isinstance(cmd[0].content, SymStr):
        return False

    if not is_test(cmd[0].content.parts[0]):
        return False

    args = cmd[1:]
    if not len(args) in {3, 4}:
        return False

    # Check if if all arguments are concrete
    field_content = [f.content for f in cmd]
    if not all(isinstance(c, SymStr) for c in field_content):
        return False
    field_parts = [c.parts for c in field_content if isinstance(c, SymStr)]
    if not all(all(isinstance(p, str) for p in parts) for parts in field_parts):
        return False

    if len(args) == 3:
        match (args[0].content, args[1].content):
            case (SymStr([s]), SymStr([op])) if op == "-n":
                return s != ""
            case (SymStr([s]), SymStr([op])) if op == "-z":
                return s == ""
            case _:
                return False

    if len(args) == 4:
        match (args[0].content, args[1].content, args[2].content):
            case (SymStr([s1]), SymStr([op]), SymStr([s2])) if op == "=":
                return s1 == s2
            case (SymStr([s1]), SymStr([op]), SymStr([s2])) if op == "!=":
                return s1 != s2
            case (SymStr([s1]), SymStr([op]), SymStr([s2])) if op in ["-eq", "-ne", "-lt", "-le", "-gt", "-ge"]:
                try:
                    assert isinstance(s1, str) and isinstance(s2, str)
                    n1 = int(s1)
                    n2 = int(s2)
                except ValueError:
                    return False
                match op:
                    case "-eq":
                        return n1 == n2
                    case "-ne":
                        return n1 != n2
                    case "-lt":
                        return n1 < n2
                    case "-le":
                        return n1 <= n2
                    case "-gt":
                        return n1 > n2
                    case "-ge":
                        return n1 >= n2
            case _:
                return False

    return False


# ============================================================
#                  Symbolic Expander
# ============================================================

def expand(traces: Traces, stuff: list[AST.ArgChar], info: ScriptInfo) -> list[tuple[Trace, list[Field]]]:
    res = []
    for trace in traces:
        res.append((trace, expand_simple(stuff, trace.latest_state)))
        # if expanded := expand_simple(stuff, trace.latest_state):
        #     res.append((trace, expanded))
        # else:
        #     res.append((trace, [arbitrary_field(stuff)]))
    return res

def expand_simple(stuff: list[AST.ArgChar], state: State) -> list[Field]:
    IFS = " \t\n"

    def expand_inner(chars: list[AST.ArgChar], quoted: bool = False) -> list[Field]:
        res = []
        field_so_far: list[str | SymVar] = []
        field_so_far_words_min: int = 1
        field_so_far_words_max: int | float = 1

        def add_a_field(one_field: Field) -> None:
            nonlocal field_so_far, field_so_far_words_min, field_so_far_words_max, res
            match one_field.content:
                case CompletelyArbitrary():
                    field_so_far = []
                    field_so_far_words_min = 1
                    field_so_far_words_max = 1
                    # TODO this is technically not right, if there are more arbitrary fields later the end result field should be a whole new arbitrary field
                    res.append(one_field)
                case SymStr(parts):
                    field_so_far.extend(parts)
                    if one_field.count.min > 1:
                        field_so_far_words_min += one_field.count.min - 1
                    if one_field.count.max > 1:
                        field_so_far_words_max += one_field.count.max - 1

        def finish_field_so_far() -> None:
            nonlocal field_so_far, res
            res.append(Field(SymStr(field_so_far).simplify(),
                             WordCount(field_so_far_words_min, field_so_far_words_max)))
            field_so_far = []

        for argchar in chars:
            match argchar:
                # todo what about globs?
                case AST.CArgChar() as c:
                    if not quoted and c.pretty() in IFS:
                        # end this field
                        if field_so_far != []:
                            finish_field_so_far()
                    else:
                        field_so_far.append(c.pretty(AST.QUOTED if quoted else AST.UNQUOTED))
                case AST.EArgChar() as c:
                    field_so_far.append(c.pretty(AST.QUOTED if quoted else AST.UNQUOTED))
                case AST.QArgChar() as q:
                    inside = expand_inner(q.arg, True)
                    one_field = join_fields(inside).quote()
                    add_a_field(one_field)
                case AST.VArgChar() as var:
                    if (v := state.lookup(var.var)):
                        if var.fmt == "Normal":
                            add_a_field(v.value)
                        else:
                            logging.info(f"expansion: treating var {var.pretty()} with unhandled fmt {var.fmt} as completely arbitrary field")
                            add_a_field(arbitrary_field(var), ArbitraryType.APPROXIMATION, state)
                    else:
                        Reporter.add_error(reporter.UnboundID(var.pretty())) # todo we should report path information
                        add_a_field(arbitrary_field(var, ArbitraryType.ENVIRONMENT, state)) # todo worth recording somehow that the value comes from the environment?
                case _:
                    # todo: if its a command substitution, need to go interp it
                    logging.info(f"expansion: treating unhandled argchar as completely arbitrary field: {argchar.pretty()}")
                    add_a_field(arbitrary_field(argchar, ArbitraryType.APPROXIMATION, state))

        if field_so_far != []:
            finish_field_so_far()

        return res

    return expand_inner(stuff)

def expand_args_dumb(traces: Traces, args: list[list[AST.ArgChar]], info: ScriptInfo) -> tuple[Traces, list[Field]]:
    expanded_args: List[Field] = []
    res_traces = traces
    for arg in args:
        expansions = expand(res_traces, arg, info)
        res_traces = [expansion[0] for expansion in expansions]
        expanded_fields = [expansion[1] for expansion in expansions]
        # for each trace, we have a list of fields that this arg expands to

        # # Design 1: collapse each field individually across all traces
        # final_number_of_fields = max(len(field_list) for field_list in expanded_fields)
        # # for each final field at index i, obtain field by collapsing all fields at index i
        # # across all traces (if a trace has fewer fields, it contributes an empty field)
        # for i in range(final_number_of_fields):
        #     fields_at_i = [field_list[i] if i < len(field_list) else Field(SymStr([""]), WordCount(0, 0)) for field_list in expanded_fields]
        #     collapsed_field = collapse_fields(fields_at_i)
        #     expanded_args.append(collapsed_field)

        # Design 2: if all fields are the same across all traces, keep that, else give up entirely
        if all(field == expanded_fields[0] for field in expanded_fields):
            expanded_args.extend(expanded_fields[0])
        else:
            # todo could be smarter about the ranges of word counts, but wont do unless needed
            expanded_args.append(arbitrary_field(arg, ArbitraryType.APPROXIMATION, None))
    return res_traces, expanded_args



# =====================
#  Field manipulation
# =====================

def arbitrary_field(ast: AST.AstNode, kind: ArbitraryType, producing_state: State) -> Field:
    return Field(CompletelyArbitrary(ast, kind, producing_state),
                 WordCount(0, inf))

def join_fields(fields: list[Field]) -> Field:
    content = []
    min_words = 0
    max_words = 0
    for field in fields:
        match field.content:
            case SymStr(parts):
                content.extend(parts)
                min_words += field.count.min
                max_words += field.count.max
            case CompletelyArbitrary(_):
                # TODO: this is not quite sound! If there are multiple arbitrary fields, we need to make a new one...
                return field

    return Field(SymStr(content), WordCount(min_words, max_words))

def collapse_fields(fields: List[Field], source: AST.AstNode | None = None) -> Field:
    """Collapse alternative versions of a field into one field abstracting over all of them."""
    # if all alternatives are the same, return that
    if all(field == fields[0] for field in fields):
        return fields[0]
    else:
        # otherwise, return a CompletelyArbitrary field with min/max word counts
        min_words = min(field.count.min for field in fields)
        max_words = max(field.count.max for field in fields)
        return Field(CompletelyArbitrary(source),
                     WordCount(min_words, max_words))



# ============================================================
#                  Symbolic Interpreter
# ============================================================

context_line = None

def guarded_interp_node(traces: Traces,
                        node: AST.AstNode,
                        info: ScriptInfo,
                        command_cb: Optional[Callable[[list[Field]], None]] = None) -> Traces:
    try:
        return interp_node(traces, node, info, command_cb)
    except Exception:
        logging.error(f"Interp raised: {traceback.format_exc()}. Ignoring.")
        return traces

def interp_node(traces: Traces,
                node: AST.AstNode,
                info: ScriptInfo,
                command_cb: Optional[Callable[[list[Field]], None]] = None) -> Traces:
    # refer to https://github.com/binpash/shasta/blob/main/shasta/ast_node.py
    logging.debug(f"Interpreting {trim_string_for_logging(node.pretty())} with {len(traces)} traces")
    match node:
        case AST.CommandNode() if not (node.arguments == [] and node.assignments != []):
            return handle_commandnode(traces, node, info, command_cb)
        case AST.CommandNode() if node.arguments == [] and node.assignments != []: # why is kind of parse possible??
            # do the assignments inside
            t = traces
            for assign in node.assignments:
                t = guarded_interp_node(t, assign, info)
            return t

        case AST.IfNode():
            t1 = guarded_interp_node(traces, node.cond, info)
            # todo: extend pathcond with actual condition true/false
            t2 = guarded_interp_node(trace_map(t1, lambda s: s.add_pathcond(f"cond_L{context_line}:true")),
                                     node.then_b,
                                     info)
            if node.else_b is not None:
                t3 = guarded_interp_node(trace_map(t1, lambda s: s.add_pathcond(f"cond_L{context_line}:false")),
                                         node.else_b,
                                         info)
                return t2 + t3
            else:
                return t2


        case AST.AssignNode():
            trace_expansion_pairs = expand(traces, node.val, info)
            return [record_assignment(t, node.var, join_fields(rhs)) for (t, rhs) in trace_expansion_pairs]


        case AST.SemiNode():
            t2 = guarded_interp_node(traces, node.left_operand, info, command_cb)
            return guarded_interp_node(t2, node.right_operand, info, command_cb)


        case AST.WhileNode():
            return handle_while(traces, node, info)

        case AST.ForNode():
            t1, items = expand_args_dumb(traces, node.argument, info)
            t2_expansion_pairs = expand(t1, node.variable, info)
            if join_fields(items).count.max <= 1:
                Reporter.add_error(reporter.LoopRunsOnce())
            # Interpret the for loop body
            # TODO: Will want to interpret the body multiple times (up to max count of times)
            t3 = [record_assignment(t, symb_utils.argchar_conc(node.variable), join_fields(var)) for t, var in t2_expansion_pairs]
            t4 = guarded_interp_node(t3, node.body, info)
            return t4

        case AST.FileRedirNode():
            for trace in traces:
                var = symb_utils.argchar_conc(node.arg)
                if var in trace.latest_state.fundefs:
                    # TODO: Associate the warning with the trace that caused it
                    Reporter.add_error(reporter.RedirectToFunction())
            # TODO: Also handle the effects of redirection on the FS
            return traces

        case AST.RedirNode():
            t1 = guarded_interp_node(traces, node.node, info)
            t2 = t1
            for redir in node.redir_list:
                t2 = t2.extend(guarded_interp_node(t1, redir, info)) or t2
            return t2


        case AST.DefunNode():
            trace_expansion_pairs = expand(traces, node.name, info)
            return trace_map(traces, lambda s: s.set_fundef(symb_utils.argchar_conc(node.name), node))

        # todo bring other cases as needed

        case _:
            raise NotImplementedError(
                    f"node type {type(node)} not handled",
                    node
                )


def starting_state() -> State:
    env = {}
    # env["IFS"] = ShellVar(" \t\n")
    # for defaultvar in ["HOME", "PWD", "OLDPWD", "PATH"]:
    #     env[defaultvar] = ShellVar(symb_utils.create_fresh_var(f"default_{defaultvar}"))
    return State([], env, {}, {}, SymStr(["0"]), None)

@dataclass(frozen=True)
class AST_parse:
    ast_node: AST.AstNode
    rawtext: str
    line_before: int
    line_after: int  # relevant for mysterious shell reasons

def trim_string_for_logging(s: str, max_len: int = 120) -> str:
    return s if len(s) <= max_len else s[:max_len] + "..."

def symb_engine(nodes: list[AST_parse], info: ScriptInfo) -> list[Trace]:
    global context_line
    logging.debug(f"Running symb engine with {len(nodes)} raw nodes")
    traces = [Trace([starting_state()])]
    for node in nodes:
        logging.debug(f"Interpreting next node {trim_string_for_logging(node.ast_node.pretty())}")
        context_line = node.line_before
        traces = guarded_interp_node(traces, node.ast_node, info)

    return traces


def parse_script(filename) -> list[AST_parse]:
    shasta_nodes = parse_shell_to_asts(filename)
    logging.debug(f"Parsed script with {len(shasta_nodes)} nodes")
    nodes = [AST_parse(*x) for x in shasta_nodes]
    return nodes


def symbexec_file(input_file: str) -> Traces:
    nodes = parse_script(input_file)
    # opt_store = parse_shebang_args(input_file)
    return symb_engine(nodes, ScriptInfo(None))


def main(file: str) -> dict:
    logging.info(f"Processing file {file}")
    Reporter.initialize(file)
    try:
        symbexec_file(file)
    except Exception:
        logging.info(f"Failed due to {traceback.format_exc()}.Returning unknown")

    report_dict = Reporter.get_report()
    logging.info("Time taken: " + str(report_dict["time"]))
    return report_dict


def argmain():
    arg_parser = ArgumentParser(
        prog="Sash",
        description="Static analyis for posix shell scripts",
    )
    arg_parser.add_argument(
        "filename",
        help="Input shell script file",
    )
    arg_parser.add_argument(
        "--debug",
        "-d",
        action="store_true",
        help="optional flag to enable debugging messages",
    )
    arg_dict = vars(arg_parser.parse_args(sys.argv[1:]))
    if arg_dict["debug"]:
        logging.basicConfig(
            format="[%(filename)s:%(lineno)d] %(message)s", level=logging.DEBUG
        )
        Config.set("DEBUG", True)
    else:
        logging.basicConfig(level=logging.CRITICAL)

    filename = arg_dict["filename"]
    logging.debug(f"Full filename is {os.path.realpath(filename)}")
    report_dict = main(filename)
    print(json.dumps(report_dict))


if __name__ == "__main__":
    argmain()
