import pandas as pd
import sys
import re
import argparse
import subprocess
from pathlib import Path
from collections import Counter
import yaml
import bugdepth
from benchmark_metadata import (
    BENCHMARK_NAMES,
    WILD_BENCHMARK_DESCRIPTIONS,
    benchmark_key,
    short_name,
)

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
parser.add_argument(
    "--wild",
    action="store_true",
    help="Output only the in-the-wild script table (project, one-word purpose, citation).",
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

timing_by_benchmark = {}
timeout_sweep_dir = Path(results_path).resolve().parent / "timeout-sweep"
timing_csv_candidates = [
    timeout_sweep_dir / "results_t60_dfs_on.csv",
    timeout_sweep_dir / "results_t60.csv",
]
for timing_csv_path in timing_csv_candidates:
    if not timing_csv_path.exists():
        continue
    timing_df = pd.read_csv(timing_csv_path)
    if "kind" in timing_df.columns:
        timing_df = timing_df[timing_df["kind"] == "buggy"].copy()
    for _, row in timing_df.iterrows():
        bm_name = benchmark_key(row["benchmark"])
        timed_out = str(row.get("timed_out", "")).strip().lower() in {"1", "true", "yes"}
        timing_by_benchmark[bm_name] = {
            "time": float(row["time"]),
            "timed_out": timed_out,
        }
    break

def parse_issue_list(value):
    if pd.isna(value):
        return []
    return [item.strip() for item in str(value).split(";") if item and item.strip()]

def count_matches(expected_issues, actual_issues):
    expected_counter = Counter(expected_issues)
    actual_counter = Counter(actual_issues)
    return sum(min(expected_counter[issue], actual_counter[issue]) for issue in expected_counter)

def feature_marks(feature_names, optimistic_forking=False, targeted_pass=False):
    marks = []
    if "WE" in feature_names:
        marks.append(r"\WE")
    if "CS" in feature_names:
        marks.append(r"\SP")
    if "FS" in feature_names:
        marks.append(r"\FS")
    if targeted_pass:
        marks.append(r"\TE")
    marks_text = " ".join(marks) if marks else "-"
    return marks_text

def parse_issue_lines(issues):
    lines = []
    for issue in issues:
        match = re.match(r"^L([0-9]+):", issue)
        if match:
            lines.append(int(match.group(1)))
    return lines


def approx(value, fmt=".1f"):
    return rf"$\sim${value:{fmt}}"


def table_time_seconds(row):
    bm_name = get_bm_name(row["benchmark"])
    timing = timing_by_benchmark.get(bm_name)
    if timing is None:
        return float(row["time"])
    return float(timing["time"])


def table_time_cell(row):
    bm_name = get_bm_name(row["benchmark"])
    timing = timing_by_benchmark.get(bm_name)
    if timing is None:
        time = float(row["time"])
        return f"{time:.2f}s" if time > EPSILON else "<1ms"
    if timing["timed_out"]:
        return ">60s"
    time = float(timing["time"])
    return f"{time:.2f}s" if time > EPSILON else "<1ms"

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
    "commits/cp_nonexistent": r"\cite{benchmark:commits:cp-nonexistent}",
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
    "web_forums/accident": r"\cite{benchmark:web-forums:accident}",
    "web_forums/capturing_empty_output": r"\cite{benchmark:web-forums:capturing-empty-output}",
    "web_forums/claude2": r"\cite{benchmark:web-forums:claude-2}",
    "web_forums/claude3": r"\cite{benchmark:web-forums:claude-3}",
    "web_forums/claude4": r"\cite{benchmark:web-forums:claude-4}",
    "web_forums/claude5": r"\cite{benchmark:web-forums:claude-5}",
    "web_forums/claude6": r"\cite{benchmark:web-forums:claude-6}",
    "web_forums/claude_wipe": r"\cite{benchmark:web-forums:claude-wipe}",
    "web_forums/confused_mkdir": r"\cite{benchmark:web-forums:confused-mkdir}",
    "web_forums/delete_home_user": r"\cite{benchmark:web-forums:delete-home-user}",
    "web_forums/delete_slash": r"\cite{benchmark:web-forums:delete-slash}",
    "web_forums/empty_path": r"\cite{benchmark:web-forums:empty-path}",
    "web_forums/find_rm": r"\cite{benchmark:web-forums:find-rm}",
    "web_forums/for_mv": r"\cite{benchmark:web-forums:for-mv}",
    "web_forums/move_home": r"\cite{benchmark:web-forums:move-home}",
    "web_forums/posix2": r"\cite{benchmark:web-forums:posix-2}",
    "web_forums/replacement": r"\cite{benchmark:web-forums:replacement}",
    "web_forums/unexpected_stdin": r"\cite{benchmark:web-forums:unexpected-stdin}",
    "web_forums/unset_var": r"\cite{benchmark:web-forums:unset-var}",
    "web_forums/unset_var-cmd_always_fails": r"\cite{benchmark:web-forums:unset-var-cmd-always-fails}",
    "web_forums/sc_author": r"\cite{shellcheck:issue-910}",
    "web_forums/silly_q": r"\cite{benchmark:web-forums:silly-q}",
    "web_forums/troll": r"\cite{benchmark:web-forums:troll}",
    "web_forums/wrong_mkdir": r"\cite{benchmark:web-forums:wrong-mkdir}",
    "web_forums/wrong_mv": r"\cite{benchmark:web-forums:wrong-mv}",
    "web_forums/xargs_accident_rm": r"\cite{benchmark:web-forums:xargs-accident-rm}",
    "web_forums/xargs_del_files": r"\cite{benchmark:web-forums:xargs-del-files}",
}

