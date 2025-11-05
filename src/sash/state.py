from dataclasses import dataclass, field, replace, fields
import sash.constraints
import shasta.ast_node as AST
import logging
from typing import Callable, Optional, Any
from enum import Enum
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

class ArbitraryType(Enum):
    APPROXIMATION = 0
    ENVIRONMENT = 1

@dataclass(frozen=True)
class CompletelyArbitrary:
    source: FrozenAst
    kind: ArbitraryType
    producing_state: Optional['State'] # shouldn't ever result in cyclic data, because the state that is used to compute an arbitrary value should only ever be an ancester of the state the stores it, but beware
    prefix: Optional[SymStr] = None
    suffix: Optional[SymStr] = None

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
            and self.suffix == other.suffix

    def __repr__(self):
        return f"CompletelyArbitrary(s`{repr(self.source)[:30]}`, {self.kind}, state<{hash(self.producing_state)}>, pre:{self.prefix}, suf:{self.suffix})"

@dataclass(frozen=True)
class WordCount:
    min: int
    max: int | float  # use `math.inf` for infinity

@dataclass(frozen=True)
class Field:
    content: SymStr | CompletelyArbitrary
    count: WordCount

    def quote(self) -> 'Field':
        return Field(self.content, WordCount(min(self.count.min, 1),
                                             min(self.count.max, 1)))

@dataclass(frozen=True)
class ShellVar:
    value: Field
    readonly : bool = False
    export : bool = False

@dataclass(frozen=True)
class SetOptions:
    NOUNSET = "u"
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
class State:
    pathcond:                    tuple[sash.constraints.Constraint, ...] = field(default_factory=tuple)
    env:                         FrozenDict[str, ShellVar]               = field(default_factory=FrozenDict)
    localenv:                    FrozenDict[str, ShellVar]               = field(default_factory=FrozenDict)
    fundefs:                     FrozenDict[str, FrozenAst]              = field(default_factory=FrozenDict)
    last_exit_code:              SymStr                                  = SymStr(("0",))
    last_cmd:                    Optional[FrozenAst]                     = None
    opts:                        SetOptions                              = field(default_factory=SetOptions)
    known_nonexistant_commands:  frozenset[str]                          = field(default_factory=frozenset)
    terminated:                  bool                                    = False # by `exit` or similar

    external_data: Any = None # ASSUMPTION: must be hashable

    # NOTE: (and beware) intentionally ignoring pathcond in equality and hash
    def __hash__(self):
        return hash(tuple(getattr(self, field.name) for field in fields(self) if field.name != "pathcond"))
    def __eq__(self, other):
        return isinstance(other, State) and \
            all(getattr(self, field.name) == getattr(other, field.name)
                for field in fields(self) if field.name != "pathcond")

    def set_env(self, var: str, value: ShellVar) -> 'State':
        return replace(self, env=self.env.set(var, value))

    def extend_env(self, new_vars: dict[str, ShellVar]) -> 'State':
        return replace(self, env=(self.env | new_vars))

    def extend_localenv(self, new_vars: dict[str, ShellVar]) -> 'State':
        return replace(self, localenv=(self.localenv | new_vars))

    def add_pathcond(self, cond: sash.constraints.Constraint) -> 'State':
        new_pathcond = self.pathcond + (cond,)
        return replace(self, pathcond=new_pathcond)

    def lookup(self, var: str) -> Optional[ShellVar]:
        if var in self.localenv:
            return self.localenv[var]
        elif self.localenv and var.isnumeric():
            # Positional parameters are not dynamically scoped?
            return None
        elif var in self.env:
            return self.env[var]
        else:
            return None

    def set_fundef(self, name: str, defn: FrozenAst) -> 'State':
        return replace(self, fundefs=self.fundefs.set(name, defn))

    def lookup_fundef(self, name: str) -> Optional[FrozenAst]:
        return self.fundefs.get(name, None)

    def set_external(self, data) -> 'State':
        return replace(self, external_data=data)

    def set_options(self, options: set[str]) -> 'State':
        return replace(self, opts=self.opts.set_options(options))

    def set_last_exit_code(self, code: SymStr) -> 'State':
        return replace(self, last_exit_code=code)

    def terminate(self) -> 'State':
        return replace(self, terminated=True)

    def record_nonexistant_command(self, name: str) -> 'State':
        return replace(self, known_nonexistant_commands=self.known_nonexistant_commands | {name})

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

Traces = list[Trace]

def trace_map(traces: Traces, f: Callable[[State], State]) -> Traces:
    return [trace.extend(f) for trace in traces]

def collapse_traces(traces: Traces) -> Traces:
    traces_by_latest_states = {}
    for t in traces:
        if t.latest_state not in traces_by_latest_states:
            traces_by_latest_states[t.latest_state] = t
    return list(traces_by_latest_states.values())


@dataclass(frozen=True)
class ScriptInfo:
    # Dictionary mapping function names to the line number where they are defined (in the future).
    future_fundef_lines: FrozenDict[str, int] = field(default_factory=FrozenDict)

