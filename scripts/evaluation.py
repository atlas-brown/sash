#!/usr/bin/env -S uv run python3
import sys
import os
import subprocess
import json
import yaml
import sash.reporter

def run_cmd(cmd, check=False, capture_stdout=True):
    proc = subprocess.run(cmd, shell=False, capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, cmd, proc.stdout, proc.stderr)
    return proc

def get_git_toplevel():
    proc = run_cmd(["git", "rev-parse", "--show-toplevel"])
    if proc.returncode != 0:
        print("Failed to determine git top-level directory", file=sys.stderr)
        sys.exit(1)
    return proc.stdout.strip()

def find_benchmarks(bench_dir):
    for root, dirs, files in os.walk(bench_dir):
        # skip files in _not_integrated
        dirs[:] = [d for d in dirs if d not in "_not_integrated"]
        for name in files:
            if name == "posix.sh":
                path = os.path.join(root, name)
                # include regular files and symlinks
                if os.path.isfile(path) or os.path.islink(path):
                    yield path

def load_expected_codes(gt_path):
    try:
        with open(gt_path, "r") as f:
            data = yaml.safe_load(f)
        codes = set()
        for entry in data.get("ground_truth", []).get("errors", []):
            code = entry.get("code")
            if isinstance(code, str):
                codes.add(code)
            elif isinstance(code, list):
                codes.update(code)
        return codes
    except Exception as e:
        print(f"Failed to read/parse ground truth {gt_path}: {e}", file=sys.stderr)
        return set()

def extract_codes_from_output(output):
    try:
        data = json.loads(output)
        codes = [e.get("code") for e in data.get("errors", [])]
        return set(codes), data
    except Exception:
        return None, None

def get_all_reporter_codes():
    return sash.reporter.Report.all_codes()

def main():
    top = get_git_toplevel()
    os.chdir(top)
    bench_dir = os.path.join(top, "benchmarks")
    if len(sys.argv) > 1 and sys.argv[1]:
        bench_dir = os.path.join(bench_dir, sys.argv[1])

    known_codes = get_all_reporter_codes()
    with open(os.path.join(top, "benchmarks/codes_out_of_scope.yaml"), "r") as f:
        out_of_scope_codes = set(yaml.safe_load(f))
        print(out_of_scope_codes)

    failure = 0
    total = 0

    for benchmark in find_benchmarks(bench_dir):
        print("\n\n")
        print(f"Running benchmark: {benchmark}")
        proc = run_cmd(["uv", "run", "sash", benchmark])
        output = proc.stdout

        gt_path = os.path.join(os.path.dirname(benchmark), "info.yaml")
        if not os.path.isfile(gt_path):
            # No ground truth, just print the output
            print(output, end="")
            total += 1
            continue

        expected = load_expected_codes(gt_path)
        expected_in_scope = expected - out_of_scope_codes
        unknown_codes = expected_in_scope - known_codes
        # Check that all expected codes are valid
        for code in unknown_codes:
            print(f"Ground truth contains unknown error code: {code}")

        actual_codes, parsed_json = extract_codes_from_output(output)
        if actual_codes is None:
            # Couldn't parse output as JSON; treat as failure
            print("FAIL")
            print("Expected:")
            for c in expected:
                print(c)
            print("Actual (raw output, not valid JSON):")
            print(output)
            failure += 1
            total += 1
            continue

        # Check that every expected code appears in the actual codes (actual may contain extras)
        missing = expected - actual_codes
        if missing:
            print(f"FAIL, time: {parsed_json.get('time', 'N/A')}s")
            print("Missing expected codes:")
            for c in missing:
                print(c)
            print("Actual:")
            print(json.dumps(parsed_json.get("errors", []), indent=2))
            failure += 1
        else:
            print(f"Pass, time: {parsed_json.get('time', 'N/A')}s")
        total += 1

    if failure != 0:
        print(f"{failure}/{total} benchmarks failed")
        sys.exit(1)
    else:
        print(f"All {total} benchmarks passed!")
        sys.exit(0)

if __name__ == "__main__":
    main()
