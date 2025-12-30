"""
Tests for static analysis of shell scripts using sash.symb.main.
These tests run sample shell scripts and verify that expected errors or warnings are reported.
"""
import shasta.ast_node as AST
from util import *

import sash.reporter as reporter

foo_var = AST.VArgChar(fmt="Normal", null=False, var="FOO", arg=[])

def test_unbound_variable(tmp_path):
    # Using an unset variable should produce an unbound error
    script = write_script(tmp_path, "echo $FOO\n")
    report = reset_and_run_main(script)
    expected_error = reporter.UnboundID(foo_var.pretty(), 0)
    assert_expected_report(report, [expected_error])

def test_bound_variable_no_error(tmp_path):
    # Assigning a variable before use should not produce any errors
    script = write_script(
        tmp_path,
        "FOO=bar\n"
        "echo $FOO\n"
    )
    report = reset_and_run_main(script)
    assert_expected_report(report, [])

    # Assign a variable inside a for loop
    script = write_script(
        tmp_path,
        "for FOO in a b; do FOO=bar; done\n"
        "echo $FOO\n"
    )
    report = reset_and_run_main(script)
    assert_expected_report(report, [])

def test_special_vars_no_unbound_error(tmp_path):
    # Using a parameter variable should not produce an unbound error
    script = write_script(tmp_path, 'echo $1 $5 "$@" $# $HOME $PWD\n')
    report = reset_and_run_main(script)
    assert_expected_report(report, [])

def test_unbound_variable_cmdsubst(tmp_path):
    # Using an unset variable should produce an unbound error
    script = write_script(tmp_path, "echo $(echo $FOO)\n")
    report = reset_and_run_main(script)
    expected_error = reporter.UnboundID(foo_var.pretty(), 0)
    assert_expected_report(report, [expected_error])

    script = write_script(tmp_path, "ls $(echo $FOO)\n")
    report = reset_and_run_main(script)
    expected_error = reporter.UnboundID(foo_var.pretty(), 0)
    assert_expected_report(report, [expected_error])

def test_unbound_variable_setu(tmp_path):
    # Using an unset variable with 'set -u' should produce an unbound error
    script = write_script(tmp_path, "set -u\n echo $FOO\n")
    report = reset_and_run_main(script)
    expected_error = reporter.UnboundIDSetU(foo_var.pretty(), 0)
    assert_expected_report(report, [expected_error])

def test_delete_system_file(tmp_path):
    # Deleting a system file should produce a DeleteSystemFile error
    script = write_script(tmp_path, "rm /usr\n")
    report = reset_and_run_main(script)
    expected_error = reporter.DeleteSystemFile("/usr", 0)
    assert_expected_report(report, [expected_error])

    script = write_script(tmp_path, "rm $FOO/usr\n")
    report = reset_and_run_main(script)
    expected_error1 = reporter.WordSplitCouldDeleteSystemFile("/usr", 0)
    expected_error2 = reporter.UnboundID(foo_var.pretty(), 0)
    expected_error3 = reporter.DangerousWordSplit("$FOO", 0)
    assert_expected_report(report, [expected_error1, expected_error2, expected_error3])


    script = write_script(tmp_path, "rm -rf $STEAMROOT/*\n")
    report = reset_and_run_main(script)
    expected_error1 = reporter.WordSplitCouldDeleteSystemFile("/*", 0)
    expected_error2 = reporter.UnboundID(foo_var.pretty(), 0)
    expected_error3 = reporter.DangerousWordSplit("$STEAMROOT", 0)
    assert_expected_report(report, [expected_error1, expected_error2, expected_error3])

    script = write_script(tmp_path, "rm -rf /*\n")
    report = reset_and_run_main(script)
    expected_error = reporter.DeleteSystemFile("/*", 0)
    assert_expected_report(report, [expected_error])

    script = write_script(tmp_path, "rm -rf \"$FOO\"/\n")
    report = reset_and_run_main(script)
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
    report = reset_and_run_main(script)
    expected_error1 = reporter.WordSplitCouldDeleteSystemFile("/", 0)
    expected_error2 = reporter.UnboundID(foo_var.pretty(), 0)
    assert_expected_report(report, [expected_error1, expected_error2])

    # regression test for binding unbound args after first expansion
    script = write_script(tmp_path, """
echo $1
rm -rf "${1-/usr}"
""")
    report = reset_and_run_main(script)
    expected_error = reporter.DeleteSystemFile("/usr", 0)
    assert_expected_report(report, [expected_error])

    script = write_script(tmp_path, """
rm -rf "${DESTDIR}${LIBDIR}/${CAMLP5N}"
""")
    report = reset_and_run_main(script)
    expected_error1 = reporter.WordSplitCouldDeleteSystemFile("${DESTDIR}${LIBDIR}/${CAMLP5N}", 0)
    expected_error2 = reporter.UnboundID("DESTDIR", 0)
    expected_error3 = reporter.UnboundID("LIBDIR", 0)
    expected_error4 = reporter.UnboundID("CAMLP5N", 0)
    assert_expected_report(report, [expected_error1, expected_error2, expected_error3, expected_error4])

