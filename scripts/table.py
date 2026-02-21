import pandas as pd
import sys
import re
import argparse
import subprocess
from pathlib import Path
from collections import Counter
from bug_depth_stats import compute_script_metrics
from benchmark_metadata import BENCHMARK_NAMES, benchmark_key, short_name

EPSILON = 1e-3
ROOT_DIR = Path(__file__).resolve().parents[1]

parser = argparse.ArgumentParser()
parser.add_argument(
    "--appendix",
    action="store_true",
    help="Output full table with all benchmark rows (no omitted-lines summary).",
)
parser.add_argument(
    "--results_csv",
    type=str,
    default="results/results.csv",
    help="Path to results CSV (default: results/results.csv).",
)
args = parser.parse_args()

results_path = args.results_csv
results = pd.read_csv(results_path)
buggy_results = results[results["kind"] == "buggy"].copy()
fixed_results = results[results["kind"] == "fixed"].copy()
loc_cache_path = Path("results/benchmark_loc.csv")
precompute_loc_script = Path(__file__).with_name("precompute_loc_cache.py")

if not loc_cache_path.exists():
    try:
        subprocess.run(
            [
                sys.executable,
                str(precompute_loc_script),
                "--results_csv",
                results_path,
                "--output_csv",
                str(loc_cache_path),
            ],
            check=True,
        )
    except Exception as exc:
        print(
            f"Failed to create LoC cache at {loc_cache_path}: {exc}",
            file=sys.stderr,
        )
        sys.exit(1)

loc_cache_df = pd.read_csv(loc_cache_path)


def benchmark_suffix(path):
    p = Path(str(path))
    parts = p.parts
    if "benchmarks" in parts:
        idx = parts.index("benchmarks")
        return str(Path(*parts[idx:]))
    return str(p)


def resolve_benchmark_path(path):
    p = Path(str(path))
    if p.exists():
        return p

    if not p.is_absolute():
        candidate = ROOT_DIR / p
        if candidate.exists():
            return candidate

    parts = p.parts
    if "benchmarks" in parts:
        idx = parts.index("benchmarks")
        candidate = ROOT_DIR / Path(*parts[idx:])
        if candidate.exists():
            return candidate

    if p.is_absolute():
        return p
    return ROOT_DIR / p


def benchmark_lookup_keys(path):
    p = Path(str(path))
    resolved = resolve_benchmark_path(path)
    keys = [
        str(p),
        benchmark_suffix(p),
        str(resolved),
        benchmark_suffix(resolved),
    ]
    # Preserve order, drop duplicates.
    return list(dict.fromkeys(keys))


loc_by_benchmark = {}
for _, row in loc_cache_df.iterrows():
    bm_path = row["benchmark"]
    loc = int(row["loc"])
    for key in benchmark_lookup_keys(bm_path):
        loc_by_benchmark[key] = loc

def parse_issue_list(value):
    if pd.isna(value):
        return []
    return [item.strip() for item in str(value).split(";") if item and item.strip()]

def count_matches(expected_issues, actual_issues):
    expected_counter = Counter(expected_issues)
    actual_counter = Counter(actual_issues)
    return sum(min(expected_counter[issue], actual_counter[issue]) for issue in expected_counter)

def feature_marks(feature_names):
    marks = []
    if "WE" in feature_names:
        marks.append(r"\WE")
    if "CS" in feature_names:
        marks.append(r"\SP")
    if "FS" in feature_names:
        marks.append(r"\FS")
    if "SE" in feature_names:
        marks.append(r"\SE")
    return " ".join(marks) if marks else "-"

def parse_issue_lines(issues):
    lines = []
    for issue in issues:
        match = re.match(r"^L([0-9]+):", issue)
        if match:
            lines.append(int(match.group(1)))
    return lines


def approx(value, fmt=".1f"):
    return rf"$\sim${value:{fmt}}"

names = BENCHMARK_NAMES

