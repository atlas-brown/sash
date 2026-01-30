import math
import tempfile
from pathlib import Path
from pprint import pformat

import pytest
import shasta.ast_node as AST

import sash.parser as parser
import sash.reporter as reporter
import sash.symb as symb
import sash.main as main
import sash.symbolic.strings


def write_script(tmp_path, content: str) -> str:
    """Helper to write a shell script to a temporary file."""
    path = tmp_path / "script.sh"
    path.write_text(content)
    return str(path)

def is_under_allowable_condition(issue: reporter.Issue, allowable_strs: list[str]) -> bool:
    cond_str = str(issue.condition)
    return not issue.condition or any(s in cond_str for s in allowable_strs)

def assert_expected_report(report: reporter.Report, expected_errors: list[reporter.Issue], allowable_conditions: list[str] = []):
    """Helper to compare actual report with expected errors."""
    actual = [rep.code.value for rep in report.issues if is_under_allowable_condition(rep, allowable_conditions)]
    expected = [err.code.value for err in expected_errors]
    if sorted(actual) != sorted(expected):
        pytest.fail(
            f"\nExpected errors:\n{pformat(sorted(expected))}\n"
            f"Actual errors:\n{pformat(sorted(actual))}\n"
            f"Full report:\n{pformat(report.to_dict())}\n"
        )

def assert_not_expected_report(report, not_expected_errors: list[reporter.Issue]):
    """Helper to ensure that certain errors are not (i.e., should not be expected) in the actual report."""
    actual = [rep["code"] for rep in report["errors"]]
    not_expected = [err.code for err in not_expected_errors]
    for code in not_expected:
        assert code not in actual

def parse_script(script_content: str) -> list[AST.AstNode]:
    with tempfile.TemporaryDirectory() as tmp_path:
        p = write_script(Path(tmp_path), script_content)
        res = []
        for wrapped_ast in parser.parse_shell_script(p):
            res.append(wrapped_ast.ast_node)
        return res

def create_field(val: str) -> sash.symbolic.strings.Field:
    min_words = 0
    max_words = 0

    previously_space = True # to handle leading spaces
    for c in val:
        if c == "*":
            # glob character
            max_words = math.inf
        elif not c.isspace() and previously_space:
            min_words += 1
        previously_space = c.isspace()

    return sash.symbolic.strings.Field(
        create_symstr(val),
        sash.symbolic.strings.WordCount(min_words, max(min_words, max_words))
    )

def create_symstr(val: str) -> sash.symbolic.strings.SymStr:
    return sash.symbolic.strings.SymStr((val,))

def reset_and_run_main(script: str, solver: bool = False) -> reporter.Report:
    """Helper to reset the reporter and run the main analysis on a script."""
    reporter.Reporter.reset()
    report = main.main(script, solver=solver)
    return report

def reset_and_run_symbexec_main(script: str, solver: bool = False) -> symb.SymbexecResult:
    """Helper to reset the reporter and run only the symbolic execution on a script."""
    reporter.Reporter.reset()
    symr = main.symbexec_main(script, solver=solver)
    return symr
