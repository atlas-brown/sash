"""
Tests for the simple symbolic expander.
"""
import pytest
import shasta.ast_node as AST
from sash.symbolic.strings import ArbitraryType, CompletelyArbitrary, Field, SymStr, WordCount
from util import *

import sash.reporter as reporter
from sash.frozen import freeze, freeze_thing
from sash.interpreter_config import InterpConfig
from sash.symbolic.state import *
from sash.symb import expand_args, expand_args_dumb, expand_simple, starting_state

reporter.Reporter.initialize("<test>")
config = InterpConfig()

def constant_field(string: str, words: int = 1) -> Field:
    return Field(SymStr((string,)), WordCount(words, words))

def glob_field(string: str, min_words: int = 1) -> Field:
    assert '*' in string
    return Field(SymStr((string,)), WordCount(min_words, float('inf')))

def expand_simple_r(stuff: list[AST.ArgChar],
                   state: State,
                   config: InterpConfig) -> list[Field]:
    expansions = expand_simple(stuff, state, config)
    assert len(expansions) == 1
    return expansions[0][0]

def test_expand():
    script = parse_script("""echo hi there""")
    assert len(script) == 1
    assert isinstance(script[0], AST.CommandNode)

    state = starting_state()

    expanded = [expand_simple_r(arg, state, config) for arg in script[0].arguments]
    assert len(expanded) == 3
    assert expanded[0] == [constant_field("echo")]
    assert expanded[1] == [constant_field("hi")]
    assert expanded[2] == [constant_field("there")]

def test_expand_quotes():
    script = parse_script("""echo "hi there" 'and here'""")
    assert len(script) == 1
    assert isinstance(script[0], AST.CommandNode)

    state = starting_state()

    expanded = [expand_simple_r(arg, state, config) for arg in script[0].arguments]
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

    expanded = expand_simple_r(script[0].arguments[0], state, config)
    assert expanded == [constant_field("hi")]

def test_expand_vars():
    script = parse_script("""echo $A "$B" '$C'""")
    assert len(script) == 1
    assert isinstance(script[0], AST.CommandNode)

    state = starting_state()\
        .set_env("A", ShellVar(constant_field("hi")))\
        .set_env("B", ShellVar(constant_field("there")))\
        .set_env("C", ShellVar(constant_field("and here")))

    expanded = [expand_simple_r(arg, state, config) for arg in script[0].arguments]
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
    expanded = expand_simple_r(script[0].arguments[1], state, config)
    assert expanded == [constant_field("b")]

def test_expand_vars_split():
    script = parse_script("""echo $A "$B" '$C'""")
    assert len(script) == 1
    assert isinstance(script[0], AST.CommandNode)

    state = starting_state()\
        .set_env("A", ShellVar(constant_field("hi hello", 2)))\
        .set_env("B", ShellVar(constant_field("there there")))\
        .set_env("C", ShellVar(constant_field("and here")))

    expanded = [expand_simple_r(arg, state, config) for arg in script[0].arguments]
    assert len(expanded) == 4
    assert expanded[0] == [constant_field("echo")]
    assert expanded[1] == [constant_field("hi hello", 2)]
    assert expanded[2] == [constant_field("there there", 1)]
    # Note: '$C' is single-quoted, so it should not be expanded
    # and should remain as literal '$C'
    assert expanded[3] == [constant_field("$C")]

def test_expand_vars_split_general_case():
    script = parse_script("""echo $A""")
    assert len(script) == 1
    assert isinstance(script[0], AST.CommandNode)

    state = starting_state()\
        .set_env("A", ShellVar(constant_field("a b", 2)))

    expanded = [expand_simple_r(arg, state, config) for arg in script[0].arguments]
    assert len(expanded) == 2
    assert expanded[0] == [constant_field("echo")]
    assert expanded[1] == [constant_field("a"), constant_field("b")]

def test_expand_vars_joined():
    script = parse_script("""echo $A$A before"$B" $B$B ${A}after before$A "$A"after""")
    assert len(script) == 1
    assert isinstance(script[0], AST.CommandNode)

    state = starting_state()\
        .set_env("A", ShellVar(constant_field("hi hello", 2)))\
        .set_env("B", ShellVar(constant_field("there")))

    expanded = [expand_simple_r(arg, state, config) for arg in script[0].arguments]
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

    expanded = [expand_simple_r(arg, state, config) for arg in script[0].arguments]
    assert len(expanded) == 2
    assert expanded[0] == [constant_field("echo")]
    assert expanded[1] == [Field(CompletelyArbitrary(freeze(script[0].arguments[1][0]),
                                                     ArbitraryType.ENVIRONMENT,
                                                     state),
                                 WordCount(0, float('inf')))]

