import fnmatch
import logging
import os
import threading
import traceback
import time
import tempfile
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass, field, replace
from enum import Enum
from math import inf
from collections.abc import Callable
from threading import Event
from typing import NamedTuple

import shasta.ast_node as AST

from sash.fs import FSModel, FSModelSimple
import sash.parser as parser
import sash.reporter as reporter
from sash.symbolic.strings import (
    ArbitraryType,
    CompletelyArbitrary,
    ExpandedChunk,
    Field,
    LiteralChunk,
    PreSplitWord,
    SymStr,
    WordCount,
    merge_partial_fields,
)
import sash.util as util
from sash.config import Config # TODO: refactor to delete sash.config, move all needed stuff to InterpConfig
from sash.constraints import *
from sash.frozen import FrozenAst, FrozenDict, freeze, freeze_thing
from sash.interpreter_config import BranchDecision, BranchSelection, InterpConfig, UnboundVariablePolicy
from sash.reporter import Reporter
from sash.solver import field_to_z3
from sash.specs import get_spec, CmdSpec
from sash.dfs_targeted import *
from sash.symbolic.state import *
from sash.debugtools.logger import DebugLogger


def _set_env_like(state: State, name: str, value: Field | PreSplitWord) -> State:
    stored_value = value if isinstance(value, PreSplitWord) else PreSplitWord.from_field(value)
    existing = state.lookup(name)
    if existing is None:
        return state.set_env(name, ShellVar(stored_value))
    return state.set_env(
        name,
        ShellVar(
            stored_value,
            readonly=existing.readonly,
            export=existing.export,
            ghost=existing.ghost,
        ),
    )


def _pathcond_contradicts(state: State, new_cond: Constraint) -> bool:
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


def _path_lookup_disabled_for_command(name: str, traces: Traces) -> bool:
    # Commands invoked with a slash do not use PATH lookup.
    if "/" in name:
        return False

    # Known shell builtins should still resolve even with PATH unset/empty.
    if name in {"set", "unset", "exit", "read", "cd", "command", "test", "[", "true", "false", ":"}:
        return False

    # Function calls are independent of PATH lookup as well.
    if traces and all(t.latest_state.lookup_fundef(name) is not None for t in traces):
        return False

    for trace in traces:
        path_var = trace.latest_state.lookup("PATH")
        if path_var is None:
            return True
        path_str = path_var.try_to_str()
        if path_str == "":
            return True
    return False


def _update_pwd_for_cd(state: State, expanded_args: tuple[Field, ...]) -> State:
    pwd = state.lookup("PWD")
    if pwd is None:
        return state

    target: Field | None = None
    if len(expanded_args) <= 1:
        home = state.lookup("HOME")
        if home is not None:
            target = home.as_field()
    else:
        operand = expanded_args[1]
        if operand.try_to_str() == "-":
            oldpwd = state.lookup("OLDPWD")
            if oldpwd is not None:
                target = oldpwd.as_field()
        else:
            target = operand

    if target is None:
        return state

    state = _set_env_like(state, "OLDPWD", pwd.value)
    state = _set_env_like(state, "PWD", target)
    return state


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


def _arg_is_fully_literal(arg: list[AST.ArgChar]) -> bool:
    for char in arg:
        match char:
            case AST.CArgChar() | AST.EArgChar():
                continue
            case AST.QArgChar() as q:
                if not _arg_is_fully_literal(q.arg):
                    return False
            case _:
                return False
    return True


def _commandnode_literal_tokens(node: AST.CommandNode) -> list[str] | None:
    tokens: list[str] = []
    for arg in node.arguments:
        if not _arg_is_fully_literal(arg):
            return None
        tokens.append(extract_literal_strings_from_arg(arg))
    return tokens


def _normalized_cmd_name(name: str) -> str:
    return name.rsplit("/", 1)[-1] if "/" in name else name


def _spec_io_for_literal_tokens(tokens: list[str]) -> IOType | None:
    if not tokens:
        return None
    normalized_name = _normalized_cmd_name(tokens[0])
    cmd_inv: tuple[Field, ...] = tuple(
        [Field(SymStr((normalized_name,)), WordCount(1, 1))]
        + [Field(SymStr((tok,)), WordCount(1, 1)) for tok in tokens[1:]]
    )
    spec = get_spec(normalized_name, cmd_inv)
    if spec is None:
        return None
    return spec.io


def _node_invocation_has_no_stdout(node: AST.CommandNode) -> bool:
    tokens = _commandnode_literal_tokens(node)
    if not tokens:
        return False

    io = _spec_io_for_literal_tokens(tokens)

    return io in {IOType.NONE, IOType.STDIN}


def _node_invocation_expects_stdin(node: AST.CommandNode) -> bool:
    tokens = _commandnode_literal_tokens(node)
    if not tokens:
        return False

    io = _spec_io_for_literal_tokens(tokens)
    return io in {IOType.STDIN, IOType.BOTH}


def _node_invocation_name(node: AST.CommandNode) -> str:
    tokens = _commandnode_literal_tokens(node)
    if not tokens:
        return node.pretty()
    return _normalized_cmd_name(tokens[0])


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
            return pwd_var.as_field(), state
        case "echo":
            output_field = merge_partial_fields(operands, sep=" ", state=state) # TODO: sep should be from IFS
            if (output_str := output_field.try_to_str()) is not None:
                output_field = Field(SymStr((output_str,)), word_count_from_output(output_str))
            if isinstance(output_field.content, CompletelyArbitrary) and output_field.count.min == 0:
                output_field = Field(replace(output_field.content, maybe_empty=True), output_field.count)
            return output_field, state
        case "printf": # Treating like echo for now
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


def is_test(f: Field):
    return isinstance(f.content, SymStr) and f.content.parts and f.content.parts[0] in ["test", "["]


def is_constant_test(cmd1: list[Field], cmd2: list[Field]) -> bool:
    """Return true if `cmd1` and `cmd2` are both tests that always have the same result."""

    if len(cmd1) < 1 or len(cmd2) < 1 or len(cmd1) != len(cmd2):
        return False
    match (cmd1[0], cmd2[0]):
        case f1_0, f2_0 if is_test(f1_0) and is_test(f2_0):
            # see CompletelyArbitrary __eq__, which makes this work
            return all(f1 == f2 for f1, f2 in zip(cmd1[1:], cmd2[1:]))
        case _:
            return False


def _parse_loop_control_level(expanded_args: list[Field]) -> int:
    if len(expanded_args) <= 1:
        return 1
    level_str = expanded_args[1].try_to_str()
    if level_str is None:
        return 1
    try:
        level = int(level_str)
    except ValueError:
        return 1
    return max(level, 1)

# ============================================================
#                  Symbolic Expander
# ============================================================

# PreSplitWord is used inside ShellVar values in the new storage IR; make it hashable for FrozenDict.
def _presplitword_hash(self) -> int:  # type: ignore[override]
    return hash(tuple(self.chunks))

PreSplitWord.__hash__ = _presplitword_hash  # type: ignore[assignment]


DEFAULT_IFS = " \t\n"


def _concrete_ifs_value(curr_state: State) -> str | None:
    ifs_value = curr_state.lookup("IFS")
    if ifs_value is None:
        return DEFAULT_IFS
    stored = ifs_value.value
    if isinstance(stored, PreSplitWord):
        # If more than one chunks are present, at least one of them will be symbolic
        if len(stored.chunks) == 1:
            chunk = stored.chunks[0]
            if isinstance(chunk, LiteralChunk):
                return chunk.content
            if isinstance(chunk, ExpandedChunk) and isinstance(chunk.content, str):
                return chunk.content
        return None
    if isinstance(stored, Field):
        return stored.try_to_str()
    return None


def _collect_positional_params(curr_state: State) -> list[PreSplitWord]:
    params: dict[int, PreSplitWord] = {}
    for mapping in (curr_state.localenv, curr_state.env):
        for name, shellvar in mapping.items():
            if not name.isdecimal():
                continue
            idx = int(name)
            if idx <= 0 or idx in params:
                continue
            params[idx] = shellvar.value
    return [params[i] for i in sorted(params)]


def _literal_argchars(argchars: list[AST.ArgChar]) -> str | None:
    chars: list[str] = []
    for ch in argchars:
        match ch:
            case AST.CArgChar() | AST.EArgChar():
                chars.append(chr(ch.char))
            case AST.QArgChar() as qarg:
                inner = _literal_argchars(qarg.arg)
                if inner is None:
                    return None
                chars.append(inner)
            case _:
                return None
    return "".join(chars)


def _trim_pattern(value: str, pattern: str, mode: str) -> str:
    if mode in {"TrimR", "TrimRMax"}:
        indices = [i for i in range(len(value) + 1) if fnmatch.fnmatch(value[i:], pattern)]
        if not indices:
            return value
        idx = min(indices) if mode == "TrimRMax" else max(indices)
        return value[:idx]
    if mode in {"TrimL", "TrimLMax"}:
        indices = [i for i in range(len(value) + 1) if fnmatch.fnmatch(value[:i], pattern)]
        if not indices:
            return value
        idx = max(indices) if mode == "TrimLMax" else min(indices)
        return value[idx:]
    return value


def _maybe_report_inconsistent_ifs(traces: Traces, config: InterpConfig) -> None:
    if not traces:
        return
    concrete_values: list[str] = []
    for trace in traces:
        ifs_value = _concrete_ifs_value(trace.latest_state)
        if ifs_value is not None:
            concrete_values.append(ifs_value)
    unique_values = sorted(set(concrete_values))
    if len(unique_values) > 1:
        Reporter.add_issue(reporter.InconsistentIFS(unique_values, context_line), config)


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
    _maybe_report_inconsistent_ifs(traces, config)
    for trace in traces:
        prefix_fields = prefix.get(id(trace), [])
        # expand_simple(stuff, trace.latest_state, config)
        for expanded_fields, new_state in expand_simple(stuff, trace.latest_state, config):
            new_trace = trace.extend(new_state)
            res.append((new_trace, prefix_fields + expanded_fields))
    return res


def expand_simple(stuff: list[AST.ArgChar],
                  state: State,
                  config: InterpConfig) -> list[tuple[list[Field], State]]:
    """
    (MODIFIED) Retrofitted to act as the Command Context bridge.
    Generates PreSplitWords, applies IFS splitting, and handles glob approximation.
    """
    def positional_match(argchars: list[AST.ArgChar]) -> tuple[AST.VArgChar, bool] | None:
        if len(argchars) != 1:
            return None
        match argchars[0]:
            case AST.VArgChar() as var_node if var_node.var in {"@", "*"}:
                return var_node, False
            case AST.QArgChar() as qarg:
                if len(qarg.arg) == 1 and isinstance(qarg.arg[0], AST.VArgChar) and qarg.arg[0].var in {"@", "*"}:
                    return qarg.arg[0], True
        return None

    def expand_positional(var_node: AST.VArgChar, quoted: bool) -> list[tuple[list[Field], State]]:
        params = _collect_positional_params(state)
        if not params:
            return [([], state)]
        if var_node.var == "*" and quoted:
            ifs_value = _concrete_ifs_value(state)
            if ifs_value is None:
                arbitrary = CompletelyArbitrary(freeze_thing(var_node), ArbitraryType.APPROXIMATION, state)
                return [([Field(arbitrary, WordCount(0, inf))], state)]
            sep = ifs_value[:1]
            pieces: list[str] = []
            for param in params:
                param_str = param.try_to_str()
                if param_str is None:
                    arbitrary = CompletelyArbitrary(freeze_thing(var_node), ArbitraryType.APPROXIMATION, state)
                    return [([Field(arbitrary, WordCount(0, inf))], state)]
                pieces.append(param_str)
            joined = sep.join(pieces)
            return [([Field(SymStr((joined,)), WordCount(1, 1))], state)]

        if quoted:
            fields = [param.to_field().quote() for param in params]
            return [(fields, state)]

        ifs_value = _concrete_ifs_value(state)
        if ifs_value is None:
            arbitrary = CompletelyArbitrary(freeze_thing(var_node), ArbitraryType.APPROXIMATION, state)
            return [([Field(arbitrary, WordCount(0, inf))], state)]
        expanded_fields: list[Field] = []
        for param in params:
            expanded_fields.extend(param.expand_from_storage(False).do_field_splitting(ifs_value))
        return [(expanded_fields, state)]

    positional = positional_match(stuff)
    if positional is not None:
        var_node, quoted = positional
        return expand_positional(var_node, quoted)

    def word_needs_ifs(word: PreSplitWord) -> bool:
        return any(isinstance(chunk, ExpandedChunk) and not chunk.is_quoted for chunk in word.chunks)

    expansions = expand_to_word_simple(stuff, state, config)
    res: list[tuple[list[Field], State]] = []
    for word, new_state in expansions:
        ifs_value = _concrete_ifs_value(new_state)
        if ifs_value is None and word_needs_ifs(word):
            arbitrary = CompletelyArbitrary(freeze_thing(stuff), ArbitraryType.APPROXIMATION, new_state)
            res.append(([Field(arbitrary, WordCount(0, inf))], new_state))
            continue
        split_ifs = ifs_value if ifs_value is not None else DEFAULT_IFS
        res.append((word.do_field_splitting(split_ifs), new_state))
    return res


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


