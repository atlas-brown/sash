from copy import copy
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
from sash.interpreter_config import InterpConfig
import sash.reporter as reporter
import sash.symb_utils as symb_utils

def handle_commandnode(traces: Traces,
                       node: AST.CommandNode,
                       config: InterpConfig) -> Traces:
    logging.debug(f"Handling command node {trim_string_for_logging(node.pretty())} with {len(traces)} traces")
    t1, expanded_args = expand_args_dumb(traces, node.arguments, config)
    logging.debug(f"Expanded cmd to {expanded_args}")

    if expanded_args:
        match field_to_str(expanded_args[0]):
            case "rm":
                logging.debug("Exploring all possible expansions of rm args")
                expansions = expand_args(traces, node.arguments, config)
                simplified_expansions = collapse_equiv_trace_expansions(expansions)
                for trace, arg_fields in simplified_expansions:
                    handle_rm(arg_fields)
            case "set":
                t1 = handle_set(expanded_args, t1)
            case "exit":
                t1 = handle_exit(t1)
            case some_name if isinstance(some_name, str):
                # todo: we could actually not use `expand_args_dumb` here, and instead do trace-specific expansion, since the function body is handled trace-specifically anyway
                # deferred for now until we actually need it (see test_function_call_multipath)
                t1 = handle_function_call_or_unknown(some_name, expanded_args[1:], t1, config)
            case _:
                logging.debug(f"Non-constant command invocation {expanded_args}, optimistically treating as no-op")
    t2 = t1
    for redir in node.redir_list:
        t2 = t2.extend(guarded_interp_node(t1, redir, config)) or t2

    config.apply_expanded_command_cbs(expanded_args)
    logging.debug(f"Done with command {trim_string_for_logging(node.pretty())} after expanding its args to {expanded_args} (it had assignments: {node.assignments})")
    return t2

def handle_rm(expanded_args: List[Field]) -> None:
    logging.debug(f"Checking rm command with expansion possibility: {expanded_args}")
    def is_protected(path):
        return any(path in [p, p + "/", p + "/*"] for p in Config.get("PROTECTED_PATHS"))
    for arg_field in expanded_args[1:]:
        if (path := field_to_str(arg_field)) and is_protected(path):
            Reporter.add_error(reporter.DeleteSystemFile(path, context_line))
        match arg_field:
            case Field(CompletelyArbitrary(source=source), WordCount(max=m)) if m > 1:
                Reporter.add_error(reporter.DangerousWordSplit(source, context_line))
        match arg_field:
            case Field(CompletelyArbitrary(prefix=pre, suffix=suf), WordCount(min, max)) if min == 0 or max > 1:
                if pre is not None and (path := symb_utils.symbstr_to_str(pre.parts)) and is_protected(path):
                    Reporter.add_error(reporter.WordSplitCouldDeleteSystemFile(path, context_line))
                if suf is not None and (path := symb_utils.symbstr_to_str(suf.parts)) and is_protected(path):
                    Reporter.add_error(reporter.WordSplitCouldDeleteSystemFile(path, context_line))

def handle_function_call_or_unknown(func_name: str,
                                    arg_fields: List[Field],
                                    traces: Traces,
                                    config: InterpConfig) -> Traces:
    # is it a known function, and the same one across all traces?
    func_defs = {t.latest_state.lookup_fundef(func_name) for t in traces}
    if len(func_defs) == 1:
        if None in func_defs:
            return handle_unknown_command(func_name, arg_fields, traces, config)
        else:
            the_func = func_defs.pop()
            assert isinstance(the_func, FrozenAst)
            return handle_function_call(the_func.ast, arg_fields, traces, config)
    else:
        logging.error(f"Name {func_name} is defined as different functions across traces, giving up on this call")
        return traces

