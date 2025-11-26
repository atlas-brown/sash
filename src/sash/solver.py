import threading
from sash.constraints import *
from sash.reporter import *
from sash.interpreter_config import InterpConfig
from sash.state import State, SymStr, Field, CompletelyArbitrary, Assertion, Trace
from dataclasses import replace
import logging
from sash.util import shasta_pretty
from pprint import pformat
import z3

arbitrary_to_z3_var: dict[CompletelyArbitrary, z3.ExprRef] = {}
tracked_assertions: dict[z3.BoolRef, Assertion] = {}

def reset_z3cache():
    global arbitrary_to_z3_var, tracked_assertions
    arbitrary_to_z3_var, tracked_assertions = {}, {}

def field_to_z3(field: Field) -> z3.ExprRef:
    return field_content_to_z3(field.content)

def field_content_to_z3(field_content: SymStr | CompletelyArbitrary) -> z3.ExprRef:
    match field_content:
        case SymStr(parts):
            assert all(isinstance(part, str) for part in parts), "SymStr with SymVars not supported in Z3 translation yet"
            return z3.StringVal("".join(parts)) # type: ignore
        case CompletelyArbitrary() as arbitrary:
            arbitrary_no_pfx_sfx = replace(arbitrary, prefix=None, suffix=None)
            if arbitrary_no_pfx_sfx not in arbitrary_to_z3_var:
                arbitrary_to_z3_var[arbitrary_no_pfx_sfx] = z3.FreshConst(z3.StringSort(), 'arb-' + shasta_pretty(arbitrary.source))
            z3_var = arbitrary_to_z3_var[arbitrary_no_pfx_sfx]
            if arbitrary.prefix:
                z3_var = z3.Concat(field_content_to_z3(arbitrary.prefix), z3_var)
            if arbitrary.suffix:
                z3_var = z3.Concat(z3_var, field_content_to_z3(arbitrary.suffix))
            return z3_var
    assert False, f"Expected field content, got {field_content}"

def constraint_to_z3(constraint: Constraint, s: State) -> z3.ExprRef:
    def norm_constraint_to_z3(constraint: Constraint, s: State):
        match constraint:
            case Empty() | HasStdout() | ExpectsStdin() | IsWritten() | CommandExists():
                return z3.BoolVal(True)
            case IsRead():
                return s.fs_model.is_read_z3(field_content_to_z3(constraint.path.content))
            case Description(text):
                # A no-op constraint with a message attached to it
                return z3.FreshBool(f"description: {text}")
            case And(lhs, rhs):
                return z3.And(norm_constraint_to_z3(lhs, s), norm_constraint_to_z3(rhs, s))
            case Or(lhs, rhs):
                return z3.Or(norm_constraint_to_z3(lhs, s), norm_constraint_to_z3(rhs, s))
            case StringEq(lhs, rhs):
                return field_content_to_z3(lhs.content) == field_content_to_z3(rhs.content)
            case IsFile(path):
                return s.fs_model.is_file_z3(field_content_to_z3(path.content))
            case IsDir(path):
                return s.fs_model.is_dir_z3(field_content_to_z3(path.content))
            case IsDeleted(path):
                return s.fs_model.is_deleted_z3(field_content_to_z3(path.content))
            case IsUnread(path):
                return s.fs_model.is_unread_z3(field_content_to_z3(path.content))
            case Not(c):
                return z3.Not(norm_constraint_to_z3(c, s))
            case Implies(premise, conclusion):
                return z3.Implies(norm_constraint_to_z3(premise, s), norm_constraint_to_z3(conclusion, s))
            case _:
                logging.error("Unrecognized constraint type in Z3 translation: %s (type %s)",
                              constraint, type(constraint))
                return z3.BoolVal(True)

    return norm_constraint_to_z3(NormalizedFSConstraint(constraint).constraint, s)

def state_to_z3(s: State) -> z3.ExprRef:
    if logging.getLogger().isEnabledFor(logging.DEBUG):
        logging.debug("Path condition constraints: %s", pformat(s.pathcond))
    pathcond_formula = z3.And([constraint_to_z3(pc, s) for pc in s.pathcond]) if s.pathcond else z3.BoolVal(True)

    if logging.getLogger().isEnabledFor(logging.DEBUG):
        logging.debug("Path condition formula: %s", pformat(pathcond_formula))

    env_formula = []
    for var, val in (s.env | s.localenv).items():
        var_z3 = z3.String(var)
        val_z3 = field_content_to_z3(val.value.content)
        eq_formula = (var_z3 == val_z3)
        env_formula.append(eq_formula)
    env_formula = z3.And(env_formula)

    fs_state_formula = s.fs_model.state_to_z3()

    if logging.getLogger().isEnabledFor(logging.DEBUG):
        logging.debug("FS state:\n%s", pformat(s.fs_model))

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
            logging.warning("Unrecognized tracked var in core: %s", tracked)
            continue

        constraint = assertion.constraint
        state = assertion.producing_state

        err = UnsatisfiedPrecondition(
            constraint,
            assertion.source_str,
            assertion.source_line
        )
        Reporter.add_issue(err)


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
def run_solver(traces: list[Trace], config: InterpConfig, stop: threading.Event | None = None):
    logging.debug("Running Z3 solver on assertions")
    for trace in traces:
        assertions = trace.latest_state.assertions
        logging.debug("Checking %d assertions", len(assertions))
        for assertion in assertions:
            if logging.getLogger().isEnabledFor(logging.DEBUG):
                logging.debug("Checking assertion from line %s :: %s", assertion.source_str, pformat(assertion))

            if stop and stop.is_set():
                logging.warning("Solver timed out")
                Reporter.set_timed_out()
                return
            solver = z3.Solver()
            solver.set(unsat_core=True)
            assertion_var, assertion_formula = assertion_to_z3(assertion)
            solver.assert_and_track(assertion_formula, assertion_var)

            if logging.getLogger().isEnabledFor(logging.DEBUG):
                logging.debug("Arb z3 map: %s", pformat(arbitrary_to_z3_var))

            #logging.debug("Current solver state: %s", solver)
            result = solver.check()
            logging.debug("Assertion:\n%s", assertion_formula)
            logging.debug("Assertion must be violated?: %s (ie %s)", result == z3.unsat, result)
            if result == z3.unsat:
                core = solver.unsat_core()
                logging.debug("Unsat core: %s", core)
                model_to_reports(core)
            else:
                model = solver.model()
                logging.debug("SAT model: %s", model)
