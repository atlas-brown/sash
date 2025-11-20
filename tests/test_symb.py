"""
Tests for static analysis of shell scripts using sash.symb.main.
These tests run sample shell scripts and verify that expected errors or warnings are reported.
"""
import sash.main as symb
import sash.reporter as reporter
import shasta.ast_node as AST
from util import *

reporter.Reporter.initialize("<test>")
foo_var = AST.VArgChar(fmt="Normal", null=False, var="FOO", arg=[])

def test_unbound_variable(tmp_path):
    # Using an unset variable should produce an unbound error
    script = write_script(tmp_path, "echo $FOO\n")
    report = symb.main(script)
    expected_error = reporter.UnboundID(foo_var.pretty(), 0)
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
    script = write_script(tmp_path, 'echo $1 $5 "$@" $# $HOME $PWD\n')
    report = symb.main(script)
    assert_expected_report(report, [])

def test_unbound_variable_cmdsubst(tmp_path):
    # Using an unset variable should produce an unbound error
    script = write_script(tmp_path, "echo $(echo $FOO)\n")
    report = symb.main(script)
    expected_error = reporter.UnboundID(foo_var.pretty(), 0)
    assert_expected_report(report, [expected_error])

    script = write_script(tmp_path, "ls $(echo $FOO)\n")
    report = symb.main(script)
    expected_error = reporter.UnboundID(foo_var.pretty(), 0)
    assert_expected_report(report, [expected_error])

def test_unbound_variable_setu(tmp_path):
    # Using an unset variable with 'set -u' should produce an unbound error
    script = write_script(tmp_path, "set -u\n echo $FOO\n")
    report = symb.main(script)
    expected_error = reporter.UnboundIDSetU(foo_var.pretty(), 0)
    assert_expected_report(report, [expected_error])

def test_delete_system_file(tmp_path):
    # Deleting a system file should produce a DeleteSystemFile error
    script = write_script(tmp_path, "rm /usr\n")
    report = symb.main(script)
    expected_error = reporter.DeleteSystemFile("/usr", 0)
    assert_expected_report(report, [expected_error])

    script = write_script(tmp_path, "rm $FOO/usr\n")
    report = symb.main(script)
    expected_error1 = reporter.WordSplitCouldDeleteSystemFile("/usr", 0)
    expected_error2 = reporter.UnboundID(foo_var.pretty(), 0)
    expected_error3 = reporter.DangerousWordSplit("$FOO", 0)
    assert_expected_report(report, [expected_error1, expected_error2, expected_error3])


    script = write_script(tmp_path, "rm -rf $STEAMROOT/*\n")
    report = symb.main(script)
    expected_error1 = reporter.WordSplitCouldDeleteSystemFile("/*", 0)
    expected_error2 = reporter.UnboundID(foo_var.pretty(), 0)
    expected_error3 = reporter.DangerousWordSplit("$STEAMROOT", 0)
    assert_expected_report(report, [expected_error1, expected_error2, expected_error3])

    script = write_script(tmp_path, "rm -rf /*\n")
    report = symb.main(script)
    expected_error = reporter.DeleteSystemFile("/*", 0)
    assert_expected_report(report, [expected_error])

    script = write_script(tmp_path, "rm -rf \"$FOO\"/\n")
    report = symb.main(script)
    expected_error1 = reporter.WordSplitCouldDeleteSystemFile("/", 0)
    expected_error2 = reporter.UnboundID(foo_var.pretty(), 0)
    assert_expected_report(report, [expected_error1, expected_error2])

    script = write_script(tmp_path, """
if [ "$FOO" = "yes" ]; then
    A=yes
else
    A=no
fi
rm -rf "$FOO/"
""")
    report = symb.main(script)
    expected_error1 = reporter.WordSplitCouldDeleteSystemFile("/", 0)
    expected_error2 = reporter.UnboundID(foo_var.pretty(), 0)
    assert_expected_report(report, [expected_error1, expected_error2])

    # regression test for binding unbound args after first expansion
    script = write_script(tmp_path, """
echo $1
rm -rf "${1-/usr}"
""")
    report = symb.main(script)
    expected_error = reporter.DeleteSystemFile("/usr", 0)
    assert_expected_report(report, [expected_error])

