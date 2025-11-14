import logging
import traceback
from collections import defaultdict
from copy import copy
from dataclasses import dataclass
from math import inf
from threading import Event
from typing import NamedTuple

import shasta.ast_node as AST

import sash.reporter as reporter
import sash.symb_utils as symb_utils
from sash.config import Config
from sash.frozen import FrozenAst, freeze, freeze_thing
from sash.interpreter_config import InterpConfig
from sash.parser import *
from sash.reporter import Reporter
from sash.specs import *
from sash.state import *
from sash.util import *


def set_exit_code_arbitrary(traces: Traces) -> Traces:
    """Set the exit code to an arbitrary symbolic value to denote that it is unknown."""
    return trace_map(traces, lambda s: s.set_last_exit_code(SymStr((SymVar("exit_code"),))))

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
                cmd_traces = []
                for arg_fields, traces in simplified_expansions.items():
                    for trace in traces:
                        ts, tf = handle_rm(arg_fields, trace)
                        cmd_traces.append(ts)
                        if config.in_checked_position:
                            cmd_traces.append(tf)
                t1 = cmd_traces
            case "set":
                t1 = handle_set(expanded_args, t1)
            case "exit":
                t1 = handle_exit(t1)
            # TODO: Unify rm with other commands
            case cmd_name if spec := get_spec(cmd_name, tuple(expanded_args)):
                t_precond = trace_map(t1, lambda s: s.add_assertion(spec.check))
                t_success = trace_map(t_precond, lambda s: s.add_pathcond(spec.success_postcond).update_fs(spec.success_postcond).set_last_exit_code(SymStr(("0",))))
                t_failure = []
                if config.in_checked_position:
                    t_failure = trace_map(t_precond, lambda s: s.add_pathcond(spec.failure_postcond).update_fs(spec.failure_postcond).set_last_exit_code(SymStr(("1",))))
                t1 = t_success + t_failure
            case some_name if isinstance(some_name, str):
                # todo: we could actually not use `expand_args_dumb` here, and instead do trace-specific expansion, since the function body is handled trace-specifically anyway
                # deferred for now until we actually need it (see test_function_call_multipath)
                t1 = handle_function_call_or_unknown(some_name, expanded_args[1:], t1, config)
            case _:
                logging.debug(f"Non-constant command invocation {expanded_args}, optimistically treating as no-op")

    t2 = set_exit_code_arbitrary(t1)
    for redir in node.redir_list:
        t2 = t2.extend(guarded_interp_node(t1, redir, config)) or t2

    config.apply_expanded_command_cbs(expanded_args)
    logging.debug(f"Done with command {trim_string_for_logging(node.pretty())} after expanding its args to {expanded_args} (it had assignments: {node.assignments})")
    return t2

