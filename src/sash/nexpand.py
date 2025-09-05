import glob
import itertools
from dataclasses import dataclass, field
import logging
import os
from typing import Optional, Sequence
from copy import deepcopy

from shasta.ast_node import (
    AArgChar,
    ArgChar,
    BArgChar,
    CArgChar,
    CommandNode,
    EArgChar,
    QArgChar,
    SubshellNode,
    TArgChar,
    VArgChar,
)

import sash.defs as pru
import sash.reporter as reporter
# import sash.deprecated.rules as rules
import sash.specs as specs
import sash.symb as symb
import sash.symb_node as symb_node
import sash.symb_result as symb_result
from sash import arith_expansion, error_report, nodelist, symb_datatypes, symb_utils
from sash.defs import SymbArgChar, Symbstr
from sash.exceptions import StuckExpansion, Unimplemented
from sash.expansionstate import VarSet, VarStore, VarUnknown, VarUnset
from sash.symb_utils import create_fresh_varname

################################################################################
# EARLY EXPANSION
################################################################################

# General approach:
#
# - expand_* functions try to expand the AST
#   + words return a string when it works, raises when it doesn't
#     TODO MMG 2020-12-14 really should return (intermediate?) fields, not a single string
#   + commands just set the structural bits appropriately


# PUBLIC FUNCTIONS
@dataclass()
class ExpMetadata:
    vars_used: list[str] = field(default_factory=list)
    pru_lits: list[pru.PRU] = field(default_factory=list)

    def merge(self, other):
        self.vars_used.extend(other.vars_used)
        self.pru_lits.extend(other.pru_lits)

@dataclass
class SymbArgsVariant:
    args: list[Symbstr]
    constraint: Optional[pru.PRU]


def symbstr_list_same(ls: list[Symbstr]) -> bool:
    if len(ls) == 0:
        return True
    first = ls[0]
    for i in range(1, len(ls)):
        if ls[i] != first:
            return False
    return True


def tokenize(ls: Symbstr) -> Symbstr:
    split_tok = "="
    if len(ls) == 0:
        return []
    res_ls = []
    for arg in ls:
        if isinstance(arg, SymbArgChar):
            res_ls.append(arg)
        elif isinstance(arg, str):
            splt_arg = arg.split(split_tok)
            for idx, i in enumerate(splt_arg):
                res_ls.append(i)
                if idx != len(splt_arg) - 1:
                    res_ls.append(split_tok)
    return res_ls


def collapse_symbstrs(curn: nodelist.NodeList, ls: list[Symbstr]) -> Symbstr:
    if len(ls) == 0:
        return []
    assert len(ls) == len(curn.nodes)
    ls = [tokenize(i) for i in ls]
    mxln = max([len(i) for i in ls])
    # Pad the shorter lists
    for idx in range(len(ls)):
        while len(ls[idx]) < mxln:
            ls[idx].append("")
    ln = {len(i) for i in ls}
    assert len(ln) == 1
    mxln = ln.pop()
    stopidx = -1
    finals = []
    for idx in range(mxln):
        atposi = [ls[j][idx] for j in range(len(ls))]
        atposi_st = set(atposi)
        if len(atposi_st) == 1:
            finals.append(atposi_st.pop())
        else:
            stopidx = idx
            break
    if stopidx != -1:
        nvar = SymbArgChar(create_fresh_varname("collapsevr"))
        nvar_argexp = specs.make_arg_sexp(nvar)
        constr = pru.OrExp(
            [
                pru.Implies(
                    pru.AndExp(curn.nodes[j].path_cond),
                    pru.SEq(nvar_argexp, specs.make_argls_sexp(ls[j][stopidx:])),
                )
                for j in range(len(ls))
            ]
        )
        curn.add_shell_constr(f"collapse {ls}", constr)
        finals.append(nvar)
    return finals


def collapse_args(curn: nodelist.NodeList, ls: list[list[Symbstr]]) -> list[Symbstr]:
    lengths = [len(i) for i in ls]
    assert len(set(lengths)) == 1  # all lists have the same length
    mx_field_len = lengths[0]
    final_field_list: list[Symbstr] = []
    for field_idx in range(mx_field_len):
        all_options = [i[field_idx] for i in ls]
        collapsed = collapse_symbstrs(curn, all_options)
        final_field_list.append(collapsed)
    return final_field_list


def do_barg(curn: nodelist.NodeList, arg: BArgChar) -> list[ArgChar]:
    """Handle command substitutions in arg by symbolically interpreting the subshell, and making a fresh symbolic var for the output."""
    tmpls = []
    subshell_node = SubshellNode(-1, arg.node, [])
    symb.interp_node(curn, subshell_node)
    # assert curn.cmdsubst_val is not None
    resval = symb_utils.create_fresh_var("cmd_subst_output:nexpand:148")
    curn.cmdsubst_val = None
    assert curn.is_cmdsubst >= 0
    tmpls.append(resval)
    return tmpls


