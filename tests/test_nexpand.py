# lltodo: add test for
# [ -e ${2}Applications/iTunes.app ] ==>
# [[[], [-, e], [V(Normal,False,2,[]), A, p, p, l, i, c, a, t, i, o, n, s, /, i, T, u, n, e, s, ., a, p, p], []]]
import pytest
from unittest.mock import Mock, MagicMock, patch
from dataclasses import dataclass
from typing import Optional

# Import the module under test
import shseer.nexpand as nexpand
from shseer.nexpand import ExpMetadata, expand_arg, expand_args, expand_simple

# Mock imports
from shasta.ast_node import (
    ArgChar, CArgChar, VArgChar, QArgChar, BArgChar, CommandNode
)
from shseer.defs import SymbArgChar, Symbstr
import shseer.defs as pru
import shseer.symb_node as symb_node
import shseer.nodelist as nodelist
import shseer.symb_utils as symb_utils
import shseer.specs as specs

class ComparableCArgChar(CArgChar):
    def __eq__(self, other):
        return (isinstance(other, ComparableCArgChar) or isinstance(other, CArgChar)) \
            and self.char == other.char

def encode_args(v):
    match v:
        case str():
            return ComparableCArgChar(ord(v))
        case [_, *_]:
            res = []
            for el in v:
                res.append(encode_args(el))
            return res
        case QArgChar():
            return QArgChar(encode_args(v.arg))
        case _:
            return v
        
def simplify(pru_exp):
    import re
    match pru_exp:
        case pru.AndExp(ls=[one]) | pru.OrExp(ls=[one]):
            return simplify(one)
        case pru.AndExp(ls):
            return pru.AndExp([simplify(e) for e in ls])
        case pru.OrExp(ls):
            return pru.OrExp([simplify(e) for e in ls])
        case pru.AppString(ls):
            return pru.AppString([simplify(e) for e in ls])
        case pru.SEq(v1, v2):
            return pru.SEq(simplify(v1), simplify(v2))
        case pru.Neg(e):
            return pru.Neg(simplify(e))
        case SymbArgChar(var=name):
            return SymbArgChar(re.sub('![0-9]+$', '', name))
        case pru.SString(name, stype):
            return pru.SString(re.sub('![0-9]+$', '', name), stype)
        case [_, *_]:
            return [simplify(e) for e in pru_exp]
        case other:
            return other