def handle_rm(expanded_args: tuple[Field], trace: Trace) -> tuple[Trace, Trace]:
    logging.debug(f"Checking rm command with expansion possibility: {expanded_args}")
    spec = rm_spec(expanded_args)

    logging.debug(f"Adding rm precondition: {spec.check}")
    trace = trace.extend(lambda s: s.add_assertion(spec.check))

    def is_protected(path):
        return any(path in [p, p + "/", p + "/*"] for p in Config.get("PROTECTED_PATHS"))
    for arg_field in expanded_args[1:]:
        if (path := field_to_str(arg_field)) and is_protected(path):
            Reporter.add_issue(reporter.DeleteSystemFile(path, context_line))
        match arg_field:
            case Field(CompletelyArbitrary(source=source), WordCount(max=m)) if m > 1:
                Reporter.add_issue(reporter.DangerousWordSplit(source, context_line))
        match arg_field:
            case Field(CompletelyArbitrary(prefix=pre, suffix=suf), WordCount(min, max)) if min == 0 or max > 1:
                if pre is not None and (path := symb_utils.symbstr_to_str(pre.parts)) and is_protected(path):
                    Reporter.add_issue(reporter.WordSplitCouldDeleteSystemFile(path, context_line))
                if suf is not None and (path := symb_utils.symbstr_to_str(suf.parts)) and is_protected(path):
                    Reporter.add_issue(reporter.WordSplitCouldDeleteSystemFile(path, context_line))

    return (
        trace.extend(lambda s: s.add_pathcond(spec.success_postcond).update_fs(spec.success_postcond).set_last_exit_code(SymStr(("0",)))),
        trace.extend(lambda s: s.add_pathcond(spec.failure_postcond).update_fs(spec.failure_postcond).set_last_exit_code(SymStr(("1",))))
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
            return handle_function_call(the_func.ast, arg_fields, traces, config)
    else:
        logging.error(f"Name {func_name} is defined as different functions across traces, giving up on this call")
        return traces

def handle_unknown_command(name: str,
                           arg_fields: list[Field],
                           traces: Traces,
                           config: InterpConfig) -> Traces:
    if name in config.info.known_fundefs_names:
        Reporter.add_issue(reporter.UndefinedFunction(name, context_line))

    if name.endswith("/") or any(name in t.latest_state.known_nonexistant_commands for t in traces):
        Reporter.add_issue(reporter.NotACommand(name, context_line))

    logging.debug(f"Unknown command {name}, optimistically treating as no-op")
    return traces

def handle_function_call(func_node: AST.DefunNode,
                         arg_fields: list[Field],
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
    return trace.extend(lambda s: s.set_env(var, ShellVar(rhs)).set_last_exit_code(SymStr(("0",))))

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
    # If all traces happen to terminate in the body, t3 will be empty after the next line
    # Additionally, test_cmds will not have a second entry
    t3 = guarded_interp_node(t2, node.test, temp_config)
    if len(t3) == 0:
        logging.debug(f"All traces terminated on first iter of while body")
        return t3
    # Special case: only one iteration
    if interpret_test(test_cmds[1]) == False:
        logging.debug(f"While loop only runs once")
        return t3
    logging.debug(f"collected test_cmds: {test_cmds}")
    # todo extend path condition
    t4 = guarded_interp_node(t3, node.body, config)
    logging.debug(f"Interpreting third test")
    t5 = guarded_interp_node(t4, node.test, temp_config)
    # If all traces happen to terminate on the second iteration, t5 will be empty
    # Additionally, test_cmds will not have a third entry
    if len(t5) == 0:
        logging.debug(f"All traces terminated on second iter of while body")
        return t5
    logging.debug(f"collected test_cmds: {test_cmds}")

    logging.debug(f"Checking constant test cond")
    assert len(test_cmds) == 3
    if is_constant_test(test_cmds[2], test_cmds[1]):
        Reporter.add_issue(reporter.InfiniteLoop(node, context_line))

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

    # Check if if all arguments are concrete
    field_content = [f.content for f in cmd]
    if not all(isinstance(c, SymStr) for c in field_content):
        return None
    field_parts = [c.parts for c in field_content if isinstance(c, SymStr)]
    if not all(all(isinstance(p, str) for p in parts) for parts in field_parts):
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
    temp_config = replace(temp_config, in_checked_position=True)
    t1 = guarded_interp_node(traces, node.cond, temp_config)
    logging.debug(f"collected test_cmds: {test_cmds}")
    logging.debug(f"Checking constant test cond")
    if len(test_cmds) == 0:
        logging.warning("Failed to collect any test commands? Giving up on constant condition check.")
        test_result = None
    else:
        logging.debug(f"Checking if test command {test_cmds[-1]} is constant true/false")
        test_result = interpret_test(test_cmds[-1])
        logging.debug(f"Test command result: {test_result}")
    if test_result is not None:
        Reporter.add_issue(reporter.ConstantCondition(test_cmds, context_line))
        if test_result == True and (node.else_b is not None and node.else_b.pretty()):
                                                             # Hack because libdash sometimes gives empty else bodies
            t1 = trace_map(t1, lambda s: s.set_last_exit_code(SymStr(("0",))))
            logging.debug(f"Reporting dead code in else branch.")
            Reporter.add_issue(reporter.DeadCode(node.else_b, context_line))
        elif test_result == False:
            t1 = trace_map(t1, lambda s: s.set_last_exit_code(SymStr(("1",))))
            logging.debug(f"Reporting dead code in then branch")
            Reporter.add_issue(reporter.DeadCode(node.then_b, context_line))
    # Several possibilities here:
    # 1. Constant test true -- interpret then_b and return that
    # 2. Constant test false with no else -- just return t1
    # 3. Constant test false with else -- interpret else_b and return that
    # 4. Non-constant test -- interpret both branches and combine results
    # TODO: Do the same for AND and OR
    t_success = [t for t in t1 if t.latest_state.last_exit_code == SymStr(("0",))]
    t_failure = [t for t in t1 if t.latest_state.last_exit_code == SymStr(("1",))]
    t_other   = [t for t in t1 if t.latest_state.last_exit_code not in {SymStr(("0",)), SymStr(("1",))}]
    assert len(t_success) + len(t_failure) + len(t_other) == len(t1), f"Expected all traces to be either success or failure, got {len(t_success)} success and {len(t_failure)} failure out of {len(t1)} total"
    if test_result in {True, None}:
        # Always take the 'then' branch, unless test condition is known to always be false
        t_then = guarded_interp_node(t_success + trace_map(t_other, lambda s: s.set_last_exit_code(SymStr(("0",)))),
                                    node.then_b,
                                    config)
        return t_then

    if node.else_b is not None and test_result in {False, None}:
        t_else = guarded_interp_node(t_failure + trace_map(t_other, lambda s: s.set_last_exit_code(SymStr(("1",)))),
                                    node.else_b,
                                    config)
        return t_else

    return t_other

def handle_exit(traces: Traces) -> Traces:
    logging.debug(f"Handling exit command, terminating {len(traces)} traces")
    return trace_map(traces, lambda s: s.terminate())

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
                        self.add_a_field(Field(state.last_exit_code, WordCount(1, 1)))
                    elif (v := state.lookup(var.var)):
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
                                    non_default, default = self.fork(Description(f"{var.pretty()} takes the default value"))
                                    Partial.add_the_default(default, var)
                                    non_default.add_a_field(arbitrary_field(var, ArbitraryType.APPROXIMATION, state))
                                    return [non_default, default]
                        else:
                            logging.info(f"expansion: treating var {var.pretty()} with unhandled fmt {var.fmt} as completely arbitrary field")
                            self.add_a_field(arbitrary_field(var, ArbitraryType.APPROXIMATION, state))
                    elif var.fmt == "Minus":
                        # This is the case that $VAR is unset: take the default
                        non_default, default = self.fork(Description(f"{var.var} takes the default value {constant_field(shasta_pretty(var.arg))}"))
                        Partial.add_the_default(default, var)
                        non_default.add_a_field(arbitrary_field(var, ArbitraryType.ENVIRONMENT, state))
                        return [non_default, default]
                    else:
                        # todo we should report path information
                        if not is_special_var(var.var):
                            error_code = reporter.UnboundIDSetU if state.opts.is_set(SetOptions.NOUNSET) else reporter.UnboundID
                            Reporter.add_issue(error_code(var.pretty(), context_line))
                        self.add_a_field(arbitrary_field(var,
                                                         ArbitraryType.APPROXIMATION if is_special_var(var.var) else ArbitraryType.ENVIRONMENT,
                                                         state))
                case AST.BArgChar() as b:
                    logging.info(f"expansion: treating backquote argchar {b.pretty()} as completely arbitrary field")
                    # todo use the trace: this case suggests we should really generalize the interface of `expand_simple` to be from one trace to many, instead of one state to many
                    t = guarded_interp_node([Trace((state,))], b.node, config)
                    self.add_a_field(arbitrary_field(b, ArbitraryType.APPROXIMATION, state))
                case _:
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

        def fork(self, pathcond: Constraint) -> tuple['Partial', 'Partial']:
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

def field_to_str(field: Field) -> str | None:
    match field:
        case Field(SymStr(parts), _):
            return symb_utils.symbstr_to_str(parts)
        case _:
            return None

def is_special_var(name: str) -> bool:
    return name.isdecimal() or name in ["@", "#", "?"]

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
        Reporter.add_issue(reporter.DeadCode(node, context_line))
        return traces

    logging.debug(f"Interpreting {trim_string_for_logging(node.pretty())} with {len(traces)} traces")
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
                            Reporter.add_issue(reporter.RedirectToFunction(something, context_line))
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
            t2 = guarded_interp_node(trace_map(t1, lambda s: s.add_pathcond(Description(f"{node.NodeName}_L{context_line}:{'true' if isinstance(node, AST.AndNode) else 'false'}"))),
                                               node.right_operand,
                                               config)
            t3 = trace_map(t1, lambda s: s.add_pathcond(Description(f"{node.NodeName}_L{context_line}:{'false' if isinstance(node, AST.AndNode) else 'true'}")))
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

def find_func_defs(traces: Traces, nodes: list[WrappedAst], config: InterpConfig) -> set[str]:
    # TODO: Write unit tests for function definitions being recorded correctly (low priority)
    known_fundefs_names: set[str] = set()
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
        for n in symb_utils.iter_ast_commands(node.ast_node, skip=skip):
            if isinstance(n, AST.DefunNode):
                try:
                    _, func_name = expand_assuming_single_constant_word(traces, n.name, config)
                    known_fundefs_names.add(func_name)
                except AssertionError:
                    # Only statically-known function names are recorded
                    continue

    return known_fundefs_names


class SymbexecStatus(Enum):
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    FAILED = "failed"


class SymbexecResult(NamedTuple):
    status: SymbexecStatus
    traces: Traces


def symb_engine(nodes: list[WrappedAst], config: InterpConfig, stop: Event | None) -> SymbexecResult:
    global context_line
    logging.debug(f"Running symb engine with {len(nodes)} raw nodes")
    traces = [Trace((starting_state(),))]

    known_fundefs_names = find_func_defs(traces, nodes, config)
    updated_config = config.set_info(ScriptInfo(known_fundefs_names=frozenset(known_fundefs_names)))

    for node in nodes:
        if stop and stop.is_set():
            logging.info("Symbolic execution interrupted by stop event")
            return SymbexecResult(status=SymbexecStatus.INTERRUPTED, traces=traces)

        context_line = node.line_before + 1
        logging.debug(f"Interpreting next node (line {context_line}) {trim_string_for_logging(node.ast_node.pretty())}")
        traces = guarded_interp_node(traces, node.ast_node, updated_config)

    return SymbexecResult(status=SymbexecStatus.COMPLETED, traces=traces)

def symbexec_file(input_file: str,
                  config: InterpConfig,
                  stop: Event | None) -> SymbexecResult:
    nodes = parse_shell_script(input_file)
    # opt_store = parse_shebang_args(input_file)
    return symb_engine(nodes, config, stop)
