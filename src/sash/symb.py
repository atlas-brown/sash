from dataclasses import dataclass
import json
import logging
from math import inf
import os
import sys
import traceback
from argparse import ArgumentParser
from typing import Any, Dict, List, Optional, Set
from sash.util import *
import shasta.ast_node as AST
from sash.frozen import FrozenAst, freeze, freeze_thing
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
    logging.debug(f"Expanded cmd to {expanded_args}")

    if expanded_args:
        match field_to_str(expanded_args[0]):
            case "rm":
                handle_rm(expanded_args)
            case some_name if isinstance(some_name, str):
                # todo: we could actually not use `expand_args_dumb` here, and instead do trace-specific expansion, since the function body is handled trace-specifically anyway
                # deferred for now until we actually need it (see test_function_call_multipath)
                t1 = handle_function_call_or_unknown(some_name, expanded_args[1:], t1, info)
            case _:
                logging.debug(f"Non-constant command invocation {expanded_args}, optimistically treating as no-op")
    t2 = t1
    for redir in node.redir_list:
        t2 = t2.extend(guarded_interp_node(t1, redir, info)) or t2

    if cb is not None:
        cb(expanded_args)
    logging.debug(f"Done with command {trim_string_for_logging(node.pretty())} after expanding its args to {expanded_args} (it had assignments: {node.assignments})")
    return t2

def handle_rm(expanded_args: List[Field]) -> None:
    def is_protected(path):
        return any(path in [p, p + "/", p + "/*"] for p in Config.get("PROTECTED_PATHS"))
    for arg_field in expanded_args[1:]:
        if (path := field_to_str(arg_field)) and is_protected(path):
            Reporter.add_error(reporter.DeleteSystemFile(path))
        match arg_field:
            case Field(CompletelyArbitrary(source=source), WordCount(max=m)) if m > 1:
                Reporter.add_error(reporter.DangerousWordSplit(source))
        match arg_field:
            case Field(CompletelyArbitrary(prefix=pre, suffix=suf), WordCount(min, max)) if min == 0 or max > 1:
                if pre is not None and (path := symb_utils.symbstr_to_str(pre.parts)) and is_protected(path):
                    Reporter.add_error(reporter.CouldDeleteSystemFile(path))
                if suf is not None and (path := symb_utils.symbstr_to_str(suf.parts)) and is_protected(path):
                    Reporter.add_error(reporter.CouldDeleteSystemFile(path))

def handle_function_call_or_unknown(func_name: str,
                                    arg_fields: List[Field],
                                    traces: Traces,
                                    info: ScriptInfo) -> Traces:
    # is it a known function, and the same one across all traces?
    func_defs = {t.latest_state.lookup_fundef(func_name) for t in traces}
    if len(func_defs) == 1:
        if None in func_defs:
            logging.debug(f"Unknown command {func_name}, optimistically treating as no-op")
            return traces
        else:
            the_func = func_defs.pop()
            assert isinstance(the_func, FrozenAst)
            return handle_function_call(the_func.ast, arg_fields, traces, info)
    else:
        logging.error(f"Name {func_name} is defined as different functions across traces, giving up on this call")
        return traces

def handle_function_call(func_node: AST.DefunNode,
                         arg_fields: List[Field],
                         traces: Traces,
                         info: ScriptInfo) -> Traces:
    logging.debug(f"Handling function call to {trim_string_for_logging(func_node.pretty())} with args {arg_fields}")
    # As long as arg_fields are a single word, map those to local positional parameters
    # as soon as we hit a field that is not a single word, give up
    localenv: dict[str, ShellVar] = {}
    for i, arg in enumerate(arg_fields):
        if arg.count == WordCount(1, 1):
            localenv[str(i + 1)] = ShellVar(arg)
        else:
            logging.debug(f"Function argument {i} is not guaranteed to be a single word, giving up on positional parameters ({arg})")
            break
    logging.debug(f"Bound localenv for call: {localenv}")
    t1 = []
    for t in traces:
        t1.append(t.extend(lambda s: s.extend_localenv(localenv)))
    return guarded_interp_node(t1, func_node.body, info)

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

