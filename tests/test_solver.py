"""
Tests for SMT solver integration.
"""
import z3
from sash.fs import FSModel, FSModelSimple
from sash.symbolic.strings import ArbitraryType, CompletelyArbitrary, Field, WordCount
from util import *

import sash.reporter as reporter
from sash.constraints import IsDeleted, IsFile, IsRead, StringEq
from sash.solver import (
    field_content_to_z3,
    reset_z3cache,
    state_to_z3,
)
from sash.symbolic.state import ShellVar
from sash.symb import starting_state
from sash.fs import FileInfo, File, Dir, Del, Read, Unread

reporter.Reporter.initialize("<test>")

def z3var(name):
    return z3.String(name)

def z3_fs_var(id):
    return z3.Array(f'fs{id}', z3.StringSort(), FileInfo)

def assert_equiv_formulas(f1, f2):
    s = z3.Solver()
    res = s.check(z3.Not(f1 == f2))
    assert res == z3.unsat, f"Formulas are not equivalent:\nf1: {f1}\nf2: {f2}\nModel: {s.model()}"

def make_env_constraints_z3(s, env_constraints):
    default_env_constraints = {
        name: field_content_to_z3(shellvar.value.content)
        for name, shellvar in s.env.items()
        if name in {"HOME", "PWD", "OLDPWD", "PATH", "PWD_INIT"}
    }
    parts = [z3var(name) == z3expr for name, z3expr in (env_constraints | default_env_constraints).items()]
    return z3.And(*parts)

def assert_equiv_fs_states(f1, f2, f1_starting_fs_id, f2_starting_fs_id):
    s = z3.Solver()
    s.add(f1)
    s.add(f2)
    s.add(f1_starting_fs_id == f2_starting_fs_id)
    res = s.check(z3.Not(f1 == f2))
    assert res == z3.unsat, f"FS states are not equivalent:\nf1: {f1}\nf2: {f2}\nModel: {s.model()}"

def test_state_to_z3():
    reset_z3cache()

    state = starting_state(FSModel())
    arb = Field(CompletelyArbitrary(None, ArbitraryType.ENVIRONMENT, state), WordCount(0, float('inf')))
    s = state.set_env("A", ShellVar(Field.create_constant("value1")))\
        .set_env("B", ShellVar(arb))\
        .add_pathcond(StringEq(arb, Field.create_constant("")))

    arbz3var = field_content_to_z3(arb.content)

    fs_formula = True

    pathcond_formula = arbz3var == ""

    env_formula = make_env_constraints_z3(s,
                                          {"A": z3.StringVal("value1"),
                                           "B": arbz3var})

    formula = state_to_z3(s)
    assert_equiv_formulas(formula,
                          z3.And(fs_formula, pathcond_formula, env_formula))

def test_state_to_z3_more_stuff():
    reset_z3cache()

    state = starting_state(FSModel())
    arb = Field(CompletelyArbitrary(None, ArbitraryType.ENVIRONMENT, state), WordCount(0, float('inf')))
    arb2 = Field(CompletelyArbitrary(None, ArbitraryType.ENVIRONMENT, state), WordCount(0, float('inf')))
    s = state.set_env("A", ShellVar(Field.create_constant("value1")))\
        .set_env("B", ShellVar(arb))\
        .set_env("1", ShellVar(arb2))\
        .add_pathcond(StringEq(arb, Field.create_constant("")))\
        .add_pathcond(StringEq(arb2, arb))

    arbz3var = field_content_to_z3(arb.content)
    arb2z3var = field_content_to_z3(arb2.content)

    fs_formula = True

    pathcond_formula = z3.And(arbz3var == "", arb2z3var == arbz3var)
    env_formula = make_env_constraints_z3(s,
                                          {"A": z3.StringVal("value1"),
                                           "B": arbz3var,
                                           "1": arb2z3var})

    formula = state_to_z3(s)
    assert_equiv_formulas(formula,
                          z3.And(fs_formula, pathcond_formula, env_formula))

def test_state_to_z3_local_vars():
    reset_z3cache()

    state = starting_state(FSModel())
    arb = Field(CompletelyArbitrary(None, ArbitraryType.ENVIRONMENT, state), WordCount(0, float('inf')))
    s = state.set_env("A", ShellVar(Field.create_constant("value1")))\
        .extend_localenv({"A": ShellVar(arb)})\
        .add_pathcond(StringEq(arb, Field.create_constant("")))

    arbz3var = field_content_to_z3(arb.content)

    fs_formula = True

    pathcond_formula = arbz3var == ""

    env_formula = make_env_constraints_z3(s,
                                          {"A": arbz3var})

    formula = state_to_z3(s)
    assert_equiv_formulas(formula,
                          z3.And(fs_formula, pathcond_formula, env_formula))


