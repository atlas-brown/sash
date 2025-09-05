from typing import Dict, List, Optional, Set
import shasta.ast_node as AST

import sash.error_report as error_report
import sash.reporter as reporter
from sash import (
    casep,
    specs,
    syfi,
    symb_datatypes,
    symb_utils,
)
# import sash.deprecated.rules as rules
from sash import defs as pru
from sash.defs import Symbstr
from sash.specs import CommandInvMapping, from_pru
from sash.symb_result import ShseerResult


def handle_test_one_arg(
    arg: Symbstr, curn: NodeList, cmd_name: str, negated: bool = False
):
    arg_sexp = specs.make_argls_sexp(arg, is_path=False)
    pru_constrs = specs.Neg(pru.str_empty(arg_sexp))
    bspec = specs.CommandSpec(
        specs.ShseerCmdInv(cmd_name, set(), [arg]), from_pru(pru_constrs), negated
    )
    curn.frombuiltin(bspec, is_test=True)
    return curn


def handle_test_two_args(
    flag: Optional[str],
    arg: Symbstr,
    curn: NodeList,
    cmd_name: str,
    process_unknown_command: Callable[[NodeList, list[Symbstr], bool], NodeList],
    negated: bool = False,
) -> NodeList:
    pru_constrs = None
    spec = None
    match flag:
        case "!":
            return handle_test_one_arg(arg, curn, cmd_name, negated=not negated)
        case "-d":
            spec, pru_constrs = specs.is_ctype(arg, [syfi.ConstraintType.Dir])
        case "-e":
            spec, pru_constrs = specs.is_ctype(
                arg, [syfi.ConstraintType.File, syfi.ConstraintType.Dir]
            )
        case "-f":
            spec, pru_constrs = specs.is_ctype(arg, [syfi.ConstraintType.File])
        case "-n":
            arg_sexp = specs.make_argls_sexp(arg, is_path=False)
            pru_constrs = specs.Neg(pru.str_empty(arg_sexp))
        case "-z":
            arg_sexp = specs.make_argls_sexp(arg, is_path=False)
            pru_constrs = pru.str_empty(arg_sexp)
        case _:
            symb_cmd_args: Symbstr = [cmd_name] + [" "] + arg
            return process_unknown_command(curn, [symb_cmd_args], has_side_effects=False)
    fspec = [spec] if spec else []
    bspec = specs.CommandSpec(
        specs.ShseerCmdInv(cmd_name, set(flag), [arg]),
        fspec + from_pru(pru_constrs),
        negated,
    )
    curn.frombuiltin(bspec, is_test=True)
    return curn


def handle_test_three_args(
    arg1: Symbstr,
    flag: Optional[str],
    arg2: Symbstr,
    curn: NodeList,
    cmd_name: str,
    process_unknown_command: Callable[[NodeList, list[Symbstr], bool], NodeList],
    negated: bool = False,
) -> NodeList:
    arg1_sexp = specs.make_argls_sexp(arg1, is_path=False)
    arg2_sexp = specs.make_argls_sexp(arg2, is_path=False)
    if flag in ["=", "!=", "<", ">"]:
        check_string_op_int(curn, flag, arg1, arg2)
    if flag in ["-eq", "-ne", "-gt", "-lt", "-ge", "-le"] and not check_int_ops(
        curn, flag, arg1, arg2
    ):
        curn.add_commandtrack("test", 1,curn.checked_pos)
        if curn.checked_pos == 0:
            reporter.REPORTER.set_judgement(ShseerResult.ScriptError)
        return curn
    pru_constr: Optional[pru.PRU] = None
    match flag:
        case "=":
            pru_constr = specs.SEq(arg1_sexp, arg2_sexp)
        case "!=":
            pru_constr = specs.Neg(specs.SEq(arg1_sexp, arg2_sexp))
        case "<":
            pru_constr = pru.StreLe(arg1_sexp, arg2_sexp)
        case ">":
            pru_constr = pru.StrGe(arg1_sexp, arg2_sexp)
        case "-eq":
            pru_constr = pru.IntEq(arg1_sexp, arg2_sexp)
        case "-ne":
            pru_constr = pru.Neg(pru.IntEq(arg1_sexp, arg2_sexp))
        case "-gt":
            pru_constr = pru.IntGe(arg1_sexp, arg2_sexp)
        case "-lt":
            pru_constr = pru.IntLe(arg1_sexp, arg2_sexp)
        case "-ge":
            pru_constr = pru.OrExp(
                [pru.IntGe(arg1_sexp, arg2_sexp), pru.IntEq(arg1_sexp, arg2_sexp)]
            )
        case "-le":
            pru_constr = pru.OrExp(
                [pru.IntLe(arg1_sexp, arg2_sexp), pru.IntEq(arg1_sexp, arg2_sexp)]
            )

        case _:
            if symb_utils.symbstr_to_str(arg1) == "!":
                return handle_test_two_args(
                    flag, arg2, curn, cmd_name, process_unknown_command, negated=(not negated)
                )
            else:
                # Assume this is a bad flag
                reporter.REPORTER.add_error_message(
                    error_report.BadTestFlag(flag if flag else "N/A")
                )
                # set exit code to 1
                curn.add_commandtrack("test", 1,curn.checked_pos)
                return curn
    bspec = specs.CommandSpec(
        specs.ShseerCmdInv(cmd_name, set(flag), [arg1, arg2]),
        from_pru(pru_constr),
        negated,
    )
    curn.frombuiltin(bspec, is_test=True)
    return curn


