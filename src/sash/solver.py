import threading
from sash.constraints import *
from sash.reporter import *
from sash.interpreter_config import InterpConfig
from sash.symbolic.state import State, Assertion, Trace, RefineableConstraint
from dataclasses import replace
import logging
from sash.symbolic.strings import CompletelyArbitrary, Field, SymStr
from sash.util import shasta_pretty
from sash.fs import FileInfo, File, Read, Unread
from pprint import pformat
import z3
from sash.debugtools.solver_debug import get_debugger as solver_debugger, log_assertion_result

arbitrary_to_z3_var: dict[CompletelyArbitrary, z3.ExprRef] = {}
tracked_assertions: dict[z3.BoolRef, Assertion] = {}
command_exists_predicate = z3.Function('command_exists', z3.StringSort(), z3.BoolSort())

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
            arbitrary_no_pfx_sfx = replace(arbitrary, prefix=None, suffix=None,
                                           # As far as paths are concerned, whether they're quoted or not is irrelevant
                                           quoted=False, maybe_empty=False)
            if arbitrary_no_pfx_sfx not in arbitrary_to_z3_var:
                arbitrary_to_z3_var[arbitrary_no_pfx_sfx] = z3.FreshConst(z3.StringSort(), 'arb-' + shasta_pretty(arbitrary.source))
            z3_var = arbitrary_to_z3_var[arbitrary_no_pfx_sfx]
            if arbitrary.prefix:
                z3_var = z3.Concat(field_content_to_z3(arbitrary.prefix), z3_var)
            if arbitrary.suffix:
                z3_var = z3.Concat(z3_var, field_content_to_z3(arbitrary.suffix))
            return z3_var
    assert False, f"Expected field content, got {field_content}"

def _command_exists_to_z3(field: Field, s: State) -> z3.ExprRef:
    """
    Model command existence.
    - If we have concrete evidence that the command is missing, encode False.
    - Otherwise, leave it symbolic via an uninterpreted predicate over the command name.
    """
    if (cmd_name := field.try_to_str()) and cmd_name in s.known_nonexistent_commands:
        return z3.BoolVal(False)
    return command_exists_predicate(field_to_z3(field))


z3True = z3.BoolVal(True)

def constraint_to_z3(constraint: Constraint, s: State) -> z3.ExprRef:
    def norm_constraint_to_z3(constraint: Constraint, s: State):
        match constraint:
            case Empty():
                return z3True
            case CommandExists(name):
                return _command_exists_to_z3(name, s)
            case IsRead(path):
                return s.fs_model.is_read_z3(field_content_to_z3(path.content))
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
            case Not(c):
                return z3.Not(norm_constraint_to_z3(c, s))
            case Implies(premise, conclusion):
                return z3.Implies(norm_constraint_to_z3(premise, s), norm_constraint_to_z3(conclusion, s))
            case _:
                logging.error("Unrecognized constraint type in Z3 translation: %s (type %s)",
                              constraint, type(constraint))
                return z3True

    return norm_constraint_to_z3(constraint.normalized().constraint, s)

def state_to_z3(s: State) -> z3.ExprRef:
    if logging.getLogger().isEnabledFor(logging.DEBUG):
        logging.debug("Path condition constraints: %s", pformat(s.pathcond))
    pathcond_formula = z3.And([constraint_to_z3(pc.constraint, pc.producing_state) for pc in s.pathcond]) if s.pathcond else z3.BoolVal(True)

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

def assertion_to_z3(assertion: Assertion) -> tuple[z3.BoolRef, # assertion var
                                                   z3.ExprRef, # state formula
                                                   z3.ExprRef,
                                                   list[tuple[z3.ExprRef, Issue]]]: # assertion constraint formula
    """
    Convert an assertion to a tracked Z3 formula.
    Returns a tuple of (assertion tracking var, state formula, assertion constraint formula).
    """
    assertion_var = z3.FreshBool('assertion')
    tracked_assertions[assertion_var] = assertion
    rc = assertion.constraint
    constraint_formula = constraint_to_z3(rc.full, assertion.producing_state)
    refinement_formulas = [(constraint_to_z3(c, assertion.producing_state), im(assertion.source_line)) for (c, im) in rc.refinements]
    state_formula = state_to_z3(assertion.producing_state)

    return assertion_var, state_formula, constraint_formula, refinement_formulas


# TODO: doesn't seem like this whole indirection via the assertion var matters at all, we should cut it
def model_to_reports(core: list[z3.BoolRef],
                     solver: 'Z3Solver',
                     config: InterpConfig,
                     full_assertion: z3.ExprRef,
                     state_formula: z3.ExprRef,
                     refinements: list[tuple[z3.ExprRef, Issue]],
                     debugger: 'Optional[SolverDebugger]' = None):
    """
    Convert an unsat core to structured reporter errors.
    """
    for tracked in core:
        assertion = tracked_assertions.get(tracked)
        if not assertion:
            logging.warning("Unrecognized tracked var in core: %s", tracked)
            continue

        rc = assertion.constraint

        match refinements:
            case [(tt, issue)]:
                assert tt == z3True, "Non-empty single refinement doesn't make sense"
                logging.debug(f"Assertion has no refinements, reporting sole issue")
                Reporter.add_issue(issue, config)
                if debugger is not None:
                    to_log = (full_assertion, issue)
            case more:
                logging.debug(f"Refining assertion using {len(more)} refinements")
                assert more, "Can't have empty refinement list"
                combination_so_far = z3True
                reported = False
                for constraint_formula, issue in more:
                    solver.push()
                    combination_so_far = z3.And(combination_so_far, constraint_formula)
                    result = solver.check(combination_so_far)
                    solver.pop()
                    if result == z3.unsat:
                        Reporter.add_issue(issue, config)
                        if debugger is not None:
                            to_log = (constraint_formula, issue)
                        reported = True
                        break
                assert reported, f"Bad assertion refinements: at least one refinement is not implied by overall assertion? In {assertion.constraint}"
        if debugger is not None:
            assertion_formula, issue = to_log
            log_assertion_result(
                        assertion=assertion,
                        assertion_formula=assertion_formula,
                        state_formula=state_formula,
                        issue=str(issue.code)[5:],
                        arb_z3_map=arbitrary_to_z3_var,
                        result_type="UNSAT",
                        solver_time=-1,
                        unsat_core=core,
                        debugger=debugger,
                    )