class TestEnumerateArgPossibilities:

    def test_no_splits(self):
        """Test with no splits, just a single variant: ['r', 'm']"""
        arg_with_splits = encode_args(['r', 'm'])
        result = nexpand.enumerate_arg_possibilities(arg_with_splits)
        
        assert len(result) == 1
        assert result[0].split_up == [arg_with_splits]
        assert result[0].constraint is None

    def test_single_var_split_possibility(self):
        """Test one var that could be split, or not: ['r', 'm', VarSplitPossibility('d2', 'lhs', 'rhs', C)]"""
        arg_with_splits = encode_args(['r', 'm', nexpand.VarSplitPossibility(SymbArgChar('d2'), SymbArgChar('_split_d2_lhs'), SymbArgChar('_split_d2_rhs'), 
                                                                             pru.SEq(pru.SString('d2'), specs.make_argls_sexp([SymbArgChar('_split_d2_lhs'), " ", SymbArgChar('_split_d2_rhs')])))])
        result = nexpand.enumerate_arg_possibilities(arg_with_splits)
        
        assert len(result) == 2
        
        # First variant: d2 is not split
        assert result[0].split_up == encode_args([['r', 'm', SymbArgChar('d2')]])
        assert simplify(result[0].constraint) == pru.Neg(pru.SEq(pru.SString('d2'), specs.make_argls_sexp([SymbArgChar('_split_d2_lhs'), " ", SymbArgChar('_split_d2_rhs')])))
        
        # Second variant: d2 is split
        assert result[1].split_up == encode_args([['r', 'm', SymbArgChar('_split_d2_lhs')], 
                                                  [SymbArgChar('_split_d2_rhs')]])
        assert simplify(result[1].constraint) == pru.SEq(pru.SString('d2'), specs.make_argls_sexp([SymbArgChar('_split_d2_lhs'), " ", SymbArgChar('_split_d2_rhs')]))

    def test_two_vars_split_possibilities(self):
        """Test two vars that could be split, or not: ['r', 'm', VarSplitPossibility('d2', 'lhs', 'rhs', C), VarSplitPossibility('d3', 'lhs', 'rhs', C)]"""
        arg_with_splits = encode_args(['r', 'm',
            nexpand.VarSplitPossibility(SymbArgChar('d2'), SymbArgChar('_split_d2_lhs'), SymbArgChar('_split_d2_rhs'),
                                        pru.SEq(pru.SString('d2'), specs.make_argls_sexp([SymbArgChar('_split_d2_lhs'), " ", SymbArgChar('_split_d2_rhs')]))),
            nexpand.VarSplitPossibility(SymbArgChar('d3'), SymbArgChar('_split_d3_lhs'), SymbArgChar('_split_d3_rhs'),
                                        pru.SEq(pru.SString('d3'), specs.make_argls_sexp([SymbArgChar('_split_d3_lhs'), " ", SymbArgChar('_split_d3_rhs')]))),
        ])
        result = nexpand.enumerate_arg_possibilities(arg_with_splits)
        
        assert len(result) == 4
        
        all_splits = [result[i].split_up for i in range(4)]
        print(all_splits)
        # First variant: neither d2 nor d3 split
        assert result[0].split_up == encode_args([['r', 'm', SymbArgChar('d2'), SymbArgChar('d3')]])
        
        # Second variant: d2 split, d3 not split
        assert result[1].split_up == encode_args([['r', 'm', SymbArgChar('_split_d2_lhs')], [SymbArgChar('_split_d2_rhs'), SymbArgChar('d3')]])
        
        # Third variant: d2 not split, d3 split
        assert result[2].split_up == encode_args([['r', 'm', SymbArgChar('d2'), SymbArgChar('_split_d3_lhs')], [SymbArgChar('_split_d3_rhs')]])
        
        # Fourth variant: both d2 and d3 split
        assert result[3].split_up == encode_args([['r', 'm', SymbArgChar('_split_d2_lhs')], 
                                 [SymbArgChar('_split_d2_rhs'), SymbArgChar('_split_d3_lhs')], 
                                 [SymbArgChar('_split_d3_rhs')]])

        assert result[2].constraint == pru.AndExp([pru.Neg(pru.SEq(pru.SString('d2'), specs.make_argls_sexp([SymbArgChar('_split_d2_lhs'), " ", SymbArgChar('_split_d2_rhs')]))),
                                                   pru.SEq(pru.SString('d3'), specs.make_argls_sexp([SymbArgChar('_split_d3_lhs'), " ", SymbArgChar('_split_d3_rhs')]))])

class TestUnpackSplitVariants:
    def test_no_splits(self):
        arglist_with_splits = encode_args([['r', 'm'], ['-', 'r', 'f']])
        result = nexpand.unpack_split_variants(arglist_with_splits)
        assert result == [nexpand.ArgListVariant(arglist_with_splits, None)]

    def test_one_var(self):
        arglist_with_splits = encode_args([['r', 'm', nexpand.VarSplitPossibility(SymbArgChar('d2'), SymbArgChar('_split_d2_lhs'), SymbArgChar('_split_d2_rhs'), 
                                                                                  pru.SEq(pru.SString('d2'), specs.make_argls_sexp([SymbArgChar('_split_d2_lhs'), " ", SymbArgChar('_split_d2_rhs')])))]])
        result = nexpand.unpack_split_variants(arglist_with_splits)
        assert len(result) == 2
        assert result[0].args == encode_args([['r', 'm', SymbArgChar('d2')]])
        assert simplify(result[0].constraint) == pru.Neg(pru.SEq(pru.SString('d2'), specs.make_argls_sexp([SymbArgChar('_split_d2_lhs'), " ", SymbArgChar('_split_d2_rhs')])))
        assert result[1].args == encode_args([['r', 'm', SymbArgChar('_split_d2_lhs')], 
                                              [SymbArgChar('_split_d2_rhs')]])
        assert simplify(result[1].constraint) == pru.SEq(pru.SString('d2'), specs.make_argls_sexp([SymbArgChar('_split_d2_lhs'), " ", SymbArgChar('_split_d2_rhs')]))
    
    def test_one_var_2nd_arg(self):
        arglist_with_splits = encode_args([['r', 'm'], [nexpand.VarSplitPossibility(SymbArgChar('d2'), SymbArgChar('_split_d2_lhs'), SymbArgChar('_split_d2_rhs'), 
                                                                                  pru.SEq(pru.SString('d2'), specs.make_argls_sexp([SymbArgChar('_split_d2_lhs'), " ", SymbArgChar('_split_d2_rhs')])))]])
        result = nexpand.unpack_split_variants(arglist_with_splits)
        assert len(result) == 2
        assert result[0].args == encode_args([['r', 'm'], [SymbArgChar('d2')]])
        assert simplify(result[0].constraint) == pru.Neg(pru.SEq(pru.SString('d2'), specs.make_argls_sexp([SymbArgChar('_split_d2_lhs'), " ", SymbArgChar('_split_d2_rhs')])))
        assert result[1].args == encode_args([['r', 'm'], [SymbArgChar('_split_d2_lhs')], [SymbArgChar('_split_d2_rhs')]])
        assert simplify(result[1].constraint) == pru.SEq(pru.SString('d2'), specs.make_argls_sexp([SymbArgChar('_split_d2_lhs'), " ", SymbArgChar('_split_d2_rhs')]))