def test_delete_splitting(tmp_path):
    script = write_script(tmp_path, "rm $UNQUOTED\n")
    report = symb.main(script)
    expected_error1 = reporter.DangerousWordSplit("$UNQUOTED", 0)
    expected_error2 = reporter.UnboundID(foo_var.pretty(), 0)
    assert_expected_report(report, [expected_error1, expected_error2])


def test_redirect_to_function(tmp_path):
    # Redirecting output to a function should produce an error
    script = write_script(tmp_path, ("myfunc() { echo hi; }\n"
                                     "echo hello > myfunc\n"))
    report = symb.main(script)
    expected_error = reporter.RedirectToFunction("myfunc", 0)
    assert_expected_report(report, [expected_error])

def test_redirect_to_variable_no_error(tmp_path):
    # Redirecting output to a variable should not produce any errors
    script = write_script(tmp_path, ("myvar=output.txt\n"
                                     "echo hello > $myvar\n"))
    report = symb.main(script)
    assert_expected_report(report, [])


# for i in one; do echo $i; done\n
def test_loop_runs_once__const(tmp_path):
    # A loop over a single constant should produce a LoopRunsOnce warning
    script = write_script(tmp_path, "for i in one; do echo $i; done\n")
    report = symb.main(script)
    expected_warning = reporter.LoopRunsOnce(None, 0)
    assert_expected_report(report, [expected_warning])

# for i in 'one two'; do echo $i; done\n
def test_loop_runs_once__const_multiple_quoted(tmp_path):
    # A loop over multiple quoted constants should produce a LoopRunsOnce warning
    script = write_script(tmp_path, 'for i in "one two"; do echo $i; done\n')
    report = symb.main(script)
    expected_warning = reporter.LoopRunsOnce(None, 0)
    assert_expected_report(report, [expected_warning])

# foo=one\n for i in $foo; do echo $i; done\n
def test_loop_runs_once__var(tmp_path):
    # A loop over a variable assigned a single constant should produce a LoopRunsOnce warning
    script = write_script(tmp_path, "foo=one\n for i in $foo; do echo $i; done\n")
    report = symb.main(script)
    expected_warning = reporter.LoopRunsOnce(None, 0)
    assert_expected_report(report, [expected_warning])

# foo="one two"\n for i in "$foo"; do echo $i; done\n
def test_loop_runs_once__var_multiple_const_quoted(tmp_path):
    # A loop over a quoted variable assigned multiple quoted constants should produce a LoopRunsOnce warning
    script = write_script(tmp_path, 'foo="one two"\n for i in "$foo"; do echo $i; done\n')
    report = symb.main(script)
    expected_warning = reporter.LoopRunsOnce(None, 0)
    assert_expected_report(report, [expected_warning])

# for i in $(echo one); do echo $i; done\n
@pytest.mark.skip(reason="Currently cannot distinguish single vs multiple words from command substitutions")
def test_loop_runs_once__cmdsubst(tmp_path):
    # A loop over a command substitution that produces a single constant should produce a LoopRunsOnce warning
    script = write_script(tmp_path, "for i in $(echo one); do echo $i; done\n")
    report = symb.main(script)
    expected_warning = reporter.LoopRunsOnce(None, 0)
    assert_expected_report(report, [expected_warning])

# for i in "$(echo one two)"; do echo $i; done\n
def test_loop_runs_once__cmdsubst_quoted_multiple_const(tmp_path):
    # A loop over a quoted command substitution that produces multiple quoted constants should produce a LoopRunsOnce warning
    script = write_script(tmp_path, 'for i in "$(echo one two)"; do echo $i; done\n')
    report = symb.main(script)
    expected_warning = reporter.LoopRunsOnce(None, 0)
    assert_expected_report(report, [expected_warning])

# for i in one two; do echo $i; done\n
def test_loop_runs_multiple__no_warning(tmp_path):
    # A loop over multiple constants should not produce a LoopRunsOnce warning
    script = write_script(tmp_path, "for i in one two; do echo $i; done\n")
    report = symb.main(script)
    assert_expected_report(report, [])

    # TODO: Support globs
    # script = write_script(tmp_path, "for i in *.sh; do echo $i; done\n")
    # report = symb.main(script)
    # assert_expected_report(report, [])