def expand_to_word_simple(stuff: list[AST.ArgChar],
                          state: State,
                          config: InterpConfig) -> list[tuple[PreSplitWord, State]]:
    """
    (NEW) Core AST evaluator.
    Walks the AST, resolves parameter/command expansions, and preserves quotes.
    Returns all possible expansions as intermediate PreSplitWords.
    """
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
            case AST.VArgChar() as var_node:
                return var_node.var
            case AST.QArgChar() as qarg:
                return argchars_var_name(qarg.arg)
            case _:
                return None

    def source_var_name(source) -> str | None:
        match source:
            case FrozenAst(ast=ast):
                match ast:
                    case AST.VArgChar() as var_node:
                        return var_node.var
                    case AST.QArgChar() as qarg:
                        return argchars_var_name(qarg.arg)
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

    def word_is_definitely_empty(value: PreSplitWord | Field) -> bool:
        if isinstance(value, Field):
            return field_is_definitely_empty(value)
        if not value.chunks:
            return True
        if any(isinstance(chunk, LiteralChunk) and chunk.content for chunk in value.chunks):
            return False
        if any(isinstance(chunk, ExpandedChunk) and isinstance(chunk.content, str) and chunk.content for chunk in value.chunks):
            return False
        for chunk in value.chunks:
            if isinstance(chunk, ExpandedChunk) and isinstance(chunk.content, CompletelyArbitrary):
                if chunk.count.max > 0:
                    return False
        return all(
            (isinstance(chunk, LiteralChunk) and chunk.content == "")
            or (isinstance(chunk, ExpandedChunk) and isinstance(chunk.content, str) and chunk.content == "")
            or (isinstance(chunk, ExpandedChunk) and isinstance(chunk.content, CompletelyArbitrary) and chunk.count.max == 0)
            for chunk in value.chunks
        )

    def word_is_definitely_non_empty(value: PreSplitWord | Field) -> bool:
        if isinstance(value, Field):
            return field_is_definitely_non_empty(value)
        if any(isinstance(chunk, LiteralChunk) and chunk.content for chunk in value.chunks):
            return True
        if any(isinstance(chunk, ExpandedChunk) and isinstance(chunk.content, str) and chunk.content for chunk in value.chunks):
            return True
        for chunk in value.chunks:
            if isinstance(chunk, ExpandedChunk) and isinstance(chunk.content, CompletelyArbitrary):
                if chunk.count.min >= 1:
                    return True
        return False

    def empty_word(quoted: bool) -> PreSplitWord:
        return PreSplitWord([ExpandedChunk(content="", is_quoted=quoted, count=WordCount(0, 0))])

    def as_expansion_word(word: PreSplitWord) -> PreSplitWord:
        converted: list[LiteralChunk | ExpandedChunk] = []
        for chunk in word.chunks:
            if isinstance(chunk, LiteralChunk):
                converted.append(
                    ExpandedChunk(
                        content=chunk.content,
                        is_quoted=chunk.is_quoted,
                        count=WordCount(1, 1),
                    )
                )
            else:
                converted.append(chunk)
        return PreSplitWord(converted)

    def arbitrary_word(source: AST.AstNode, kind: ArbitraryType, producing_state: State, min_words: int = 0, quoted: bool = False) -> PreSplitWord:
        return PreSplitWord([
            ExpandedChunk(
                content=CompletelyArbitrary(freeze_thing(source), kind, producing_state),
                is_quoted=quoted,
                count=WordCount(min_words, inf),
            )
        ])

    def word_from_field(field: Field, quoted: bool, producing_state: State) -> PreSplitWord:
        content = field.content
        if isinstance(content, SymStr):
            text = content.try_to_str()
            if text is not None:
                return PreSplitWord([
                    ExpandedChunk(content=text, is_quoted=quoted, count=field.count)
                ])
            arbitrary = CompletelyArbitrary(freeze_thing(content), ArbitraryType.APPROXIMATION, producing_state)
            return PreSplitWord([
                ExpandedChunk(content=arbitrary, is_quoted=quoted, count=field.count)
            ])
        return PreSplitWord([
            ExpandedChunk(content=content, is_quoted=quoted, count=field.count)
        ])

    def word_from_value(value: PreSplitWord | Field, quoted: bool, producing_state: State) -> PreSplitWord:
        if isinstance(value, PreSplitWord):
            return value.expand_from_storage(quoted)
        return word_from_field(value, quoted, producing_state)

    def ensure_non_empty_word(value: PreSplitWord | Field, quoted: bool, producing_state: State) -> PreSplitWord:
        if isinstance(value, PreSplitWord):
            for chunk in value.chunks:
                if isinstance(chunk, ExpandedChunk) and isinstance(chunk.content, CompletelyArbitrary):
                    if chunk.count.min < 1:
                        return PreSplitWord([
                            ExpandedChunk(
                                content=chunk.content,
                                is_quoted=quoted,
                                count=WordCount(1, chunk.count.max),
                            )
                        ])
            return value.expand_from_storage(quoted)
        if value.count.min < 1:
            adjusted = replace(value, count=WordCount(1, value.count.max))
            return word_from_field(adjusted, quoted, producing_state)
        return word_from_field(value, quoted, producing_state)

    def qarg_literal(qarg: AST.QArgChar) -> str | None:
        chars: list[str] = []
        for ch in qarg.arg:
            match ch:
                case AST.CArgChar() | AST.EArgChar():
                    chars.append(chr(ch.char))
                case _:
                    return None
        return "".join(chars)

    def decode_ansi_c_escapes(raw: str) -> str | None:
        result: list[str] = []
        i = 0
        simple_escapes = {
            "a": "\a",
            "b": "\b",
            "e": "\x1b",
            "E": "\x1b",
            "f": "\f",
            "n": "\n",
            "r": "\r",
            "t": "\t",
            "v": "\v",
            "\\": "\\",
            "'": "'",
            '"': '"',
            "?": "?",
        }
        while i < len(raw):
            ch = raw[i]
            if ch != "\\":
                result.append(ch)
                i += 1
                continue

            i += 1
            if i >= len(raw):
                return None

            esc = raw[i]
            if esc in simple_escapes:
                result.append(simple_escapes[esc])
                i += 1
                continue

            if esc in "01234567":
                j = i
                while j < len(raw) and raw[j] in "01234567" and (j - i) < 3:
                    j += 1
                result.append(chr(int(raw[i:j], 8) & 0xFF))
                i = j
                continue

            if esc == "x":
                j = i + 1
                while j < len(raw) and raw[j].lower() in "0123456789abcdef":
                    j += 1
                if j == i + 1:
                    return None
                result.append(chr(int(raw[i + 1:j], 16) & 0xFF))
                i = j
                continue

            if esc == "u":
                j = i + 1
                if j + 4 > len(raw):
                    return None
                digits = raw[j:j + 4]
                if any(d.lower() not in "0123456789abcdef" for d in digits):
                    return None
                result.append(chr(int(digits, 16)))
                i = j + 4
                continue

            if esc == "U":
                j = i + 1
                if j + 8 > len(raw):
                    return None
                digits = raw[j:j + 8]
                if any(d.lower() not in "0123456789abcdef" for d in digits):
                    return None
                value = int(digits, 16)
                if value > 0x10FFFF:
                    return None
                result.append(chr(value))
                i = j + 8
                continue

            result.append(esc)
            i += 1

        return "".join(result)

    @dataclass
    class Partial:
        quoted: bool
        state: State
        chunks: list[LiteralChunk | ExpandedChunk] = field(default_factory=list)
        literal_buffer: list[str] = field(default_factory=list)
        literal_buffer_quoted: bool | None = None

        def flush_literal(self) -> None:
            if not self.literal_buffer:
                return
            text = "".join(self.literal_buffer)
            quoted = bool(self.literal_buffer_quoted)
            self.chunks.append(LiteralChunk(content=text, is_quoted=quoted))
            self.literal_buffer.clear()
            self.literal_buffer_quoted = None

        def add_literal(self, text: str) -> None:
            if self.literal_buffer and self.literal_buffer_quoted != self.quoted:
                self.flush_literal()
            if not self.literal_buffer:
                self.literal_buffer_quoted = self.quoted
            self.literal_buffer.append(text)

        def add_word(self, word: PreSplitWord) -> None:
            self.flush_literal()
            self.chunks.extend(word.chunks)

        def add_expanded(self, content: str | CompletelyArbitrary, count: WordCount) -> None:
            self.flush_literal()
            self.chunks.append(ExpandedChunk(content=content, is_quoted=self.quoted, count=count))

        def add_empty(self) -> None:
            self.add_word(empty_word(self.quoted))

        def try_expand_dollar_quoted_literal(self, qarg: AST.QArgChar) -> bool:
            if self.quoted or not self.literal_buffer:
                return False
            last_part = self.literal_buffer[-1]
            if not last_part.endswith("$"):
                return False

            raw = qarg_literal(qarg)
            if raw is None:
                return False
            decoded = decode_ansi_c_escapes(raw)
            if decoded is None:
                return False

            if last_part == "$":
                self.literal_buffer.pop()
            else:
                self.literal_buffer[-1] = last_part[:-1]
            self.literal_buffer.append(decoded)
            return True

        def finish(self) -> tuple[PreSplitWord, State]:
            self.flush_literal()
            return PreSplitWord(self.chunks), self.state

        def fork(self, pathcond: Constraint) -> tuple['Partial', 'Partial']:
            lhs = self.fork_state(self.state.add_pathcond(pathcond))
            rhs = self.fork_state(self.state.add_pathcond(Not(pathcond)))
            return (lhs, rhs)

        def fork_state(self, new_state: State) -> 'Partial':
            return replace(
                self,
                state=new_state,
                chunks=list(self.chunks),
                literal_buffer=list(self.literal_buffer),
                literal_buffer_quoted=self.literal_buffer_quoted,
            )

        def next(self, argchar: AST.ArgChar) -> list['Partial']:
            match argchar:
                case AST.CArgChar() as c:
                    self.add_literal(c.pretty(AST.QUOTED if self.quoted else AST.UNQUOTED))
                case AST.EArgChar() as c:
                    self.add_literal(c.pretty(AST.QUOTED if self.quoted else AST.UNQUOTED))
                case AST.TArgChar() as t:
                    if not self.quoted and getattr(t, "string", None) in (None, "None"):
                        home_var = self.state.lookup("HOME")
                        if home_var is not None:
                            home_word = word_from_value(home_var.value, True, self.state)
                            self.add_word(home_word)
                        else:
                            self.add_word(arbitrary_word(t, ArbitraryType.ENVIRONMENT, self.state))
                    else:
                        logging.debug("Expansion: treating tilde '%s' as completely arbitrary", t.pretty())
                        self.add_word(arbitrary_word(t, ArbitraryType.APPROXIMATION, self.state))
                case AST.QArgChar() as qarg:
                    if self.try_expand_dollar_quoted_literal(qarg):
                        return [self]
                    partial_for_inside = Partial(True, self.state)
                    res: list[Partial] = []
                    for inside_partial in expand_inner(qarg.arg, partial_for_inside):
                        inner_word, inner_state = inside_partial.finish()
                        continuing = self.fork_state(inner_state)
                        continuing.add_word(inner_word)
                        res.append(continuing)
                    return res
                case AST.VArgChar() as var_node:
                    name = var_node.var
                    if name.isdecimal() and name != "0":
                        name = str(int(name) + self.state.curr_shift_offset())

                    def expand_default_value(partial: 'Partial') -> tuple[PreSplitWord, State]:
                        default_expansions = expand_inner(var_node.arg, Partial(partial.quoted, partial.state))
                        if len(default_expansions) != 1:
                            raise NotImplementedError(
                                f"default value expansion forking is not implemented (got {len(default_expansions)} expansions)"
                            )
                        return default_expansions[0].finish()

                    def assign_default_value(partial: 'Partial', default_word: PreSplitWord, default_state: State) -> None:
                        partial.state = default_state.set_env(name, ShellVar(default_word.prepare_for_storage()))
                        partial.add_word(as_expansion_word(default_word))

                    def add_value_word(value: PreSplitWord | Field) -> None:
                        self.add_word(word_from_value(value, self.quoted, self.state))

                    if name == "?":
                        if self.state.last_exit_code[1] == Confidence.DEFINITE:
                            code_str = self.state.last_exit_code[0].try_to_str()
                            if code_str is not None:
                                self.add_expanded(code_str, WordCount(1, 1))
                            else:
                                self.add_word(arbitrary_word(var_node, ArbitraryType.APPROXIMATION, self.state, min_words=1, quoted=self.quoted))
                        else:
                            self.add_word(arbitrary_word(var_node, ArbitraryType.APPROXIMATION, self.state, min_words=1, quoted=self.quoted))
                        return [self]


                    if (v := self.state.lookup(name)):
                        value = v.value
                        if var_node.fmt == "Normal" \
                            or (var_node.fmt == "Minus" and not var_node.null and not v.ghost) \
                            or (var_node.fmt == "Question" and not var_node.null and not v.ghost):
                            add_value_word(value)
                        elif var_node.fmt == "Minus" and (var_node.null or v.ghost):
                            if word_is_definitely_empty(value):
                                default_word, default_state = expand_default_value(self)
                                self.state = default_state
                                self.add_word(as_expansion_word(default_word))
                            elif word_is_definitely_non_empty(value):
                                add_value_word(value)
                            else:
                                value_field = value.to_field() if isinstance(value, PreSplitWord) else value
                                empty_field = Field(SymStr(("",)), WordCount(0, 0))
                                empty_cond = StringEq(value_field, empty_field)
                                non_default = self.fork_state(self.state.add_pathcond(Not(empty_cond)))
                                default = self.fork_state(self.state.add_pathcond(empty_cond))
                                default_word, default_state = expand_default_value(default)
                                default.state = default_state
                                default.add_word(as_expansion_word(default_word))
                                non_default.add_word(arbitrary_word(var_node, ArbitraryType.APPROXIMATION, non_default.state, quoted=self.quoted))
                                return [non_default, default]
                        elif var_node.fmt in {"Length", "TrimR", "TrimRMax", "TrimL", "TrimLMax"}:
                            definitely_empty = word_is_definitely_empty(value)
                            definitely_non_empty = word_is_definitely_non_empty(value)
                            value_field = value.to_field() if isinstance(value, PreSplitWord) else value

                            def add_symbolic_trim_result(partial: 'Partial', min_words: int) -> None:
                                out_field = arbitrary_field(
                                    var_node,
                                    ArbitraryType.APPROXIMATION,
                                    partial.state,
                                    min_words=min_words,
                                )
                                partial.add_word(word_from_field(out_field, partial.quoted, partial.state))

                            if definitely_empty:
                                if var_node.fmt == "Length":
                                    self.add_expanded("0", WordCount(1, 1))
                                else:
                                    self.add_word(empty_word(self.quoted))
                            else:
                                if var_node.fmt == "Length":
                                    if isinstance(value, Field):
                                        value_str = value.try_to_str()
                                    else:
                                        value_str = value.try_to_str()
                                    if value_str is not None:
                                        self.add_expanded(str(len(value_str)), WordCount(1, 1))
                                    else:
                                        self.add_word(arbitrary_word(var_node, ArbitraryType.APPROXIMATION, self.state, min_words=1, quoted=self.quoted))
                                else:
                                    pattern = _literal_argchars(var_node.arg)
                                    value_str = value.try_to_str()
                                    if pattern is not None and value_str is not None:
                                        trimmed = _trim_pattern(value_str, pattern, var_node.fmt)
                                        if trimmed == "":
                                            self.add_word(empty_word(self.quoted))
                                        else:
                                            self.add_expanded(trimmed, WordCount(1, 1))
                                    else:
                                        supports_empty_refinement = (
                                            pattern in {"/", "/*"}
                                            and var_node.fmt in {"TrimR", "TrimL"}
                                        )
                                        if supports_empty_refinement and not definitely_non_empty:
                                            empty_field = Field(SymStr(("",)), WordCount(0, 0))
                                            empty_constraint = StringEq(value_field, empty_field)
                                            empty_case = self.fork_state(
                                                self.state.add_pathcond(
                                                    empty_constraint,
                                                    source_str=f"trim empty case for {var_node.pretty()}",
                                                    source_line=context_line,
                                                )
                                            )
                                            empty_case.add_word(empty_word(empty_case.quoted))

                                            non_empty_case = self.fork_state(
                                                self.state.add_pathcond(
                                                    Not(empty_constraint),
                                                    source_str=f"trim non-empty case for {var_node.pretty()}",
                                                    source_line=context_line,
                                                )
                                            )
                                            add_symbolic_trim_result(non_empty_case, 1)
                                            return [non_empty_case, empty_case]

                                        min_words = 1 if definitely_non_empty else 0
                                        add_symbolic_trim_result(self, min_words)
                        elif var_node.fmt == "Assign":
                            if not var_node.null:
                                add_value_word(value)
                            else:
                                if word_is_definitely_empty(value):
                                    default_word, default_state = expand_default_value(self)
                                    assign_default_value(self, default_word, default_state)
                                elif word_is_definitely_non_empty(value):
                                    add_value_word(value)
                                else:
                                    empty_case, non_empty = self.fork(Description(f"{name} is non-empty for := expansion"))
                                    default_word, default_state = expand_default_value(empty_case)
                                    assign_default_value(empty_case, default_word, default_state)
                                    non_empty.add_word(ensure_non_empty_word(value, non_empty.quoted, non_empty.state))
                                    return [non_empty, empty_case]
                        elif var_node.fmt == "Question" and (var_node.null or v.ghost):
                            if word_is_definitely_empty(value):
                                self.state = self.state.terminate()
                                self.chunks = []
                                self.literal_buffer = []
                                self.literal_buffer_quoted = None
                                return [self]
                            if word_is_definitely_non_empty(value):
                                add_value_word(value)
                            else:
                                self.add_word(ensure_non_empty_word(value, self.quoted, self.state))
                        elif var_node.fmt == "Plus" and not var_node.null:
                            default_word, default_state = expand_default_value(self)
                            self.state = default_state
                            self.add_word(as_expansion_word(default_word))
                        elif var_node.fmt == "Plus" and var_node.null:
                            if word_is_definitely_empty(value):
                                self.add_word(empty_word(self.quoted))
                            elif word_is_definitely_non_empty(value):
                                default_word, default_state = expand_default_value(self)
                                self.state = default_state
                                self.add_word(as_expansion_word(default_word))
                            else:
                                empty_case, word_case = self.fork(Description(f"{name} is non-empty for :+ expansion"))
                                empty_case.add_word(empty_word(self.quoted))
                                default_word, default_state = expand_default_value(word_case)
                                word_case.state = default_state
                                word_case.add_word(as_expansion_word(default_word))
                                return [empty_case, word_case]
                        else:
                            logging.info("Expansion: treating var '%s' with unhandled fmt '%s' as completely arbitrary", var_node.pretty(), var_node.fmt)
                            self.add_word(arbitrary_word(var_node, ArbitraryType.APPROXIMATION, self.state, quoted=self.quoted))
                        return [self]

                    if var_node.fmt == "Minus":
                        if config.unbound_policy == UnboundVariablePolicy.EMPTY:
                            default_word, default_state = expand_default_value(self)
                            self.state = default_state
                            self.add_word(as_expansion_word(default_word))
                        else:
                            non_default, default = self.fork(Description(f"{name} takes the default value {Field.create_constant(util.shasta_pretty(var_node.arg))}"))
                            default_word, default_state = expand_default_value(default)
                            default.state = default_state
                            default.add_word(as_expansion_word(default_word))
                            arbitrary_for_this_var = arbitrary_word(var_node, ArbitraryType.ENVIRONMENT, non_default.state, quoted=non_default.quoted)
                            non_default.state = non_default.state.extend_localenv({
                                name: ShellVar(arbitrary_for_this_var.prepare_for_storage(), ghost=True)
                            })
                            non_default.add_word(arbitrary_for_this_var)
                            return [non_default, default]
                    elif var_node.fmt == "Question":
                        if config.unbound_policy == UnboundVariablePolicy.EMPTY:
                            self.state = self.state.terminate()
                            self.chunks = []
                            self.literal_buffer = []
                            self.literal_buffer_quoted = None
                            return [self]
                        self.add_word(arbitrary_word(var_node, ArbitraryType.ENVIRONMENT, self.state, min_words=1, quoted=self.quoted))
                    elif var_node.fmt == "Plus":
                        self.add_word(empty_word(self.quoted))
                    elif var_node.fmt == "Assign":
                        default_word, default_state = expand_default_value(self)
                        assign_default_value(self, default_word, default_state)
                    else:
                        if not is_special_var(name):
                            error_code = reporter.UnboundIDSetU if self.state.opts.is_set(SetOptions.NOUNSET) else reporter.UnboundID
                            Reporter.add_issue(error_code(var_node.pretty(), context_line), config)
                        if config.unbound_policy == UnboundVariablePolicy.EMPTY:
                            empty_word_value = empty_word(self.quoted)
                            self.add_word(empty_word_value)
                            self.state = self.state.extend_localenv({
                                name: ShellVar(empty_word_value.prepare_for_storage(), ghost=True)
                            })
                        else:
                            arbitrary_for_this_var = arbitrary_word(
                                var_node,
                                ArbitraryType.APPROXIMATION if is_special_var(name) else ArbitraryType.ENVIRONMENT,
                                self.state,
                                quoted=self.quoted,
                            )
                            self.state = self.state.extend_localenv({
                                name: ShellVar(arbitrary_for_this_var.prepare_for_storage(), ghost=True)
                            })
                            self.add_word(arbitrary_for_this_var)
                    return [self]
                case AST.BArgChar() as barg:
                    inner_cmds = []
                    temp_config = config.add_expanded_command_callback(lambda expanded: inner_cmds.append(expanded))
                    _ = guarded_interp_node([Trace((self.state,))], barg.node, temp_config)
                    output_word: PreSplitWord | None = None
                    if len(inner_cmds) != 0 and isinstance(barg.node, AST.CommandNode):
                        expanded_args = inner_cmds[-1]
                        if expanded_args and (cmd_name := expanded_args[0].try_to_str()):
                            spec = get_spec(cmd_name, tuple(expanded_args))
                            output_field, new_state = command_substitution_output(
                                cmd_name,
                                expanded_args[1:],
                                barg,
                                self.state,
                                spec,
                                config,
                            )
                            self.state = new_state
                            if output_field is not None:
                                output_word = word_from_field(output_field, self.quoted, self.state)
                    if output_word is not None:
                        self.add_word(output_word)
                    else:
                        self.add_word(arbitrary_word(barg, ArbitraryType.APPROXIMATION, self.state, quoted=self.quoted))
                case _:
                    logging.debug(
                        "Unsupported argchar of type '%s': '%s'; treating as completely arbitrary",
                        argchar.NodeName,
                        argchar.pretty(),
                    )
                    self.add_word(arbitrary_word(argchar, ArbitraryType.APPROXIMATION, self.state, quoted=self.quoted))

            return [self]

    def expand_inner(chars: list[AST.ArgChar], partial: Partial) -> list[Partial]:
        expansions = [partial]
        for argchar in chars:
            expansions = [next_expansion for expansion in expansions for next_expansion in expansion.next(argchar)]
        return expansions

    partials = expand_inner(stuff, Partial(False, state))
    return [partial.finish() for partial in partials]


