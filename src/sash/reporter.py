from collections.abc import Sequence
import logging
import math
import copy
from abc import ABC
from dataclasses import dataclass
from enum import Enum
from typing import NamedTuple
from sash.interpreter_config import InterpConfig
from sash.constraints import Constraint, Description, Empty
from sash.debugtools.logger import DebugLogger
from sash.symbolic.strings import Field


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
    UNEXPECTED_STDIN = "unexpected_stdin"
    COMMAND_CAN_ONLY_FAIL = "command_can_only_fail"
    CAPTURING_EMPTY_OUTPUT = "capturing_empty_output"
    CMD_ASSERTION_PATH_STATE = "cmd_expected_path_state"
    DATA_LOSS = "data_loss"
    DELETE_USER_DIRECTORY = "del_user_dir"
    INCONSISTENT_IFS = "inconsistent_ifs"


def _prettify_object(node: object) -> str:
    """Try to get a pretty string representation of the given object, falling back to the default str() if not available."""
    pretty = getattr(node, "pretty", None)
    return str(pretty()) if callable(pretty) else str(node)


def _prettify_paths(paths: Sequence[Field]) -> str:
    """Try to get a pretty string representation of the given paths, falling back to the default str() if not available."""
    pretty_paths = []
    for path in paths:
        if isinstance(path, Field):
            pretty_path = path.try_to_str() or _prettify_object(path.content)
        else:
            pretty_path = _prettify_object(path)
        pretty_paths.append(pretty_path)
    return f"({', '.join(pretty_paths)})"


@dataclass(frozen=True)
class Issue(ABC):
    code: Code
    message: str
    severity: Severity
    line: int | None
    constraint: Constraint | None = None

    def is_error(self) -> bool:
        return self.severity == Severity.ERROR

    def is_warning(self) -> bool:
        return self.severity == Severity.WARNING

    def __repr__(self) -> str:
        qualification = f"IF {self.constraint} then " if self.constraint else ""
        return f"L{self.line}:{self.code}: {qualification}{self.message}"

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, Issue)
            and self.code == other.code
            and self.message == other.message
            and self.severity == other.severity
            and self.line == other.line
        )

    def __hash__(self) -> int:
        return hash((self.code, self.message, self.severity, self.line))

    def to_dict(self) -> dict:
        return {
            "line": self.line,
            "code": self.code.value,
            "severity": self.severity.value,
            "condition": str(self.constraint) if self.constraint else None,
            "message": self.message
        }

    def under_constraint(self, cons: Constraint | None) -> 'Issue':
        if cons is not None and cons != Empty():
            # Workaround that `replace` doesn't work with custom custructors
            cp = copy.copy(self)
            object.__setattr__(cp, 'constraint', cons)
            return cp
        else:
            return self

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
        super().__init__(Code.INFINITE_LOOP, f"condition for the following loop never changes, causing an infinite loop:\n{_prettify_object(loop)}", Severity.ERROR, line)


class ConstantCondition(Issue):
    def __init__(self, cond, line):
        super().__init__(Code.CONSTANT_CONDITION, f"condition is always true or false:\n{_prettify_object(cond)}", Severity.WARNING, line)


class LoopRunsOnce(Issue):
    def __init__(self, loop, line):
        super().__init__(Code.LOOP_RUNS_ONCE, f"loop runs only once:\n{_prettify_object(loop)}", Severity.WARNING, line)


class DeleteSystemFile(Issue):
    def __init__(self,filename:str, line):
        super().__init__(Code.DELETE_SYSTEM_FILE, f"WILL delete system file '{filename}'", Severity.ERROR, line)


class WordSplitCouldDeleteSystemFile(Issue):
    def __init__(self,filename:str, line):
        super().__init__(Code.WORD_SPLIT_COULD_DELETE_SYSTEM_FILE, f"word splitting or empty variable could lead to deletion of system file {filename}", Severity.ERROR, line)


class DangerousWordSplit(Issue):
    def __init__(self, source, line):
        super().__init__(Code.DANGEROUS_WORD_SPLIT, f"code could be split in a dangerous position, leading to unexpected arguments to dangerous commands:\n{_prettify_object(source)}", Severity.WARNING, line)


class RedirectToFunction(Issue):
    def __init__(self, function_name: str, line):
        super().__init__(Code.REDIRECT_TO_FUNCTION,f"redirecting output to {function_name}, which is a function, actually writes to a file with that name", Severity.WARNING, line)