def handle_unknown_command(name: str,
                           arg_fields: List[Field],
                           traces: Traces,
                           config: InterpConfig) -> Traces:
    if (future_line := config.info.future_fundef_lines.get(name)) is not None and context_line < future_line:
        Reporter.add_error(reporter.UndefinedFunction(name, context_line))

    if name.endswith("/") or any(name in t.latest_state.known_nonexistant_commands for t in traces):
        Reporter.add_error(reporter.NotACommand(name, context_line))

    logging.debug(f"Unknown command {name}, optimistically treating as no-op")
    return traces

def handle_function_call(func_node: AST.DefunNode,
                         arg_fields: List[Field],
                         traces: Traces,
                         config: InterpConfig) -> Traces:
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
    return guarded_interp_node(t1, func_node.body, config)

def record_assignment(trace: Trace, var: str, rhs: Field) -> Trace:
    return trace.extend(lambda s: s.set_env(var, ShellVar(rhs)))

def handle_while(traces: Traces,
                 node: AST.WhileNode,
                 config: InterpConfig):
    logging.debug(f"Checking while loop for an infinite loop")
    test_cmds = []
    def get_the_test(cmd_fields):
        test_cmds.append(cmd_fields)
    temp_config = config.add_expanded_command_callback(get_the_test)
    logging.debug(f"Interpreting first iteration")
    t1 = guarded_interp_node(traces, node.test, temp_config)
    logging.debug(f"collected test_cmds: {test_cmds}")
    # Special case: never runs
    if interpret_test(test_cmds[0]) == False:
        logging.debug(f"While loop never runs")
        return t1
    # todo extend path condition
    t2 = guarded_interp_node(t1, node.body, config)
    logging.debug(f"Interpreting second iteration")
    t3 = guarded_interp_node(t2, node.test, temp_config)
    # Special case: only one iteration
    if interpret_test(test_cmds[1]) == False:
        logging.debug(f"While loop only runs once")
        return t3
    logging.debug(f"collected test_cmds: {test_cmds}")
    # todo extend path condition
    t4 = guarded_interp_node(t3, node.body, config)
    logging.debug(f"Interpreting third test")
    t5 = guarded_interp_node(t4, node.test, temp_config)
    logging.debug(f"collected test_cmds: {test_cmds}")

    logging.debug(f"Checking constant test cond")
    assert len(test_cmds) == 3
    if is_constant_test(test_cmds[2], test_cmds[1]):
        Reporter.add_error(reporter.InfiniteLoop(node, context_line))

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
    if not len(args) in {2, 3, 4}:
        return None

    # Check if if all arguments are concrete
    field_content = [f.content for f in cmd]
    if not all(isinstance(c, SymStr) for c in field_content):
        return None
    field_parts = [c.parts for c in field_content if isinstance(c, SymStr)]
    if not all(all(isinstance(p, str) for p in parts) for parts in field_parts):
        return None

    if len(args) == 2:
        match (args[0].content, args[1].content):
            case (SymStr([s1]), SymStr([s2])) if s1 in {"-f", "-d", "-e"} and s2 == "":
                return False
            case _:
                return None

    if len(args) == 3:
        if args[0].content == SymStr(("!",)):
            res = interpret_test(args[1:])
            return not res if res is not None else None
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

def handle_set(expanded_args: List[Field], traces: Traces) -> Traces:
    to_set = set()
    for arg in expanded_args[1:]:
        match arg:
            case Field(SymStr([flag]), WordCount(1, 1)) if isinstance(flag, str):
                if flag.startswith("-"):
                    if SetOptions.relevant(flag):
                        to_set.update(flag[1:])
                    else:
                        logging.debug(f"set: ignoring irrelevant option: {flag}")
                else:
                    raise NotImplementedError(f"set: option unsetting not implemented: {expanded_args}")

            case _:
                raise NotImplementedError(f"set with non-constant args: {expanded_args}")
    return trace_map(traces, lambda s: s.set_options(to_set))

