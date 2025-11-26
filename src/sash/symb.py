import logging
import traceback
from collections import defaultdict
from copy import copy
from dataclasses import dataclass, field, replace
from enum import Enum
from math import inf
from threading import Event
from typing import NamedTuple, Callable
from copy import deepcopy

import shasta.ast_node as AST

import sash.parser as parser
import sash.reporter as reporter
import sash.util as util
from sash.config import Config
from sash.constraints import *
from sash.frozen import FrozenAst, FrozenDict, freeze, freeze_thing
from sash.interpreter_config import InterpConfig, UnboundVariablePolicy
from sash.reporter import Reporter
from sash.solver import field_to_z3
from sash.specs import get_spec
from sash.state import ArbitraryType, CompletelyArbitrary, Field, FuncMap, SetOptions, ShellVar, State, SymStr, SymVar, Trace, Traces, WordCount, collapse_traces, is_special_var, trace_map


def handle_commandnode(traces: Traces,
                       node: AST.CommandNode,
                       config: InterpConfig) -> Traces:
    if logging.getLogger().isEnabledFor(logging.DEBUG):
        logging.debug("Handling command node %s with %d traces", trim_string_for_logging(node.pretty()), len(traces))

    t1, expanded_args = expand_args_dumb(traces, node.arguments, config)
    logging.debug("Expanded cmd to %s", expanded_args)

    if expanded_args:
        match expanded_args[0].try_to_str():
            case "rm":
                logging.debug("Exploring all possible expansions of rm args")
                expansions = expand_args(traces, node.arguments, config)
                simplified_expansions = collapse_equiv_trace_expansions(expansions)
                cmd_traces = []
                for arg_fields, traces in simplified_expansions.items():
                    for trace in traces:
                        ts, tf = handle_rm(arg_fields, trace, node)
                        cmd_traces.append(ts)
                        if config.in_checked_position:
                            logging.debug("rm is in a checked position? Adding failure traces")
                            cmd_traces.append(tf)
                t1 = cmd_traces
            case "set":
                t1 = handle_set(expanded_args, t1)
            case "exit":
                t1 = handle_exit(t1)
            case "read":
                t1 = handle_read(expanded_args, t1)
            case "xargs":
                t1 = handle_xargs(t1, node, expanded_args, config)
            # TODO: Unify rm with other commands
            case cmd_name if spec := get_spec(cmd_name, tuple(expanded_args)):
                logging.debug("Adding %s precondition: %s", cmd_name, spec.check)
                t_precond = trace_map(t1, lambda s: s.add_assertion(spec.check, source_str=node.pretty(), source_line=context_line))
                t_success = trace_map(t_precond,
                                      lambda s: s.add_pathcond(spec.success_postcond)\
                                                 .update_fs(spec.success_postcond)\
                                                 .set_last_exit_code(SymStr(("0",)), spec.failure_postcond))
                t_failure = []
                if config.in_checked_position:
                    t_failure = trace_map(t_precond,
                                          lambda s: s.add_pathcond(spec.failure_postcond)\
                                                     .update_fs(spec.failure_postcond)\
                                                     .set_last_exit_code(SymStr(("1",)), spec.failure_postcond))
                t1 = t_success + t_failure
            case some_name if isinstance(some_name, str):
                # todo: we could actually not use `expand_args_dumb` here, and instead do trace-specific expansion, since the function body is handled trace-specifically anyway
                # deferred for now until we actually need it (see test_function_call_multipath)
                t1 = handle_function_call_or_unknown(some_name, expanded_args[1:], t1, config)
            case _:
                logging.debug("Non-constant command invocation %s, optimistically treating as no-op", expanded_args)

    for redir in node.redir_list:
        t1 = guarded_interp_node(t1, redir, config)

    config.apply_expanded_command_cbs(expanded_args)

    if logging.getLogger().isEnabledFor(logging.DEBUG):
        logging.debug("Done with command %s after expanding its args to %s (it had assignments: %s)",
                      trim_string_for_logging(node.pretty()), expanded_args, node.assignments)
    return t1

def handle_rm(expanded_args: tuple[Field], trace: Trace, node: AST.CommandNode) -> tuple[Trace, Trace]:
    logging.debug("Checking rm command with expansion possibility: %s", expanded_args)
    spec = get_spec("rm", expanded_args)

    assert spec is not None, "Expected rm spec to always be found"

    logging.debug("Adding rm precondition: %s", spec.check)
    trace = trace.extend(lambda s: s.add_assertion(spec.check, source_str=node.pretty(), source_line=context_line))

    def is_protected(path):
        return any(path in [p, p + "/", p + "/*"] for p in Config.get("PROTECTED_PATHS"))
    for arg_field in expanded_args[1:]:
        if (path := arg_field.try_to_str()) and is_protected(path):
            Reporter.add_issue(reporter.DeleteSystemFile(path, context_line))
        match arg_field:
            case Field(CompletelyArbitrary(source=source), WordCount(max=m)) if m > 1:
                Reporter.add_issue(reporter.DangerousWordSplit(source, context_line))
        match arg_field:
            case Field(CompletelyArbitrary(prefix=pre, suffix=suf), WordCount(min, max)) if min == 0 or max > 1:
                if pre is not None and (path := pre.try_to_str()) and is_protected(path):
                    Reporter.add_issue(reporter.WordSplitCouldDeleteSystemFile(path, context_line))
                if suf is not None and (path := suf.try_to_str()) and is_protected(path):
                    Reporter.add_issue(reporter.WordSplitCouldDeleteSystemFile(path, context_line))

    return (
        trace.extend(lambda s: s.add_pathcond(spec.success_postcond)\
                                .update_fs(spec.success_postcond)\
                                .set_last_exit_code(SymStr(("0",)), spec.failure_postcond)),
        trace.extend(lambda s: s.add_pathcond(spec.failure_postcond)\
                                .update_fs(spec.failure_postcond)\
                                .set_last_exit_code(SymStr(("1",)), spec.failure_postcond))
    )


