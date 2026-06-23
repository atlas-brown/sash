#! /usr/bin/env -S uv run
from collections.abc import Callable
import sys
import yaml
import subprocess
from pathlib import Path
import jsonschema
import multiprocessing
import traceback
import re
import report

from dataclasses import dataclass, field
import sash.main
from sash.main import build_cli as build_sash_cli
import sash.reporter


def build_cli():
    # fmt: off
    parser = build_sash_cli(options_only=True)
    parser.description = "Evaluate SaSh"
    parser.epilog = "Regardless of which flags are used to select which analyses to run, all detected benchmarks' info files will be validated against a schema, to make sure no important info is missing"
    parser.add_argument('-b', '--benchmarks', type=Path, default=ROOT_DIR / 'benchmarks' / 'bugs_and_variants', help='Path to the benchmarks directory, relative to the git toplevel (default: <git_toplevel>/benchmarks)')
    parser.add_argument('-O', '--only', type=str, default='.*', help='Regex to filter benchmarks to run (default: run all)')
    parser.add_argument('-S', '--skip-buggy', action='store_true', help='Don\'t run the evaluation on the buggy versions of the benchmarks (default: false)')
    parser.add_argument('-f', '--fixed', action='store_true', help='Run the evaluation on the fixed versions of the benchmarks (default: false)')
    parser.add_argument('-v', '--variants', action='store_true', help='Run the evaluation on the variant versions of the benchmarks; given the values of \'-S\' and \'-f\', only the matching variants will run (default: false)')
    parser.add_argument('-vO', '--variants-only', action='store_true', help='Run the evaluation only on the variant versions of the benchmarks (default: false)')
    parser.add_argument('-a', '--all', action='store_true', help='Run the evaluation on all versions of the benchmarks; equivalent to \'-f -v\' (default: false)')
    parser.add_argument('-c', '--csv', type=Path, default=None, help='File to write CSV results to (default: no CSV output)')
    parser.add_argument('-H', '--html', type=Path, default=None, help='File to write HTML overview to (default: no HTML output)')
    parser.add_argument('-N', '--no-color', action='store_true', help='Disable colored output to stderr (default: false)')
    parser.add_argument('-j', '--jobs', type=int, default=1, help='Number of parallel jobs (default: 1, as introducing multiple threads can slow execution down and lead to different results for smaller timeouts; be aware)')
    parser.add_argument('-V', '--verbose', action='store_true', help='Enable printing of error reports or exceptions that occur, and raw output when ground truth is missing (default: false)')
    return parser
    # fmt: on


