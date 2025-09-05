from dataclasses import dataclass
import getopt
import json
import logging
import os
import shlex
import sys
import traceback
from argparse import ArgumentParser
from copy import deepcopy
from typing import Dict, List, Optional, Set
from pash_annotations.datatypes.BasicDatatypes import Flag, FlagOption, Operand, Option
from pash_annotations.datatypes.CommandInvocationInitial import CommandInvocationInitial
from pash_annotations.parser.parser import (
    are_all_individually_flags,
    get_dict_flag_to_primary_repr,
    get_dict_option_to_primary_repr,
    get_set_of_all_flags,
    get_set_of_all_options,
)
from pash_annotations.parser.util_parser import get_json_data
import shasta.ast_node as AST

import sash
import sash.error_report as error_report
import sash.exceptions
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
from sash import nexpand
from sash.config import Config
from sash.controlflow import Break, Continue, Exit, Return
from sash.defs import Symbstr
from sash.exceptions import (
    EarlyError,
    ImpureExpansion,
    InvalidVariable,
    ParseException,
    StuckExpansion,
    TerminateProgram,
    Unimplemented,
)
from sash.expansionstate import SetOptionStore, SymbExitCode, VarStore
from sash.nodelist import NodeList, get_path_var
from sash.shell_parser import parse_shell_to_asts
from sash.solver_datatypes import QueryResult
from sash.specs import CommandInvMapping, from_pru
from sash.symb_result import ShseerResult

from sash.special_cmds.control import *
from sash.special_cmds.test import *
from sash.special_cmds.vars import *


def annot_parser_wrapper(str_ls_args: list[str]) -> CommandInvMapping:
    # split all terms (command, flags, options, arguments, operands)
    parsed_elements_list: list[str] = str_ls_args

    cmd_name: str = parsed_elements_list[0]
    json_data = get_json_data(cmd_name)
    # TODO: if there is an element "\n", we lose the quotation marks currently

    set_of_all_flags: Set[str] = get_set_of_all_flags(json_data)
    dict_flag_to_primary_repr: Dict[str, str] = get_dict_flag_to_primary_repr(json_data)
    set_of_all_options: Set[str] = get_set_of_all_options(json_data)
    dict_option_to_primary_repr: Dict[str, str] = get_dict_option_to_primary_repr(
        json_data
    )
    # dict_option_to_class_for_arg: Dict[str, WhichClassForArg] = get_dict_option_to_class_for_arg(json_data)

    # parse list of command invocation terms
    flag_option_list: List[FlagOption] = []
    i = 1
    while i < len(parsed_elements_list):
        potential_flag_or_option = parsed_elements_list[i]
        if potential_flag_or_option in set_of_all_flags:
            flag_name_as_string: str = dict_flag_to_primary_repr.get(
                potential_flag_or_option, potential_flag_or_option
            )
            flag: Flag = Flag(flag_name_as_string)
            flag_option_list.append(flag)
        elif (potential_flag_or_option in set_of_all_options) and (
            (i + 1) < len(parsed_elements_list)
        ):
            option_name_as_string: str = dict_option_to_primary_repr.get(
                potential_flag_or_option, potential_flag_or_option
            )
            option_arg_as_string: str = parsed_elements_list[i + 1]
            option = Option(option_name_as_string, option_arg_as_string)
            flag_option_list.append(option)
            i += 1  # since we consumed another term for the argument
        elif are_all_individually_flags(potential_flag_or_option, set_of_all_flags):
            for split_el in list(potential_flag_or_option[1:]):
                flag: Flag = Flag(f"-{split_el}")
                flag_option_list.append(flag)
        else:
            break  # next one is Operand, and we keep these in separate list
        i += 1

    # we would probably want to skip '--' but then the unparsed command could have a different meaning so we'd need to keep it
    # for now, omitted
    # if parsed_elements_list[i] == '--':
    #     i += 1

    # operand_list = [Operand(operand_name) for operand_name in parsed_elements_list[i:]]
    operand_list = []
    idx_list = []
    for idx in range(i, len(parsed_elements_list)):
        operand_list.append(Operand(parsed_elements_list[idx]))
        idx_list.append(idx)

    return CommandInvMapping(
        CommandInvocationInitial(cmd_name, flag_option_list, operand_list), idx_list
    )