def process_barg_char(
    curn: nodelist.NodeList, args: list[list[ArgChar]]
) -> list[list[ArgChar]]:
    res_ls: list[list[ArgChar]] = []
    for word in args:
        tmpls: list[ArgChar] = []
        for arg in word:
            if isinstance(arg, BArgChar):
                tmpls.extend(do_barg(curn, arg))
            elif isinstance(arg, QArgChar):
                # Process quoted args recursively but preserve the quote wrapper
                inner_processed = process_barg_char(curn, [arg.arg])[0]
                tmpls.append(QArgChar(inner_processed))
            else:
                tmpls.append(arg)
            
        res_ls.append(tmpls)
    return res_ls


def expand_args(
    curn: nodelist.NodeList, args: list[list[ArgChar]], quoted=False
) -> list[list[SymbArgsVariant]]:
    args_cmd_subbed = process_barg_char(curn, args)
    logging.debug(f"after processing command substitutions: {args_cmd_subbed}")

    res_ls: list[list[SymbArgsVariant]] = []
    for node in curn.nodes:
        orig_data = ExpMetadata()
        node_res: list[list[ArgChar]] = []
        for arg in args_cmd_subbed:
            exp_arg, expmeta = expand_arg(node, arg, quoted=quoted)
            logging.debug(f"expd {arg} -> {exp_arg}")
            node_res.append(exp_arg)
            orig_data.merge(expmeta)
        #splt_args = split_args(node_res, node)
        res_ls.append(split_args(node_res, node))
        for vr in orig_data.vars_used:
            curn.var_tracker.use_var(vr)
        curn.add_shell_constr(
            f"Expansion",
            pru.Implies(node.get_path_cond(), pru.AndExp(orig_data.pru_lits)),
        )
    return res_ls
    # TODO: lltodo this basically seems like the wrong approach. Instead of trying to collapse symbstrs across nodes and all 
    # this business, let's just bubble everything up
    # so that the caller of expand_simple needs to account for all the possible expansions
    # -- across both the different nodes(paths) AND the different split possibilities.
    # then we'll make that function do a loop over all the things, and just proceed with the first split possibility per node.
    #
    # There MIGHT be some value in collapsing all of the expansion possibilities that are identical. 
    # E.g. if there are 10 nodes expanding `echo hi`, no need to eval that 10 times... but can consider that later.
    mx_field_len = max([len(i) for i in res_ls])
    # Append so that all arglists have same length
    for i in range(len(res_ls)):
        while len(res_ls[i]) < mx_field_len:
            empty_arg: Symbstr = [""]
            res_ls[i].append(empty_arg)

    final_field_list: list[Symbstr] = collapse_args(curn, res_ls)
    return final_field_list


def expand_simple(unexp_cmd: CommandNode, curn: nodelist.NodeList) -> list[list[tuple[CommandNode, Optional[pru.PRU]]]]:
    """
    Returns all possible expansions of `unexp_cmd`. The possibilities are provided as two nested levels of lists:
    1. Expansions for each active path condition in `curn` (`curn.nodes`)
    2. All possible expansions for a given path condition considering word splits, 
       along with a constraint describing when the splitting occurs
    """
    logging.debug(f"expanding args: {unexp_cmd.arguments}")
    # top level list is nodes, second level list is words
    all_possible_arg_expansions_across_nodes_and_word_splits = expand_args(curn, unexp_cmd.arguments)
    assert len(all_possible_arg_expansions_across_nodes_and_word_splits) == len(curn.nodes)
    res = []
    for one_node_arg_expansions in all_possible_arg_expansions_across_nodes_and_word_splits:
        this_node_cmd_expansions = []
        for arg_expansion in one_node_arg_expansions:
            cmd = deepcopy(unexp_cmd)
            cmd.arguments = arg_expansion.args  # type: ignore
            farg: Symbstr = cmd.arguments[0] if cmd.arguments else [""]  # type: ignore
            is_spec_built = cmd.arguments and is_special_builtin(
                symb_utils.symbstr_to_str(farg)
            )
            if is_spec_built:
                for symbnode in curn.nodes:
                    symbnode.enter_cmdlocal()
            # Expand redir list
            for i, r in enumerate(cmd.redir_list):
                arg_ls = []
                for symnode in curn.nodes:
                    file_arg, expdata = expand_arg(symnode, r.arg, quoted=False)
                    file_arg_symbstr = symb_utils.symb_string_of_arg(file_arg)
                    arg_ls.append([file_arg_symbstr])
                    process_metadata(curn, symnode, expdata)
                file_arg_res = collapse_args(curn, arg_ls)
                cmd.redir_list[i].arg = file_arg_res[0]  # TODO handle multiargs correctlt
            expand_assignments(cmd, curn)
            if is_spec_built:
                for symbnode in curn.nodes:
                    symbnode.exit_cmdlocal()
            this_node_cmd_expansions.append((cmd, arg_expansion.constraint))
        res.append(this_node_cmd_expansions)

    return res


# PRIVATE FUNCTIONS DONT INVOKE
# POSIX Section2.15


def is_special_builtin(cmd_name: Optional[str]) -> bool:
    return cmd_name in [
        "break",
        ":",
        "continue",
        ".",
        "eval",
        "exec",
        "exit",
        "readonly",
        "return",
        "set",
        "shift",
        "times",
        "trap",
        "unset",
    ]

