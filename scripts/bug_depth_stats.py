#!/usr/bin/env -S uv run python3
"""Compute per-bug-line depth and path-convolution proxy stats for benchmark scripts."""
import argparse
import csv
import math
import re
import statistics
import subprocess
import sys
from pathlib import Path

import yaml
import sash.parser
import shasta.ast_node as AST


def git_toplevel() -> Path:
    return Path(
        subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"], encoding="utf-8"
        ).strip()
    )


ROOT_DIR = git_toplevel()
DEFAULT_BENCHMARKS = ROOT_DIR / "benchmarks"
DEFAULT_OUTPUT = ROOT_DIR / "results" / "bug_depth_stats.csv"

OPEN_KEYWORDS = {"if", "for", "while", "until", "case", "select"}
CLOSE_KEYWORDS = {"fi", "done", "esac"}
FUNC_DECL_RE = re.compile(r"^\s*(?:function\s+)?[A-Za-z_][A-Za-z0-9_]*\s*\(\s*\)\s*(\{)?")
TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|(?<!\$)\(|\)")
ONLY_CLOSE_BRACE_RE = re.compile(r"^\s*}\s*;?\s*$")

CONTROL_NODE_TYPES = (
    AST.ForNode,
    AST.WhileNode,
    AST.IfNode,
    AST.CaseNode,
    AST.SubshellNode,
    AST.DefunNode,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compute bug-line depth stats from benchmark ground truths. "
            "Outputs per-bug-line CSV and aggregate summary."
        )
    )
    parser.add_argument(
        "--benchmarks",
        type=Path,
        default=DEFAULT_BENCHMARKS,
        help=f"Benchmarks root directory (default: {DEFAULT_BENCHMARKS})",
    )
    parser.add_argument(
        "--output-csv",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Output CSV file (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--include-not-integrated",
        action="store_true",
        help="Include benchmarks under _not_integrated directories (default: false)",
    )
    parser.add_argument(
        "--only",
        type=str,
        default=".*",
        help="Regex to filter benchmark directory paths (default: all)",
    )
    return parser.parse_args()


def is_comment_start(prev_char: str | None) -> bool:
    return prev_char is None or prev_char.isspace() or prev_char in ";|&(){}"


def strip_comments_and_strings(line: str) -> str:
    out = []
    in_single = False
    in_double = False
    escaped = False
    prev_char: str | None = None

    for ch in line:
        if in_single:
            if ch == "'":
                in_single = False
            out.append(" ")
            prev_char = ch
            continue

        if in_double:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_double = False
            out.append(" ")
            prev_char = ch
            continue

        if ch == "'":
            in_single = True
            out.append(" ")
            prev_char = ch
            continue

        if ch == '"':
            in_double = True
            out.append(" ")
            prev_char = ch
            continue

        if ch == "#" and is_comment_start(prev_char):
            break

        out.append(ch)
        prev_char = ch

    return "".join(out)


def ends_with_unescaped_backslash(s: str) -> bool:
    trailing = len(s) - len(s.rstrip("\\"))
    return trailing > 0 and trailing % 2 == 1


def is_statement(logical_line: str) -> bool:
    s = logical_line.strip()
    if not s:
        return False

    s = re.sub(r";+\s*$", "", s).strip()
    if not s:
        return False

    lowered = s.lower()
    if lowered in {"fi", "done", "esac", "then", "do", "else", "}", ";;"}:
        return False
    if ONLY_CLOSE_BRACE_RE.match(s):
        return False
    return True


def _node_line_number(node: AST.AstNode, fallback_line: int | None = None) -> int | None:
    line_number = getattr(node, "line_number", None)
    if isinstance(line_number, int) and line_number > 0:
        return line_number
    return fallback_line


def _collect_depth_from_ast(
    node: AST.AstNode,
    current_depth: int,
    depth_by_line: dict[int, int],
    fallback_line: int | None = None,
) -> None:
    is_control = isinstance(node, CONTROL_NODE_TYPES)
    node_depth = current_depth + 1 if is_control else current_depth

    line_number = _node_line_number(node, fallback_line)
    if line_number is not None and line_number > 0:
        depth_by_line[line_number] = max(depth_by_line.get(line_number, 0), node_depth)

    next_depth = node_depth if is_control else current_depth

    match node:
        case AST.PipeNode():
            for item in node.items:
                _collect_depth_from_ast(item, next_depth, depth_by_line)
        case AST.CommandNode():
            return
        case AST.SubshellNode():
            _collect_depth_from_ast(node.body, next_depth, depth_by_line)
        case AST.AndNode():
            _collect_depth_from_ast(node.left_operand, next_depth, depth_by_line)
            _collect_depth_from_ast(node.right_operand, next_depth, depth_by_line)
        case AST.OrNode():
            _collect_depth_from_ast(node.left_operand, next_depth, depth_by_line)
            _collect_depth_from_ast(node.right_operand, next_depth, depth_by_line)
        case AST.SemiNode():
            _collect_depth_from_ast(node.left_operand, next_depth, depth_by_line)
            _collect_depth_from_ast(node.right_operand, next_depth, depth_by_line)
        case AST.NotNode():
            _collect_depth_from_ast(node.body, next_depth, depth_by_line)
        case AST.RedirNode():
            _collect_depth_from_ast(node.node, next_depth, depth_by_line)
        case AST.BackgroundNode():
            _collect_depth_from_ast(node.node, next_depth, depth_by_line)
        case AST.DefunNode():
            _collect_depth_from_ast(node.body, next_depth, depth_by_line)
        case AST.ForNode():
            _collect_depth_from_ast(node.body, next_depth, depth_by_line)
        case AST.WhileNode():
            _collect_depth_from_ast(node.test, next_depth, depth_by_line)
            _collect_depth_from_ast(node.body, next_depth, depth_by_line)
        case AST.IfNode():
            _collect_depth_from_ast(node.cond, next_depth, depth_by_line)
            _collect_depth_from_ast(node.then_b, next_depth, depth_by_line)
            if node.else_b is not None:
                _collect_depth_from_ast(node.else_b, next_depth, depth_by_line)
        case AST.CaseNode():
            for case in node.cases:
                _collect_depth_from_ast(case["cbody"], next_depth, depth_by_line)
        case _:
            return


def compute_parser_depth(lines: list[str], script_path: str) -> list[int]:
    depth_at_line = [0] * (len(lines) + 1)
    try:
        wrapped_nodes = sash.parser.parse_shell_script(script_path)
    except Exception:
        return depth_at_line

    depth_by_line: dict[int, int] = {}
    for wrapped in wrapped_nodes:
        fallback_line = wrapped.get_line_number()
        _collect_depth_from_ast(
            wrapped.ast_node,
            current_depth=0,
            depth_by_line=depth_by_line,
            fallback_line=fallback_line,
        )

    for line_number, depth in depth_by_line.items():
        if 1 <= line_number <= len(lines):
            depth_at_line[line_number] = max(depth_at_line[line_number], depth)

    return depth_at_line


def compute_script_metrics(lines: list[str], script_path: str | None = None) -> dict:
    total_lines = len(lines)
    loc = sum(1 for line in lines if strip_comments_and_strings(line).strip())

    depth_at_line: list[int] = [0] * (total_lines + 1)  # 1-based
    statements_before_line: list[int] = [0] * (total_lines + 1)  # 1-based

    depth = 0
    statements_seen = 0
    pending_func_open = False
    continuation_parts: list[str] = []

    for i, raw_line in enumerate(lines, start=1):
        depth_at_line[i] = depth
        statements_before_line[i] = statements_seen

        clean_line = strip_comments_and_strings(raw_line)
        stripped = clean_line.strip()

        if pending_func_open and re.match(r"^\s*{\s*$", stripped):
            depth += 1
            pending_func_open = False

        func_match = FUNC_DECL_RE.match(stripped)
        if func_match:
            if func_match.group(1) is not None:
                depth += 1
            else:
                pending_func_open = True

        for token in TOKEN_RE.findall(stripped):
            low = token.lower()
            if token == ")":
                depth = max(depth - 1, 0)
            elif low in CLOSE_KEYWORDS:
                depth = max(depth - 1, 0)
            elif token == "(":
                depth += 1
            elif low in OPEN_KEYWORDS:
                depth += 1

        if ONLY_CLOSE_BRACE_RE.match(stripped):
            depth = max(depth - 1, 0)
            pending_func_open = False

        if not stripped:
            continue

        if continuation_parts:
            continuation_parts.append(stripped)
        else:
            continuation_parts = [stripped]

        if ends_with_unescaped_backslash(stripped):
            continuation_parts[-1] = continuation_parts[-1][:-1].rstrip()
            continue

        logical_line = " ".join(part for part in continuation_parts if part)
        continuation_parts = []
        if is_statement(logical_line):
            statements_seen += 1

    if continuation_parts:
        logical_line = " ".join(part for part in continuation_parts if part)
        if is_statement(logical_line):
            statements_seen += 1

    if script_path is not None:
        parser_depth_at_line = compute_parser_depth(lines, script_path)
        for line_number in range(1, total_lines + 1):
            depth_at_line[line_number] = max(
                depth_at_line[line_number], parser_depth_at_line[line_number]
            )

    return {
        "lines": lines,
        "total_lines": total_lines,
        "loc": loc,
        "depth_at_line": depth_at_line,
        "statements_before_line": statements_before_line,
        "final_depth": depth,
        "final_statements_seen": statements_seen,
    }


def iter_benchmark_dirs(
    benchmarks_root: Path, include_not_integrated: bool
) -> list[Path]:
    benchmark_dirs = []
    for category_dir in sorted(benchmarks_root.iterdir()):
        if not category_dir.is_dir():
            continue
        if not include_not_integrated and "_not_integrated" in category_dir.parts:
            continue
        for bench_dir in sorted(category_dir.iterdir()):
            if not bench_dir.is_dir():
                continue
            if not include_not_integrated and "_not_integrated" in bench_dir.parts:
                continue
            if (bench_dir / "info.yaml").exists():
                benchmark_dirs.append(bench_dir)
    return benchmark_dirs


def percentile_nearest_rank(values: list[float], p: float) -> float:
    if not values:
        return float("nan")
    sorted_values = sorted(values)
    rank = max(1, math.ceil((p / 100.0) * len(sorted_values)))
    return sorted_values[rank - 1]


def summarize(values: list[float]) -> dict[str, float]:
    if not values:
        return {
            "min": float("nan"),
            "mean": float("nan"),
            "median": float("nan"),
            "p90": float("nan"),
            "max": float("nan"),
        }
    return {
        "min": min(values),
        "mean": statistics.fmean(values),
        "median": statistics.median(values),
        "p90": percentile_nearest_rank(values, 90.0),
        "max": max(values),
    }


def fmt_stats(stats: dict[str, float]) -> str:
    return (
        f"min={stats['min']:.2f}, mean={stats['mean']:.2f}, "
        f"median={stats['median']:.2f}, p90={stats['p90']:.2f}, max={stats['max']:.2f}"
    )


def main() -> None:
    args = parse_args()
    bench_filter = re.compile(args.only)

    if not args.benchmarks.exists():
        print(f"Benchmark root does not exist: {args.benchmarks}", file=sys.stderr)
        sys.exit(1)

    rows: list[dict] = []
    script_cache: dict[Path, dict] = {}
    warnings = 0

    benchmark_dirs = iter_benchmark_dirs(args.benchmarks, args.include_not_integrated)
    for bench_dir in benchmark_dirs:
        if not bench_filter.search(bench_dir.as_posix()):
            continue

        info_path = bench_dir / "info.yaml"
        try:
            info = yaml.safe_load(info_path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"[WARN] Failed to read {info_path}: {exc}", file=sys.stderr)
            warnings += 1
            continue

        bugs_meta = info.get("bugs", {}) if isinstance(info, dict) else {}
        ground_truths = info.get("ground_truths", []) if isinstance(info, dict) else []

        for gt in ground_truths:
            kind = gt.get("kind")
            gt_path = gt.get("path")
            gt_bugs = gt.get("bugs", {})

            if not isinstance(gt_bugs, dict) or not isinstance(gt_path, str):
                continue

            script_path = bench_dir / gt_path
            if not script_path.exists():
                print(f"[WARN] Missing script {script_path}", file=sys.stderr)
                warnings += 1
                continue

            if script_path not in script_cache:
                try:
                    lines = script_path.read_text(encoding="utf-8", errors="surrogateescape").splitlines()
                except Exception as exc:
                    print(f"[WARN] Failed to read script {script_path}: {exc}", file=sys.stderr)
                    warnings += 1
                    continue
                script_cache[script_path] = compute_script_metrics(lines, str(script_path))

            metrics = script_cache[script_path]
            total_lines = metrics["total_lines"]
            depth_at_line = metrics["depth_at_line"]
            statements_before_line = metrics["statements_before_line"]
            final_depth = metrics["final_depth"]
            final_statements_seen = metrics["final_statements_seen"]
            script_lines = metrics["lines"]

            for bug_id, bug_info in gt_bugs.items():
                line_numbers = bug_info.get("lines") or bug_info.get("regression_lines") or []
                bug_meta = bugs_meta.get(bug_id, {})
                bug_code = bug_meta.get("code", "")
                bug_desc = bug_meta.get("description", "")

                for bug_line in line_numbers:
                    if not isinstance(bug_line, int) or bug_line < 1:
                        continue

                    if 1 <= bug_line <= total_lines:
                        line_text = script_lines[bug_line - 1]
                        nesting_depth = depth_at_line[bug_line]
                        statements_before = statements_before_line[bug_line]
                    else:
                        line_text = ""
                        nesting_depth = final_depth
                        statements_before = final_statements_seen
                        print(
                            f"[WARN] Bug line {bug_line} out of range for {script_path}",
                            file=sys.stderr,
                        )
                        warnings += 1

                    line_percentile = bug_line / max(total_lines, 1)

                    rows.append(
                        {
                            "benchmark_dir": bench_dir.relative_to(ROOT_DIR).as_posix(),
                            "ground_truth_kind": kind,
                            "script_path": script_path.relative_to(ROOT_DIR).as_posix(),
                            "bug_id": bug_id,
                            "bug_code": bug_code,
                            "bug_description": bug_desc,
                            "bug_line": bug_line,
                            "line_text": line_text,
                            "nesting_depth": nesting_depth,
                            "statements_before": statements_before,
                            "loc": metrics["loc"],
                            "line_percentile": round(line_percentile, 6),
                        }
                    )

    args.output_csv.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "benchmark_dir",
        "ground_truth_kind",
        "script_path",
        "bug_id",
        "bug_code",
        "bug_description",
        "bug_line",
        "line_text",
        "nesting_depth",
        "statements_before",
        "loc",
        "line_percentile",
    ]
    with args.output_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    nesting_values = [float(r["nesting_depth"]) for r in rows]
    statement_values = [float(r["statements_before"]) for r in rows]

    print(f"% Scripts analyzed: {len(script_cache)}", file=sys.stderr)
    print(f"% Bug-line rows: {len(rows)}", file=sys.stderr)
    print(f"% Warnings: {warnings}", file=sys.stderr)
    print(f"% Nesting depth stats: {fmt_stats(summarize(nesting_values))}", file=sys.stderr)
    print(
        f"% Statements-before stats: {fmt_stats(summarize(statement_values))}",
        file=sys.stderr,
    )

    kinds = sorted({r["ground_truth_kind"] for r in rows if r["ground_truth_kind"] is not None})
    for kind in kinds:
        kind_rows = [r for r in rows if r["ground_truth_kind"] == kind]
        k_nesting = [float(r["nesting_depth"]) for r in kind_rows]
        k_statements = [float(r["statements_before"]) for r in kind_rows]
        print(f"% Kind={kind} rows: {len(kind_rows)}", file=sys.stderr)
        print(f"%   Nesting: {fmt_stats(summarize(k_nesting))}", file=sys.stderr)
        print(f"%   Statements-before: {fmt_stats(summarize(k_statements))}", file=sys.stderr)

    print(f"% CSV written: {args.output_csv}", file=sys.stderr)


if __name__ == "__main__":
    main()