def assume_unknowns_are_files(assertions: list[Assertion]) -> tuple[Assertion, ...]:
    def add_condition(im):
        return lambda line: im(line).under_condition(Description("Assume unknown paths are files"))
    def with_file_condition(rc: RefineableConstraint) -> RefineableConstraint:
        return replace(rc, refinements=[(c, add_condition(im)) for c, im in rc.refinements])

    new_assertions: list[Assertion] = []
    for assertion in assertions:
        state = assertion.producing_state
        fs_model = state.fs_model
        new_fs_model = fs_model.set_default_path_state(FileInfo.mk_pair(File, Read))
        new_state = replace(state, fs_model=new_fs_model).add_pathcond(Description("Assume unknown paths are files"))
        conditional_constraint = with_file_condition(assertion.constraint)
        new_assertion = replace(assertion,
                                producing_state=new_state,
                                constraint=conditional_constraint)
        new_assertions.append(new_assertion)
    return tuple(new_assertions)

# <assertion_constraint>: if true, then things are OK, if false then there's a bug

# assert not(<assertion_constraint>)
# --> if sat, then there's a model where the assertion fails
# --> if unsat, then there's no model where the assertion fails

# assert <assertion_constraint>
# --> if sat, then there's a model where the assertion succeeds
# --> if unsat, then there's no model where the assertion succeeds (ie it can only fail)
def run_solver(traces: list[Trace], config: InterpConfig, stop: threading.Event | None = None):
    total_issues_before_solver = len(Reporter._issues)
    timed_out = False

    total_assertions = sum(len(trace.latest_state.assertions) for trace in traces)
    checked_assertions = 0

    if config.debug_instrumentation:
        debugger = solver_debugger()

    logging.info("Checking %d total assertions from %d total traces", total_assertions, len(traces))
    for i, trace in enumerate(traces):
        if timed_out:
            logging.info("Ignoring %d/%d assertions due to solver timeout", total_assertions - checked_assertions, total_assertions)
            break

        assertions = trace.latest_state.assertions
        assertions = assertions + assume_unknowns_are_files(assertions)
        assertions = sorted(assertions, key=lambda a: a.priority, reverse=True)
        logging.debug("Trace %d/%d: checking %d assertions", i + 1, len(traces), len(assertions))
        for assertion in assertions:
            if logging.getLogger().isEnabledFor(logging.DEBUG):
                logging.debug("Checking assertion id %s from line %s :: %s", id(assertion), assertion.source_str, pformat(assertion))

            if stop and stop.is_set():
                timed_out = True
                logging.warning("Solver timed out")
                Reporter.set_timed_out()
                break

            checked_assertions += 1

            solver = z3.Solver()
            solver.set('timeout', 5000)
            solver.set(unsat_core=True)
            assertion_var, state_formula, assertion_formula, refinements = assertion_to_z3(assertion)
            solver.add(state_formula)

            if solver.check() == z3.unsat:
                logging.debug("Path condition is unsat, skipping assertion check")
                if config.debug_instrumentation:
                    log_assertion_result(
                        assertion=assertion,
                        state_formula=state_formula,
                        assertion_formula=assertion_formula,
                        issue='/'.join({str(issue.code)[5:] for _, issue in refinements}),
                        arb_z3_map=None,
                        result_type="PATHCOND_UNSAT",
                        solver_time=0.0,
                        debugger=debugger,
                    )
                continue

            solver.push()
            solver.assert_and_track(assertion_formula, assertion_var)

            if logging.getLogger().isEnabledFor(logging.DEBUG):
                logging.debug("Arb z3 map: %s", pformat(arbitrary_to_z3_var))

            result = solver.check()
            if logging.getLogger().isEnabledFor(logging.DEBUG):
                logging.debug("State:\n%s", state_formula)
                logging.debug("Assertion:\n%s", assertion_formula)
                logging.debug("Assertion must be violated?: %s (ie %s)", result == z3.unsat, result)

            if result == z3.unsat:
                core = solver.unsat_core()
                logging.debug("Unsat core: %s", core)
                solver.pop()
                model_to_reports(core, solver, config, assertion_formula, state_formula, refinements,
                                 debugger if config.debug_instrumentation else None)
            else:
                model = solver.model() if result == z3.sat else None
                logging.debug("{z3.result}, model: %s", model)
                if config.debug_instrumentation:
                    log_assertion_result(
                        assertion=assertion,
                        assertion_formula=assertion_formula,
                        state_formula=state_formula,
                        issue='/'.join({str(issue.code)[5:] for _, issue in refinements}),
                        arb_z3_map=arbitrary_to_z3_var,
                        result_type="SAT",
                        solver_time=-1,
                        sat_model=model,
                        debugger=debugger,
                    )

    logging.info("Solving produced %d new reports", len(Reporter._issues) - total_issues_before_solver)

