import logging
from collections.abc import Callable
from dataclasses import dataclass, field, fields, replace
from enum import Enum
from typing import Any, Optional

import shasta.ast_node as AST

from sash.fs import FSModel, FSModelSimple
from sash.symbolic.strings import Field, PreSplitWord, SymStr, presplit_to_field, presplit_try_to_str
import sash.util as util
from sash.constraints import (
    CommandExists,
    Constraint,
    Empty,
    Implies,
    Not,
    NormalizedConstraint,
)
from sash.frozen import FrozenAst, FrozenDict
from sash.debugtools.logger import DebugLogger

@dataclass(frozen=True)
class ShellVar:
    value: PreSplitWord
    readonly : bool = False
    export : bool = False
    ghost : bool = False # was this variable binding created implicitly by the engine, but has never actually been set?

    def as_field(self) -> Field:
        return presplit_to_field(self.value)

    def try_to_str(self) -> str | None:
        return presplit_try_to_str(self.value)

@dataclass(frozen=True)
class SetOptions:
    NOUNSET = "u"
    NOFAIL = "e"
    current: frozenset[str] = field(default_factory=frozenset)
    def set_options(self, options: set[str]) -> 'SetOptions':
       return SetOptions(current=self.current | options)
    def unset_options(self, options: set[str]) -> 'SetOptions':
       return SetOptions(current=self.current - options)
    def is_set(self, option: str) -> bool:
       return option in self.current

    @classmethod
    def relevant(cls, option: str) -> bool:
        return option.strip("-") not in {"x"}

@dataclass(frozen=True)
class RefineableConstraint:
    full: Constraint
    refinements: tuple[tuple[Constraint,
                             Callable[[int], 'reporter.Issue']],
                       ...]

    def __post_init__(self):
        assert self.full, "empty RefineableConstraints should not be constructed"
        assert len(self.refinements) >= 1, "must have at least one issue"
        match self.refinements:
            case [(c, _)]:
                assert c == Empty(), "single refinement constraint must be empty"

def SimpleConstraint(c: Constraint, issue_maker: Callable[[int], 'reporter.Issue']) -> RefineableConstraint | None:
    if not c:
        return None
    else:
        return RefineableConstraint(c, ((Empty(), issue_maker),))

# <assertion_constraint>: if true, then things are OK, if false then there's a bug
@dataclass(frozen=True)
class Assertion:
    producing_state: "State"
    constraint: RefineableConstraint
    source_str: str
    source_line: int
    priority: int = 0
    include_fs: bool = True

    def __post_init__(self):
        assert isinstance(self.constraint, RefineableConstraint), "Assertion constructed with non-RefineableConstraint"

    # exclude the state from repr to avoid large prints
    def __repr__(self):
        return f"Assertion(state<{hash(self.producing_state)}>, constraint={repr(self.constraint)}, source_str={repr(self.source_str)}, source_line={self.source_line}, priority={self.priority}, include_fs={self.include_fs})"

@dataclass(frozen=True)
class Condition(Assertion):
    constraint: Constraint

    def __repr__(self):
        return f"Condition(state<{hash(self.producing_state)}>, constraint={repr(self.constraint)}, source_str={repr(self.source_str)}, source_line={self.source_line})"

    def __post_init__(self):
        pass # since constraint is just `Constraint`, no need for this


class Confidence(Enum):
    DEFINITE = 0
    SPECULATIVE = 1