sources = {
    "high_profile/c00-steam": r"\cite{steambugissue}",
    "high_profile/c01-bumblebee": r"\cite{bumblebeebugissue}",
    "high_profile/w00-itunes": r"\cite{itunesbugpost}",
    "high_profile/w01-squid": r"\cite{squidbugreport}",
    "high_profile/c02-n": r"\cite{nbugissue}",
    "high_profile/c03-backup_manager": r"\cite{backupmanagerbugcommit}",

    "milestone_1/const_loop": r"\cite{stackexchangewhileloopdeletesallfiles}",
    "milestone_1/loop_once-useless_test": r"\cite{stackoverflowreplaceinfiles}",
    "milestone_1/unset_var_1": r"\cite{ohmyzshbugfix}",
    "milestone_2/rm_root": r"\cite{stackoverflowdeletedatabase}",
    "web_forums/rm_root_2": r"\cite{shellscriptrmrf}",
    "commits/debootstrap": r"\cite{debian:debootstrap:bug}",

    "simple_fs/overwrite_file": r"\cite{stackoverflow:mvbug}",

    # Rest of citations
    "commits/const_cond": r"\cite{benchmark:commits:const-cond}",
    "commits/debootstrap_2": r"\cite{benchmark:commits:debootstrap-2}",
    "commits/ignored_command_v": r"\cite{benchmark:commits:ignored-command-v}",
    "commits/makefile": r"\cite{benchmark:commits:makefile}",
    "commits/unset_func": r"\cite{benchmark:commits:unset-func}",
    "commits/unset_var_2": r"\cite{benchmark:commits:unset-var-2}",
    "commits/unset_var_3": r"\cite{benchmark:commits:unset-var-3}",
    "commits/unset_var_5": r"\cite{benchmark:commits:unset-var-5}",
    "commits/unset_var_set_u_1": r"\cite{benchmark:commits:unset-var-set-u-1}",
    "commits/unset_var_set_u_2": r"\cite{benchmark:commits:unset-var-set-u-2}",
    "milestone_1/redir_to_func-redir_to_func": r"\cite{benchmark:milestone-1:redir-to-func-redir-to-func}",
    "milestone_2/loop_once": r"\cite{benchmark:milestone-2:loop-once}",
    "milestone_2/loop_once-loop_once": r"\cite{benchmark:milestone-2:loop-once-loop-once}",
    "milestone_2/unset_var-const_if-dead_code": r"\cite{benchmark:milestone-2:unset-var-const-if-dead-code}",
    "simple_fs/access_after_mv": r"\cite{benchmark:simple-fs:access-after-mv}",
    "simple_fs/access_del_resource": r"\cite{benchmark:simple-fs:access-del-resource}",
    "simple_fs/cd_into_file": r"\cite{benchmark:simple-fs:cd-into-file}",
    "simple_fs/overwrite_file_2": r"\cite{benchmark:simple-fs:overwrite-file-2}",
    "simple_fs/overwrite_file_3": r"\cite{benchmark:simple-fs:overwrite-file-3}",
    "simple_fs/overwrite_file_4": r"\cite{benchmark:simple-fs:overwrite-file-4}",
    "web_forums/capturing_empty_output": r"\cite{benchmark:web-forums:capturing-empty-output}",
    "web_forums/unexpected_stdin": r"\cite{benchmark:web-forums:unexpected-stdin}",
    "web_forums/unset_var": r"\cite{benchmark:web-forums:unset-var}",
    "web_forums/unset_var-cmd_always_fails": r"\cite{benchmark:web-forums:unset-var-cmd-always-fails}",
}


