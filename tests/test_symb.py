"""
Tests for static analysis of shell scripts using sash.symb.main.
These tests run sample shell scripts and verify that expected errors or warnings are reported.
"""
import sash.symb as symb
import sash.reporter as reporter
import shasta.ast_node as AST
from util import *

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

def test_special_vars_no_unbound_error(tmp_path):
    # Using a parameter variable should not produce an unbound error
    script = write_script(tmp_path, 'echo $1 "$@" $# $HOME $PWD\n')
    report = symb.main(script)
    assert_expected_report(report, [])

def test_unbound_variable_cmdsubst(tmp_path):
    # Using an unset variable should produce an unbound error
    script = write_script(tmp_path, "echo $(echo $FOO)\n")
    report = symb.main(script)
    expected_error = reporter.UnboundID(foo_var.pretty())
    assert_expected_report(report, [expected_error])

def test_delete_system_file(tmp_path):
    # Deleting a system file should produce a DeleteSystemFile error
    script = write_script(tmp_path, "rm /usr\n")
    report = symb.main(script)
    expected_error = reporter.DeleteSystemFile("/usr")
    assert_expected_report(report, [expected_error])

    script = write_script(tmp_path, "rm $FOO/usr\n")
    report = symb.main(script)
    expected_error1 = reporter.DeleteSystemFile("")
    expected_error2 = reporter.UnboundID(foo_var.pretty())
    expected_error3 = reporter.DangerousWordSplit("$FOO")
    assert_expected_report(report, [expected_error1, expected_error2, expected_error3])


    script = write_script(tmp_path, "rm -rf $STEAMROOT/*\n")
    report = symb.main(script)
    expected_error1 = reporter.DeleteSystemFile("")
    expected_error2 = reporter.UnboundID(foo_var.pretty())
    expected_error3 = reporter.DangerousWordSplit("$FOO")
    assert_expected_report(report, [expected_error1, expected_error2, expected_error3])

    script = write_script(tmp_path, "rm -rf /*\n")
    report = symb.main(script)
    expected_error = reporter.DeleteSystemFile("/*")
    assert_expected_report(report, [expected_error])

def test_delete_splitting(tmp_path):
    script = write_script(tmp_path, "rm $UNQUOTED\n")
    report = symb.main(script)
    expected_error1 = reporter.DangerousWordSplit("$UNQUOTED")
    expected_error2 = reporter.UnboundID(foo_var.pretty())
    assert_expected_report(report, [expected_error1, expected_error2])


def test_redirect_to_function(tmp_path):
    # Redirecting output to a function should produce an error
    script = write_script(tmp_path, ("myfunc() { echo hi; }\n"
                                     "echo hello > myfunc\n"))
    report = symb.main(script)
    expected_error = reporter.RedirectToFunction("myfunc")
    assert_expected_report(report, [expected_error])

def test_redirect_to_variable_no_error(tmp_path):
    # Redirecting output to a variable should not produce any errors
    script = write_script(tmp_path, ("myvar=output.txt\n"
                                     "echo hello > $myvar\n"))
    report = symb.main(script)
    assert_expected_report(report, [])


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

    # TODO: Support globs
    # script = write_script(tmp_path, "for i in *.sh; do echo $i; done\n")
    # report = symb.main(script)
    # assert_expected_report(report, [])

    script = write_script(tmp_path, "for i in $FOO*.sh; do echo $i; done\n")
    report = symb.main(script)
    expected_error = reporter.UnboundID(foo_var.pretty())
    assert_expected_report(report, [expected_error])

def test_constant_while_condition(tmp_path):
    # A while loop with a constant true condition should produce an InfiniteLoop error
    script = write_script(tmp_path, "A=a\nB=b\nwhile [ $A != $B ]; do echo hi; done\n")
    report = symb.main(script)
    expected_error = reporter.InfiniteLoop(None) # Mock the location
    assert_expected_report(report, [expected_error])

def test_constant_while_condition2(tmp_path):
    script = write_script(tmp_path, """
NUMSNAPS=$(ls while | awk '{print $1}' | wc -l)
RETAIN=2

while [ "$RETAIN" -le "$NUMSNAPS" ]; do
    echo hi
done
""")
    report = symb.main(script)
    expected_error = reporter.InfiniteLoop(None) # Mock the location
    assert_expected_report(report, [expected_error])

def test_changing_while_condition_no_error(tmp_path):
    # A while loop where the condition can change should not produce any errors
    script = write_script(tmp_path, "A=a\nB=b\nwhile [ $A != $B ]; do A=$B; done\n")
    report = symb.main(script)
    assert_expected_report(report, [])

def test_changing_while_condition_error(tmp_path):
    # A while loop where the condition never changes after the first iteration should error
    script = write_script(tmp_path, "A=a\nB=b\nwhile [ $A != $B ]; do A=hello; done\n")
    report = symb.main(script)
    expected_error = reporter.InfiniteLoop(None) # Mock the location
    assert_expected_report(report, [expected_error])