def main(
    benchmarks_dir: Path,
    bench_filter: re.Pattern,
    run_buggy: bool,
    run_fixed: bool,
    run_variants: bool,
    run_only_variants: bool,
    csv_file: Path | None,
    html_file: Path | None,
    verbose: bool,
    no_color: bool,
    num_jobs: int,
):
    if no_color:
        disable_color()

    benchmarks_dir = benchmarks_dir.absolute()

    # This should be the only possible early exit of the script, any other errors must be handled gracefully
    try:
        VALIDATOR.check_schema(INFO_SCHEMA)
    except jsonschema.SchemaError as e:
        eprint(f"Internal error: Info schema is invalid: {e.message}")
        exit(1)

    eprint(f"{BOLD}Hello!{RESET}")
    oos_codes = load_oos_codes(benchmarks_dir)
    if oos_codes:
        eprint(f"Out-of-scope codes:")
        for code in sorted(oos_codes):
            eprint(f"  {code}")

    eprint(f"\n{BOLD}Preparing analyses{RESET}")
    stats = EvalStats()
    jobs: list[Job] = []
    for bench_dir in benchmarks_dir.iterdir():
        if bench_dir.is_dir() and bench_filter.match(bench_dir.relative_to(benchmarks_dir).as_posix()):
            jobs.extend(
                prepare_jobs(
                    bench_dir,
                    stats,
                    oos_codes,
                    eval_buggy=run_buggy,
                    eval_fixed=run_fixed,
                    eval_variants=run_variants,
                    eval_only_variants=run_only_variants,
                    verbose=verbose,
                )
            )
    eprint("Done!")

    if len(jobs) == 0:
        eprint("\nNo analyses to run; exiting")
        exit(0)

    eprint(f"\n{BOLD}Running analyses{RESET}")
    eprint(
        f"Running {len(jobs)} analyses (on {stats.benchmarks - stats.skipped} benchmarks) using {num_jobs} processes"
    )

    global SASH_KWARGS
    with multiprocessing.Pool(
        processes=num_jobs,
        initializer=_init_worker,
        initargs=(no_color,),
    ) as pool:
        finished = pool.starmap(
            run_job,
            [
                (
                    job,
                    verbose,
                    SASH_KWARGS,
                )
                for job in jobs
            ],
        )

    eprint(f"\n{BOLD}Printing per-analyis results{RESET}")
    for job in finished:
        process_finished_job(stats, job)
        if job is not finished[-1]:
            eprint()  # Blank line for readability

    eprint(f"\n{BOLD}Printing aggregate results{RESET}")
    eprint("Total benchmarks (pair of buggy + fixed scripts, with an optional variant): ", stats.benchmarks)
    eprint("Total analyses ran: ", stats.analyses)
    eprint("  Successful: ", stats.successful)
    eprint("  Failed (raised exception): ", stats.crashed)
    eprint("  Timed out: ", stats.timed_out)
    eprint("Total time: ", f"{stats.total_time:.2f}s")
    eprint("  Total execution time: ", f"{stats.exec_time:.2f}s")
    eprint("  Total solver time: ", f"{stats.solver_time:.2f}s")
    eprint("Total known bugs (present in each benchmark's ground truth): ", stats.buggy_expected_bugs)
    eprint("  Out of these were detected: ", stats.buggy_detected_bugs)
    eprint("  Out of these were not detected: ", stats.buggy_undetected_bugs)
    eprint("Total unknown bugs detected (not present in each benchmark's ground truth): ", stats.buggy_unexpected_bugs)

    if csv_file is not None:
        export_as_csv(
            file=csv_file,
            jobs=finished,
        )

    if html_file is not None:
        generate_html_report(
            filename=html_file,
            stats=stats,
            jobs=finished,
        )

    if stats.crashed > 0:
        exit(1)


# This is used to avoid changes when SaSh's main signature changes
def make_sash_main(**kwargs) -> Callable[[Path], sash.reporter.Report]:

    def sash_main(file: Path) -> sash.reporter.Report:
        return sash.main.main(file=file, **{k: v for k, v in kwargs.items() if v is not None})

    return sash_main


@dataclass
class ReportEntry:
    sash_code: str
    line: int
    shellcheck_code: str | None = None

    def __eq__(self, other: object) -> bool:
        # Ignore shellcheck_code for equality checks
        return (
            isinstance(other, ReportEntry)
            and self.sash_code == other.sash_code
            and self.line == other.line
        )


# Describes a job to be run on a benchmark, where benchmark here is a specific file (e.g., posix.sh)
# The ground truth corresponds to that specific file
@dataclass
class Job:
    benchmark: Path
    ground_truth: dict


# Extends the Job with results from running the analysis
# The field additional_info is used to for "backwards compatibility" with the html report generator
@dataclass
class FinishedJob(Job):
    timed_out: bool
    crashed: bool
    exn_traceback: str | None = None
    report: sash.reporter.Report | None = None
    additional_info: dict = field(default_factory=dict)


@dataclass
class EvalStats:
    benchmarks: int = 0  # Directory-level benchmarks
    skipped: int = 0

    analyses: int = 0  # Total analyses (files) run
    crashed: int = 0
    timed_out: int = 0

    exec_time: float = 0.0  # Symbolic execution time (constraint collection phase)
    solver_time: float = 0.0  # Constraint solver time

    # Total number of bugs expected to be detected across buggy benchmarks
    buggy_expected_bugs: int = 0
    # Total number of bugs detected across buggy benchmarks
    buggy_detected_bugs: int = 0
    # Total number of additional bugs detected across buggy benchmarks
    buggy_unexpected_bugs: int = 0

    # Total number of bugs that were expected to NOT be detected across fixed benchmarks
    fixed_expected_missing_bugs: int = 0
    # Total number of bugs that were expected to NOT be detected but were actually detected across fixed benchmarks
    fixed_regression_bugs: int = 0
    # Total number of bugs that we had no expectation about but were detected across fixed benchmarks
    fixed_rest_bugs: int = 0

    @property
    def successful(self) -> int:
        return self.analyses - self.crashed

    @property
    def total_time(self) -> float:
        return self.exec_time + self.solver_time

    # Total number of bugs that were expected but not detected across all benchmarks
    @property
    def buggy_undetected_bugs(self) -> int:
        return self.buggy_expected_bugs - self.buggy_detected_bugs


