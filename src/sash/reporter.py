import logging
import time
import traceback
from abc import ABC
from dataclasses import dataclass
from enum import Enum
from typing import Optional


def make_unique(ls):
    return list(set(ls))

@dataclass(frozen=True)
class Report(ABC):
    code:str
    message : str
    def __repr__(self) -> str:
        return f"{self.code}:{self.message}"

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "message": self.message
        }

class Error(Report):
    pass

class Warning(Report):
    pass

class ParseError(Error):
    def __init__(self,msg : str ="") -> None:
        self.report = f"Failed to parse script {msg}"
        super().__init__("parse",self.report)

class UnboundID(Error):
    def __init__(self, var):
        super().__init__("unbound", f"no definition found for {var}")

class InfiniteLoop(Error):
    def __init__(self, loop):
        super().__init__("infinite_loop", f"condition for loop {loop} never changes, causing an infinite loop")

class ConstantCondition(Warning):
    def __init__(self):
        super().__init__("const_cond", "condition is always true or false")

class LoopRunsOnce(Warning):
    def __init__(self):
        super().__init__("loop_once", "loop runs only once")

class DeleteSystemFile(Error):
    def __init__(self,filename:str):
        super().__init__("del_sys_file",f"might delete system file {filename}")

class DangerousWordSplit(Warning):
    def __init__(self, source):
        super().__init__("word_split", f"{source} could be split, leading to unexpected arguments")

class RedirectToFunction(Warning):
    def __init__(self, function_name: str):
        super().__init__("redir_func",f"redirecting output to {function_name}, which is a function, actually writes to a file with that name")

class Reporter:
    _filename = ""
    _errors: set[Report]
    _start_time = time.monotonic()
    _solver_time : float = 0

    @classmethod
    def initialize(cls,filename:str):
        cls._filename = filename
        cls._error_messages:list[tuple[str,str]] = []
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