def expand_to_word(traces: Traces,
                   stuff: list[AST.ArgChar],
                   config: InterpConfig) -> list[tuple[Trace, PreSplitWord]]:
    """
    (NEW) Generic interface for Assignment Contexts.
    Returns traces and their un-split intermediate representations.
    """
    res = []
    for trace in traces:
        for word, new_state in expand_to_word_simple(stuff, trace.latest_state, config):
            new_trace = trace.extend(new_state)
            res.append((new_trace, word.prepare_for_storage()))
    return res

# =====================
#  Field manipulation
# =====================

def arbitrary_field(ast: AST.AstNode, kind: ArbitraryType, producing_state: State | None, min_words = 0) -> Field:
    return Field(CompletelyArbitrary(freeze_thing(ast), kind, producing_state),
                 WordCount(min_words, inf))


def join_fields(fields: list[Field]) -> Field:
    """Join a list of fields into one field that approximates all of them."""
    return merge_partial_fields(fields, sep=" ", state=None)


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
returning_trace_stash: list[Trace] = []
breaking_trace_stash: list[Trace] = []
continuing_trace_stash: list[Trace] = []

trace_count = 1

def _compact_trace_history(trace: Trace) -> Trace:
    """
    Keep only the initial and latest states for traces that are no longer executed.
    This preserves solver-relevant information (initial env and latest assertions/fs)
    while reducing memory retained in inactive trace storage.
    """
    if len(trace.states) <= 2:
        return trace
    return replace(trace, states=(trace.states[0], trace.states[-1]))


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