def process_finished_job(stats: EvalStats, job: FinishedJob):
    where = job.benchmark.relative_to(ROOT_DIR)

    def process_buggy_job(job: FinishedJob):
        # We care about three things:
        # (1) Bugs that were expected to be detected and were actually detected
        # (2) Bugs that were expected to be detected but were not detected (calculated from the first two)
        # (3) Bugs that were not expected to be detected but were detected anyway

        all_reports = [ReportEntry(i.code, i.line, None) for i in job.report.issues]  # type: ignore

        all_gt = [
            ReportEntry(bug_info["code"], l, bug_info.get("shellcheck"))
            for bug_info in job.ground_truth["bugs"].values()
            for l in bug_info.get("lines", [])
        ]

        all_expected_detected = []  # (1)
        for entry in all_gt:
            if entry in all_reports:
                all_reports.remove(entry)
                # Entry includes the ShellCheck code, which is desired
                all_expected_detected.append(entry)

        all_expected_undetected = []  # (2)
        temp_all_expected_detected = all_expected_detected.copy()
        for entry in all_gt:
            if entry not in temp_all_expected_detected:
                # Entry includes the ShellCheck code, which is desired
                all_expected_undetected.append(entry)
            else:
                # We remove from the temp list to account for the same bug appearing multiple times in a single line
                # list.remove() only removes the first occurrence
                temp_all_expected_detected.remove(entry)

        # Entries do not include the ShellCheck code, which is fine because we don't care about it here
        all_unexpected_detected = all_reports  # (3)

        stats.buggy_expected_bugs += len(all_gt)
        stats.buggy_detected_bugs += len(all_expected_detected)
        stats.buggy_unexpected_bugs += len(all_unexpected_detected)

        if len(all_expected_undetected) == 0:
            eprint_succ(
                where,
                f"All expected bugs detected ({len(all_expected_detected)} total)",
            )
        else:
            eprint_fail(
                where,
                f"{len(all_expected_detected)} out of {len(all_expected_detected) + len(all_expected_undetected)} expected bugs detected",
            )
            for entry in all_expected_undetected:
                eprint_fail(
                    where,
                    f"L{entry.line}:{entry.sash_code} is missing",
                )

        if len(all_unexpected_detected) > 0:
            eprint_info(
                where,
                f"{len(all_unexpected_detected)} additional bugs detected",
            )

        # For html report generation
        job.additional_info = {
            "expected": all_gt,
            "actual": [ReportEntry(i.code, i.line, None) for i in job.report.issues],  # type: ignore
            "detected_all": len(all_expected_undetected) == 0,
            "kind": job.ground_truth["kind"],
        }

    def process_fixed_job(job: FinishedJob):
        # We care about two things:
        # (1) Bugs that were expected to not be detected but were actually detected
        # (2) Bugs that were detected but we had no expectation about them

        all_reports = [ReportEntry(i.code, i.line, None) for i in job.report.issues]  # type: ignore

        all_gt = [
            ReportEntry(bug_info["code"], l, bug_info.get("shellcheck"))
            for bug_info in job.ground_truth["bugs"].values()
            for l in bug_info.get("regression_lines", [])
        ]

        all_unexpected_detected = []  # (1)
        for entry in all_gt:
            if entry in all_reports:
                all_reports.remove(entry)
                # Entry includes the ShellCheck code, which is desired
                all_unexpected_detected.append(entry)

        all_no_expectation_detected = all_reports  # (2)

        stats.fixed_expected_missing_bugs += len(all_gt)
        stats.fixed_regression_bugs += len(all_unexpected_detected)
        stats.fixed_rest_bugs += len(all_no_expectation_detected)

        if len(all_unexpected_detected) == 0:
            eprint_succ(
                where,
                f"No regression bugs detected (0 out of {len(all_gt)} expected missing bugs)",
            )
        else:
            eprint_fail(
                where,
                f"{len(all_unexpected_detected)} out of {len(all_gt)} expected missing bugs were detected",
            )
            for entry in all_unexpected_detected:
                eprint_fail(
                    where,
                    f"L{entry.line}:{entry.sash_code} was detected but expected to be missing",
                )

        if len(all_no_expectation_detected) > 0:
            eprint_warn(
                where,
                f"{len(all_no_expectation_detected)} additional bugs detected",
            )

        # For html report generation
        job.additional_info = {
            "expected": all_gt,
            "actual": [ReportEntry(i.code, i.line, None) for i in job.report.issues],  # type: ignore
            "detected_all": len(all_unexpected_detected) == 0,
            "kind": job.ground_truth["kind"],
        }

    # Evaluate job status
    if job.crashed:
        eprint_fail(where, "Exception during analysis")
        if job.exn_traceback:
            eprint(job.exn_traceback)
        stats.crashed += 1
        return

    assert (
        job.report is not None
    ), "How was a report not generated if the job didn't crash?"

    et = job.report.time
    st = job.report.solver_time
    stats.exec_time += et
    stats.solver_time += st
    if job.timed_out:
        eprint_warn(
            where,
            f"Analysis timed out; exec: {et:.2f}s, solver: {st:.2f}s, total: {et+st:.2f}s",
        )
        stats.timed_out += 1
    else:
        eprint_succ(
            where,
            f"Analysis completed; exec: {et:.2f}s, solver: {st:.2f}s, total: {et+st:.2f}s",
        )

    # Evaluate job results
    if job.ground_truth["kind"] in ["buggy", "buggy_variant"]:
        process_buggy_job(job)
    elif job.ground_truth["kind"] in ["fixed", "fixed_variant"]:
        process_fixed_job(job)
    else:
        raise AssertionError(
            f"Should not have executed file of kind '{job.ground_truth['kind']}'"
        )