descriptions = {
    "high_profile/c00-steam": r"Path traversal to \sh{/*} loss",
    "high_profile/c01-bumblebee": r"Deletes \sh{/usr}",
    "high_profile/w00-itunes": r"Deletes drive",
    "high_profile/w01-squid": r"Unknown \sh{rm} target",
    "high_profile/c02-n": r"Deletes \sh{/usr/local/*}",
    "high_profile/c03-backup_manager": r"Bad \sh{$?} check to data loss",

    "milestone_1/const_loop": r"Constant \sh{while} condition",
    "milestone_1/loop_once-useless_test": r"Run-once \sh{for} loop",
    "milestone_1/unset_var_1": r"Unset variable in \sh{echo}",
    "milestone_2/rm_root": r"Typo causes DB loss",
    "web_forums/rm_root_2": r"Failed \sh{mktemp} to data loss",
    "commits/debootstrap": r"Empty \sh{$2} to \sh{cwd} loss",

    "simple_fs/overwrite_file": r"Data loss from \sh{xargs mv}",

    "milestone_1/redir_to_func-redir_to_func": r"Redirect to function",
    "web_forums/unset_var-cmd_always_fails": r"Always empty \sh{mkdir} arg",
    "web_forums/capturing_empty_output": r"Captures \sh{mkdir} output",
    "web_forums/claude_wipe": r"Agent wipes \sh{$HOME}",
    "web_forums/unexpected_stdin": r"Empty \sh{$1} to stuck program",
    "web_forums/unset_var": r"Unset \sh{$bar} used",
    "simple_fs/access_after_mv": r"Uses dir after moving it",
    "simple_fs/cd_into_file": r"May \sh{cd} into regular file",
    "simple_fs/access_del_resource": r"Move from deleted dir",
    "simple_fs/overwrite_file_4": r"Generated file overwrite",
    "simple_fs/overwrite_file_3": r"File overwrite",
    "simple_fs/overwrite_file_2": r"Renames to same file",
    "milestone_2/unset_var-const_if-dead_code": r"Constant \sh{if}",
    "milestone_2/loop_once": r"Comma not in \sh{IFS}",
    "milestone_2/loop_once-loop_once": r"Disabled glob",
    "commits/ignored_command_v": r"Use missing shell",
    "commits/unset_func": r"Undefined function",
    "commits/const_cond": r"Unreachable \sh{$?} branch",
    "commits/makefile": r"Unset path to \sh{rm -rf /}",
    "commits/cp_nonexistent": r"Copies missing file to itself",
    "commits/unset_var_3": r"Unset var used in test",
    "commits/unset_var_2": r"Unset \sh{$file} used in file check",
    "commits/unset_var_5": r"Unset var used for download",
    "commits/debootstrap_2": r"Deletes user-supplied dir",
    "commits/unset_var_set_u_1": r"Abort check from \sh{set -u}",
    "commits/unset_var_set_u_2": r"Var self-append break",
}

# WE: word expansion
# CS: command specs
# FS: file system
# SE: symbolic execution
features = {
    "commits/const_cond": ["SE"], # SE to reason about set -e, the test semantics and condition being constant
    "commits/debootstrap": ["WE", "CS"], # WE to reason about the various parameter expansions, CS to reason about rm
    "commits/debootstrap_2": ["WE", "CS"], # WE to reason about the various parameter expansions, CS to reason about rm
    "commits/ignored_command_v": ["CS"], # CS to reason about command -v and env
    "commits/makefile": ["WE", "CS"], # WE to reason about variable expansion, CS to reason about rm
    "commits/unset_func": ["SE"], # SE to reason about the unset function call
    "commits/unset_var_2": ["WE"], # WE to reason about the unset variable
    "commits/unset_var_3": ["WE"], # WE to reason about the unset variable
    "commits/unset_var_5": ["WE"], # WE to reason about the unset variable
    "commits/unset_var_set_u_1": ["WE"], # WE to reason about the unset variable
    "commits/unset_var_set_u_2": ["WE"], # WE to reason about the unset variable

    "high_profile/c00-steam": ["WE", "CS"],
    "high_profile/c01-bumblebee": ["CS"],
    "high_profile/w00-itunes": ["WE", "CS"],
    "high_profile/w01-squid": ["CS"],
    "high_profile/c02-n": ["WE", "FS", "CS"],
    "high_profile/c03-backup_manager": ["CS"],

    "milestone_1/const_loop": ["WE", "SE"], # WE to determine what the condition is, SE to determine that it's const
    "milestone_1/loop_once-useless_test": ["WE", "SE"], # WE to determine what the iteree is, SE to determine that it causes a for to loop once
    "milestone_1/redir_to_func-redir_to_func": ["SE"], # SE to determine that the redirection is to a function
    "milestone_1/unset_var_1": ["WE"], # WE to determine that the variable is unset

    "milestone_2/loop_once": ["WE", "SE"], # WE to determine what the iteree is, SE to determine that it causes a for to loop once
    "milestone_2/loop_once-loop_once": ["WE", "SE"], # WE to determine what the iteree is, SE to determine that it causes a for to loop once
    "milestone_2/rm_root": ["CS", "WE"], # CS to determine dangerous rm, WE to determine unbound variables
    "milestone_2/unset_var-const_if-dead_code": ["WE"], # WE to detect unbound (the other bugs are oos)

    "simple_fs/access_after_mv": ["CS", "FS"], # CS to reason about mv, FS to reason about file access
    "simple_fs/access_del_resource": ["WE", "CS", "FS", "SE"], # WE to reason about the loop condition, SE to reason about the loop, CS to reason about rm, FS to reason about file access
    "simple_fs/cd_into_file": ["CS", "FS"], # CS to reason about cd, FS to reason about the file
    "simple_fs/overwrite_file": ["SE", "FS"], # SE to reason about redirection, FS to reason about the file
    "simple_fs/overwrite_file_2": ["WE", "SE", "CS", "FS"], # WE to reason about the iteree, SE to reason about the for loop, CS to reason about rm, FS to reason about overwrites
    "simple_fs/overwrite_file_3": ["SE", "CS", "FS"], # SE to reason about the pipeline and the while loop, CS to reason about cp, FS to reason about overwrites
    "simple_fs/overwrite_file_4": ["WE", "SE", "FS"], # WE to reason about the expansion of the filename, SE to reason about redirection and about all expansions being the same file, FS to reason about overwrites

    "web_forums/capturing_empty_output": ["CS", "SE"], # SE to reason about the subshell, CS to reason about mkdir not having output
    "web_forums/rm_root_2": ["WE", "CS"], # WE to reason about unbound variable, CS to reason about rm
    "web_forums/unexpected_stdin": ["CS", "SE"], # CS to reason about grep, SE to compare specs across traces
    "web_forums/unset_var": ["WE"], # WE to reason about unbound variable
    "web_forums/unset_var-cmd_always_fails": ["WE", "CS"], # WE to reason about unbound variable, CS to reason about test command and mkdir command
}