def split_args(args: list[list[ArgChar]], curn: symb_node.SymbNode) -> list[SymbArgsVariant]:
    ifs, constr = curn.get_varstore("IFS")
    assert constr is None
    assert ifs is not None and ifs.value is not None
    ifs = symb_utils.symbstr_to_str(ifs.value)
    if ifs is None:
        logging.info("IFS is symbolic. assuming IFS  ")
        ifs = "\n\t "
    ifs_str = ifs
    ifs = [ord(c) for c in ifs]

    res: list[list[ArgChar]] = []
    for arg in args:
        cur = []
        for c in arg:
            if isinstance(c, CArgChar) and c.char in ifs:
                # split!
                if len(cur) > 0:  # TODO(mmg): or if val isn't IFS whitespace
                    res.append(cur)
                cur = []
            else:
                cur.append(c)

        if len(cur) > 0:
            res.append(cur)

    return make_symb_args_variants(res, ifs_str)


OneArg = list[ArgChar]
ArgList = list[OneArg]

def make_symb_args_variants(args: ArgList, ifs: str) -> list[SymbArgsVariant]:
    """
    Make a list of SymbArgsVariant from the given args and IFS.
    Each SymbArgsVariant represents a possible split of the args based on the IFS.

    Examples (abbreviating CArgChars as single-character strings):
    ```
    # no splits, just a single variant: `rm -rf`
    make_symb_args_variants([['r', 'm'], ['-', 'r', 'f']], " ") -> [SymbArgsVariant(['rm', '-rf'], pru.TrueAtom())]

    # one var that could be split, or not: `rm $d2`: generate two new vars as separate args and a constraint that relates the new vars to the original var
    make_symb_args_variants([['r', 'm'], [SymbArgChar('d2')]], " ") 
      -> [SymbArgsVariant([['rm'], [SymbArgChar('d2')]], 
                          pru.Neg(pru.SEq(SymbArgChar('d2'), pru.AppString([SymbArgChar('_split_d2_lhs'), " ", SymbArgChar('_split_d2_rhs')])))), 
          SymbArgsVariant([['rm'], [SymbArgChar('_split_d2_lhs')], [SymbArgChar('_split_d2_rhs')]], pru.SEq(SymbArgChar('d2'), pru.AppString([SymbArgChar('_split_d2_lhs'), " ", SymbArgChar('_split_d2_rhs')])))]
          #                                                      ^^ NOTE: (critical) the split vars become two distinct symbolic string arguments!
                          pru.SEq(SymbArgChar('d2'), pru.AppString([SymbArgChar('_split_d2_lhs'), " ", SymbArgChar('_split_d2_rhs')]))))
    # two vars that could be split, or not: `rm $d2$d3`: generate four new vars as separate args and a constraint that relates the new vars to each original var
    make_symb_args_variants([['r', 'm'], [SymbArgChar('d2'), SymbArgChar('d3')]], " ")
      -> [SymbArgsVariant([['rm'], [SymbArgChar('d2'), SymbArgChar('d3')]],
                                                    # ^^ NOTE: d2+d3 remains a single arg
                          pru.And(pru.Neg(pru.SEq(SymbArgChar('d2'), pru.AppString([SymbArgChar('_split_d2_lhs'), " ", SymbArgChar('_split_d2_rhs')]))),
                                  pru.Neg(pru.SEq(SymbArgChar('d3'), pru.AppString([SymbArgChar('_split_d3_lhs'), " ", SymbArgChar('_split_d3_rhs')]))))
          SymbArgsVariant([['rm'], [SymbArgChar('_split_d2_lhs')], [SymbArgChar('_split_d2_rhs'), SymbArgChar('d3')]], 
                                   # ^ one new arg for the lhs of d2 split, and then one arg for the rhs+d3
                          pru.And(pru.SEq(SymbArgChar('d2'), pru.AppString([SymbArgChar('_split_d2_lhs'), " ", SymbArgChar('_split_d2_rhs')])),
                                  pru.Neg(pru.SEq(SymbArgChar('d3'), pru.AppString([SymbArgChar('_split_d3_lhs'), " ", SymbArgChar('_split_d3_rhs')]))))),
          SymbArgsVariant([['rm'], [SymbArgChar('d2'), SymbArgChar('_split_d3_lhs')], SymbArgChar('_split_d3_rhs')], 
                                   # ^ one arg for d2 + lhs of d3 split, and then one arg for the rhs
                          pru.And(pru.Neg(pru.SEq(SymbArgChar('d2'), pru.AppString([SymbArgChar('_split_d2_lhs'), " ", SymbArgChar('_split_d2_rhs')]))),
                                  pru.SEq(SymbArgChar('d3'), pru.AppString([SymbArgChar('_split_d3_lhs'), " ", SymbArgChar('_split_d3_rhs')])))),
          SymbArgsVariant([['rm'], [SymbArgChar('_split_d2_lhs')], [SymbArgChar('_split_d2_rhs'), SymbArgChar('_split_d3_lhs')], [SymbArgChar('_split_d3_rhs')]], 
                # both args split: ^ one arg d2_lhs,              one arg d2_rhs+d3_lhs, and                                   one arg for d3_rhs
                          pru.And(pru.SEq(SymbArgChar('d2'), pru.AppString([SymbArgChar('_split_d2_lhs'), " ", SymbArgChar('_split_d2_rhs')])),
                                  pru.SEq(SymbArgChar('d3'), pru.AppString([SymbArgChar('_split_d3_lhs'), " ", SymbArgChar('_split_d3_rhs')]))))]
    # if there are constant strings appended around vars, just carry them along in each variant:
    make_symb_args_variants([['r', 'm'], ['f', 'o', 'o', SymbArgChar('d2'), 'b', 'a', 'r']], " ") 
      -> [SymbArgsVariant([['rm'], ['f', 'o', 'o', SymbArgChar('d2'), 'b', 'a', 'r']], pru.Neg(pru.SEq(SymbArgChar('d2'), pru.AppString([SymbArgChar('_split_d2_lhs'), " ", SymbArgChar('_split_d2_rhs')])))), 
          SymbArgsVariant([['rm'], ['foo', SymbArgChar('_split_d2_lhs')], [SymbArgChar('_split_d2_rhs'), 'bar']], pru.SEq(SymbArgChar('d2'), pru.AppString([SymbArgChar('_split_d2_lhs'), " ", SymbArgChar('_split_d2_rhs')])))]
    """
    split_variants: list[list[ArgChar | VarSplitPossibility]] = [expand_possible_var_splits(arg, ifs) for arg in args]
    unpacked = unpack_split_variants(split_variants)
    return [SymbArgsVariant([symb_utils.symb_string_of_arg(arg) for arg in alv.args], 
                            alv.constraint) \
            for alv in unpacked]