# foo='one two'\n for i in $foo; do echo $i; done\n
def test_loop_runs_multiple__var_no_warning(tmp_path):
    # A loop over a variable that is assigned multiple constants should not produce a LoopRunsOnce warning
    script = write_script(tmp_path, "foo='one two'\n for i in $foo; do echo $i; done\n")
    report = symb.main(script)
    assert_expected_report(report, [])

# for i in $(echo 'one two'); do echo $i; done\n
def test_loop_runs_multiple__cmdsubst_no_warning(tmp_path):
    # A loop over a command substitution that produces multiple constants should not produce a LoopRunsOnce warning
    script = write_script(tmp_path, "for i in $(echo 'one two'); do echo $i; done\n")
    report = symb.main(script)
    assert_expected_report(report, [])

# for i in *.sh; do echo $i; done\n
@pytest.mark.skip(reason="Currently no support for globs")
def test_loop_runs_multiple__glob_no_warning(tmp_path):
    # A loop over a glob should not produce a LoopRunsOnce warning
    script = write_script(tmp_path, "for i in *.sh; do echo $i; done\n")
    report = symb.main(script)
    assert_expected_report(report, [])


def test_constant_while_condition(tmp_path):
    # A while loop with a constant true condition should produce an InfiniteLoop error
    script = write_script(tmp_path, "A=a\nB=b\nwhile [ $A != $B ]; do echo hi; done\n")
    report = symb.main(script)
    expected_error = reporter.InfiniteLoop(None, 0) # Mock the location
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
    expected_error = reporter.InfiniteLoop(None, 0) # Mock the location
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
    expected_error = reporter.InfiniteLoop(None, 0) # Mock the location
    assert_expected_report(report, [expected_error])

def test_function_call(tmp_path):
    # A function that is called should not produce unbound variable errors for its parameters
    script = write_script(tmp_path, """
myfunc() {
    rm "$1"
}
myfunc /usr
""")
    report = symb.main(script)
    expected_error = reporter.DeleteSystemFile("/usr", 0)
    assert_expected_report(report, [expected_error])

def test_case(tmp_path):
    # A case statement should handle all branches correctly
    script = write_script(tmp_path, """
case "$1" in
    start)
        rm -rf /usr
        ;;
    *)
        echo "fine"
        ;;
esac
""")
    report = symb.main(script)
    expected_error = reporter.DeleteSystemFile("/usr", 0)
    assert_expected_report(report, [expected_error])

def test_and_or(tmp_path):
    # A case statement should handle all branches correctly
    script = write_script(tmp_path, """
echo hi && rm -rf /usr || rm -rf /*
""")
    report = symb.main(script)
    expected_error1 = reporter.DeleteSystemFile("/usr", 0)
    expected_error2 = reporter.DeleteSystemFile("/*", 0)
    assert_expected_report(report, [expected_error1, expected_error2])

def test_const_cond(tmp_path):
    # A constant condition in an if statement should be detected, and the dead code should not be interpreted
    script = write_script(tmp_path, """
if [ "a" = "b" ]; then
    rm -rf /*
fi
""")
    report = symb.main(script)
    expected_error1 = reporter.ConstantCondition(None, 0) # Mock the location
    expected_error2 = reporter.DeadCode("rm -rf /*", 0)
    assert_expected_report(report, [expected_error1, expected_error2]) # Notice: no DeleteSystemFile error

    script = write_script(tmp_path, """
if [ "a" = "b" ]; then
    rm -rf /*
else
    echo $FOO
fi
""")
    report = symb.main(script)
    expected_error1 = reporter.ConstantCondition(None, 0) # Mock the location
    expected_error2 = reporter.DeadCode("rm -rf /*", 0)
    expected_error3 = reporter.UnboundID(foo_var.pretty(), 0)
    assert_expected_report(report, [expected_error1, expected_error2, expected_error3])

def test_dead_code(tmp_path):
    # Code after exit should not be interpreted
    script = write_script(tmp_path, """
echo $FOO
exit 1
rm -rf /usr
""")
    report = symb.main(script)
    expected_error1 = reporter.UnboundID(foo_var.pretty(), 0)
    expected_error2 = reporter.DeadCode("rm -rf /usr", 0)
    assert_expected_report(report, [expected_error1, expected_error2]) # Notice: no DeleteSystemFile error

    script = write_script(tmp_path, """
wget foobar || exit 1
echo "not dead code!"
""")
    report = symb.main(script)
    assert_expected_report(report, []) # Notice: no DeadCode error

