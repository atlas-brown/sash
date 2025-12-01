import logging
from collections.abc import Callable
from dataclasses import dataclass, field, fields, replace
from enum import Enum
from typing import Any, Optional

import shasta.ast_node as AST

import sash.util as util
from sash.constraints import (
    CommandExists,
    Constraint,
    Empty,
    FSModel,
    FSModelSimple,
    Implies,
    NormalizedFSConstraint,
    Not,
    normalize_fs_constraints,
)
from sash.frozen import FrozenAst, FrozenDict


@dataclass(frozen=True)
class SymVar:
    name: str

@dataclass(frozen=True)
class SymStr:
    parts: tuple[str | SymVar, ...] = field(default_factory=tuple)

    def is_simple(self) -> bool:
        """Return true if there are no adjacent strings in `parts`."""
        last_was_str = False
        for p in self.parts:
            if isinstance(p, str):
                if last_was_str:
                    return False
                last_was_str = True
            else:
                last_was_str = False
        return True

    def simplify(self) -> 'SymStr':
        if self.is_simple():
            return self

        # collapse all adjacent strings into one string
        new_parts = []
        this_str = ""
        for part in self.parts:
            if isinstance(part, str):
                this_str += part
            else:
                if this_str != "":
                    new_parts.append(this_str)
                    this_str = ""
                new_parts.append(part)
        if this_str != "":
            new_parts.append(this_str)
        return SymStr(tuple(new_parts))

    def try_to_str(self) -> str | None:
        nls : list[str] = []
        for i in self.parts:
            if isinstance(i,str):
                nls.append(i)
            else:
                return None
        return "".join(nls)

class ArbitraryType(Enum):
    APPROXIMATION = 0
    ENVIRONMENT = 1

@dataclass(frozen=True)
class CompletelyArbitrary:
    source: FrozenAst
    kind: ArbitraryType
    producing_state: 'State | None' # shouldn't ever result in cyclic data, because the state that is used to compute an arbitrary value should only ever be an ancester of the state the stores it, but beware
    prefix: SymStr | None = None
    suffix: SymStr | None = None
    quoted: bool = False

    def __eq__(self, other):
        # If the state producing this is unknown, conservatively say it can't be equal to any other
        # Another twist here, the producing state is only relevant for the APPROXIMATION kind, because
        # arbitrariness due to the environment should be the same regardless of state
        return isinstance(other, CompletelyArbitrary) \
            and self.source == other.source \
            and self.kind == other.kind \
            and (self.kind == ArbitraryType.ENVIRONMENT or self.producing_state == other.producing_state) \
            and self.producing_state is not None \
            and self.prefix == other.prefix \
            and self.suffix == other.suffix \
            and self.quoted == other.quoted

    def __hash__(self):
        return hash((self.source, self.kind, self.producing_state if self.kind == ArbitraryType.APPROXIMATION else None, self.prefix, self.suffix, self.quoted))

    def __repr__(self):
        return f"CompletelyArbitrary(s`{repr(self.source)[:30]}`, {self.kind}, state<{hash(self.producing_state)}>, pre:{self.prefix}, suf:{self.suffix}, q:{self.quoted})"

@dataclass(frozen=True)
class WordCount:
    min: int
    max: int | float  # use `math.inf` for infinity

@dataclass(frozen=True)
class Field:
    content: SymStr | CompletelyArbitrary
    count: WordCount

    def quote(self) -> 'Field':
        if isinstance(self.content, CompletelyArbitrary):
            return Field(replace(self.content, quoted=True),
                         WordCount(min(self.count.min, 1), min(self.count.max, 1)))
        return Field(self.content, WordCount(min(self.count.min, 1),
                                             min(self.count.max, 1)))

    def is_constant(self) -> bool:
        return isinstance(self.content, SymStr) and all(isinstance(p, str) for p in self.content.parts) and self.count.min == self.count.max

    def try_to_str(self) -> str | None:
        match self.content:
            case SymStr():
                return self.content.try_to_str()
            case _:
                return None

    def without_trailing_slash(self) -> 'Field':
        if isinstance(self.content, SymStr) and isinstance(self.content.parts[-1], str):
            # only remove trailing slash if it's part of a string literal at the end
            first_parts = self.content.parts[:-1]
            last_part = self.content.parts[-1]
            if last_part.endswith("/"):
                new_path = Field(SymStr(first_parts + (last_part[:-1],)), self.count)
                return new_path

        # otherwise, do nothing
        return self

    @staticmethod
    def create_constant(s: str, words: int = 1) -> 'Field':
        return Field(SymStr((s,)), WordCount(words, words))