WILD_PROJECT_NAMES = {
    "affine": "AFFiNE",
    "bashreduce": "BashReduce",
    "batocera.linux": "Batocera Linux",
    "caker": "Caker",
    "ch32-data": "ch32-data",
    "cli-1": "IPinfo CLI",
    "cli-2": "IPinfo CLI",
    "cosmos-omnibus": "Cosmos Omnibus",
    "crawl4ai": "Crawl4AI",
    "cs_notes": "CS Notes",
    "danghuangshang": "Danghuangshang",
    "dotfiles_ooloth-1": "Dotfiles",
    "dotfiles_ooloth-2": "Dotfiles",
    "dotfiles-1": "Dotfiles",
    "dotfiles-2": "Dotfiles",
    "edeliver": "Edeliver",
    "embree": "Embree",
    "facedetection_dsfd-1": "FaceDetection-DSFD",
    "facedetection_dsfd-2": "FaceDetection-DSFD",
    "facedetection_dsfd-3": "FaceDetection-DSFD",
    "ghorg": "Ghorg",
    "gloo_gateway_2.1_demo": "Gloo Gateway",
    "hasor": "Hasor",
    "la_capitaine_icon_theme": "La Capitaine Icon Theme",
    "libreelec.tv": "LibreELEC",
    "moby": "Moby",
    "multigres-1": "Multigres",
    "multigres-2": "Multigres",
    "netdata": "Netdata",
    "next.js": "Next.js",
    "node-1": "Base Node",
    "node-2": "Base Node",
    "openpilot-1": "Openpilot",
    "openpilot-2": "Openpilot",
    "opensc": "OpenSC",
    "p4c-1": "P4 Compiler",
    "p4c-2": "P4 Compiler",
    "p4c-3": "P4 Compiler",
    "p4c-4": "P4 Compiler",
    "p4c-5": "P4 Compiler",
    "pgbouncer": "PgBouncer",
    "plantsvszombies": "PlantsVsZombies Fan Game",
    "pytorch": "PyTorch",
    "rapidpro_docker": "RapidPro Docker",
    "serverless": "Serverless",
    "sourcerercc": "SourcererCC",
    "steamtools-1": "Watt SteamTools",
    "steamtools-2": "Watt SteamTools",
    "swiftenv": "Swiftenv",
    "tazpkg": "Tazpkg",
    "test_infra-1": "Kubernetes Test Infra",
    "test_infra-2": "Kubernetes Test Infra",
    "test_infra-3": "Kubernetes Test Infra",
    "theme_switcher": "Theme Switcher",
    "toolsave": "ToolSave",
    "v2m": "V2M",
    "ventoy-1": "Ventoy",
    "ventoy-2": "Ventoy",
    "vllm-1": "vLLM",
    "vllm-2": "vLLM",
    "whishper": "Whishper"
}

WILD_PROJECT_PURPOSES = {
    "AFFiNE": "Updater",
    "Base Node": "Setup",
    "BashReduce": "CLI",
    "Batocera Linux": "Init",
    "CS Notes": "Docs",
    "Caker": "Build",
    "Cosmos Omnibus": "Entrypoint",
    "Crawl4AI": "CI",
    "Danghuangshang": "Daemon",
    "Dotfiles": "Setup",
    "Edeliver": "Deploy",
    "Embree": "Build",
    "FaceDetection-DSFD": "Dataset",
    "Ghorg": "CI",
    "Gloo Gateway": "Uninstall",
    "Hasor": "Setup",
    "IPinfo CLI": "CLI",
    "Kubernetes Test Infra": "CI",
    "La Capitaine Icon Theme": "Theme",
    "LibreELEC": "Updater",
    "Moby": "CI",
    "Multigres": "Tooling",
    "Netdata": "Monitoring",
    "Next.js": "Deploy",
    "OpenSC": "Build",
    "Openpilot": "Setup",
    "P4 Compiler": "Build",
    "PgBouncer": "Test",
    "PlantsVsZombies Fan Game": "Build",
    "PyTorch": "CI",
    "RapidPro Docker": "Uninstall",
    "Serverless": "Installer",
    "SourcererCC": "Tooling",
    "Watt SteamTools": "Utility",
    "Tazpkg": "Package",
    "Theme Switcher": "Configuration",
    "ToolSave": "Uninstall",
    "V2M": "Uninstall",
    "Ventoy": "Boot",
    "vLLM": "CI",
    "Whishper": "Installer"
}