def interpret_test(cmd: list[Field]) -> bool | None:
    """Return true or false `cmd` is a test that always returns either of the two results. Return None if unknown."""
    logging.debug(f"Checking if test command {cmd} is constant true/false")
    if len(cmd) < 1:
        return None

    if not isinstance(cmd[0].content, SymStr):
        return None

    if not is_test(cmd[0].content.parts[0]):
        return None

    args = cmd[1:]
    if not len(args) in {3, 4}:
        return None

    # Check if if all arguments are concrete
    field_content = [f.content for f in cmd]
    if not all(isinstance(c, SymStr) for c in field_content):
        return None
    field_parts = [c.parts for c in field_content if isinstance(c, SymStr)]
    if not all(all(isinstance(p, str) for p in parts) for parts in field_parts):
        return None

    if len(args) == 3:
        match (args[0].content, args[1].content):
            case (SymStr([s]), SymStr([op])) if op == "-n":
                return s != ""
            case (SymStr([s]), SymStr([op])) if op == "-z":
                return s == ""
            case _:
                return None

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
                    return None
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
                return None

    return None


# ============================================================
#                  Symbolic Expander
# ============================================================

def expand(traces: Traces, stuff: list[AST.ArgChar], info: ScriptInfo) -> list[tuple[Trace, list[Field]]]:
    res = []
    for trace in traces:
        res.append((trace, expand_simple(stuff, trace.latest_state, info)))
        # if expanded := expand_simple(stuff, trace.latest_state):
        #     res.append((trace, expanded))
        # else:
        #     res.append((trace, [arbitrary_field(stuff)]))
    return res

# Different fields are definitely separated; things within a field *may be separated as well!*
def expand_simple(stuff: list[AST.ArgChar], state: State, info: ScriptInfo) -> list[Field]:
    IFS = " \t\n"

    def expand_inner(chars: list[AST.ArgChar], quoted: bool = False) -> list[Field]:
        ## Notes on what's happening here:
        # Need to build up fields with individual characters, and also SymStrs that we come across
        # Along the way, will see some CompletelyArbitrarys
        # The CompletelyArbitrarys kind of soak up the whole field -- if any part of a final field
        # is arbitrary, then the whole field is arbitrary
        # BUT -- we can preserve some info that will lead to better error messages:
        # if there's some SymStr that's being prepended or appended to an arbitrary thing, we can
        # record that the SymStr is a known prefix or suffix of the arbitrary thing
        combined_fields_so_far: list[Field | None] = [] # None's mean a hard break due to IFS
        field_so_far: list[str | SymVar] = []
        field_so_far_words_min: int = 1
        field_so_far_words_max: int | float = 1

        def add_a_field(one_field: Field) -> None:
            nonlocal field_so_far, field_so_far_words_min, field_so_far_words_max, combined_fields_so_far
            match one_field.content:
                case CompletelyArbitrary():
                    finish_field_so_far()
                    combined_fields_so_far.append(one_field)
                case SymStr(parts):
                    field_so_far.extend(parts)
                    if one_field.count.min > 1:
                        field_so_far_words_min += one_field.count.min - 1
                    if one_field.count.max > 1:
                        field_so_far_words_max += one_field.count.max - 1

        def finish_field_so_far(IFS: bool = False) -> None:
            nonlocal field_so_far, field_so_far_words_min, field_so_far_words_max, combined_fields_so_far
            if field_so_far != []:
                combined_fields_so_far.append(Field(SymStr(tuple(field_so_far)).simplify(),
                                                    WordCount(field_so_far_words_min, field_so_far_words_max)))
                if IFS:
                    combined_fields_so_far.append(None)
                field_so_far = []
                field_so_far_words_min = 1
                field_so_far_words_max = 1

        for argchar in chars:
            match argchar:
                # todo what about globs?
                case AST.CArgChar() as c:
                    if not quoted and c.pretty() in IFS:
                        finish_field_so_far(True)
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
                            add_a_field(arbitrary_field(var, ArbitraryType.APPROXIMATION, state))
                    else:
                        if not is_special_var(var.var):
                            Reporter.add_error(reporter.UnboundID(var.pretty())) # todo we should report path information
                        add_a_field(arbitrary_field(var,
                                                    ArbitraryType.APPROXIMATION if is_special_var(var.var) else ArbitraryType.ENVIRONMENT,
                                                    state))
                case AST.BArgChar() as b:
                    logging.info(f"expansion: treating backquote argchar {b.pretty()} as completely arbitrary field")
                    add_a_field(arbitrary_field(b, ArbitraryType.APPROXIMATION, state))
                    # todo use the trace
                    t = guarded_interp_node([Trace((state,))], b.node, info)
                case _:
                    # todo: if its a command substitution, need to go interp it
                    logging.error(f"argchar: {argchar} {type(argchar)}")
                    logging.info(f"expansion: treating unhandled argchar as completely arbitrary field: {argchar.pretty()}")
                    add_a_field(arbitrary_field(argchar, ArbitraryType.APPROXIMATION, state))

        finish_field_so_far()

        # Join the combined fields so far, folding symstrs into arbitrary fields as prefixes and suffixes
        split = split_at(combined_fields_so_far, None)
        return [merge_partial_fields(part, None, state) for part in split if part != []]

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