def split_returning_traces(traces: Traces) -> tuple[Traces, Traces]:
    returning_traces, non_returning_traces = util.partition(traces, lambda t: t.latest_state.is_returning)
    if len(returning_traces) > 0:
        logging.debug("Splitting off %d returning traces", len(returning_traces))
    return non_returning_traces, returning_traces


def split_breaking_traces(traces: Traces) -> tuple[Traces, Traces]:
    breaking_traces, non_breaking_traces = util.partition(traces, lambda t: t.latest_state.break_level > 0)
    if len(breaking_traces) > 0:
        logging.debug("Splitting off %d breaking traces", len(breaking_traces))
    return non_breaking_traces, breaking_traces


def split_continuing_traces(traces: Traces) -> tuple[Traces, Traces]:
    continuing_traces, non_continuing_traces = util.partition(traces, lambda t: t.latest_state.continue_level > 0)
    if len(continuing_traces) > 0:
        logging.debug("Splitting off %d continuing traces", len(continuing_traces))
    return non_continuing_traces, continuing_traces


def consume_break_traces(traces: Traces) -> tuple[Traces, Traces]:
    non_breaking, breaking = split_breaking_traces(traces)
    consumed = [t.extend(lambda s: s.set_break_level(0)) for t in breaking if t.latest_state.break_level == 1]
    propagated = [t.extend(lambda s: s.decrement_break_level()) for t in breaking if t.latest_state.break_level > 1]
    return non_breaking, consumed + propagated


def consume_continue_traces(traces: Traces) -> tuple[Traces, Traces, Traces]:
    non_continuing, continuing = split_continuing_traces(traces)
    local = [t.extend(lambda s: s.set_continue_level(0)) for t in continuing if t.latest_state.continue_level == 1]
    propagated = [t.extend(lambda s: s.decrement_continue_level()) for t in continuing if t.latest_state.continue_level > 1]
    return non_continuing, local, propagated


def _collect_non_arg_ast_nodes(value: object,
                               seen_ids: set[int],
                               non_arg_ids: set[int]) -> None:
    if isinstance(value, AST.AstNode):
        node_id = id(value)
        if node_id in seen_ids:
            return
        seen_ids.add(node_id)

        if not isinstance(value, AST.ArgChar):
            non_arg_ids.add(node_id)

        for child in value.__dict__.values():
            _collect_non_arg_ast_nodes(child, seen_ids, non_arg_ids)
        return

    if isinstance(value, dict):
        for child in value.values():
            _collect_non_arg_ast_nodes(child, seen_ids, non_arg_ids)
        return

    if isinstance(value, (list, tuple, set, frozenset)):
        for child in value:
            _collect_non_arg_ast_nodes(child, seen_ids, non_arg_ids)


def count_non_arg_ast_nodes(nodes: list[parser.WrappedAst]) -> int:
    seen_ids: set[int] = set()
    non_arg_ids: set[int] = set()
    for wrapped in nodes:
        _collect_non_arg_ast_nodes(wrapped.ast_node, seen_ids, non_arg_ids)
    return len(non_arg_ids)


def mark_subtree_as_interpreted_for_coverage(root: object) -> None:
    seen_ids: set[int] = set()

    def walk(value: object) -> None:
        if isinstance(value, AST.AstNode):
            node_id = id(value)
            if node_id in seen_ids:
                return
            seen_ids.add(node_id)
            if not isinstance(value, AST.ArgChar):
                Reporter.mark_interpreted_ast_node(value)
            for child in value.__dict__.values():
                walk(child)
            return

        if isinstance(value, dict):
            for child in value.values():
                walk(child)
            return

        if isinstance(value, (list, tuple, set, frozenset)):
            for child in value:
                walk(child)

    walk(root)


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
            pattern_expansions = expand(t1_active, node.arguments[1], config)
            pattern_missing = any(len(fields) == 0 for _, fields in pattern_expansions)
            if pattern_missing:
                Reporter.add_issue(reporter.UnexpectedStdin(cmd_name, context_line), config)
            elif expanded_args[1].count.min == 0 and not util.is_definitely_non_empty(expanded_args[1], t1_active[0]):
                Reporter.add_issue(reporter.UnexpectedStdin(cmd_name, context_line), config)
        if isinstance(cmd_name, str) and _path_lookup_disabled_for_command(cmd_name, t1_active):
            # PATH is unset and command is not guaranteed to exist
            Reporter.add_issue(reporter.NotACommand(cmd_name, context_line), config)
        elif cmd_name == "sudo" and len(expanded_args) >= 2:
            # PATH is not unset (otherwise sudo would not be found)
            sudo_cmd_name = expanded_args[1].try_to_str()
            if isinstance(sudo_cmd_name, str) and not sudo_cmd_name.startswith("-") and (sudo_cmd_name.endswith("/") \
                or any(sudo_cmd_name in t.latest_state.known_nonexistent_commands for t in traces)):
                # Since sudo is a metacommand we need to check if the command it tries to invoke is valid
                Reporter.add_issue(reporter.NotACommand(sudo_cmd_name, context_line), config)

    if expanded_args:
        # TODO: Improve the structure of this function and move this code block elsewhere
        match expanded_args[0].try_to_str():
            case "test" | "[":
                # Warn about field splitting in test commands
                for arg in expanded_args[1:]:
                    match arg:
                        case Field(CompletelyArbitrary() as content, WordCount(_, max_words)):
                            if not content.quoted and max_words > 1:
                                Reporter.add_issue(reporter.DangerousWordSplit(content.source, context_line), config)

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
                        if config.in_checked_position or config.force_fork_all:
                            logging.debug("Adding failure traces for rm")
                            cmd_traces.append(tf)
                t1 = cmd_traces
            case "sudo" if len(expanded_args) >= 2 and expanded_args[1].try_to_str() == "rm":
                logging.debug("Exploring all possible expansions of sudo rm args")
                expansions = expand_args(t1, node.arguments, config)
                simplified_expansions = collapse_equiv_trace_expansions(expansions)
                cmd_traces = []
                for arg_fields, traces in simplified_expansions.items():
                    if len(arg_fields) < 2 or arg_fields[1].try_to_str() != "rm":
                        continue
                    rm_arg_fields = tuple(arg_fields[1:])
                    for trace in traces:
                        ts, tf = handle_rm(rm_arg_fields, trace, node, config)
                        cmd_traces.append(ts)
                        if config.in_checked_position or config.force_fork_all:
                            logging.debug("Adding failure traces for sudo rm")
                            cmd_traces.append(tf)
                if cmd_traces:
                    t1 = cmd_traces
            case "set":
                t1 = handle_set(expanded_args, t1)
            case "unset":
                t1 = handle_unset(expanded_args, t1)
            case "exit":
                t1 = handle_exit(t1)
            case "return":
                if len(expanded_args) > 2 and (retval := expanded_args[1].try_to_str()) is not None:
                    t1 = handle_return(t1, retval)
                else:
                    t1 = handle_return(t1, None)
            case "break":
                t1 = handle_break(t1, expanded_args)
            case "continue":
                t1 = handle_continue(t1, expanded_args)
            case "read":
                t1 = handle_read(expanded_args, t1, node)
            case "xargs":
                t1 = handle_xargs(t1, node, expanded_args, config)
            case "eval":
                t1 = handle_eval(t1, node, expanded_args, config)
            case "find":
                t1 = handle_find(t1, node, expanded_args, config)
            case "shift":
                t1 = handle_shift(t1, node, expanded_args, config)
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
                if spec.check:
                    t_precond = trace_map(t1, lambda s: s.add_assertion(spec.check, source_str=node.pretty(), source_line=context_line))
                    if config.debug_instrumentation:
                        for trace in t1:
                            DebugLogger.log_assertion(spec.check, trace.latest_state, context_line, config.current_pass)
                else:
                    t_precond = t1

                knowledge_before_exec, knowledge_after_exec = spec.success_postcond if isinstance(spec.success_postcond, tuple) else (Empty(), spec.success_postcond)
                t_success_precond = [t for t in t_precond if not _pathcond_contradicts(t.latest_state, knowledge_after_exec)]
                t_success = trace_map(t_success_precond,
                                      lambda s: s.add_pathcond(knowledge_before_exec)\
                                                 .update_fs(knowledge_after_exec)\
                                                 .add_pathcond(knowledge_after_exec)\
                                                 .update_known_commands(knowledge_after_exec)\
                                                 .set_last_exit_code(SymStr(("0",)),
                                                                     Confidence.DEFINITE if s.opts.is_set(SetOptions.NOFAIL) and not config.in_checked_position else Confidence.SPECULATIVE,
                                                                     spec.failure_postcond))
                if cmd_name == "cd": # TODO: Put this somewhere better
                    t_success = trace_map(t_success, lambda s: _update_pwd_for_cd(s, tuple(expanded_args)))
                t_failure = []
                if config.in_checked_position or config.force_fork_all:
                    t_failure_precond = [t for t in t_precond if not _pathcond_contradicts(t.latest_state, spec.failure_postcond)]
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
                # In checked positions (if/while conditions), non-constant commands may either
                # succeed or fail. Keep both outcomes to avoid incorrectly pruning branches.
                if config.in_checked_position or config.force_fork_all:
                    t_success = trace_map(t1, lambda s: s.set_last_exit_code(SymStr(("0",)), Confidence.SPECULATIVE))
                    t_failure = trace_map(t1, lambda s: s.set_last_exit_code(SymStr(("1",)), Confidence.SPECULATIVE))
                    t1 = t_success + t_failure

    for redir in node.redir_list:
        t1 = guarded_interp_node(t1, redir, config)

    config.apply_expanded_command_cbs(expanded_args)

    if logging.getLogger().isEnabledFor(logging.DEBUG):
        logging.debug("Done with command %s after expanding its args to %s (it had assignments: %s)",
                      trim_string_for_logging(node.pretty()), expanded_args, node.assignments)
    return t1


