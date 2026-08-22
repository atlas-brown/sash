#! /usr/bin/env -S uv run
import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml


EXPECTED_VERSION = "0.11.0"


@dataclass(frozen=True, order=True)
class Warning:
    path: str
    code: str
    line: int


@dataclass(frozen=True)
class ExpectedBug:
    path: str
    bug: str
    code: Optional[str]
    line: int


@dataclass(frozen=True)
class BenchmarkResult:
    checked: bool
    true_positives: int = 0
    false_negatives: int = 0
    true_negatives: int = 0
    false_positives: int = 0


@dataclass(frozen=True)
class AggregateResult:
    kind: str
    checked: int = 0
    true_positives: int = 0
    false_negatives: int = 0
    true_negatives: int = 0
    false_positives: int = 0


@dataclass(frozen=True)
class ExpectedSilence:
    bug: ExpectedBug
    silent_is_false_negative: bool


def selected_paths(info: dict, kinds: set[str]) -> set[str]:
    return {
        gt["path"]
        for gt in info.get("ground_truths", [])
        if gt["kind"] in kinds
    }


def build_cli() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare ShellCheck output with existing benchmark metadata."
    )
    parser.add_argument(
        "-b",
        "--benchmarks",
        type=Path,
        default=Path("benchmarks/bugs_and_variants"),
        help="Benchmarks directory (default: benchmarks/bugs_and_variants)",
    )
    parser.add_argument(
        "-O",
        "--only",
        default=None,
        help="Only check one benchmark directory name, e.g. m2-unset_var",
    )
    parser.add_argument(
        "--kind",
        action="append",
        choices=["original", "buggy", "fixed", "buggy_variant", "fixed_variant"],
        default=None,
        help="Ground-truth kind to check; repeatable (default: buggy, fixed, buggy_variant)",
    )
    parser.add_argument(
        "--list-unclassified",
        action="store_true",
        help="List unclassified ShellCheck warnings on ground-truth bug lines.",
    )
    return parser