def test_delete_splitting(tmp_path):
    script = write_script(tmp_path, "rm $UNQUOTED\n")
    report = reset_and_run_main(script)
    expected_error1 = reporter.DangerousWordSplit("$UNQUOTED", 0)
    expected_error2 = reporter.UnboundID(foo_var.pretty(), 0)
    assert_expected_report(report, [expected_error1, expected_error2])


def test_redirect_to_function(tmp_path):
    # Redirecting output to a function should produce an error
    script = write_script(tmp_path, ("myfunc() { echo hi; }\n"
                                     "echo hello > myfunc\n"))
    report = reset_and_run_main(script)
    expected_error = reporter.RedirectToFunction("myfunc", 0)
    assert_expected_report(report, [expected_error])

def test_redirect_to_variable_no_error(tmp_path):
    # Redirecting output to a variable should not produce any errors
    script = write_script(tmp_path, ("myvar=output.txt\n"
                                     "echo hello > $myvar\n"))
    report = reset_and_run_main(script)
    assert_expected_report(report, [])


# for i in one; do echo $i; done\n
def test_loop_runs_once__const(tmp_path):
    # A loop over a single constant should produce a LoopRunsOnce warning
    script = write_script(tmp_path, "for i in one; do echo $i; done\n")
    report = reset_and_run_main(script)
    expected_warning = reporter.LoopRunsOnce(None, 0)
    assert_expected_report(report, [expected_warning])

# for i in 'one two'; do echo $i; done\n
def test_loop_runs_once__const_multiple_quoted(tmp_path):
    # A loop over multiple quoted constants should produce a LoopRunsOnce warning
    script = write_script(tmp_path, 'for i in "one two"; do echo $i; done\n')
    report = reset_and_run_main(script)
    expected_warning = reporter.LoopRunsOnce(None, 0)
    assert_expected_report(report, [expected_warning])

# foo=one\n for i in $foo; do echo $i; done\n
def test_loop_runs_once__var(tmp_path):
    # A loop over a variable assigned a single constant should produce a LoopRunsOnce warning
    script = write_script(tmp_path, "foo=one\n for i in $foo; do echo $i; done\n")
    report = reset_and_run_main(script)
    expected_warning = reporter.LoopRunsOnce(None, 0)
    assert_expected_report(report, [expected_warning])

# foo="one two"\n for i in "$foo"; do echo $i; done\n
def test_loop_runs_once__var_multiple_const_quoted(tmp_path):
    # A loop over a quoted variable assigned multiple quoted constants should produce a LoopRunsOnce warning
    script = write_script(tmp_path, 'foo="one two"\n for i in "$foo"; do echo $i; done\n')
    report = reset_and_run_main(script)
    expected_warning = reporter.LoopRunsOnce(None, 0)
    assert_expected_report(report, [expected_warning])