def handle_rm(expanded_args: tuple[Field, ...], trace: Trace, node: AST.CommandNode, config: InterpConfig) -> tuple[Trace, Trace]:
    logging.debug("Checking rm command with expansion possibility: %s", expanded_args)
    spec = get_spec("rm", expanded_args)

    assert spec is not None, "Expected rm spec to always be found"

    if spec.check:
        logging.debug("Adding rm precondition: %s", spec.check)
        DebugLogger.log_assertion(spec.check, trace.latest_state, context_line, config.current_pass)
        trace = trace.extend(lambda s: s.add_assertion(spec.check, source_str=node.pretty(), source_line=context_line))

    non_flag_args = [arg for arg in expanded_args[1:] if not util.is_flag(arg)]

    pwdval = trace.latest_state.lookup("PWD")
    start_pwdval = trace.latest_state.lookup(config.pwd_init_var)
    homeval = trace.latest_state.lookup("HOME")

    def same_location(lhs: Field, rhs: Field) -> bool:
        lhs_str = lhs.try_to_str()
        rhs_str = rhs.try_to_str()
        if lhs_str is not None and rhs_str is not None:
            return lhs.try_without_trailing_slash() == rhs.try_without_trailing_slash()
        lhs_core = util.field_core_key(lhs)
        rhs_core = util.field_core_key(rhs)
        return lhs_core is not None and lhs_core == rhs_core

    def path_depth_from_base(base_path: str, target_path: str) -> int | None:
        # Return how many segments `target_path` is below `base_path`.
        # 0 means equal, 1 means immediate child, 2+ means deeper.
        base_parts = [p for p in base_path.strip("/").split("/") if p]
        target_parts = [p for p in target_path.strip("/").split("/") if p]
        if target_parts[:len(base_parts)] != base_parts:
            return None
        return len(target_parts) - len(base_parts)

    def normalize_path_field(path_field: Field) -> Field:
        normalized = path_field
        while True:
            next_path = normalized.try_without_leading_dot_slash().try_without_trailing_slash()
            if next_path == normalized:
                return normalized
            normalized = next_path

    def home_depth(pwd: Field, home: Field) -> int | None:
        pwd_str = pwd.try_to_str()
        home_str = home.try_to_str()
        if pwd_str is not None and home_str is not None:
            return path_depth_from_base(home_str, pwd_str)

        pwd_core = util.field_core_key(pwd)
        home_core = util.field_core_key(home)
        if pwd_core is None or home_core is None or pwd_core != home_core:
            return None

        pwd_suffix = ""
        if isinstance(pwd.content, CompletelyArbitrary) and pwd.content.suffix is not None:
            pwd_suffix = pwd.content.suffix.try_to_str()
            if pwd_suffix is None:
                return 0  # conservative fallback
        home_suffix = ""
        if isinstance(home.content, CompletelyArbitrary) and home.content.suffix is not None:
            home_suffix = home.content.suffix.try_to_str()
            if home_suffix is None:
                return 0  # conservative fallback
        return path_depth_from_base(home_suffix, pwd_suffix)

    def could_equal_literal(field: Field, literal: str) -> bool:
        field_str = field.try_to_str()
        if field_str is not None:
            return field_str == literal

        if field.count.min > 1 or field.count.max < 1:
            return False

        match field.content:
            case CompletelyArbitrary(prefix=prefix, suffix=suffix):
                if prefix is not None and (prefix_str := prefix.try_to_str()) is not None and not literal.startswith(prefix_str):
                    return False
                if suffix is not None and (suffix_str := suffix.try_to_str()) is not None and not literal.endswith(suffix_str):
                    return False
                return True
            case _:
                return True

    at_pwd_init = pwdval is not None and start_pwdval is not None and same_location(pwdval.as_field(), start_pwdval.as_field())
    home_level = home_depth(pwdval.as_field(), homeval.as_field()) if (pwdval is not None and homeval is not None) else None
    at_home_top_level = home_level is not None and home_level <= 1
    # TODO: Replace this heuristic with a proper "current working directory" abstraction independent of env-field shape.
    if (
        (at_pwd_init or at_home_top_level)
        and any(arg.try_to_str() == "*" for arg in non_flag_args)
    ):
        Reporter.add_issue(reporter.DeleteSystemFile(pwdval.try_to_str() or "PWD", context_line), config)

    if non_flag_args:
        if start_pwdval is not None:
            normalized_start_pwd = normalize_path_field(start_pwdval.as_field().quote())
            normalized_args = tuple(normalize_path_field(arg_field.quote()) for arg_field in non_flag_args)
            trace = trace.extend(lambda s: s.add_assertion(SimpleConstraint(And.from_field_iter(normalized_args,
                                                                                                lambda arg_field: Not(StringEq(arg_field, normalized_start_pwd))),
                                                                            lambda line: reporter.DeleteSystemFile("Init PWD", line)),
                                                        node.pretty(),
                                                        context_line, priority=11, include_fs=False))

    protected_paths = Config.get("PROTECTED_PATHS")
    if protected_paths:
        protected_checks = tuple(
            (
                And.from_field_iter(
                    tuple(arg_field for arg_field in non_flag_args if could_equal_literal(arg_field, path)),
                    lambda arg_field, p=path: Not(
                        StringEq(arg_field, Field(SymStr((p,)), WordCount(1, 1)))
                    ),
                ),
                lambda line, p=path: reporter.DeleteSystemFile(p, line),
            )
            for path in protected_paths
            if any(could_equal_literal(arg_field, path) for arg_field in non_flag_args)
        )
        if protected_checks:
            trace = trace.extend(
                lambda s: s.add_assertion(
                    SimpleConstraint(*protected_checks[0]) if len(protected_checks) == 1 else RefineableConstraint(
                        And.from_iter(check for check, _ in protected_checks),
                        protected_checks,
                    ),
                    node.pretty(),
                    context_line,
                    priority=10,
                    include_fs=False,
                )
            )


    for arg_idx, arg_field in enumerate(expanded_args[1:], start=1):
        if util.is_flag(arg_field):
            continue
        definitely_non_empty = util.is_definitely_non_empty(arg_field, trace)
        logging.debug("arg %d: %s, definitely_non_empty=%s", arg_idx, arg_field, definitely_non_empty)
        if path := arg_field.try_to_str():
            if util.is_protected(path):
                Reporter.add_issue(reporter.DeleteSystemFile(path, context_line), config)
            if util.is_user_directory(path):
                Reporter.add_issue(reporter.DeleteUserDirectory(path, context_line), config)

        def maybe_report_protected_split(content: CompletelyArbitrary, max_words: int | float) -> None:
            if content.maybe_empty and content.quoted and not definitely_non_empty:
                # "<pre>$VAR<post>" but $VAR could be empty
                exp = ""
                if content.prefix is not None and (pre := content.prefix.try_to_str()):
                    exp += pre
                if content.suffix is not None and (suf := content.suffix.try_to_str()):
                    exp += suf
                if util.is_protected(exp):
                    Reporter.add_issue(reporter.WordSplitCouldDeleteSystemFile(exp, context_line), config)

            if max_words > 1 and not content.quoted:
                if content.prefix is not None and (pre := content.prefix.try_to_str()) and util.is_protected(pre):
                    Reporter.add_issue(reporter.WordSplitCouldDeleteSystemFile(pre, context_line), config)
                if content.suffix is not None and (suf := content.suffix.try_to_str()) and util.is_protected(suf):
                    Reporter.add_issue(reporter.WordSplitCouldDeleteSystemFile(suf, context_line), config)

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


def handle_find(traces: Traces,
                node: AST.CommandNode,
                expanded_args: list[Field],
                config: InterpConfig) -> Traces:
    arg_strs = [f.try_to_str() for f in expanded_args]
    if any(arg is None for arg in arg_strs):
        return handle_unknown_command("find", expanded_args[1:], traces, config)

    args = [arg for arg in arg_strs if isinstance(arg, str)]

    # Parse starting points: positional args before the first expression term.
    search_roots: list[str] = []
    for tok in args[1:]:
        if tok.startswith("-") or tok in {"!", "(", ")"}:
            break
        search_roots.append(tok)
    if not search_roots:
        search_roots = ["."]

    def literal_argchars(s: str) -> list[AST.ArgChar]:
        return [AST.CArgChar(ord(ch)) for ch in s]

    def symbolic_suffix_argchars(prefix: str) -> list[AST.ArgChar]:
        # quoted command substitution -> one symbolic path segment
        return literal_argchars(prefix) + [AST.QArgChar([AST.BArgChar(AST.CommandNode(node.line_number, [], [], []))])]

    def path_variants(root: str) -> list[list[AST.ArgChar]]:
        root_with_slash = root if root.endswith("/") else f"{root}/"
        variants = [
            literal_argchars(root),
            symbolic_suffix_argchars(root),
            symbolic_suffix_argchars(root_with_slash),
        ]
        deduped: list[list[AST.ArgChar]] = []
        seen = set()
        for argchars in variants:
            key = tuple(ch.pretty() for ch in argchars)
            if key not in seen:
                seen.add(key)
                deduped.append(argchars)
        return deduped

    generated_cmd_traces: Traces = []

    # Approximate `find ... -exec cmd ... {} ... \;` by unrolling into synthetic command nodes.
    # TODO: model find's expression language and -exec argument expansion soundly.
    i = 1
    while i < len(args):
        if args[i] == "-exec" and i + 1 < len(args):
            j = i + 1
            exec_args: list[str] = []
            has_placeholder = False
            while j < len(args) and args[j] not in {";", "\\;", "+"}:
                if args[j] == "{}":
                    has_placeholder = True
                j += 1
                exec_args.append(args[j - 1])

            if j < len(args) and has_placeholder and exec_args:
                for root in search_roots:
                    for placeholder_arg in path_variants(root):
                        mangled_cmdnode = deepcopy(node)
                        mangled_cmdnode.arguments = []
                        mangled_cmdnode.assignments = []
                        mangled_cmdnode.redir_list = []
                        for tok in exec_args:
                            if tok == "{}":
                                mangled_cmdnode.arguments.append(deepcopy(placeholder_arg))
                            else:
                                mangled_cmdnode.arguments.append(literal_argchars(tok))
                        generated_cmd_traces.extend(handle_commandnode(traces, mangled_cmdnode, config))
                i = j
        i += 1

    if generated_cmd_traces:
        return generated_cmd_traces
    return handle_unknown_command("find", expanded_args[1:], traces, config)


def handle_shift(traces: Traces,
                node: AST.CommandNode,
                expanded_args: list[Field],
                config: InterpConfig) -> Traces:
    assert expanded_args, "the first argument should be 'shift'"
    if len(expanded_args) == 1:
        t = trace_map(traces, lambda s: s.incr_shift_offset(1))
    else:
        if (off := expanded_args[1].try_to_int()) is not None:
            t = trace_map(traces, lambda s: s.incr_shift_offset(off))
        else:
            # Give up
            t = traces
    return t


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
                if config.in_checked_position or config.force_fork_all:
                    t_success = trace_map(traces, lambda s: s.set_last_exit_code(SymStr(("0",)), Confidence.SPECULATIVE))
                    t_failure = trace_map(traces, lambda s: s.set_last_exit_code(SymStr(("1",)), Confidence.SPECULATIVE))
                    return t_success + t_failure

                logging.debug("Ignoring function call to %s (configured as no-op)", func_name)
                return traces
            the_func = func_defs.pop()
            assert isinstance(the_func, FrozenAst)
            return handle_function_call(func_name, the_func.ast, arg_fields, traces, config)
    else:
        logging.debug("Name %s is defined as different functions across traces, giving up on this call", func_name)
        return traces


def handle_unknown_command(name: str,
                           arg_fields: list[Field],
                           traces: Traces,
                           config: InterpConfig) -> Traces:
    if name in func_map.funcs.keys():
        Reporter.add_issue(reporter.UndefinedFunction(name, context_line), config)

    if name.endswith("/") \
       or any(name in t.latest_state.known_nonexistent_commands for t in traces):
        Reporter.add_issue(reporter.NotACommand(name, context_line), config)

    logging.debug("Unknown command %s, optimistically treating as no-op", name) # that reads its operands", name)
    # mark all args as being read
    #t = trace_map(traces, lambda s: s.update_fs(And.from_field_iter(arg_fields, lambda f: IsFile(f) >> IsRead(f))))
    #return t
    # this makes execution significantly slower, so for now leave it commented out
    if config.in_checked_position or config.force_fork_all:
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
        if config.in_checked_position or config.force_fork_all:
            t_success = trace_map(traces, lambda s: s.set_last_exit_code(SymStr(("0",)), Confidence.SPECULATIVE))
            t_failure = trace_map(traces, lambda s: s.set_last_exit_code(SymStr(("1",)), Confidence.SPECULATIVE))
            return t_success + t_failure

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
            localenv[str(i + 1)] = ShellVar(PreSplitWord.from_field(arg))
        else:
            logging.debug("Function argument %d is not guaranteed to be a single word, giving up on positional parameters (%s)", i, arg)
            break
    logging.debug("Bound localenv for call: %s", localenv)
    t1 = []
    for t in traces:
        if name in t.latest_state.call_stack:
            logging.error("Found recursive function definition! %s via %s", name, t.latest_state.call_stack)
            return traces
        t1.append(t.extend(lambda s: s.enter_function(name, localenv)))
    call_result_traces = guarded_interp_node(t1, func_node.body, config)
    return [t.extend(lambda s: s.set_returning(False).exit_function()) for t in call_result_traces]


def record_assignment(trace: Trace, var: str, rhs: PreSplitWord, definite_confidence: bool = True) -> Trace:
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
        selection = config.branch_policy_pre(node)
        t_true = [t for t in t1 if t.latest_state.last_exit_code[0] == SymStr(("0",))]
        t_false = [t for t in t1 if t.latest_state.last_exit_code[0] == SymStr(("1",))]
        t_other = [t for t in t1 if t.latest_state.last_exit_code[0] not in {SymStr(("0",)), SymStr(("1",))}]
        t_true = t_true + trace_map(t_other, lambda s: s.set_last_exit_code(SymStr(("0",)), Confidence.SPECULATIVE))
        t_false = t_false + trace_map(t_other, lambda s: s.set_last_exit_code(SymStr(("1",)), Confidence.SPECULATIVE))
        if selection.decision == BranchDecision.FIRST:
            logging.debug("While loop single-path decision: take body once")
            t_body = guarded_interp_node(t_true, node.body, config)
            t_body, broken = consume_break_traces(t_body)
            t_body, continued, propagated_continue = consume_continue_traces(t_body)
            return t_body + broken + continued + propagated_continue
        logging.debug("While loop single-path decision: skip body")
        return t_false
    # Special case: never runs
    if len(test_cmds) > 0 and interpret_test(test_cmds[0]) == False:
        logging.debug("While loop never runs")
        return t1

    t1 = [t for t in t1 if t.latest_state.last_exit_code != (SymStr(("1",)), Confidence.DEFINITE)]
    t_skip_body = [t for t in traces if t.latest_state.last_exit_code == (SymStr(("1",)), Confidence.DEFINITE)]
    t2 = guarded_interp_node(t1, node.body, config)
    t2, break_exit = consume_break_traces(t2)
    t2, continue_next_iter, continue_exit = consume_continue_traces(t2)
    t2 = t2 + continue_next_iter
    t_skip_body = t_skip_body + break_exit + continue_exit


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
    t4, break_exit = consume_break_traces(t4)
    t4, continue_next_iter, continue_exit = consume_continue_traces(t4)
    t4 = t4 + continue_next_iter
    t_skip_body = t_skip_body + break_exit + continue_exit


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