def shellcheck_version() -> str | None:
    proc = subprocess.run(
        ["shellcheck", "--version"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        return None
    for line in proc.stdout.splitlines():
        if line.startswith("version:"):
            return line.split(":", 1)[1].strip()
    return None


def run_shellcheck(script: Path, reported_path: str) -> set[Warning]:
    proc = subprocess.run(
        [
            "shellcheck",
            "--format=json",
            "--shell=sh",
            "--enable=all",
            "--severity=style",
            script.as_posix(),
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode not in (0, 1):
        raise RuntimeError(proc.stderr.strip() or f"ShellCheck exited {proc.returncode}")

    data = json.loads(proc.stdout or "[]")
    return {
        Warning(
            path=reported_path,
            code=f"SC{int(item['code']):04d}",
            line=item["line"],
        )
        for item in data
    }


def load_info(bench_dir: Path) -> dict:
    with (bench_dir / "info.yaml").open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def expected_bugs(info: dict, kinds: set[str]) -> list[ExpectedBug]:
    expected = []
    for gt in info.get("ground_truths", []):
        if gt["kind"] not in kinds:
            continue
        if "variant" in gt["kind"]:
            continue

        for bug_id, gt_bug in gt.get("bugs", {}).items():
            shellcheck_code = shellcheck_code_for(info, gt, bug_id, gt_bug)
            for line in gt_bug.get("lines", []):
                expected.append(ExpectedBug(gt["path"], bug_id, shellcheck_code, line))
    return expected


def expected_absences(info: dict, kinds: set[str]) -> list[ExpectedBug]:
    return [item.bug for item in expected_silences(info, kinds)]


def expected_silences(info: dict, kinds: set[str]) -> list[ExpectedSilence]:
    expected = []
    for gt in info.get("ground_truths", []):
        if gt["kind"] not in kinds:
            continue

        for bug_id, gt_bug in gt.get("bugs", {}).items():
            if "variant" in gt["kind"]:
                for line in gt_bug.get("lines", []):
                    shellcheck_code = info["bugs"][bug_id]["shellcheck"]
                    expected.append(
                        ExpectedSilence(
                            ExpectedBug(gt["path"], bug_id, shellcheck_code, line),
                            silent_is_false_negative=True,
                        )
                    )
                continue

            shellcheck_code = info["bugs"][bug_id]["shellcheck"]
            for line in gt_bug.get("regression_lines", []):
                expected.append(
                    ExpectedSilence(
                        ExpectedBug(gt["path"], bug_id, shellcheck_code, line),
                        silent_is_false_negative=False,
                    )
                )
    return sorted(
        expected,
        key=lambda item: (
            item.bug.path,
            item.bug.line,
            item.bug.bug,
            item.bug.code or "",
            item.silent_is_false_negative,
        ),
    )


def shellcheck_code_for(info: dict, gt: dict, bug_id: str, gt_bug: dict) -> str | None:
    if "shellcheck" in gt_bug:
        return gt_bug["shellcheck"]
    if "variant" in gt["kind"]:
        return None
    return info["bugs"][bug_id]["shellcheck"]


def check_benchmark(
    bench_dir: Path, kinds: set[str], list_unclassified: bool = False
) -> BenchmarkResult:
    info = load_info(bench_dir)
    paths = selected_paths(info, kinds)
    if not paths:
        return BenchmarkResult(False)

    expected = expected_bugs(info, kinds)
    expected_silent = expected_silences(info, kinds)
    expected_missing = [item.bug for item in expected_silent]

    actual_by_path = {
        path: run_shellcheck(bench_dir / path, path)
        for path in sorted(paths)
    }

    missing = [
        bug
        for bug in expected
        if bug.code is None
        or Warning(bug.path, bug.code, bug.line) not in actual_by_path[bug.path]
    ]
    classified = {
        Warning(bug.path, bug.code, bug.line)
        for bug in expected + expected_missing
        if bug.code is not None
    }
    ground_truth_lines = {
        (bug.path, bug.line)
        for bug in expected + expected_missing
    }
    unclassified = sorted(
        warning
        for warnings in actual_by_path.values()
        for warning in warnings
        if warning not in classified
        and (warning.path, warning.line) in ground_truth_lines
    )
    if list_unclassified and unclassified:
        print(
            f"{bench_dir}: unclassified ShellCheck warnings on ground-truth lines",
            file=sys.stderr,
        )
        for warning in unclassified:
            expected_here = [
                f"{bug.bug}:{bug.code or 'no-shellcheck-code'}"
                for bug in expected + expected_missing
                if bug.path == warning.path and bug.line == warning.line
            ]
            print(
                f"  {warning.path}:{warning.line}: {warning.code}"
                f" (ground truth: {', '.join(expected_here)})",
                file=sys.stderr,
            )

    if missing:
        missing_coded = [bug for bug in missing if bug.code is not None]
        if missing_coded:
            print(f"{bench_dir}: missing expected ShellCheck warnings", file=sys.stderr)
            for bug in missing_coded:
                print(f"  {bug.path}:{bug.line}: {bug.code} for {bug.bug}", file=sys.stderr)

    false_positives = [
        item
        for item in expected_silent
        if item.bug.code is not None
        and Warning(item.bug.path, item.bug.code, item.bug.line) in actual_by_path[item.bug.path]
    ]
    if false_positives:
        print(f"{bench_dir}: ShellCheck warnings on expected-silent ground truth", file=sys.stderr)
        for item in false_positives:
            bug = item.bug
            print(f"  {bug.path}:{bug.line}: {bug.code} for {bug.bug}", file=sys.stderr)

    silent = [item for item in expected_silent if item not in false_positives]
    silent_false_negatives = sum(1 for item in silent if item.silent_is_false_negative)
    silent_true_negatives = len(silent) - silent_false_negatives

    return BenchmarkResult(
        checked=True,
        true_positives=len(expected) - len(missing),
        false_negatives=len(missing) + silent_false_negatives,
        true_negatives=silent_true_negatives,
        false_positives=len(false_positives),
    )


def aggregate_kind(
    kind: str, benches: list[Path], list_unclassified: bool = False
) -> AggregateResult:
    checked = 0
    true_positives = false_negatives = true_negatives = false_positives = 0
    for bench_dir in benches:
        result = check_benchmark(bench_dir, {kind}, list_unclassified)
        if result.checked:
            checked += 1
        true_positives += result.true_positives
        false_negatives += result.false_negatives
        true_negatives += result.true_negatives
        false_positives += result.false_positives

    return AggregateResult(
        kind=kind,
        checked=checked,
        true_positives=true_positives,
        false_negatives=false_negatives,
        true_negatives=true_negatives,
        false_positives=false_positives,
    )


def print_result(result: AggregateResult) -> None:
    print(f"{result.kind}:")
    print(f"  checked benchmarks: {result.checked}")
    print(f"  true positives: {result.true_positives}")
    print(f"  false negatives: {result.false_negatives}")
    print(f"  true negatives: {result.true_negatives}")
    print(f"  false positives: {result.false_positives}")


def main() -> int:
    args = build_cli().parse_args()

    version = shellcheck_version()
    if version != EXPECTED_VERSION:
        print(
            f"WARNING: expected ShellCheck {EXPECTED_VERSION}, found {version or 'unknown'}",
            file=sys.stderr,
        )

    benches = sorted(p for p in args.benchmarks.iterdir() if p.is_dir())
    if args.only:
        benches = [p for p in benches if p.name == args.only]

    kinds = args.kind or ["buggy", "fixed", "buggy_variant"]
    results = [aggregate_kind(kind, benches, args.list_unclassified) for kind in kinds]
    for index, result in enumerate(results):
        if index > 0:
            print()
        print_result(result)

    return 1 if any(r.false_negatives or r.false_positives for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
