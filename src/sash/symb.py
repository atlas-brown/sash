import logging
import threading
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

from sash.fs import FSModel, FSModelSimple
import sash.parser as parser
import sash.reporter as reporter
from sash.symbolic.strings import ArbitraryType, CompletelyArbitrary, Field, SymStr, SymVar, WordCount
import sash.util as util
from sash.config import Config # TODO: refactor to delete sash.config, move all needed stuff to InterpConfig
from sash.constraints import *
from sash.frozen import FrozenAst, FrozenDict, freeze, freeze_thing
from sash.interpreter_config import BranchDecision, InterpConfig, UnboundVariablePolicy
from sash.reporter import Reporter
from sash.solver import field_to_z3
from sash.specs import get_spec, CmdSpec
from sash.dfs_targeted import *
from sash.symbolic.state import *
from sash.debugtools.logger import DebugLogger


def handle_commandnode(traces: Traces,
                       node: AST.CommandNode,
                       config: InterpConfig) -> Traces:
    if logging.getLogger().isEnabledFor(logging.DEBUG):
        logging.debug("Handling command node %s with %d traces", trim_string_for_logging(node.pretty()), len(traces))

    # Handle variable expansion before we evaluate the command itself
    t1, expanded_args = expand_args_dumb(traces, node.arguments, config)
    t1_active, t1_inactive = drop_terminated_traces(t1)
    if not t1_active:
        logging.debug("All traces terminated during expansion of %s", trim_string_for_logging(node.pretty()))
        return t1
    logging.debug("Expanded cmd to %s", expanded_args)

    if expanded_args and len(node.arguments) >= 2:
        cmd_name = expanded_args[0].try_to_str()
        if cmd_name == "grep":
            # If the command is `grep` and the first argument is not provided (different from an empty string),
            # meaning a pattern is not provided for the command,
            # `grep` will expect input from stdin instead of treating the second argument as a file.
            if expanded_args[1].count.min == 0:
                Reporter.add_issue(reporter.UnexpectedStdin(cmd_name, context_line), config)

    if expanded_args:
        match expanded_args[0].try_to_str():
            case "rm":
                logging.debug("Exploring all possible expansions of rm args")
                expansions = expand_args(t1, node.arguments, config)
                simplified_expansions = collapse_equiv_trace_expansions(expansions)
                cmd_traces = []
                for arg_fields, traces in simplified_expansions.items():
                    for trace in traces:
                        ts, tf = handle_rm(arg_fields, trace, node, config)
                        cmd_traces.append(ts)
                        if config.in_checked_position:
                            logging.debug("rm is in a checked position? Adding failure traces")
                            cmd_traces.append(tf)
                t1 = cmd_traces
            case "set":
                t1 = handle_set(expanded_args, t1)
            case "exit":
                t1 = handle_exit(t1)
            case "return":
                t1 = handle_return(t1)
            case "read":
                t1 = handle_read(expanded_args, t1, node)
            case "xargs":
                t1 = handle_xargs(t1, node, expanded_args, config)
            # TODO: Unify rm with other commands
            case cmd_name if spec := get_spec(cmd_name, tuple(expanded_args)):
                logging.debug("Adding %s precondition: %s", cmd_name, spec.check)
                if cmd_name == "env":
                    match spec.failure_postcond:
                        case Not(CommandExists(non_existent_cmd_field)):
                            non_existent_cmd_name = non_existent_cmd_field.try_to_str()
                            if isinstance(non_existent_cmd_name, str):
                                should_report = any(
                                    non_existent_cmd_name not in trace.latest_state.known_existing_commands
                                    for trace in t1
                                )
                                if should_report:
                                    Reporter.add_issue(reporter.NotACommand(non_existent_cmd_name, context_line), config)
                if spec.min_operands > 0:
                    trace_expansions = expand_args(t1, node.arguments, config)
                    has_sufficient_operands = False

                    def check_if_constrained_to_empty(c: Constraint):
                        """Check if a constraint constrains a field to be empty."""
                        match c:
                            case StringEq(_, rhs):
                                return rhs == Field(SymStr(("",)), WordCount(0, 0)) or (isinstance(rhs.content, SymStr) and rhs.content.parts == ("",))
                            case _:
                                return False

                    for trace, trace_expanded_args in trace_expansions:
                        if len(trace_expanded_args) > 0:
                            total_min_words = sum(f.count.min for f in trace_expanded_args[1:])
                            # Short-circuit: if the minimum number of words is already sufficient, there is no need to check further.
                            if spec.min_operands <= total_min_words:
                                has_sufficient_operands = True
                                break
                            total_max_words: int | float = 0
                            has_inf = False
                            all_definitely_empty = True
                            for f in trace_expanded_args[1:]:
                                if f.count.max == inf:
                                    has_inf = True
                                    all_definitely_empty = False
                                    break
                                if f.count.max > 0:
                                    all_definitely_empty = False
                                total_max_words += f.count.max
                            # If all operands are definitely empty, skip this trace.
                            if all_definitely_empty:
                                continue
                            if has_inf:
                                # Prevent false positives when there are constraints that force some arguments to be empty.
                                if any(check_if_constrained_to_empty(cond.constraint) for cond in trace.latest_state.pathcond):
                                    continue
                                has_sufficient_operands = True
                                break
                            # If the total maximum number of words is sufficient, then we should not report a `command_can_only_fail` issue.
                            if total_max_words >= spec.min_operands:
                                has_sufficient_operands = True
                                break
                    if not has_sufficient_operands:
                        assert isinstance(cmd_name, str), "cmd_name should be str when a spec is found"
                        Reporter.add_issue(reporter.CommandCanOnlyFail(cmd_name, context_line), config)
                t_precond = trace_map(t1, lambda s: s.add_assertion(spec.check, source_str=node.pretty(), source_line=context_line).update_known_commands(spec.check))
                if config.debug_instrumentation:
                    for trace in t1:
                        DebugLogger.log_assertion(spec.check, trace.latest_state, context_line, config.current_pass)

                def pathcond_contradicts(state: State, new_cond: Constraint) -> bool:
                    if new_cond == Empty():
                        return False
                    norm_new = new_cond.normalized().constraint
                    for cond in state.pathcond:
                        norm_existing = cond.constraint.normalized().constraint
                        if isinstance(norm_existing, Not) and norm_existing.constraint == norm_new:
                            return True
                        if isinstance(norm_new, Not) and norm_new.constraint == norm_existing:
                            return True
                    return False

                t_success_precond = [t for t in t_precond if not pathcond_contradicts(t.latest_state, spec.success_postcond)]
                t_success = trace_map(t_success_precond,
                                      lambda s: s.update_fs(spec.success_postcond)\
                                                 .add_pathcond(spec.success_postcond)\
                                                 .update_known_commands(spec.success_postcond)\
                                                 .set_last_exit_code(SymStr(("0",)),
                                                                     Confidence.DEFINITE if s.opts.is_set(SetOptions.NOFAIL) and not config.in_checked_position else Confidence.SPECULATIVE,
                                                                     spec.failure_postcond))
                t_failure = []
                if config.in_checked_position:
                    t_failure_precond = [t for t in t_precond if not pathcond_contradicts(t.latest_state, spec.failure_postcond)]
                    t_failure = trace_map(t_failure_precond,
                                          lambda s: s.update_fs(spec.failure_postcond)\
                                                     .add_pathcond(spec.failure_postcond)\
                                                     .update_known_commands(spec.failure_postcond)\
                                                     .set_last_exit_code(SymStr(("1",)),
                                                                         Confidence.SPECULATIVE,
                                                                         spec.failure_postcond))
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