# for i in $(echo one); do echo $i; done\n
@pytest.mark.skip(reason="Currently cannot distinguish single vs multiple words from command substitutions")
def test_loop_runs_once__cmdsubst(tmp_path):
    # A loop over a command substitution that produces a single constant should produce a LoopRunsOnce warning
    script = write_script(tmp_path, "for i in $(echo one); do echo $i; done\n")
    report = reset_and_run_main(script)
    expected_warning = reporter.LoopRunsOnce(None, 0)
    assert_expected_report(report, [expected_warning])

# for i in "$(echo one two)"; do echo $i; done\n
def test_loop_runs_once__cmdsubst_quoted_multiple_const(tmp_path):
    # A loop over a quoted command substitution that produces multiple quoted constants should produce a LoopRunsOnce warning
    script = write_script(tmp_path, 'for i in "$(echo one two)"; do echo $i; done\n')
    report = reset_and_run_main(script)
    expected_warning = reporter.LoopRunsOnce(None, 0)
    assert_expected_report(report, [expected_warning])

# for i in one two; do echo $i; done\n
def test_loop_runs_multiple__no_warning(tmp_path):
    # A loop over multiple constants should not produce a LoopRunsOnce warning
    script = write_script(tmp_path, "for i in one two; do echo $i; done\n")
    report = reset_and_run_main(script)
    assert_expected_report(report, [])

    # TODO: Support globs
    # script = write_script(tmp_path, "for i in *.sh; do echo $i; done\n")
    # report = symb.main(script)
    # assert_expected_report(report, [])

# foo='one two'\n for i in $foo; do echo $i; done\n
def test_loop_runs_multiple__var_no_warning(tmp_path):
    # A loop over a variable that is assigned multiple constants should not produce a LoopRunsOnce warning
    script = write_script(tmp_path, "foo='one two'\n for i in $foo; do echo $i; done\n")
    report = reset_and_run_main(script)
    assert_expected_report(report, [])

# for i in $(echo 'one two'); do echo $i; done\n
def test_loop_runs_multiple__cmdsubst_no_warning(tmp_path):
    # A loop over a command substitution that produces multiple constants should not produce a LoopRunsOnce warning
    script = write_script(tmp_path, "for i in $(echo 'one two'); do echo $i; done\n")
    report = reset_and_run_main(script)
    assert_expected_report(report, [])

# for i in *.sh; do echo $i; done\n
def test_loop_runs_multiple__glob_no_warning(tmp_path):
    # A loop over a glob should not produce a LoopRunsOnce warning
    script = write_script(tmp_path, "for i in *.sh; do echo $i; done\n")
    report = reset_and_run_main(script)
    assert_expected_report(report, [])


def test_constant_while_condition(tmp_path):
    # A while loop with a constant true condition should produce an InfiniteLoop error
    script = write_script(tmp_path, "A=a\nB=b\nwhile [ $A != $B ]; do echo hi; done\n")
    report = reset_and_run_main(script)
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
    report = reset_and_run_main(script)
    expected_error = reporter.InfiniteLoop(None, 0) # Mock the location
    assert_expected_report(report, [expected_error])

def test_changing_while_condition_no_error(tmp_path):
    # A while loop where the condition can change should not produce any errors
    script = write_script(tmp_path, "A=a\nB=b\nwhile [ $A != $B ]; do A=$B; done\n")
    report = reset_and_run_main(script)
    assert_expected_report(report, [])

def test_changing_while_condition_error(tmp_path):
    # A while loop where the condition never changes after the first iteration should error
    script = write_script(tmp_path, "A=a\nB=b\nwhile [ $A != $B ]; do A=hello; done\n")
    report = reset_and_run_main(script)
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
    report = reset_and_run_main(script)
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
    report = reset_and_run_main(script)
    expected_error = reporter.DeleteSystemFile("/usr", 0)
    assert_expected_report(report, [expected_error])