class DeadCode(Issue):
    def __init__(self, code, line):
        super().__init__(Code.DEAD_CODE, f"code is unreachable and will never be executed:\n{_prettify_object(code)}", Severity.WARNING, line)


class EmptyVar(Issue):
    def __init__(self, varname: str, line):
        super().__init__(Code.EMPTY_VAR, f"variable {varname} might be empty", Severity.WARNING, line)


class IgnoredCommandResult(Issue):
    def __init__(self, command: str, line):
        super().__init__(Code.IGNORED_COMMAND_RESULT, f"the result of command '{command}' is ignored.", Severity.WARNING, line)


class NotACommand(Issue):
    def __init__(self, name: str, line):
        super().__init__(Code.NOT_A_COMMAND, f"'{name}' is invoked as a command, but it cannot be one", Severity.ERROR, line)


class UnexpectedStdin(Issue):
    def __init__(self, command: str, line):
        super().__init__(Code.UNEXPECTED_STDIN, f"command '{command}' expects input from stdin if the first argument is empty", Severity.ERROR, line)


class CommandCanOnlyFail(Issue):
    def __init__(self, command: str, line):
        super().__init__(Code.COMMAND_CAN_ONLY_FAIL, f"command '{command}' can only fail", Severity.WARNING, line)


class CapturingEmptyOutput(Issue):
    def __init__(self, command: str, line):
        super().__init__(Code.CAPTURING_EMPTY_OUTPUT, f"command '{command}' captures empty output", Severity.WARNING, line)


class ExpectedPathState(Issue):
    def __init__(self, command: str, state: str, paths: Sequence[Field], line):
        super().__init__(Code.CMD_ASSERTION_PATH_STATE, f"command '{command}' expects paths that are {state}, but one or more of the following paths might not be: {_prettify_paths(paths)}", Severity.ERROR, line)


class DataLoss(Issue):
    def __init__(self, command: str, paths: Sequence[Field], line):
        super().__init__(Code.DATA_LOSS, f"command '{command}' deletes the following paths, one of which has not been read, potentially causing loss of data: {_prettify_paths(paths)}", Severity.ERROR, line)


class DeleteUserDirectory(Issue):
    def __init__(self, directory: str, line):
        super().__init__(Code.DELETE_USER_DIRECTORY, f"deletes user directory '{directory}'", Severity.WARNING, line)


class InconsistentIFS(Issue):
    def __init__(self, ifs_values: list[str], line):
        values = ", ".join(repr(value) for value in ifs_values)
        super().__init__(Code.INCONSISTENT_IFS, f"IFS differs across traces: {values}", Severity.WARNING, line)


@dataclass(frozen=True)
class Report:
    filename: str
    issues: list[Issue]
    time: float
    solver_time: float
    timed_out: bool
    ast_nodes_total: int
    ast_nodes_interpreted: int
    ast_coverage_pct: float

    def to_dict(self) -> dict:
        return {
            "filename": self.filename,
            "issues": [issue.to_dict() for issue in self.issues],
            "time": self.time,
            "solver_time": self.solver_time,
            "timed_out": self.timed_out,
            "ast_nodes_total": self.ast_nodes_total,
            "ast_nodes_interpreted": self.ast_nodes_interpreted,
            "ast_coverage_pct": self.ast_coverage_pct,
        }

    def to_plain_text(self) -> str:
        """Render the report as user-facing, pretty plain text."""

        lines = [f"Analysis report for file {self.filename}:"]

        if self.issues:
            lines.append(f"Issues ({len(self.issues)} in total):")
            for idx, issue in enumerate(self.issues, start=1):
                header = f"{idx}. {issue.severity.value.upper()} at line {issue.line if issue.line is not None else 'unknown'}:"
                lines.append(f"\t{header}")
                lines.append(f"\t\tError code: {issue.code.value}")
                if issue.constraint:
                    lines.append(f"\t\tCondition: {issue.constraint}")
                lines.append(f"\t\tMessage: {issue.message}")
        else:
            lines.append("No issues found.")

        lines.extend([
            "Summary:",
            f"\tSymbolic execution time: {self.time} seconds",
            f"\tSolver time: {self.solver_time} seconds",
            f"\tTimed out: {self.timed_out}",
            f"\tAST nodes in total: {self.ast_nodes_total}",
            f"\tAST nodes interpreted: {self.ast_nodes_interpreted}",
            f"\tAST coverage: {self.ast_coverage_pct}%",
        ])

        return "\n".join(lines)

    def to_compact_text(self) -> str:
        """Render only issues, in a compact shellcheck-like format."""
        lines: list[str] = []
        for issue in self.issues:
            line = issue.line if issue.line is not None else 0
            lines.append(
                f"{self.filename}:{line}: {issue.severity.value}: {issue.message} [{issue.code.value}]"
            )
            if issue.constraint:
                lines.append(f"    condition: {issue.constraint}")
        return "\n".join(lines)


