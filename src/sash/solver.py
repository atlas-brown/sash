from sash.constraints import *
from sash.reporter import *
from sash.interpreter_config import InterpConfig
from sash.state import *
import z3

arbitrary_to_z3_var = {}

def field_to_z3(field_content: SymStr | CompletelyArbitrary) -> z3.ExprRef:
    match field_content:
        case SymStr(parts):
            assert all(isinstance(part, str) for part in parts), "SymStr with SymVars not supported in Z3 translation yet"
            return z3.StringVal("".join(parts))
        case CompletelyArbitrary() as arbitrary:
            arbitrary_no_pfx_sfx = replace(arbitrary, prefix=None, suffix=None)
            if arbitrary_no_pfx_sfx not in arbitrary_to_z3_var:
                arbitrary_to_z3_var[arbitrary_no_pfx_sfx] = z3.FreshConst(z3.StringSort(), arbitrary.source.pretty())
            z3_var = arbitrary_to_z3_var[arbitrary_no_pfx_sfx]
            z3_var = z3.Concat(field_to_z3(arbitrary.prefix) if arbitrary.prefix else z3.StringVal(""),
                               z3_var,
                               field_to_z3(arbitrary.suffix) if arbitrary.suffix else z3.StringVal(""))
            return z3_var

def constraint_to_z3(constraint: Constraint, s: State):
    match constraint:
        case Empty() | HasStdout() | ExpectsStdin() | Reads() | Writes():
            return z3.BoolVal(True)
        case Not(c):
            return z3.Not(constraint_to_z3(c, s))
        case And(lhs, rhs):
            return z3.And(constraint_to_z3(lhs, s), constraint_to_z3(rhs, s))
        case Or(lhs, rhs):
            return z3.Or(constraint_to_z3(lhs, s), constraint_to_z3(rhs, s))
        case StringEq(lhs, rhs):
            return field_to_z3(lhs.content) == field_to_z3(rhs.content)
        case IsFile(path):
            return s.fs_model.is_file_z3(field_to_z3(path.content))
        case IsDir(path):
            return s.fs_model.is_dir_z3(field_to_z3(path.content))
        case IsDeleted(path):
            return s.fs_model.is_deleted_z3(field_to_z3(path.content))
        case _:
            raise NotImplementedError(f"Z3 translation not implemented for constraint type: {type(constraint)}")

def state_to_z3(s: State) -> z3.ExprRef:
    pathcond_formula = z3.And([constraint_to_z3(pc, s) for pc in s.pathcond])

    env_formula = []
    for var, val in s.env.items():
        var_z3 = z3.String(var)
        val_z3 = field_to_z3(val.value)
        eq_formula = (var_z3 == val_z3)
        env_formula.append(eq_formula)
    env_formula = z3.And(env_formula)

    return z3.And(s.fs_model.state_to_z3(), pathcond_formula, env_formula)

def assertion_to_z3(solver: z3.Solver, assertion: Assertion) -> tuple[z3.BoolRef, z3.ExprRef]:
    assertion_var = z3.FreshBool('assertion')
    constraint_formula = constraint_to_z3(assertion.constraint, assertion.producing_state)
    state_formula = state_to_z3(assertion.producing_state)

    assertion_formula = z3.Implies(state_formula, constraint_formula)

    return assertion_var, assertion_formula


def model_to_reports(model: z3.ModelRef) -> list[Report]:
    pass

# TODOS:
# map assertions to error messages
# turn model into reports
# first create an always true fs model
# write unit tests
# update michael's interface
# plug in michael's fs model
# trace merging

# <assertion_constraint>: if true, then things are OK, if false then there's a bug

# assert not(<assertion_constraint>)
# --> if sat, then there's a model where the assertion fails
# --> if unsat, then there's no model where the assertion fails

# assert <assertion_constraint>
# --> if sat, then there's a model where the assertion succeeds
# --> if unsat, then there's no model where the assertion succeeds (ie it can only fail)
def run_solver(traces: list[Trace], config: InterpConfig) -> list[Report]:
    solver = z3.Solver()
    solver.set(unsat_core=True)

    for trace in traces:
        assertions = trace.latest_state.assertions
        for assertion in assertions:
            assertion_var, assertion_formula = assertion_to_z3(solver, assertion)
            solver.assert_and_track(assertion_formula, assertion_var)


    result = solver.check()
    if result == z3.unsat:
        core = solver.unsat_core()
        return model_to_reports(core)
    else:
        return []