def test_and_or(tmp_path):
    # A case statement should handle all branches correctly
    script = write_script(tmp_path, """
echo hi && rm -rf /usr || rm -rf /*
""")
    report = reset_and_run_main(script)
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
    report = reset_and_run_main(script)
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
    report = reset_and_run_main(script)
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
    report = reset_and_run_main(script)
    expected_error1 = reporter.UnboundID(foo_var.pretty(), 0)
    expected_error2 = reporter.DeadCode("rm -rf /usr", 0)
    assert_expected_report(report, [expected_error1, expected_error2]) # Notice: no DeleteSystemFile error

    script = write_script(tmp_path, """
cd foobar || exit 1
echo "not dead code!"
""")
    report = reset_and_run_main(script)
    assert_expected_report(report, []) # Notice: no DeadCode error

def test_non_command(tmp_path):
    # Invoking a non-command should produce a NotACommand error
    script = write_script(tmp_path, """
foo=bar // this was not a command
""")
    report = reset_and_run_main(script)
    expected_error = reporter.NotACommand("//", 0)
    assert_expected_report(report, [expected_error])


# f; f() { :; }\n
def test_fundef_after_call__toplevel_def_toplevel_call(tmp_path):
    """Test that a function defined after its call is reported as "function_use_before_def"."""
    script = write_script(tmp_path, "f; f() { :; }\n")
    report = reset_and_run_main(script)
    expected_error = reporter.UndefinedFunction("f", 0)
    assert_expected_report(report, [expected_error])

# f() { g; }; f; g() { :; }\n
def test_fundef_after_call__toplevel_def_infunc_call(tmp_path):
    """Test that a function defined after its call is reported as "function_use_before_def"."""
    script = write_script(tmp_path, "f() { g; }; f; g() { :; }\n")
    report = reset_and_run_main(script)
    expected_error = reporter.UndefinedFunction("g", 0)
    assert_expected_report(report, [expected_error])

# f() { g() { :; }; }; g; f\n
def test_fundef_after_call__infunc_def_toplevel_call(tmp_path):
    """Test that a function defined after its call is reported as "function_use_before_def"."""
    script = write_script(tmp_path, "f() { g() { :; }; }; g; f\n")
    report = reset_and_run_main(script)
    expected_error = reporter.UndefinedFunction("g", 0)
    assert_expected_report(report, [expected_error])

# f() { g() { :; }; }; h() { g; }; h; f\n
def test_fundef_after_call__infunc_def_infunc_call(tmp_path):
    """Test that a function defined after its call is reported as "function_use_before_def"."""
    script = write_script(tmp_path, "f() { g() { :; }; }; h() { g; }; h; f\n")
    report = reset_and_run_main(script)
    expected_error = reporter.UndefinedFunction("g", 0)
    assert_expected_report(report, [expected_error])


# f() { :; }; f\n
def test_fundef_before_call__toplevel_def_toplevel_call_no_error(tmp_path):
    """Test that a function defined before its call does not produce an error."""
    script = write_script(tmp_path, "f() { :; }; f\n")
    report = reset_and_run_main(script)
    assert_expected_report(report, [])

# f() { g; }; g() { :; }; f\n
def test_fundef_before_call__toplevel_def_infunc_call_no_error(tmp_path):
    """Test that a function defined before its call does not produce an error."""
    script = write_script(tmp_path, "f() { g; }; g() { :; }; f\n")
    report = reset_and_run_main(script)
    assert_expected_report(report, [])

# f() { g() { :; }; }; f; g\n
def test_fundef_before_call__infunc_def_toplevel_call_no_error(tmp_path):
    """Test that a function defined before its call does not produce an error."""
    script = write_script(tmp_path, "f() { g() { :; }; }; f; g\n")
    report = reset_and_run_main(script)
    assert_expected_report(report, [])

# f() { g() { :; }; }; h() { g; }; f; h\n
def test_fundef_before_call__infunc_def_infunc_call_no_error(tmp_path):
    """Test that a function defined before its call does not produce an error."""
    script = write_script(tmp_path, "f() { g() { :; }; }; h() { g; }; f; h\n")
    report = reset_and_run_main(script)
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
    report = reset_and_run_main(script)
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
    report = reset_and_run_main(script)
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
    report = reset_and_run_main(script)
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
    report = reset_and_run_main(script)
    assert_expected_report(report, [])