def get_command_invocation(cnd: AST.CommandNode) -> Optional[CommandInvMapping]:
    try:
        str_ls_args = [symb_utils.string_of_arg(a) for a in cnd.arguments]
        return annot_parser_wrapper(str_ls_args)
    except Exception:
        return None

def handle_truefalse(curn: NodeList, cmd_name: str) -> NodeList:
    # Don't add the specs - these commands are always true or false
    match cmd_name:
        case "true":
            curn.add_commandtrack("true", 0,curn.checked_pos)
        case "false":
            curn.add_commandtrack("false", 1,curn.checked_pos)
        case _:
            raise ValueError()
    return curn


def empty_command(cmd: AST.CommandNode):
    return len(cmd.arguments) == 0 and len(cmd.assignments) == 0


def get_command_name(node: AST.CommandNode, curn: NodeList) -> Optional[str]:
    if len(node.arguments) == 0:
        return None
    cmd_name_symbstr = symb_utils.symb_string_of_arg((node.arguments[0]))
    cmd_name = symb_utils.symbstr_to_str(cmd_name_symbstr)
    if cmd_name is None:
        cmd_name_exp = specs.make_argls_sexp(cmd_name_symbstr)
        # TODO-RRR we should actually check for argument[1] ...
        if curn.query_empty_or_space(cmd_name_exp) != QueryResult.Never:
            reporter.REPORTER.add_error_message(error_report.EmptyStringCommand())
        return None
    return cmd_name


# REFERENCE POINT
# TODO-R change so that we don't ignore args/flags when calling this function with proccmd
def process_unknown_command(
    snode: NodeList, proccmd: list[Symbstr], has_side_effects: bool = True
) -> NodeList:
    # logging.error("")
    if Config.get("ASSUME_SIDE_EFFECTS"):
        logging.debug(
            f"Got unknown command {proccmd} with side effects. Invalidating symbolic state"
        )
        snode.clear_node()
    else:
        logging.warning(f"Got unknown command {proccmd} without side effects. Ignoring")
  
    reporter.REPORTER.add_unimplemented_error(f"{proccmd}")
    cmd_name = symb_utils.symbstr_to_str(proccmd[0])
    
    if snode.resolve_external_cmd_if_concrete(proccmd[0]):
       reporter.REPORTER.add_error_message(error_report.CommandNotFoundLocally(str(cmd_name)))
        
    
    cmd_name = cmd_name if cmd_name is not None else "unknown"
    snode.add_commandtrack(f"{cmd_name}", SymbExitCode(symb_utils.create_fresh_var(f"exit_code_{cmd_name}:symb:782")),snode.checked_pos,True)
    return snode


def process_command(snode: NodeList, cmd_node: AST.CommandNode) -> NodeList:
    # print(f"Processing command {cmd_node.pretty()}")
    symb_cmd_args = [symb_utils.string_of_arg(a) for a in cmd_node.arguments]
    logging.debug(f"Processing command invocation {symb_cmd_args}")
    proccmd = get_command_invocation(cmd_node)
    if proccmd is None:
        return process_unknown_command(snode, symb_cmd_args)
    # rules.hook_command_inv(proccmd.cmdinv)
    cspec = specs.get_annotations(proccmd, symb_cmd_args)
    if cspec is None:
        return process_unknown_command(snode, symb_cmd_args)
    if (cmdspec := cspec.get_spec()) is None:
        return process_unknown_command(snode, symb_cmd_args)
    snode.fromspec(cmdspec)
    return snode