@dataclass
class VarSplitPossibility: # Represents a possible split of an argument into two: from [*<before>, <original_var>, *<after>] to [*<before>, <lhs>] and [<rhs>, *<after>]
    original_var: SymbArgChar  # The variable that was split
    lhs: SymbArgChar
    rhs: SymbArgChar
    constraint: pru.PRU # Condition under which this split could happen

def expand_possible_var_splits(arg: OneArg, ifs: str) -> list[ArgChar | VarSplitPossibility]:
    """
    Expand the argument by splitting it on the IFS characters.
    Returns either the argument, if it does not contain any symbolic variables, or a list of ArgSplitPossibility objects if it does.
    """
    def convert(c: ArgChar) -> ArgChar | VarSplitPossibility:
        if isinstance(c, SymbArgChar):
            # This is a symbolic variable, we can split it
            name = c.var
            lhs = SymbArgChar(create_fresh_varname(f"_split_{name}_lhs"))
            rhs = SymbArgChar(create_fresh_varname(f"_split_{name}_rhs"))
            return VarSplitPossibility(
                original_var=c,
                lhs=lhs,
                rhs=rhs,
                constraint=pru.OrExp([pru.SEq(specs.make_arg_sexp(c),
                                              specs.make_argls_sexp([lhs, ifs_char, rhs])) \
                                      for ifs_char in ifs]),
            )
        else:
            return c
    return [convert(c) for c in arg]

@dataclass
class ArgPossibility:
    split_up: list[OneArg]
    constraint: Optional[pru.PRU]  # Condition under which this argument is valid

