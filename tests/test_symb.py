"""
Tests for static analysis of shell scripts using sash.symb.main.
These tests run sample shell scripts and verify that expected errors or warnings are reported.
"""
import sash.symb as symb
import sash.reporter as reporter
import shasta.ast_node as AST
import sash.reporter as reporter

# Utilities
def write_script(tmp_path, content: str) -> str:
    """Helper to write a shell script to a temporary file."""
    path = tmp_path / "script.sh"
    path.write_text(content)
    return str(path)

def compare_reports(report, expected_errors: list[reporter.ShseerError]):
    """Helper to compare actual report with expected errors."""
    actual_errors = report["error_messages"]
    expected = [(err.code, err.message) for err in expected_errors]
    return sorted(actual_errors) == sorted(expected)

# ======

foo_var = AST.VArgChar(fmt="Normal", null=False, var="FOO", arg=[])

def test_unbound_variable(tmp_path):
    # Using an unset variable should produce an unbound error
    script = write_script(tmp_path, "echo $FOO\n")
    report = symb.main(script)
    expected_error = reporter.UnboundID(foo_var.pretty())
    assert compare_reports(report, [expected_error])


def test_bound_variable_no_error(tmp_path):
    # Assigning a variable before use should not produce any errors
    script = write_script(
        tmp_path,
        "FOO=bar\n"
        "echo $FOO\n"
    )
    report = symb.main(script)
    assert compare_reports(report, [])
