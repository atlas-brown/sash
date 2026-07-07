import os
import yaml
from collections import Counter
import matplotlib.pyplot as plt
import subprocess
import sys

# Note: if `timeout` supplied, may raise subprocess.TimeoutExpired
def run_cmd(cmd, check=False, capture_stdout=True, timeout=None):
    proc = subprocess.run(cmd, shell=False, capture_output=True, text=True, timeout=timeout)
    if check and proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, cmd, proc.stdout, proc.stderr)
    return proc

def get_git_toplevel():
    proc = run_cmd(["git", "rev-parse", "--show-toplevel"])
    if proc.returncode != 0:
        print("Failed to determine git top-level directory", file=sys.stderr)
        sys.exit(1)
    return proc.stdout.strip()

# Grab all info.yaml files from benchmark directories
def gather_benchmark_info(root_dir):
    benchmark_info = []
    for subdir, dirs, files in os.walk(root_dir):
        # skip files in _not_integrated
        dirs[:] = [d for d in dirs if d not in "_not_integrated"]
        for file in files:
            if file == 'info.yaml':
                file_path = os.path.join(subdir, file)
                with open(file_path, 'r') as f:
                    info = yaml.safe_load(f)
                    benchmark_info.append(info)
    return benchmark_info

top = get_git_toplevel()
root_directory = os.path.join(top, "benchmarks")
benchmarks = gather_benchmark_info(root_directory)

with open(os.path.join(root_directory, "codes_out_of_scope.yaml"), 'r') as f:
    # the yaml file contains a list of codes
    out_of_scope_codes = yaml.safe_load(f)

code_distribution = Counter()
for benchmark in benchmarks:
    errors = benchmark.get('ground_truth', {}).get('errors', [])
    for error in errors:
        code = error.get('code')
        if code in out_of_scope_codes:
            continue
        for c in (code if isinstance(code, list) else [code]):
            code_distribution[c] += 1

code_distribution = dict(sorted(code_distribution.items(), key=lambda item: item[1], reverse=True))

# Calculate how many of each code are detected by shellcheck ("ground_truth.shellcheck.detects")
code_distribution_sc = Counter()
for benchmark in benchmarks:
    errors = benchmark.get('ground_truth', {}).get('errors', [])
    for error in errors:
        code = error.get('code')
        if code in out_of_scope_codes:
            continue
        for c in (code if isinstance(code, list) else [code]):
            if error.get("shellcheck", {}).get("detects", False):
                code_distribution_sc[c] += 1

code_distribution_sc = dict(sorted(code_distribution_sc.items(), key=lambda item: item[1], reverse=True))

# Plotting
#codes = list(code_distribution.keys())
#counts = list(code_distribution.values())
#plt.figure(figsize=(10, 5))
#plt.bar(codes, counts)

# Plot numbers on top of bars
#for i, count in enumerate(counts):
#    plt.text(i, count + 0.5, str(count), ha='center')

#plt.gca().spines['top'].set_visible(False)
#plt.gca().spines['right'].set_visible(False)
#plt.xlabel('Error code')
#plt.ylabel('Frequency')
#plt.xticks(rotation=80)
#plt.tight_layout()
#plt.show()

# Plot the code_distribution first, and then overlay code_distribution_sc in a different color
codes = list(code_distribution.keys())
counts = [code_distribution.get(code, 0) for code in codes]
counts_sc = [code_distribution_sc.get(code, 0) for code in codes]
x = range(len(codes))
plt.figure(figsize=(12, 6))
bar_width = 0.4
plt.bar(x, counts, width=bar_width, label='SaSh', color='skyblue')
plt.bar([i + bar_width for i in x], counts_sc, width=bar_width, label='ShellCheck', color='orange')
# Plot numbers on top of bars
for i, count in enumerate(counts):
    plt.text(i, count + 0.5, str(count), ha='center')
for i, count in enumerate(counts_sc):
    plt.text(i + bar_width, count + 0.5, str(count), ha='center')
plt.gca().spines['top'].set_visible(False)
plt.gca().spines['right'].set_visible(False)
plt.xlabel('Error code')
plt.ylabel('Frequency')
plt.xticks([i + bar_width / 2 for i in x], codes, rotation=80)
plt.legend()
plt.tight_layout()
plt.savefig('code_distribution.png', dpi=300)
