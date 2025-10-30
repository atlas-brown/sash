"""
Tests for the simple symbolic expander.
"""
import pytest
import sash
import sash.reporter as reporter
from sash.symb import expand_simple, expand_args_dumb, starting_state
from sash.state import *
from sash.frozen import freeze, freeze_thing
from sash.interpreter_config import InterpConfig
import shasta.ast_node as AST
from util import *
from unittest.mock import Mock, MagicMock

config = InterpConfig()

def constant_field(string: str, words: int = 1) -> Field:
    return Field(SymStr((string,)), WordCount(words, words))

def test_expand():
    script = parse_script("""echo hi there""")
    assert len(script) == 1
    assert isinstance(script[0], AST.CommandNode)
    # Using pytest's monkeypatch or unittest.mock for mocking

    state = starting_state()

    expanded = [expand_simple(arg, state, config) for arg in script[0].arguments]
    assert len(expanded) == 3
    assert expanded[0] == [constant_field("echo")]
    assert expanded[1] == [constant_field("hi")]
    assert expanded[2] == [constant_field("there")]

def test_expand_quotes():
    script = parse_script("""echo "hi there" 'and here'""")
    assert len(script) == 1
    assert isinstance(script[0], AST.CommandNode)

    state = starting_state()

    expanded = [expand_simple(arg, state, config) for arg in script[0].arguments]
    assert len(expanded) == 3
    assert expanded[0] == [constant_field("echo")]
    assert expanded[1] == [constant_field("hi there")]
    assert expanded[2] == [constant_field("and here")]

def test_expand_one_var():
    script = parse_script("""$A""")
    assert len(script) == 1
    assert isinstance(script[0], AST.CommandNode)

    state = starting_state()\
        .set_env("A", ShellVar(constant_field("hi")))

    expanded = expand_simple(script[0].arguments[0], state, config)
    assert expanded == [constant_field("hi")]

def test_expand_vars():
    script = parse_script("""echo $A "$B" '$C'""")
    assert len(script) == 1
    assert isinstance(script[0], AST.CommandNode)

    state = starting_state()\
        .set_env("A", ShellVar(constant_field("hi")))\
        .set_env("B", ShellVar(constant_field("there")))\
        .set_env("C", ShellVar(constant_field("and here")))

    expanded = [expand_simple(arg, state, config) for arg in script[0].arguments]
    assert len(expanded) == 4
    assert expanded[0] == [constant_field("echo")]
    assert expanded[1] == [constant_field("hi")]
    assert expanded[2] == [constant_field("there")]
    # Note: '$C' is single-quoted, so it should not be expanded
    # and should remain as literal '$C'
    assert expanded[3] == [constant_field("$C")]

def test_expand_localvars():
    script = parse_script("""echo $2""")
    assert len(script) == 1
    assert isinstance(script[0], AST.CommandNode)

    state = starting_state()\
        .extend_localenv({"1": ShellVar(constant_field("a")),
                          "2": ShellVar(constant_field("b"))})
    expanded = expand_simple(script[0].arguments[1], state, config)
    assert expanded == [constant_field("b")]

def test_expand_vars_split():
    script = parse_script("""echo $A "$B" '$C'""")
    assert len(script) == 1
    assert isinstance(script[0], AST.CommandNode)

    state = starting_state()\
        .set_env("A", ShellVar(constant_field("hi hello", 2)))\
        .set_env("B", ShellVar(constant_field("there there")))\
        .set_env("C", ShellVar(constant_field("and here")))

    expanded = [expand_simple(arg, state, config) for arg in script[0].arguments]
    assert len(expanded) == 4
    assert expanded[0] == [constant_field("echo")]
    assert expanded[1] == [constant_field("hi hello", 2)]
    assert expanded[2] == [constant_field("there there", 1)]
    # Note: '$C' is single-quoted, so it should not be expanded
    # and should remain as literal '$C'
    assert expanded[3] == [constant_field("$C")]

def test_expand_vars_joined():
    script = parse_script("""echo $A$A before"$B" $B$B ${A}after before$A "$A"after""")
    assert len(script) == 1
    assert isinstance(script[0], AST.CommandNode)

    state = starting_state()\
        .set_env("A", ShellVar(constant_field("hi hello", 2)))\
        .set_env("B", ShellVar(constant_field("there")))

    expanded = [expand_simple(arg, state, config) for arg in script[0].arguments]
    assert len(expanded) == 7
    assert expanded[0] == [constant_field("echo")]
    assert expanded[1] == [constant_field("hi hellohi hello", 3)]
    assert expanded[2] == [constant_field("beforethere", 1)]
    assert expanded[3] == [constant_field("therethere", 1)]
    assert expanded[4] == [constant_field("hi helloafter", 2)]
    assert expanded[5] == [constant_field("beforehi hello", 2)]
    assert expanded[6] == [constant_field("hi helloafter", 1)]