@dataclass(frozen=True)
class State:
    pathcond:                    tuple[Condition, ...]       = field(default_factory=tuple)
    env:                         FrozenDict[str, ShellVar]   = field(default_factory=FrozenDict)
    localenv:                    FrozenDict[str, ShellVar]   = field(default_factory=FrozenDict)
    call_stack:                  tuple[str, ...]             = field(default_factory=tuple)
    fundefs:                     FrozenDict[str, FrozenAst]  = field(default_factory=FrozenDict)
    last_exit_code:              tuple[SymStr, Confidence]   = (SymStr(("0",)), Confidence.DEFINITE)
    last_cmd_failure_postcond:   Optional[Constraint]        = None
    opts:                        SetOptions                  = field(default_factory=SetOptions)
    known_nonexistent_commands:  frozenset[str]              = field(default_factory=frozenset)
    known_existing_commands:     frozenset[str]              = field(default_factory=frozenset)
    terminated:                  bool                        = False # by `exit` or similar
    assertions:                  tuple[Assertion, ...]       = field(default_factory=tuple)
    fs_model:                    FSModel                     = field(default_factory=FSModel)
    is_returning:                bool                        = False # whether we're in the process of returning from a function (i.e. have executed a `return` but not yet popped the call stack)
    break_level:                 int                         = 0
    continue_level:              int                         = 0

    external_data: Any = None # ASSUMPTION: must be hashable

    _hash: int = field(default=None, compare=False)
    def __post_init__(self):
        object.__setattr__(
            self,
            "_hash",
            hash(
                tuple(
                    getattr(self, field.name)
                    for field in fields(self)
                    if field.name != "_hash"
                )
            ),
        )

    def __hash__(self):
        return self._hash

    def set_env(self, var: str, value: ShellVar) -> 'State':
        return replace(self, env=self.env.set(var, value))

    def unset_env(self, var: str) -> 'State':
        env = dict(self.env)
        env.pop(var, None)
        return replace(self, env=FrozenDict(env))

    def extend_env(self, new_vars: dict[str, ShellVar]) -> 'State':
        return replace(self, env=(self.env | new_vars))

    def extend_localenv(self, new_vars: dict[str, ShellVar]) -> 'State':
        return replace(self, localenv=(self.localenv | new_vars))

    def add_pathcond(self, cond: Constraint, source_str: str | None = None, source_line: int | None = None) -> 'State':
        if cond == Empty():
            logging.debug("Skipping empty path condition from %s at line %s", source_str, source_line)
            return self
        new_pathcond = self.pathcond + (Condition(self, cond, source_str, source_line),)
        return replace(self, pathcond=new_pathcond)

    def add_assertion(self, assertion_constraint: RefineableConstraint, source_str: str | None = None, source_line: int | None = None, priority: int = 0, include_fs: bool = True) -> 'State':
        assert isinstance(assertion_constraint, RefineableConstraint), f"Got non-RC assertion: {type(assertion_constraint)} : {assertion_constraint}"
        if assertion_constraint == Empty():
            logging.debug("Skipping empty assertion from %s at line %s", source_str, source_line)
            return self
        assertion = Assertion(producing_state=self,
                              constraint=assertion_constraint,
                              source_str=source_str,
                              source_line=source_line,
                              priority=priority,
                              include_fs=include_fs)
        logging.debug(f"Added assertion id: {id(assertion)}")
        new_assertions = self.assertions + (assertion,)
        return replace(self, assertions=new_assertions)

    def lookup(self, var: str) -> ShellVar | None:
        if var in self.localenv:
            return self.localenv[var]
        elif self.call_stack and is_special_var(var):
            # Positional parameters not provided end up referring to the outer context's value for that parameter?? For now, just give up on them inside functions
            return None
        elif var in self.env:
            return self.env[var]
        else:
            return None

    def set_fundef(self, name: str, defn: FrozenAst) -> 'State':
        return replace(self, fundefs=self.fundefs.set(name, defn))

    def lookup_fundef(self, name: str) -> FrozenAst | None:
        return self.fundefs.get(name, None)

    def set_external(self, data) -> 'State':
        return replace(self, external_data=data)

    def set_options(self, options: set[str]) -> 'State':
        return replace(self, opts=self.opts.set_options(options))

    def update_fs(self, constraints: Constraint) -> 'State':
        return replace(self, fs_model=self.fs_model.apply_postcondition(constraints.normalized()))

    def set_last_exit_code(self, code: SymStr, confidence: Confidence, failure_postcond: Optional[Constraint] = None) -> 'State':
        return replace(self,
                       last_exit_code=(code, confidence),
                       last_cmd_failure_postcond=(failure_postcond if failure_postcond is not None else self.last_cmd_failure_postcond))

    def terminate(self) -> 'State':
        return replace(self, terminated=True)

    def record_nonexistent_command(self, name: str) -> 'State':
        return replace(
            self,
            known_nonexistent_commands=self.known_nonexistent_commands | {name},
            known_existing_commands=self.known_existing_commands - {name},
        )

    def remove_nonexistent_command(self, name: str) -> 'State':
        return replace(self, known_nonexistent_commands=self.known_nonexistent_commands - {name})

    def record_existing_command(self, name: str) -> 'State':
        return replace(
            self,
            known_existing_commands=self.known_existing_commands | {name},
            known_nonexistent_commands=self.known_nonexistent_commands - {name},
        )

    def remove_existing_command(self, name: str) -> 'State':
        return replace(self, known_existing_commands=self.known_existing_commands - {name})

    def update_known_commands(self, constraint: Constraint) -> 'State':
        norm_spec = constraint.normalized() # turns ~(a & b) into (~a | ~b), removes double negations, etc.
        if isinstance(norm_spec, NormalizedConstraint):
            norm_spec = norm_spec.constraint

        updated_state = self

        negation = False
        for c in util.iter_constraint(norm_spec, skip=[Implies]): # unclear how to handle command existence in implications (which does not happen anyways now)
            if isinstance(c, Not):
                negation = True
            elif isinstance(c, CommandExists):
                cmd_name = c.name.try_to_str()
                if not cmd_name:
                    negation = False
                    continue
                if negation:
                    updated_state = updated_state.record_nonexistent_command(cmd_name)
                else:
                    updated_state = updated_state.record_existing_command(cmd_name)
                negation = False
            else:
                negation = False

        return updated_state

    def enter_function(self, name: str) -> 'State':
        return replace(self, call_stack=self.call_stack + (name,))

    def is_in_function(self) -> bool:
        return len(self.call_stack) > 0

    def set_returning(self, is_returning: bool) -> 'State':
        return replace(self, is_returning=is_returning)

    def set_break_level(self, level: int) -> 'State':
        return replace(self, break_level=max(level, 0))

    def set_continue_level(self, level: int) -> 'State':
        return replace(self, continue_level=max(level, 0))

    def decrement_break_level(self) -> 'State':
        return self.set_break_level(self.break_level - 1)

    def decrement_continue_level(self) -> 'State':
        return self.set_continue_level(self.continue_level - 1)

    def exit_function(self) -> 'State':
        assert len(self.call_stack) > 0, "Tried to exit function when not in function"
        return replace(self, call_stack=self.call_stack[:-1])

