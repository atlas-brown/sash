import tempfile
from pathlib import Path
import shasta.ast_node as AST
import sash.parser as parser
import sash.reporter as reporter

def write_script(tmp_path, content: str) -> str:
    """Helper to write a shell script to a temporary file."""
    path = tmp_path / "script.sh"
    path.write_text(content)
    return str(path)

def assert_expected_report(report, expected_errors: list[reporter.Report]):
    """Helper to compare actual report with expected errors."""
    actual = [rep["code"] for rep in report["errors"]]
    expected = [err.code for err in expected_errors]
    assert sorted(actual) == sorted(expected)

def parse_script(script_content: str) -> list[AST.AstNode]:
    with tempfile.TemporaryDirectory() as tmp_path:
        p = write_script(Path(tmp_path), script_content)
        res = []
        for wrapped_ast in parser.parse_shell_script(p):
            res.append(wrapped_ast.ast_node)
        return res