def run_job(
    job: Job,
    verbose: bool,
    sash_kwargs: dict,
) -> FinishedJob:
    where = job.benchmark.relative_to(ROOT_DIR)
    eprint_info(where, "Running analysis")
    finished: FinishedJob
    try:
        sash.reporter.Reporter.reset()  # I'm not sure this is needed, but just in case

        report = sash.main.main(
            job.benchmark,
            **{k: v for k, v in sash_kwargs.items() if v is not None},
            log_level="DISABLED",
            collect_debug_info=False,
        )

        finished = FinishedJob(
            benchmark=job.benchmark,
            ground_truth=job.ground_truth,
            timed_out=report.timed_out,
            crashed=False,
            report=report,
        )

    except (AssertionError, BaseException) as e:
        if isinstance(e, KeyboardInterrupt):
            raise e  # Re-raise keyboard interrupts

        exn_traceback = traceback.format_exc() if verbose else None

        finished = FinishedJob(
            benchmark=job.benchmark,
            ground_truth=job.ground_truth,
            timed_out=False,
            crashed=True,
            exn_traceback=exn_traceback,
            report=None,
        )

    return finished


def prepare_jobs(
    benchmark_dir: Path,
    stats: EvalStats,
    oos_codes: set[str],
    eval_buggy,
    eval_fixed,
    eval_variants,
    eval_only_variants: bool,
    verbose: bool = False,
) -> list[Job]:
    all_codes = sash.reporter.Issue.all_codes()
    where = benchmark_dir.relative_to(ROOT_DIR)
    stats.benchmarks += 1

    info = load_info(benchmark_dir)
    if info is None:
        eprint_fail(where, f"Skipping evaluation due to missing '{INFO_FILENAME}'")
        stats.skipped += 1
        return []

    val_errs = validate_benchmark(benchmark_dir, info)
    if len(val_errs) > 0:
        eprint_fail(
            where, f"Skipping evaluation due to '{INFO_FILENAME}' validation errors"
        )
        if verbose:
            for err in val_errs:
                eprint_fail(where, f"{err}")
        stats.skipped += 1
        return []

    # Info exists and is valid

    bugs = {}
    for bug_id, bug_info in info["bugs"].items():
        if bug_info["code"] in oos_codes:
            eprint_info(
                where,
                f"Skipping bug '{bug_id}' (code '{bug_info['code']}' is out of scope)",
            )
            continue

        if bug_info["code"] not in all_codes:
            eprint_fail(
                where,
                f"Skipping bug '{bug_id}' (code '{bug_info['code']}' is not a recognized code)",
            )
            continue

        bugs[bug_id] = bug_info

    eval_kinds = []
    if eval_only_variants:
        if eval_buggy:
            eval_kinds.append("buggy_variant")
        if eval_fixed:
            eval_kinds.append("fixed_variant")
    else:
        if eval_buggy:
            eval_kinds.append("buggy")
        if eval_fixed:
            eval_kinds.append("fixed")
        if eval_variants and eval_buggy:
            eval_kinds.append("buggy_variant")
        if eval_variants and eval_fixed:
            eval_kinds.append("fixed_variant")

    jobs = []
    for gt in info["ground_truths"]:
        if gt["kind"] not in eval_kinds:
            continue

        path = benchmark_dir / gt["path"]
        if not path.exists():
            eprint_fail(where, f"File '{gt['path']}' does not exist")
            continue

        # Call list() here to be able to modify the dict while iterating
        for bug_id in list(gt["bugs"].keys()):
            if bug_id not in bugs:
                # The bug is out of scope; delete it from ground truth
                del gt["bugs"][bug_id]
                continue

            # Enrich ground truth with bug info for easier access later
            gt["bugs"][bug_id]["code"] = bugs[bug_id]["code"]
            gt["bugs"][bug_id]["description"] = bugs[bug_id]["description"]
            gt["bugs"][bug_id]["shellcheck"] = bugs[bug_id]["shellcheck"]

        jobs.append(Job(benchmark=path, ground_truth=gt))

    stats.analyses += len(jobs)
    return jobs


