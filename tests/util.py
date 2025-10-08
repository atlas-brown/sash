import tempfile
from pathlib import Path
import shasta.ast_node as AST
import sash.shell_parser as parser
import sash.reporter as reporter

def write_script(tmp_path, content: str) -> str:
    """Helper to write a shell script to a temporary file."""
    path = tmp_path / "script.sh"
    path.write_text(content)
    return str(path)

def assert_expected_report(report, expected_errors: list[reporter.Report]):
    """Helper to compare actual report with expected errors."""
    actual = [(err) for err, msg in report["error_messages"]]
    expected = [(err.code) for err in expected_errors]
    assert set(actual) == set(expected)

def parse_script(script_content: str) -> AST.AstNode:
    with tempfile.TemporaryDirectory() as tmp_path:
        p = write_script(Path(tmp_path), script_content)
        res = []
        for top_level_info in parser.parse_shell_to_asts(p):
            res.append(top_level_info[0])
        return res

