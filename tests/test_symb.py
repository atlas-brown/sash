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

def assert_expected_report(report, expected_errors: list[reporter.Report]):
    """Helper to compare actual report with expected errors."""
    actual_errors = report["error_messages"]
    expected = [(err.code, err.message) for err in expected_errors]
    assert set(actual_errors) == set(expected)
# ======

foo_var = AST.VArgChar(fmt="Normal", null=False, var="FOO", arg=[])

def test_unbound_variable(tmp_path):
    # Using an unset variable should produce an unbound error
    script = write_script(tmp_path, "echo $FOO\n")
    report = symb.main(script)
    expected_error = reporter.UnboundID(foo_var.pretty())
    assert_expected_report(report, [expected_error])


def test_bound_variable_no_error(tmp_path):
    # Assigning a variable before use should not produce any errors
    script = write_script(
        tmp_path,
        "FOO=bar\n"
        "echo $FOO\n"
    )
    report = symb.main(script)
    assert_expected_report(report, [])

    # Assign a variable inside a for loop
    script = write_script(
        tmp_path,
        "for FOO in a b; do FOO=bar; done\n"
        "echo $FOO\n"
    )
    report = symb.main(script)
    assert_expected_report(report, [])


def test_delete_system_file(tmp_path):
    # Deleting a system file should produce a DeleteSystemFile error
    script = write_script(tmp_path, "rm /usr\n")
    report = symb.main(script)
    expected_error = reporter.DeleteSystemFile("/usr")
    assert_expected_report(report, [expected_error])

    script = write_script(tmp_path, "rm $FOO/usr\n")
    report = symb.main(script)
    expected_error1 = reporter.DeleteSystemFile("/usr")
    expected_error2 = reporter.UnboundID(foo_var.pretty())
    assert_expected_report(report, [expected_error1, expected_error2])

def test_loop_runs_once(tmp_path):
    # A loop over a single constant should produce a LoopRunsOnce warning
    script = write_script(tmp_path, "for i in foo; do echo $i; done\n")
    report = symb.main(script)
    expected_warning = reporter.LoopRunsOnce()
    assert_expected_report(report, [expected_warning])
    script = write_script(tmp_path, "FOO=once\n"
                                     "for i in $FOO; do echo $i; done\n")
    report.clear()
    report = symb.main(script)
    expected_warning = reporter.LoopRunsOnce()
    assert_expected_report(report, [expected_warning])

def test_loop_runs_multiple_no_warning(tmp_path):
    # A loop over multiple constants should not produce a LoopRunsOnce warning
    script = write_script(tmp_path, "for i in foo bar; do echo $i; done\n")
    report = symb.main(script)
    assert_expected_report(report, [])

    script = write_script(tmp_path, "for i in *.sh; do echo $i; done\n")
    report = symb.main(script)
    assert_expected_report(report, [])

    script = write_script(tmp_path, "for i in $FOO*.sh; do echo $i; done\n")
    report = symb.main(script)
    expected_error = reporter.UnboundID(foo_var.pretty())
    assert_expected_report(report, [expected_error])