class TestMakeSymbArgsVariants:
    """Test the make_symb_args_variants function which generates symbolic argument variants."""
    
    def test_no_splits_simple_command(self):
        """Test with no splits, just a single variant: `rm -rf`"""
        # Convert from abbreviated form ['r', 'm'], ['-', 'r', 'f'] to actual CArgChars
        args = encode_args([['r', 'm'], ['-', 'r', 'f']])
        ifs = " "
        
        result = nexpand.make_symb_args_variants(args, ifs)
        
        assert len(result) == 1
        variant = result[0]
        assert variant.args == [['rm'], ['-rf']]
        # Should have a TrueAtom constraint since no splitting occurs
        assert variant.constraint == None
    
    def test_single_var_split_possibility(self):
        """Test one var that could be split, or not: `rm $d2`"""
        # Convert from abbreviated form ['r', 'm'], [SymbArgChar('d2')]
        args = encode_args([['r', 'm'], [SymbArgChar('d2')]])
        ifs = " "
        
        result = nexpand.make_symb_args_variants(args, ifs)
        
        # Should generate two variants: one where d2 is not split, one where it is
        assert len(result) == 2
        
        # First variant: d2 is not split
        no_split_variant = result[0]
        assert no_split_variant.args == [['rm'], [SymbArgChar('d2')]]
        # Constraint should be negation of split condition
        assert simplify(no_split_variant.constraint) == pru.Neg(pru.SEq(pru.SString('d2'), specs.make_argls_sexp([SymbArgChar('_split_d2_lhs'), " ", SymbArgChar('_split_d2_rhs')])))
        
        # Second variant: d2 is split into two arguments
        split_variant = result[1]
        assert simplify(split_variant.args) == [['rm'], [SymbArgChar('_split_d2_lhs')], [SymbArgChar('_split_d2_rhs')]]
        # Constraint should be the split condition
        assert simplify(split_variant.constraint) == pru.SEq(pru.SString('d2'), specs.make_argls_sexp([SymbArgChar('_split_d2_lhs'), " ", SymbArgChar('_split_d2_rhs')]))
    
    def test_two_vars_split_possibilities(self):
        """Test two vars that could be split, or not: `rm $d2$d3`"""
        # Convert from abbreviated form ['r', 'm'], [SymbArgChar('d2'), SymbArgChar('d3')]
        args = encode_args([['r', 'm'], [SymbArgChar('d2'), SymbArgChar('d3')]])
        ifs = " "
        
        result = nexpand.make_symb_args_variants(args, ifs)
        
        # Should generate four variants based on combinations of splits
        assert len(result) == 4

        # Find and verify each variant type
        variant_args = [simplify(variant.args) for variant in result]
        
        # Variant 1: neither d2 nor d3 split
        assert [['rm'], [SymbArgChar('d2'), SymbArgChar('d3')]] in variant_args
        
        # Variant 2: d2 split, d3 not split
        assert [['rm'], [SymbArgChar('_split_d2_lhs')], [SymbArgChar('_split_d2_rhs'), SymbArgChar('d3')]] in variant_args
        
        # Variant 3: d2 not split, d3 split
        assert [['rm'], [SymbArgChar('d2'), SymbArgChar('_split_d3_lhs')], [SymbArgChar('_split_d3_rhs')]] in variant_args
        
        # Variant 4: both d2 and d3 split
        assert [['rm'], [SymbArgChar('_split_d2_lhs')], [SymbArgChar('_split_d2_rhs'), SymbArgChar('_split_d3_lhs')], [SymbArgChar('_split_d3_rhs')]] in variant_args

        # check the constraints
        for variant in result:
            a = simplify(variant.args)
            if a == [['rm'], [SymbArgChar('d2'), SymbArgChar('d3')]]:
                assert simplify(variant.constraint) == pru.AndExp([pru.Neg(pru.SEq(pru.SString('d2'), specs.make_argls_sexp([SymbArgChar('_split_d2_lhs'), " ", SymbArgChar('_split_d2_rhs')]))),
                                                                   pru.Neg(pru.SEq(pru.SString('d3'), specs.make_argls_sexp([SymbArgChar('_split_d3_lhs'), " ", SymbArgChar('_split_d3_rhs')])))])
            elif a == [['rm'], [SymbArgChar('_split_d2_lhs')], [SymbArgChar('_split_d2_rhs'), SymbArgChar('d3')]]:
                assert simplify(variant.constraint) == pru.AndExp([pru.SEq(pru.SString('d2'), specs.make_argls_sexp([SymbArgChar('_split_d2_lhs'), " ", SymbArgChar('_split_d2_rhs')])),
                                                                   pru.Neg(pru.SEq(pru.SString('d3'), specs.make_argls_sexp([SymbArgChar('_split_d3_lhs'), " ", SymbArgChar('_split_d3_rhs')])))])
            elif a == [['rm'], [SymbArgChar('d2'), SymbArgChar('_split_d3_lhs')], [SymbArgChar('_split_d3_rhs')]]:
                assert simplify(variant.constraint) == pru.AndExp([pru.Neg(pru.SEq(pru.SString('d2'), specs.make_argls_sexp([SymbArgChar('_split_d2_lhs'), " ", SymbArgChar('_split_d2_rhs')]))),
                                                                   pru.SEq(pru.SString('d3'), specs.make_argls_sexp([SymbArgChar('_split_d3_lhs'), " ", SymbArgChar('_split_d3_rhs')]))])
            elif a == [['rm'], [SymbArgChar('_split_d2_lhs')], [SymbArgChar('_split_d2_rhs'), SymbArgChar('_split_d3_lhs')], [SymbArgChar('_split_d3_rhs')]]:
                assert simplify(variant.constraint) == pru.AndExp([pru.SEq(pru.SString('d2'), specs.make_argls_sexp([SymbArgChar('_split_d2_lhs'), " ", SymbArgChar('_split_d2_rhs')])),
                                                                   pru.SEq(pru.SString('d3'), specs.make_argls_sexp([SymbArgChar('_split_d3_lhs'), " ", SymbArgChar('_split_d3_rhs')]))])
            else:
                assert False, f"Should be impossible: Unexpected variant args: {variant.args}"
    
    def test_constant_strings_around_vars(self):
        """Test vars with constant strings: `rm foo$d2bar`"""
        # Convert from abbreviated form ['r', 'm'], ['f', 'o', 'o', SymbArgChar('d2'), 'b', 'a', 'r']
        args = encode_args([['r', 'm'], ['f', 'o', 'o', SymbArgChar('d2'), 'b', 'a', 'r']])
        ifs = " "
        
        result = nexpand.make_symb_args_variants(args, ifs)
        
        # Should generate two variants: one where d2 is not split, one where it is
        assert len(result) == 2
        
        # First variant: d2 is not split, constants carried along
        no_split_variant = result[0]
        assert no_split_variant.args == [['rm'], ['foo', SymbArgChar('d2'), 'bar']]
        
        # Second variant: d2 is split, constants distributed properly
        split_variant = result[1]
        assert simplify(split_variant.args) == [['rm'], ['foo', SymbArgChar('_split_d2_lhs')], [SymbArgChar('_split_d2_rhs'), 'bar']]

    def test_different_ifs_characters(self):
        """Test with different IFS characters"""
        args = encode_args([['e', 'c', 'h', 'o'], 
                            [SymbArgChar('d2')]
        ])
        ifs = "\t"  # tab as IFS
        
        result = nexpand.make_symb_args_variants(args, ifs)
        
        # Should still generate split variants but with tab as separator
        assert len(result) == 2
        
        # Verify the variants
        variant_args = [simplify(variant.args) for variant in result]
        assert [['echo'], [SymbArgChar('d2')]] in variant_args
        assert [['echo'], [SymbArgChar('_split_d2_lhs')], [SymbArgChar('_split_d2_rhs')]] in variant_args
        expected_c = pru.SEq(pru.SString('d2'), specs.make_argls_sexp([SymbArgChar('_split_d2_lhs'), "\t", SymbArgChar('_split_d2_rhs')]))
        c = [simplify(e.constraint) for e in result]
        assert c[1] == expected_c
        assert c[0] == pru.Neg(expected_c)

    def test_no_splits_simple_command_quoted_var(self):
        """Test with no splits, just a single variant: `rm -rf "$d2"`"""
        args = encode_args([['r', 'm'], ['-', 'r', 'f'], [QArgChar([SymbArgChar('d2')])]])
        ifs = " "
        
        result = nexpand.make_symb_args_variants(args, ifs)
        
        assert len(result) == 1
        variant = result[0]
        assert variant.args == [['rm'], ['-rf'], [SymbArgChar('d2')]]
        assert variant.constraint == None