def expand_assuming_single_constant_word(traces: Traces, stuff: list[AST.ArgChar], info: ScriptInfo) -> tuple[Traces, str]:
    t0, fields = expand_args_dumb(traces, [stuff], info)
    match fields:
        case [Field(SymStr((one_word,)), WordCount(1, 1))] if isinstance(one_word, str):
            return t0, one_word
        case _:
            assert False, f"expected {stuff} to be a single constant word, but found something else after expansion: {fields}"

def field_to_str(field: Field) -> Optional[str]:
    match field:
        case Field(SymStr(parts), _):
            return symb_utils.symbstr_to_str(parts)
        case _:
            return None

def is_special_var(name: str) -> bool:
    return name.isdecimal() or name in ["@", "#"]

# =====================
#  Field manipulation
# =====================

def arbitrary_field(ast: AST.AstNode, kind: ArbitraryType, producing_state: Optional[State]) -> Field:
    return Field(CompletelyArbitrary(freeze_thing(ast), kind, producing_state),
                 WordCount(0, inf))

def join_fields(fields: list[Field]) -> Field:
    """Join a list of fields into one field that approximates all of them."""
    return merge_partial_fields(fields, sep=" ", state=None)

def merge_partial_fields(fields: list[Field], sep: Optional[str] = " ", state: Optional[State] = None) -> Field:
    """Merge a list of partial fields into one field, merging SymStrs and folding them into CompletelyArbitrarys as prefixes or suffixes."""

    def merge_symstrs(symstrs: list[Field]) -> Field:
        assert all(isinstance(f.content, SymStr) for f in symstrs)
        match symstrs:
            case []:
                return Field(SymStr(()), WordCount(0, 0))
            case [one]:
                return one
            case [Field(SymStr(parts), c), *rest]:
                content = parts
                count = c
                for field in rest:
                    content = content + ((sep,) if sep else ()) + field.content.parts # type: ignore (field.content is SymStr due to assert above)
                    count = merge_counts(count, field.count, 1 if sep else 0)
                return Field(SymStr(tuple(content)), count)
            case _:
                assert False, "unreachable"

    def collect_prefixes_suffixes(fields: list[Field]) -> tuple[Field | None, Field | None]:
        prefixes = []
        for field in fields:
            if isinstance(field.content, SymStr):
                prefixes.append(field)
            else:
                break
        suffixes = []
        for field in reversed(fields):
            if isinstance(field.content, SymStr):
                suffixes.append(field)
            else:
                break
        return (merge_symstrs(prefixes) if prefixes else None,
                merge_symstrs(suffixes) if suffixes else None)

    num_arbitraries = sum(1 for field in fields if isinstance(field.content, CompletelyArbitrary))
    if num_arbitraries == 0:
        # just join the symstrs
        return merge_symstrs(fields)
    elif num_arbitraries == 1:
        arbitrary = [field for field in fields if isinstance(field.content, CompletelyArbitrary)][0]
        prefix, suffix = collect_prefixes_suffixes(fields)
        if prefix is not None:
            arbitrary = add_prefix(arbitrary, prefix)
        if suffix is not None:
            arbitrary = add_suffix(arbitrary, suffix)
        return arbitrary
    else:
        # multiple arbitraries -- give up and return a new arbitrary field
        arbitraries = [field for field in fields if isinstance(field.content, CompletelyArbitrary)]
        prefix, suffix = collect_prefixes_suffixes(fields)
        arbitrary = Field(CompletelyArbitrary(freeze_thing([a.content.source for a in arbitraries]), # type: ignore
                                              ArbitraryType.APPROXIMATION,
                                              state),
                          WordCount(0, inf))
        if prefix is not None:
            arbitrary = add_prefix(arbitrary, prefix)
        if suffix is not None:
            arbitrary = add_suffix(arbitrary, suffix)
        return arbitrary