def enumerate_arg_possibilities(arg_with_splits: list[ArgChar | VarSplitPossibility]) -> list[ArgPossibility]:
    """
    Enumerate all possible arguments from the given argument with splits.
    Every combination of split and unsplit variants is returned as a list of ArgPossibility objects.
    Each ArgPossibility contains the new possible arguments to replace this original, and a constraint that describes the condition under which this argument is valid.

    Example:
    enumerate_arg_possibilities(['r', 'm']) -> [ArgPossibility([['r', 'm']], None)]
    enumerate_arg_possibilities(['r', 'm', VarSplitPossibility('d2', 'lhs', 'rhs', C)]) -> [ArgPossibility([['r', 'm', SymbArgChar('d2')]], pru.Neg(C))),
                                                                                            ArgPossibility([['r', 'm', SymbArgChar('_split_d2_lhs')], [SymbArgChar('_split_d2_rhs')]], C)]
    """

    def powerset(iterable):
        s = list(iterable)
        return itertools.chain.from_iterable(itertools.combinations(s, r) for r in range(len(s) + 1))

    def unsplit_vars_in(argparts: list[ArgChar | VarSplitPossibility]) -> tuple[list[ArgChar], list[pru.PRU]]:
        res = []
        constraint = []
        for part in argparts:
            match part:
                case VarSplitPossibility(original_var=v, constraint=c):
                    res.append(v)
                    if constraint is None:
                        constraint = c
                    else:
                        constraint.append(pru.Neg(c))
                case other:
                    res.append(other)
        return res, constraint

    possibilities: list[ArgPossibility] = []

    # Pre compute all possible combinations of vars that can be split in this one arg
    indices_of_vars = [i for i, c in enumerate(arg_with_splits) if isinstance(c, VarSplitPossibility)]
    indices_to_expand = powerset(indices_of_vars)
    for indices in indices_to_expand:
        the_split_up_arg = []
        constraints = []
        last_split_end_index = 0
        last_split_rhs = []
        for index in indices:
            split_possibility = arg_with_splits[index]
            assert isinstance(split_possibility, VarSplitPossibility)
            arg_to_here, constraints_to_here = unsplit_vars_in(arg_with_splits[last_split_end_index:index])
            the_split_up_arg.append(last_split_rhs + arg_to_here + [split_possibility.lhs])
            constraints.extend(constraints_to_here + [split_possibility.constraint])
            last_split_end_index = index + 1
            last_split_rhs = [split_possibility.rhs]
        # Add the rest of the arg after the last split
        arg_to_here, constraint_to_here = unsplit_vars_in(arg_with_splits[last_split_end_index:])
        constraints.extend(constraint_to_here)
        the_split_up_arg.append(last_split_rhs + arg_to_here)
        possibilities.append(ArgPossibility(the_split_up_arg, 
                                            pru.AndExp(constraints) if constraints else None))
    return possibilities

    # symb_strs_plus_split_points: list[Symbstr | Split] = []
    # for arg in res:
    #     # arg: list[ArgChar]
    #     var_indices = [i for i, c in enumerate(arg) if isinstance(c, SymbArgChar)]
    #     if var_indices:
    #         # this is an arg that has somewhere inside some variable refs
    #         # plan: turn this arg into (1 + count(vars in arg)) new ones where each one ends and/or starts with a var
    #         new_args = []
    #         var_indices_ext = [0] + var_indices + [len(arg) - 1] # pad with 0 and last index of the arg
    #         for i in range(1, len(var_indices_ext) - 1): # skip over the padding...
    #             # ... which guarantees that these are always valid indices
    #             this_var_index = var_indices_ext[i]
    #             last_var_index = var_indices_ext[i - 1]
    #             next_var_index = var_indices_ext[i + 1]
    #             var = arg[this_var_index]
    #             assert isinstance(var, SymbArgChar)
    #             name = var.var
    #             split = Split(
    #                 original_var=var,
    #                 before=symb_utils.symb_string_of_arg(arg[last_var_index:this_var_index]),
    #                 lhs=SymbArgChar(symb_utils.create_fresh_varname(f"_split_{name}_lhs_")),
    #                 rhs=SymbArgChar(symb_utils.create_fresh_varname(f"_split_{name}_rhs_")),
    #                 after=symb_utils.symb_string_of_arg(arg[this_var_index + 1:next_var_index]),
    #                 constraint=pru.Neg(pru.SEq(var, pru.AppString([symb_utils.create_fresh_var(), ifs.value, symb_utils.create_fresh_var()]))),
    #             )
    #             symb_strs_plus_split_points.append(split)
    #     else: # no vars
    #         symb_strs_plus_split_points.append(symb_utils.symb_string_of_arg(arg))

@dataclass
class ArgListVariant:
    args: ArgList
    constraint: Optional[pru.PRU]  # Condition under which this variant is valid

def unpack_split_variants(args: list[list[ArgChar | VarSplitPossibility]]) -> list[ArgListVariant]:
    """
    Unpack all of the possible arg splits encoded in `args`.
    Examples (abbreviating CArgChars as strings):
    unpack_split_variants([['r', 'm'], ['-', 'r', 'f']]) -> ArgListVariant([['r', 'm'], ['-', 'r', 'f']]) # no splits, nothing unpacked
    unpack_split_variants([
                           ['r', 'm'],
                           ArgSplitPossibility([], SymbArgChar('d2'), SymbArgChar('_split_d2_lhs'), SymbArgChar('_split_d2_rhs'), ['A', 'p', 'p'], C)]) ->

    """
    # Idea here is that `enumerated_arg_variants` is a kind of compact columnar representation of the combinations, like
    # index: 0 1 2 3 <-- each index corresponds to one raw argument
    #        a b c d
    #          b'  d'
    #              d''
    #
    # and we want to unfold it with `all_possible_combinations`, like
    # indexes here correspond to one possible full list of arguments
    # 0: a b  c d
    # 1: a b  c d'
    # 2: a b  c d''
    # 3: a b' c d
    # 4: a b' c d'
    # 5: a b' c d''
    assert args, "empty command?"
    enumerated_arg_variants: list[list[ArgPossibility]] = [enumerate_arg_possibilities(arg) for arg in args]
    all_possible_combinations: list[list[ArgPossibility]] = [[]]
    for one_args_enumeration in enumerated_arg_variants:
        combos = itertools.product(all_possible_combinations, one_args_enumeration)
        all_possible_combinations = [prefix + [next_arg] for prefix, next_arg in combos]

    res = []
    for arglist in all_possible_combinations:
        # arglist: list[ArgPossibility]
        this_arglist_flattened: list[OneArg] = []
        for ap in arglist:
            this_arglist_flattened.extend(ap.split_up)
        constraints = [ap.constraint for ap in arglist if ap.constraint is not None]
        this_arglist_constraint = pru.AndExp(constraints) if constraints else None
        res.append(ArgListVariant(this_arglist_flattened, this_arglist_constraint))

    return res