def process_trap_command(curn: NodeList, proccmd: AST.CommandNode) -> NodeList:
    """
    We don't actually model traps. This is just so that any cleanup functions also get checked
    """
    cmd_name = get_command_name(proccmd, curn)
    assert cmd_name == "trap"
    args = proccmd.arguments[1:]
    if len(args) == 0:
        return curn

    for arg in args:
        concstr = symb_utils.argchar_conc(arg)
        if concstr in ["-l", "-p"]:
            return curn
    action_arg = args[0]
    conc_action_arg = symb_utils.argchar_conc(action_arg)
    if (
        conc_action_arg is not None
        and not conc_action_arg.startswith("SIG")
        and conc_action_arg not in ["INT", "TERM", "EXIT", "--"]
    ):
        cmd_node = AST.CommandNode(-1, [], [action_arg], [])
        return guarded_interp_node(curn, cmd_node)
    return curn


def handle_read(curn: NodeList, proccmd: AST.CommandNode) -> NodeList:
    cmd_name = get_command_name(proccmd, curn)
    assert cmd_name == "read"
    args = proccmd.arguments[1:]
    args_str = [
        symb_utils.string_of_arg(symb_utils.symb_string_of_arg(arg)) for arg in args
    ]
    # recognize bash options for now but should throw warning for non POSIX options (everything except -r,-d)
    # Doing this rn because just want to mark the variables as mutated
    optlist, args = getopt.getopt(args_str, "ersa:d:i:n:p:t:u:N:")
    for arg in args:
        symb_datatypes.NodeSymMaps.adddecl(arg)
        curn.add_variable(arg, None, [symb_utils.create_fresh_var("read_arg:symb:868")])
    return curn


def handle_builtin(
    curn: NodeList, cmd_name: Optional[str], proccmd: AST.CommandNode
) -> Optional[NodeList]:
    match cmd_name:
        case "trap":
            return process_trap_command(curn, proccmd)
        case "break":
            return handle_breakcontinue(curn, proccmd, Break)
        case "continue":
            return handle_breakcontinue(curn, proccmd, Continue)
        case "exit":
            return handle_exitreturn(curn, proccmd, Exit)
        case "return":
            return handle_exitreturn(curn, proccmd, Return)
        case "test" | "[" | "[[":
            if cmd_name == "[[":
                reporter.REPORTER.add_syntax_error(
                    "[[",
                    "[[ is a bash only construct undefined in POSIX. Proceeding as if [ ] was used, which could lead to different behavior",
                )
            return handle_test(curn, proccmd, cmd_name)
        case "true" | "false":
            return handle_truefalse(curn, cmd_name)
        case "shift":
            symb_args = [
                symb_utils.symb_string_of_arg(arg) for arg in proccmd.arguments[1:]
            ]
            if not curn.shift_args(symb_args):
                curn.add_commandtrack(f"shift {symb_args}", 1,curn.checked_pos)
                # TODO continue here
                reporter.REPORTER.set_judgement(ShseerResult.ScriptError)
            else:
                curn.add_commandtrack(f"shift {symb_args}", 0, curn.checked_pos,True)

            return curn
        case "getopts":
            return curn.handle_getopts(proccmd)
        case "read":
            return handle_read(curn, proccmd)
        case "command":
            ncmd_node = deepcopy(proccmd)
            ncmd_node.arguments = ncmd_node.arguments[1:]
            ncmd_node.assignments = []
            ncmd_node.redir_list = []
            cnd = symb_datatypes.ConditionalVisit() if curn.checked_pos > 0 else None
            curn.ast_map[id(ncmd_node)] = symb_datatypes.ASTNodeVisit(
                ncmd_node.pretty(), conditional=cnd, visited=True, constant=False
            )
            return handle_commandnode(ncmd_node, curn, suppress_func_lookup=True)
        case "set":
            return curn.handle_set(proccmd)
        case "unset":
            return handle_unset(curn, proccmd)
        case "cd":
            return curn.handle_cd(proccmd)
        case "exec":
            # Shell is done
            curn.add_commandtrack("exec", 0,curn.checked_pos,True)
            # If no args then just do nothing
            if len(proccmd.arguments) > 1:
                curn.set_exitflow(Exit( symb_utils.create_fresh_var("exec_exit:symb:938"),curn.subshell_counter))
                # We don't know what the exit code could be
            return curn
        case "eval":
            logging.debug("Found eval construct. Removing all state")
            # Just return a new node. no state should persist
            reporter.REPORTER.add_syntax_error(
                "eval", "Eval is not supported. Halting execution"
            )
            reporter.REPORTER.set_judgement(ShseerResult.UNKNOWN)
            # return [NodeList()]
        case "alias" | "unalias":
            logging.error("Cannot handle alias/unalias")
            reporter.REPORTER.add_unimplemented_error(cmd_name)
            reporter.REPORTER.set_judgement(ShseerResult.EXPANSION_INCOMPLETE)
            raise TerminateProgram()
        case "bg" | "wait":
            logging.error("Cannot handle bg/wait")
            reporter.REPORTER.add_unimplemented_error(cmd_name)
            raise TerminateProgram()
        # case "times" | ":" | "ulimit" | "umask" | "printf" | "type" | "jobs" | "jobid" | "pwd":
        #     # TODO-R these affect shell state but we do not model them
        #     return process_dummy_builtin(curn, proccmd)
        case _:
            logging.warning(f"Ignoring unsupported builtin {cmd_name}")
            return None


