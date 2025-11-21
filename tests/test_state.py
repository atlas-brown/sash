"""
Tests for state utilities.
"""
from util import *

import sash.reporter as reporter
from sash.state import (
    ArbitraryType,
    CompletelyArbitrary,
    Field,
    ShellVar,
    SymStr,
    Trace,
    WordCount,
)
from sash.symb import collapse_traces, starting_state

reporter.Reporter.initialize("<test>")

def test_collapse_traces():
    assert len(collapse_traces([Trace((starting_state(),))])) == 1
    assert len(collapse_traces([Trace((starting_state(),)),
                                Trace((starting_state(),))])) == 1
    assert len(collapse_traces([Trace((starting_state(),)),
                                Trace((starting_state(),)),
                                Trace((starting_state(),))])) == 1

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



def test_field_normalization():
    path1 = create_field("/a/b/c/")
    path2 = create_field("/a/b/c")

    assert path1.without_trailing_slash() == path2.without_trailing_slash()

def test_field_normalization_with_glob():
    path1 = create_field("/a/b*/c/")
    path2 = create_field("/a/b*/c")

    assert path1.without_trailing_slash() == path2.without_trailing_slash()

def test_field_normalization_with_spaces():
    path1 = create_field("   /a/  b/ c/   ")
    path2 = create_field("   /a/  b/ c")

    assert path1.without_trailing_slash() != path2.without_trailing_slash()
