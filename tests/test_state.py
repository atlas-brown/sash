"""
Tests for state utilities.
"""
from sash.symbolic.strings import ArbitraryType, CompletelyArbitrary, Field, SymStr, WordCount
from util import *
from dataclasses import replace

import sash.reporter as reporter
from sash.symbolic.state import (
    ShellVar,
    Trace,
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
    # If the path conditions differ, the states should be distinct.
    assert len(collapse_traces([
        Trace((starting_state().set_env("foo", ShellVar(Field(SymStr(("hi",)), WordCount(1, 1))))
               .add_pathcond("cond_L5:true"),)),
        Trace((starting_state().set_env("foo", ShellVar(Field(SymStr(("hi",)), WordCount(1, 1))))
               .add_pathcond("cond_L5:false"),))
    ])) == 2
    # If they do have the same path conditions, they should collapse to one.
    assert len(collapse_traces([
        Trace((starting_state().set_env("foo", ShellVar(Field(SymStr(("hello",)), WordCount(1, 1))))
               .add_pathcond("cond_L5:true"),)),
        Trace((starting_state().set_env("foo", ShellVar(Field(SymStr(("hello",)), WordCount(1, 1))))
               .add_pathcond("cond_L5:true"),))
    ])) == 1

def test_quote():
    assert Field(SymStr(("why hello there",)), WordCount(3, 3)).quote() == Field(SymStr(("why hello there",)), WordCount(1, 1))
    assert Field(SymStr(("singleword",)), WordCount(1, 1)).quote() == Field(SymStr(("singleword",)), WordCount(1, 1))
    arb = CompletelyArbitrary(None, ArbitraryType.APPROXIMATION, None)
    quoted = Field(arb, WordCount(2, 5)).quote()
    assert quoted.count == WordCount(1, 1)
    assert isinstance(quoted.content, CompletelyArbitrary)
    assert quoted.content.quoted is True
    quoted = Field(arb, WordCount(1, 1)).quote()
    assert quoted.count == WordCount(1, 1)
    assert quoted.content.quoted is True
    quoted = Field(arb, WordCount(0, 5)).quote()
    assert quoted.count == WordCount(0, 1)
    assert quoted.content.quoted is True
    quoted = Field(arb, WordCount(0, float('inf'))).quote()
    assert quoted.count == WordCount(0, 1)
    assert quoted.content.quoted is True
    quoted = Field(arb, WordCount(0, 0)).quote()
    assert quoted.count == WordCount(0, 0)
    assert quoted.content.quoted is True



def test_field_normalization():
    path1 = create_field("/a/b/c/")
    path2 = create_field("/a/b/c")

    assert path1.try_without_trailing_slash() == path2.try_without_trailing_slash()

def test_field_normalization_with_glob():
    path1 = create_field("/a/b*/c/")
    path2 = create_field("/a/b*/c")

    assert path1.try_without_trailing_slash() == path2.try_without_trailing_slash()

def test_field_normalization_with_spaces():
    path1 = create_field("   /a/  b/ c/   ")
    path2 = create_field("   /a/  b/ c")

    assert path1.try_without_trailing_slash() != path2.try_without_trailing_slash()