get_bm_name = benchmark_key

# Default (non-appendix) table keeps the original curated subset.
DEFAULT_TABLE_SUBSET = {
    "high_profile/c00-steam",
    "high_profile/c01-bumblebee",
    "high_profile/w00-itunes",
    "high_profile/w01-squid",
    "high_profile/c02-n",
    "high_profile/c03-backup_manager",
    "milestone_1/const_loop",
    "milestone_1/loop_once-useless_test",
    "milestone_1/unset_var_1",
    "milestone_2/rm_root",
    "web_forums/rm_root_2",
    "commits/debootstrap",
    "simple_fs/overwrite_file",
}

def get_loc(path):
    for key in benchmark_lookup_keys(path):
        if key in loc_by_benchmark:
            return loc_by_benchmark[key]
    key = str(path)
    if key not in loc_by_benchmark:
        print(
            f"Missing LoC for {key} in {loc_cache_path}. "
            f"Re-run: python scripts/precompute_loc_cache.py",
            file=sys.stderr,
        )
        return 0
    return loc_by_benchmark[key]

depth_metrics_cache = {}
def get_depth_metrics(path):
    resolved = str(resolve_benchmark_path(path))
    if resolved not in depth_metrics_cache:
        try:
            lines = Path(resolved).read_text(encoding="utf-8", errors="surrogateescape").splitlines()
            depth_metrics_cache[resolved] = compute_script_metrics(lines, resolved)
        except Exception as e:
            print(f"Failed to compute depth metrics for {resolved}: {e}", file=sys.stderr)
            depth_metrics_cache[resolved] = {
                "total_lines": 0,
                "depth_at_line": [0],
                "bfs_nodes_before_line": [0],
                "statements_before_line": [0],
                "final_depth": 0,
                "final_bfs_nodes_seen": 0,
                "final_statements_seen": 0,
            }
    return depth_metrics_cache[resolved]

def deepest_bug_depth(path, expected_issues):
    bug_lines = parse_issue_lines(expected_issues)
    if not bug_lines:
        return 0

    metrics = get_depth_metrics(path)
    total_lines = metrics["total_lines"]
    bfs_nodes_before_line = metrics.get("bfs_nodes_before_line", [0])
    fallback_bfs = metrics.get("final_bfs_nodes_seen", 0)

    max_bfs_nodes = max(
        bfs_nodes_before_line[line] if 1 <= line <= total_lines else fallback_bfs
        for line in bug_lines
    )
    return max_bfs_nodes