# Notes about empty strings:
# mkdir "" # -> mkdir throws error 'no such file or directory'
# mkdir # -> mkdir shows usage message
#
# A= # A="" is equivalent
# mkdir "$A" # -> mkdir throws error 'no such file or directory'
# mkdir $A # -> mkdir shows usage message
#
# mkdir "$(:)" # -> mkdir throws error 'no such file or directory'
# mkdir $(:) # -> mkdir shows usage message
#
# A quoted empty string expands to a word of length zero (one word)
# An unquoted empty string disappears entirely (zero words)

def test_expand_empty_string():
    script = parse_script('''echo ""''')
    assert len(script) == 1
    assert isinstance(script[0], AST.CommandNode)

    state = starting_state()

    expanded = [expand_simple_r(arg, state, config) for arg in script[0].arguments]
    assert len(expanded) == 2
    assert expanded[0] == [constant_field("echo")]
    assert expanded[1] == [constant_field("", 1)]

def test_expand_quoted_empty_var():
    script = parse_script('''echo "$A"''')
    assert len(script) == 1
    assert isinstance(script[0], AST.CommandNode)

    state = starting_state()
    state = state.set_env("A", ShellVar(constant_field("", 1)))

    expanded = [expand_simple_r(arg, state, config) for arg in script[0].arguments]
    assert len(expanded) == 2
    assert expanded[0] == [constant_field("echo")]
    assert expanded[1] == [constant_field("", 1)]

def test_expand_unquoted_empty_var():
    script = parse_script('''echo $A''')
    assert len(script) == 1
    assert isinstance(script[0], AST.CommandNode)

    state = starting_state()
    state = state.set_env("A", ShellVar(constant_field("", 1)))

    expanded = [expand_simple_r(arg, state, config) for arg in script[0].arguments]
    assert len(expanded) == 1
    assert expanded[0] == [constant_field("echo")]

def test_expand_undefined_var_default():
    script = parse_script("""${A:-default}""")
    assert len(script) == 1
    assert isinstance(script[0], AST.CommandNode)

    state = starting_state()

    expanded = expand_simple(script[0].arguments[0], state, config)
    assert len(expanded) == 2
    assert all(len(expansion[1].pathcond) == 1 for expansion in expanded)
    assert expanded[0][0] == [Field(CompletelyArbitrary(freeze(script[0].arguments[0][0]),
                                                  ArbitraryType.ENVIRONMENT,
                                                  state),
                              WordCount(0, float('inf')))]
    assert expanded[1][0] == [constant_field("default")]


def test_expand_question_var():
    script = parse_script("""${A:?error message}""")
    assert len(script) == 1
    assert isinstance(script[0], AST.CommandNode)

    state = starting_state()

    expanded = expand_simple(script[0].arguments[0], state, config)
    assert len(expanded) == 1
    assert expanded[0][0] == [Field(CompletelyArbitrary(freeze(script[0].arguments[0][0]),
                                                  ArbitraryType.ENVIRONMENT,
                                                  state),
                              WordCount(1, float('inf')))]


def test_expand_question_var_bound():
    script = parse_script("""${A:?error message}""")
    assert len(script) == 1
    assert isinstance(script[0], AST.CommandNode)

    state = starting_state()

    state = state.set_env("A", ShellVar(constant_field("value")))
    expanded = expand_simple_r(script[0].arguments[0], state, config)
    assert expanded == [constant_field("value")] # ':?' should be completely irrelevant here


def test_expand_question_var_bound_unknown():
    script = parse_script("""${A:?error message}""")
    assert len(script) == 1
    assert isinstance(script[0], AST.CommandNode)

    state = starting_state()

    state = state.set_env("A", ShellVar(Field(CompletelyArbitrary(freeze(script[0].arguments[0][0]),
                                                                 ArbitraryType.ENVIRONMENT,
                                                                 state),
                                              WordCount(0, float('inf')))))
    expanded = expand_simple(script[0].arguments[0], state, config)
    assert len(expanded) == 1
    assert expanded[0][0] == [Field(CompletelyArbitrary(freeze(script[0].arguments[0][0]),
                                                      ArbitraryType.ENVIRONMENT,
                                                      state),
                                  WordCount(1, float('inf')))]


def test_expand_question_var_empty():
    script = parse_script("""${A:?error message}""")
    assert len(script) == 1
    assert isinstance(script[0], AST.CommandNode)

    reporter.Reporter.reset()
    reporter.Reporter.initialize("<test>")
    state = starting_state()

    state = state.set_env("A", ShellVar(constant_field("", 1)))
    expanded = expand_simple_r(script[0].arguments[0], state, config)
    # If the variable is empty, expansion terminates and yields no fields
    assert expanded == []
    report = reporter.Reporter.get_report()
    assert len(report.issues) == 0