def is_special_var(name: str) -> bool:
    return name.isdecimal() or name in ["@", "#"]

@dataclass(frozen=True)
class Trace:
    states: tuple[State, ...]

    def extend(self, state: State | Callable[[State], State]) -> 'Trace':
        new_state = state if isinstance(state, State) else state(self.states[-1])
        DebugLogger.log_trace_extension(self.latest_state, new_state)
        return replace(self, states=self.states + (new_state,))

    @property
    def latest_state(self):
        return self.states[-1]

    def fail_last_command(self) -> 'Trace':
        assert len(self.states) > 1, "Cannot fail last command of a trace with only one state (no last command)"
        last_state = self.states[-1]
        prior_state = self.states[-2]
        if last_state.last_cmd_failure_postcond is not None:
            new_state = replace(last_state,
                                pathcond=prior_state.pathcond,
                                fs_model=prior_state.fs_model)\
                                .add_pathcond(last_state.last_cmd_failure_postcond)\
                                .update_fs(last_state.last_cmd_failure_postcond)\
                                .set_last_exit_code(SymStr(("1",)), Confidence.SPECULATIVE)
            DebugLogger.log_trace_extension(prior_state, new_state)
            return replace(self, states=self.states[:-1] + (new_state,))
        else:
            return self

Traces = list[Trace]

def trace_map(traces: Traces, f: Callable[[State], State]) -> Traces:
    return [trace.extend(f) for trace in traces]

def collapse_traces(traces: Traces) -> tuple[Traces, Traces]:
    logging.debug("Collapsing %d traces", len(traces))
    traces_by_latest_states: dict[State, Trace] = {}
    for t in traces:
        if t.latest_state not in traces_by_latest_states:
            traces_by_latest_states[t.latest_state] = t
    # technically this isn't dropping any traces
    logging.debug("Collapsed to %d traces", len(traces_by_latest_states))
    return list(traces_by_latest_states.values()), []

@dataclass(frozen=True)
class FuncMap:
    # Map from function name to set function definitions (by name)
    funcs: FrozenDict[str, AST.Command] = field(default_factory=FrozenDict)
    # Set of functions that have been called
    called: set[str] = field(default_factory=set)

    def uncalled_funcs(self) -> dict[str, AST.Command]:
        return {name: node for name, node in self.funcs.items() if name not in self.called}