def test_non_command(tmp_path):
    # Invoking a non-command should produce a NotACommand error
    script = write_script(tmp_path, """
foo=bar // this was not a command
""")
    report = symb.main(script)
    expected_error = reporter.NotACommand("//", 0)
    assert_expected_report(report, [expected_error])


# f; f() { :; }\n
def test_fundef_after_call__toplevel_def_toplevel_call(tmp_path):
    """Test that a function defined after its call is reported as "function_use_before_def"."""
    script = write_script(tmp_path, "f; f() { :; }\n")
    report = symb.main(script)
    expected_error = reporter.UndefinedFunction("f", 0)
    assert_expected_report(report, [expected_error])

# f() { g; }; f; g() { :; }\n
def test_fundef_after_call__toplevel_def_infunc_call(tmp_path):
    """Test that a function defined after its call is reported as "function_use_before_def"."""
    script = write_script(tmp_path, "f() { g; }; f; g() { :; }\n")
    report = symb.main(script)
    expected_error = reporter.UndefinedFunction("g", 0)
    assert_expected_report(report, [expected_error])

# f() { g() { :; }; }; g; f\n
def test_fundef_after_call__infunc_def_toplevel_call(tmp_path):
    """Test that a function defined after its call is reported as "function_use_before_def"."""
    script = write_script(tmp_path, "f() { g() { :; }; }; g; f\n")
    report = symb.main(script)
    expected_error = reporter.UndefinedFunction("g", 0)
    assert_expected_report(report, [expected_error])

# f() { g() { :; }; }; h() { g; }; h; f\n
def test_fundef_after_call__infunc_def_infunc_call(tmp_path):
    """Test that a function defined after its call is reported as "function_use_before_def"."""
    script = write_script(tmp_path, "f() { g() { :; }; }; h() { g; }; h; f\n")
    report = symb.main(script)
    expected_error = reporter.UndefinedFunction("g", 0)
    assert_expected_report(report, [expected_error])


# f() { :; }; f\n
def test_fundef_before_call__toplevel_def_toplevel_call_no_error(tmp_path):
    """Test that a function defined before its call does not produce an error."""
    script = write_script(tmp_path, "f() { :; }; f\n")
    report = symb.main(script)
    assert_expected_report(report, [])

# f() { g; }; g() { :; }; f\n
def test_fundef_before_call__toplevel_def_infunc_call_no_error(tmp_path):
    """Test that a function defined before its call does not produce an error."""
    script = write_script(tmp_path, "f() { g; }; g() { :; }; f\n")
    report = symb.main(script)
    assert_expected_report(report, [])

# f() { g() { :; }; }; f; g\n
def test_fundef_before_call__infunc_def_toplevel_call_no_error(tmp_path):
    """Test that a function defined before its call does not produce an error."""
    script = write_script(tmp_path, "f() { g() { :; }; }; f; g\n")
    report = symb.main(script)
    assert_expected_report(report, [])

# f() { g() { :; }; }; h() { g; }; f; h\n
def test_fundef_before_call__infunc_def_infunc_call_no_error(tmp_path):
    """Test that a function defined before its call does not produce an error."""
    script = write_script(tmp_path, "f() { g() { :; }; }; h() { g; }; f; h\n")
    report = symb.main(script)
    assert_expected_report(report, [])


def test_const_cond_triggered_by_exit_code_simple(tmp_path):
    """Test that a constant condition in an if statement based on exit code is detected."""
    script = write_script(tmp_path, """
echo "test" > /dev/null
result="success"
if [ $? -gt 0 ]; then
    echo "This should never run"
fi
""")
    report = symb.main(script)
    expected_error1 = reporter.ConstantCondition(None, 0)
    expected_error2 = reporter.DeadCode('echo "This should never run"', 0)
    assert_expected_report(report, [expected_error1, expected_error2])