def interp_function(curn: NodeList, c: AST.CommandNode) -> Optional[NodeList]:
    function_name = get_command_name(c, curn)
    if function_name is None:
        return None 
    isfunc, funcdef = curn.in_function_defs(function_name)
    if not isfunc:
        logging.debug(f"Lookup for function called {function_name} failed")
        return None
    if funcdef is None:
        reporter.REPORTER.add_error_message(
            error_report.UndefinedFunction(function_name)
        )
        return None
    func_args = [symb_utils.symb_string_of_arg(arg) for arg in c.arguments[1:]]

    curn.enter_function(func_args)
    res_ls = guarded_interp_node(curn, funcdef.body)
    curn.exit_function()
    return res_ls




def handle_echo_stdout(node: AST.CommandNode, curn: NodeList) -> None:
    cmd_name = get_command_name(node, curn)
    if cmd_name != "echo":
        return
    out: Symbstr = []
    args = node.arguments[1:]
    for idx, arg in enumerate(args):
        arg_str = symb_utils.symb_string_of_arg(arg)
        nls = []
        for c in arg_str:
            if isinstance(c, str):
                nls.append(c)
            elif isinstance(c, pru.SymbArgChar):
                vl = curn.try_symbarg_concrete(c)
                if vl is None:
                    nls.append(c)
                else:
                    nls.append(vl)
        out.extend(nls)
        if idx != len(args) - 1:
            out.append(" ")
    curn.add_echo_out(out)


def remove_whitespace(node: AST.CommandNode) -> None:
    # You shouldn't remove whitespace that is quoted
    nls = []
    for arg in node.arguments:
        narg_ls = []
        for carg in arg:
            match carg:
                case AST.CArgChar():
                    if chr(carg.char).isspace() or chr(carg.char) == "":
                        continue
                    narg_ls.append(carg)
                case '':
                    continue
                case _:
                    narg_ls.append(carg)
        if narg_ls:
            nls.append(narg_ls)
    node.arguments = nls


def check_node_constant(expanded_node: AST.CommandNode) -> bool:
    symb_args = [symb_utils.symb_string_of_arg(arg) for arg in expanded_node.arguments]
    pos_conc_args = [symb_utils.symbstr_to_str(arg) for arg in symb_args]
    is_conc = all([arg is not None for arg in pos_conc_args])
    return is_conc