def handle_if(traces: Traces, node: AST.IfNode, config: InterpConfig) -> Traces:
    test_cmds = []
    def get_the_test(cmd_fields):
        test_cmds.append(cmd_fields)
    temp_config = config.add_expanded_command_callback(get_the_test)
    t1 = guarded_interp_node(traces, node.cond, temp_config)
    logging.debug(f"collected test_cmds: {test_cmds}")
    logging.debug(f"Checking constant test cond")
    if len(test_cmds) == 0:
        logging.warning("Failed to collect any test commands? Giving up on constant condition check.")
        test_result = None
    else:
        test_result = interpret_test(test_cmds[-1])
    if test_result is not None:
        Reporter.add_error(reporter.ConstantCondition(test_cmds, context_line))
        if test_result == True and node.else_b is not None:
            Reporter.add_error(reporter.DeadCode(node.else_b, context_line))
        elif test_result == False:
            Reporter.add_error(reporter.DeadCode(node.then_b, context_line))
    # Several possibilities here:
    # 1. Constant test true -- interpret then_b and return that
    # 2. Constant test false with no else -- just return t1
    # 3. Constant test false with else -- interpret else_b and return that
    # 4. Non-constant test -- interpret both branches and combine results
    res = t1
    if test_result in {True, None}:
        # todo: extend pathcond with actual condition true/false
        res = guarded_interp_node(trace_map(t1, lambda s: s.add_pathcond(f"cond_L{context_line}:true")),
                                    node.then_b,
                                    config)
    if node.else_b is not None and test_result in {False, None}:
        t3 = guarded_interp_node(trace_map(t1, lambda s: s.add_pathcond(f"cond_L{context_line}:false")),
                                    node.else_b,
                                    config)
        return res + t3
    else:
        return res

def handle_exit(traces: Traces) -> Traces:
    logging.debug(f"Handling exit command, terminating {len(traces)} traces")
    return trace_map(traces, lambda s: s.terminate())

# ============================================================
#                  Symbolic Expander
# ============================================================

def expand(traces: Traces,
           stuff: list[AST.ArgChar],
           config: InterpConfig,
           prefix: dict[int, list[Field]] = {}) -> list[tuple[Trace, list[Field]]]:
    """If supplied, `prefix` specifies a prefix to prepend to each expansion produced by each trace (mapped by its id)."""
    res = []
    for trace in traces:
        prefix_fields = prefix.get(id(trace), [])
        # expand_simple(stuff, trace.latest_state, config)
        for expanded_fields, new_state in expand_simple(stuff, trace.latest_state, config):
            new_trace = trace.extend(new_state)
            res.append((new_trace, prefix_fields + expanded_fields))
    return res

