from collections.abc import Iterable
import logging
import math
import copy
from abc import ABC
from dataclasses import dataclass, field
from enum import Enum
from sash.interpreter_config import InterpConfig
from sash.constraints import Constraint, Description, Empty
from sash.debugtools.logger import DebugLogger
from sash.symbolic.strings import Field
from typing import ClassVar
import shasta.ast_node as AST

class Severity(Enum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class Issue(ABC):
    code: ClassVar[str]
    severity: ClassVar[Severity]

    message: str
    line: int | None
    constraint: Constraint | None = field(default=None, compare=False, hash=False)


    def is_error(self) -> bool:
        return self.severity == Severity.ERROR

    def is_warning(self) -> bool:
        return self.severity == Severity.WARNING

    def __repr__(self) -> str:
        qualification = f"IF {self.constraint} then " if self.constraint else ""
        return f"L{self.line}:{self.code}: {qualification}{self.message}"

    def to_dict(self) -> dict:
        return {
            "line": self.line,
            "code": self.code,
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
        """Return the set of all possible issue codes."""
        return {subclass.code for subclass in cls.__subclasses__()}


@dataclass(frozen=True)
class ParseError(Issue):
    code = "parse"
    severity = Severity.ERROR

    def __init__(self, msg: str) -> None:
        super().__init__(f"Parse error: {msg}", None)


@dataclass(frozen=True)
class UnboundID(Issue):
    code = "unbound"
    severity = Severity.ERROR

    # Do not include the line when hashing in order to prevent duplicate errors about the same variable
    line: int | None = field(compare=False, hash=False)

    def __init__(self, var: str, line: int | None):
        super().__init__(f"No definition found for '{var}'", line)



@dataclass(frozen=True)
class UnboundIDSetU(Issue):
    code = "unbound_setu"
    severity = Severity.ERROR

    # Do not include the line when hashing in order to prevent duplicate errors about the same variable
    line: int | None = field(compare=False, hash=False)

    def __init__(self, var: str, line: int | None):
        super().__init__(f"No definition found for '{var}' in `set -u` mode", line)


@dataclass(frozen=True)
class UndefinedFunction(Issue):
    code = "function_use_before_def"
    severity = Severity.ERROR

    def __init__(self, name: str, line: int | None):
        super().__init__(f"Function '{name}' is used before its definition", line)


@dataclass(frozen=True)
class InfiniteLoop(Issue):
    code = "infinite_loop"
    severity = Severity.ERROR

    def __init__(self, loop: AST.ForNode | AST.WhileNode, line: int | None):
        super().__init__(f"Condition for the following loop never changes, causing an infinite loop:\n{loop.pretty()}", line)


@dataclass(frozen=True)
class ConstantCondition(Issue):
    code = "const_cond"
    severity = Severity.WARNING

    def __init__(self, cond: AST.Command, line: int | None):
        super().__init__(f"Condition is always true or false:\n{cond.pretty()}", line)


@dataclass(frozen=True)
class LoopRunsOnce(Issue):
    code = "loop_once"
    severity = Severity.WARNING

    def __init__(self, loop: AST.ForNode | AST.WhileNode, line: int | None):
        super().__init__(f"Loop runs only once:\n{loop.pretty()}", line)


@dataclass(frozen=True)
class DeleteSystemFile(Issue):
    code = "del_sys_file"
    severity = Severity.ERROR

    def __init__(self, filename: str, line: int | None):
        super().__init__(f"May delete system file '{filename}'", line)


@dataclass(frozen=True)
class WordSplitCouldDeleteSystemFile(Issue):
    code = "word_split_del_sys_file"
    severity = Severity.ERROR

    def __init__(self, filename: str, line: int | None):
        super().__init__(f"Word splitting or empty variable could lead to deletion of system file {filename}", line)


@dataclass(frozen=True)
class DangerousWordSplit(Issue):
    code = "word_split"
    severity = Severity.WARNING

    def __init__(self, source: AST.CommandNode, line: int | None):
        # TODO: Figure out why source is a tuple sometimes and fix it
        super().__init__(f"Word splitting could lead to unexpected arguments to dangerous commands:\n{source.pretty() if not isinstance(source, tuple) else source[0].pretty()}", line)


@dataclass(frozen=True)
class RedirectToFunction(Issue):
    code = "redir_func"
    severity = Severity.WARNING

    def __init__(self, function_name: str, line: int | None):
        super().__init__(f"Redirecting output to '{function_name}', which is a function, actually writes to a file with that name", line)


@dataclass(frozen=True)
class DeadCode(Issue):
    code = "dead_code"
    severity = Severity.WARNING

    def __init__(self, code: AST.AstNode, line: int | None):
        super().__init__(f"Unreachable code:\n{code.pretty()}", line)


@dataclass(frozen=True)
class EmptyVar(Issue):
    code = "empty_var"
    severity = Severity.WARNING

    def __init__(self, varname: str, line: int | None):
        super().__init__(f"Variable '{varname}' might be empty", line)


@dataclass(frozen=True)
class IgnoredCommandResult(Issue):
    code = "ignored_cmd_result"
    severity = Severity.WARNING

    def __init__(self, command: str, line: int | None):
        super().__init__(f"The output of command '{command}' is ignored.", line)


@dataclass(frozen=True)
class NotACommand(Issue):
    code = "not_a_command"
    severity = Severity.ERROR

    def __init__(self, name: str, line: int | None):
        super().__init__(f"'{name}' is invoked as a command, but it cannot be one", line)


# TODO: Improve error message and detection
@dataclass(frozen=True)
class UnexpectedStdin(Issue):
    code = "unexpected_stdin"
    severity = Severity.ERROR

    def __init__(self, command: str, line: int | None):
        super().__init__(f"Command '{command}' expects input from stdin if the first argument is empty", line)


@dataclass(frozen=True)
class CommandCanOnlyFail(Issue):
    code = "command_can_only_fail"
    severity = Severity.WARNING

    def __init__(self, command: str, line: int | None):
        super().__init__(f"Command '{command}' can only fail", line)


@dataclass(frozen=True)
class CapturingEmptyOutput(Issue):
    code = "capturing_empty_output"
    severity = Severity.WARNING

    def __init__(self, command: str, line: int | None):
        super().__init__(f"Substitution captures output of '{command}', which doesn't produce any", line)


@dataclass(frozen=True)
class ExpectedPathState(Issue):
    code = "cmd_expected_path_state"
    severity = Severity.ERROR

    def __init__(self, command: str, state: str, paths: Iterable[Field], line: int | None):
        super().__init__(f"Command '{command}' expects paths that are {state}, but one or more of the following paths might not be: {', '.join(p.pretty() for p in paths).rstrip()}", line)


@dataclass(frozen=True)
class DataLoss(Issue):
    code = "data_loss"
    severity = Severity.ERROR

    def __init__(self, command: str, paths: Iterable[Field], line: int | None):
        super().__init__(f"Command '{command}' deletes the following paths, one of which has not been read, potentially causing loss of data: {', '.join(p.pretty() for p in paths).rstrip()}", line)


@dataclass(frozen=True)
class DeleteUserDirectory(Issue):
    code = "del_user_dir"
    severity = Severity.WARNING

    def __init__(self, directory: str, line: int | None):
        super().__init__(f"Deletes user directory '{directory}'", line)


@dataclass(frozen=True)
class InconsistentIFS(Issue):
    code = "inconsistent_ifs"
    severity = Severity.WARNING

    def __init__(self, ifs_values: list[str], line: int | None):
        super().__init__(f"IFS differs across traces: {', '.join(repr(value) for value in ifs_values).rstrip()}", line)


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
                lines.append(f"\t\tError code: {issue.code}")
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
                f"{self.filename}:{line}: {issue.severity.value}: {issue.message} [{issue.code}]"
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
    def drop_issues(cls, issue_types: set[type[Issue]]):
        cls._issues = {issue: cons for issue, cons in cls._issues.items() if not type(issue) in issue_types}

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
