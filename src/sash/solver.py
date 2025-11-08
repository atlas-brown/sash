from sash.constraints import *
from sash.reporter import *
from sash.interpreter_config import InterpConfig
from sash.state import *
import z3

arbitrary_to_z3_var = {}
tracked_assertions = {}

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
            if arbitrary.prefix:
                z3_var = z3.Concat(field_to_z3(arbitrary.prefix), z3_var)
            if arbitrary.suffix:
                z3_var = z3.Concat(z3_var, field_to_z3(arbitrary.suffix))
            return z3_var
    assert False, f"Expected field content, got {field_content}"

def constraint_to_z3(constraint: Constraint, s: State):
    match constraint:
        case Empty() | HasStdout() | ExpectsStdin() | Reads() | Writes() | Description():
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
        case Description(text):
            # A no-op constraint with a message attached to it
            return z3.FreshBool(f"description: {text}")
        case _:
            logging.error(f"Unrecognized constraint type in Z3 translation: {constraint} (type {type(constraint)})")
            return z3.BoolVal(True)


def state_to_z3(s: State) -> z3.ExprRef:
    logging.debug(f"Translating state to Z3: {s.pathcond=}")
    # TODO: The pathcondition formula is not properly converted to constraints updates to the fs should be modeled as z3.Store operations, probably
    # pathcond_formula = z3.And([constraint_to_z3(pc, s) for pc in s.pathcond]) if s.pathcond else z3.BoolVal(True)
    # logging.debug(f"Path condition formula: {pathcond_formula}")
    pathcond_formula = z3.BoolVal(True)

    env_formula = []
    for var, val in s.env.items():
        var_z3 = z3.String(var)
        val_z3 = field_to_z3(val.value.content)
        eq_formula = (var_z3 == val_z3)
        env_formula.append(eq_formula)
    env_formula = z3.And(env_formula)

    fs_state_formula = s.fs_model.state_to_z3(field_to_z3)
    logging.debug(f"FS state: {s.fs_model.state}")

    return z3.And(fs_state_formula, pathcond_formula, env_formula)

def assertion_to_z3(assertion: Assertion) -> tuple[z3.BoolRef, z3.ExprRef]:
    assertion_var = z3.FreshBool('assertion')
    tracked_assertions[assertion_var] = assertion
    constraint_formula = constraint_to_z3(assertion.constraint, assertion.producing_state)
    state_formula = state_to_z3(assertion.producing_state)

    assertion_formula = z3.And(state_formula, constraint_formula)

    return assertion_var, assertion_formula


def model_to_reports(core: list[z3.BoolRef]):
    """
    Convert an unsat core to structured reporter errors.
    """
    for tracked in core:
        assertion = tracked_assertions.get(tracked)
        if not assertion:
            logging.warning(f"Unrecognized tracked var in core: {tracked}")
            continue

        constraint = assertion.constraint
        state = assertion.producing_state

        err = UnsatisfiedPrecondition(
            constraint,
            0, # TODO: line number
        )
        Reporter.add_error(err)


# TODOS:
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
def run_solver(traces: list[Trace], config: InterpConfig):

    for trace in traces:
        assertions = trace.latest_state.assertions
        for assertion in assertions:
            solver = z3.Solver()
            solver.set(unsat_core=True)
            assertion_var, assertion_formula = assertion_to_z3(assertion)
            solver.assert_and_track(assertion_formula, assertion_var)

            logging.debug(f"Current solver state: {solver}")
            result = solver.check()
            if result == z3.unsat:
                core = solver.unsat_core()
                logging.info(f"Unsat core: {core}")
                model_to_reports(core)
            else:
                model = solver.model()
                logging.info(f"SAT model: {model}")
