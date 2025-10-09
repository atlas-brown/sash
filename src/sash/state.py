from dataclasses import dataclass, field, replace
import sash.constraints
import shasta.ast_node as AST
import logging
from typing import Callable, Optional
from enum import Enum
from sash.util import make_hashable


@dataclass(frozen=True)
class SymVar:
    name: str

@dataclass(frozen=True)
class SymStr:
    parts: list[str | SymVar] = field(default_factory=list)

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
        return SymStr(new_parts)

class ArbitraryType(Enum):
    APPROXIMATION = 0
    ENVIRONMENT = 1

@dataclass(frozen=True)
class CompletelyArbitrary:
    source: AST.AstNode
    kind: ArbitraryType
    producing_state: Optional['State'] # shouldn't ever result in cyclic data, because the state that is used to compute an arbitrary value should only ever be an ancester of the state the stores it, but beware

    def __eq__(self, other):
        return isinstance(other, CompletelyArbitrary) \
            and self.source == other.source \
            and self.kind == other.kind \
            and self.producing_state == other.producing_state \
            and self.producing_state is not None # If the state producing this is unknown, conservatively say it can't be equal to any other

@dataclass(frozen=True)
class WordCount:
    min: int
    max: int | float  # use `math.inf` for infinity

@dataclass(frozen=True)
class Field:
    content: SymStr | CompletelyArbitrary
    count: WordCount

    def quote(self) -> 'Field':
        return Field(self.content, WordCount(1, 1))

@dataclass(frozen=True)
class ShellVar:
    value: Field
    readonly : bool = False
    export : bool = False

@dataclass(frozen=True)
class State:
    pathcond: list[sash.constraints.Constraint]
    env: dict[str, ShellVar]
    localenv: dict[str, ShellVar]
    fundefs: dict[str, AST.DefunNode]
    last_exit_code: SymStr
    last_cmd: Optional[AST.AstNode]

    # NOTE: (and beware) intentionally ignoring pathcond in equality and hash
    def __hash__(self):
        return make_hashable(replace(self, pathcond=[])).__hash__()
    def __eq__(self, other):
        return self.env == other.env \
            and self.localenv == other.localenv \
            and self.fundefs == other.fundefs \
            and self.last_exit_code == other.last_exit_code \
            and self.last_cmd == other.last_cmd

    def set_env(self, var: str, value: ShellVar) -> 'State':
        new_env = dict(self.env)
        new_env[var] = value
        return replace(self, env=new_env)

    def set_fundef(self, name: str, defn: AST.DefunNode) -> 'State':
        new_fundefs = dict(self.fundefs)
        new_fundefs[name] = defn
        return replace(self, fundefs=new_fundefs)

    def add_pathcond(self, cond: sash.constraints.Constraint) -> 'State':
        new_pathcond = self.pathcond + [cond]
        return replace(self, pathcond=new_pathcond)

    def lookup(self, var: str) -> Optional[ShellVar]:
        if var in self.localenv:
            return self.localenv[var]
        elif var in self.env:
            return self.env[var]
        else:
            return None

@dataclass(frozen=True)
class Trace:
    states: list[State]

    def extend(self, state: State | Callable[[State], State]) -> 'Trace':
        if isinstance(state, State):
            return Trace(self.states + [state])
        else:
            return Trace(self.states + [state(self.states[-1])])

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


@dataclass
class SetOptionStore:
    allexport : Optional[bool] = None
    notify : Optional[bool] = None
    noclobber : Optional[bool] = None
    errexit : Optional[bool] = True
    noglob : Optional[bool] = None
    h : Optional[bool] = None
    monitor : Optional[bool] = None
    noexec : Optional[bool] = None
    nounset : Optional[bool] = None
    verbose : Optional[bool] = None
    xtrace : Optional[bool] = None
    ignoreeof : Optional[bool]  = None
    nolog : Optional[bool] = None
    pipefail : Optional[bool] = True
    vi : Optional[bool] = None
    def handle_option(self, option:str, value:bool) -> bool:
        match option:
            case 'a' | "allexport":
                self.allexport = value
            case 'b' | "notify":
                self.notify = value
            case 'C' | "noclobber":
                self.noclobber = value
            case 'e' | "errexit":
                self.errexit = value
            case 'f' | "noglob":
                self.noglob = value
            case 'h':
                self.h = value
            case 'm' | "monitor":
                self.monitor = value
            case 'n' | "noexec":
                self.noexec = value
            case 'u' | "nounset":
                self.nounset = value
            case 'v' | "verbose":
                self.verbose = value
            case 'x' | "xtrace":
                self.xtrace = value
            case 'ignoreeof':
                self.ignoreeof = value
            case 'nolog':
                self.nolog = value
            case 'pipefail':
                self.pipefail = value
            case 'vi':
                self.vi = value
            case _:
                logging.warning(f"Unknown option: {option}. Ignoring")
                return False
        return True

@dataclass(frozen=True)
class ScriptInfo:
    opts: SetOptionStore