def handle_function_call_or_unknown(func_name: str,
                                    arg_fields: list[Field],
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
            return handle_function_call(func_name, the_func.ast, arg_fields, traces, config)
    else:
        logging.error("Name %s is defined as different functions across traces, giving up on this call", func_name)
        return traces

def handle_unknown_command(name: str,
                           arg_fields: list[Field],
                           traces: Traces,
                           config: InterpConfig) -> Traces:
    if name in func_map.funcs.keys():
        Reporter.add_issue(reporter.UndefinedFunction(name, context_line))

    if name.endswith("/") or any(name in t.latest_state.known_nonexistent_commands for t in traces):
        Reporter.add_issue(reporter.NotACommand(name, context_line))

    logging.debug("Unknown command %s, optimistically treating as no-op", name)
    return traces

def handle_function_call(name: str,
                         func_node: AST.DefunNode,
                         arg_fields: list[Field],
                         traces: Traces,
                         config: InterpConfig) -> Traces:
    if logging.getLogger().isEnabledFor(logging.DEBUG):
        logging.debug("Handling function call to %s with args %s",
                      trim_string_for_logging(func_node.pretty()), arg_fields)

    func_map.called.add(name) # record that this function was called
    # As long as arg_fields are a single word, map those to local positional parameters
    # as soon as we hit a field that is not a single word, give up
    localenv: dict[str, ShellVar] = {}
    for i, arg in enumerate(arg_fields):
        if arg.count == WordCount(1, 1):
            localenv[str(i + 1)] = ShellVar(arg)
        else:
            logging.debug("Function argument %d is not guaranteed to be a single word, giving up on positional parameters (%s)", i, arg)
            break
    logging.debug("Bound localenv for call: %s", localenv)
    t1 = []
    for t in traces:
        if name in t.latest_state.call_stack:
            logging.error("Found recursive function definition! %s via %s", name, t.latest_state.call_stack)
            return traces
        t1.append(t.extend(lambda s: s.enter_function(name).extend_localenv(localenv)))
    call_result_traces = guarded_interp_node(t1, func_node.body, config)
    # TODO: should actually pop the localenv as well! need a stack of localenvs...
    return [t.extend(lambda s: s.exit_function()) for t in call_result_traces]

def record_assignment(trace: Trace, var: str, rhs: Field) -> Trace:
    return trace.extend(lambda s: s.set_env(var, ShellVar(rhs)).set_last_exit_code(SymStr(("0",))))

def handle_while(traces: Traces,
                 node: AST.WhileNode,
                 config: InterpConfig):
    logging.debug("Checking while loop for an infinite loop")
    test_cmds = []
    def get_the_test(cmd_fields):
        test_cmds.append(cmd_fields)
    temp_config = config.add_expanded_command_callback(get_the_test)


    logging.debug("Interpreting first iteration")
    t1 = guarded_interp_node(traces, node.test, temp_config)
    logging.debug("collected test_cmds: %s", test_cmds)
    # Special case: never runs
    if len(test_cmds) > 0 and interpret_test(test_cmds[0]) == False:
        logging.debug("While loop never runs")
        return t1

    t1 = [t for t in t1 if t.latest_state.last_exit_code != SymStr(("1",))]
    t_skip_body = [t for t in traces if t.latest_state.last_exit_code == SymStr(("1",))]
    t2 = guarded_interp_node(t1, node.body, config)


    logging.debug("Interpreting second iteration")
    # If all traces happen to terminate in the body, t3 will be empty after the next line
    # Additionally, test_cmds will not have a second entry
    t3 = guarded_interp_node(t2, node.test, temp_config)
    if len(t3) == 0:
        logging.debug("All traces terminated on first iter of while body")
        return t3 + t_skip_body
    # Special case: only one iteration
    if len(test_cmds) < 2:
        logging.debug("Failing to collect test commands? Giving up on constant loop checks.")
        return t3 + t_skip_body
    elif interpret_test(test_cmds[1]) == False:
        logging.debug("While loop only runs once")
        return t3 + t_skip_body
    elif is_constant_test(test_cmds[0], test_cmds[1]):
        Reporter.add_issue(reporter.InfiniteLoop(node, context_line))
        return t3 + t_skip_body
    logging.debug("collected test_cmds: %s", test_cmds)
    # todo extend path condition
    t4 = guarded_interp_node(t3, node.body, config)


    logging.debug("Interpreting third test")
    t5 = guarded_interp_node(t4, node.test, temp_config)
    # If all traces happen to terminate on the second iteration, t5 will be empty
    # Additionally, test_cmds will not have a third entry
    if len(t5) == 0:
        logging.debug("All traces terminated on second iter of while body")
        return t5 + t_skip_body
    logging.debug("collected test_cmds: %s", test_cmds)

    logging.debug("Checking constant test cond")
    if len(test_cmds) > 2 and is_constant_test(test_cmds[2], test_cmds[1]):
        Reporter.add_issue(reporter.InfiniteLoop(node, context_line))

    return t5 + t_skip_body

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
    """Return true or false if `cmd` is a test that always returns either of the two results. Return None if unknown."""
    if len(cmd) < 1:
        return None

    if not isinstance(cmd[0].content, SymStr):
        return None

    if not is_test(cmd[0].content.parts[0]):
        return None

    args = cmd[1:]
    if cmd[-1].content == SymStr(("]",)):
        args = args[:-1]
    if not len(args) in {2, 3}:
        return None

    if len(args) == 2:
        if args[0].content == SymStr(("!",)):
            res = interpret_test(args[1:])
            return not res if res is not None else None
        match (args[0].content, args[1].content):
            case (SymStr([op]), SymStr([s])) if op == "-n":
                return s != ""
            case (SymStr([op]), SymStr([s])) if op == "-z":
                return s == ""
            case (SymStr([op]), SymStr([s1])) if op in {"-f", "-d", "-e"} and s1 == "":
                return False
            case _:
                return None

    if len(args) == 3:
        match (args[0].content, args[1].content, args[2].content):
            case (SymStr([s1]), SymStr([op]), SymStr([s2])) if op == "=":
                return s1 == s2
            case (SymStr([s1]), SymStr([op]), SymStr([s2])) if op == "!=":
                return s1 != s2
            case (CompletelyArbitrary() as lhs, SymStr([op]), CompletelyArbitrary() as rhs) if op in ["=", "!="]:
                # if the two are definitely the same, we can say something in this case
                if lhs == rhs:
                    return op == "="
                else:
                    return None
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

def handle_set(expanded_args: list[Field], traces: Traces) -> Traces:
    to_set = set()
    for arg in expanded_args[1:]:
        match arg:
            case Field(SymStr([flag]), WordCount(1, 1)) if isinstance(flag, str):
                if flag.startswith("-"):
                    if SetOptions.relevant(flag):
                        to_set.update(flag[1:])
                    else:
                        logging.debug("set: ignoring irrelevant option: %s", flag)
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
    temp_config = replace(temp_config, in_checked_position=True)
    t1 = guarded_interp_node(traces, node.cond, temp_config)
    logging.debug("collected test_cmds: %s", test_cmds)
    logging.debug("Checking constant test cond")
    if len(test_cmds) == 0:
        logging.warning("Failed to collect any test commands? Giving up on constant condition check.")
        test_result = None
    else:
        logging.debug("Checking if test command %s is constant true/false", test_cmds[-1])
        test_result = interpret_test(test_cmds[-1])
        logging.debug("Test command result: %s", test_result)
    if test_result is not None:
        Reporter.add_issue(reporter.ConstantCondition(test_cmds, context_line))
        if test_result == True and (node.else_b is not None and node.else_b.pretty()):
                                                             # Hack because libdash sometimes gives empty else bodies
            t1 = trace_map(t1, lambda s: s.set_last_exit_code(SymStr(("0",))))
            logging.debug("Reporting dead code in else branch.")
            Reporter.add_issue(reporter.DeadCode(node.else_b, context_line))
        elif test_result == False:
            t1 = trace_map(t1, lambda s: s.set_last_exit_code(SymStr(("1",))))
            logging.debug("Reporting dead code in then branch")
            Reporter.add_issue(reporter.DeadCode(node.then_b, context_line))
    else:
        logging.debug("FORK: explicit if")
    # Several possibilities here:
    # 1. Constant test true -- interpret then_b and return that
    # 2. Constant test false with no else -- just return t1
    # 3. Constant test false with else -- interpret else_b and return that
    # 4. Non-constant test -- interpret both branches and combine results
    if test_result is True:
        return guarded_interp_node(t1, node.then_b, config)
    elif test_result is False:
        if node.else_b is not None:
            return guarded_interp_node(t1, node.else_b, config)
        else:
            return t1
    else:
        return handle_branch(t1,
                            lambda ts: guarded_interp_node(ts, node.then_b, config),
                            lambda fs: guarded_interp_node(fs, node.else_b, config) if node.else_b is not None else fs,
                            node,
                            config)

def handle_exit(traces: Traces) -> Traces:
    logging.debug("Handling exit command, terminating %d traces", len(traces))
    return trace_map(traces, lambda s: s.terminate())

def handle_branch(traces: Traces, success_cb: Callable[[Traces], Traces], failure_cb: Callable[[Traces], Traces], node: AST.AstNode, config: InterpConfig) -> Traces:
    t_success = [t for t in traces if t.latest_state.last_exit_code == SymStr(("0",))]
    t_failure = [t for t in traces if t.latest_state.last_exit_code == SymStr(("1",))]
    t_other   = [t for t in traces if t.latest_state.last_exit_code not in {SymStr(("0",)), SymStr(("1",))}]
    t_then = success_cb(t_success + trace_map(t_other, lambda s: s.set_last_exit_code(SymStr(("0",)))))
    t_else = failure_cb(t_failure + trace_map(t_other, lambda s: s.set_last_exit_code(SymStr(("1",)))))
    t_then_bp, t_else_bp = config.branch_policy(node, t_then, t_else)
    res = t_then_bp + t_else_bp
    if all(t.latest_state.terminated for t in res):
        logging.debug("All traces terminated with branch policy decision; ignoring policy for this branch (line %d)", context_line)
        return t_then + t_else
    else:
        return res

def handle_read(expanded_args: list[Field], traces: Traces) -> Traces:
    """Handle a `read` command with given expanded args (list of `Fields`) on the given traces."""
    collected: list[tuple[str, Field]] = []
    # Collect (variable name, original field) pairs from args.
    for arg in expanded_args[1:]:
        try:
            name = arg.try_to_str()
        except Exception:
            name = None
        if isinstance(name, str) and name != "":
            collected.append((name, arg))
    # If there are no variable names, return traces with exit code set to 0, as `read` consumed input but bound nothing.
    if not collected:
        return [t.extend(lambda s: s.set_last_exit_code(SymStr(("0",)))) for t in traces]
    new_traces: Traces = []
    for trace in traces:
        curr_trace = trace
        # For each variable to be read into, record an assignment of that variable to the corresponding field.
        for var_name, value_field in collected:
            curr_trace = record_assignment(curr_trace, var_name, value_field)
        new_traces.append(curr_trace)
    return new_traces

def handle_xargs(traces: Traces, node: AST.CommandNode, expanded_args: list[Field], config: InterpConfig) -> Traces:
    match expanded_args:
        case [Field(SymStr(("xargs",)), _),
              Field(SymStr(("-I",)), _),
              Field(SymStr((thename,)), _),
              *the_cmd]:
            mangled_cmdnode = deepcopy(node)
            mangled_cmdnode.arguments = mangled_cmdnode.arguments[3:]
            t1 = handle_commandnode(traces, mangled_cmdnode, config)
            t2 = handle_commandnode(t1, mangled_cmdnode, config)
            return t2
        case _:
            logging.warning("Ignoring unsupported xargs invocation: %s", node.pretty())
            return traces

# ============================================================
#                  Symbolic Expander
# ============================================================

# Symbolic expander design overview:
#
# - `expand` is the generic interface to expansion of a single "thing", which boils down to calling `expand_simple` for each active trace
#
# - `expand_simple` implements expansion for a single active trace
#
# - `expand_args` and `expand_args_dumb` provide higher level interfaces for expanding all of the "things" in a list of arguments,
#   + `expand_args_dumb` collapses different expansions into a single one by approximating fields
#   + `expand_args` does not do that
#
# - `expand_assuming_single_constant_word` is a convenience for "thing"s that should only ever be a single constant word

def expand(traces: Traces,
           stuff: list[AST.ArgChar],
           config: InterpConfig,
           prefix: dict[int, list[Field]] = {}) -> list[tuple[Trace, list[Field]]]:
    """
    Return all possible expansions of `stuff` across all `traces`, using the state information in each trace.
    Result is a list of trace and expansion pairs.

    The result traces may be extensions of those in `traces`, and may include *more* traces than were provided (with `traces`) because expansion may introduce forking of traces -- for instance, to explore taking both the default and non-default value of `${VAR:-default}`.

    If supplied, `prefix` specifies a prefix to prepend to each expansion produced by each trace (mapped by its id).
    """
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
                  config: InterpConfig) -> list[tuple[list[Field], State]]: # TODO why is this order swapped wrt `expand`?
    """
    Return all possible expansions of `stuff` for the given `state`.
    Result is a list of pairs of: an expansion plus a new state associated with that expansion.
    """
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
                        if c.pretty() == "*" and not self.quoted:
                            self.field_so_far_words_max = inf
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
                    if var.var == "?":
                        self.add_a_field(Field(self.state.last_exit_code, WordCount(1, 1)))
                    elif (v := self.state.lookup(var.var)):
                        if var.fmt == "Normal" or (var.fmt == "Minus" and not var.null and not v.ghost):
                            # explanation of the minus case: the POSIX spec says that for
                            # ${VAR-default} the result is the value of $VAR as long as $VAR is set -- whether it's empty ("null") or not
                            # ^^ this corresponds to the second part of the condition above (var.null false means no `:`)
                            self.add_a_field(v.value)
                        elif var.fmt == "Minus" and (var.null or v.ghost):
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
                                    non_default, default = self.fork(Description(f"{var.pretty()} takes the default value"))
                                    Partial.add_the_default(default, var)
                                    non_default.add_a_field(arbitrary_field(var, ArbitraryType.APPROXIMATION, self.state))
                                    return [non_default, default]
                        elif var.fmt in {"Length", "TrimR", "TrimRMax", "TrimL", "TrimLMax"} and v.value == Field(SymStr(("",)), WordCount(0, 0)):
                            # All of these manipulations have known results on the empty string
                            logging.info("Special casing string manipulation expansion on empty string")
                            match var.fmt:
                                case "Length":
                                    self.add_a_field(Field(SymStr(("0",)), WordCount(1, 1)))
                                case "TrimR" | "TrimRMax" | "TrimL" | "TrimLMax":
                                    self.add_a_field(Field(SymStr(("",)), WordCount(0, 0)))
                        else:
                            logging.info("expansion: treating var %s with unhandled fmt %s as completely arbitrary field", var.pretty(), var.fmt)
                            self.add_a_field(arbitrary_field(var, ArbitraryType.APPROXIMATION, self.state))
                    elif var.fmt == "Minus":
                        # This is the case that $VAR is unset: take the default
                        if config.unbound_policy == UnboundVariablePolicy.EMPTY:
                            logging.info("expansion: treating unset var %s as empty string due to config, so taking the default (%s) unconditionally",
                                         var.pretty(), util.shasta_pretty(var.arg))
                            Partial.add_the_default(self, var)
                        else:
                            logging.debug("expansion: forking on unset var %s to take default (%s) or arbitrary",
                                          var.var, util.shasta_pretty(var.arg))
                            non_default, default = self.fork(Description(f"{var.var} takes the default value {Field.create_constant(util.shasta_pretty(var.arg))}"))
                            Partial.add_the_default(default, var)
                            arbitrary_for_this_var = arbitrary_field(var, ArbitraryType.ENVIRONMENT, non_default.state)
                            # localenv to avoid creating an arbitrary that persists beyond a function body
                            non_default.state = non_default.state.extend_localenv({var.var: ShellVar(arbitrary_for_this_var, ghost=True)})
                            non_default.add_a_field(arbitrary_for_this_var)
                            return [non_default, default]
                    else:
                        # todo we should report path information
                        if not is_special_var(var.var):
                            error_code = reporter.UnboundIDSetU if self.state.opts.is_set(SetOptions.NOUNSET) else reporter.UnboundID
                            Reporter.add_issue(error_code(var.pretty(), context_line))
                        if config.unbound_policy == UnboundVariablePolicy.EMPTY:
                            logging.info("expansion: treating unbound var %s as empty string due to config", var.pretty())
                            self.add_a_field(Field(SymStr(("",)), WordCount(0, 0)))
                        else:
                            arbitrary_for_this_var = arbitrary_field(var,
                                                                    ArbitraryType.APPROXIMATION if is_special_var(var.var) else ArbitraryType.ENVIRONMENT,
                                                                    self.state)
                            # localenv to avoid creating an arbitrary that persists beyond a function body
                            self.state = self.state.extend_localenv({var.var: ShellVar(arbitrary_for_this_var, ghost=True)})
                            self.add_a_field(arbitrary_for_this_var)
                case AST.BArgChar() as b:
                    logging.info("expansion: treating backquote argchar %s as completely arbitrary field", b.pretty())
                    # todo use the trace: this case suggests we should really generalize the interface of `expand_simple` to be from one trace to many, instead of one state to many
                    t = guarded_interp_node([Trace((self.state,))], b.node, config)
                    self.add_a_field(arbitrary_field(b, ArbitraryType.APPROXIMATION, self.state))
                case _:
                    logging.error("argchar: %s %s", argchar.pretty(), type(argchar))
                    logging.info("expansion: treating unhandled argchar as completely arbitrary field: %s", argchar.pretty())
                    self.add_a_field(arbitrary_field(argchar, ArbitraryType.APPROXIMATION, self.state))

            # Most cases fall through to here, no forking going on
            return [self]

        def finish(self) -> tuple[list[Field], State]:
            self.finish_field_so_far()
            # Join the combined fields so far, folding symstrs into arbitrary fields as prefixes and suffixes
            split = util.split_at(self.combined_fields_so_far, None)
            return ([merge_partial_fields(part, None, self.state) for part in split if part != []], self.state)

        def fork(self, pathcond: Constraint) -> tuple['Partial', 'Partial']:
            logging.debug("FORK: expansion")
            lhs = self.fork_state(self.state.add_pathcond(pathcond))
            rhs = self.fork_state(self.state.add_pathcond(Not(pathcond)))
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
    """
    Expand `args` into a *single* list of fields, collapsing differences in the expansion of each arg between traces by approximating that arg with `CompletelyArbitrary`.
    Result is a pair of: a new set of traces, and the expansion of `args`.

    This function is a simplified interface to `expand`, which collapses the different expansion possibilities arising from different traces.
    The simplification comes at the cost of approximation.
    """
    expanded_args: list[Field] = []
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
    """
    Return all possible expansions of `args`, across all `traces`.
    Result is a list of pairs of: a trace, and an associated expansion of `args`.
    """
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
    """
    Expand `stuff` into a string, under the assumption that it expands to a single constant word across all `traces`.
    Result is a pair of: a new set of traces, and the string.

    If the assumption is violated, raises an AssertionError.
    """
    t0, fields = expand_args_dumb(traces, [stuff], config)
    match fields:
        case [Field(SymStr((one_word,)), WordCount(1, 1))] if isinstance(one_word, str):
            return t0, one_word
        case _:
            assert False, f"expected {stuff} to be a single constant word, but found something else after expansion: {fields}"