def char_code(c) -> ArgChar:
    if c in "'\\\"()${}[]*?":
        return EArgChar(ord(c))
    else:
        return CArgChar(ord(c))


def should_pathname_exp(arg: list[ArgChar]) -> bool:
    isglob = False
    for arg_char in arg:
        if isinstance(arg_char, SymbArgChar):
            if isinstance(arg_char.argtype, pru.SymbNormal):
                return False
            else:
                isglob = True
    return isglob


def concrete_pathname_expansion(
    curn: symb_node.SymbNode, arg: list[ArgChar], quoted=False
) -> list[ArgChar]:
    if quoted or not curn.concrete:
        return arg
    if not should_pathname_exp(arg):
        return arg
    conc_arg = symb_utils.symbstr_to_str(symb_utils.symb_string_of_arg(arg))
    if conc_arg is None:
        return arg

    unexp_path = []
    for chr in conc_arg:
        if isinstance(chr, str):
            unexp_path.append(chr)
        elif isinstance(chr, SymbArgChar):
            match chr.argtype:
                case pru.SymbGlobStar():
                    unexp_path.append("*")
                case pru.SymbGlobQMark():
                    unexp_path.append("?")
                case _:
                    raise ValueError("bad type")
        elif isinstance(chr, CArgChar):
            unexp_path.append(chr.pretty())
        else:
            raise ValueError("bad type")
    unexp_path_str: str = "".join(unexp_path)
    is_relative = unexp_path_str.startswith("/")
    if is_relative:
        pwd, constr = curn.get_varstore("PWD")
        assert pwd is not None and pwd.value is not None
        if (
            pwd_conc := symb_utils.symbstr_to_str(pwd.value)
        ) is None:  # Relative path cannot be concretized
            return arg
        unexp_path_str = os.path.join(pwd_conc, unexp_path_str)

    exp_path = glob.glob(unexp_path_str)
    if len(exp_path) == 0:
        reporter.REPORTER.add_syntax_error(
            "", f"Path {unexp_path_str} does not match anything on current system"
        )
    return list(
        itertools.chain(*[[CArgChar(ord(elem)) for elem in i] for i in exp_path])
    )


def expand_arg(
    node: symb_node.SymbNode, arg_chars: list[ArgChar], quoted=False
) -> tuple[list[ArgChar], ExpMetadata]:  # Each list is along one node
    # First collect all character expansion results
    all_charexp_res: list[ArgChar] = []
    origmeta = ExpMetadata()
    for arg_char in arg_chars:
        charexp_res, metadata = expand_arg_char(node, arg_char, quoted)
        # assert len(charexp_res) == len(curn.nodes)
        origmeta.merge(metadata)
        all_charexp_res.extend(charexp_res)  # type: ignore
    return all_charexp_res, origmeta


# TODO-RR vast room for improvement
def arg_ispattern(arg: list[ArgChar]) -> bool:
    for a in arg:
        if not isinstance(a, CArgChar) or chr(a.char) in ["*", "?", "{", "}", "[", "]"]:
            return True
    return False


def handle_patternchars(patternchar: str, quoted, curn: symb_node.SymbNode):
    assert len(patternchar) == 1
    if patternchar in ["*", "?", "{", "}", "[", "]"] and not quoted:
        if patternchar in "*?" and curn.set_options.f is True:
            reporter.REPORTER.add_error_message(error_report.GlobDisabled())
            return patternchar
        if patternchar == "*":
            return SymbArgChar(create_fresh_varname("starglob:nexpand:627"), pru.SymbGlobStar())
        elif patternchar == "?":
            return SymbArgChar(create_fresh_varname("qmarkglob:nexpand:629"), pru.SymbGlobQMark())
        else:
            logging.debug("May have gotten a pattern char ignoring.")
            # raise Unimplemented("globbing", arg_char)
            return patternchar
    return patternchar


def get_conc_val(curn: symb_node.SymbNode, varname: str) -> str | None:
    varval, constrs = curn.get_varstore(varname)
    if varval is None or varval.value is None:
        return None
    return symb_utils.symbstr_to_str(varval.value)


