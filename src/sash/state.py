from dataclasses import dataclass, field
import sash.pru as pru
import shasta.ast_node as AST
import logging

@dataclass(frozen=True)
class ShellVar:
    value: SymbStr
    readonly : bool = False
    export : bool = False

@dataclass(frozen=True)
class State:
    pathcond: list[pru.PRU]
    env: dict[str, ShellVar]
    localenv: dict[str, ShellVar]
    fundefs: dict[str, AST.DefunNode]
    last_exit_code: SymbStr
    last_cmd: Optional[AST.AstNode]

@dataclass(frozen=True)
class Trace:
    states: list[State]

    def extend(self, state: State) -> Trace:
        return Trace(self.states + [state])


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