# =====================
#  Field manipulation
# =====================

def arbitrary_field(ast: AST.AstNode, kind: ArbitraryType, producing_state: State | None) -> Field:
    return Field(CompletelyArbitrary(freeze_thing(ast), kind, producing_state),
                 WordCount(0, inf))

def join_fields(fields: list[Field]) -> Field:
    """Join a list of fields into one field that approximates all of them."""
    return merge_partial_fields(fields, sep=" ", state=None)

def merge_partial_fields(fields: list[Field], sep: str | None = " ", state: State | None = None) -> Field:
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


def collapse_fields(fields: list[Field], source: AST.AstNode | None = None) -> Field:
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

def collapse_equiv_trace_expansions(expansions: list[tuple[Trace, list[Field]]]) -> dict[tuple[Field], list[Trace]]:
    """Collect all originating traces for each unique expansion."""
    seen = defaultdict(list)
    for trace, fields in expansions:
        key = tuple(fields)
        seen[key].append(trace)
    return seen


# ============================================================
#                  Symbolic Interpreter
# ============================================================

context_line = None

trace_count = 1
def collapse_traces_if_too_many(traces: Traces) -> Traces:
    global trace_count
    if len(traces) > trace_count:
        logging.debug("Too many traces (%d), collapsing", len(traces))
        traces = collapse_traces(traces)
        trace_count = len(traces)
        logging.debug("Collapsed to %d traces", trace_count)
    return traces