# Different fields are definitely separated; things within a field *may be separated as well!*
def expand_simple(stuff: list[AST.ArgChar],
                  state: State,
                  config: InterpConfig) -> list[tuple[list[Field], State]]:
    IFS = " \t\n"

    @dataclass
    class Partial:
        ## Notes on what's happening here:
        # Need to build up fields with individual characters, and also SymStrs that we come across
        # Along the way, will see some CompletelyArbitrarys
        # The CompletelyArbitrarys kind of soak up the whole field -- if any part of a final field
        # is arbitrary, then the whole field is arbitrary
        # BUT -- we can preserve some info that will lead to better error messages:
        # if there's some SymStr that's being prepended or appended to an arbitrary thing, we can
        # record that the SymStr is a known prefix or suffix of the arbitrary thing
        quoted: bool
        state: State
        combined_fields_so_far: list[Field | None] = field(default_factory=list) # None's mean a hard break due to IFS
        field_so_far: list[str | SymVar] = field(default_factory=list)
        field_so_far_words_min: int = 1
        field_so_far_words_max: int | float = 1

        def add_a_field(self, one_field: Field) -> None:
            match one_field.content:
                case CompletelyArbitrary():
                    self.finish_field_so_far()
                    self.combined_fields_so_far.append(one_field)
                case SymStr(parts):
                    self.field_so_far.extend(parts)
                    if one_field.count.min > 1:
                        self.field_so_far_words_min += one_field.count.min - 1
                    if one_field.count.max > 1:
                        self.field_so_far_words_max += one_field.count.max - 1

        def finish_field_so_far(self, IFS: bool = False) -> None:
            if self.field_so_far != []:
                self.combined_fields_so_far.append(Field(SymStr(tuple(self.field_so_far)).simplify(),
                                                         WordCount(self.field_so_far_words_min, self.field_so_far_words_max)))
                if IFS:
                    self.combined_fields_so_far.append(None)
                self.field_so_far = []
                self.field_so_far_words_min = 1
                self.field_so_far_words_max = 1

        @classmethod
        def add_the_default(cls, who: 'Partial', var: AST.VArgChar):
            default_expansions = expand_inner(var.arg, Partial(False, who.state))
            assert len(default_expansions) == 1, "default value expansion forking not implemented"
            default_fields, default_state = default_expansions[0].finish()
            assert default_state == who.state, "default value expansion should not change state"
            for default_field in default_fields:
                who.add_a_field(default_field)

        def next(self, argchar: AST.ArgChar) -> list['Partial']:
            match argchar:
                # todo what about globs?
                case AST.CArgChar() as c:
                    if not self.quoted and c.pretty() in IFS:
                        self.finish_field_so_far(True)
                    else:
                        self.field_so_far.append(c.pretty(AST.QUOTED if self.quoted else AST.UNQUOTED))
                case AST.EArgChar() as c:
                    self.field_so_far.append(c.pretty(AST.QUOTED if self.quoted else AST.UNQUOTED))
                case AST.QArgChar() as q:
                    partial_for_inside = Partial(True, self.state)
                    res = []
                    for inside_partial in expand_inner(q.arg, partial_for_inside):
                        fields_inside, state_inside = inside_partial.finish()
                        one_field = join_fields(fields_inside).quote()
                        continuing_partial = self.fork_state(state_inside)
                        continuing_partial.add_a_field(one_field)
                        res.append(continuing_partial)
                    return res
                case AST.VArgChar() as var:
                    if (v := state.lookup(var.var)):
                        if var.fmt == "Normal" or (var.fmt == "Minus" and not var.null):
                            # explanation of the minus case: the POSIX spec says that for
                            # ${VAR-default} the result is the value of $VAR as long as $VAR is set -- whether it's empty ("null") or not
                            # ^^ this corresponds to the second part of the condition above (var.null false means no `:`)
                            self.add_a_field(v.value)
                        elif var.fmt == "Minus" and var.null:
                            # This is the case that it's ${VAR:-default}:
                            # IF $VAR is empty, take the default
                            # Otherwise, take the result is $VAR
                            match v.value:
                                case Field(_, WordCount(0, 0)):
                                    # We know $VAR is empty
                                    Partial.add_the_default(self, var)
                                case Field(SymStr(stuff), _) if all(isinstance(thing, str) for thing in stuff):
                                    # We know $VAR is NOT empty
                                    self.add_a_field(v.value)
                                case something_not_constant: # either a symbolic str or arbitrary
                                    non_default, default = self.fork(f"{var.pretty()} takes the default value")
                                    Partial.add_the_default(default, var)
                                    non_default.add_a_field(arbitrary_field(var, ArbitraryType.APPROXIMATION, state))
                                    return [non_default, default]
                        else:
                            logging.info(f"expansion: treating var {var.pretty()} with unhandled fmt {var.fmt} as completely arbitrary field")
                            self.add_a_field(arbitrary_field(var, ArbitraryType.APPROXIMATION, state))
                    elif var.fmt == "Minus":
                        # This is the case that $VAR is unset: take the default
                        non_default, default = self.fork(f"{var.pretty()} takes the default value")
                        Partial.add_the_default(default, var)
                        non_default.add_a_field(arbitrary_field(var, ArbitraryType.ENVIRONMENT, state))
                        return [non_default, default]
                    else:
                        # todo we should report path information
                        if not is_special_var(var.var):
                            error_code = reporter.UnboundIDSetU if state.opts.is_set(SetOptions.NOUNSET) else reporter.UnboundID
                            Reporter.add_error(error_code(var.pretty(), context_line))
                        self.add_a_field(arbitrary_field(var,
                                                         ArbitraryType.APPROXIMATION if is_special_var(var.var) else ArbitraryType.ENVIRONMENT,
                                                         state))
                case AST.BArgChar() as b:
                    logging.info(f"expansion: treating backquote argchar {b.pretty()} as completely arbitrary field")
                    # todo use the trace
                    t = guarded_interp_node([Trace((state,))], b.node, config)
                    self.add_a_field(arbitrary_field(b, ArbitraryType.APPROXIMATION, state))
                case _:
                    # todo: if its a command substitution, need to go interp it
                    logging.error(f"argchar: {argchar} {type(argchar)}")
                    logging.info(f"expansion: treating unhandled argchar as completely arbitrary field: {argchar.pretty()}")
                    self.add_a_field(arbitrary_field(argchar, ArbitraryType.APPROXIMATION, state))

            # Most cases fall through to here, no forking going on
            return [self]

        def finish(self) -> tuple[list[Field], State]:
            self.finish_field_so_far()
            # Join the combined fields so far, folding symstrs into arbitrary fields as prefixes and suffixes
            split = split_at(self.combined_fields_so_far, None)
            return ([merge_partial_fields(part, None, self.state) for part in split if part != []], self.state)

        def fork(self, pathcond: Any) -> tuple['Partial', 'Partial']:
            lhs = self.fork_state(self.state.add_pathcond(pathcond))
            rhs = self.fork_state(self.state.add_pathcond("not " + pathcond))
            return (lhs, rhs)

        def fork_state(self, new_state: State) -> 'Partial':
            return replace(self,
                           state=new_state,
                           combined_fields_so_far=copy(self.combined_fields_so_far),
                           field_so_far=copy(self.field_so_far))

    def expand_inner(chars: list[AST.ArgChar], partial: Partial) -> list[Partial]:
        expansions = [partial]
        for argchar in chars:
            expansions = [next_expansion for expansion in expansions for next_expansion in expansion.next(argchar)]
        return expansions

    partials = expand_inner(stuff, Partial(False, state))
    return [partial.finish() for partial in partials]