class TestExpandSimple:
    """Test the expand_simple function which expands a complete command node."""
    
    def setup_method(self):
        """Set up mock objects for each test."""
        self.mock_nodelist = Mock(spec=nodelist.NodeList)
        self.mock_node1 = Mock(spec=symb_node.SymbNode)
        
        self.mock_nodelist.nodes = [self.mock_node1]
        self.mock_node1.get_varval = lambda var: (symb_node.VarUnknown(), None)
        self.mock_node1.get_varstore = lambda var: (symb_node.default_variables()[var], None)
        self.mock_nodelist.var_tracker = Mock(spec=symb_node.VarTracker)
        self.mock_nodelist.var_tracker.use_var = lambda var: None
        
        # Create a mock CommandNode
        self.mock_command = Mock(spec=CommandNode)
        self.mock_command.redir_list = []
        self.mock_command.assignments = []
    
    def test_expand_simple_basic_command_no_var(self):
        """Test expanding a basic command without special features."""
        self.mock_command.arguments = encode_args([['r', 'm'], ['-', 'r', 'f'], ['A', 'p', 'p']])

        result = expand_simple(self.mock_command, self.mock_nodelist)
        assert len(result) == 1
        assert len(result[0]) == 1
        assert result[0][0][1] == None # constraint
        assert result[0][0][0].arguments[0] == ['rm']
        assert result[0][0][0].arguments[1] == ['-rf']
        assert result[0][0][0].arguments[2] == ['App']

    def test_expand_simple_basic_command_with_var(self):
        """Test expanding a basic command without special features."""
        self.mock_command.arguments = encode_args([['r', 'm'], ['-', 'r', 'f'], [SymbArgChar('d2'), 'A', 'p', 'p']])

        result = expand_simple(self.mock_command, self.mock_nodelist)
        assert len(result) == 1
        assert len(result[0]) == 2

        assert simplify(result[0][0][1]) == pru.Neg(pru.OrExp([pru.SEq(pru.SString('d2'), specs.make_argls_sexp([SymbArgChar('_split_d2_lhs'), " ", SymbArgChar('_split_d2_rhs')])),
                                                               pru.SEq(pru.SString('d2'), specs.make_argls_sexp([SymbArgChar('_split_d2_lhs'), "\t", SymbArgChar('_split_d2_rhs')])),
                                                               pru.SEq(pru.SString('d2'), specs.make_argls_sexp([SymbArgChar('_split_d2_lhs'), "\n", SymbArgChar('_split_d2_rhs')]))])) # constraint
        assert result[0][0][0].arguments[0] == ['rm']
        assert result[0][0][0].arguments[1] == ['-rf']
        assert result[0][0][0].arguments[2] == [SymbArgChar('d2'), 'App']

        assert simplify(result[0][1][1]) == pru.OrExp([pru.SEq(pru.SString('d2'), specs.make_argls_sexp([SymbArgChar('_split_d2_lhs'), " ", SymbArgChar('_split_d2_rhs')])),
                                                       pru.SEq(pru.SString('d2'), specs.make_argls_sexp([SymbArgChar('_split_d2_lhs'), "\t", SymbArgChar('_split_d2_rhs')])),
                                                       pru.SEq(pru.SString('d2'), specs.make_argls_sexp([SymbArgChar('_split_d2_lhs'), "\n", SymbArgChar('_split_d2_rhs')]))])
        assert result[0][1][0].arguments[0] == ['rm']
        assert result[0][1][0].arguments[1] == ['-rf']
        assert simplify(result[0][1][0].arguments[2]) == [SymbArgChar('_split_d2_lhs')]
        assert simplify(result[0][1][0].arguments[3]) == [SymbArgChar('_split_d2_rhs'), 'App']

    def test_expand_simple_basic_command_quoted(self):
        """Test expanding a basic command without special features."""
        self.mock_command.arguments = encode_args([['r', 'm'], ['-', 'r', 'f'], [QArgChar([SymbArgChar('d2'), 'A', 'p', 'p'])]])

        result = expand_simple(self.mock_command, self.mock_nodelist)
        assert len(result) == 1
        assert len(result[0]) == 1

        assert simplify(result[0][0][1]) == None
        assert result[0][0][0].arguments[0] == ['rm']
        assert result[0][0][0].arguments[1] == ['-rf']
        assert result[0][0][0].arguments[2] == [SymbArgChar('d2'), 'App']
    


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