def drop_terminated_traces(traces: Traces) -> Traces:
    active_traces = [t for t in traces if not t.latest_state.terminated]
    if len(active_traces) < len(traces):
        logging.debug("Dropping %d terminated traces", len(traces) - len(active_traces))
    return active_traces

def guarded_interp_node(traces: Traces,
                        node: AST.AstNode,
                        config: InterpConfig) -> Traces:
    global stop_event
    global context_line
    if stop_event and stop_event.is_set():
        logging.info("Symbolic execution interrupted by stop event")
        Reporter.set_timed_out()
        return traces # same behavior as if the rest of the script is not implemented
        # todo is this sound?

    prev_context_line = context_line
    context_line = getattr(node, "line_number", context_line)

    try:
        res = interp_node(traces, node, config)
        context_line = prev_context_line
        return res
    except NotImplementedError as e:
        logging.error("Interp raised: %s. Ignoring.", e)
        context_line = prev_context_line
        return traces

def interp_node(traces: Traces,
                node: AST.AstNode,
                config: InterpConfig) -> Traces:
    # refer to https://github.com/binpash/shasta/blob/main/shasta/ast_node.py
    traces = drop_terminated_traces(traces)
    traces = config.trace_collapser(traces)
    traces = config.apply_node_cbs(traces, node)
    if not traces:
        logging.debug("No active traces when interpreting %s, reporting dead code and returning early", trim_string_for_logging(node.pretty()))
        Reporter.add_issue(reporter.DeadCode(node, context_line))
        return traces

    logging.debug("Interpreting line %d %s with %d traces",
                  context_line, trim_string_for_logging(node.pretty()), len(traces))
    match node:
        case AST.CommandNode():
            if len(node.arguments) == 0:
                # assignment (e.g. VAR=value)
                # note: assignments get parsed into CommandNodes with empty arguments (unfortunately)
                t = traces
                for assign in node.assignments:
                    assert isinstance(assign, AST.AssignNode)
                    t = guarded_interp_node(t, assign, config)
                return t

            # command (e.g. echo hello)
            # note: local assignments (e.g. LC_ALL=C sort file.txt) are ignored for now

            return handle_commandnode(traces, node, config)

        case AST.IfNode():
            return handle_if(traces, node, config)

        case AST.CaseNode():
            t1, case_arg_fields = expand_args_dumb(traces, [node.argument], config)
            res = []
            for case in node.cases:
                logging.debug("FORK: explicit case")
                # todo handle patterns; this is like a conditional, we could learn something about pathcond here
                res.extend(guarded_interp_node(trace_map(t1, lambda s: s.add_pathcond(Description(f"case_L{context_line}_pattern_{case['cpattern']}:matched"))),
                                               case["cbody"],
                                               config))
            return res

        case AST.AssignNode():
            # The expand() function is used everywhere where expansion is needed.
            # For that reason, if a quoted argument is passed in, the resulting Field will always contain a maximum word count of 1.
            # If that weren't the case, the following command would be interpreted wrongly: cp "filename with spaces" dest.
            # However, in the context of assignments, we want the resulting Field to have the correct word count, even if the argument is quoted.
            # A simple way to achieve this is to unquote the argument before passing it to expand().

            # "if the value of the node is a single quoted argument, remove the quotes"
            val = node.val[0].arg if len(node.val) == 1 and isinstance(node.val[0], AST.QArgChar) else node.val
            trace_expansion_pairs = expand(traces, val, config)
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
                Reporter.add_issue(reporter.LoopRunsOnce(node, context_line))
            # if all items are constant, we can unroll the loop
            if all(field.is_constant() for field in items):
                logging.debug("For loop over constant items, unrolling: %s", items)
                t2 = t1
                for item_field in items:
                    t2 = [record_assignment(t, var_name, item_field) for t in t2]
                    t2 = guarded_interp_node(t2, node.body, config)
                return t2
            else:
                t_res = None # result is just the traces after one iteration
                t_current = t1
                for i in range(config.max_loop_unroll):
                    logging.debug("For loop unrolling iteration %d/%d", i+1, config.max_loop_unroll)
                    t2 = [record_assignment(t, var_name, arbitrary_field(node.argument,
                                                                        ArbitraryType.APPROXIMATION,
                                                                        t.latest_state)) \
                        for t in t_current]
                    t_current = guarded_interp_node(t2, node.body, config)
                    if t_res is None:
                        t_res = t_current
                assert t_res is not None
                return t_res

        case AST.FileRedirNode():
            res = []
            for t, redir_args in expand(traces, node.arg, config):
                t_precond = t.extend(t.latest_state.add_assertion(And.from_field_iter(redir_args, lambda f: IsRead(f)), source_str=node.pretty(), source_line=context_line))
                t_postcond = t_precond.extend(t_precond.latest_state.update_fs(And.from_field_iter(redir_args, lambda f: IsFile(f))))
                res.append(t_postcond)
                match redir_args:
                    case [Field(SymStr([something]), WordCount(1, 1))]:
                        if isinstance(something, str) and something in t.latest_state.fundefs:
                            # TODO: Associate the warning with the trace that caused it
                            Reporter.add_issue(reporter.RedirectToFunction(something, context_line))
                    case [Field(CompletelyArbitrary(), _)]:
                        pass
                    case _:
                        logging.warning("Found a redir to multiple words: %s - Ignoring.", trim_string_for_logging(str(redir_args)))
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
            logging.debug("FORK: explicit AND/OR")
            # workaround the `checked_position` by manually adding the failure traces back
            # because some programs use huge functions inside AND/OR nodes, and there's no need to fork on EVERYTHING inside them
            t1 = guarded_interp_node(traces, node.left_operand, config) # intentionally not in a checked position
            t_failure = [t.fail_last_command() for t in t1 if t.latest_state.last_exit_code == SymStr(("0",))]
            def success(traces_with_exit_0: Traces) -> Traces:
                if isinstance(node, AST.AndNode):
                    return guarded_interp_node(traces_with_exit_0, node.right_operand, config)
                else:
                    return traces_with_exit_0
            def failure(traces_with_exit_1: Traces) -> Traces:
                logging.debug("Inside the failure case of %s node with %d traces",
                              'AND' if isinstance(node, AST.AndNode) else 'OR',
                              len(traces_with_exit_1))
                if isinstance(node, AST.AndNode):
                    return traces_with_exit_1
                else:
                    return guarded_interp_node(traces_with_exit_1, node.right_operand, config)
            return handle_branch(t1 + t_failure, success, failure, node, config)

        case AST.NotNode():
            t1 = guarded_interp_node(traces, node.body, config)
            t2 = trace_map(t1,
                          lambda s: s if s.last_exit_code not in {SymStr(("0",)), SymStr(("1",))}
                                      else s.set_last_exit_code(SymStr(("1",)) if s.last_exit_code == SymStr(("0",)) else SymStr(("0",))))
            return t2

        case AST.PipeNode():
            t = traces
            # Sequentially interpret each command in the pipe, and return the aggregated traces.
            for cmd in node.items:
                t = guarded_interp_node(t, cmd, config)
            return t

        # todo bring other cases as needed

        case _:
            raise NotImplementedError(f"node type {node.NodeName} not handled")