# Validates the benchmark's info file against the schema
def validate_benchmark(benchmark_dir: Path, info: dict | None) -> list[str]:
    where = benchmark_dir.relative_to(ROOT_DIR)

    if info is None and (info := load_info(benchmark_dir)) is None:
        return []

    errors = []
    for e in VALIDATOR(schema=INFO_SCHEMA).iter_errors(info):
        path = ".".join([str(p) for p in e.path])
        if len(path) > 0:
            path = f"{BLUE}{path}{RESET}: "
        errors.append(f"{MAGENTA}{where}{RESET}: {path}{e.message}")

    if len(errors) > 0:
        eprint_fail(where, f"'{INFO_FILENAME}' has {len(errors)} validation errors")

    return sorted(errors, key=str)


def load_info(benchmark_dir: Path) -> dict | None:
    where = benchmark_dir.relative_to(ROOT_DIR)
    info_file = benchmark_dir / INFO_FILENAME

    if not info_file.exists():
        eprint_fail(where, f"Missing '{INFO_FILENAME}' file")
        return None

    with info_file.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_oos_codes(benchmarks_dir: Path) -> set[str]:
    oos_file = benchmarks_dir / "codes_out_of_scope.yaml"
    res = set()
    if oos_file.exists():
        with oos_file.open("r", encoding="utf-8") as f:
            oos_codes = yaml.safe_load(f)
            res.update(oos_codes)
    return res


def git_toplevel() -> Path:
    return Path(
        subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"], encoding="utf-8"
        ).strip()
    )


def eprint_succ(where: Path, msg: str):
    eprint(f"[{GREEN}{where}{RESET}] {msg}")


def eprint_fail(where: Path, msg: str):
    eprint(f"[{RED}{where}{RESET}] {msg}")


def eprint_warn(where: Path, msg: str):
    eprint(f"[{YELLOW}{where}{RESET}] {msg}")


def eprint_info(where: Path, msg: str):
    eprint(f"[{CYAN}{where}{RESET}] {msg}")


def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


def disable_color():
    global MAGENTA, BLUE, CYAN, GREEN, YELLOW, RED, RESET, BOLD, UNDERLINE
    MAGENTA = ""
    BLUE = ""
    CYAN = ""
    GREEN = ""
    YELLOW = ""
    RED = ""
    RESET = ""
    BOLD = ""
    UNDERLINE = ""


def _init_worker(no_color: bool) -> None:
    if no_color:
        disable_color()