# TODO-RRR: Order in this function is important and should be rechecked
def handle_commandnode(
    un_exp_node: AST.CommandNode, nl: NodeList, suppress_func_lookup: bool = False
) -> NodeList:
    # N: modifying a node without copying can be a cause for subtle bugs!
    # we expand on a copy because this may be called again (such as in a loop)
    logging.debug(f"Handling command node {un_exp_node.pretty()} with{'OUT' if suppress_func_lookup else ''} function lookup")
    if empty_command(un_exp_node):
        return nl 
    expansion_possibilities_across_paths = nexpand.expand_simple(deepcopy(un_exp_node), nl)
    logging.debug(f"there are {sum(len(l) for l in expansion_possibilities_across_paths)} different expansion possibilities")
    merged_nl_res = None
    # TODO: if `expansion_possibilities_across_paths` has duplicates across path conditions (nodes), then we could only split
    # here into the actually different cases. Conjecture is that the vast majority of the time they will all be identical.
    for one_node_nodelist, expansion_possibilities in zip(nl.split_all(), expansion_possibilities_across_paths):
        this_pathcond_res = None
        for expanded_node, expansion_constraint in expansion_possibilities:
            curn = one_node_nodelist.copy_nodes()
            if expansion_constraint:
                pathvar = get_path_var("expansion_word_split")
                for node in curn.nodes:
                    # node.path_cond.append(expansion_constraint)
                    node.path_cond.append(pathvar)
                curn.add_shell_constr("expansion word splitting",
                                      pru.Implies(pathvar, expansion_constraint))

            logging.debug(f"handling expansion possibility: {expanded_node.arguments}")
            remove_whitespace(expanded_node)
            if check_node_constant(expanded_node):
                curn.mark_node_constant(un_exp_node)
            cmd_name = get_command_name(expanded_node, curn)
            # rules.hook_command_expanded(expanded_node)
            if empty_command(expanded_node):
                return curn 
            if handle_local_vars(expanded_node, curn):
                return curn 

            handle_echo_stdout(expanded_node, curn)

            curn = handle_redir_ls(expanded_node.redir_list, curn)

            if (res := handle_builtin(curn, cmd_name, expanded_node)) is not None:
                return res
            if (not suppress_func_lookup) and (
                (fun_res := interp_function(curn, expanded_node)) is not None
            ):
                return fun_res
            res = process_command(curn, expanded_node)
            # if this_pathcond_res is None:
            #     this_pathcond_res = res
            this_pathcond_res = res
            
        if merged_nl_res is None:
            merged_nl_res = this_pathcond_res
        else:
            merged_nl_res.merge(this_pathcond_res, MERGE_NODES)

    return merged_nl_res



def guarded_interp_node(symb_n: NodeList, node: AST.AstNode) -> NodeList:
    try:
        return interp_node(symb_n, node)
    except Exception:
        logging.error(f"Interpreting raised exception: {traceback.format_exc()}. Proceeding by ignoring.")
        return symb_n


def interp_node(symb_n: NodeList, node: AST.AstNode) -> NodeList:
    iscnd = None
    # for curnn in symb_n.nodes:
    
    iscnd = symb_datatypes.ConditionalVisit() if symb_n.checked_pos > 0 else None
    symb_n.ast_map[id(node)] = symb_datatypes.ASTNodeVisit(
            node.pretty(), conditional=iscnd, visited=True, constant=False
        )
    match node:
        case AST.CommandNode():
            return handle_commandnode(node, symb_n)
        # todo bring other cases from shseer codebase
        case _:
            raise sash.exceptions.OutOfScopeError(
                    f"node type {type(node)} not handled"
                )


def starting_state() -> State:
    env = {}
    env["IFS"] = ShellVar(" \t\n")
    for defaultvar in ["HOME", "PWD", "OLDPWD", "PATH"]:
        env[defaultvar] = ShellVar(symb_utils.create_fresh_var(f"default_{defaultvar}"))

