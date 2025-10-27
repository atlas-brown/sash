import logging
import time
import traceback
from abc import ABC
from dataclasses import dataclass
from enum import Enum
from typing import Optional

@dataclass(frozen=True)
class Report(ABC):
    code:str
    message : str
    source_line: int

    def __repr__(self) -> str:
        return f"L{self.source_line}:{self.code}:{self.message}"

    def to_dict(self) -> dict:
        return {
            "line": self.source_line,
            "code": self.code,
            "message": self.message
        }

    @classmethod
    def all_codes(cls) -> set[str]:
        """Return the set of CODE values declared on Report subclasses."""
        codes: set[str] = set()
        def walk(c):
            for s in c.__subclasses__():
                code = getattr(s, "CODE", None)
                if code:
                    codes.add(code)
                walk(s)
        walk(cls)
        return codes

class Error(Report):
    pass

class Warning(Report):
    pass

class ParseError(Error):
    CODE = "parse"
    def __init__(self,msg : str ="") -> None:
        self.report = f"Failed to parse script {msg}"
        super().__init__(self.CODE,self.report, None)

class UnboundID(Error):
    CODE = "unbound"
    def __init__(self, var, line):
        super().__init__(self.CODE, f"no definition found for {var}", line)

class UndefinedFunction(Error):
    CODE = "function_use_before_def"
    def __init__(self, name, line):
        super().__init__(self.CODE, f"function {name} is used before its definition", line)

class InfiniteLoop(Error):
    CODE = "infinite_loop"
    def __init__(self, loop, line):
        super().__init__(self.CODE, f"condition for loop {loop} never changes, causing an infinite loop", line)

class ConstantCondition(Warning):
    CODE = "const_cond"
    def __init__(self, cond, line):
        super().__init__(self.CODE, f"condition {cond} is always true or false", line)

class LoopRunsOnce(Warning):
    CODE = "loop_once"
    def __init__(self, loop, line):
        super().__init__(self.CODE, f"loop {loop} runs only once", line)

class DeleteSystemFile(Error):
    CODE = "del_sys_file"
    def __init__(self,filename:str, line):
        super().__init__(self.CODE,f"WILL delete system file {filename}", line)

class CouldDeleteSystemFile(Error):
    CODE = "could_del_sys_file"
    def __init__(self,filename:str, line):
        super().__init__(self.CODE,f"might delete system file {filename}", line)


class DangerousWordSplit(Warning):
    CODE = "word_split"
    def __init__(self, source, line):
        super().__init__(self.CODE, f"{source} could be split in a dangerous position, leading to unexpected arguments to dangerous commands", line)

class RedirectToFunction(Warning):
    CODE = "redir_func"
    def __init__(self, function_name: str, line):
        super().__init__(self.CODE,f"redirecting output to {function_name}, which is a function, actually writes to a file with that name", line)

class DeadCode(Warning):
    CODE = "dead_code"
    def __init__(self, code, line):
        super().__init__(self.CODE, f"{code} is unreachable and will never be executed", line)

class EmptyVar(Warning):
    CODE = "empty_var"
    def __init__(self, varname: str, line):
        super().__init__(self.CODE, f"variable {varname} might be empty", line)

class IgnoredCommandResult(Warning):
    CODE = "ignored_cmd_result"
    def __init__(self, command: str, line):
        super().__init__(self.CODE, f"the result of command '{command}' is ignored.", line)

class Reporter:
    _filename = ""
    _errors: set[Report] = set()
    _start_time = time.monotonic()
    _solver_time : float = 0

    @classmethod
    def initialize(cls,filename:str):
        cls._filename = filename
        cls._errors = set()
        cls._start_time = time.monotonic()
        cls._solver_time : float = 0

    @classmethod
    def add_error(cls,rep:Report):
        cls._errors.add(rep)

    @classmethod
    def get_report(cls) -> dict:
        end_time = time.monotonic()
        time_elapsed = round(end_time - cls._start_time,2)
        dct = {
            "filename": cls._filename,
            "errors": [e.to_dict() for e in cls._errors],
            "time" : time_elapsed,
            "solver_time" : cls._solver_time,
        }
        return dct

    @classmethod
    def set_solver_time(cls,time:float):
        cls._solver_time = time

