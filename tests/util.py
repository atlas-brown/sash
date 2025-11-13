import tempfile
import pytest
from pprint import pformat
from pathlib import Path
import shasta.ast_node as AST
import sash.parser as parser
import sash.reporter as reporter

def write_script(tmp_path, content: str) -> str:
    """Helper to write a shell script to a temporary file."""
    path = tmp_path / "script.sh"
    path.write_text(content)
    return str(path)

def assert_expected_report(report: reporter.Report, expected_errors: list[reporter.Issue]):
    """Helper to compare actual report with expected errors."""
    actual = [rep.code.value for rep in report.issues]
    expected = [err.code.value for err in expected_errors]
    if sorted(actual) != sorted(expected):
        pytest.fail(
            f"\nExpected errors:\n{pformat(sorted(expected))}\n"
            f"Actual errors:\n{pformat(sorted(actual))}\n"
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

