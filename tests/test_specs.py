from itertools import combinations
from pprint import pformat

import z3
from hypothesis import assume, given, settings
from hypothesis import strategies as st
from sash import reporter
from sash.fs import FSModelSimple
import sash.symbolic.strings
from sash.symbolic.strings import Field
from util import assert_expected_report, create_field, create_symstr, reset_and_run_main, write_script

import sash.specs as specs
from sash.constraints import (
    And,
    Empty,
    Implies,
    IsDeleted,
    IsFile,
    IsRead,
    Not,
    Or,
)
from sash.solver import assertion_to_z3
from sash.symbolic.state import Assertion, State

# Nice little message for copilot:
#   check: (essentially) the precondition that must hold for a command to succeed, but not exactly
#   success_postcond: the postcondition that holds if the command succeeds
#   failure_postcond: the postcondition that holds if the command fails
# The things that we can check are limited to simple file system constraints and variable string equalities


def sanity_check_spec_constraints(cmd_name: str, cmd_spec: specs.CmdSpec):
    fs_model = FSModelSimple(lambda _: z3.FreshConst(z3.StringSort(), "field"))

    fs_model.apply_postcondition(cmd_spec.success_postcond.normalized())
    fs_model.apply_postcondition(cmd_spec.failure_postcond.normalized())

    if cmd_spec.check:
        _, state_formula, full_check_formula, refinements = assertion_to_z3(Assertion(
            producing_state=State(fs_model=fs_model),
            constraint=cmd_spec.check,
            source_str="",
            source_line=0
        ))
        solver = z3.Solver()
        solver.add(state_formula)
        assert solver.check(z3.Not(z3.Implies(full_check_formula, z3.And(*[refinement_formula for refinement_formula, _ in refinements])))) == z3.unsat, "RefineableConstraint: full check does not imply all refinements"
        if solver.check(full_check_formula) == z3.unsat and len(refinements) > 1:
            combination_so_far = z3.BoolVal(True)
            some_fail = False
            for refinement_formula, _ in refinements:
                combination_so_far = z3.And(combination_so_far, refinement_formula)
                if solver.check(combination_so_far) == z3.unsat:
                    some_fail = True
            assert some_fail, f"RefineableConstraint: {cmd_name} spec full check failure does not imply any refinements fail.\n\nFull spec: {full_check_formula}\n\nRefinements: {[refinement_formula for refinement_formula, _ in refinements]}"


def test_rm_spec__check_disallows_deleting_unread_files():
    cmd_name = create_symstr("rm")
    permutations_of_filenames = [
        [create_field(f"file{i}_{j}") for i in range(j)] for j in [1, 2, 10]
    ]

    supported_flags = list(specs.Rm.supported_flags)
    permutations_of_flags: list[set[str]] = []

    n = len(supported_flags)
    for r in range(n + 1):
        for combo in combinations(supported_flags, r):
            permutations_of_flags.append(set(combo))

    invocations = [
        create_cmd_inv(cmd_name, flags, {}, filenames)
        for flags in permutations_of_flags
        for filenames in permutations_of_filenames
    ]

    # Every possible check that rm produces must disallow deleting unread files
    expected_checks_per_inv = [
        [IsRead(f) for f in inv.operands]
        for inv in invocations
    ]

    generated_specs = [specs.rm_spec(inv) for inv in invocations]
    for inv, s, ecs in zip(invocations, generated_specs, expected_checks_per_inv):
        for ec in ecs:
            assert constraint_contains(s.check.full, ec), f"Expected check to contain:\n{pformat(ec)}\nbut got:\n{pformat(s.check)}\nfor invocation:\n{pformat(inv)}"
        sanity_check_spec_constraints('rm', s)