@dataclass(frozen=True)
class ShellVar:
    value: Field
    readonly : bool = False
    export : bool = False
    ghost : bool = False # was this variable binding created implicitly by the engine, but has never actually been set?

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

# <assertion_constraint>: if true, then things are OK, if false then there's a bug
@dataclass(frozen=True)
class Assertion:
    producing_state: "State"
    constraint: Constraint
    source_str: str
    source_line: int

    # exclude the state from repr to avoid large prints
    def __repr__(self):
        return f"Assertion(state<{hash(self.producing_state)}>, constraint={repr(self.constraint)}, source_str={repr(self.source_str)}, source_line={self.source_line})"

@dataclass(frozen=True)
class Condition(Assertion):
    pass

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

    external_data: Any = None # ASSUMPTION: must be hashable

    _hash: int = None
    def __post_init__(self):
        object.__setattr__(self, '_hash',
                           hash(tuple(getattr(self, field.name) for field in fields(self))))
    def __hash__(self):
        return self._hash

    def set_env(self, var: str, value: ShellVar) -> 'State':
        return replace(self, env=self.env.set(var, value))

    def extend_env(self, new_vars: dict[str, ShellVar]) -> 'State':
        return replace(self, env=(self.env | new_vars))

    def extend_localenv(self, new_vars: dict[str, ShellVar]) -> 'State':
        return replace(self, localenv=(self.localenv | new_vars))

    def add_pathcond(self, cond: Constraint, source_str: str | None = None, source_line: int | None = None) -> 'State':
        new_pathcond = self.pathcond + (Condition(self, cond, source_str, source_line),)
        return replace(self, pathcond=new_pathcond)

    def add_assertion(self, assertion_constraint: Constraint, source_str: str | None = None, source_line: int | None = None) -> 'State':
        if assertion_constraint == Empty():
            logging.debug("Skipping empty assertion from %s at line %s", source_str, source_line)
            return self
        assertion = Assertion(producing_state=self, constraint=assertion_constraint, source_str=source_str, source_line=source_line)
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
        return replace(self, fs_model=self.fs_model.apply_postcondition(NormalizedFSConstraint(constraints)))

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

    def update_known_commands(self, spec: Constraint) -> 'State':
        norm_spec = normalize_fs_constraints(spec) # turns ~(a & b) into (~a | ~b), removes double negations, etc.

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

    def exit_function(self) -> 'State':
        assert self.call_stack, "Tried to exit function when not in function"
        return replace(self, call_stack=self.call_stack[:-1])

def is_special_var(name: str) -> bool:
    return name.isdecimal() or name in ["@", "#"]

@dataclass(frozen=True)
class Trace:
    states: tuple[State, ...]

    def extend(self, state: State | Callable[[State], State]) -> 'Trace':
        if isinstance(state, State):
            return Trace(self.states + (state,))
        else:
            return Trace(self.states + (state(self.states[-1]),))

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
            return replace(self, states=self.states[:-1] + (new_state,))
        else:
            return self

Traces = list[Trace]

def trace_map(traces: Traces, f: Callable[[State], State]) -> Traces:
    return [trace.extend(f) for trace in traces]

def collapse_traces(traces: Traces) -> Traces:
    traces_by_latest_states: dict[State, Trace] = {}
    for t in traces:
        if t.latest_state not in traces_by_latest_states:
            traces_by_latest_states[t.latest_state] = t
    return list(traces_by_latest_states.values())

@dataclass(frozen=True)
class FuncMap:
    # Map from function name to set function definitions (by name)
    funcs: FrozenDict[str, AST.Command] = field(default_factory=FrozenDict)
    # Set of functions that have been called
    called: set[str] = field(default_factory=set)

    def uncalled_funcs(self) -> dict[str, AST.Command]:
        return {name: node for name, node in self.funcs.items() if name not in self.called}
