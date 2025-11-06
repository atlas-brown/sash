from sash.state import CompletelyArbitrary, Field, SymStr
from sash.constraints import *
from typing import Callable, Optional
from dataclasses import dataclass, replace
import logging
from pash_annotations.parser import parser

@dataclass(frozen=True)
class Cmd:
    cmd_name: SymStr
    flags: set[str]
    options: dict[str, Field]
    args: list[Field]

    def __str__(self) -> str:
        arg_str = " ".join([str(arg) for arg in self.args])
        flag_str = " ".join(self.flags)
        return f"{self.cmd_name} {flag_str} {arg_str}"

@dataclass(frozen=True)
class CmdSpec:
    precond: Constraint
    success_postcond: Constraint
    failure_postcond: Constraint

# TODO: plug in annotations parsing code
def parse_command(cmd: list[Field]) -> Cmd:
    logging.debug(f"Parsing command from fields: {cmd}")
    # parser.parse()

def rm_spec(cmd_: list[Field]) -> CmdSpec:
    # rm $PATH
    # rm -r $PATH
    # rm -r -f $PATH

    cmd = parse_command(cmd_)

    logging.debug(f"Ignored irrelevant flags for rm: {cmd.flags - {'-r', '-f'}}")
    cmd = replace(cmd, flags={flag for flag in cmd.flags if flag in {"-r", "-f"}})

    match cmd:
        case Cmd(SymStr(["rm"]), set(), {}, path):
            return CmdSpec(
                precond=IsFile(path),
                success_postcond=And(IsDeleted(path), ReadsPath(path)),
                failure_postcond=Empty()
            )
        case Cmd(SymStr(["rm"]), set(["-f"]), {}, path):
            return CmdSpec(
                precond=And(Not(IsDir(path)), Not(IsDeleted(path))),
                success_postcond=And(IsDeleted(path), ReadsPath(path)),
                failure_postcond=Empty())
        case Cmd(SymStr(["rm"]), set(["-r"]), {}, path):
            return CmdSpec(
                precond=Or(IsFile(path), IsDir(path)),
                success_postcond=And(IsDeleted(path), ReadsPath(path)),
                failure_postcond=Empty())
        case Cmd(SymStr(["rm"]), set(["-r", "-f"]), {}, path):
            return CmdSpec(
                precond=Not(IsDeleted(path)),
                success_postcond=And(IsDeleted(path), ReadsPath(path)),
                failure_postcond=Empty())
        case Cmd(_, _, _, path):
            return CmdSpec(
                precond=Empty(),
                success_postcond=ReadsPath(path),
                failure_postcond=Empty()
            )