WILD_CURATED_ROWS = [
    ("pytorch", "PyTorch", "CI", 1),
    ("test_infra", "Kubernetes", "CI", 3),
    ("next.js", "Next.js", "CI", 1),
    ("p4-compiler", "P4 Compiler", "Build", 15),
    ("vllm-1", "vLLM", "CI", 4),
    ("affine", "AFFiNE", "Updater", 3),
    ("moby", "Moby", "CI", 2),
    # ("ghorg", "Ghorg", "CI", 2),
    # ("caker", "Caker", "Build", 1),
    # ("gloo-gateway-2.1-demo", "Gloo Gateway", "Uninstall", 1),
]

WILD_SOURCE_KEYS = {
    "BashReduce": r"\cite{bashreduce}",
    "Base Node": r"\cite{base-node}",
    "AFFiNE": r"\cite{affine}",
    "Batocera Linux": r"\cite{batocera-linux}",
    "Caker": r"\cite{caker}",
    "Cosmos Omnibus": r"\cite{cosmos-omnibus}",
    "Crawl4AI": r"\cite{crawl4ai}",
    "Danghuangshang": r"\cite{danghuangshang}",
    "Dotfiles": ["dotfiles-nicknisi", "dotfiles-gihrig"],
    "Gloo Gateway": r"\cite{gloo-gateway-2.1-demo}",
    "Ghorg": r"\cite{ghorg}",
    "Kubernetes Test Infra": r"\cite{kubernetes-test-infra}",
    "Moby": r"\cite{moby}",
    "Next.js": r"\cite{nextjs}",
    "OpenSC": r"\cite{opensc}",
    "Openpilot": r"\cite{openpilot}",
    "P4 Compiler": r"\cite{p4:comm:2014}",
    "PyTorch": r"\cite{pytorch}",
    "RapidPro Docker": r"\cite{rapidpro-docker}",
    "Serverless": r"\cite{serverless}",
    "Tazpkg": r"\cite{tazpkg}",
    "Theme Switcher": r"\cite{theme-switcher}",
    "ToolSave": r"\cite{toolsave}",
    "V2M": r"\cite{v2m}",
    "Ventoy": r"\cite{ventoy}",
    "vLLM": r"\cite{vllm}",
}


def render_wild_citation(project_name):
    citation = WILD_SOURCE_KEYS.get(project_name, "")
    if isinstance(citation, (list, tuple)):
        if not citation:
            return ""
        return r"\cite{" + ",".join(citation) + "}"
    if citation:
        return citation
    return ""


