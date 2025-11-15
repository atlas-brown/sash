from itertools import combinations
from pprint import pformat

import sash.specs as specs
import sash.symb as symb
from sash.constraints import And, CommandExists, Empty, Implies, IOType, IsDeleted, IsDir, IsFile, IsUnread, Not, Or, StringEq

# Nice little message for copilot:
#   check: (essentially) the precondition that must hold for a command to succeed, but not exactly
#   success_postcond: the postcondition that holds if the command succeeds
#   failure_postcond: the postcondition that holds if the command fails
# The things that we can check are limited to simple file system constraints and variable string equalities


def test_rm_spec__check_disallows_deleting_unreadable_files():
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

    # Every possible check that rm produces must disallow deleting unreadable files
    # When -f is provided, the check takes the form of an implication, because operands don't need to be files (can be dirs or deleted)
    # When -f is not provided, the check is a conjunction that requires operands to be files and not unreadable
    expected_checks_per_inv = [
        [IsFile(f) >> ~IsUnread(f) if "-f" in inv.flags else IsFile(f) & ~IsUnread(f) for f in inv.operands]
        for inv in invocations
    ]

    generated_specs = [specs.rm_spec(inv) for inv in invocations]
    for inv, s, ecs in zip(invocations, generated_specs, expected_checks_per_inv):
        for ec in ecs:
            assert constraint_contains(s.check, ec), f"Expected check to contain:\n{pformat(ec)}\nbut got:\n{pformat(s.check)}\nfor invocation:\n{pformat(inv)}"


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


def create_symstr(val: str) -> symb.SymStr:
    return symb.SymStr((val,))


def create_field(val: str) -> specs.Field:
    words = 0
    if len(val) > 0:
        words = 1

    previously_space = True # to handle leading spaces
    for c in val:
        if c.isspace() and not previously_space:
            words += 1
        previously_space = c.isspace()

    return specs.Field(
        create_symstr(val),
        symb.WordCount(words, words)
    )


def create_cmd_inv(cmd_name: symb.SymStr, flags: set[str], options: dict[str, symb.Field], operands: list[symb.Field]) -> specs.CmdInvocation:
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