def starting_state(fs_model: FSModel | None = None) -> State:
    # env["IFS"] = ShellVar(" \t\n")
    # for defaultvar in ["HOME", "PWD", "OLDPWD", "PATH"]:
    #     env[defaultvar] = ShellVar(SymStr(util.create_fresh_varname(f"default_{defaultvar}"))
    root = State(fs_model = FSModelSimple(field_to_z3)) if fs_model is None else State(fs_model = fs_model)
    make_ast = lambda var: AST.VArgChar("Normal", False, var, [])
    starter_env = {
        "HOME": ShellVar(arbitrary_field(make_ast("HOME"), ArbitraryType.ENVIRONMENT, root)),
        "PWD": ShellVar(arbitrary_field(make_ast("PWD"), ArbitraryType.ENVIRONMENT, root)),
        "OLDPWD": ShellVar(arbitrary_field(make_ast("OLDPWD"), ArbitraryType.ENVIRONMENT, root)),
        "PATH": ShellVar(arbitrary_field(make_ast("PATH"), ArbitraryType.ENVIRONMENT, root))
    }
    return root.extend_env(starter_env)

def trim_string_for_logging(s: str, max_len: int = 300) -> str:
    return s if len(s) <= max_len else s[:max_len] + "..."

def find_func_defs(traces: Traces, nodes: list[parser.WrappedAst], config: InterpConfig) -> FrozenDict[str, AST.Command]:
    # TODO: Write unit tests for function definitions being recorded correctly (low priority)
    funcs: FrozenDict[str, AST.Command] = FrozenDict({})
    for node in nodes:
        if not isinstance(node.ast_node, AST.Command):
            continue

        # The functions defined in these nodes are not available at the top level
        # Limitation: We only track top-level-visible function definitions for now
        skip = [
            AST.PipeNode,
            AST.SubshellNode,
            AST.WhileNode
        ]
        for n in util.iter_ast_command(node.ast_node, skip=skip):
            if isinstance(n, AST.DefunNode):
                try:
                    _, func_name = expand_assuming_single_constant_word(traces, n.name, config)
                    funcs = funcs.set(func_name, n.body)
                except AssertionError:
                    # Only statically-known function names are recorded
                    continue

    return funcs