def test_param_expansion_question_nonempty_no_error(tmp_path):
    """Test that ${VAR:?} continues when unset in symbolic mode and reports word-split."""
    script = write_script(tmp_path, """
rm -rf ${SQUID_PIDFILE_DIR:?}/*
""")
    report = reset_and_run_main(script)
    expected_error1 = reporter.WordSplitCouldDeleteSystemFile("/*", 0)
    expected_error2 = reporter.DangerousWordSplit(None, 0)
    assert_expected_report(report, [expected_error1, expected_error2])

def test_param_expansion_question_empty_policy_exits(tmp_path):
    """Test that ${VAR:?} exits early under EMPTY unbound policy without rm warnings."""
    script = write_script(tmp_path, """
rm -rf ${SQUID_PIDFILE_DIR:?}/*
rm -rf /usr
""")
    reporter.Reporter.reset()
    reporter.Reporter.initialize(script)
    config = symb.InterpConfig(unbound_policy=symb.UnboundVariablePolicy.EMPTY, DFS_first=False)
    res = symb.symbexec_file(script, config, stop=None)
    assert res.status == symb.SymbexecStatus.COMPLETED
    report = reporter.Reporter.get_report()
    expected_error = reporter.DeadCode(None, 0)
    assert_expected_report(report, [expected_error])

def test_param_expansion_question_terminates_empty(tmp_path):
    """Test that ${VAR:?} terminates execution if the variable is empty or unset."""
    script = write_script(tmp_path, """
FOO=""
rm -rf ${FOO:?}/*
rm -rf /usr
""")
    report = reset_and_run_main(script)
    expected_error1 = reporter.DeadCode(None, 0)
    assert_expected_report(report, [expected_error1])

def test_question_nonempty(tmp_path):
    """Test that ${VAR:?} halts execution if VAR is unset."""
    script = write_script(tmp_path, """
    echo ${UNSET_VAR:?}
    rm -rf /usr
    """)
    report = reset_and_run_main(script)
    expected_error1 = reporter.DeleteSystemFile("/usr", 0)
    assert_expected_report(report, [expected_error1])


def test_double_rm(tmp_path):
    """Test that deleting the same file twice is reported."""
    script = write_script(tmp_path, """
rm somefile.txt
rm somefile.txt
""")
    res = reset_and_run_symbexec_main(script, solver=True)
    report = reporter.Reporter.get_report()
    assert len(res.traces) == 1
    assert len(res.traces[0].latest_state.assertions) == 2
    expected_warning = reporter.UnsatisfiedPrecondition(None, "rm somefile.txt", 0)
    assert_expected_report(report, [expected_warning])

def test_double_mv(tmp_path):
    """Test that deleting the same file twice is reported."""
    script = write_script(tmp_path, """
mv somefile.txt otherfile.txt
mv somefile.txt otherfile2.txt
""")
    res = reset_and_run_symbexec_main(script, solver=True)
    report = reporter.Reporter.get_report()
    assert len(res.traces) == 1
    assert len(res.traces[0].latest_state.assertions) == 2
    expected_warning = reporter.UnsatisfiedPrecondition(None, "mv somefile.txt otherfile.txt", 0)
    assert_expected_report(report, [expected_warning])


def test_read_after_rm(tmp_path):
    """Test that reading a file after it has been deleted is reported."""
    script = write_script(tmp_path, """
rm "$2"
cp "$2" something.txt
""")
    res = reset_and_run_symbexec_main(script, solver=True)
    report = reporter.Reporter.get_report()
    assert len(res.traces) == 1
    assert len(res.traces[0].latest_state.assertions) == 2
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
    report = reset_and_run_main(script)
    assert_expected_report(report, [])

def test_pathcond_and_precond(tmp_path):
    # Path conditions should be used to reason about preconditions
    script = write_script(tmp_path, """
rm "$1"
if [ "$2" = "$1" ]; then
    cat "$2"
fi
""")
    report = reset_and_run_main(script, solver=True)
    expected_warning = reporter.UnsatisfiedPrecondition(None, "cat \"$2\"", 0)
    assert_expected_report(report, [expected_warning])