def collapse_fields(fields: List[Field], source: AST.AstNode | None = None) -> Field:
    """Collapse alternative versions of a field into one field abstracting over all of them."""
    # if all alternatives are the same, return that
    if all(field == fields[0] for field in fields):
        return fields[0]
    else:
        # otherwise, return a CompletelyArbitrary field with min/max word counts
        min_words = min(field.count.min for field in fields)
        max_words = max(field.count.max for field in fields)
        return Field(CompletelyArbitrary(freeze(source) if source is not None else source,
                                         ArbitraryType.APPROXIMATION,
                                         None),
                     WordCount(min_words, max_words))

def add_prefix(arbitrary_field: Field, prefix_symstr: Field) -> Field:
    match (arbitrary_field, prefix_symstr):
        case (Field(CompletelyArbitrary(prefix=None) as a, acount),
              Field(SymStr() as s, scount)):
            return Field(replace(a, prefix=s), merge_counts(acount, scount))
        case (Field(CompletelyArbitrary(prefix=SymStr(pre_parts)) as a, acount),
              Field(SymStr(more_parts) as s, scount)):
            return Field(replace(a, prefix=SymStr(more_parts + pre_parts)), merge_counts(acount, scount))
        case _:
            assert False, "unreachable"
def add_suffix(arbitrary_field: Field, suffix_symstr: Field) -> Field:
    match (arbitrary_field, suffix_symstr):
        case (Field(CompletelyArbitrary(suffix=None) as a, acount),
              Field(SymStr() as s, scount)):
            return Field(replace(a, suffix=s), merge_counts(acount, scount))
        case (Field(CompletelyArbitrary(suffix=SymStr(suf_parts)) as a, acount),
              Field(SymStr(more_parts) as s, scount)):
            return Field(replace(a, suffix=SymStr(suf_parts + more_parts)), merge_counts(acount, scount))
        case _:
            assert False, "unreachable"
def merge_counts(c1: WordCount, c2: WordCount, sep: int = 0) -> WordCount:
    return WordCount(c1.min + max(c2.min - 1, 0) + sep,
                     c1.max + max(c2.max - 1, 0) + sep)



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
    except NotImplementedError as e:
        logging.error(f"Interp raised: {traceback.format_exc()}. Ignoring.")
        return traces