def test_expand_undefined_var():
    script = parse_script("""echo $A""")
    assert len(script) == 1
    assert isinstance(script[0], AST.CommandNode)

    state = starting_state()

    expanded = [expand_simple(arg, state, config) for arg in script[0].arguments]
    assert len(expanded) == 2
    assert expanded[0] == [constant_field("echo")]
    assert expanded[1] == [Field(CompletelyArbitrary(freeze(script[0].arguments[1][0]),
                                                     ArbitraryType.ENVIRONMENT,
                                                     state),
                                 WordCount(0, float('inf')))]


def test_expand_cmdsubst():
    script = parse_script("""echo $(foo bar)""")
    assert len(script) == 1
    assert isinstance(script[0], AST.CommandNode)

    state = starting_state()

    expanded = [expand_simple(arg, state, config) for arg in script[0].arguments]
    assert len(expanded) == 2
    assert expanded[0] == [constant_field("echo")]
    assert expanded[1] == [Field(CompletelyArbitrary(freeze(script[0].arguments[1][0]),
                                                     ArbitraryType.APPROXIMATION,
                                                     state),
                                 WordCount(0, float('inf')))]

def test_expand_d2concat():
    script = parse_script("""rm -rf ${2}Applications/iTunes.app 2> /dev/null""")
    assert len(script) == 1
    assert isinstance(script[0], AST.CommandNode)

    state = starting_state()
    expanded = expand_simple(script[0].arguments[2], state, config)
    assert expanded == [Field(CompletelyArbitrary(freeze(script[0].arguments[2][0]),
                                                  ArbitraryType.APPROXIMATION,
                                                  state,
                                                  suffix=SymStr(("Applications/iTunes.app",))),
                              WordCount(0, float('inf')))]

def test_expand_pre_and_suffix():
    script = parse_script("""rm -rf b${2}a""")
    assert len(script) == 1
    assert isinstance(script[0], AST.CommandNode)

    state = starting_state()
    expanded = expand_simple(script[0].arguments[2], state, config)
    assert expanded == [Field(CompletelyArbitrary(freeze(script[0].arguments[2][1]),
                                                  ArbitraryType.APPROXIMATION,
                                                  state,
                                                  prefix=SymStr(("b",)),
                                                  suffix=SymStr(("a",))),
                              WordCount(0, float('inf')))]

    script = parse_script("""rm -rf b${2}m${3}a""")
    assert len(script) == 1
    assert isinstance(script[0], AST.CommandNode)

    state = starting_state()
    expanded = expand_simple(script[0].arguments[2], state, config)
    assert expanded == [Field(CompletelyArbitrary(freeze_thing([script[0].arguments[2][1], script[0].arguments[2][3]]),
                                                  ArbitraryType.APPROXIMATION,
                                                  state,
                                                  prefix=SymStr(("b",)),
                                                  suffix=SymStr(("a",))),
                              WordCount(0, float('inf')))]


def test_expand_args_dumb():
    script = parse_script("""rm -rf ${2}Applications/iTunes.app 2> /dev/null""")
    assert len(script) == 1
    assert isinstance(script[0], AST.CommandNode)

    state = starting_state()

    _, expanded = expand_args_dumb([Trace((state,))], script[0].arguments, config)
    assert len(expanded) == 3
    assert expanded[0] == constant_field("rm")
    assert expanded[1] == constant_field("-rf")
    assert expanded[2] == Field(CompletelyArbitrary(freeze(script[0].arguments[2][0]),
                                                    ArbitraryType.APPROXIMATION,
                                                    state,
                                                    suffix=SymStr(("Applications/iTunes.app",))),
                                 WordCount(0, float('inf')))

def test_expand_args_dumb_multipath():
    script = parse_script("""
rm -rf $UNBOUND/*
""")
    assert len(script) == 1
    assert isinstance(script[0], AST.CommandNode)

    state = starting_state()
    state1 = state.set_env("A", ShellVar(constant_field("value1")))
    state2 = state.set_env("A", ShellVar(constant_field("value2")))
    traces, expanded = expand_args_dumb([Trace((state1,)), Trace((state2,))],
                                         script[0].arguments,
                                         config)
    assert len(traces) == 2
    assert len(expanded) == 3
    assert expanded[0] == constant_field("rm")
    assert expanded[1] == constant_field("-rf")
    assert expanded[2] == Field(CompletelyArbitrary(freeze(script[0].arguments[2][0]),
                                                    ArbitraryType.ENVIRONMENT,
                                                    traces[0].latest_state,
                                                    suffix=SymStr(("/*",))),
                                 WordCount(0, float('inf')))