def test_rm_spec__z_postcond_is_deleted_operands():
    cmd_name = create_symstr("rm")
    permutations_of_filenames = [
        [create_field(f"file{i}_{j}") for i in range(j)] for j in [1, 2, 10]
    ]

    supported_flags = list(specs.Rm.supported_flags)
    permutations_of_flags: list[set[str]] = []

    n = len(supported_flags)
    for r in range(n + 1):
        for combo in combinations(supported_flags, r):
            permutations_of_flags.append(set(combo))

    invocations = [
        create_cmd_inv(cmd_name, flags, {}, filenames)
        for flags in permutations_of_flags
        for filenames in permutations_of_filenames
    ]

    # Every possible success_postcond that rm produces must be just the deletion of the operands
    expected_spost = [
        And.from_field_iter(inv.operands, IsDeleted)
        for inv in invocations
    ]

    generated_specs = [specs.rm_spec(inv) for inv in invocations]
    for inv, s, esp in zip(invocations, generated_specs, expected_spost):
        assert s.success_postcond == esp, f"Expected check to be:\n{pformat(esp)}\nbut got:\n{pformat(s.success_postcond)}\nfor invocation:\n{pformat(inv)}"
        sanity_check_spec_constraints('rm', s)


def test_rm_spec__failure_postcond_is_empty():
    cmd_name = create_symstr("rm")
    permutations_of_filenames = [
        [create_field(f"file{i}_{j}") for i in range(j)] for j in [1, 2, 10]
    ]

    supported_flags = list(specs.Rm.supported_flags)
    permutations_of_flags: list[set[str]] = []

    n = len(supported_flags)
    for r in range(n + 1):
        for combo in combinations(supported_flags, r):
            permutations_of_flags.append(set(combo))

    invocations = [
        create_cmd_inv(cmd_name, flags, {}, filenames)
        for flags in permutations_of_flags
        for filenames in permutations_of_filenames
    ]

    generated_specs = [specs.rm_spec(inv) for inv in invocations]
    for s in generated_specs:
        assert s.failure_postcond == Empty(), f"Expected failure postcondition to be Empty, but got:\n{pformat(s.failure_postcond)}"
        sanity_check_spec_constraints('rm', s)


def test_test_spec__check_is_always_empty():
    cmd_name = create_symstr("test")
    op_1 = create_field("op1")
    op_2 = create_field("op2")

    negation_field = create_field("!")
    flag_fields = [create_field(s) for s in ["-d", "-f", "-e", "-n", "-z", "-r", "-w", "-x"]]
    binop_fields = [create_field(s) for s in ["=", "!=", "-eq", "-ne", "-gt", "-lt", "-ge", "-le"]]

    invocations: list[specs.CmdInvocation] = []
    # single operand (with optional negation)
    for flag in flag_fields:
        invocations.append(create_cmd_inv(cmd_name, set(), {}, [flag]))
        invocations.append(create_cmd_inv(cmd_name, set(), {}, [negation_field, flag]))

    # binary operands (with optional negation)
    for binop in binop_fields:
        invocations.append(create_cmd_inv(cmd_name, set(), {}, [op_1, binop, op_2]))
        invocations.append(create_cmd_inv(cmd_name, set(), {}, [negation_field, op_1, binop, op_2]))

    generated_specs = [specs.test_spec(inv) for inv in invocations]
    for s in generated_specs:
        assert not s.check, f"Expected check to be Empty, but got:\n{pformat(s.check)}"
        sanity_check_spec_constraints('test', s)


def test_test_spec__postconds_are_negations_of_each_other():
    cmd_name = create_symstr("test")
    op_1 = create_field("op1")
    op_2 = create_field("op2")

    negation_field = create_field("!")
    flag_fields = [create_field(s) for s in ["-d", "-f", "-e", "-n", "-z", "-r", "-w", "-x"]]
    binop_fields = [create_field(s) for s in ["=", "!=", "-eq", "-ne", "-gt", "-lt", "-ge", "-le"]]

    invocations: list[specs.CmdInvocation] = []
    # single operand (with optional negation)
    for flag in flag_fields:
        invocations.append(create_cmd_inv(cmd_name, set(), {}, [flag]))
        invocations.append(create_cmd_inv(cmd_name, set(), {}, [negation_field, flag]))

    # binary operands (with optional negation)
    for binop in binop_fields:
        invocations.append(create_cmd_inv(cmd_name, set(), {}, [op_1, binop, op_2]))
        invocations.append(create_cmd_inv(cmd_name, set(), {}, [negation_field, op_1, binop, op_2]))

    generated_specs = [specs.test_spec(inv) for inv in invocations]
    for s in generated_specs:
        assert (s.success_postcond, s.failure_postcond) != (Empty(), Empty()), "Both postconds empty even though supported, correct invocations"
        if (s.success_postcond == Empty() or s.failure_postcond == Empty()):
            continue

        assert (
            s.success_postcond == ~s.failure_postcond or s.failure_postcond == ~s.success_postcond
        ), f"Postconds must be negations of each other, but got:\nSuccess:\n{pformat(s.success_postcond)}\nFailure:\n{pformat(s.failure_postcond)}"

        sanity_check_spec_constraints('test', s)