def handle_test_four_args(
    arg1: Symbstr,
    arg2: Symbstr,
    arg3: Symbstr,
    arg4: Symbstr,
    curn: NodeList,
    cmd_name: str,
    process_unknown_command: Callable[[NodeList, list[Symbstr], bool], NodeList],
    negated: bool = False,
) -> NodeList:
    if symb_utils.symbstr_to_str(arg1) == "!":
        return handle_test_three_args(
            arg2,
            symb_utils.symbstr_to_str(arg3),
            arg4,
            curn,
            cmd_name,
            negated=not negated,
        )
    symb_cmd_args: list[Symbstr] = [
        [cmd_name] + [" "] + arg1 + [" "] + arg2 + [" "] + arg3 + [" "] + arg4
    ]
    return process_unknown_command(curn, symb_cmd_args, has_side_effects=False)


def handle_test(
    curn: NodeList, expanded_node: AST.CommandNode, cmd_name: str,
    process_unknown_command: Callable[[NodeList, list[Symbstr], bool], NodeList]
) -> NodeList:
    if cmd_name == "[" and symb_utils.string_of_arg(expanded_node.arguments[-1]) != "]":
        reporter.REPORTER.add_syntax_error(
            str(expanded_node), "Missing closing bracket"
        )
        reporter.REPORTER.set_judgement(ShseerResult.ScriptError)
    if (
        cmd_name == "[["
        and symb_utils.string_of_arg(expanded_node.arguments[-1]) != "]]"
    ):
        reporter.REPORTER.add_syntax_error(
            expanded_node.pretty(), "Missing closing bracket"
        )
        reporter.REPORTER.set_judgement(ShseerResult.ScriptError)

    if cmd_name in ["[", "[["]:
        args = expanded_node.arguments[1:-1]  # Ignore the name and closing bracket
    else:
        args = expanded_node.arguments[1:]  # Ignore the name

    match len(args):
        case 0:
            # Exit false (1)
            curn.add_commandtrack(cmd_name, 1, False, True)
            return curn
        case 1:
            arg = symb_utils.symb_string_of_arg(args[0])
            return handle_test_one_arg(arg, curn, cmd_name)
        case 2:
            flag_arg = symb_utils.string_of_arg(args[0])
            if flag_arg:
                flag = flag_arg
                arg = symb_utils.symb_string_of_arg(args[1])
                return handle_test_two_args(flag, arg, curn, cmd_name, process_unknown_command)
        case 3:
            flag_arg = symb_utils.symbstr_to_str(symb_utils.symb_string_of_arg(args[1]))
            if flag_arg:
                arg1 = symb_utils.symb_string_of_arg(args[0])
                flag = flag_arg
                arg2 = symb_utils.symb_string_of_arg(args[2])
                return handle_test_three_args(arg1, flag, arg2, curn, cmd_name, process_unknown_command)
        case 4:
            arg1 = symb_utils.symb_string_of_arg(args[0])
            arg2 = symb_utils.symb_string_of_arg(args[1])
            arg3 = symb_utils.symb_string_of_arg(args[2])
            arg4 = symb_utils.symb_string_of_arg(args[3])
            return handle_test_four_args(arg1, arg2, arg3, arg4, curn, cmd_name)
    symb_cmd_args = cmd_symb_str(expanded_node)
    return process_unknown_command(curn, symb_cmd_args)



def check_string_op_int(curn: NodeList, op: str, arg1: Symbstr, arg2: Symbstr) -> bool:
    if (arg1str := symb_utils.symbstr_to_str(arg1)) and arg1str and arg1str.isnumeric():
        # TODO-R actually report the value arg1 could have
        reporter.REPORTER.add_error_message(error_report.StringOpOnInt(op, str(arg1)))
        return False
    if (arg2str := symb_utils.symbstr_to_str(arg2)) and arg2str and arg2str.isnumeric():
        # TODO-R actually report the value arg2 could have
        reporter.REPORTER.add_error_message(error_report.StringOpOnInt(op, str(arg2)))
        return False
    return True


def check_int_ops(curn: NodeList, op: str, arg1: Symbstr, arg2: Symbstr) -> bool:
    arg1exp = specs.make_argls_sexp(arg1, is_path=False)
    arg2exp = specs.make_argls_sexp(arg2, is_path=False)
    if not curn.query_isint(arg1exp):
        reporter.REPORTER.add_error_message(
            error_report.TestOpExpectedNumber(op, symb_utils.repr_symbstr(arg1))
        )
        return False
    if not curn.query_isint(arg2exp):
        reporter.REPORTER.add_error_message(
            error_report.TestOpExpectedNumber(op, symb_utils.repr_symbstr(arg2))
        )
        return False
    return True