def interpret_test(cmd: list[Field]) -> bool | None:
    """Return true or false if `cmd` is a test that always returns either of the two results. Return None if unknown."""

    def definitely_empty(field: Field) -> bool:
        return field.try_to_str() == ""

    def definitely_non_empty(field: Field) -> bool:
        return (s := field.try_to_str()) is not None and s != ""

    def definitely_not_equal(f1: Field, f2: Field) -> bool:
        return (s1 := f1.try_to_str()) is not None and (s2 := f2.try_to_str()) is not None and s1 != s2

    if not cmd or not is_test(cmd[0]):
        return None

    args = cmd[1:]
    if (cmd[0].content, cmd[-1].content) == (SymStr(("[",)), SymStr(("]",))):
        args = args[:-1]

    negated: bool = False
    while args and args[0] == SymStr(("!",)):
        negated ^= True
        args = args[1:]

    result: bool | None = None
    match args:
        # Empty test has exit code 1
        case []: result = False
        case [s]:
            if definitely_non_empty(s): result = True
            elif definitely_empty(s): result = False
        case [op, s]:
            match op.content:
                case SymStr(("-n",)):
                    if definitely_non_empty(s): result = True
                    elif definitely_empty(s): result = False
                case SymStr(("-z",)):
                    if definitely_non_empty(s): result = False
                    elif definitely_empty(s): result = True
                case SymStr((op,)) if op in ("-f", "-d", "-e"):
                    if definitely_empty(s): result = False
        case [s1, op, s2]:
            num_ops = {
                "-eq": lambda a, b: a == b,
                "-ne": lambda a, b: a != b,
                "-lt": lambda a, b: a < b,
                "-gt": lambda a, b: a > b,
                "-le": lambda a, b: a <= b,
                "-ge": lambda a, b: a >= b,
            }
            match op.content:
                case SymStr(("=",)):
                    if s1 == s2: result = True
                    elif definitely_not_equal(s1, s2): result = False
                case SymStr(("!=",)):
                    if s1 == s2: result = False
                    elif definitely_not_equal(s1, s2): result = True
                case SymStr((op,)) if op in num_ops.keys():
                    if (n1 := s1.try_to_int()) is not None and (n2 := s2.try_to_int()) is not None:
                        result = num_ops[op](n1, n2)
        case _:
            # At this point give up, the command likely contains && and || expressions
            pass

    if result is not None:
        result ^= negated # xor

    return result


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
                elif flag.startswith("+"):
                    raise NotImplementedError(f"set: option unsetting not implemented: {expanded_args}")
                else:
                    logging.debug("set: ignoring non-option argument: %s", flag)
                    pass # It's probably pipefail tbh
            case _:
                raise NotImplementedError(f"set with non-constant args: {expanded_args}")
    return trace_map(traces, lambda s: s.set_options(to_set))


def handle_unset(expanded_args: list[Field], traces: Traces) -> Traces:
    vars_to_unset: list[str] = []
    for arg in expanded_args[1:]:
        var_name = arg.try_to_str()
        if not var_name:
            raise NotImplementedError(f"unset with non-constant args: {expanded_args}")
        vars_to_unset.append(var_name)

    empty = PreSplitWord([ExpandedChunk(content="", is_quoted=False, count=WordCount(0, 0))])

    def apply_unset(state: State) -> State:
        updated = state
        for var_name in vars_to_unset:
            # Model unset as a ghost empty variable so expansions treat it as unset-like.
            updated = updated.set_env(var_name, ShellVar(empty, ghost=True))
        return updated

    return trace_map(traces, apply_unset)


def handle_if(traces: Traces, node: AST.IfNode, config: InterpConfig) -> Traces:
    test_line_number = context_line
    test_cmds: list[list[Field]] = []
    def get_the_test(cmd_fields: list[Field]):
        nonlocal test_line_number
        test_line_number = context_line
        test_cmds.append(cmd_fields)
    temp_config = config.add_expanded_command_callback(get_the_test)
    temp_config = replace(temp_config, in_checked_position=True)

    # Execute the test commands individually for each trace
    res: list[tuple[Traces, list[Field]]] = []
    for i, t in enumerate(traces):
        logging.debug("If handler: interpreting test commands for trace %i/%d", i, len(traces))
        logging.debug("collected test_cmds: %s", test_cmds)
        t = guarded_interp_node([t], node.cond, temp_config)
        if len(test_cmds) > 0:
            res.append((t, test_cmds[-1]))
            test_cmds = []

    test_results = set()
    then_traces, else_traces, both_traces = [], [], []
    for i, (ts1, test_cmd) in enumerate(res):
        logging.debug("If handler: checking for const cond for traces %i/%d", i, len(traces))
        if len(test_cmd) == 0:
            logging.debug("Failed to collect any test commands? Giving up on constant condition check.")
            test_result = None
        else:
            logging.debug("Checking if test command %s is constant true/false", test_cmd)
            test_result = interpret_test(test_cmd)
            logging.debug("Test command result: %s", test_result)

        test_results.add(test_result)
        if test_result is not None:
            #Reporter.add_issue(reporter.ConstantCondition(test_cmd, test_line_number), config)
            if test_result == True:
                ts2 = trace_map(ts1, lambda s: s.set_last_exit_code(SymStr(("0",)), Confidence.DEFINITE))
                then_traces.extend(ts2)
            elif test_result == False:
                ts2 = trace_map(ts1, lambda s: s.set_last_exit_code(SymStr(("1",)), Confidence.DEFINITE))
                else_traces.extend(ts2)
        else:
            # Test result is None
            logging.debug("FORK: explicit if")
            ts2 = trace_map
            both_traces.extend(ts1)

    if len(test_results) == 1 and any(b in test_results for b in (True, False)):
        Reporter.add_issue(reporter.ConstantCondition(res[0][1], test_line_number), config)
        if then_traces:
            assert not both_traces, "test was constant across all traces"
            if node.else_b is not None and node.else_b.pretty():
                                           # Hack because libdash sometimes gives empty else bodies
                logging.debug("Reporting dead code in else branch.")
                Reporter.add_issue(reporter.DeadCode(node.else_b, test_line_number), config)
        elif else_traces:
            assert not both_traces, "test was constant across all traces"
            logging.debug("Reporting dead code in then branch")
            Reporter.add_issue(reporter.DeadCode(node.then_b, test_line_number), config)
        else:
            raise AssertionError("unreachable")

    # Several possibilities here:
    # 1. Constant test true -- interpret then_b and return that
    # 2. Constant test false with no else -- just return t1
    # 3. Constant test false with else -- interpret else_b and return that
    # 4. Non-constant test -- interpret both branches and combine results
    if len(test_results) == 1 and True in test_results:
        return guarded_interp_node(then_traces, node.then_b, config)
    elif len(test_results) == 1 and False in test_results:
        if node.else_b is not None:
            return guarded_interp_node(else_traces, node.else_b, config)
        else:
            return else_traces
    else:
        if config.branch_policy_pre is not None:
            selection = config.branch_policy_pre(node)
            logging.debug("If statement single-path decision: %s", selection)
            if selection.decision == BranchDecision.FIRST:
                return guarded_interp_node(then_traces + both_traces, node.then_b, config)
            if selection.decision == BranchDecision.SECOND:
                if node.else_b is not None:
                    return guarded_interp_node(else_traces + both_traces, node.else_b, config)
                return else_traces + both_traces
        return handle_branch(then_traces + else_traces + both_traces,
                            lambda ts: guarded_interp_node(ts, node.then_b, config),
                            lambda fs: guarded_interp_node(fs, node.else_b, config) if node.else_b is not None else fs,
                            node,
                            config)


def handle_exit(traces: Traces) -> Traces:
    logging.debug("Handling exit command, terminating %d traces", len(traces))
    return trace_map(traces, lambda s: s.terminate())


def handle_return(traces: Traces, retval: str | None) -> Traces:
    logging.debug("Handling return command")
    # TODO: handle return value properly
    return trace_map(traces, lambda s: s.set_returning(True) if s.is_in_function() else s)


def handle_break(traces: Traces, expanded_args: list[Field]) -> Traces:
    level = _parse_loop_control_level(expanded_args)
    logging.debug("Handling break command with level %d", level)
    return trace_map(traces, lambda s: s.set_break_level(level))


def handle_continue(traces: Traces, expanded_args: list[Field]) -> Traces:
    level = _parse_loop_control_level(expanded_args)
    logging.debug("Handling continue command with level %d", level)
    return trace_map(traces, lambda s: s.set_continue_level(level))


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
            curr_trace = record_assignment(
                curr_trace,
                var_name,
                PreSplitWord.from_field(arbitrary_field(node, ArbitraryType.ENVIRONMENT, curr_trace.latest_state)),
            )
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
            logging.debug("Ignoring unsupported xargs invocation: %s", node.pretty())
            return traces


def handle_eval(traces: Traces,
                node: AST.CommandNode,
                expanded_args: list[Field],
                config: InterpConfig) -> Traces:
    if len(expanded_args) <= 1:
        return trace_map(traces, lambda s: s.set_last_exit_code(SymStr(("0",)), Confidence.DEFINITE))

    eval_parts: list[str] = []
    for field in expanded_args[1:]:
        part = field.try_to_str()
        if part is None:
            return handle_unknown_command("eval", expanded_args[1:], traces, config)
        eval_parts.append(part)

    eval_script = " ".join(eval_parts)
    if eval_script.strip() == "":
        return trace_map(traces, lambda s: s.set_last_exit_code(SymStr(("0",)), Confidence.DEFINITE))

    temp_path = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as temp_file:
            temp_path = temp_file.name
            temp_file.write(eval_script)
            if not eval_script.endswith("\n"):
                temp_file.write("\n")
        parsed_nodes = parser.parse_shell_script(temp_path)
    except Exception:
        logging.debug("Failed to parse constant eval payload; falling back to unknown eval handling", exc_info=True)
        return handle_unknown_command("eval", expanded_args[1:], traces, config)
    finally:
        if temp_path is not None:
            try:
                os.unlink(temp_path)
            except OSError:
                pass

    t = traces
    for wrapped_node in parsed_nodes:
        if isinstance(wrapped_node.ast_node, AST.Command):
            t = guarded_interp_node(t, wrapped_node.ast_node, config)
    return t


def handle_case(traces: Traces, node: AST.CaseNode, config: InterpConfig) -> Traces:
    t1, case_arg_fields = expand_args_dumb(traces, [node.argument], config)

    cases_to_run = list(node.cases)
    # if config.branch_policy_pre is not None:
    #     selection = config.branch_policy_pre(node)
    #     if selection.case_index is not None:
    #         if 0 <= selection.case_index < len(node.cases):
    #             cases_to_run = [node.cases[selection.case_index]]
    #         else:
    #             cases_to_run = []
    #     elif selection.decision == BranchDecision.FIRST:
    #         cases_to_run = node.cases[:1]
    #     elif selection.decision == BranchDecision.SECOND:
    #         cases_to_run = node.cases[1:2]

    res = []
    for case in cases_to_run:
        logging.debug("FORK: explicit case")
        # todo handle patterns; this is like a conditional, we could learn something about pathcond here
        res.extend(guarded_interp_node(trace_map(t1, lambda s: s.add_pathcond(Description(f"case_L{context_line}_pattern_{case['cpattern']}:matched"))),
                                        case["cbody"],
                                        config))
    return res


def handle_assign_node(traces: Traces, node: AST.AssignNode, config: InterpConfig) -> Traces:
    # The expand() function is used everywhere where expansion is needed.
    # For that reason, if a quoted argument is passed in, the resulting Field will always contain a maximum word count of 1.
    # If that weren't the case, the following command would be interpreted wrongly: cp "filename with spaces" dest.
    # However, in the context of assignments, we want the resulting Field to have the correct word count, even if the argument is quoted.
    # A simple way to achieve this is to unquote the argument before passing it to expand().

    # "if the value of the node is a single quoted argument, remove the quotes"

    val = node.val[0].arg if len(node.val) == 1 and isinstance(node.val[0], AST.QArgChar) else node.val

    trace_expansion_pairs = expand_to_word(traces, val, config)

    # If the assignment contains a command substitution do not set exit code to 0 with definite confidence
    assignment_definitely_succeeds = not any(isinstance(ac, AST.BArgChar) for ac in util.iter_argchar_list(node.val, [AST.AArgChar]))
    return [record_assignment(t, node.var, word, assignment_definitely_succeeds) for (t, word) in trace_expansion_pairs]


def handle_semi_node(traces: Traces, node: AST.SemiNode, config: InterpConfig) -> Traces:
    t2 = guarded_interp_node(traces, node.left_operand, config)
    return guarded_interp_node(t2, node.right_operand, config)