def create_cmd_inv(cmd_name: sash.symbolic.strings.SymStr, flags: set[str], options: dict[str, sash.symbolic.strings.Field], operands: list[sash.symbolic.strings.Field]) -> specs.CmdInvocation:
    return specs.CmdInvocation(
        cmd_name=cmd_name,
        flags=flags,
        options=options,
        operands=operands
    )


def constraint_contains(constraint, subconstraint) -> bool:
    if constraint == subconstraint:
        return True

    if isinstance(constraint, And) or isinstance(constraint, Or):
        if constraint_contains(constraint.lhs, subconstraint):
            return True

        if constraint_contains(constraint.rhs, subconstraint):
            return True

    if isinstance(constraint, Not):
        return constraint_contains(constraint.constraint, subconstraint)

    if isinstance(constraint, Implies):
        return constraint_contains(constraint.premise, subconstraint) or constraint_contains(constraint.conclusion, subconstraint)

    return False


@settings(max_examples=200, deadline=None)
@given(
    cmd_name=st.sampled_from(sorted(set(specs.CMD_SPECS.keys()) - {"sudo", "command"})),
    args=st.lists(
        st.one_of(
            # Common flag-like tokens and operators that many specs understand
            st.sampled_from([
                "-f", "-R", "-r", "-d", "-i", "-v", "-p", "-q", "-V", "-c",
                "-eq", "-ne", "-gt", "-lt", "-ge", "-le",
                "-e", "-n", "-z", "-r", "-w", "-x", "!", "-", "]"
            ]),
            # Alphanumeric tokens
            st.from_regex(r"[A-Za-z0-9_\-]{1,8}", fullmatch=True),
            # name=value style
            st.builds(
                lambda a, b: f"{a}={b}",
                st.from_regex(r"[A-Za-z]{1,6}", fullmatch=True),
                st.from_regex(r"[A-Za-z0-9]{1,6}", fullmatch=True),
            ),
        ),
        min_size=0,
        max_size=8,
    ),
)
def test_hypothesis_specs_to_constraints_do_not_crash(cmd_name: str, args: list[str]):
    # Build Fields (first token is the command name)
    fields = tuple([Field.create_constant(cmd_name)] + [Field.create_constant(a) for a in args])
    cmd_spec = specs.get_spec(cmd_name, fields)
    assert cmd_spec is not None, f"Spec function for command '{cmd_name}' returned None for fields: {pformat(fields)}"

    assume(cmd_spec.success_postcond != Empty() or cmd_spec.failure_postcond != Empty())
    sanity_check_spec_constraints(cmd_name, cmd_spec)


def test_access_after_mv_core(tmp_path):
    script = write_script(tmp_path, """
    mv /opt/actualbudget /opt/actualbudget_bak
    mv actualbudget-actual-server-*/* /opt/actualbudget/
    """)

    report = reset_and_run_main(script, solver=True)
    expected_report = reporter.ExpectedPathState('mv', 'directory', ('/opt/actualbudget/',), 2)
    assert_expected_report(report, [expected_report])


def test_access_after_mv_core_fixed(tmp_path):
    script = write_script(tmp_path, """
    mv /opt/actualbudget /opt/actualbudget_bak
    mkdir /opt/actualbudget
    mv actualbudget-actual-server-*/* /opt/actualbudget/
    """)

    report = reset_and_run_main(script, solver=True)
    assert_expected_report(report, [])