def test_expand_cmdsubst():
    script = parse_script("""echo $(foo bar)""")
    assert len(script) == 1
    assert isinstance(script[0], AST.CommandNode)

    state = starting_state()

    expanded = [expand_simple_r(arg, state, config) for arg in script[0].arguments]
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
    expanded = expand_simple_r(script[0].arguments[2], state, config)
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
    expanded = expand_simple_r(script[0].arguments[2], state, config)
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
    assert expanded[0][0] == [Field(CompletelyArbitrary(freeze_thing([script[0].arguments[2][1], script[0].arguments[2][3]]),
                                                  ArbitraryType.APPROXIMATION,
                                                  expanded[0][1],
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

def test_expand_args_dumb_multipath_bound():
    script = parse_script("""
rm -rf $A/*
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

    match expanded[2]:
        case Field(CompletelyArbitrary(source, kind, None, None, None), wc):
            assert source == freeze_thing(script[0].arguments[2])
            assert kind == ArbitraryType.APPROXIMATION
            assert wc == WordCount(0, float('inf'))
        case _:
            assert expanded[2] == False, f"Unexpected expansion result"
    # Want to write this, but CompletelyArbitrary equality with None producing_state is always false!
    # assert expanded[2] == Field(CompletelyArbitrary(freeze_thing(script[0].arguments[2]),
    #                                                 ArbitraryType.APPROXIMATION,
    #                                                 None,
    #                                                 # suffix=SymStr(("/*",)) # TODO: should preserve the common suffix!
    #                                                 ),
    #                              WordCount(0, float('inf')))

def test_expand_args_multipath():
    script = parse_script("""
rm -rf $A/*
""")
    assert len(script) == 1
    assert isinstance(script[0], AST.CommandNode)

    state = starting_state()
    state1 = state.set_env("A", ShellVar(constant_field("value1")))
    state2 = state.set_env("A", ShellVar(constant_field("value2")))
    expansions = expand_args([Trace((state1,)), Trace((state2,))],
                             script[0].arguments,
                             config)

    assert len(expansions) == 2
    v1_expansions = expansions[0][1]
    v2_expansions = expansions[1][1]
    assert len(v1_expansions) == 3
    assert v1_expansions[0] == constant_field("rm")
    assert v1_expansions[1] == constant_field("-rf")
    assert v1_expansions[2] == glob_field("value1/*")
    assert len(v2_expansions) == 3
    assert v2_expansions[0] == constant_field("rm")
    assert v2_expansions[1] == constant_field("-rf")
    assert v2_expansions[2] == glob_field("value2/*")


def test_expand_glob_var():
    script = parse_script("""echo $A*""")
    assert len(script) == 1
    assert isinstance(script[0], AST.CommandNode)

    state = starting_state()
    state = state.set_env("A", ShellVar(constant_field("hi")))
    expanded = expand_simple_r(script[0].arguments[1], state, config)
    assert expanded == [glob_field("hi*")]


def test_expand_undefined_glob_var():
    script = parse_script("""echo $A*""")
    assert len(script) == 1
    assert isinstance(script[0], AST.CommandNode)

    state = starting_state()

    expanded = expand_simple_r(script[0].arguments[1], state, config)
    assert expanded == [Field(CompletelyArbitrary(freeze(script[0].arguments[1][0]),
                                                 ArbitraryType.ENVIRONMENT,
                                                 state,
                                                 suffix=SymStr(("*",))),
                             WordCount(0, float('inf')))]


def test_expand_quoted_glob_var():
    script = parse_script("""echo "$A*" """)
    assert len(script) == 1
    assert isinstance(script[0], AST.CommandNode)

    state = starting_state()
    state = state.set_env("A", ShellVar(constant_field("hi")))
    expanded = expand_simple_r(script[0].arguments[1], state, config)
    assert expanded == [constant_field("hi*")]


def test_expand_undefined_quoted_glob_var():
    script = parse_script("""echo "$A*" """)
    assert len(script) == 1
    assert isinstance(script[0], AST.CommandNode)

    state = starting_state()

    expanded = expand_simple_r(script[0].arguments[1], state, config)
    # left side is a VArgChar, right side is a QArgChar (thus the .arg[0] indexing)
    assert expanded == [Field(CompletelyArbitrary(freeze(script[0].arguments[1][0].arg[0]), # type: ignore
                                                 ArbitraryType.ENVIRONMENT,
                                                 state,
                                                 suffix=SymStr(("*",)),
                                                 quoted=True,
                                                 maybe_empty=True),
                             WordCount(1, 1))]


# TODO: we need to keep track of the decisions about whether an unbound var is set or not (e.g. the below should only have 2 expansions, not 4)
# This will allow us to trim paths and avoid false positives
@pytest.mark.skip(reason="Not currently tracking decisions about default values")
def test_expand_undefined_fork_state_tracking():
    #script = parse_script("""${1:-default}${2:-default2}${1:-default3}""")
    script = parse_script("""${1:-default}${1:-default2}""")
    assert len(script) == 1
    assert isinstance(script[0], AST.CommandNode)

    state = starting_state()

    expanded = expand_simple(script[0].arguments[0], state, config)
    assert len(expanded) == 2