def expand_args_dumb(traces: Traces,
                     args: list[list[AST.ArgChar]],
                     config: InterpConfig) -> tuple[Traces, list[Field]]:
    expanded_args: List[Field] = []
    res_traces = traces
    for arg in args:
        expansions = expand(res_traces, arg, config)
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
            # todo could be smarter about the ranges of word counts and prefix/suffix preservation, but wont do unless needed
            expanded_args.append(arbitrary_field(arg, ArbitraryType.APPROXIMATION, None))
    return res_traces, expanded_args

def expand_args(traces: Traces,
                args: list[list[AST.ArgChar]],
                config: InterpConfig) -> list[tuple[Trace, list[Field]]]:
    prefixes = {id(trace): [] for trace in traces}
    res_traces = traces
    for arg in args:
        expansions = expand(res_traces, arg, config, prefixes)
        res_traces = [expansion[0] for expansion in expansions]
        for res_trace, expanded_fields in expansions:
            prefixes[id(res_trace)] = expanded_fields

    return [(trace, prefixes[id(trace)]) for trace in res_traces]

def expand_assuming_single_constant_word(traces: Traces,
                                         stuff: list[AST.ArgChar],
                                         config: InterpConfig) -> tuple[Traces, str]:
    t0, fields = expand_args_dumb(traces, [stuff], config)
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

def collapse_equiv_trace_expansions(expansions: list[tuple[Trace, list[Field]]]) -> list[tuple[Trace, list[Field]]]:
    """Remove duplicate expansions from `expansions`, picking a representative trace for each unique expansion."""
    seen = {}
    result = []
    for trace, fields in expansions:
        key = tuple(fields)
        if key not in seen:
            seen[key] = trace
            result.append((trace, fields))
    return result