def expand_arg_char_singlenode(
    arg_char: ArgChar, quoted, curn: symb_node.SymbNode
) -> Sequence[ArgChar | str]:
    if isinstance(arg_char, CArgChar):
        if (
            (patternchar := chr(arg_char.char)) in ["*", "?"]
            and not quoted
            and not curn.in_assigflag
        ):
            return [handle_patternchars(patternchar, quoted, curn)]
        return [arg_char]
    elif isinstance(arg_char, EArgChar):
        ## 2021-09-15 MMG Just guessing here
        if arg_char.char in ["*", "?", "{", "}", "[", "]"] and not quoted:
            raise Unimplemented("globbing", arg_char)
        return [arg_char]
    elif isinstance(arg_char, TArgChar):
        arg = arg_char.string
        if arg is None or arg == "" or arg == "None":
            varval, constr = curn.get_varval("HOME")
            assert constr is None
            match varval:
                case VarSet(val):
                    return val 
                case VarUnset():
                    reporter.REPORTER.add_syntax_error("HOME", "HOME is unset")
                    return [""]
                case VarUnknown():
                    raise StuckExpansion(
                        "HOME is unknown. this should not happen - check ExpansionState init",
                        arg_char,
                    )
        else:
            # TODO 2020-12-10 getpwnam
            raise Unimplemented("~ with prefix", arg_char)
    elif isinstance(arg_char, AArgChar):
        return arith_expansion.arith_expansion(arg_char, curn) 
    elif isinstance(arg_char, SymbArgChar):
        return [arg_char]
    elif (
        isinstance(arg_char, BArgChar)
        or isinstance(arg_char, QArgChar)
        or isinstance(arg_char, VArgChar)
    ):
        raise ValueError("unreachable")
    elif isinstance(arg_char, str):
        return [arg_char]
    else:
        raise Unimplemented("weird object", arg_char)


def expand_arg_char(
    node: symb_node.SymbNode, arg_char: ArgChar, quoted: bool = False
) -> tuple[Sequence[ArgChar | str], ExpMetadata]:
    if isinstance(arg_char, QArgChar):
        # First collect all chr_res
        all_chr_res: list[ArgChar] = []
        expdata_orig = ExpMetadata()
        for chr in arg_char.arg:
            chr_res, expdata = expand_arg_char(node, chr, quoted=True)
            for chr in chr_res:
                if isinstance(chr, str):
                    all_chr_res.extend([char_code(c) for c in list(chr)])
                else:
                    all_chr_res.append(chr)
            expdata_orig.merge(expdata)
        return [QArgChar(all_chr_res)], expdata_orig
    elif isinstance(arg_char, VArgChar):
        exp_arg, metadata = expand_var(
            fmt=arg_char.fmt,
            null=arg_char.null,
            var=arg_char.var,
            arg=arg_char.arg,
            quoted=quoted,
            node=node,
        )
        assert isinstance(exp_arg, list)
        return exp_arg, metadata
    elif isinstance(arg_char, BArgChar):
        raise ValueError("unreachable")
    else:
        return expand_arg_char_singlenode(arg_char, quoted, node), ExpMetadata()


def normal_fetch(
    curn: symb_node.SymbNode, var: str
) -> tuple[list[SymbArgChar | str], Optional[pru.PRU]]:
    value, constr = curn.get_varval(var)
    match value:
        case VarSet(val):
            return val, constr
        case VarUnset():
            return [""], constr
        case VarUnknown():
            # if var.isupper() and not curn.concrete:
            reporter.REPORTER.add_syntax_error(
                "",
                f"Variable {var} is not defined. Assuming exported from environment",
            )
            nres: pru.Symbstr = [
                SymbArgChar(symb_utils.create_fresh_varname(f"unknown_var_{var}:nexpand:744"))
            ]
            curn.add_variable(var, None, nres)
            return nres, constr


