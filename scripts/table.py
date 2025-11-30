import pandas as pd
import sys
import os
from io import StringIO

EPSILON = 1e-3

results_path = "results/results.csv"
results = pd.read_csv(results_path)

names = {
    "high_profile/c00-steam": "Steam updater",
    "high_profile/c01-bumblebee": "NVIDIA driver installer",
    "high_profile/w00-itunes": "iTunes updater",
    "high_profile/w01-squid": "Squid init script",
    "high_profile/c02-n": "Node.js version manager",
    "high_profile/c03-backup_manager": "Ubuntu backup manager",

    "milestone_1/const_loop": "DigitalOcean snapshot",
    "milestone_1/loop_once-useless_test": "AutoTest config rename",
    "milestone_1/unset_var_1": "OhMyZsh update script",
    "milestone_2/rm_root": "MongoDB backup script",
    "web_forums/rm_root_2": "AIX server data gather",
}

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
}


descriptions = {
    "high_profile/c00-steam": r"Failed path traversal leads to \sh{rm /*}",
    "high_profile/c01-bumblebee": r"Deletion of \sh{/usr}",
    "high_profile/w00-itunes": r"Variable expansion deletes arbitrary files",
    "high_profile/w01-squid": r"Externally-set variable used in \sh{rm}",
    "high_profile/c02-n": r"Loop deletes \sh{/usr/local/*}",
    "high_profile/c03-backup_manager": r"Data loss due to wrong exit status check",

    "milestone_1/const_loop": r"Constant \sh{while} loop condition",
    "milestone_1/loop_once-useless_test": r"Single loop iteration",
    "milestone_1/unset_var_1": r"Unset variable used in \sh{echo}",
    "milestone_2/rm_root": r"Typo in invocation causes database loss",
    "web_forums/rm_root_2": r"Failed \sh{mktemp} causes data loss",
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


def get_bm_name(path):
    subpath = os.path.dirname(path)
    parts = subpath.split(os.sep)[1:]
    result = os.path.join(*parts)
    return result

def get_loc(path):
    proc = os.popen(f"cloc --json {path}")
    output = proc.read()
    proc.close()
    data = None
    data = pd.read_json(StringIO(output))
    loc = int(data.get("SUM", {}).get("code", 0))
    # assert loc > 0, f"Failed to get LoC for path: {path}"
    return loc

def create_table_line(result):
    path, time, detected = result["benchmark"], result["time"], result["detected_all"]
    # Grab just the folder and benchmark subfolder
    bm_name = get_bm_name(path)
    name = names.get(bm_name, None)
    if name is None:
        print(f"Unknown benchmark name {bm_name} for path: {path}", file=sys.stderr)
        return
    description = descriptions[bm_name]
    time = f"{time:.2f}s" if time > EPSILON else "<1ms"
    loc = get_loc(path)
    source = sources.get(bm_name, "")
    detected = r"\checkmark" if bool(detected) else ""
    we_feature = "WE" in features.get(bm_name, [])
    cs_feature = "CS" in features.get(bm_name, [])
    fs_feature = "FS" in features.get(bm_name, [])

    we_mark = r"\checkmark" if we_feature else ""
    cs_mark = r"\checkmark" if cs_feature else ""
    fs_mark = r"\checkmark" if fs_feature else ""

    return f"{name} & {loc}  & {description} & {detected} & {time} & {we_mark}  & {cs_mark}  & {fs_mark}  & {source}  \\\\"

rest_of_benchmarks = []

print(r"""
    \begin{tabular}{lrlccrcccl}
    \toprule
    \textbf{Name} & \textbf{LoC} & \textbf{Bug} & \textbf{D?} & $t (s)$ & \textbf{WE} & \textbf{CS} & \textbf{FS} & \textbf{Source} \\
    \midrule
"""
)

for result in results.to_dict(orient="records"):
    line = create_table_line(result)
    if line:
        print(line)
    else:
        rest_of_benchmarks.append(result)

# find time range for rest_of_benchmarks
min_time = min(r["time"] for r in rest_of_benchmarks)
max_time = max(r["time"] for r in rest_of_benchmarks)
time_range = f"{min_time:.2f}--{max_time:.2f}s"

locs = [get_loc(r["benchmark"]) for r in rest_of_benchmarks]
min_loc = min(locs)
max_loc = max(locs)
loc_range = f"{min_loc}--{max_loc}"

detected = sum(1 for r in rest_of_benchmarks if r["detected_all"])
total = len(rest_of_benchmarks)
detection_rate = f"{detected}/{total}"

we_count = sum(1 for r in rest_of_benchmarks if "WE" in features.get(get_bm_name(r["benchmark"]), []))
cs_count = sum(1 for r in rest_of_benchmarks if "CS" in features.get(get_bm_name(r["benchmark"]), []))
fs_count = sum(1 for r in rest_of_benchmarks if "FS" in features.get(get_bm_name(r["benchmark"]), []))

print(rf"""\emph{{More buggy scripts}} & {loc_range} &  & {detection_rate} & {time_range} & {we_count} & {cs_count} & {fs_count} & \cf{{sec:full-ds}} \\""")
print(r"\hspace{.5em}\dots & & & & & & & & \\")

# Print summary line across all benchmarks
locs = [get_loc(r["benchmark"]) for r in results.to_dict(orient="records")]
min_loc = min(locs)
max_loc = max(locs)
loc_range = f"{min_loc}--{max_loc}"

detected = sum(1 for r in results.to_dict(orient="records") if r["detected_all"])
total = len(results)
detection_rate = f"{detected}/{total}"
times = [r["time"] for r in results.to_dict(orient="records")]
min_time = min(times)
max_time = max(times)
time_range = f"{min_time:.2f}--{max_time:.2f}s"

we_count = sum(1 for r in results.to_dict(orient="records") if "WE" in features.get(get_bm_name(r["benchmark"]), []))
cs_count = sum(1 for r in results.to_dict(orient="records") if "CS" in features.get(get_bm_name(r["benchmark"]), []))
fs_count = sum(1 for r in results.to_dict(orient="records") if "FS" in features.get(get_bm_name(r["benchmark"]), []))

print(rf"""
\midrule
\textbf{{Total}} & {loc_range} &  & {detection_rate} & {time_range} & {we_count} & {cs_count} & {fs_count} &  \\ """)

print(r"""
\bottomrule
\end{tabular}
""")

# Print some stats about the benchmarks
total_benchmarks = len(results)
# Total bugs
# benchmark,missing_gt,crashed,timed_out,time,detected_all,expected_results,actual_results,shellcheck_codes,line_numbers
# benchmarks/commits/unset_var_set_u_2/posix.sh,False,False,False,0.04071833333000541,True,unbound_setu,unbound_setu,,15
total_bugs = (
    results["expected_results"]
    .fillna("")
    .where(lambda s: s != "")
    .dropna()
    .str.split(";")
    .map(len)
    .sum()
)
n_bugs = (
    results["expected_results"]
    .fillna("")
    .where(lambda s: s != "")
    .dropna()
    .str.split(";")
    .map(len)
)
bugs_min = n_bugs.min()
bugs_max = n_bugs.max()

print(f"% Total benchmarks: {total_benchmarks}", file=sys.stderr)
print(f"% Total bugs: {total_bugs}", file=sys.stderr)
print(f"% Bugs per benchmark: {bugs_min}--{bugs_max}", file=sys.stderr)