# ============================================================
#                  Symbolic Interpreter
# ============================================================

context_line = None

trace_count = 1
def collapse_traces_if_too_many(traces: Traces) -> Traces:
    global trace_count
    if len(traces) > trace_count:
        logging.debug(f"Too many traces ({len(traces)}), collapsing")
        traces = collapse_traces(traces)
        trace_count = len(traces)
        logging.debug(f"Collapsed to {trace_count} traces")
    return traces

def drop_terminated_traces(traces: Traces) -> Traces:
    active_traces = [t for t in traces if not t.latest_state.terminated]
    if len(active_traces) < len(traces):
        logging.debug(f"Dropping {len(traces) - len(active_traces)} terminated traces")
    return active_traces

def guarded_interp_node(traces: Traces,
                        node: AST.AstNode,
                        config: InterpConfig) -> Traces:
    try:
        return interp_node(traces, node, config)
    except NotImplementedError as e:
        logging.error(f"Interp raised: {traceback.format_exc()}. Ignoring.")
        return traces

def interp_node(traces: Traces,
                node: AST.AstNode,
                config: InterpConfig) -> Traces:
    # refer to https://github.com/binpash/shasta/blob/main/shasta/ast_node.py
    traces = drop_terminated_traces(traces)
    traces = config.trace_collapser(traces)
    traces = config.apply_node_cbs(traces, node)
    if not traces:
        logging.debug(f"No active traces when interpreting {trim_string_for_logging(node.pretty())}, reporting dead code and returning early")
        Reporter.add_error(reporter.DeadCode(node, context_line))
        return traces

    logging.debug(f"Interpreting {trim_string_for_logging(node.pretty())} with {len(traces)} traces")
    match node:
        case AST.CommandNode() if not (node.arguments == [] and node.assignments != []):
            return handle_commandnode(traces, node, config)
        case AST.CommandNode() if node.arguments == [] and node.assignments != []: # why is kind of parse possible??
            # do the assignments inside
            t = traces
            for assign in node.assignments:
                t = guarded_interp_node(t, assign, config)
            return t

        case AST.IfNode():
            return handle_if(traces, node, config)

        case AST.CaseNode():
            t1, case_arg_fields = expand_args_dumb(traces, [node.argument], config)
            res = []
            for case in node.cases:
                # todo handle patterns; this is like a conditional, we could learn something about pathcond here
                res.extend(guarded_interp_node(trace_map(t1, lambda s: s.add_pathcond(f"case_L{context_line}_pattern_{case['cpattern']}:matched")),
                                               case["cbody"],
                                               config))
            return res

        case AST.AssignNode():
            trace_expansion_pairs = expand(traces, node.val, config)
            return [record_assignment(t, node.var, join_fields(rhs)) for (t, rhs) in trace_expansion_pairs]


        case AST.SemiNode():
            t2 = guarded_interp_node(traces, node.left_operand, config)
            return guarded_interp_node(t2, node.right_operand, config)


        case AST.WhileNode():
            return handle_while(traces, node, config)

        case AST.ForNode():
            t0, var_name = expand_assuming_single_constant_word(traces, node.variable, config)
            t1, items = expand_args_dumb(t0, node.argument, config)
            if join_fields(items).count.max <= 1:
                Reporter.add_error(reporter.LoopRunsOnce(node, context_line))
            # if all items are constant, we can unroll the loop
            if all(symb_utils.is_constant(field) for field in items):
                logging.debug(f"For loop over constant items, unrolling: {items}")
                t2 = t1
                for item_field in items:
                    t2 = [record_assignment(t, var_name, item_field) for t in t2]
                    t2 = guarded_interp_node(t2, node.body, config)
                return t2
            else:
                t2 = [record_assignment(t, var_name, arbitrary_field(node.argument,
                                                                    ArbitraryType.APPROXIMATION,
                                                                    t.latest_state)) \
                    for t in t1]
                # TODO: Will want to interpret the body multiple times (up to max count of times).
                t3 = guarded_interp_node(t2, node.body, config)
                return t3

        case AST.FileRedirNode():
            res = []
            for t, redir_args in expand(traces, node.arg, config):
                res.append(t)
                match redir_args:
                    case [Field(SymStr([something]), WordCount(1, 1))]:
                        if isinstance(something, str) and something in t.latest_state.fundefs:
                            # TODO: Associate the warning with the trace that caused it
                            Reporter.add_error(reporter.RedirectToFunction(something, context_line))
                    case [Field(CompletelyArbitrary(), _)]:
                        pass
                    case _:
                        logging.warning(f"Found a redir to multiple words: {trim_string_for_logging(str(redir_args))} - Ignoring.")
                        pass
            # TODO: Also handle the effects of redirection on the FS
            return res

        case AST.RedirNode():
            t1 = guarded_interp_node(traces, node.node, config)
            t2 = t1
            for redir in node.redir_list:
                t2 = guarded_interp_node(t2, redir, config)
            return t2


        case AST.DefunNode():
            # Note: the type annotation in the Shasta source code is *wrong* for node.name -- it's a string
            t1, name = expand_assuming_single_constant_word(traces, node.name, config)
            return trace_map(t1, lambda s: s.set_fundef(name, freeze(node)))

        case AST.AndNode() | AST.OrNode():
            t1 = guarded_interp_node(traces, node.left_operand, config)
            t2 = guarded_interp_node(trace_map(t1, lambda s: s.add_pathcond(f"{node.NodeName}_L{context_line}:{'true' if isinstance(node, AST.AndNode) else 'false'}")),
                                               node.right_operand,
                                               config)
            t3 = trace_map(t1, lambda s: s.add_pathcond(f"{node.NodeName}_L{context_line}:{'false' if isinstance(node, AST.AndNode) else 'true'}"))
            return t2 + t3

        # todo bring other cases as needed

        case _:
            raise NotImplementedError(
                    f"node type {type(node)} not handled",
                    node
                )