@dataclass(frozen=True)
class AST_parse:
    ast_node: AstNode
    rawtext: str
    line_before: int
    line_after: int  # relevant for mysterious shell reasons

def symb_engine(nodes: list[AST_parse], opt_store: SetOptionStore) -> set[Trace]:
    logging.debug(f"Running symb engine with {len(nodes)} raw nodes")
    info = ScriptInfo(opt_store)
    traces = {Trace([State([], [], )])}
    for node in nodes:
        logging.debug(f"Interpreting next node {node.pretty()}")
        traces = guarded_interp_node(traces, node, info)

    return traces


def parse_script(filename) -> list[AST_parse]:
    shasta_nodes = parse_shell_to_asts(filename)
    logging.debug(f"Parsed script with {len(shasta_nodes)} nodes")
    nodes = [AST_parse(*x) for x in shasta_nodes]
    return nodes


def parse_shebang_args(inputfile: str) -> SetOptionStore:
    optstore = SetOptionStore()
    def parse_option(opt: str) -> tuple[Optional[str], Optional[bool]]:
        if opt.startswith("+"):
            return opt[1:], False
        elif opt.startswith("-"):
            return opt[1:], True
        else:
            return None, None

    firstline = open(inputfile).readline()
    if not (firstline.startswith("#!/bin/sh") or firstline.startswith("#!/bin/dash")):
        return optstore

    args = shlex.split(firstline)
    if len(args) < 2:
        return optstore

    farg = args[1]

    flags, pres = parse_option(farg)
    if flags is not None:
        assert pres is not None
        opts = [i for i in flags]
        for opt in opts:
            optstore.handle_option(opt, pres)
    return optstore


def symbexec_file(input_file: str) -> NodeList:
    nodes = parse_script(input_file)
    opt_store = parse_shebang_args(input_file)
    return symb_engine(nodes, opt_store)


def main(file: str) -> dict:
    logging.info(f"Processing file {file}")
    reporter.REPORTER.initialize(file)
    try:
        symbexec_file(file)
    except ParseException:
        logging.error(f"Failed to parse file {file}")
        logging.debug(f"Failed to parse file {file} due to {traceback.format_exc()}")
        reporter.REPORTER.set_judgement_safe(ShseerResult.ParseError)
    except sash.exceptions.OutOfScopeError as ex:
        logging.error(f"Failed due to {str(ex)}")
        logging.debug(f"Failed due to {traceback.format_exc()}")
        reporter.REPORTER.set_judgement_safe(ShseerResult.UNKNOWN)
    except Exception:
        logging.info(f"Failed due to {traceback.format_exc()}.Returning unknown")
        reporter.REPORTER.set_error_safe()

    report_dict = reporter.REPORTER.get_report()
    logging.info("Time taken: " + str(report_dict["time"]))
    return report_dict


def argmain():
    arg_parser = ArgumentParser(
        prog="Sash",
        description="Static analyis for posix shell scripts",
    )
    arg_parser.add_argument(
        "filename",
        help="Input shell script file",
    )
    arg_parser.add_argument(
        "--debug",
        "-d",
        action="store_true",
        help="optional flag to enable debugging messages",
    )
    arg_parser.add_argument(
        "-z3",
        action="store_true",
        help="optional flag to enable z3 solver",
    )
    arg_dict = vars(arg_parser.parse_args(sys.argv[1:]))
    if arg_dict["debug"]:
        logging.basicConfig(
            format="[%(filename)s:%(lineno)d] %(message)s", level=logging.DEBUG
        )
        Config.set("DEBUG", True)
    else:
        logging.basicConfig(level=logging.CRITICAL)
    if arg_dict["z3"]:
        Config.set("CREATE_Z3", True)

    filename = arg_dict["filename"]
    logging.debug(f"Full filename is {os.path.realpath(filename)}")
    report_dict = main(filename)
    print(json.dumps(report_dict))


if __name__ == "__main__":
    argmain()
