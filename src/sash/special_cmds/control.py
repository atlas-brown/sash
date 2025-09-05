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
from sash.controlflow import Break, Continue, Exit, Return
from sash.defs import Symbstr
from sash.expansionstate import SetOptionStore, SymbExitCode, VarStore
from sash.nodelist import NodeList, get_path_var
from sash.symb_result import ShseerResult

def handle_breakcontinue(
    snode: NodeList, proccmd: AST.CommandNode, cflow_tp: type[Break] | type[Continue]
) -> NodeList:
    cmd_name = cflow_tp.cmd_name()
    if snode.loop_level == 0 and cflow_tp == Break:
        reporter.REPORTER.add_error_message(error_report.BreakOutsideLoop())
        return snode
    if snode.loop_level == 0 and cflow_tp == Continue:
        reporter.REPORTER.add_syntax_error("continue", "Continue outside loop")
        return snode
    if len(proccmd.arguments) > 2:
        reporter.REPORTER.add_syntax_error(
            str(proccmd), f"{cmd_name} takes at most a single argument"
        )
    n = 1
    if len(proccmd.arguments) >= 2:
        arg = symb_utils.string_of_arg(proccmd.arguments[1])
        if arg is None:
            raise ValueError(f"expected {cmd_name} argument to be concrete")
        if not arg.isnumeric() or int(arg) < 0:
            reporter.REPORTER.add_syntax_error(
                str(proccmd), f"Expected {cmd_name} arg to a positive number"
            )
            reporter.REPORTER.set_judgement(ShseerResult.ScriptError)
        n = int(arg)

    if n > snode.loop_level:
        reporter.REPORTER.add_syntax_error(
            str(proccmd),
            f"Only have {snode.loop_level} loops but found {cmd_name} for {n}",
        )
        n = snode.loop_level
    snode.set_cflow(cflow_tp(n))
    snode.add_commandtrack(cmd_name, 0,snode.checked_pos,True)
    return snode


def check_exitreturn_code(
    curn: NodeList, arg: Symbstr, tp: type[Exit] | type[Return]
) -> bool:
    arg_exp = specs.make_argls_sexp(arg, is_path=False)
    if curn.invalid_code_allowed(arg_exp):  # Doesn't enforce constraints
        if tp == Exit:
            reporter.REPORTER.add_error_message(error_report.InvalidExitStatusRange())
            reporter.REPORTER.add_error_message(
                error_report.InvalidExitStatusWithData()
            )
        else:
            reporter.REPORTER.add_error_message(error_report.InvalidReturnValueRange())
            reporter.REPORTER.add_error_message(
                error_report.InvalidReturnValueWithData()
            )
        return False
    elif not curn.check_validcode(arg_exp):  # Enforces constraints
        if tp == Exit:
            reporter.REPORTER.add_error_message(error_report.InvalidExitStatusRange())
        else:
            reporter.REPORTER.add_error_message(
                error_report.InvalidReturnValueWithData()
            )
        return False
    return True


def get_exit_code(inpstr: str) -> int:
    try:
        intc = int(inpstr)
        return intc % 255
    except ValueError:
        return 1


def handle_exitreturn(
    snode: NodeList, proccmd: AST.CommandNode, tp: type[Exit] | type[Return]
) -> NodeList:
    cmd_name = tp.cmd_name()
    if len(proccmd.arguments) > 2:
        if tp == Exit:
            reporter.REPORTER.add_error_message(
                error_report.InvalidExitStatusWithData()
            )
            reporter.REPORTER.add_error_message(error_report.InvalidExitStatusRange())
        else:
            reporter.REPORTER.add_error_message(error_report.InvalidReturnValueRange())
            reporter.REPORTER.add_error_message(
                error_report.InvalidReturnValueWithData()
            )

    command_code = pru.SymbArgChar(symb_utils.create_fresh_varname("cmd_exit_code:symb:271"))
    if len(proccmd.arguments) == 1:
        snode.add_shell_constr("exitreturn", pru.SEq(specs.make_arg_sexp(command_code),specs.make_arg_sexp("0")))
        snode.add_commandtrack(cmd_name, SymbExitCode(command_code),snode.checked_pos,True)
    elif len(proccmd.arguments) >= 2:
        # TODO-RRR handle variables here!
        arg = symb_utils.symb_string_of_arg(proccmd.arguments[1])
        check_exitreturn_code(snode, arg, tp)  # TODO-r handle case with ; at the end
        exit_code_var = symb_utils.create_fresh_var("cmd_exit_code:symb:279")
        snode.add_shell_constr("exitreturn", pru.SEq(specs.make_arg_sexp(command_code),specs.make_argls_sexp(arg)))
        command_code = exit_code_var
        if (conc_arg := symb_utils.symbstr_to_str(arg)) is not None:
            conc_code = get_exit_code(conc_arg)
            snode.add_commandtrack(cmd_name, conc_code, False, True)
        else:
            snode.add_commandtrack(cmd_name, SymbExitCode(exit_code_var), False, True)
    if tp == Exit:
        snode.set_cflow(Exit(command_code,snode.subshell_counter))
    elif tp == Return:
        snode.set_cflow(Return(command_code)) # TODO return should also have an n , function in subshell
    return snode