def handle_for_node(traces: Traces, node: AST.ForNode, config: InterpConfig) -> Traces:
    t0, var_name = expand_assuming_single_constant_word(traces, node.variable, config)
    t1, items = expand_args_dumb(t0, node.argument, config)
    if join_fields(items).count.max <= 1:
        Reporter.add_issue(reporter.LoopRunsOnce(node, context_line), config)
    # if all items are constant, we can unroll the loop
    logging.debug("For loop items: %s", items)
    if all(field.is_constant() for field in items):
        # Word-split constant items within the for-loop list (e.g. $VAR="a b")
        split_items: list[Field] = []
        for item in items:
            item_str = item.try_to_str()
            IFS = " \t\n" # TODO: Grab this from the config
            if item_str is not None and any(ch in IFS for ch in item_str):
                split_items.extend(Field(SymStr((word,)), WordCount(1, 1)) for word in item_str.split())
                continue
            split_items.append(item)
        items = split_items
        logging.debug("For loop over constant items, unrolling: %s", items)
        t2 = t1
        exited: Traces = []
        for item_field in items:
            t2 = [record_assignment(t, var_name, PreSplitWord.from_field(item_field)) for t in t2]
            t2 = guarded_interp_node(t2, node.body, config)
            t2, break_exit = consume_break_traces(t2)
            t2, continue_next_iter, continue_exit = consume_continue_traces(t2)
            exited.extend(break_exit + continue_exit)
            t2 = t2 + continue_next_iter
        return t2 + exited
    else:
        t_current = t1
        exited: Traces = []
        for i in range(config.max_loop_unroll):
            logging.debug("For loop unrolling iteration %d/%d", i+1, config.max_loop_unroll)
            t2 = [record_assignment(t, var_name, PreSplitWord.from_field(arbitrary_field(node.variable,
                                                                ArbitraryType.APPROXIMATION,
                                                                t.latest_state))) \
                for t in t_current]
            t_current = guarded_interp_node(t2, node.body, config)
            t_current, break_exit = consume_break_traces(t_current)
            t_current, continue_next_iter, continue_exit = consume_continue_traces(t_current)
            exited.extend(break_exit + continue_exit)
            t_current = t_current + continue_next_iter
        return t_current + exited


def handle_file_redir_node(traces: Traces, node: AST.FileRedirNode, config: InterpConfig) -> Traces:
    res = []
    for t, redir_args in expand(traces, node.arg, config):
        # If the redir_arg is already known to be a safe path to overwrite, we don't need to add any assertions
        if all(redir_arg.is_constant() and \
            redir_arg.content.try_to_str() in Config.get("SAFE_OVERWRITE_PATHS") \
            for redir_arg in redir_args):
            res.append(t)
            continue

        t_precond = t
        if node.redir_type in ["To", "Clobber"]: # >, >|
            safe_paths = Config.get("SAFE_OVERWRITE_PATHS")
            def not_safe_path(op: Field) -> Constraint:
                return And.from_iter(Not(StringEq(op, Field.create_constant(p, 1))) for p in safe_paths)

            assertion_constraint = SimpleConstraint(And.from_field_iter(redir_args, lambda op: Implies(not_safe_path(op), IsRead(op) | IsDeleted(op))),
                                                    lambda line: reporter.DataLoss(node.pretty(), redir_args, line))
            if assertion_constraint:
                t_precond = t.extend(t.latest_state.add_assertion(
                    assertion_constraint,
                    source_str=node.pretty(),
                    source_line=context_line
                ))
                DebugLogger.log_assertion(assertion_constraint, t.latest_state, context_line, config.current_pass)
            else:
                t_precond = t
            t_postcond = t_precond.extend(t_precond.latest_state.update_fs(And.from_field_iter(redir_args, IsFile)))

        elif node.redir_type == "Append": # >>
            # NOTE: asserting IsFile also implicitly asserts that the file is *unread*
            assertion_constraint = SimpleConstraint(And.from_field_iter(redir_args, lambda op: ~IsDir(op)),
                                                    lambda line: reporter.ExpectedPathState(node.pretty(), 'non-directories', redir_args, line))
            if assertion_constraint:
                t_precond = t.extend(t.latest_state.add_assertion(assertion_constraint, source_str=node.pretty(), source_line=context_line))
                DebugLogger.log_assertion(assertion_constraint, t.latest_state, context_line, config.current_pass)
            else:
                t_precond = t
            t_postcond = t_precond.extend(t_precond.latest_state.update_fs(And.from_field_iter(redir_args, IsFile)))

        elif node.redir_type == "From": # <
            # The targets of the redirection were read from
            assertion_constraint = SimpleConstraint(And.from_field_iter(redir_args, IsFile),
                                                    lambda line: reporter.ExpectedPathState(node.pretty(), 'files', redir_args, line))
            if assertion_constraint:
                t_precond = t.extend(t.latest_state.add_assertion(assertion_constraint, source_str=node.pretty(), source_line=context_line))
                DebugLogger.log_assertion(assertion_constraint, t.latest_state, context_line, config.current_pass)
            else:
                t_precond = t
            t_postcond = t_precond.extend(t_precond.latest_state.update_fs(And.from_field_iter(redir_args, IsRead)))

        elif node.redir_type == "FromTo":
            # Conservatively assume the file is opened for reading
            assertion_constraint = SimpleConstraint(And.from_field_iter(redir_args, lambda op: ~IsDir(op)),
                                                    lambda line: reporter.ExpectedPathState(node.pretty(), 'non-directories', redir_args, line))
            if assertion_constraint:
                t_precond = t.extend(t.latest_state.add_assertion(assertion_constraint, source_str=node.pretty(), source_line=context_line))
                DebugLogger.log_assertion(assertion_constraint, t.latest_state, context_line, config.current_pass)
            else:
                t_precond = t
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
                logging.debug("Found a redir to multiple words: %s - Ignoring.", trim_string_for_logging(str(redir_args)))
                pass
    return res


def handle_redir_node(traces: Traces, node: AST.RedirNode, config: InterpConfig) -> Traces:
    t1 = guarded_interp_node(traces, node.node, config)
    t2 = t1
    for redir in node.redir_list:
        t2 = guarded_interp_node(t2, redir, config)
    return t2


def handle_background_node(traces: Traces, node: AST.BackgroundNode, config: InterpConfig) -> Traces:
    # Approximate background execution as ordinary sequential execution.
    # This preserves issue finding/coverage without modeling concurrency.
    t1 = guarded_interp_node(traces, node.node, config)
    t2 = t1
    for redir in node.redir_list:
        t2 = guarded_interp_node(t2, redir, config)
    if node.after_ampersand is not None:
        t2 = guarded_interp_node(t2, node.after_ampersand, config)
    return t2


def handle_defun_node(traces: Traces, node: AST.DefunNode, config: InterpConfig) -> Traces:
    # Note: the type annotation in the Shasta source code is *wrong* for node.name -- it's a string
    t1, name = expand_assuming_single_constant_word(traces, node.name, config)
    return trace_map(t1, lambda s: s.set_fundef(name, freeze(node)))


def handle_and_or_node(traces: Traces, node: AST.AndNode | AST.OrNode, config: InterpConfig) -> Traces:
    logging.debug("FORK: explicit AND/OR")
    # Workaround the `checked_position` by manually adding the failure traces back only when
    # the left operand was *not* evaluated in a checked position.
    left_config = config if config.in_checked_position else replace(config, in_checked_position=False)
    right_config = config
    t1 = guarded_interp_node(traces, node.left_operand, left_config)
    t_failure: Traces = []
    if not left_config.in_checked_position:
        t_failure = [t.fail_last_command() for t in t1 if t.latest_state.last_exit_code[0] == SymStr(("0",))]
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


def handle_not_node(traces: Traces, node: AST.NotNode, config: InterpConfig) -> Traces:
    t1 = guarded_interp_node(traces, node.body, config)
    t2 = trace_map(t1,
                lambda s: s if s.last_exit_code[0] not in {SymStr(("0",)), SymStr(("1",))}
                            else s.set_last_exit_code(SymStr(("1",)) if s.last_exit_code[0] == SymStr(("0",)) else SymStr(("0",)),
                                                        s.last_exit_code[1]))
    return t2


def handle_subshell_node(traces: Traces, node: AST.SubshellNode, config: InterpConfig) -> Traces:
    # # A subshell executes its body but does not persist shell-local state
    # # changes (variables, function definitions, options, etc.) back to the
    # # parent shell. Keep side effects like fs/assertions/path conditions.
    res: Traces = []
    for parent_trace in traces:
        parent_state = parent_trace.latest_state
        sub_traces = guarded_interp_node([parent_trace], node.body, config)
        for sub_trace in sub_traces:
            res.append(
                sub_trace.extend(
                    lambda s, p=parent_state: replace(
                        s,
                        env=p.env,
                        localenv=p.localenv,
                        call_stack=p.call_stack,
                        fundefs=p.fundefs,
                        opts=p.opts,
                        known_nonexistent_commands=p.known_nonexistent_commands,
                        known_existing_commands=p.known_existing_commands,
                        terminated=p.terminated,
                    )
                )
            )
    mark_subtree_as_interpreted_for_coverage(node.body)
    return res


def handle_pipe_node(traces: Traces, node: AST.PipeNode, config: InterpConfig) -> Traces:
    # Since variable assignments from parameter expansion, such as `${var:=default}`, in pipeline commands
    # should not persist beyond the pipeline, save the environment before the pipeline and restore it after.
    saved_envs = [(t.latest_state.env, t.latest_state.localenv) for t in traces]
    t = traces
    # Sequentially interpret each command in the pipeline, and return the aggregated traces.
    for i, cmd in enumerate(node.items):
        if (
            i > 0
            and isinstance(cmd, AST.CommandNode)
            and isinstance(node.items[i - 1], AST.CommandNode)
        ):
            lhs = node.items[i - 1]
            rhs = cmd
            if _node_invocation_has_no_stdout(lhs) and _node_invocation_expects_stdin(rhs):
                Reporter.add_issue(reporter.UnexpectedStdin(_node_invocation_name(rhs), context_line), config)
        t = guarded_interp_node(t, cmd, config)
    # Since traces can fork and merge, we need to match traces back to their original saved environments.
    # Thus, restore the environment of each trace to the environment of the first trace that matches its current state.
    restored_traces = []
    for trace in t:
        saved_env, saved_localenv = saved_envs[0] if saved_envs else (trace.latest_state.env, trace.latest_state.localenv)
        restored_traces.append(trace.extend(lambda s, env=saved_env, localenv=saved_localenv:
                                            replace(s, env=env, localenv=localenv)))
    return restored_traces


def guarded_interp_node(traces: Traces,
                        node: AST.AstNode,
                        config: InterpConfig) -> Traces:
    global stop_event
    global context_line
    global breaking_trace_stash
    global continuing_trace_stash
    if stop_event and stop_event.is_set():
        logging.info("Symbolic execution interrupted by stop event")
        Reporter.set_timed_out()
        return traces # same behavior as if the rest of the script is not implemented
        # todo is this sound?

    prev_context_line = context_line
    context_line = getattr(node, "line_number", context_line)
    Reporter.mark_interpreted_ast_node(node)

    traces, inactive1 = drop_terminated_traces(traces)
    if config.disable_trace_collapsing:
        inactive2 = []
    else:
        traces, inactive2 = config.trace_collapser(traces)
    inactive_trace_stash.extend([_compact_trace_history(t) for t in inactive1 + inactive2])
    traces = config.apply_node_cbs(traces, node)

    # Stash any traces that are returning (i.e., have executed a return statement in a function
    # and are waiting to be joined back with their caller) so that they don't interfere with
    # interpretation of the current node; we'll join them back in at the end of the function
    traces, returning = split_returning_traces(traces)
    returning_trace_stash.extend(returning)
    traces, breaking = split_breaking_traces(traces)
    breaking_trace_stash.extend(breaking)
    traces, continuing = split_continuing_traces(traces)
    continuing_trace_stash.extend(continuing)

    res: Traces = []
    if len(traces) > 0:
        res = interp_node(traces, node, config)
        context_line = prev_context_line
    else:
        Reporter.add_issue(reporter.DeadCode(node, context_line), config)
    with_returning = res + returning_trace_stash + breaking_trace_stash + continuing_trace_stash
    returning_trace_stash.clear()
    breaking_trace_stash.clear()
    continuing_trace_stash.clear()
    return with_returning


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

    # fmt: off
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
            # note: local assignments (e.g. LC_ALL=C sort file.txt) are still ignored
            # semantically for command execution, but we do evaluate their RHS
            # expansions (e.g., command substitutions) for issue reporting/coverage.
            t = traces
            for assign in node.assignments:
                assert isinstance(assign, AST.AssignNode)
                Reporter.mark_interpreted_ast_node(assign)
                val = (
                    assign.val[0].arg
                    if len(assign.val) == 1 and isinstance(assign.val[0], AST.QArgChar)
                    else assign.val
                )
                # We intentionally do not persist assignment effects here.
                # Only evaluate expansions to reflect interpretation of RHS nodes.
                # In particular, do not leak expansion-side ghost env bindings
                # (e.g. from unbound variables) into subsequent command state.
                expand(t, val, config)

            return handle_commandnode(t, node, config)

        case AST.AndNode():        return handle_and_or_node(traces, node, config)
        case AST.AssignNode():     return handle_assign_node(traces, node, config)
        case AST.BackgroundNode(): return handle_background_node(traces, node, config)
        case AST.CaseNode():       return handle_case(traces, node, config)
        case AST.DefunNode():      return handle_defun_node(traces, node, config)
        case AST.FileRedirNode():  return handle_file_redir_node(traces, node, config)
        case AST.ForNode():        return handle_for_node(traces, node, config)
        case AST.IfNode():         return handle_if(traces, node, config)
        case AST.NotNode():        return handle_not_node(traces, node, config)
        case AST.OrNode():         return handle_and_or_node(traces, node, config)
        case AST.PipeNode():       return handle_pipe_node(traces, node, config)
        case AST.RedirNode():      return handle_redir_node(traces, node, config)
        case AST.SemiNode():       return handle_semi_node(traces, node, config)
        case AST.SubshellNode():   return handle_subshell_node(traces, node, config)
        case AST.WhileNode():      return handle_while(traces, node, config)

        # todo bring other cases as needed
        case _:
            logging.debug("Unhandled node type '%s'; treating as no-op", node.NodeName)
            return traces
    # fmt: on