descriptions = {
    "high_profile/c00-steam": r"Failed \sh{cd} to \sh{rm /*}",
    "high_profile/c01-bumblebee": r"Deletes \sh{/usr}",
    "high_profile/w00-itunes": r"Deletes drive",
    "high_profile/w01-squid": r"Unknown \sh{rm} target",
    "high_profile/c02-n": r"Deletes \sh{/usr/local/*}",
    "high_profile/c03-backup_manager": r"Bad \sh{$?} check",

    "milestone_1/const_loop": r"Constant \sh{while}",
    "milestone_1/loop_once-useless_test": r"Run-once \sh{for} loop",
    "milestone_1/unset_var_1": r"Unset variable in \sh{echo}",
    "milestone_2/rm_root": r"Typo causes DB loss",
    "web_forums/rm_root_2": r"Failed \sh{mktemp} to data loss",
    "commits/debootstrap": r"Empty \sh{$2} to \sh{cwd} loss",

    "simple_fs/overwrite_file": r"Data loss from \sh{xargs mv}",

    "milestone_1/redir_to_func-redir_to_func": r"Redirect to function",
    "web_forums/accident": r"Wildcard \sh{rm} deletes cwd",
    "web_forums/unset_var-cmd_always_fails": r"Always empty \sh{mkdir} arg",
    "web_forums/capturing_empty_output": r"Captures \sh{mkdir} output",
    "web_forums/claude2": r"Claude deletes \sh{$HOME}",
    "web_forums/claude3": r"Deletes only file copy",
    "web_forums/claude4": r"Typo overwrites file",
    "web_forums/claude5": r"Failed \sh{cd} to \sh{rm -rf *}",
    "web_forums/claude6": r"Deletes project cache",
    "web_forums/claude_wipe": r"Agent wipes \sh{$HOME}",
    "web_forums/confused_mkdir": r"Assumes \sh{mkdir} path output",
    "web_forums/delete_home_user": r"Deletes \sh{/home/user}",
    "web_forums/delete_slash": r"Deletes \sh{/} via extra \sh{rm} arg",
    "web_forums/empty_path": r"Unset \sh{PATH}",
    "web_forums/find_rm": r"Deletes system files",
    "web_forums/for_mv": r"Moves file to missing dest",
    "web_forums/move_home": r"Moves user's \sh{$HOME}",
    "web_forums/posix2": r"Quoted glob in file check",
    "web_forums/replacement": r"Typo truncates file",
    "web_forums/sc_author": r"Hidden \sh{rm -rf /}",
    "web_forums/silly_q": r"Passes multiple sources to \sh{mv}",
    "web_forums/troll": r"Malware deletes \sh{/home}",
    "web_forums/unexpected_stdin": r"Empty \sh{$1} to stuck program",
    "web_forums/unset_var": r"Unset \sh{$bar} used",
    "web_forums/wrong_mkdir": r"Captures \sh{mkdir} output",
    "web_forums/wrong_mv": r"No destination \sh{mv}",
    "web_forums/xargs_accident_rm": r"Deletes files before \sh{mv}",
    "web_forums/xargs_del_files": r"Moves files to missing dir",
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
    "commits/unset_var_2": r"File check on unset var",
    "commits/unset_var_5": r"Unset var used for download",
    "commits/debootstrap_2": r"Deletes supplied path",
    "commits/unset_var_set_u_1": r"Abort check from \sh{set -u}",
    "commits/unset_var_set_u_2": r"Var self-append break",
}

if args.wild:
    wild_root = ROOT_DIR / "in_the_wild"
    wild_rows_by_project = {}
    for script_dir in sorted(p for p in wild_root.iterdir() if p.is_dir()):
        info_path = script_dir / "info.yaml"
        if not info_path.exists():
            continue
        with info_path.open("r", encoding="utf-8") as f:
            info = yaml.safe_load(f) or {}
        sources_list = info.get("sources", [])
        ground_truths = info.get("ground_truths", [])
        bug_defs = info.get("bugs", {}) or {}
        bug_count = 0
        descriptions_set = set()
        for gt in ground_truths:
            for bug_id, bug_info in (gt.get("bugs", {}) or {}).items():
                bug_count += len(bug_info.get("lines", []) or [])
                desc = bug_info.get("description") or (bug_defs.get(bug_id) or {}).get("description")
                if desc:
                    descriptions_set.add(desc)
        key = script_dir.name
        project_name = WILD_PROJECT_NAMES.get(key, key)
        row = wild_rows_by_project.setdefault(
            project_name,
            {"sources": [], "bug_count": 0, "descriptions": set()},
        )
        for source in sources_list[:1]:
            if source not in row["sources"]:
                row["sources"].append(source)
        row["bug_count"] += bug_count
        row["descriptions"].update(descriptions_set)

    if args.appendix:
        print(r"""\begin{tabular}{lllr}
\toprule
\textbf{Project} & \textbf{Domain} & \textbf{Bug description} & \textbf{\#B} \\
\midrule
""")
    else:
        print(r"""\begin{tabular}{lcr}
\toprule
\textbf{Project} & \textbf{Domain} & \textbf{\#B} \\
\midrule
""")
    total_bug_count = 0
    if args.appendix:
        for project_name in sorted(wild_rows_by_project):
            row = wild_rows_by_project[project_name]
            purpose = WILD_PROJECT_PURPOSES.get(project_name, "Util")
            citation = render_wild_citation(project_name)
            project_cell = f"{project_name}~{citation}" if citation else project_name
            description_cell = WILD_BENCHMARK_DESCRIPTIONS.get(
                project_name, "; ".join(sorted(row["descriptions"]))
            )
            total_bug_count += row["bug_count"]
            print(f"{project_cell} & {purpose} & {description_cell} & {row['bug_count']}  \\\\")
    else:
        for key, project_name, purpose, bug_count in WILD_CURATED_ROWS:
            project_row_name = WILD_PROJECT_NAMES.get(key, key)
            citation = render_wild_citation(project_name) or render_wild_citation(project_row_name)
            project_cell = f"{project_name}~{citation}" if citation else project_name
            print(f"{project_cell} & {purpose} & {bug_count}  \\\\")
        for row in wild_rows_by_project.values():
            total_bug_count += row["bug_count"]
    print(r"\midrule")
    if args.appendix:
        print(rf"Total &  &  & {total_bug_count}  \\")
    else:
        print(rf"Total \cf{{app:all-bug-reports}} & $\ldots$ & {total_bug_count}  \\")
    print(r"""
\bottomrule
\end{tabular}
""")
    sys.exit(0)