def test_read_binds_single_variable(tmp_path):
    """Test that read `FOO` binds variable `FOO`; later use should be bound."""
    script = write_script(tmp_path, "read FOO\necho $FOO\n")
    report = reset_and_run_main(script)
    assert_expected_report(report, [])

def test_read_no_variables(tmp_path):
    """Test that read with no args does not bind any variables; later use should be unbound."""
    script = write_script(tmp_path, "read\necho $FOO\n")
    report = reset_and_run_main(script)
    expected_error = reporter.UnboundID(foo_var.pretty(), 0)
    assert_expected_report(report, [expected_error])

def test_read_binds_multiple_variables(tmp_path):
    """Test that read FOO BAR binds both FOO and BAR; later use should be bound."""
    script = write_script(tmp_path, "read FOO BAR\necho $FOO $BAR\n")
    report = reset_and_run_main(script)
    assert_expected_report(report, [])

def test_read_binds_quoted_variable_name(tmp_path):
    """Test that read `"FOO"` binds variable `FOO`; later use should be bound."""
    script = write_script(tmp_path, 'read "FOO"\necho $FOO\n')
    report = reset_and_run_main(script)
    assert_expected_report(report, [])

def test_redirection_to_unread_file(tmp_path):
    """Test that redirecting input from a file that was not read produces an error."""
    script = write_script(tmp_path, """
    x=$(pwd)
    echo "Hello" > $x.txt
    echo "bug" > $x.txt
    """)
    report = reset_and_run_main(script, solver=True)
    expected_error = reporter.UnsatisfiedPrecondition(None, 'echo "bug" > $x.txt', 0)
    assert_expected_report(report, [expected_error])

def test_redirection_to_read_file(tmp_path):
    """Test that redirecting input from a file that was not read produces an error."""
    script = write_script(tmp_path, """
    x=$(pwd)
    echo "Hello" > $x.txt
    cat $x.txt
    echo "bug" > $x.txt
    """)
    report = reset_and_run_main(script, solver=True)
    assert_expected_report(report, [])

def test_mv_unread_file(tmp_path):
    """Test that moving a file that was not read produces an error."""
    script = write_script(tmp_path, """
    x=$(pwd)
    y=$(pwd)/other
    echo "Hello" > $x.txt
    cat $x.txt
    echo "Hello" > $y.txt
    mv $x.txt $y.txt
    """)
    report = reset_and_run_main(script, solver=True)
    expected_error = reporter.UnsatisfiedPrecondition(None, 'mv $x.txt $y.txt', 0)
    assert_expected_report(report, [expected_error])

def test_mv_read_file(tmp_path):
    """Test that moving a file that was read does not produce an error."""
    script = write_script(tmp_path, """
    x=$(pwd)
    y=$(pwd)/other
    echo "Hello" > $x.txt
    echo "Hello" > $y.txt
    cat $y.txt # cat reads the file so there is no error
    mv $x.txt $y.txt
    """)
    report = reset_and_run_main(script, solver=True)
    assert_expected_report(report, [])

def test_xargs_rm(tmp_path):
    """Test that deleting the same file twice is reported."""
    script = write_script(tmp_path, """
xargs -I thing rm somefile.txt thing
""")
    res = reset_and_run_symbexec_main(script, solver=True)
    report = reporter.Reporter.get_report()
    assert len(res.traces) == 1
    assert len(res.traces[0].latest_state.assertions) == 2
    expected_warning = reporter.UnsatisfiedPrecondition(None, "rm somefile.txt thing", 0)
    expected_warning2 = reporter.DangerousWordSplit(None, 0)
    assert_expected_report(report, [expected_warning, expected_warning2])

def test_grep_no_pattern(tmp_path):
    """Test that `grep` with no pattern is reported as an unexpected stdin issue."""
    script = write_script(tmp_path, """
grep $1 somefile.txt
""")
    report = reset_and_run_main(script, solver=True)
    expected_error = reporter.UnexpectedStdin("grep", 0)
    assert_expected_report(report, [expected_error])