def test_const_cond_triggered_by_exit_code_nested_if(tmp_path):
    """Test that a constant condition in an if statement based on exit code is detected."""
    script = write_script(tmp_path, """
if [ -e "var" ]; then
    command1 | command2
    status="done"
    if [ $? -gt 0 ]; then
        echo "This should never run either"
    fi
fi
""")
    report = symb.main(script)
    expected_error1 = reporter.ConstantCondition(None, 0)
    expected_error2 = reporter.DeadCode('echo "This should never run"', 0)
    assert_expected_report(report, [expected_error1, expected_error2])

def test_const_cond_arg_eq(tmp_path):
    """Test that a constant condition in an if statement based on argument equality is detected."""
    script = write_script(tmp_path, """
A=$1
B=$2
if [ "$A" = "$1" ]; then
    echo "This should always run"
fi
""")
    report = symb.main(script)
    expected_error1 = reporter.ConstantCondition(None, 0)
    assert_expected_report(report, [expected_error1])

def test_const_cond_arg_eq_not_in_functions(tmp_path):
    """Test that a constant condition in an if statement based on argument equality is detected."""
    script = write_script(tmp_path, """
A=$1
B=$2
myfunc() {
if [ "$A" = "$1" ]; then
    echo "we cant tell if this will always run"
fi
}
myfunc foo
""")
    report = symb.main(script)
    assert_expected_report(report, [])


def test_double_rm(tmp_path):
    """Test that deleting the same file twice is reported."""
    script = write_script(tmp_path, """
rm somefile.txt
rm somefile.txt
""")
    #report = symb.main(script)
    res = symb.symbexec_main(script, solver=True)
    assert len(res.traces) == 1
    assert len(res.traces[0].latest_state.assertions) == 2
    report = reporter.Reporter.get_report()
    expected_warning = reporter.UnsatisfiedPrecondition(None, "rm somefile.txt", 0)
    assert_expected_report(report, [expected_warning])

def test_double_mv(tmp_path):
    """Test that deleting the same file twice is reported."""
    script = write_script(tmp_path, """
mv somefile.txt otherfile.txt
mv somefile.txt otherfile2.txt
""")
    #report = symb.main(script)
    res = symb.symbexec_main(script, solver=True)
    assert len(res.traces) == 1
    assert len(res.traces[0].latest_state.assertions) == 2
    report = reporter.Reporter.get_report()
    expected_warning = reporter.UnsatisfiedPrecondition(None, "mv somefile.txt otherfile.txt", 0)
    assert_expected_report(report, [expected_warning])


def test_read_after_rm(tmp_path):
    """Test that reading a file after it has been deleted is reported."""
    script = write_script(tmp_path, """
rm "$2"
cp "$2" something.txt
""")
    #report = symb.main(script)
    res = symb.symbexec_main(script, solver=True)
    assert len(res.traces) == 1
    assert len(res.traces[0].latest_state.assertions) == 2
    report = reporter.Reporter.get_report()
    expected_warning = reporter.UnsatisfiedPrecondition(None, "cp \"$2\" something.txt", 0)
    assert_expected_report(report, [expected_warning])

def test_nested_function_localenv(tmp_path):
    # A function that is called should not produce unbound variable errors for its parameters
    script = write_script(tmp_path, """
f1() {
    f2 ok
    rm "$1"
}
f2() {
    echo "$1"
}
f1 /usr
""")
    report = symb.main(script)
    expected_error = reporter.DeleteSystemFile("/usr", 0)
    assert_expected_report(report, [])

def test_pathcond_and_precond(tmp_path):
    # Path conditions should be used to reason about preconditions
    script = write_script(tmp_path, """
rm "$1"
if [ "$2" = "$1" ]; then
    cat "$2"
fi
""")
    report = symb.main(script)
    expected_warning = reporter.UnsatisfiedPrecondition(None, "cat \"$2\"", 0)
    assert_expected_report(report, [expected_warning])

# def test_function_call_multipath(tmp_path):
#     # A function that is called should not produce unbound variable errors for its parameters
#     script = write_script(tmp_path, """
# myfunc() {
#     rm "$1"
# }
# if [ "$2" = "yes" ]; then
#     FOO=/usr
# else
#     FOO=/something/totally/safe
# fi
# myfunc "$FOO"
# """)
#     report = symb.main(script)
#     expected_error = reporter.DeleteSystemFile("/usr", 0)
#     assert_expected_report(report, [expected_error])