def extract_literal_strings_from_arg(arg: list[AST.ArgChar]) -> str:
    """Extract all literal character strings from an argument."""
    result = []
    for char in arg:
        match char:
            case AST.CArgChar() | AST.EArgChar():
                result.append(char.pretty(AST.QUOTED))
            case AST.QArgChar():
                result.append(extract_literal_strings_from_arg(char.arg))
            case AST.VArgChar() | AST.BArgChar() | AST.AArgChar() | AST.TArgChar():
                pass
    return "".join(result)

# Case where the output string can be determined
def word_count_from_output(output: str) -> WordCount:
    if output == "":
        return WordCount(0, 0)
    words = output.split()
    return WordCount(len(words), len(words))

def command_substitution_output(cmd_name: str,
                                operands: list[Field],
                                subst_node: AST.BArgChar,
                                state: State,
                                spec: CmdSpec | None,
                                config: InterpConfig) -> tuple[Field | None, State]:

    if spec and spec.io in {IOType.NONE, IOType.STDIN}:
        Reporter.add_issue(reporter.CapturingEmptyOutput(cmd_name, context_line), config)

    match cmd_name:
        case "pwd":
            pwd_var = state.lookup("PWD")
            assert pwd_var is not None, "PWD should always be defined"
            return pwd_var.value, state
        case "echo":
            output_field = merge_partial_fields(operands, sep=" ", state=state) # TODO: sep should be from IFS
            if (output_str := output_field.try_to_str()) is not None:
                output_field = Field(SymStr((output_str,)), word_count_from_output(output_str))
            if isinstance(output_field.content, CompletelyArbitrary) and output_field.count.min == 0:
                output_field = Field(replace(output_field.content, maybe_empty=True), output_field.count)
            return output_field, state
        case "mktemp":
            output_path = arbitrary_field(subst_node, ArbitraryType.APPROXIMATION, state)
            assert spec is not None and spec.io in {IOType.STDOUT_FILE, IOType.STDOUT_DIR}, f"unexpected spec? {spec}"
            constraint = IsFile if spec.io == IOType.STDOUT_FILE else IsDir
            state_with_filetype = state.update_fs(constraint(output_path))
            return output_path, state_with_filetype

        case _:
            return None, state