# A RunResult is the format that the functions to export to CSV and HTML expect
def job_to_run_result(job: FinishedJob) -> report.RunResult:
    if job.report is None:
        return report.RunResult(
            benchmark=job.benchmark.as_posix(),
            kind="unknown",
            missing_gt=False,
            crashed=job.crashed,
            timed_out=job.timed_out,
            time=None,
            exec_time=None,
            solver_time=None,
            detected_all=False,
            expected_results=None,
            actual_results=None,
            shellcheck_codes=None,
            line_numbers=None,
            ast_nodes_total=None,
            ast_nodes_interpreted=None,
            ast_coverage_pct=None,
        )

    assert job.additional_info is not None
    assert "expected" in job.additional_info  # list[ReportInfo]
    assert "actual" in job.additional_info  # list[ReportInfo]
    assert "detected_all" in job.additional_info  # bool
    assert "kind" in job.additional_info  # str

    expected_results = [
        f"L{j.line}:{j.sash_code}" for j in job.additional_info["expected"]
    ]
    actual_results = [f"L{j.line}:{j.sash_code}" for j in job.additional_info["actual"]]
    shellcheck_codes = [j.shellcheck_code for j in job.additional_info["expected"]]
    line_numbers = [j.line for j in job.additional_info["actual"]]

    return report.RunResult(
        benchmark=job.benchmark.as_posix(),
        kind=job.additional_info["kind"],
        missing_gt=False,
        crashed=job.crashed,
        timed_out=job.timed_out,
        time=job.report.time,
        exec_time=job.report.time,
        solver_time=job.report.solver_time,
        detected_all=job.additional_info["detected_all"],
        expected_results=expected_results,
        actual_results=actual_results,
        shellcheck_codes=shellcheck_codes,
        line_numbers=line_numbers,
        ast_nodes_total=job.report.ast_nodes_total,
        ast_nodes_interpreted=job.report.ast_nodes_interpreted,
        ast_coverage_pct=job.report.ast_coverage_pct,
    )


def export_as_csv(
    file: Path,
    jobs: list[FinishedJob],
):
    run_results = [job_to_run_result(job) for job in jobs]
    with file.open("w") as csvfile:
        csvfile.write(f"{','.join(report.RunResult._fields)}" + "\n")
        for r in run_results:
            csvfile.write(
                f"{r.benchmark},"
                f"{r.kind},"
                f"{r.missing_gt},"
                f"{r.crashed},"
                f"{r.timed_out},"
                f"{r.time},"
                f"{r.exec_time},"
                f"{r.solver_time},"
                f"{r.detected_all},"
                f"{';'.join([e if e is not None else '' for e in r.expected_results]) if r.expected_results else ''},"
                f"{';'.join([a if a is not None else '' for a in r.actual_results]) if r.actual_results else ''},"
                f"{';'.join([c if c is not None else '' for c in r.shellcheck_codes]) if r.shellcheck_codes else ''},"
                f"{';'.join(str(line) if line is not None else '' for line in r.line_numbers) if r.line_numbers else ''},"
                f"{r.ast_nodes_total if r.ast_nodes_total is not None else ''},"
                f"{r.ast_nodes_interpreted if r.ast_nodes_interpreted is not None else ''},"
                f"{r.ast_coverage_pct if r.ast_coverage_pct is not None else ''}"
                "\n"
            )

    eprint(f"CSV report generated: {file.name}")


def generate_html_report(
    filename: Path,
    stats: EvalStats,
    jobs: list[FinishedJob],
):
    run_results = [job_to_run_result(job) for job in jobs]
    ran = stats.analyses
    skipped = stats.skipped
    failed = stats.crashed
    unknown = 0
    timed_out = stats.timed_out
    total_issues = stats.buggy_expected_bugs
    detected_issues_expected = stats.buggy_detected_bugs
    detected_issues_extra = stats.buggy_unexpected_bugs
    detected_issues_extra_unsat_preconds = 0  # Not tracked currently
    detected_issues_extra_unset_vars = 0  # Not tracked currently
    total_exec_time = stats.exec_time
    total_solver_time = stats.solver_time

    report.generate_html_report(
        html_file=filename,
        run_results=run_results,
        ran=ran,
        skipped=skipped,
        failed=failed,
        unknown=unknown,
        timed_out=timed_out,
        total_issues=total_issues,
        detected_issues_expected=detected_issues_expected,
        detected_issues_extra=detected_issues_extra,
        detected_issues_extra_unsat_preconds=detected_issues_extra_unsat_preconds,
        detected_issues_extra_unset_vars=detected_issues_extra_unset_vars,
        total_exec_time=total_exec_time,
        total_solver_time=total_solver_time,
    )

    eprint(f"HTML report generated: {filename}")