fixed_actual_by_bm = {}
for _, row in fixed_results.iterrows():
    bm_name = get_bm_name(row["benchmark"])
    fixed_actual_by_bm.setdefault(bm_name, set()).update(parse_issue_list(row["actual_results"]))

# For each buggy benchmark: true iff fixed run does not report any corresponding buggy issue.
fixed_clears_bug_by_bm = {}
for _, row in buggy_results.iterrows():
    bm_name = get_bm_name(row["benchmark"])
    buggy_expected = set(parse_issue_list(row["expected_results"]))
    fixed_actual = fixed_actual_by_bm.get(bm_name, set())
    fixed_clears_bug_by_bm[bm_name] = len(buggy_expected & fixed_actual) == 0

def create_table_line(result, allow_fallback=False):
    path, time = result["benchmark"], result["time"]
    # Grab just the folder and benchmark subfolder
    bm_name = get_bm_name(path)
    bm_short_id = short_name(path, default=bm_name)
    name = names.get(bm_name, None)
    description = descriptions.get(bm_name, None)
    if name is None:
        if not allow_fallback:
            return
        name = bm_name.replace("_", r"\_")
    if description is None:
        if not allow_fallback:
            return
        description = ""
    time = f"{time:.2f}s" if time > EPSILON else "<1ms"
    loc = get_loc(path)
    source = sources.get(bm_name, "")
    expected_issues = parse_issue_list(result["expected_results"])
    actual_issues = parse_issue_list(result["actual_results"])
    n_bugs = len(expected_issues)
    detected = count_matches(expected_issues, actual_issues)
    max_bfs_nodes = deepest_bug_depth(path, expected_issues)
    depth_cell = f"{max_bfs_nodes}"
    fixed_clears_bug = fixed_clears_bug_by_bm.get(bm_name, False)
    fixed_mark = r"\checkmark" if fixed_clears_bug else ""
    feature_mark = feature_marks(features.get(bm_name, []))

    bugs_detected_cell = f"{detected}/{n_bugs}"
    return f"{bm_short_id} & {name} & {description} & {bugs_detected_cell} & {fixed_mark} & {time} & {feature_mark} & {loc} & {depth_cell} & {source}  \\\\"

rest_of_benchmarks = []

print(r"""
    \begin{tabular}{lllrcrcrrl}
    \toprule
    \textbf{ID} & \textbf{Name} & \textbf{Description} & \textbf{D?/\#B} & \textbf{F?} & \textbf{$t$} & \textbf{F} & \textbf{LoC} & \textbf{$\downarrow$} & \textbf{Source} \\
    \midrule
"""
)

for result in buggy_results.to_dict(orient="records"):
    bm_name = get_bm_name(result["benchmark"])
    if not args.appendix and bm_name not in DEFAULT_TABLE_SUBSET:
        rest_of_benchmarks.append(result)
        continue

    line = create_table_line(result, allow_fallback=args.appendix)
    if line:
        print(line)
    else:
        rest_of_benchmarks.append(result)

if not args.appendix and rest_of_benchmarks:
    avg_time_rest = sum(r["time"] for r in rest_of_benchmarks) / len(rest_of_benchmarks)
    time_avg_cell = f"{approx(avg_time_rest, '.2f')}s"

    locs = [get_loc(r["benchmark"]) for r in rest_of_benchmarks]
    avg_loc_rest = sum(locs) / len(locs)
    loc_avg_cell = approx(avg_loc_rest, ".1f")

    total_bugs_rest = sum(len(parse_issue_list(r["expected_results"])) for r in rest_of_benchmarks)
    detected_bugs_rest = sum(
        count_matches(parse_issue_list(r["expected_results"]), parse_issue_list(r["actual_results"]))
        for r in rest_of_benchmarks
    )
    depth_values_rest = [
        deepest_bug_depth(r["benchmark"], parse_issue_list(r["expected_results"]))
        for r in rest_of_benchmarks
    ]
    avg_depth_rest = sum(depth_values_rest) / len(depth_values_rest)
    depth_avg_rest_cell = approx(avg_depth_rest, ".1f")
    total = len(rest_of_benchmarks)
    fixed_clear_count = sum(1 for r in rest_of_benchmarks if fixed_clears_bug_by_bm.get(get_bm_name(r["benchmark"]), False))
    fixed_clear_rate = f"{fixed_clear_count}/{total}"

    we_count = sum(1 for r in rest_of_benchmarks if "WE" in features.get(get_bm_name(r["benchmark"]), []))
    cs_count = sum(1 for r in rest_of_benchmarks if "CS" in features.get(get_bm_name(r["benchmark"]), []))
    fs_count = sum(1 for r in rest_of_benchmarks if "FS" in features.get(get_bm_name(r["benchmark"]), []))
    feature_count_marks = f"{we_count} \\WE/{cs_count} \\SP/{fs_count} \\FS"

    print(rf""" & \emph{{More buggy scripts}} &  & {detected_bugs_rest}/{total_bugs_rest} & {fixed_clear_rate} & {time_avg_cell} & {feature_count_marks} & {loc_avg_cell} & {depth_avg_rest_cell} & \cref{{sec:full-ds}} \\""")
    print(r"\hspace{.5em}\dots & & & & & & & & & \\")