class Reporter:
    _filename: str
    _issues: dict[Issue, Constraint | None]
    _exec_time: float = math.nan
    _solver_time: float = math.nan
    _timed_out: bool
    _ast_nodes_total: int = 0
    _interpreted_ast_node_ids: set[int]
    _initialized: bool = False

    @classmethod
    def initialize(cls, filename:str):
        if cls._initialized:
            logging.warning("Reporter is already initialized; resetting")
            cls.reset()

        cls._filename = filename
        cls._issues = {}
        cls._exec_time = math.nan
        cls._solver_time = math.nan
        cls._timed_out = False
        cls._ast_nodes_total = 0
        cls._interpreted_ast_node_ids = set()
        cls._initialized = True

    @classmethod
    def add_issue(cls, issue: Issue, current_config: InterpConfig):
        # todo: improve condition handling (would need proper types instead of searching text)
        new_cons = issue.constraint or current_config.current_pass_constraint
        if issue in cls._issues:
            existing_cons = cls._issues[issue]
            # If the most general condition (none) is in place, do nothing
            if existing_cons is None:
                pass

            # If the second most general condition (empty vars) is in place, only update to None
            elif isinstance(existing_cons, Description) and ("empty" in existing_cons.text) and (new_cons in [None, Empty()]):
                cls._issues[issue] = None
                DebugLogger.log_issue(issue, current_config.current_pass)

            # All other conditions are just as general as each other; do nothing
            return

        cls._issues[issue] = new_cons
        DebugLogger.log_issue(issue, current_config.current_pass)

    @classmethod
    def set_exec_time(cls, exec_time: float):
        cls._exec_time = exec_time

    @classmethod
    def set_solver_time(cls, solver_time: float):
        cls._solver_time = solver_time

    @classmethod
    def set_timed_out(cls):
        cls._timed_out = True

    @classmethod
    def set_ast_nodes_total(cls, total: int):
        cls._ast_nodes_total = max(int(total), 0)

    @classmethod
    def mark_interpreted_ast_node(cls, node: object):
        cls._interpreted_ast_node_ids.add(id(node))

    @classmethod
    def clear_timed_out(cls):
        cls._timed_out = False

    @classmethod
    def get_timed_out(cls) -> bool:
        return cls._timed_out

    @classmethod
    def reset(cls):
        cls._initialized = False
        cls._issues = dict()
        cls._exec_time = math.nan
        cls._solver_time = math.nan
        cls._timed_out = False
        cls._ast_nodes_total = 0
        cls._interpreted_ast_node_ids = set()

    @classmethod
    def drop_issues(cls, codes: set[Code]):
        cls._issues = {issue: cons for issue, cons in cls._issues.items() if issue.code not in codes}

    @classmethod
    def get_report(cls) -> Report:
        if math.isnan(cls._exec_time):
            logging.debug("Execution time not set; defaulting to 0.0")
            cls._exec_time = 0.0

        if math.isnan(cls._solver_time):
            logging.debug("Solver time not set; defaulting to 0.0")
            cls._solver_time = 0.0

        interpreted_ast_nodes = min(
            len(cls._interpreted_ast_node_ids),
            cls._ast_nodes_total,
        )
        ast_coverage_pct = (
            (100.0 * interpreted_ast_nodes / cls._ast_nodes_total)
            if cls._ast_nodes_total > 0
            else 0.0
        )

        return Report(
            filename=cls._filename,
            # We only add the condition to the issue here to enable easy deduplication while accumulating issues
            issues=sorted([i.under_constraint(cons) for i, cons in cls._issues.items()], key = lambda i: i.line if i.line is not None else -1),
            time=cls._exec_time,
            solver_time=cls._solver_time,
            timed_out=cls._timed_out,
            ast_nodes_total=cls._ast_nodes_total,
            ast_nodes_interpreted=interpreted_ast_nodes,
            ast_coverage_pct=ast_coverage_pct,
        )
