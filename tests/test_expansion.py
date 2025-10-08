"""
Tests for the simple symbolic expander.
"""
import sash
import sash.reporter as reporter
from sash.symb import expand_simple, expand_args_dumb, starting_state
from sash.state import *
import shasta.ast_node as AST
from util import *
from unittest.mock import Mock, MagicMock

def constant_field(string: str, words: int = 1) -> Field:
    return Field(SymStr([string]), WordCount(words, words))

def test_expand():
    script = parse_script("""echo hi there""")
    assert len(script) == 1
    assert isinstance(script[0], AST.CommandNode)
    # Using pytest's monkeypatch or unittest.mock for mocking

    state = starting_state()

    expanded = [expand_simple(arg, state) for arg in script[0].arguments]
    assert len(expanded) == 3
    assert expanded[0] == [constant_field("echo")]
    assert expanded[1] == [constant_field("hi")]
    assert expanded[2] == [constant_field("there")]

def test_expand_quotes():
    script = parse_script("""echo "hi there" 'and here'""")
    assert len(script) == 1
    assert isinstance(script[0], AST.CommandNode)

    state = starting_state()

    expanded = [expand_simple(arg, state) for arg in script[0].arguments]
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

    expanded = expand_simple(script[0].arguments[0], state)
    assert expanded == [constant_field("hi")]

def test_expand_vars():
    script = parse_script("""echo $A "$B" '$C'""")
    assert len(script) == 1
    assert isinstance(script[0], AST.CommandNode)

    state = starting_state()\
        .set_env("A", ShellVar(constant_field("hi")))\
        .set_env("B", ShellVar(constant_field("there")))\
        .set_env("C", ShellVar(constant_field("and here")))

    expanded = [expand_simple(arg, state) for arg in script[0].arguments]
    assert len(expanded) == 4
    assert expanded[0] == [constant_field("echo")]
    assert expanded[1] == [constant_field("hi")]
    assert expanded[2] == [constant_field("there")]
    # Note: '$C' is single-quoted, so it should not be expanded
    # and should remain as literal '$C'
    assert expanded[3] == [constant_field("$C")]

def test_expand_vars_split():
    script = parse_script("""echo $A "$B" '$C'""")
    assert len(script) == 1
    assert isinstance(script[0], AST.CommandNode)

    state = starting_state()\
        .set_env("A", ShellVar(constant_field("hi hello", 2)))\
        .set_env("B", ShellVar(constant_field("there there")))\
        .set_env("C", ShellVar(constant_field("and here")))

    expanded = [expand_simple(arg, state) for arg in script[0].arguments]
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

    expanded = [expand_simple(arg, state) for arg in script[0].arguments]
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

    expanded = [expand_simple(arg, state) for arg in script[0].arguments]
    assert len(expanded) == 2
    assert expanded[0] == [constant_field("echo")]
    assert expanded[1] == [Field(CompletelyArbitrary(script[0].arguments[1][0],
                                                     ArbitraryType.ENVIRONMENT,
                                                     state),
                                 WordCount(0, float('inf')))]


def test_expand_cmdsubst():
    script = parse_script("""echo $(foo bar)""")
    assert len(script) == 1
    assert isinstance(script[0], AST.CommandNode)

    state = starting_state()

    expanded = [expand_simple(arg, state) for arg in script[0].arguments]
    assert len(expanded) == 2
    assert expanded[0] == [constant_field("echo")]
    assert expanded[1] == [Field(CompletelyArbitrary(script[0].arguments[1][0],
                                                     ArbitraryType.APPROXIMATION,
                                                     state),
                                 WordCount(0, float('inf')))]