def interp_node(traces: Traces,
                node: AST.AstNode,
                info: ScriptInfo,
                command_cb: Optional[Callable[[list[Field]], None]] = None) -> Traces:
    # refer to https://github.com/binpash/shasta/blob/main/shasta/ast_node.py
    traces = maybe_collapse_traces(traces)
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
            # todo: early exit if condition is constant false
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

        case AST.CaseNode():
            t1, case_arg_fields = expand_args_dumb(traces, [node.argument], info)
            res = []
            for case in node.cases:
                # todo handle patterns; this is like a conditional, we could learn something about pathcond here
                res.extend(guarded_interp_node(trace_map(t1, lambda s: s.add_pathcond(f"case_L{context_line}_pattern_{case['cpattern']}:matched")),
                                               case["cbody"],
                                               info))
            return res

        case AST.AssignNode():
            trace_expansion_pairs = expand(traces, node.val, info)
            return [record_assignment(t, node.var, join_fields(rhs)) for (t, rhs) in trace_expansion_pairs]


        case AST.SemiNode():
            t2 = guarded_interp_node(traces, node.left_operand, info, command_cb)
            return guarded_interp_node(t2, node.right_operand, info, command_cb)


        case AST.WhileNode():
            return handle_while(traces, node, info)

        case AST.ForNode():
            t0, var_name = expand_assuming_single_constant_word(traces, node.variable, info)
            t1, items = expand_args_dumb(t0, node.argument, info)
            if join_fields(items).count.max <= 1:
                Reporter.add_error(reporter.LoopRunsOnce())
            # Interpret the for loop body
            t2 = [record_assignment(t, var_name, arbitrary_field(node.argument,
                                                                 ArbitraryType.APPROXIMATION,
                                                                 t.latest_state)) \
                  for t in t1]
            # TODO: Will want to interpret the body multiple times (up to max count of times).
            # If the arguments are known statically we can even do every iteration.
            # Otherwise, we should do it with a *fresh* arbitrary_field every time!
            # (maybe need to add an extra distinguisher to CompletelyArbitrary for that?)
            t3 = guarded_interp_node(t2, node.body, info)
            return t3

        case AST.FileRedirNode():
            res = []
            for t, redir_args in expand(traces, node.arg, info):
                res.append(t)
                match redir_args:
                    case [Field(SymStr([something]), WordCount(1, 1))]:
                        if isinstance(something, str) and something in t.latest_state.fundefs:
                            # TODO: Associate the warning with the trace that caused it
                            Reporter.add_error(reporter.RedirectToFunction(something))
                    case [Field(CompletelyArbitrary(), _)]:
                        pass
                    case _:
                        logging.warning(f"Found a redir to multiple words: {trim_string_for_logging(str(redir_args))} - Ignoring.")
                        pass
            # TODO: Also handle the effects of redirection on the FS
            return res

        case AST.RedirNode():
            t1 = guarded_interp_node(traces, node.node, info)
            t2 = t1
            for redir in node.redir_list:
                t2 = guarded_interp_node(t2, redir, info)
            return t2


        case AST.DefunNode():
            # Note: the type annotation in the Shasta source code is *wrong* for node.name -- it's a string
            t1, name = expand_assuming_single_constant_word(traces, node.name, info)
            return trace_map(t1, lambda s: s.set_fundef(name, freeze(node)))

        # todo bring other cases as needed

        case _:
            raise NotImplementedError(
                    f"node type {type(node)} not handled",
                    node
                )

trace_count = 1
def maybe_collapse_traces(traces: Traces) -> Traces:
    global trace_count
    if len(traces) > trace_count:
        logging.debug(f"Too many traces ({len(traces)}), collapsing")
        traces = collapse_traces(traces)
        trace_count = len(traces)
        logging.debug(f"Collapsed to {trace_count} traces")
    return traces

def starting_state() -> State:
    # env["IFS"] = ShellVar(" \t\n")
    # for defaultvar in ["HOME", "PWD", "OLDPWD", "PATH"]:
    #     env[defaultvar] = ShellVar(symb_utils.create_fresh_var(f"default_{defaultvar}"))
    root = State((), FrozenDict(), FrozenDict(), FrozenDict(), SymStr(("0",)), None)
    starter_env = {
        "HOME": ShellVar(arbitrary_field(None, ArbitraryType.ENVIRONMENT, root)),
        "PWD": ShellVar(arbitrary_field(None, ArbitraryType.ENVIRONMENT, root)),
        "OLDPWD": ShellVar(arbitrary_field(None, ArbitraryType.ENVIRONMENT, root)),
        "PATH": ShellVar(arbitrary_field(None, ArbitraryType.ENVIRONMENT, root))
    }
    return root.extend_env(starter_env)

@dataclass(frozen=True)
class AST_parse:
    ast_node: AST.AstNode
    rawtext: str
    line_before: int
    line_after: int  # relevant for mysterious shell reasons

def trim_string_for_logging(s: str, max_len: int = 300) -> str:
    return s if len(s) <= max_len else s[:max_len] + "..."

def symb_engine(nodes: list[AST_parse], info: ScriptInfo) -> list[Trace]:
    global context_line
    logging.debug(f"Running symb engine with {len(nodes)} raw nodes")
    traces = [Trace((starting_state(),))]
    for node in nodes:
        context_line = node.line_before
        logging.debug(f"Interpreting next node (line {context_line}) {trim_string_for_logging(node.ast_node.pretty())}")
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
    print(json.dumps(report_dict, indent=2))


if __name__ == "__main__":
    argmain()