def starting_state() -> State:
    # env["IFS"] = ShellVar(" \t\n")
    # for defaultvar in ["HOME", "PWD", "OLDPWD", "PATH"]:
    #     env[defaultvar] = ShellVar(symb_utils.create_fresh_var(f"default_{defaultvar}"))
    root = State((), FrozenDict(), FrozenDict(), FrozenDict(), SymStr(("0",)), None, SetOptions())
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

def symb_engine(nodes: list[AST_parse], config: InterpConfig) -> list[Trace]:
    global context_line
    logging.debug(f"Running symb engine with {len(nodes)} raw nodes")
    traces = [Trace((starting_state(),))]

    future_fundef_lines: dict[str, int] = {}
    for node in nodes:
        if isinstance(node.ast_node, AST.DefunNode):
            try:
                _, func_name = expand_assuming_single_constant_word(traces, node.ast_node.name, config)
                future_fundef_lines[func_name] = node.line_before + 1
            except AssertionError:
                # If the function name is not a single constant word, we may not be able to record its line number.
                continue
    updated_config = config.set_info(ScriptInfo(future_fundef_lines=FrozenDict(future_fundef_lines)))

    for node in nodes:
        context_line = node.line_before + 1
        logging.debug(f"Interpreting next node (line {context_line}) {trim_string_for_logging(node.ast_node.pretty())}")
        traces = guarded_interp_node(traces, node.ast_node, updated_config)

    return traces


def parse_script(filename) -> list[AST_parse]:
    shasta_nodes = parse_shell_to_asts(filename)
    logging.debug(f"Parsed script with {len(shasta_nodes)} nodes")
    nodes = [AST_parse(*x) for x in shasta_nodes]
    return nodes


def symbexec_file(input_file: str,
                  config: InterpConfig = InterpConfig(trace_collapser = collapse_traces_if_too_many)) -> Traces:
    nodes = parse_script(input_file)
    # opt_store = parse_shebang_args(input_file)
    return symb_engine(nodes, config)

