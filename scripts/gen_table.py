import pandas as pd
import sys
import os

results_path = "scripts/results.csv"
results = pd.read_csv(results_path)

# df = pd.read_csv(csv_path)
# latex_table = df.to_latex(index=False, escape=False, float_format="%.2f", label=label)
# with open(latex_path, "w") as f:
#     f.write(latex_table)

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
    "high_profile/c00-steam": r"Failed path traversal leads to \ttt{rm /*}",
    "high_profile/c01-bumblebee": r"Deletion of \sh{/usr}",
    "high_profile/w00-itunes": r"Variable expansion deletes arbitrary files",
    "high_profile/w01-squid": r"Externally-set variable used in \sh{rm}",
    "high_profile/c02-n": r"Loop deletes directories under \sh{/usr/local}",
    "high_profile/c03-backup_manager": r"Wrong exit status check causes file deletion",

    "milestone_1/const_loop": r"Constant \sh{while} loop condition",
    "milestone_1/loop_once-useless_test": r"Single loop iteration",
    "milestone_1/unset_var_1": r"Unset variable used in \sh{echo}",
    "milestone_2/rm_root": r"Typo in invocation causes database loss",
    "web_forums/rm_root_2": r"Failed \sh{mktemp} causes data loss",
}

def create_table_line(result):
    def get_name(path):
        subpath = os.path.dirname(path)
        parts = subpath.split(os.sep)[1:]
        result = os.path.join(*parts)
        return result
    path, time, detected = result["benchmark"], result["time"], result["detected"]
    # Grab just the folder and benchmark subfolder
    bm_name = get_name(path)
    name = names.get(bm_name, None)
    if name is None:
        print(f"Unknown benchmark name {bm_name} for path: {path}", file=sys.stderr)
        return
    description = descriptions[bm_name]
    loc = 999
    source = sources.get(bm_name, "")
    detected = r"\checkmark" if bool(detected) else ""
    return f"{name} & {loc}  & {description} & {detected} & {time:.2f} &  &  &  & {source}  \\\\"

rest_of_benchmarks = []

print(r"""
    \begin{tabular}{lrllcrcccl}\n
    \toprule\n
    \textbf{Name} & \textbf{LoC} & \textbf{Bug} & \textbf{D?} & $t$ & \textbf{WE} & \textbf{CS} & \textbf{FS} & \textbf{Source} \\
    \midrule
"""
)

for result in results.to_dict(orient="records"):
    line = create_table_line(result)
    if line:
        print(line)
    else:
        rest_of_benchmarks.append(result)

print(r"""\emph{More buggy scripts} & \xxx & \xxx & \xxx & \xxx & \xxx & \xxx & \xxx & \cf{sec:full-ds} \\""")

for result in rest_of_benchmarks:
    # create aggregate results
    pass

print(r"\hspace{.5em}\dots & & & & & & & & \\")

print(r"""
\bottomrule
\end{tabular}
""")