def handle_rm(expanded_args: tuple[Field, ...], trace: Trace, node: AST.CommandNode, config: Config) -> tuple[Trace, Trace]:
    logging.debug("Checking rm command with expansion possibility: %s", expanded_args)
    spec = get_spec("rm", expanded_args)

    assert spec is not None, "Expected rm spec to always be found"

    logging.debug("Adding rm precondition: %s", spec.check)
    DebugLogger.log_assertion(spec.check, trace.latest_state, context_line, config.current_pass)
    trace = trace.extend(lambda s: s.add_assertion(spec.check, source_str=node.pretty(), source_line=context_line))

    # TODO: These helper functions are repeated in the expansion routine, consider refactoring them out into utils
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

    def is_definitely_non_empty(field: Field) -> bool:
        logging.debug("Checking if field %s is definitely non-empty", field)
        core = field_core_key(field)
        logging.debug("Extracted core: %s", core)
        if core is None:
            return False
        logging.debug("Checking path conditions for non-emptiness implications, have %d conditions", len(trace.latest_state.pathcond))
        return any(constraint_implies_non_empty(core, cond.constraint) for cond in trace.latest_state.pathcond)

    def is_protected(path):
        return any(path in [p, p + "/", p + "/*"] for p in Config.get("PROTECTED_PATHS"))

    for arg_idx, arg_field in enumerate(expanded_args[1:], start=1):
        definitely_non_empty = is_definitely_non_empty(arg_field)
        if (path := arg_field.try_to_str()) and is_protected(path):
            Reporter.add_issue(reporter.DeleteSystemFile(path, context_line), config)

        pwdval = trace.latest_state.lookup("PWD")
        assert pwdval is not None, "PWD should always be defined"
        trace = trace.extend(lambda s: s.add_assertion(Not(StringEq(arg_field, pwdval.value)),
                                                       node.pretty(),
                                                       context_line))

        def maybe_report_protected_split(content: CompletelyArbitrary, max_words: int | float) -> None:
            if content.maybe_empty and content.quoted and not definitely_non_empty:
                # "<pre>$VAR<post>" but $VAR could be empty
                exp = ""
                if content.prefix is not None and (pre := content.prefix.try_to_str()):
                    exp += pre
                if content.suffix is not None and (suf := content.suffix.try_to_str()):
                    exp += suf
                if is_protected(exp):
                    Reporter.add_issue(reporter.WordSplitCouldDeleteSystemFile(exp, context_line), config)

            if max_words > 1 and not content.quoted:
                if content.prefix is not None and (pre := content.prefix.try_to_str()) and is_protected(pre):
                    Reporter.add_issue(reporter.WordSplitCouldDeleteSystemFile(pre, context_line), config)
                if content.suffix is not None and (suf := content.suffix.try_to_str()) and is_protected(suf):
                    Reporter.add_issue(reporter.WordSplitCouldDeleteSystemFile(suf, context_line), config)

            # Handle cases where multiple arbitraries are merged into one and the literals between them get "lost" during the merging (e.g. $a/$b when a and b are empty)
            # This overapproximates things because it does not consider that one of the arbitraries could be for-sure not empty
            # Also, to avoid making this even on non-merged arbitraries, we first check that the arbitrary has more than one sources
            if isinstance(content.source, tuple) and len(content.source) > 1 and arg_idx < len(node.arguments):
                literal_path = extract_literal_strings_from_arg(node.arguments[arg_idx])
                if literal_path and is_protected(literal_path):
                    Reporter.add_issue(reporter.WordSplitCouldDeleteSystemFile(literal_path, context_line), config)

        match arg_field:
            case Field(CompletelyArbitrary() as content, WordCount(_, max_words)):
                if not content.quoted and max_words > 1:
                    Reporter.add_issue(reporter.DangerousWordSplit(content.source, context_line), config)
                maybe_report_protected_split(content, max_words)

    return (
        trace.extend(lambda s: s.update_fs(spec.success_postcond)\
                                .add_pathcond(spec.success_postcond)\
                                .set_last_exit_code(SymStr(("0",)), Confidence.SPECULATIVE, spec.failure_postcond)),
        trace.extend(lambda s: s.update_fs(spec.failure_postcond)\
                                .add_pathcond(spec.failure_postcond)\
                                .set_last_exit_code(SymStr(("1",)), Confidence.SPECULATIVE, spec.failure_postcond))
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
            if config.ignore_function_calls or func_name in config.ignore_function_calls_for:
                logging.debug("Ignoring function call to %s (configured as no-op)", func_name)
                return traces
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
        Reporter.add_issue(reporter.UndefinedFunction(name, context_line), config)

    if name.endswith("/") or any(name in t.latest_state.known_nonexistent_commands for t in traces):
        Reporter.add_issue(reporter.NotACommand(name, context_line), config)

    logging.debug("Unknown command %s, optimistically treating as no-op", name) # that reads its operands", name)
    # mark all args as being read
    #t = trace_map(traces, lambda s: s.update_fs(And.from_field_iter(arg_fields, lambda f: IsFile(f) >> IsRead(f))))
    #return t
    # this makes execution significantly slower, so for now leave it commented out
    if config.in_checked_position:
        t_success = trace_map(traces, lambda s: s.set_last_exit_code(SymStr(("0",)), Confidence.SPECULATIVE))
        t_failure = trace_map(traces, lambda s: s.set_last_exit_code(SymStr(("1",)), Confidence.SPECULATIVE))
        return t_success + t_failure
    return traces

def handle_function_call(name: str,
                         func_node: AST.DefunNode,
                         arg_fields: list[Field],
                         traces: Traces,
                         config: InterpConfig) -> Traces:
    if config.ignore_function_calls or name in config.ignore_function_calls_for:
        logging.debug("Ignoring function call to %s (configured as no-op)", name)
        return traces
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

def record_assignment(trace: Trace, var: str, rhs: Field, definite_confidence: bool = True) -> Trace:
    conf = Confidence.DEFINITE if definite_confidence else Confidence.SPECULATIVE
    return trace.extend(lambda s: s.set_env(var, ShellVar(rhs)).set_last_exit_code(SymStr(("0",)), conf))

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
    if config.branch_policy_pre is not None:
        decision = config.branch_policy_pre(node)
        t_true = [t for t in t1 if t.latest_state.last_exit_code[0] == SymStr(("0",))]
        t_false = [t for t in t1 if t.latest_state.last_exit_code[0] == SymStr(("1",))]
        t_other = [t for t in t1 if t.latest_state.last_exit_code[0] not in {SymStr(("0",)), SymStr(("1",))}]
        t_true = t_true + trace_map(t_other, lambda s: s.set_last_exit_code(SymStr(("0",)), Confidence.SPECULATIVE))
        t_false = t_false + trace_map(t_other, lambda s: s.set_last_exit_code(SymStr(("1",)), Confidence.SPECULATIVE))
        if decision == BranchDecision.FIRST:
            logging.debug("While loop single-path decision: take body once")
            return guarded_interp_node(t_true, node.body, config)
        logging.debug("While loop single-path decision: skip body")
        return t_false
    # Special case: never runs
    if len(test_cmds) > 0 and interpret_test(test_cmds[0]) == False:
        logging.debug("While loop never runs")
        return t1

    t1 = [t for t in t1 if t.latest_state.last_exit_code != (SymStr(("1",)), Confidence.DEFINITE)]
    t_skip_body = [t for t in traces if t.latest_state.last_exit_code == (SymStr(("1",)), Confidence.DEFINITE)]
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
        Reporter.add_issue(reporter.InfiniteLoop(node, context_line), config)
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
        Reporter.add_issue(reporter.InfiniteLoop(node, context_line), config)

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
    test_line_number = context_line
    test_cmds = []
    def get_the_test(cmd_fields):
        nonlocal test_line_number
        test_line_number = context_line
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
        Reporter.add_issue(reporter.ConstantCondition(test_cmds, test_line_number), config)
        if test_result == True and (node.else_b is not None and node.else_b.pretty()):
                                                             # Hack because libdash sometimes gives empty else bodies
            t1 = trace_map(t1, lambda s: s.set_last_exit_code(SymStr(("0",)), Confidence.DEFINITE))
            logging.debug("Reporting dead code in else branch.")
            Reporter.add_issue(reporter.DeadCode(node.else_b, test_line_number), config)
        elif test_result == False:
            t1 = trace_map(t1, lambda s: s.set_last_exit_code(SymStr(("1",)), Confidence.DEFINITE))
            logging.debug("Reporting dead code in then branch")
            Reporter.add_issue(reporter.DeadCode(node.then_b, test_line_number), config)
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
        if config.branch_policy_pre is not None:
            decision = config.branch_policy_pre(node)
            if decision == BranchDecision.FIRST:
                return guarded_interp_node(t1, node.then_b, config)
            if decision == BranchDecision.SECOND:
                if node.else_b is not None:
                    return guarded_interp_node(t1, node.else_b, config)
                return t1
        return handle_branch(t1,
                            lambda ts: guarded_interp_node(ts, node.then_b, config),
                            lambda fs: guarded_interp_node(fs, node.else_b, config) if node.else_b is not None else fs,
                            node,
                            config)

def handle_exit(traces: Traces) -> Traces:
    logging.debug("Handling exit command, terminating %d traces", len(traces))
    return trace_map(traces, lambda s: s.terminate())

def handle_return(traces: Traces) -> Traces:
    logging.debug("Handling return command, terminating %d traces", len(traces))
    return trace_map(traces, lambda s: s.terminate())

def handle_branch(traces: Traces, success_cb: Callable[[Traces], Traces], failure_cb: Callable[[Traces], Traces], node: AST.AstNode, config: InterpConfig) -> Traces:
    t_success = [t for t in traces if t.latest_state.last_exit_code[0] == SymStr(("0",))]
    t_failure = [t for t in traces if t.latest_state.last_exit_code[0] == SymStr(("1",))]
    t_other   = [t for t in traces if t.latest_state.last_exit_code[0] not in {SymStr(("0",)), SymStr(("1",))}]
    t_then = success_cb(t_success + trace_map(t_other, lambda s: s.set_last_exit_code(SymStr(("0",)), Confidence.SPECULATIVE)))
    t_else = failure_cb(t_failure + trace_map(t_other, lambda s: s.set_last_exit_code(SymStr(("1",)), Confidence.SPECULATIVE)))
    t_then_bp, t_else_bp = config.branch_policy(node, t_then, t_else)
    res = t_then_bp + t_else_bp
    if all(t.latest_state.terminated for t in res):
        logging.debug("All traces terminated with branch policy decision; ignoring policy for this branch (line %d)", context_line)
        return t_then + t_else
    else:
        return res

def handle_read(expanded_args: list[Field], traces: Traces, node: AST.AstNode) -> Traces:
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
        return [t.extend(lambda s: s.set_last_exit_code(SymStr(("0",)), Confidence.SPECULATIVE)) for t in traces]
    new_traces: Traces = []
    for trace in traces:
        curr_trace = trace
        # For each variable to be read into, record an assignment of that variable to the corresponding field.
        for var_name, value_field in collected:
            # TODO: Don't pass in the entire node, but the specific arg corresponding to this variable.
            curr_trace = record_assignment(curr_trace, var_name, arbitrary_field(node, ArbitraryType.ENVIRONMENT, curr_trace.latest_state))
        new_traces.append(curr_trace)
    return new_traces

def handle_xargs(traces: Traces, node: AST.CommandNode, expanded_args: list[Field], config: InterpConfig) -> Traces:
    match expanded_args:
        case [Field(SymStr(("xargs",)), _),
              Field(SymStr(("-I",)), _),
              Field(SymStr((thename,)), _),
              *the_cmd]:
            # beware major trickery here (sound but not clean):
            # we unroll the xargs into two invocations of the command, each time replacing
            # occurrences of thename with a command substitution that yields a fresh arbitrary each time
            # to capture the fact that each invocation may get different inputs
            the_name_unexpanded = freeze_thing(node.arguments[2])
            mangled_cmdnode = deepcopy(node)
            mangled_cmdnode.arguments = mangled_cmdnode.arguments[3:]
            # Replace all occurrences of thename in the command with a command substitution that leads to a fresh arbitrary each time
            def replace_arg(arg: list[AST.ArgChar]) -> list[AST.ArgChar]:
                if freeze_thing(arg) == the_name_unexpanded:
                    return [AST.BArgChar(AST.CommandNode(node.line_number,
                                                         [],
                                                         [],
                                                         []))]
                else:
                    return arg
            mangled_cmdnode.arguments = [replace_arg(arg) for arg in mangled_cmdnode.arguments]
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

    def field_core_key(field: Field) -> CompletelyArbitrary | None:
        match field.content:
            case CompletelyArbitrary() as content:
                return replace(content, prefix=None, suffix=None, quoted=False, maybe_empty=False)
            case _:
                return None

    def argchars_var_name(argchars: list[AST.ArgChar]) -> str | None:
        if len(argchars) != 1:
            return None
        match argchars[0]:
            case AST.VArgChar() as var:
                return var.var
            case AST.QArgChar() as q:
                return argchars_var_name(q.arg)
            case _:
                return None

    def source_var_name(source) -> str | None:
        match source:
            case FrozenAst(ast=ast):
                match ast:
                    case AST.VArgChar() as var:
                        return var.var
                    case AST.QArgChar() as q:
                        return argchars_var_name(q.arg)
                    case _:
                        return None
            case tuple() | list():
                if len(source) == 1:
                    return source_var_name(source[0])
                return None
            case _:
                return None

    def core_matches_field(core: CompletelyArbitrary, field: Field) -> bool:
        other_core = field_core_key(field)
        if other_core is None:
            return False
        if core == other_core:
            return True
        core_var = source_var_name(core.source)
        other_var = source_var_name(other_core.source)
        return core_var is not None and core_var == other_var

    def is_empty_constant(field: Field) -> bool:
        return field.try_to_str() == ""

    def is_non_empty_constant(field: Field) -> bool:
        field_str = field.try_to_str()
        return field_str is not None and field_str != ""

    def constraint_implies_non_empty(core: CompletelyArbitrary, constraint: Constraint) -> bool:
        norm = constraint.normalized().constraint
        match norm:
            case StringEq(lhs, rhs):
                if core_matches_field(core, lhs) and is_non_empty_constant(rhs):
                    return True
                if core_matches_field(core, rhs) and is_non_empty_constant(lhs):
                    return True
                return False
            case Not(StringEq(lhs, rhs)):
                if core_matches_field(core, lhs) and is_empty_constant(rhs):
                    return True
                if core_matches_field(core, rhs) and is_empty_constant(lhs):
                    return True
                return False
            case And(lhs, rhs):
                return constraint_implies_non_empty(core, lhs) or constraint_implies_non_empty(core, rhs)
            case Or(lhs, rhs):
                return constraint_implies_non_empty(core, lhs) and constraint_implies_non_empty(core, rhs)
            case _:
                return False

    def constraint_implies_empty(core: CompletelyArbitrary, constraint: Constraint) -> bool:
        norm = constraint.normalized().constraint
        match norm:
            case StringEq(lhs, rhs):
                if core_matches_field(core, lhs) and is_empty_constant(rhs):
                    return True
                if core_matches_field(core, rhs) and is_empty_constant(lhs):
                    return True
                return False
            case And(lhs, rhs):
                return constraint_implies_empty(core, lhs) or constraint_implies_empty(core, rhs)
            case Or(lhs, rhs):
                return constraint_implies_empty(core, lhs) and constraint_implies_empty(core, rhs)
            case _:
                return False

    def field_is_definitely_empty(field: Field) -> bool:
        if field.count.max == 0 or is_empty_constant(field):
            return True
        core = field_core_key(field)
        if core is None:
            return False
        return any(constraint_implies_empty(core, cond.constraint) for cond in state.pathcond)

    def field_is_definitely_non_empty(field: Field) -> bool:
        if field.count.min >= 1:
            return True
        core = field_core_key(field)
        if core is None:
            return False
        return any(constraint_implies_non_empty(core, cond.constraint) for cond in state.pathcond)

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
            #assert default_state == who.state, "default value expansion should not change state"
            who.state = default_state # if the default value contains previously unknown variables, the state gets updated
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
                case AST.TArgChar() as t:
                    # Tilde expansion: only plain "~" expands to $HOME.
                    if not self.quoted and getattr(t, "string", None) in (None, "None"):
                        home_var = self.state.lookup("HOME")
                        if home_var is not None:
                            self.add_a_field(home_var.value)
                        else:
                            self.add_a_field(arbitrary_field(t, ArbitraryType.ENVIRONMENT, self.state))
                    else:
                        logging.debug("Expansion: treating tilde '%s' as completely arbitrary", t.pretty())
                        self.add_a_field(arbitrary_field(t, ArbitraryType.APPROXIMATION, self.state))
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
                    def expand_default_value(partial: 'Partial') -> tuple[list[Field], State]:
                        default_expansions = expand_inner(var.arg, Partial(False, partial.state))
                        assert len(default_expansions) == 1, "default value expansion forking not implemented"
                        default_fields, default_state = default_expansions[0].finish()
                        return default_fields, default_state

                    def assign_default_value(partial: 'Partial', default_fields: list[Field], default_state: State) -> None:
                        partial.state = default_state.set_env(var.var, ShellVar(join_fields(default_fields)))
                        for default_field in default_fields:
                            partial.add_a_field(default_field)

                    if var.var == "?":
                        if self.state.last_exit_code[1] == Confidence.DEFINITE:
                            logging.debug("expansion: treating special var $? as constant due to definite confidence")
                            self.add_a_field(Field(self.state.last_exit_code[0], WordCount(1, 1)))
                        else:
                            self.add_a_field(Field(CompletelyArbitrary(freeze(var),
                                                                       ArbitraryType.APPROXIMATION,
                                                                       self.state),
                                             WordCount(1, 1)))
                    elif (v := self.state.lookup(var.var)):
                        if var.fmt == "Normal" \
                            or (var.fmt == "Minus" and not var.null and not v.ghost) \
                            or (var.fmt == "Question" and not var.null and not v.ghost):
                            # explanation of the minus case: the POSIX spec says that for
                            # ${VAR-default} the result is the value of $VAR as long as $VAR is set -- whether it's empty ("null") or not
                            # ^^ this corresponds to the second part of the condition above (var.null false means no `:`)
                            # same explanation for the question case (which corresponds to ${VAR?errmessage})
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
                        elif var.fmt in {"Length", "TrimR", "TrimRMax", "TrimL", "TrimLMax"}:
                            definitely_empty = field_is_definitely_empty(v.value)
                            definitely_non_empty = field_is_definitely_non_empty(v.value)
                            if definitely_empty:
                                # All of these manipulations have known results on the empty string
                                logging.info("Special casing string manipulation expansion on empty string")
                                match var.fmt:
                                    case "Length":
                                        self.add_a_field(Field(SymStr(("0",)), WordCount(1, 1)))
                                    case "TrimR" | "TrimRMax" | "TrimL" | "TrimLMax":
                                        self.add_a_field(Field(SymStr(("",)), WordCount(0, 0)))
                            else:
                                match var.fmt:
                                    case "Length":
                                        if (value_str := v.value.try_to_str()) is not None:
                                            self.add_a_field(Field(SymStr((str(len(value_str)),)), WordCount(1, 1)))
                                        else:
                                            self.add_a_field(arbitrary_field(var,
                                                                             ArbitraryType.APPROXIMATION,
                                                                             self.state,
                                                                             min_words=1))
                                    case "TrimR" | "TrimRMax" | "TrimL" | "TrimLMax":
                                        min_words = 1 if definitely_non_empty else 0
                                        self.add_a_field(arbitrary_field(var,
                                                                         ArbitraryType.APPROXIMATION,
                                                                         self.state,
                                                                         min_words=min_words))
                        elif var.fmt == "Assign":
                            # This is the case of `${VAR:=word}` with VAR set.
                            if not var.null:
                                self.add_a_field(v.value)
                            else:
                                match v.value:
                                    case Field(_, WordCount(0, 0)):
                                        logging.debug("expansion: ${%s:=word} with VAR empty, assigning default", var.var)
                                        default_fields, default_state = expand_default_value(self)
                                        assign_default_value(self, default_fields, default_state)
                                    case Field(SymStr(stuff), _) if all(isinstance(thing, str) for thing in stuff):
                                        logging.debug("expansion: ${%s:=word} with VAR non-empty, using current value", var.var)
                                        self.add_a_field(v.value)
                                    case Field(content, WordCount(min_words, max_words)):
                                        logging.debug("expansion: forking on ${%s:=word} with potentially empty VAR", var.var)
                                        empty_case, non_empty = self.fork(Description(f"{var.var} is non-empty for := expansion"))
                                        default_fields, default_state = expand_default_value(empty_case)
                                        assign_default_value(empty_case, default_fields, default_state)
                                        non_empty.add_a_field(Field(content, WordCount(max(min_words, 1), max_words)))
                                        return [non_empty, empty_case]
                                    case _:
                                        self.add_a_field(v.value)
                        elif var.fmt == "Question" and (var.null or v.ghost):
                            # This is the case of ${VAR:?errmessage}
                            match v.value:
                                case Field(_, WordCount(0, 0)):
                                    # If $VAR is empty, the script would exit here
                                    logging.debug("expansion: terminating due to ${%s:?} with definitely empty value", var.var)
                                    # terminate trace; script would exit here
                                    self.state = self.state.terminate()
                                    return [self]
                                case Field(SymStr(stuff), _) if all(isinstance(thing, str) for thing in stuff):
                                    # If $VAR cannot be empty, just use its value
                                    logging.debug("expansion: treating ${%s:?} with definitely non-empty value as normal", var.var)
                                    self.add_a_field(v.value)
                                case something_not_constant:
                                    # If $VAR might be empty, force min>=1 to continue.
                                    match v.value:
                                        case Field(content, WordCount(min_words, max_words)):
                                            logging.debug("expansion: treating ${%s:?} as non-empty to continue", var.var)
                                            self.add_a_field(Field(content,
                                                                   WordCount(max(min_words, 1), max_words)))
                                        case _:
                                            self.add_a_field(Field(CompletelyArbitrary(freeze_thing(var),
                                                                                      ArbitraryType.APPROXIMATION,
                                                                                      self.state),
                                                                   WordCount(1, inf)))
                        elif var.fmt == "Plus" and not var.null:
                            # This is the case of `${VAR+word}`, where `VAR` is set: just expand to `word`.
                            logging.debug("Expansion: '${%s+word}' with VAR set (non-colon form), expanding to word", var.var)
                            Partial.add_the_default(self, var)
                        elif var.fmt == "Plus" and var.null:
                            # This is the case of `${VAR:+word}`, where `VAR` is set, we need to check whether it's empty or not and expand accordingly.
                            match v.value:
                                case Field(_, WordCount(0, 0)):
                                    logging.debug("Expansion: '${%s:+word}' with VAR empty, returning empty", var.var)
                                    self.add_a_field(Field(SymStr(("",)), WordCount(0, 0)))
                                case Field(SymStr(stuff), _) if all(isinstance(thing, str) for thing in stuff):
                                    logging.debug("Expansion: '${%s:+word}' with VAR non-empty, expanding to word", var.var)
                                    Partial.add_the_default(self, var)
                                case _:
                                    logging.debug("Expansion: forking on '${%s:+word}' with potentially empty VAR", var.var)
                                    empty_case, word_case = self.fork(Description(f"{var.var} is non-empty for :+ expansion"))
                                    empty_case.add_a_field(Field(SymStr(("",)), WordCount(0, 0)))
                                    Partial.add_the_default(word_case, var)
                                    return [empty_case, word_case]
                        else:
                            logging.info("Expansion: treating var '%s' with unhandled fmt '%s' as completely arbitrary", var.pretty(), var.fmt)
                            self.add_a_field(arbitrary_field(var, ArbitraryType.APPROXIMATION, self.state))
                    elif var.fmt == "Minus":
                        # This is the case that $VAR is unset: take the default
                        if config.unbound_policy == UnboundVariablePolicy.EMPTY:
                            logging.info("Expansion: treating unset var '%s' as empty string due to config; taking the default ('%s') unconditionally",
                                         var.pretty(), util.shasta_pretty(var.arg))
                            Partial.add_the_default(self, var)
                        else:
                            logging.debug("Expansion: forking on unset var '%s' to take default ('%s') or arbitrary",
                                          var.var, util.shasta_pretty(var.arg))
                            non_default, default = self.fork(Description(f"{var.var} takes the default value {Field.create_constant(util.shasta_pretty(var.arg))}"))
                            Partial.add_the_default(default, var)
                            arbitrary_for_this_var = arbitrary_field(var, ArbitraryType.ENVIRONMENT, non_default.state)
                            # localenv to avoid creating an arbitrary that persists beyond a function body
                            non_default.state = non_default.state.extend_localenv({var.var: ShellVar(arbitrary_for_this_var, ghost=True)})
                            non_default.add_a_field(arbitrary_for_this_var)
                            return [non_default, default]
                    elif var.fmt == "Question":
                        # This is the case that $VAR is unset
                        if config.unbound_policy == UnboundVariablePolicy.EMPTY:
                            # In the EMPTY pass, treat unset as definitely empty, so ${:?} terminates.
                            logging.debug("Expansion: terminating due to unset var '%s' with '${:?}' (EMPTY policy)", var.var)
                            self.state = self.state.terminate()
                            return [self]
                        else:
                            # In the SYMBOLIC pass, assume it might be set and non-empty; do not terminate.
                            logging.debug("Expansion: treating unset var '%s' with '${:?}' as non-empty to continue", var.var)
                            self.add_a_field(Field(CompletelyArbitrary(freeze_thing(var),
                                                                      ArbitraryType.ENVIRONMENT,
                                                                      self.state),
                                                   WordCount(1, inf)))
                    elif var.fmt == "Plus":
                        # This is the case where $VAR is unset.
                        logging.info("Expansion: treating unset var '%s' with '${%s+...}' as an empty string", var.pretty(), var.fmt)
                        self.add_a_field(Field(SymStr(("",)), WordCount(0, 0)))
                    elif var.fmt == "Assign":
                        # This is the case where $VAR is unset and ${VAR:=word} assigns the default.
                        logging.debug("Expansion: '${%s:=word}' with VAR unset; assigning default", var.var)
                        default_fields, default_state = expand_default_value(self)
                        assign_default_value(self, default_fields, default_state)
                    else:
                        # todo we should report path information
                        if not is_special_var(var.var):
                            error_code = reporter.UnboundIDSetU if self.state.opts.is_set(SetOptions.NOUNSET) else reporter.UnboundID
                            Reporter.add_issue(error_code(var.pretty(), context_line), config)
                        if config.unbound_policy == UnboundVariablePolicy.EMPTY:
                            logging.info("Expansion: treating unbound var '%s' as empty string due to config", var.pretty())
                            empty_str_field = Field(SymStr(("",)), WordCount(0, 0))
                            self.add_a_field(empty_str_field)
                            self.state = self.state.extend_localenv({var.var: ShellVar(empty_str_field, ghost=True)})
                        else:
                            arbitrary_for_this_var = arbitrary_field(var,
                                                                    ArbitraryType.APPROXIMATION if is_special_var(var.var) else ArbitraryType.ENVIRONMENT,
                                                                    self.state)
                            # localenv to avoid creating an arbitrary that persists beyond a function body
                            self.state = self.state.extend_localenv({var.var: ShellVar(arbitrary_for_this_var, ghost=True)})
                            self.add_a_field(arbitrary_for_this_var)
                case AST.BArgChar() as b:
                    # TODO use the trace: this case suggests we should really generalize the interface of `expand_simple` to be from one trace to many, instead of one state to many
                    inner_cmds = []
                    temp_config = config.add_expanded_command_callback(lambda expanded: inner_cmds.append(expanded))
                    t = guarded_interp_node([Trace((self.state,))], b.node, temp_config)
                    output_field = None
                    if len(inner_cmds) != 0 and isinstance(b.node, AST.CommandNode):
                        expanded_args = inner_cmds[-1]
                        if expanded_args and (cmd_name := expanded_args[0].try_to_str()):
                            spec = get_spec(cmd_name, tuple(expanded_args))
                            output_field, new_state = command_substitution_output(cmd_name, expanded_args[1:], b, self.state, spec, config)
                            self.state = new_state
                    # We found one of our special commands with known output
                    if output_field is not None:
                        logging.info(f"expansion: determined commandsubst output as: {output_field}")
                        self.add_a_field(output_field)
                    # Everything else is completely arbitrary
                    else:
                        logging.info("expansion: treating backquote argchar %s as completely arbitrary field", b.pretty())
                        self.add_a_field(arbitrary_field(b, ArbitraryType.APPROXIMATION, self.state))
                case _:
                    logging.error("Unsupported argchar of type '%s': '%s'; treating as completely arbitrary", argchar.NodeName, argchar.pretty())
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
    terminated_traces: Traces = []
    for arg in args:
        expansions = expand(res_traces, arg, config)
        res_traces = [expansion[0] for expansion in expansions]
        active_expansions = [expansion for expansion in expansions if not expansion[0].latest_state.terminated]
        terminated_traces.extend([expansion[0] for expansion in expansions if expansion[0].latest_state.terminated])
        if not active_expansions:
            logging.debug(f"Stopping expansion of entire args because all traces terminated on {arg}")
            return terminated_traces, []
        expanded_fields = [expansion[1] for expansion in active_expansions]
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
    return res_traces + terminated_traces, expanded_args

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

def arbitrary_field(ast: AST.AstNode, kind: ArbitraryType, producing_state: State | None, min_words = 0) -> Field:
    return Field(CompletelyArbitrary(freeze_thing(ast), kind, producing_state),
                 WordCount(min_words, inf))

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
        quoted = all(a.content.quoted for a in arbitraries)
        arbitrary = Field(CompletelyArbitrary(freeze_thing([a.content.source for a in arbitraries]), # type: ignore
                                              ArbitraryType.APPROXIMATION,
                                              state,
                                              quoted=quoted),
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
inactive_trace_stash: list[Trace] = []

trace_count = 1
def collapse_traces_if_too_many(traces: Traces) -> tuple[Traces, Traces]:
    global trace_count
    new_inactive = []
    if len(traces) > trace_count:
        logging.debug("Too many traces (%d), collapsing", len(traces))
        traces, new_inactive = collapse_traces(traces)
        trace_count = len(traces)
        logging.debug("Collapsed to %d traces", trace_count)
    return traces, new_inactive

def drop_terminated_traces(traces: Traces) -> tuple[Traces, Traces]:
    inactive_traces, active_traces = util.partition(traces, lambda t: t.latest_state.terminated)
    if len(inactive_traces) > 0:
        logging.debug("Dropping %d terminated traces", len(inactive_traces))
    return active_traces, inactive_traces

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

    traces, inactive1 = drop_terminated_traces(traces)
    traces, inactive2 = config.trace_collapser(traces)
    inactive_trace_stash.extend(inactive1 + inactive2)
    traces = config.apply_node_cbs(traces, node)

    try:
        res = interp_node(traces, node, config)
        context_line = prev_context_line
        return res
    except NotImplementedError as e:
        logging.error("Interp raised: '%s'; ignoring.", e)
        context_line = prev_context_line
        return traces

def interp_node(traces: Traces,
                node: AST.AstNode,
                config: InterpConfig) -> Traces:
    # refer to https://github.com/binpash/shasta/blob/main/shasta/ast_node.py
    if not traces:
        if isinstance(node, AST.CommandNode) and not node.arguments and not node.assignments:
            logging.debug("Skipping dead code warning for empty command node")
            return traces
        logging.debug("No active traces when interpreting %s, reporting dead code and returning early", trim_string_for_logging(node.pretty()))
        Reporter.add_issue(reporter.DeadCode(node, context_line), config)
        return traces

    logging.debug("Interpreting line %d %s with %d traces",
                  context_line, trim_string_for_logging(node.pretty()), len(traces))
    DebugLogger.log_interp_line(context_line, traces, config.current_pass)

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

            # If the assignment contains a command substitution do not set exit code to 0 with definite confidence
            assignment_definitely_succeeds = not any(isinstance(ac, AST.BArgChar) for ac in util.iter_argchar_list(node.val, [AST.AArgChar]))
            return [record_assignment(t, node.var, join_fields(rhs), assignment_definitely_succeeds) for (t, rhs) in trace_expansion_pairs]


        case AST.SemiNode():
            t2 = guarded_interp_node(traces, node.left_operand, config)
            return guarded_interp_node(t2, node.right_operand, config)


        case AST.WhileNode():
            return handle_while(traces, node, config)

        case AST.ForNode():
            t0, var_name = expand_assuming_single_constant_word(traces, node.variable, config)
            t1, items = expand_args_dumb(t0, node.argument, config)
            if join_fields(items).count.max <= 1:
                Reporter.add_issue(reporter.LoopRunsOnce(node, context_line), config)
            # if all items are constant, we can unroll the loop
            if all(field.is_constant() for field in items):
                logging.debug("For loop over constant items, unrolling: %s", items)
                t2 = t1
                for item_field in items:
                    t2 = [record_assignment(t, var_name, item_field) for t in t2]
                    t2 = guarded_interp_node(t2, node.body, config)
                return t2
            else:
                t_current = t1
                for i in range(config.max_loop_unroll):
                    logging.debug("For loop unrolling iteration %d/%d", i+1, config.max_loop_unroll)
                    t2 = [record_assignment(t, var_name, arbitrary_field(node.variable,
                                                                        ArbitraryType.APPROXIMATION,
                                                                        t.latest_state)) \
                        for t in t_current]
                    t_current = guarded_interp_node(t2, node.body, config)
                return t_current

        case AST.FileRedirNode():
            res = []
            for t, redir_args in expand(traces, node.arg, config):
                t_precond = t
                if node.redir_type in ["To", "Clobber"]: # >, >|
                    safe_paths = Config.get("SAFE_OVERWRITE_PATHS")
                    def not_safe_path(op: Field) -> Constraint:
                        return And.from_iter(Not(StringEq(op, Field.create_constant(p, 1))) for p in safe_paths)

                    assertion_constraint = And.from_field_iter(redir_args, lambda op: Implies(not_safe_path(op), IsRead(op) | IsDeleted(op)))
                    t_precond = t.extend(t.latest_state.add_assertion(
                        assertion_constraint,
                        source_str=node.pretty(),
                        source_line=context_line
                    ))
                    DebugLogger.log_assertion(assertion_constraint, t.latest_state, context_line, config.current_pass)
                    t_postcond = t_precond.extend(t_precond.latest_state.update_fs(And.from_field_iter(redir_args, IsFile)))

                elif node.redir_type == "Append": # >>
                    # NOTE: asserting IsFile also implicitly asserts that the file is *unread*
                    assertion_constraint = And.from_field_iter(redir_args, lambda op: ~IsDir(op))
                    t_precond = t.extend(t.latest_state.add_assertion(assertion_constraint, source_str=node.pretty(), source_line=context_line))
                    DebugLogger.log_assertion(assertion_constraint, t.latest_state, context_line, config.current_pass)
                    t_postcond = t_precond.extend(t_precond.latest_state.update_fs(And.from_field_iter(redir_args, IsFile)))

                elif node.redir_type == "From": # <
                    # The targets of the redirection were read from
                    assertion_constraint = And.from_field_iter(redir_args, IsFile)
                    t_precond = t.extend(t.latest_state.add_assertion(assertion_constraint, source_str=node.pretty(), source_line=context_line))
                    DebugLogger.log_assertion(assertion_constraint, t.latest_state, context_line, config.current_pass)
                    t_postcond = t_precond.extend(t_precond.latest_state.update_fs(And.from_field_iter(redir_args, IsRead)))

                elif node.redir_type == "FromTo":
                    # Conservatively assume the file is opened for reading
                    assertion_constraint = And.from_field_iter(redir_args, lambda op: ~IsDir(op))
                    t_precond = t.extend(t.latest_state.add_assertion(assertion_constraint, source_str=node.pretty(), source_line=context_line))
                    DebugLogger.log_assertion(assertion_constraint, t.latest_state, context_line, config.current_pass)
                    t_postcond = t_precond.extend(t_precond.latest_state.update_fs(And.from_field_iter(redir_args, IsRead)))

                else:
                    assert False, f"Unexpected redirection type: {node.redir_type}"

                res.append(t_postcond)

                match redir_args:
                    case [Field(SymStr([something]), WordCount(1, 1))]:
                        if isinstance(something, str) and something in t.latest_state.fundefs:
                            # TODO: Associate the warning with the trace that caused it
                            Reporter.add_issue(reporter.RedirectToFunction(something, context_line), config)
                    case [Field(CompletelyArbitrary(), _)]:
                        pass
                    case _:
                        logging.warning("Found a redir to multiple words: %s - Ignoring.", trim_string_for_logging(str(redir_args)))
                        pass
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
            # Workaround the `checked_position` by manually adding the failure traces back only when
            # the left operand was *not* evaluated in a checked position.
            left_config = config if config.in_checked_position else replace(config, in_checked_position=False)
            right_config = config
            t1 = guarded_interp_node(traces, node.left_operand, left_config)
            t_failure: Traces = []
            if not left_config.in_checked_position:
                t_failure = [t.fail_last_command() for t in t1 if t.latest_state.last_exit_code[0] == SymStr(("0",))]
            if config.branch_policy_pre is not None:
                decision = config.branch_policy_pre(node)
                t_success = [t for t in t1 + t_failure if t.latest_state.last_exit_code[0] == SymStr(("0",))]
                t_failure_only = [t for t in t1 + t_failure if t.latest_state.last_exit_code[0] == SymStr(("1",))]
                t_other = [t for t in t1 + t_failure if t.latest_state.last_exit_code[0] not in {SymStr(("0",)), SymStr(("1",))}]
                t_success = t_success + trace_map(t_other, lambda s: s.set_last_exit_code(SymStr(("0",)), Confidence.SPECULATIVE))
                t_failure_only = t_failure_only + trace_map(t_other, lambda s: s.set_last_exit_code(SymStr(("1",)), Confidence.SPECULATIVE))
                if isinstance(node, AST.AndNode):
                    if decision == BranchDecision.FIRST:
                        return guarded_interp_node(t_success, node.right_operand, config)
                    return t_failure_only
                if decision == BranchDecision.FIRST:
                    return guarded_interp_node(t_failure_only, node.right_operand, config)
                return t_success
            def success(traces_with_exit_0: Traces) -> Traces:
                if isinstance(node, AST.AndNode):
                    return guarded_interp_node(traces_with_exit_0, node.right_operand, right_config)
                else:
                    return traces_with_exit_0
            def failure(traces_with_exit_1: Traces) -> Traces:
                logging.debug("Inside the failure case of %s node with %d traces",
                              'AND' if isinstance(node, AST.AndNode) else 'OR',
                              len(traces_with_exit_1))
                if isinstance(node, AST.AndNode):
                    return traces_with_exit_1
                else:
                    return guarded_interp_node(traces_with_exit_1, node.right_operand, right_config)
            return handle_branch(t1 + t_failure, success, failure, node, config)

        case AST.NotNode():
            t1 = guarded_interp_node(traces, node.body, config)
            t2 = trace_map(t1,
                          lambda s: s if s.last_exit_code[0] not in {SymStr(("0",)), SymStr(("1",))}
                                      else s.set_last_exit_code(SymStr(("1",)) if s.last_exit_code[0] == SymStr(("0",)) else SymStr(("0",)),
                                                                s.last_exit_code[1]))
            return t2

        case AST.PipeNode():
            # Since variable assignments from parameter expansion, such as `${var:=default}`, in pipeline commands
            # should not persist beyond the pipeline, save the environment before the pipeline and restore it after.
            saved_envs = [(t.latest_state.env, t.latest_state.localenv) for t in traces]
            t = traces
            # Sequentially interpret each command in the pipeline, and return the aggregated traces.
            for cmd in node.items:
                t = guarded_interp_node(t, cmd, config)
            # Since traces can fork and merge, we need to match traces back to their original saved environments.
            # Thus, restore the environment of each trace to the environment of the first trace that matches its current state.
            restored_traces = []
            for trace in t:
                saved_env, saved_localenv = saved_envs[0] if saved_envs else (trace.latest_state.env, trace.latest_state.localenv)
                restored_traces.append(trace.extend(lambda s, env=saved_env, localenv=saved_localenv:
                                                    replace(s, env=env, localenv=localenv)))
            return restored_traces

        # todo bring other cases as needed

        case _:
            raise NotImplementedError(f"Unhandled node type: '{node.NodeName}'")

def starting_state(fs_model: FSModel | None = None) -> State:
    # env["IFS"] = ShellVar(" \t\n")
    # for defaultvar in ["HOME", "PWD", "OLDPWD", "PATH"]:
    #     env[defaultvar] = ShellVar(SymStr(util.create_fresh_varname(f"default_{defaultvar}"))
    root = State(fs_model = FSModelSimple(field_to_z3)) if fs_model is None else State(fs_model = fs_model)
    make_ast = lambda var: AST.VArgChar("Normal", False, var, [])
    starter_env = {
        "HOME": ShellVar(arbitrary_field(make_ast("HOME"), ArbitraryType.ENVIRONMENT, root)),
        "PWD": ShellVar(arbitrary_field(make_ast("PWD"), ArbitraryType.ENVIRONMENT, root, min_words=1)),
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
    global stop_event
    global inactive_trace_stash

    logging.info("Running symb engine with %d raw nodes", len(nodes))
    inactive_trace_stash = []
    traces = [Trace((starting_state(),))]

    func_map = replace(func_map, funcs=find_func_defs(traces, nodes, config), called=set())

    for node in nodes:
        if stop_event and stop_event.is_set():
            break
        context_line = node.get_line_number()
        logging.debug("Interpreting next node (line %d) %s",
                      context_line, trim_string_for_logging(node.ast_node.pretty()))
        traces = guarded_interp_node(traces, node.ast_node, config)

    uncalled_funcs = func_map.uncalled_funcs().items()
    func_map = FuncMap() # Replace the func map with an empty one to avoid generating false "Undefined function" errors when checking uncalled functions

    func_traces: dict[str, Traces] = {}
    for (name, node) in uncalled_funcs:
        if stop_event and stop_event.is_set():
            break
        logging.info("Interpreting uncalled function '%s'", name)
        func_traces[name] = guarded_interp_node([Trace((starting_state(),))], node, config)


    return traces + [t for ts in func_traces.values() for t in ts] + inactive_trace_stash

def symbexec_file(input_file: str,
                  config: InterpConfig,
                  stop: Event | None,
                  dfs_timeout: float | None = None,
                  main_timeout: float | None = None) -> SymbexecResult:
    global stop_event
    stop_event = stop

    try:
        def constant_word(arg: list[AST.ArgChar]) -> str | None:
            chars: list[str] = []
            for c in arg:
                if isinstance(c, AST.CArgChar):
                    chars.append(chr(c.char))
                else:
                    return None
            return "".join(chars)

        def command_name(node: AST.CommandNode) -> str:
            if not node.arguments:
                return ""
            return constant_word(node.arguments[0]) or ""

        def branch_policy_half_n_half_if_too_many(node, t_then: Traces, t_else: Traces) -> tuple[Traces, Traces]:
            if len(t_then) + len(t_else) > 256:
                logging.info("Too many traces; dropping half of them in branch policy")
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
        func_defs = find_func_defs([Trace((starting_state(),))], nodes, config)

        def func_calls_dangerous(func_name: str, danger_cache: dict[str, bool]) -> bool:
            if func_name in danger_cache:
                return danger_cache[func_name]
            func_node = func_defs.get(func_name)
            danger_cache[func_name] = False
            if func_node is None:
                return False
            for cmd in util.iter_ast_command(func_node):
                if not isinstance(cmd, AST.CommandNode):
                    continue
                name = command_name(cmd)
                if is_dangerous_command(name):
                    danger_cache[func_name] = True
                    return True
                if name is not None and name in func_defs and func_calls_dangerous(name, danger_cache):
                    danger_cache[func_name] = True
                    return True
            return False

        safe_funcs = frozenset(
            name for name in func_defs.keys()
            if not func_calls_dangerous(name, {})
        )
        # opt_store = parse_shebang_args(input_file)
        if config.DFS_first:
            logging.info("Doing whole execution with a single trace first (DFS_first)")
            logging.info("DFS run: targeting dangerous commands")
            dfs_event = _set_timer(dfs_timeout) if dfs_timeout is not None else stop_event
            prev_stop_event = stop_event
            stop_event = dfs_event
            targeted_result = run_targeted_dfs(
                nodes=nodes,
                config=replace(config, current_pass="dangerous-first"),
                symb_engine=symb_engine,
                func_defs=func_defs,
                ignore_function_calls_for=safe_funcs,
            )
            logging.info("DFS run: only taking THEN branches")
            symb_engine(nodes, replace(config,
                                       branch_policy=branch_policy_only_then,
                                       current_pass="conds:then"))
            logging.info("DFS run: only taking ELSE branches")
            symb_engine(nodes, replace(config,
                                       branch_policy=branch_policy_only_else,
                                       current_pass="conds:else"))
            issues_so_far = Reporter._issues.copy()
            logging.info("DFS run: only taking THEN branches with unbound variables as empty strings")
            symb_engine(nodes, replace(config,
                                       branch_policy=branch_policy_only_then,
                                       unbound_policy=UnboundVariablePolicy.EMPTY,
                                       current_pass="unbound:empty+conds:then"))
            logging.info("DFS run: only taking ELSE branches with unbound variables as empty strings")
            symb_engine(nodes, replace(config,
                                       branch_policy=branch_policy_only_else,
                                       unbound_policy=UnboundVariablePolicy.EMPTY,
                                       current_pass="unbound:empty+conds:else"))
            logging.info("DFS run: treating unbound variables solely as empty strings")
            symb_engine(nodes, replace(config,
                                       unbound_policy=UnboundVariablePolicy.EMPTY,
                                       current_pass="unbound:empty"))
            Reporter.drop_issues({reporter.Code.DELETE_SYSTEM_FILE, reporter.Code.CONSTANT_CONDITION})
            Reporter._issues = Reporter._issues | issues_so_far # this dance ensures that any del_sys_files found before the last run are kept
            # logging.info("DFS run: exploring the first trace only")
            # symb_engine(nodes, replace(config, trace_collapser = lambda ts: ts[:1]))
            logging.info("DFS_first run complete, proceeding with normal symbolic execution")
            Reporter.drop_issues({reporter.Code.DEAD_CODE}) # wholly unreliable with branch policies
            if dfs_event is not None and dfs_event.is_set():
                Reporter.clear_timed_out()
            if stop is not None and main_timeout is None:
                main_stop = stop
            else:
                main_stop = _set_timer(main_timeout)
            stop_event = main_stop if main_stop is not None else prev_stop_event
        else:
            main_stop = stop if (stop is not None and main_timeout is None) else _set_timer(main_timeout)
            stop_event = main_stop if main_stop is not None else stop_event

        traces = symb_engine(nodes, replace(config, branch_policy=branch_policy_half_n_half_if_too_many))
        if Reporter.get_timed_out():
            return SymbexecResult(SymbexecStatus.INTERRUPTED, traces)
        return SymbexecResult(SymbexecStatus.COMPLETED, traces)
    except Exception as e:
        logging.error("Symbolic execution failed:")
        logging.error(traceback.format_exc())
        return SymbexecResult(SymbexecStatus.FAILED, [])

stop_event: Event | None = None
_timers: list[threading.Timer] = []

def _set_timer(timeout: float | None) -> Event | None:
    if timeout is None or timeout <= 0:
        return None
    event = Event()
    timer = threading.Timer(timeout, event.set)
    timer.daemon = True
    _timers.append(timer)
    timer.start()
    return event
func_map = FuncMap()