def starting_state(fs_model: FSModel | None = None, config: InterpConfig | None = None) -> State:
    # env["IFS"] = ShellVar(" \t\n")
    # for defaultvar in ["HOME", "PWD", "OLDPWD", "PATH"]:
    #     env[defaultvar] = ShellVar(SymStr(util.create_fresh_varname(f"default_{defaultvar}"))
    root = State(fs_model = FSModelSimple(field_to_z3)) if fs_model is None else State(fs_model = fs_model)
    make_ast = lambda var: AST.VArgChar("Normal", False, var, [])
    starter_env = {
        "HOME": ShellVar(PreSplitWord.from_field(arbitrary_field(make_ast("HOME"), ArbitraryType.ENVIRONMENT, root, min_words=1))),
        "PWD": ShellVar(PreSplitWord.from_field(arbitrary_field(make_ast("PWD"), ArbitraryType.ENVIRONMENT, root, min_words=1))),
        "OLDPWD": ShellVar(PreSplitWord.from_field(arbitrary_field(make_ast("OLDPWD"), ArbitraryType.ENVIRONMENT, root, min_words=1))),
        "PATH": ShellVar(PreSplitWord.from_field(arbitrary_field(make_ast("PATH"), ArbitraryType.ENVIRONMENT, root, min_words=1)))
    }
    pwd_init_var = config.pwd_init_var if config is not None else InterpConfig().pwd_init_var
    starter_env[pwd_init_var] = ShellVar(starter_env["PWD"].value, readonly=True, ghost=True)
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
    exception: Exception | None = None


# TODO: make the FS model selection configurable via the `InterpConfig`
def symb_engine(nodes: list[parser.WrappedAst], config: InterpConfig) -> Traces:
    global context_line
    global func_map
    global stop_event
    global inactive_trace_stash
    global returning_trace_stash
    global breaking_trace_stash
    global continuing_trace_stash

    logging.info("Running symb engine with %d raw nodes", len(nodes))
    inactive_trace_stash = []
    returning_trace_stash = []
    breaking_trace_stash = []
    continuing_trace_stash = []
    traces = [Trace((starting_state(config=config),))]

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
        func_traces[name] = guarded_interp_node([Trace((starting_state(config=config),))], node, config)


    return traces + [t for ts in func_traces.values() for t in ts] + inactive_trace_stash


def symbexec_file(file: str,
                  exec_timeout: float,
                  dfs_timeout: float,
                  targeted_dfs_timeout: float,
                  enable_unbound_empty_dfs: bool,
                  config: InterpConfig,
                  stop: Event | None = None) -> SymbexecResult:
    global stop_event
    global trace_count
    global inactive_trace_stash
    global returning_trace_stash
    global context_line

    stop_event = stop
    # Reset cross-run interpreter globals so consecutive analyses in the same
    # process do not influence each other's trace-collapsing behavior.
    trace_count = 1
    inactive_trace_stash = []
    returning_trace_stash = []
    context_line = None

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

        nodes = parser.parse_shell_script(file)
        Reporter.set_ast_nodes_total(count_non_arg_ast_nodes(nodes))
        func_defs = find_func_defs([Trace((starting_state(config=config),))], nodes, config)

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
        dfs_solver_traces = []
        dfs_fallback_traces = []
        if dfs_timeout > 0.0:
            dfs_phase_start = time.perf_counter()
            prev_stop_event = stop_event

            def run_dfs_pass(timeout: float | None, run_pass: Callable[[], Traces]) -> tuple[Traces, bool]:
                global stop_event
                if timeout is None:
                    pass_event = prev_stop_event
                elif timeout <= 0:
                    pass_event = Event()
                    pass_event.set()
                else:
                    pass_event = _set_timer(timeout)
                stop_event = pass_event
                traces = run_pass()
                timed_out = pass_event is not None and pass_event.is_set()
                return traces, timed_out

            dfs_phase_timed_out = False
            targeted_traces = []
            if targeted_dfs_timeout > 0.0:
                logging.info("Running DFS pass: targeting dangerous commands; budget: %.2fs", targeted_dfs_timeout)
                targeted_traces, targeted_timed_out = run_dfs_pass(
                    targeted_dfs_timeout,
                    lambda: run_targeted_dfs(
                        nodes=nodes,
                        config=replace(config, current_pass="dangerous-first"),
                        symb_engine=symb_engine,
                        func_defs=func_defs,
                        ignore_function_calls_for=safe_funcs,
                    ).traces,
                )
                dfs_phase_timed_out = dfs_phase_timed_out or targeted_timed_out

                #return SymbexecResult(SymbexecStatus.COMPLETED, targeted_traces)
            dfs_remaining_timeout = dfs_timeout - (time.perf_counter() - dfs_phase_start)
            dfs_pass_count = 2 + (3 if enable_unbound_empty_dfs else 0)
            dfs_pass_timeout = 0.0
            if dfs_pass_count > 0:
                dfs_pass_timeout = dfs_remaining_timeout / dfs_pass_count

            logging.info("Finished DFS pass: targeting dangerous commands; remaining DFS budget: %.2fs", dfs_remaining_timeout)

            only_then_traces = []
            only_else_traces = []
            only_then_unbound_empty = []
            only_else_unbound_empty = []
            unbound_empty = []
            if dfs_pass_timeout > 0.0:
                logging.info("Running DFS pass: only taking THEN branches")
                only_then_traces, only_then_timed_out = run_dfs_pass(
                    dfs_pass_timeout,
                    lambda: symb_engine(nodes, replace(config,
                                    branch_policy=branch_policy_only_then,
                                    current_pass="conds:then",
                                    current_pass_constraint=Description("(DFS) all then branches are taken"))),
                )
                dfs_phase_timed_out = dfs_phase_timed_out or only_then_timed_out
                logging.info("Running DFS pass: only taking ELSE branches")
                only_else_traces, only_else_timed_out = run_dfs_pass(
                    dfs_pass_timeout,
                    lambda: symb_engine(nodes, replace(config,
                                    branch_policy=branch_policy_only_else,
                                    current_pass="conds:else",
                                    current_pass_constraint=Description("(DFS) all else branches are taken"))),
                )
                dfs_phase_timed_out = dfs_phase_timed_out or only_else_timed_out
                if enable_unbound_empty_dfs:
                    issues_so_far = Reporter._issues.copy()
                    logging.info("Running DFS pass: only taking THEN branches with unbound variables as empty strings")
                    only_then_unbound_empty, then_unbound_timed_out = run_dfs_pass(
                        dfs_pass_timeout,
                        lambda: symb_engine(nodes, replace(config,
                                        branch_policy=branch_policy_only_then,
                                        unbound_policy=UnboundVariablePolicy.EMPTY,
                                        current_pass="unbound:empty+conds:then",
                                        current_pass_constraint=And(Description("(DFS) all then branches are taken"),
                                                                    Description("(DFS) unbound variables are empty")))),
                    )
                    dfs_phase_timed_out = dfs_phase_timed_out or then_unbound_timed_out
                    logging.info("Running DFS pass: only taking ELSE branches with unbound variables as empty strings")
                    only_else_unbound_empty, else_unbound_timed_out = run_dfs_pass(
                        dfs_pass_timeout,
                        lambda: symb_engine(nodes, replace(config,
                                        branch_policy=branch_policy_only_else,
                                        unbound_policy=UnboundVariablePolicy.EMPTY,
                                        current_pass="unbound:empty+conds:else",
                                        current_pass_constraint=And(Description("(DFS) all else branches are taken"),
                                                                    Description("(DFS) unbound variables are empty")))),
                    )
                    dfs_phase_timed_out = dfs_phase_timed_out or else_unbound_timed_out
                    logging.info("Running DFS pass: treating unbound variables solely as empty strings")
                    unbound_empty, unbound_timed_out = run_dfs_pass(
                        dfs_pass_timeout,
                        lambda: symb_engine(nodes, replace(config,
                                        unbound_policy=UnboundVariablePolicy.EMPTY,
                                        current_pass="unbound:empty",
                                        current_pass_constraint=Description("(DFS) unbound variables are empty"))),
                    )
                    dfs_phase_timed_out = dfs_phase_timed_out or unbound_timed_out
                    Reporter.drop_issues({reporter.Code.DELETE_SYSTEM_FILE, reporter.Code.CONSTANT_CONDITION, reporter.Code.COMMAND_CAN_ONLY_FAIL})
                    for i in issues_so_far: # ensure that any del_sys_files found before the last run are kept
                        if i.code == reporter.Code.DELETE_SYSTEM_FILE:
                            Reporter.add_issue(i, config)

            # logging.info("DFS run: exploring the first trace only")
            # symb_engine(nodes, replace(config, trace_collapser = lambda ts: ts[:1]))
            logging.info("DFS passes complete, proceeding with normal symbolic execution")
            dfs_solver_traces = unbound_empty
            dfs_fallback_traces = targeted_traces + \
                                  only_then_traces + only_else_traces + \
                                  only_then_unbound_empty + only_else_unbound_empty
            Reporter.drop_issues({reporter.Code.DEAD_CODE}) # wholly unreliable with branch policies
            if dfs_phase_timed_out:
                Reporter.clear_timed_out()
            if stop is not None and exec_timeout > 0.0:
                main_stop = stop
            else:
                effective_exec_timeout = exec_timeout
                if exec_timeout > 0.0:
                    dfs_elapsed = time.perf_counter() - dfs_phase_start
                    effective_exec_timeout = max(exec_timeout - dfs_elapsed, 0.0)
                    logging.info(
                        "Remaining main symbolic-exec timeout after DFS phase: %.2fs "
                        "(elapsed %.2fs of %.2fs total)",
                        effective_exec_timeout,
                        dfs_elapsed,
                        exec_timeout,
                    )
                if effective_exec_timeout <= 0.0:
                    main_stop = Event()
                    main_stop.set()
                else:
                    main_stop = _set_timer(effective_exec_timeout)
            stop_event = main_stop if main_stop is not None else prev_stop_event
        else:
            main_stop = stop if (stop is not None) else _set_timer(exec_timeout)
            stop_event = main_stop if main_stop is not None else stop_event

        traces = symb_engine(nodes, replace(config, branch_policy=branch_policy_half_n_half_if_too_many))
        logging.info(
            "Symbolic execution completed with %d traces, %d DFS solver traces, and %d DFS fallback traces",
            len(traces),
            len(dfs_solver_traces),
            len(dfs_fallback_traces),
        )
        traces = dfs_solver_traces + traces
        if Reporter.get_timed_out():
            logging.warning("Using fallback DFS traces due to timeout in main symbolic execution")
            traces = dfs_fallback_traces + traces
        # At this point most traces are already unique,
        # so the collapse function ends up taking way too much time for little benefit
        if len(traces) <= 500:
            traces, _ = collapse_traces(traces)
        if Reporter.get_timed_out():
            return SymbexecResult(SymbexecStatus.INTERRUPTED, traces)
        return SymbexecResult(SymbexecStatus.COMPLETED, traces)
    except Exception as e:
        logging.error("Symbolic execution failed:")
        logging.error(traceback.format_exc())
        return SymbexecResult(SymbexecStatus.FAILED, [], exception=e)

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
