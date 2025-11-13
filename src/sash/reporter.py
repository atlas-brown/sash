import time
from abc import ABC
from dataclasses import dataclass
from enum import Enum
from typing import NamedTuple


class Severity(Enum):
    ERROR = "error"
    WARNING = "warning"


class Code(Enum):
    PARSE = "parse"
    UNBOUND_ID = "unbound"
    UNBOUND_ID_SET_U = "unbound_setu"
    UNDEFINED_FUNCTION = "function_use_before_def"
    INFINITE_LOOP = "infinite_loop"
    CONSTANT_CONDITION = "const_cond"
    LOOP_RUNS_ONCE = "loop_once"
    DELETE_SYSTEM_FILE = "del_sys_file"
    WORD_SPLIT_COULD_DELETE_SYSTEM_FILE = "word_split_del_sys_file"
    DANGEROUS_WORD_SPLIT = "word_split"
    REDIRECT_TO_FUNCTION = "redir_func"
    DEAD_CODE = "dead_code"
    EMPTY_VAR = "empty_var"
    IGNORED_COMMAND_RESULT = "ignored_cmd_result"
    NOT_A_COMMAND = "not_a_command"
    UNSATISFIED_PRECONDITION = "unsat_precond"


@dataclass(frozen=True)
class Issue(ABC):
    code: Code
    message: str
    severity: Severity
    source_line: int | None

    def is_error(self) -> bool:
        return self.severity == Severity.ERROR

    def is_warning(self) -> bool:
        return self.severity == Severity.WARNING

    def __repr__(self) -> str:
        return f"L{self.source_line}:{self.code}:{self.message}"

    def to_dict(self) -> dict:
        return {
            "line": self.source_line,
            "code": self.code.value,
            "severity": self.severity.value,
            "message": self.message
        }

    @classmethod
    def all_codes(cls) -> set[str]:
        """Return the set of possible issue codes."""
        return {code.value for code in Code}


class ParseError(Issue):
    def __init__(self, msg: str="") -> None:
        self.report = f"Failed to parse script {msg}"
        super().__init__(Code.PARSE, self.report, Severity.ERROR, None)


class UnboundID(Issue):
    def __init__(self, var, line):
        super().__init__(Code.UNBOUND_ID, f"no definition found for {var}", Severity.ERROR, line)


class UnboundIDSetU(Issue):
    def __init__(self, var, line):
        super().__init__(Code.UNBOUND_ID_SET_U, f"no definition found for {var} in `set -u` mode", Severity.ERROR, line)


class UndefinedFunction(Issue):
    def __init__(self, name, line):
        super().__init__(Code.UNDEFINED_FUNCTION, f"function {name} is used before its definition", Severity.ERROR, line)


class InfiniteLoop(Issue):
    def __init__(self, loop, line):
        super().__init__(Code.INFINITE_LOOP, f"condition for loop {loop} never changes, causing an infinite loop", Severity.ERROR, line)


class ConstantCondition(Issue):
    def __init__(self, cond, line):
        super().__init__(Code.CONSTANT_CONDITION, f"condition {cond} is always true or false", Severity.WARNING, line)


class LoopRunsOnce(Issue):
    def __init__(self, loop, line):
        super().__init__(Code.LOOP_RUNS_ONCE, f"loop {loop} runs only once", Severity.WARNING, line)


class DeleteSystemFile(Issue):
    def __init__(self,filename:str, line):
        super().__init__(Code.DELETE_SYSTEM_FILE, f"WILL delete system file {filename}", Severity.ERROR, line)


class WordSplitCouldDeleteSystemFile(Issue):
    def __init__(self,filename:str, line):
        super().__init__(Code.WORD_SPLIT_COULD_DELETE_SYSTEM_FILE, f"word splitting or empty variable could lead to deletion of system file {filename}", Severity.ERROR, line)


class DangerousWordSplit(Issue):
    def __init__(self, source, line):
        super().__init__(Code.DANGEROUS_WORD_SPLIT, f"{source} could be split in a dangerous position, leading to unexpected arguments to dangerous commands", Severity.WARNING, line)


class RedirectToFunction(Issue):
    def __init__(self, function_name: str, line):
        super().__init__(Code.REDIRECT_TO_FUNCTION,f"redirecting output to {function_name}, which is a function, actually writes to a file with that name", Severity.WARNING, line)


class DeadCode(Issue):
    def __init__(self, code, line):
        super().__init__(Code.DEAD_CODE, f"{code} is unreachable and will never be executed", Severity.WARNING, line)


class EmptyVar(Issue):
    def __init__(self, varname: str, line):
        super().__init__(Code.EMPTY_VAR, f"variable {varname} might be empty", Severity.WARNING, line)


class IgnoredCommandResult(Issue):
    def __init__(self, command: str, line):
        super().__init__(Code.IGNORED_COMMAND_RESULT, f"the result of command '{command}' is ignored.", Severity.WARNING, line)


class NotACommand(Issue):
    def __init__(self, name: str, line):
        super().__init__(Code.NOT_A_COMMAND, f"'{name}' is invoked as a command, but it cannot be one", Severity.ERROR, line)


class UnsatisfiedPrecondition(Issue):
    def __init__(self, constraint, line):
        super().__init__(Code.UNSATISFIED_PRECONDITION, f"precondition '{constraint}' might not hold", Severity.ERROR, line)


class Report(NamedTuple):
    filename: str
    issues: list[Issue]
    time: float
    solver_time: float

    def to_dict(self) -> dict:
        return {
            "filename": self.filename,
            "issues": [issue.to_dict() for issue in self.issues],
            "time": self.time,
            "solver_time": self.solver_time,
        }


class Reporter:
    _filename: str = ""
    _issues: set[Issue] = set()
    _start_time: float = time.perf_counter()
    _solver_time: float = 0

    @classmethod
    def initialize(cls, filename:str):
        cls._filename = filename
        cls._issues = set()
        cls._start_time = time.perf_counter()
        cls._solver_time : float = 0

    @classmethod
    def add_issue(cls, issue: Issue):
        cls._issues.add(issue)

    @classmethod
    def set_solver_time(cls, time:float):
        cls._solver_time = time

    @classmethod
    def get_report(cls) -> Report:
        end_time = time.perf_counter()
        time_elapsed = end_time - cls._start_time
        return Report(
            filename=cls._filename,
            issues=list(cls._issues),
            time=time_elapsed,
            solver_time=cls._solver_time,
        )
