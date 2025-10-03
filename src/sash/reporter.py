import time
import logging
import traceback
from enum import Enum
from abc import ABC
from dataclasses import dataclass
from typing import Optional

def make_unique(ls):
    return list(set(ls))

@dataclass(frozen=True)
class ShseerReport(ABC):
    code:str
    message : str
    def __repr__(self) -> str:
        return f"{self.code}:{self.message}"

class ShseerError(ShseerReport):
    pass

class ShseerWarning(ShseerReport):
    pass

class ParseError(ShseerError):
    def __init__(self,msg : str ="") -> None:
        self.report = f"Failed to parse script {msg}"
        super().__init__("parse",self.report)

class UnboundID(ShseerError):
    def __init__(self, var):
        super().__init__("unbound", f"no definition found for {var}")


class ConstantCondition(ShseerError):
    def __init__(self):
        super().__init__("const_cond", "condition is always true or false")

class LoopRunsOnce(ShseerWarning):
    def __init__(self):
        super().__init__("loop_once", "loop runs only once")

class DeleteSystemFile(ShseerError):
    def __init__(self,filename:str):
        super().__init__("del_sys_file",f"might delete system file {filename}")

class Reporter:
    # Have this as a list even despite duplications for now
    _filename = ""
    _error_messages:list[tuple[str,str]] = []
    _start_time = time.monotonic()
    _solver_time : float = 0

    @classmethod
    def initialize(cls,filename:str):
        cls._filename = filename
        cls._error_messages:list[tuple[str,str]] = []
        cls._start_time = time.monotonic()
        cls._solver_time : float = 0

    @classmethod
    def add_error(cls,rep:ShseerReport):
        cls._error_messages.append((rep.code,rep.message))

    @classmethod
    def get_report(cls) -> dict:
        end_time = time.monotonic()
        time_elapsed = round(end_time - cls._start_time,2)
        dct = {
            "filename": cls._filename,
            "error_messages": make_unique(cls._error_messages),
            "time" : time_elapsed,
            "solver_time" : cls._solver_time,
        }
        return dct

    @classmethod
    def set_solver_time(cls,time:float):
        cls._solver_time = time