ROOT_DIR = git_toplevel()
INFO_FILENAME = "info.yaml"
VALIDATOR = jsonschema.Draft202012Validator  # Must match the $schema in INFO_SCHEMA
INFO_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["sources", "bugs", "ground_truths"],
    "properties": {
        "description": {"type": "string"},
        "sources": {
            "type": "array",
            "items": {"type": "string", "format": "uri"},
            "minItems": 1,
        },
        "notes": {"type": "array", "items": {"type": "string"}},
        "bugs": {
            "type": "object",
            "patternProperties": {
                "^bug[0-9]{2}$": {
                    "type": "object",
                    "required": ["description", "code", "shellcheck"],
                    "properties": {
                        "description": {"type": "string"},
                        "code": {"type": "string"},
                        "shellcheck": {
                            "type": ["string", "null"],
                            "pattern": "^SC[0-9]{4}$",
                        },
                        "notes": {"type": "array", "items": {"type": "string"}},
                    },
                    "additionalProperties": False,
                },
            },
            "additionalProperties": False,
        },
        "ground_truths": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["path", "kind", "bugs"],
                "properties": {
                    "path": {"type": "string"},
                    "kind": {
                        "type": "string",
                        "enum": [
                            "original",
                            "buggy",
                            "fixed",
                            "buggy_variant",
                            "fixed_variant",
                        ],
                    },
                    "bugs": {
                        "type": "object",
                        "patternProperties": {
                            "^bug[0-9]{2}$": {
                                "type": "object",
                                "oneOf": [
                                    {"required": ["lines"]},
                                    {"required": ["regression_lines"]},
                                ],
                                "properties": {
                                    "lines": {
                                        "type": "array",
                                        "items": {"type": "integer"},
                                        "minItems": 1,
                                    },
                                    "regression_lines": {
                                        "type": "array",
                                        "items": {"type": "integer"},
                                        "minItems": 1,
                                    },
                                    "shellcheck": {
                                        "type": ["string", "null"],
                                        "pattern": "^SC[0-9]{4}$",
                                    },
                                    "notes": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                    },
                                },
                                "additionalProperties": False,
                            },
                        },
                        "additionalProperties": False,
                    },
                },
                "additionalProperties": False,
            },
        },
    },
    "additionalProperties": False,
}


# ANSI color codes
MAGENTA = "\033[95m"
BLUE = "\033[94m"
CYAN = "\033[96m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
RESET = "\033[0m"
BOLD = "\033[1m"
UNDERLINE = "\033[4m"


if __name__ == "__main__":
    args = build_cli().parse_args()

    SASH_KWARGS = {
        "timeout": args.timeout,
        "exec_timeout_pct": args.exec_timeout_pct,
        "dfs_timeout_pct": args.dfs_timeout_pct,
        "targeted_dfs_timeout_pct": args.targeted_dfs_timeout_pct,
        "disable_optimistic_forking": args.disable_optimistic_forking,
        "disable_trace_collapsing": args.disable_trace_collapsing,
        "disable_targeted_dfs": args.disable_dfs or args.disable_targeted_dfs,
        "disable_unbound_as_empty_dfs": args.disable_dfs or args.disable_unbound_as_empty_dfs,
        "disable_solver": args.disable_solver,
        "disable_solver_optimizations": args.disable_solver_optimizations,
    }

    main(
        benchmarks_dir=args.benchmarks,
        bench_filter=re.compile(args.only),
        run_buggy=not args.skip_buggy or args.all,
        run_fixed=args.fixed or args.all,
        run_variants=args.variants or args.all,
        run_only_variants=args.variants_only,
        csv_file=args.csv,
        html_file=args.html,
        verbose=args.verbose,
        no_color=args.no_color,
        num_jobs=max(args.jobs, 0),
    )