def test_state_to_z3_fs_simple():
    reset_z3cache()

    state = starting_state(FSModelSimple(lambda f: field_content_to_z3(f.content)))
    s = state.set_env("A", ShellVar(Field.create_constant("value1")))\
        .update_fs(IsDeleted(Field.create_constant("somefile.txt")))

    fs_formula = z3_fs_var(1) == z3.Store(z3_fs_var(0), z3.StringVal("somefile.txt"), FileInfo.mk_pair(Del, Unread))

    pathcond_formula = True

    env_formula = make_env_constraints_z3(s,
                                          {"A": z3.StringVal("value1")})

    formula = state_to_z3(s)
    assert_equiv_formulas(formula,
                          z3.And(fs_formula, pathcond_formula, env_formula))


def test_state_to_z3_fs_more():
    reset_z3cache()

    state = starting_state(FSModelSimple(lambda f: field_content_to_z3(f.content)))
    s = state.set_env("A", ShellVar(Field.create_constant("value1")))\
        .update_fs(IsDeleted(Field.create_constant("somefile.txt")))\
        .update_fs(IsFile(Field.create_constant("somefile.txt")))\
        .update_fs(IsRead(Field.create_constant("somefile.txt")))

    fs_formula = z3.And(z3_fs_var(1) == z3.Store(z3_fs_var(0), z3.StringVal("somefile.txt"), FileInfo.mk_pair(Del, Unread)),
                        z3_fs_var(2) == z3.Store(z3_fs_var(1), z3.StringVal("somefile.txt"), FileInfo.mk_pair(File, Unread)),
                        z3_fs_var(3) == z3.Store(z3_fs_var(2), z3.StringVal("somefile.txt"), FileInfo.mk_pair(File, Read)))
    fs_formula_compressed = z3_fs_var(13) == z3.Store(z3_fs_var(0), z3.StringVal("somefile.txt"), FileInfo.mk_pair(File, Read))

    pathcond_formula = True

    env_formula = make_env_constraints_z3(s,
                                          {"A": z3.StringVal("value1")})

    formula = state_to_z3(s)
    assert_equiv_formulas(formula,
                          z3.And(fs_formula, pathcond_formula, env_formula))
    assert_equiv_fs_states(s.fs_model.state_to_z3(),
                           fs_formula_compressed,
                           z3_fs_var(0),
                           z3_fs_var(10))

def test_state_to_z3_intermediate_fs_state_pathcond():
    reset_z3cache()

    state = starting_state(FSModelSimple(lambda f: field_content_to_z3(f.content)))
    s = state.set_env("A", ShellVar(Field.create_constant("value1")))\
        .update_fs(IsDeleted(Field.create_constant("somefile.txt")))\
        .add_pathcond(IsDeleted(Field.create_constant("somefile.txt")))\
        .update_fs(IsFile(Field.create_constant("somefile.txt")))\
        .update_fs(IsRead(Field.create_constant("somefile.txt")))\
        .add_pathcond(IsRead(Field.create_constant("somefile.txt")))

    fs_formula = z3.And(z3_fs_var(1) == z3.Store(z3_fs_var(0), z3.StringVal("somefile.txt"), FileInfo.mk_pair(Del, Unread)),
                        z3_fs_var(2) == z3.Store(z3_fs_var(1), z3.StringVal("somefile.txt"), FileInfo.mk_pair(File, Unread)),
                        z3_fs_var(3) == z3.Store(z3_fs_var(2), z3.StringVal("somefile.txt"), FileInfo.mk_pair(File, Read)))
    fs_formula_compressed = z3_fs_var(13) == z3.Store(z3_fs_var(0), z3.StringVal("somefile.txt"), FileInfo.mk_pair(File, Read))

    pathcond_formula = z3.And(z3.Select(z3_fs_var(1), z3.StringVal("somefile.txt")) == FileInfo.mk_pair(Del, Unread),
                              z3.Select(z3_fs_var(3), z3.StringVal("somefile.txt")) == FileInfo.mk_pair(File, Read),)

    env_formula = make_env_constraints_z3(s,
                                          {"A": z3.StringVal("value1")})

    formula = state_to_z3(s)
    assert_equiv_formulas(formula,
                          z3.And(fs_formula, pathcond_formula, env_formula))
    assert_equiv_fs_states(s.fs_model.state_to_z3(),
                           fs_formula_compressed,
                           z3_fs_var(0),
                           z3_fs_var(10))
