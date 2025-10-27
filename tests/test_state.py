"""
Tests for state utilities.
"""
import sash
import sash.reporter as reporter
from sash.symb import expand_simple, expand_args_dumb, starting_state
from sash.state import *
import shasta.ast_node as AST
from util import *

def test_collapse_traces():
    assert len(collapse_traces([Trace([starting_state()])])) == 1
    assert len(collapse_traces([Trace([starting_state()]),
                                Trace([starting_state()])])) == 1
    assert len(collapse_traces([Trace([starting_state()]),
                                Trace([starting_state()]),
                                Trace([starting_state()])])) == 1

    assert len(collapse_traces([Trace((starting_state().set_env("foo", ShellVar(Field(SymStr(("hi",)), WordCount(1, 1))))\
                                       .add_pathcond("cond_L5:true"),)),
                                Trace((starting_state().set_env("foo", ShellVar(Field(SymStr(("hi",)), WordCount(1, 1))))\
                                       .add_pathcond("cond_L5:false"),))])) == 1

def test_quote():
    assert Field(SymStr(("why hello there",)), WordCount(3, 3)).quote() == Field(SymStr(("why hello there",)), WordCount(1, 1))
    assert Field(SymStr(("singleword",)), WordCount(1, 1)).quote() == Field(SymStr(("singleword",)), WordCount(1, 1))
    arb = CompletelyArbitrary(None, ArbitraryType.APPROXIMATION, None)
    assert Field(arb, WordCount(2, 5)).quote() == Field(arb, WordCount(1, 1))
    assert Field(arb, WordCount(1, 1)).quote() == Field(arb, WordCount(1, 1))
    assert Field(arb, WordCount(0, 5)).quote() == Field(arb, WordCount(0, 1))
    assert Field(arb, WordCount(0, float('inf'))).quote() == Field(arb, WordCount(0, 1))
    assert Field(arb, WordCount(0, 0)).quote() == Field(arb, WordCount(0, 0))