def test_access_del_resource_core(tmp_path):
    script = write_script(tmp_path, """
    mkdir workingfolder
    mv -f workingfolder/* /storage/sort_tv
    rm -rf workingfolder
    mv -f workingfolder/* /storage/sort_tv
    """)

    report = reset_and_run_main(script, solver=True)
    expected_report = reporter.ExpectedPathState('mv', 'existant', ('workingfolder',), 4)
    assert_expected_report(report, [expected_report])


def test_access_del_resource_core_fixed(tmp_path):
    script = write_script(tmp_path, """
    mkdir workingfolder
    mv -f workingfolder/* /storage/sort_tv
    mv -f workingfolder/* /storage/sort_tv
    rm -rf workingfolder
    """)

    report = reset_and_run_main(script, solver=True)
    assert_expected_report(report, [])


def test_overwrite_file_4_core(tmp_path):
    script = write_script(tmp_path, """
    x=$0 # To suppress unbound variable error
    echo "libname sasdata '$x';" > $x/chk.sas
    echo "proc print data=sasdata.data ;" > $x/chk.sas
    echo "run;" > $x/chk.sas
    """)

    report = reset_and_run_main(script, solver=True)
    expected_reports: list[reporter.Issue] = [
        reporter.DataLoss('echo', ('$x/chk.sas',), 3),
        reporter.DataLoss('echo', ('$x/chk.sas',), 4),
    ]
    assert_expected_report(report, expected_reports)


def test_overwrite_file_4_core_fixed(tmp_path):
    script = write_script(tmp_path, """
    x=$0 # To suppress unbound variable error
    echo "libname sasdata '$x';" > $x/chk.sas
    echo "proc print data=sasdata.data ;" >> $x/chk.sas
    echo "run;" >> $x/chk.sas
    """)

    report = reset_and_run_main(script, solver=True)
    assert_expected_report(report, [])


def test_overwrite_file_xargs_core(tmp_path):
    script = write_script(tmp_path, """
    touch target
    find . -name '*.R' | xargs -I files mv files target
    find . -name '*.sh' | xargs -I files mv files target
    """)

    report = reset_and_run_main(script, solver=True)
    expected_reports: list[reporter.Issue] = [
        # For each line, we get two unsat: one for overwriting target (before reading it) and one for moving file (after moving it)
        reporter.DataLoss('mv', ('target',), 2),
        reporter.DataLoss('mv', ('target',), 3),
    ]
    assert_expected_report(report, expected_reports)

def test_overwrite_file_xargs_core_fixed(tmp_path):
    script = write_script(tmp_path, """
    find . -name '*.R' | xargs -I files mv files target
    find . -name '*.sh' | xargs -I files mv files target
    """)

    report = reset_and_run_main(script, solver=True)
    expected_reports: list[reporter.Issue] = [
    ]
    assert_expected_report(report, expected_reports)

def test_overwrite_file_xargs_core_fixed_mkdir(tmp_path):
    script = write_script(tmp_path, """
    mkdir target
    find . -name '*.R' | xargs -I files mv files target
    find . -name '*.sh' | xargs -I files mv files target
    """)

    report = reset_and_run_main(script, solver=True)
    expected_reports: list[reporter.Issue] = [
    ]
    assert_expected_report(report, expected_reports)


def delete_file_after_creation(tmp_path):
    script = write_script(tmp_path, """
    touch /tmp/somefile.txt
    rm /tmp/somefile.txt
    """)

    report = reset_and_run_main(script, solver=True)
    expected_report = reporter.UnsatisfiedPrecondition(None, "rm /tmp/somefile.txt", None)
    assert_expected_report(report, [expected_report])


def delete_file_after_creation_fixed(tmp_path):
    script = write_script(tmp_path, """
    touch /tmp/somefile.txt
    grep "somepattern" /tmp/somefile.txt
    rm /tmp/somefile.txt
    """)

    report = reset_and_run_main(script, solver=True)
    assert_expected_report(report, [])