# WE: word expansion
# CS: command specs
# FS: file system
# SE: base SaSh optimisations (optimistic forking), computed from the 60s sweep
features = {
    "commits/const_cond": [], # Needs optimistic forking in the 60s sweep; no extra manual feature tag here.
    "commits/cp_nonexistent": ["CS"], # CS to reason about cp/[ behavior
    "commits/debootstrap": ["WE", "CS", "FS"], # WE to reason about the various parameter expansions, CS to reason about rm, FS for cwd/directory loss
    "commits/debootstrap_2": ["WE", "CS", "FS"], # WE to reason about the various parameter expansions, CS to reason about rm, FS for directory loss
    "commits/ignored_command_v": ["CS"], # CS to reason about command -v and env
    "commits/makefile": ["WE", "CS"], # WE to reason about variable expansion, CS to reason about rm
    "commits/unset_func": ["CS"], # CS to reason about shell function lookup/use-before-def behavior
    "commits/unset_var_2": ["WE"], # WE to reason about the unset variable
    "commits/unset_var_3": ["WE"], # WE to reason about the unset variable
    "commits/unset_var_5": ["WE"], # WE to reason about the unset variable
    "commits/unset_var_set_u_1": ["WE"], # WE to reason about the unset variable
    "commits/unset_var_set_u_2": ["WE"], # WE to reason about the unset variable

    "high_profile/c00-steam": ["WE", "CS", "FS"],
    "high_profile/c01-bumblebee": ["CS", "FS"],
    "high_profile/w00-itunes": ["WE", "CS"],
    "high_profile/w01-squid": ["CS"],
    "high_profile/c02-n": ["WE", "FS", "CS"],
    "high_profile/c03-backup_manager": ["CS"],

    "milestone_1/const_loop": ["WE"], # WE to determine what the condition is
    "milestone_1/loop_once-useless_test": ["WE"], # WE to determine what the iteree is
    "milestone_1/redir_to_func-redir_to_func": ["CS"], # CS to reason about redirection targets needing files, not functions
    "milestone_1/unset_var_1": ["WE"], # WE to determine that the variable is unset

    "milestone_2/loop_once": ["WE"], # WE to determine what the iteree is
    "milestone_2/loop_once-loop_once": ["WE"], # WE to determine what the iteree is
    "milestone_2/rm_root": ["CS", "WE"], # CS to determine dangerous rm, WE to determine unbound variables
    "milestone_2/unset_var-const_if-dead_code": ["WE"], # WE to detect unbound (the other bugs are oos)

    "simple_fs/access_after_mv": ["CS", "FS"], # CS to reason about mv, FS to reason about file access
    "simple_fs/access_del_resource": ["WE", "CS", "FS"], # WE to reason about the loop condition, CS to reason about rm, FS to reason about file access
    "simple_fs/cd_into_file": ["CS", "FS"], # CS to reason about cd, FS to reason about the file
    "simple_fs/overwrite_file": ["FS"], # FS to reason about the file
    "simple_fs/overwrite_file_2": ["WE", "CS", "FS"], # WE to reason about the iteree, CS to reason about rm, FS to reason about overwrites
    "simple_fs/overwrite_file_3": ["CS", "FS"], # CS to reason about cp, FS to reason about overwrites
    "simple_fs/overwrite_file_4": ["WE", "FS"], # WE to reason about the expansion of the filename, FS to reason about overwrites

    "web_forums/accident": ["WE", "CS", "FS"], # WE to reason about wildcard expansion, CS to reason about rm, FS for cwd loss
    "web_forums/capturing_empty_output": ["CS"], # CS to reason about mkdir not having output
    "web_forums/claude2": ["WE", "CS"], # WE to reason about ~ and wildcard expansion, CS to reason about rm
    "web_forums/claude3": ["CS", "FS"], # CS to reason about rm/rmdir, FS to reason about file loss
    "web_forums/claude4": ["FS"], # FS to reason about overwrite/data loss
    "web_forums/claude5": ["WE", "CS"], # WE for wildcard expansion, CS for rm/cd
    "web_forums/claude6": ["WE", "CS"], # WE to reason about ~ as an rm argument, CS to reason about rm
    "web_forums/claude_wipe": ["WE", "CS"], # WE to expand ~, CS to reason about the destructive rm target
    "web_forums/confused_mkdir": ["CS"], # CS to reason about mkdir output/behavior
    "web_forums/delete_home_user": ["CS", "FS"], # CS to reason about rm, FS for home-directory loss
    "web_forums/delete_slash": ["CS", "FS"], # CS to reason about rm --no-preserve-root, FS for filesystem loss
    "web_forums/empty_path": ["CS"], # CS to reason about command lookup after PATH is unset
    "web_forums/find_rm": ["CS", "FS"], # CS to reason about find -exec rm, FS for filesystem loss
    "web_forums/for_mv": ["WE", "CS", "FS"], # WE to reason about variable/glob expansion, CS to reason about mv, FS for file movement
    "web_forums/move_home": ["CS", "FS"], # CS to reason about mv, FS to reason about directory relocation
    "web_forums/posix2": ["WE", "CS"], # WE to reason about quoted glob behavior, CS to reason about test/mv
    "web_forums/rm_root_2": ["WE", "CS", "FS"], # WE to reason about unbound variable, CS to reason about rm, FS for data loss
    "web_forums/replacement": ["FS"], # FS to reason about truncation/data loss
    "web_forums/sc_author": ["CS", "FS"], # CS to reason about rm, FS for filesystem loss
    "web_forums/silly_q": ["CS", "FS"], # CS to reason about mv argument expectations, FS for file movement
    "web_forums/troll": ["CS", "FS"], # CS to reason about the hidden rm, FS for filesystem loss
    "web_forums/unexpected_stdin": ["CS"], # CS to reason about grep
    "web_forums/unset_var": ["WE"], # WE to reason about unbound variable
    "web_forums/unset_var-cmd_always_fails": ["WE", "CS"], # WE to reason about unbound variable, CS to reason about test command and mkdir command
    "web_forums/wrong_mkdir": ["CS"], # CS to reason about mkdir output
    "web_forums/wrong_mv": ["WE", "CS", "FS"], # WE to reason about glob expansion, CS to reason about mv, FS for file movement
    "web_forums/xargs_accident_rm": ["CS", "FS"], # CS to reason about xargs/rm/mv, FS for file loss/movement
    "web_forums/xargs_del_files": ["CS", "FS"], # CS to reason about xargs mv behavior, FS for file movement
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
    "milestone_2/rm_root",
    "commits/debootstrap",
    "commits/debootstrap_2",
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

lukas_program_cache = {}
lukas_line_cache = {}


def lukas_depth_at_line(path, line_number):
    resolved = str(resolve_benchmark_path(path))
    cache_key = (resolved, int(line_number))
    if cache_key in lukas_line_cache:
        return lukas_line_cache[cache_key]

    if resolved not in lukas_program_cache:
        try:
            lukas_program_cache[resolved] = bugdepth.parser.parse_shell_script(resolved)
        except Exception as e:
            print(f"Failed to parse for Lukas depth {resolved}: {e}", file=sys.stderr)
            lukas_program_cache[resolved] = []

    depth_value = 0
    try:
        program = lukas_program_cache[resolved]
        if program:
            depth_value = bugdepth.count_conds(program, int(line_number), verbose=False)
    except Exception:
        depth_value = 0

    lukas_line_cache[cache_key] = depth_value
    return depth_value

def deepest_bug_depth(path, expected_issues):
    bug_lines = parse_issue_lines(expected_issues)
    if not bug_lines:
        return 0

    return max(lukas_depth_at_line(path, line) for line in bug_lines)

fixed_actual_by_bm = {}
for _, row in fixed_results.iterrows():
    bm_name = get_bm_name(row["benchmark"])
    fixed_actual_by_bm.setdefault(bm_name, []).extend(parse_issue_list(row["actual_results"]))

# For each buggy benchmark: true iff fixed run does not report any corresponding buggy issue.
fixed_clears_bug_by_bm = {}
fixed_fp_count_by_bm = {}
for _, row in buggy_results.iterrows():
    bm_name = get_bm_name(row["benchmark"])
    buggy_expected = parse_issue_list(row["expected_results"])
    fixed_actual = fixed_actual_by_bm.get(bm_name, [])
    fp_bug_count = count_matches(buggy_expected, fixed_actual)
    fixed_fp_count_by_bm[bm_name] = fp_bug_count
    fixed_clears_bug_by_bm[bm_name] = fp_bug_count == 0

# Frozen benchmark sets for paper tables. These were derived from the 60s sweep and
# then inlined here so table output does not depend on whatever CSVs happen to be
# present locally.
optimistic_forking_benchmarks = {
    "commits/const_cond",
    "commits/ignored_command_v",
    "high_profile/c00-steam",
    "high_profile/c01-bumblebee",
    "high_profile/c02-n",
    "high_profile/c03-backup_manager",
    "high_profile/w01-squid",
    "milestone_1/const_loop",
    "simple_fs/access_after_mv",
    "simple_fs/overwrite_file_2",
    "web_forums/rm_root_2",
}

targeted_pass_benchmarks = {
    "commits/cp_nonexistent",
    "commits/debootstrap",
    "commits/makefile",
    "milestone_2/rm_root",
    "simple_fs/access_after_mv",
    "simple_fs/access_del_resource",
    "simple_fs/cd_into_file",
    "web_forums/accident",
    "web_forums/claude2",
    "web_forums/claude3",
    "web_forums/confused_mkdir",
    "web_forums/for_mv",
    "web_forums/posix2",
    "web_forums/sc_author",
    "web_forums/silly_q",
    "web_forums/wrong_mkdir",
    "web_forums/wrong_mv",
    "web_forums/xargs_accident_rm",
    "web_forums/xargs_del_files",
    *optimistic_forking_benchmarks
}

def create_table_line(result, allow_fallback=False):
    path = result["benchmark"]
    time = table_time_cell(result)
    # Grab just the folder and benchmark subfolder
    bm_name = get_bm_name(path)
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
    loc = get_loc(path)
    source = sources.get(bm_name, "")
    expected_issues = parse_issue_list(result["expected_results"])
    actual_issues = parse_issue_list(result["actual_results"])
    n_bugs = len(expected_issues)
    detected = count_matches(expected_issues, actual_issues)
    max_bfs_nodes = deepest_bug_depth(path, expected_issues)
    depth_cell = f"{max_bfs_nodes}"
    fp_bug_count = fixed_fp_count_by_bm.get(bm_name, 0)
    feature_mark = feature_marks(
        features.get(bm_name, []),
        optimistic_forking=bm_name in optimistic_forking_benchmarks,
        targeted_pass=bm_name in targeted_pass_benchmarks,
    )

    detected_prefix = f"{detected}/{n_bugs}"
    if detected < n_bugs:
        detected_prefix = rf"\textcolor{{red}}{{{detected_prefix}}}"
    bugs_detected_cell = f"{detected_prefix} | {fp_bug_count}"
    return f"{name} & {source} & {description} & {loc} & {depth_cell} & {bugs_detected_cell} & {time} & {feature_mark}  \\\\"

rest_of_benchmarks = []

if args.appendix:
    print(r"""% \TE indicates at least one targeted pass was required (DFS, unbound-empty DFS, or unknown-paths-are-files).
\setlength{\LTleft}{0pt}
\setlength{\LTright}{0pt}
\begin{longtable}{@{\extracolsep{\fill}}lllrrrcl@{}}
\toprule
\textbf{Script name} & \textbf{Source} & \textbf{Bug description} & \textbf{LoC} & \textbf{$\downarrow$} & \textbf{D/\#B | FP} & \textbf{$t$} & $\mathcal{F}$ \\
\cmidrule(r){1-5} \cmidrule(l){6-8}
\endfirsthead
\multicolumn{8}{@{}l@{}}{\tablename\ \thetable{} (continued)}\\
\toprule
\textbf{Script name} & \textbf{Source} & \textbf{Bug description} & \textbf{LoC} & \textbf{$\downarrow$} & \textbf{D/\#B | FP} & \textbf{$t$} & $\mathcal{F}$ \\
\cmidrule(r){1-5} \cmidrule(l){6-8}
\endhead
\midrule
\multicolumn{8}{r@{}}{Continued on next page}\\
\endfoot
\bottomrule
\endlastfoot
"""
    )
else:
    print(r"""% \TE indicates at least one targeted pass was required (DFS, unbound-empty DFS, or unknown-paths-are-files).
    \begin{tabular*}{\textwidth}{@{\extracolsep{\fill}}lllrrrcl@{}}
    \toprule
    \textbf{Script name} & \textbf{Source} & \textbf{Bug description} & \textbf{LoC} & \textbf{$\downarrow$} & \textbf{D/\#B | FP} & \textbf{$t$} & $\mathcal{F}$ \\
    \cmidrule(r){1-5} \cmidrule(l){6-8}
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
    avg_time_rest = sum(table_time_seconds(r) for r in rest_of_benchmarks) / len(rest_of_benchmarks)
    time_avg_cell = f"{approx(avg_time_rest, '.2f')}s"

    locs = [get_loc(r["benchmark"]) for r in rest_of_benchmarks]
    avg_loc_rest = sum(locs) / len(locs)
    loc_avg_cell = approx(avg_loc_rest, ".1f")

    total_bugs_rest = sum(len(parse_issue_list(r["expected_results"])) for r in rest_of_benchmarks)
    detected_bugs_rest = sum(
        count_matches(parse_issue_list(r["expected_results"]), parse_issue_list(r["actual_results"]))
        for r in rest_of_benchmarks
    )
    fp_bugs_rest = sum(
        fixed_fp_count_by_bm.get(get_bm_name(r["benchmark"]), 0)
        for r in rest_of_benchmarks
    )
    depth_values_rest = [
        deepest_bug_depth(r["benchmark"], parse_issue_list(r["expected_results"]))
        for r in rest_of_benchmarks
    ]
    avg_depth_rest = sum(depth_values_rest) / len(depth_values_rest)
    depth_avg_rest_cell = approx(avg_depth_rest, ".1f")

    we_count = sum(1 for r in rest_of_benchmarks if "WE" in features.get(get_bm_name(r["benchmark"]), []))
    cs_count = sum(1 for r in rest_of_benchmarks if "CS" in features.get(get_bm_name(r["benchmark"]), []))
    fs_count = sum(1 for r in rest_of_benchmarks if "FS" in features.get(get_bm_name(r["benchmark"]), []))
    te_count = sum(1 for r in rest_of_benchmarks if get_bm_name(r["benchmark"]) in targeted_pass_benchmarks)
    feature_count_marks = (
        f"{we_count}\\WE {cs_count}\\SP {fs_count}\\FS "
        f"{te_count}\\TE"
    )

    rest_detect_cell = f"{detected_bugs_rest}/{total_bugs_rest}"
    if detected_bugs_rest < total_bugs_rest:
        rest_detect_cell = rf"\textcolor{{red}}{{{rest_detect_cell}}}"
    print(rf"""\emph{{More buggy scripts}} & \cref{{sec:full-ds}} &  & {loc_avg_cell} & {depth_avg_rest_cell} & {rest_detect_cell} | {fp_bugs_rest} & {time_avg_cell} & {feature_count_marks} \\""")

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
fp_bugs_total = sum(
    fixed_fp_count_by_bm.get(get_bm_name(r["benchmark"]), 0)
    for r in buggy_results.to_dict(orient="records")
)
times = [table_time_seconds(r) for r in buggy_results.to_dict(orient="records")]
avg_time_total = sum(times) / len(times)
time_avg_total_cell = f"{approx(avg_time_total, '.2f')}s"

we_count = sum(1 for r in buggy_results.to_dict(orient="records") if "WE" in features.get(get_bm_name(r["benchmark"]), []))
cs_count = sum(1 for r in buggy_results.to_dict(orient="records") if "CS" in features.get(get_bm_name(r["benchmark"]), []))
fs_count = sum(1 for r in buggy_results.to_dict(orient="records") if "FS" in features.get(get_bm_name(r["benchmark"]), []))
te_count = sum(1 for r in buggy_results.to_dict(orient="records") if get_bm_name(r["benchmark"]) in targeted_pass_benchmarks)
feature_count_marks = (
        f"{we_count}\\WE {cs_count}\\SP {fs_count}\\FS "
        f"{te_count}\\TE"
    )

total_detect_cell = f"{detected_bugs}/{total_bugs}"

print(rf"""
\cmidrule(r){{1-5}} \cmidrule(l){{6-8}}
\textbf{{Total}} &  & \textbf{{Arith. mean ($\sim$)}} & {loc_avg_total_cell} & {depth_avg_total_cell} & {total_detect_cell} | {fp_bugs_total} & {time_avg_total_cell} & {feature_count_marks} \\ """)

if args.appendix:
    print(r"""
\end{longtable}
""")
else:
    print(r"""
\bottomrule
\end{tabular*}
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
timed_out_count = int(buggy_results["timed_out"].fillna(False).astype(bool).sum())
print(f"% Timed out benchmarks: {timed_out_count}", file=sys.stderr)

# Averages
avg_loc = sum(get_loc(r["benchmark"]) for r in buggy_results.to_dict(orient="records")) / total_benchmarks
avg_time = sum(table_time_seconds(r) for r in buggy_results.to_dict(orient="records")) / total_benchmarks
print(f"% Average LoC: {avg_loc:.2f}", file=sys.stderr)
print(f"% Average time: {avg_time:.2f}s", file=sys.stderr)

non_timeout_times = pd.to_numeric(
    buggy_results.loc[~buggy_results["timed_out"], "time"],
    errors="coerce",
).dropna()
if len(non_timeout_times) > 0:
    avg_non_timeout_time = non_timeout_times.mean()
    min_non_timeout_time = non_timeout_times.min()
    max_non_timeout_time = non_timeout_times.max()
    print(
        "The average runtime per program is "
        f"{avg_non_timeout_time:.2f} seconds, with a minimum of "
        f"{min_non_timeout_time:.2f} seconds and a maximum of "
        f"{max_non_timeout_time:.2f} seconds (excluding timeouts).",
        file=sys.stderr,
    )
else:
    print(
        "The average runtime per program is N/A seconds, with a minimum of "
        "N/A seconds and a maximum of N/A seconds (excluding timeouts).",
        file=sys.stderr,
    )
