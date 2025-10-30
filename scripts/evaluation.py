#!/usr/bin/env -S uv run python3
import sys
import os
import subprocess
import json
import yaml
import argparse
import re
import sash.reporter

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
    with open(gt_path, "r") as f:
        data = yaml.safe_load(f)
    codes = []
    for entry in data.get("ground_truth", []).get("errors", []):
        code = entry.get("code")
        if isinstance(code, str):
            codes.append(code)
        elif isinstance(code, list):
            codes.extend(code)
    return codes

def load_shellcheck_results(gt_path):
    results = []
    with open(gt_path, "r") as f:
        data = yaml.safe_load(f)
        errors = data.get("ground_truth", {}).get("errors", [])
        for error in errors:
            if error["shellcheck"]["detects"]:
                if isinstance(error["code"], str):
                    results.append(error["code"])
                elif isinstance(error["code"], list):
                    results.extend(error["code"])
    return results


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
    parser = argparse.ArgumentParser()
    parser.add_argument('--timeout', type=float, default=None, help='Timeout in seconds for each benchmark (default: no timeout)')
    parser.add_argument('--benchmarks', type=str, default=None, help='Path to the benchmarks directory (default: top-level "benchmarks")')
    parser.add_argument('--only', type=str, default=None, help='Regex to filter benchmarks to run (default: run all)')
    parser.add_argument('--output', type=str, default=None, help='File to write output to (default: stdout)')
    args = parser.parse_args()

    output_file = open(args.output, "w") if args.output else sys.stdout

    benchmark_filter = re.compile(args.only) if args.only else None

    top = get_git_toplevel()
    os.chdir(top)
    bench_dir = os.path.join(top, "benchmarks")
    if args.benchmarks:
        bench_dir = os.path.join(bench_dir, args.benchmarks)

    known_codes = get_all_reporter_codes()
    with open(os.path.join(top, "benchmarks/codes_out_of_scope.yaml"), "r") as f:
        out_of_scope_codes = set(yaml.safe_load(f))
        print(f"Out of scope codes: {out_of_scope_codes}", file=sys.stderr)

    failure = 0
    total = 0

    print("benchmark,time,detected,expected,actual,shellcheck", file=output_file)

    for benchmark in find_benchmarks(bench_dir):
        if benchmark_filter and not benchmark_filter.search(benchmark):
            continue

        print("\n\n", file=sys.stderr)
        print(f"Running benchmark: {benchmark, top}", file=sys.stderr)
        try:
            proc = run_cmd(["uv", "run", "sash", benchmark], timeout=args.timeout)
            output = proc.stdout
        except subprocess.TimeoutExpired:
            print(f"FAIL: Benchmark timed out after {args.timeout} seconds", file=sys.stderr)
            failure += 1
            total += 1
            continue

        gt_path = os.path.join(os.path.dirname(benchmark), "info.yaml")
        if not os.path.isfile(gt_path):
            # No ground truth, just print the output
            print(output, end="", file=sys.stderr)
            total += 1
            continue

        expected = load_expected_codes(gt_path)
        expected_in_scope = [e for e in expected if e not in out_of_scope_codes]
        unknown_codes = [e for e in expected_in_scope if e not in known_codes]
        # Check that all expected codes are valid
        for code in unknown_codes:
            print(f"Ground truth contains unknown error code: {code}", file=sys.stderr)

        actual_codes, parsed_json = extract_codes_from_output(output)

        if actual_codes is None:
            # Couldn't parse output as JSON; treat as failure
            print("FAIL", file=sys.stderr)
            print("Expected:", file=sys.stderr)
            for c in expected:
                print(c, file=sys.stderr)
            print("Actual (raw output, not valid JSON):", file=sys.stderr)
            print(output, file=sys.stderr)
            failure += 1
            total += 1
            continue

        # Check that every expected code appears in the actual codes (actual may contain extras)
        missing = [e for e in expected_in_scope if e not in actual_codes]
        if missing:
            print(f"FAIL", file=sys.stderr)
            print("Missing expected codes:", file=sys.stderr)
            for c in missing:
                print(c)
            print("Actual:", file=sys.stderr)
            print(json.dumps(parsed_json.get("errors", []), indent=2), file=sys.stderr)
            failure += 1
        else:
            print(f"PASS", file=sys.stderr)
        total += 1

        shellcheck_results = load_shellcheck_results(gt_path)

        expected_codes = f"{';'.join(expected)}"
        actual_caught_codes = f"{';'.join(actual_codes)}"
        shellcheck_codes = f"{';'.join(shellcheck_results)}"
        time_elapsed = f"{parsed_json.get('time', 'N/A')}"

        # Output to CSV
        benchmark_rel = os.path.relpath(benchmark, top)
        print(f"{benchmark_rel},{time_elapsed},{1 if not missing else 0},{expected_codes},{actual_caught_codes},{shellcheck_codes}", file=output_file)

    output_file.close()

    print(f"{total - failure}/{total} benchmarks succeeded", file=sys.stderr)

    if failure != 0:
        sys.exit(1)
    else:
        sys.exit(0)

if __name__ == "__main__":
    main()
