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