class SymbexecStatus(Enum):
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    FAILED = "failed"


class SymbexecResult(NamedTuple):
    status: SymbexecStatus
    traces: Traces


# TODO: make the FS model selection configurable via the `InterpConfig`
def symb_engine(nodes: list[parser.WrappedAst], config: InterpConfig) -> Traces:
    global context_line
    global func_map
    logging.debug("Running symb engine with %d raw nodes", len(nodes))
    traces = [Trace((starting_state(),))]

    func_map = replace(FuncMap(funcs=find_func_defs(traces, nodes, config)))

    for node in nodes:
        context_line = node.get_line_number()
        logging.debug("Interpreting next node (line %d) %s",
                      context_line, trim_string_for_logging(node.ast_node.pretty()))
        traces = guarded_interp_node(traces, node.ast_node, config)

    func_traces: dict[str, Traces] = {}
    for (name, node) in func_map.uncalled_funcs().items():
        logging.debug("Interpreting uncalled function '%s'", name)
        func_traces[name] = guarded_interp_node([Trace((starting_state(),))], node, config)

    return traces + [t for ts in func_traces.values() for t in ts]

def symbexec_file(input_file: str,
                  config: InterpConfig,
                  stop: Event | None) -> SymbexecResult:
    global stop_event
    stop_event = stop

    try:
        def branch_policy_half_n_half_if_too_many(node, t_then: Traces, t_else: Traces) -> tuple[Traces, Traces]:
            if len(t_then) + len(t_else) > 256:
                logging.info("Too many traces, dropping half of them in branch policy")
                half_then = [t for i, t in enumerate(t_then) if i % 2 == 0]
                half_else = [t for i, t in enumerate(t_else) if i % 2 == 0]
                return (half_then, half_else)
            else:
                return (t_then, t_else)
        def branch_policy_only_then(node, t_then: Traces, t_else: Traces) -> tuple[Traces, Traces]:
            return (t_then, []) if t_then else ([], t_else)
        def branch_policy_only_else(node, t_then: Traces, t_else: Traces) -> tuple[Traces, Traces]:
            return ([], t_else) if t_else else (t_then, [])

        nodes = parser.parse_shell_script(input_file)
        # opt_store = parse_shebang_args(input_file)
        if config.DFS_first:
            logging.info("Doing whole execution with a single trace first (DFS_first)")
            logging.info("DFS run: only taking THEN branches")
            symb_engine(nodes, replace(config, branch_policy=branch_policy_only_then))
            logging.info("DFS run: only taking ELSE branches")
            symb_engine(nodes, replace(config, branch_policy=branch_policy_only_else))
            logging.info("DFS run: only taking THEN branches with unbound variables as empty strings")
            symb_engine(nodes, replace(config, branch_policy=branch_policy_only_then, unbound_policy=UnboundVariablePolicy.EMPTY))
            logging.info("DFS run: only taking ELSE branches with unbound variables as empty strings")
            symb_engine(nodes, replace(config, branch_policy=branch_policy_only_else, unbound_policy=UnboundVariablePolicy.EMPTY))
            issues_so_far = Reporter._issues.copy()
            logging.info("DFS run: treating unbound variables solely as empty strings")
            symb_engine(nodes, replace(config, unbound_policy=UnboundVariablePolicy.EMPTY))
            Reporter.drop_issues({reporter.Code.DELETE_SYSTEM_FILE})
            Reporter._issues = Reporter._issues | issues_so_far # this dance ensures that any del_sys_files found before the last run are kept
            # logging.info("DFS run: exploring the first trace only")
            # symb_engine(nodes, replace(config, trace_collapser = lambda ts: ts[:1]))
            logging.info("DFS_first run complete, proceeding with normal symbolic execution")
            Reporter.drop_issues({reporter.Code.DEAD_CODE, reporter.Code.CONSTANT_CONDITION}) # unreliable with branch policies

        traces = symb_engine(nodes, replace(config, branch_policy=branch_policy_half_n_half_if_too_many))
        if Reporter.get_timed_out():
            return SymbexecResult(SymbexecStatus.INTERRUPTED, traces)
        return SymbexecResult(SymbexecStatus.COMPLETED, traces)
    except Exception as e:
        logging.error("Symbolic execution failed:")
        logging.error(traceback.format_exc())
        return SymbexecResult(SymbexecStatus.FAILED, [])

stop_event: Event | None = None
func_map = FuncMap()