# Print summary line across all benchmarks
locs = [get_loc(r["benchmark"]) for r in buggy_results.to_dict(orient="records")]
avg_loc_total = sum(locs) / len(locs)
loc_avg_total_cell = approx(avg_loc_total, ".1f")

total = len(buggy_results)
total_bugs = sum(len(parse_issue_list(r["expected_results"])) for r in buggy_results.to_dict(orient="records"))
detected_bugs = sum(
    count_matches(parse_issue_list(r["expected_results"]), parse_issue_list(r["actual_results"]))
    for r in buggy_results.to_dict(orient="records")
)
depth_values_total = [
    deepest_bug_depth(r["benchmark"], parse_issue_list(r["expected_results"]))
    for r in buggy_results.to_dict(orient="records")
]
avg_depth_total = sum(depth_values_total) / len(depth_values_total)
depth_avg_total_cell = approx(avg_depth_total, ".1f")
fixed_clear_count = sum(1 for r in buggy_results.to_dict(orient="records") if fixed_clears_bug_by_bm.get(get_bm_name(r["benchmark"]), False))
fixed_clear_rate = f"{fixed_clear_count}/{total}"
times = [r["time"] for r in buggy_results.to_dict(orient="records")]
avg_time_total = sum(times) / len(times)
time_avg_total_cell = f"{approx(avg_time_total, '.2f')}s"

we_count = sum(1 for r in buggy_results.to_dict(orient="records") if "WE" in features.get(get_bm_name(r["benchmark"]), []))
cs_count = sum(1 for r in buggy_results.to_dict(orient="records") if "CS" in features.get(get_bm_name(r["benchmark"]), []))
fs_count = sum(1 for r in buggy_results.to_dict(orient="records") if "FS" in features.get(get_bm_name(r["benchmark"]), []))
feature_count_marks = f"{we_count} \\WE/{cs_count} \\SP/{fs_count} \\FS"

print(rf"""
\midrule
 & \textbf{{Total}} &  & {detected_bugs}/{total_bugs} & {fixed_clear_rate} & {time_avg_total_cell} & {feature_count_marks} & {loc_avg_total_cell} & {depth_avg_total_cell} &  \\ """)

print(r"""
\bottomrule
\end{tabular}
""")

# Print some stats about the benchmarks
total_benchmarks = len(buggy_results)
# Total bugs
total_bugs = (
    buggy_results["expected_results"]
    .fillna("")
    .where(lambda s: s != "")
    .dropna()
    .str.split(";")
    .map(len)
    .sum()
)
n_bugs = (
    buggy_results["expected_results"]
    .fillna("")
    .where(lambda s: s != "")
    .dropna()
    .str.split(";")
    .map(len)
)
bugs_avg = n_bugs.mean()

print(f"% Total benchmarks: {total_benchmarks}", file=sys.stderr)
print(f"% Total bugs: {total_bugs}", file=sys.stderr)
print(f"% Bugs per benchmark: ~{bugs_avg:.2f}", file=sys.stderr)

# Averages
avg_loc = sum(get_loc(r["benchmark"]) for r in buggy_results.to_dict(orient="records")) / total_benchmarks
avg_time = sum(r["time"] for r in buggy_results.to_dict(orient="records")) / total_benchmarks
print(f"% Average LoC: {avg_loc:.2f}", file=sys.stderr)
print(f"% Average time: {avg_time:.2f}s", file=sys.stderr)