def expand_var(
    fmt, null, var, arg, quoted, node: symb_node.SymbNode
) -> tuple[Sequence[ArgChar | str], ExpMetadata]:
    # TODO 2020-12-10 special variables
    # Handle special cases
    expdata = ExpMetadata()
    expdata.vars_used.append(var)
    varval, constr = normal_fetch(node, var)
    assert isinstance(varval, list)
    match fmt:
        case "Normal":
            return varval, expdata
        case "Length":
            reporter.REPORTER.add_expansion_form("Length")
            lenfmt_ls: list[SymbArgChar | str] = []
            constrs: list[pru.NonFS] = []
            conc_val = symb_utils.symbstr_to_str(varval) 
            if conc_val is not None:
                lenfmt_ls = [str(len(conc_val))]
            else:
                resvar = symb_utils.create_fresh_var("expansion_varlen:nexpand:770")
                lit = pru.Implies(
                    node.get_path_cond(),
                    pru.SLen(
                        specs.make_argls_sexp(varval, is_path=False),  
                        specs.make_arg_sexp(resvar),
                    ),
                )
                lenfmt_ls = [resvar]
                constrs.append(lit)
                expdata.pru_lits.append(lit)

            return lenfmt_ls, expdata

        case "Minus":
            reporter.REPORTER.add_expansion_form("Minus")
            minus_arg_exp = symb_utils.symb_string_of_arg(arg)
            conc_val = symb_utils.symbstr_to_str(varval)  
            if conc_val is not None:
                fmt_ls = [conc_val]
            else:
                value_exp = specs.make_argls_sexp(varval, is_path=False)  
                nvar = symb_utils.create_fresh_var("expansion_minus:nexpand:792")
                nvar_exp = specs.make_arg_sexp(nvar)
                lit = specs.ite(
                    pru.str_empty(value_exp),
                    pru.SEq(
                        nvar_exp,
                        specs.make_argls_sexp(minus_arg_exp, is_path=False),
                    ),
                    pru.SEq(nvar_exp, value_exp),
                )
                fmt_ls = [nvar]
                expdata.pru_lits.append(lit)

            return fmt_ls, expdata
        case "Assign":
            reporter.REPORTER.add_expansion_form("Assign")
            if symb_node.is_special_var(var):
                reporter.REPORTER.add_syntax_error(
                    "",
                    f"Variable {var} is a special variable and cannot be assigned to",
                )
                reporter.REPORTER.set_judgement(symb_result.ShseerResult.SymbError)
            exp_arg, metadata = expand_arg(node, arg)
            assign_arg_exp = symb_utils.symb_string_of_arg(exp_arg)
            expdata.merge(metadata)
            conc_val = symb_utils.symbstr_to_str(varval) 
            if conc_val is not None:
                assign_ls = [conc_val]
            elif conc_val == "":
                assign_ls: list[SymbArgChar | str] = assign_arg_exp
            else:
                curval_exp = specs.make_argls_sexp(varval, is_path=False)  
                nvar = symb_utils.create_fresh_var("expansion_assignment:nexpand:824")
                nvar_exp = specs.make_arg_sexp(nvar)
                assign_exp = specs.make_argls_sexp(assign_arg_exp, is_path=False)
                lit = specs.ite(
                    pru.str_empty(curval_exp),
                    pru.SEq(nvar_exp, assign_exp),
                    pru.SEq(nvar_exp, curval_exp),
                )
                assign_ls = [nvar]
                expdata.pru_lits.append(lit)
            return assign_ls, expdata
        case "Plus":
            reporter.REPORTER.add_expansion_form("Plus")
            plus_arg_exp = symb_utils.symb_string_of_arg(arg)
            conc_val = symb_utils.symbstr_to_str(varval) 
            if conc_val is None or conc_val == "":
                fmt_ls = [""]
            else:
                value_exp = specs.make_argls_sexp(varval, is_path=False)  
                nvar = symb_utils.create_fresh_var("expansion_plus:nexpand:843")
                nvar_exp = specs.make_arg_sexp(nvar)
                lit = specs.ite(
                    pru.str_empty(value_exp),
                    pru.SEq(nvar_exp, value_exp),
                    pru.SEq(
                        nvar_exp,
                        specs.make_argls_sexp(plus_arg_exp, is_path=False),
                    ),
                )
                fmt_ls = [nvar]
                expdata.pru_lits.append(lit)
            return fmt_ls, expdata
        case "Question":
            reporter.REPORTER.add_expansion_form("Question")
            constrs: list[pru.PRU] = []
            conc_val = symb_utils.symbstr_to_str(varval)  
            if conc_val == "":
                reporter.REPORTER.add_syntax_error(
                    var,
                    f"Variable {var} is unset or empty and used in question parameter expansion resulting in an error",
                )
                reporter.REPORTER.set_judgement(symb_result.ShseerResult.SymbError)
                raise ValueError("unreachable")
            else:
                nvar = symb_utils.create_fresh_var("expansion_question:nexpand:868")
                nvar_exp = specs.make_arg_sexp(nvar)
                value_exp = specs.make_argls_sexp(varval, is_path=False)  
                constr = specs.ite(
                    pru.str_empty(value_exp),
                    pru.FalseAtom(),
                    pru.SEq(nvar_exp, value_exp),
                )
                fmt_ls = [nvar]
                expdata.pru_lits.append(constr)
            return fmt_ls, expdata
        case "TrimR" | "TrimRMax" | "TrimL" | "TrimLMax":
            # TODO-RRR need patterns
            # This is basically a non-implementation. This still needs to be implemented
            reporter.REPORTER.add_expansion_form(fmt)
            ressymbvar = SymbArgChar(create_fresh_varname("expansion_trim:nexpand:883"))
            return [ressymbvar], expdata
        case _:
            raise ValueError("bad parameter format {}".format(fmt))
    raise ValueError("bad parameter format {}".format(fmt))


def process_metadata(
    curn: nodelist.NodeList, node: symb_node.SymbNode, expdata: ExpMetadata
):
    for vr in expdata.vars_used:
        curn.var_tracker.use_var(vr)
    curn.add_shell_constr(
        f"Expansion", pru.Implies(node.get_path_cond(), pru.AndExp(expdata.pru_lits))
    )


def expand_assignments(node: CommandNode, curn: nodelist.NodeList) -> None:
    if len(node.assignments) == 0:
        return
    curn.enter_assig()
    for symnode in curn.nodes:
        origdata = ExpMetadata()
        for assign in node.assignments:
            # rules.hook_assigments(assign)
            vr: str = assign.var
            expval = process_barg_char(curn, [assign.val])
            assert len(expval) == 1
            val_expanded, expdata = expand_arg(symnode, expval[0])
            origdata.merge(expdata)

            symb_datatypes.NodeSymMaps.adddecl(vr)
            symnode.set_varstore(
                vr, VarStore(None, symb_utils.symb_string_of_arg(val_expanded))
            )
        process_metadata(curn, symnode, origdata)
    curn.exit_assignment()
    logging.debug(f"Removing assignments from command node {node}")
    node.assignments = []