def test_var_assignment_in_pipeline_not_persistent(tmp_path):
    """Test that variable assignments from `${var:=default}` in pipelines don't persist to the outer scope."""
    script = write_script(tmp_path, """
file ${FOO:=value} | cat
echo $FOO
""")
    report = reset_and_run_main(script, solver=True)
    foo_assign_var = AST.VArgChar(fmt="Normal", null=True, var="FOO", arg=[])
    expected_error1 = reporter.UnboundID(foo_assign_var.pretty(), 0)
    expected_error2 = reporter.UnboundID(foo_var.pretty(), 1)
    assert_expected_report(report, [expected_error1, expected_error2])

def test_mkdir_can_only_fail(tmp_path):
    """Test that `mkdir` can only fail if the argument is empty."""
    script = write_script(tmp_path, """
mkdir $1
""")
    report = reset_and_run_main(script, solver=True)
    expected_error = reporter.CommandCanOnlyFail("mkdir", 0)
    assert_expected_report(report, [expected_error])

def test_mkdir_produces_empty_output(tmp_path):
    """Test that `mkdir` with no verbose flag produces empty output if the argument is not empty."""
    script = write_script(tmp_path, """
path=/tmp/test
a=`mkdir $path`
echo "$a"
""")
    report = reset_and_run_main(script, solver=True)
    expected_error = reporter.CapturingEmptyOutput("mkdir", 0)
    assert_expected_report(report, [expected_error])

def test_mkdir_produces_nonempty_output_with_verbose(tmp_path):
    """Test that `mkdir` with the verbose flag produces output if the argument is not empty."""
    script = write_script(tmp_path, """
path=/tmp/test
a=`mkdir -v $path`
echo "$a"
""")
    report = reset_and_run_main(script, solver=True)
    assert_expected_report(report, [])

def test_env_invokes_nonexistent_command(tmp_path):
    """Test that `env` invoking a nonexistent command produces an error."""
    script = write_script(tmp_path, """
env NONEXISTENT_COMMAND
""")
    report = reset_and_run_main(script, solver=True)
    expected_error = reporter.NotACommand("NONEXISTENT_COMMAND", 0)
    assert_expected_report(report, [expected_error])

def test_env_invokes_nonexistent_command_with_env_vars_assigned_in_between(tmp_path):
    """Test that `env` invoking a nonexistent command with env vars assigned in between produces an error."""
    script = write_script(tmp_path, """
env VAR1=value1 VAR2=value2 NONEXISTENT_COMMAND
""")
    report = reset_and_run_main(script, solver=True)
    expected_error = reporter.NotACommand("NONEXISTENT_COMMAND", 0)
    assert_expected_report(report, [expected_error])

def test_command_exists(tmp_path):
    """Test that calling a non-existent command produces an error."""
    script = write_script(tmp_path, """
    if command -v nonexistentcommand >/dev/null 2>&1; then
     exit
    fi
    nonexistentcommand arg1 arg2
    """)
    report = reset_and_run_main(script, solver=True)
    expected_error = reporter.NotACommand("nonexistentcommand", 0)
    assert_expected_report(report, [expected_error])

    script = write_script(tmp_path, """
    if ! command -v existentcommand >/dev/null 2>&1; then
        exit
    fi
    existentcommand arg1 arg2
    """)
    report = reset_and_run_main(script, solver=True)
    assert_expected_report(report, [])

def test_command_exists_no_error(tmp_path):
    """Test that calling an existent command does not produce an error."""
    script = write_script(tmp_path, """
    if command -v a_command >/dev/null 2>&1; then
     exit
    fi
    other_cmd "Hello, World!"
    """)
    report = reset_and_run_main(script, solver=True)
    assert_expected_report(report, [])

def test_command_not_exists_no_error(tmp_path):
    """Test that calling a non-existent command after checking its non-existence does not produce an error."""
    script = write_script(tmp_path, """
    command -v git >/dev/null 2>&1 || {
        echo "Error: git is not installed"
        exit 1
    }
    env git --version
    """)
    report = reset_and_run_main(script, solver=True)
    assert_expected_report(report, [])

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
