from abc import ABC, abstractmethod
import json
import re

from sash.constraints import Description
from sash.reporter import Report, Severity

COLORS = {
    "BOLD": "\033[1m",
    "UNDERLINE": "\033[4m",
    "RED": "\033[31m",
    "YELLOW": "\033[33m",
    "RESET": "\033[0m",
}

SEVERITY_COLORS = {
    Severity.ERROR: "RED",
    Severity.WARNING: "YELLOW",
}


class Formatter(ABC):
    @abstractmethod
    def format(self, report: Report) -> str: ...


class DefaultFormatter(Formatter):
    def format(self, report: Report) -> str:
        msg = f"File: {COLORS['UNDERLINE']}{report.filename}{COLORS['RESET']}\n"
        if not report.issues:
            msg += "No issues detected"
        else:
            for i in report.issues:
                msg += "\n"
                msg += f"Line {COLORS['BOLD']}{'<unknown>' if i.line is None else str(i.line)}{COLORS['RESET']} "
                msg += f"({COLORS[SEVERITY_COLORS[i.severity]]}{i.severity.value.lower()}{COLORS['RESET']}): "
                msg += i.message.replace("\n", "\n    ")
                msg += "\n"
                if i.constraint is not None:
                    assert isinstance(i.constraint, Description)
                    msg += f"  {COLORS['BOLD']}but only if {i.constraint.text}{COLORS['RESET']}"
                    msg += "\n"
        return msg.rstrip("\n")


class JSONFormatter(Formatter):
    def __init__(self, indent: int = 2):
        self.indent = indent

    def format(self, report: Report) -> str:
        obj = {
            "filename": report.filename,
            "issues": [
                {
                    "line": i.line,
                    "severity": i.severity.value.lower(),
                    "code": i.code,
                    "message": i.message,
                    "constraint": i.constraint,
                }
                for i in report.issues
            ],
            "time": report.time,
            "solver_time": report.solver_time,
            "timed_out": report.timed_out,
            "ast_nodes_total": report.ast_nodes_total,
            "ast_nodes_interpreted": report.ast_nodes_interpreted,
            "ast_coverage_pct": report.ast_coverage_pct,
        }
        return json.dumps(obj, indent=self.indent, default=str)


class CompactFormatter(Formatter):
    def format(self, report: Report) -> str:
        msg = ""
        for i in report.issues:
            msg += f"{'<unknown>' if i.line is None else str(i.line)}: "
            msg += re.sub(r"\n[ ]*", "; ", i.message)
            msg += "\n"
        return msg.rstrip("\n")
