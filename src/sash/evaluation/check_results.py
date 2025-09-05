import json
import os
import re
import sys
from argparse import ArgumentParser
from pathlib import Path
from enum import Enum
from dataclasses import dataclass
from typing import  Optional
from collections import defaultdict
import shseer.error_report as error_report
from shseer.reporter import SmooshResult
from shseer.shellcheck_map import SC_MAP
from shseer.symb_result import ShseerResult
import pandas as pd
import subprocess
from datetime import datetime

PROGRESS_LOG = "progress.log"

# all_dirs = ["testscripts/good","testscripts/sc_good","testscripts/bad","testscripts/sc_bad","testscripts/parse","testscripts/milestone/shellcheck","testscripts/milestone/shseer"]
FS_MILESTONE_MAP = {
"benchmarks/milestone_fs/deltwice.sh" : error_report.BadFSComp,
"benchmarks/milestone_fs/test_unsetvar.sh" : error_report.VarUndefined,
"benchmarks/milestone_fs/test_badcmdinv1.sh" : error_report.BadFSComp,
"benchmarks/milestone_fs/badsymbread.sh" : error_report.BadFSComp,
"benchmarks/milestone_fs/test_getopts.sh" : error_report.SystemDirAlwaysChanged,
"benchmarks/milestone_fs/badnav.sh" : error_report.BadFSComp,
"benchmarks/milestone_fs/test_badread.sh" : error_report.BadFSComp,
"benchmarks/milestone_fs/test_badcmdinv2.sh" : error_report.BadFSComp,
"benchmarks/milestone_fs/test_badexist1.sh" : error_report.BadFSComp,
"benchmarks/milestone_fs/test_unsetfunction.sh" : error_report.UndefinedFunction,
"benchmarks/milestone_fs/test_dscope.sh"  : error_report.BadFSComp,
"benchmarks/milestone_fs/test_catastrophe1.sh" : error_report.SystemDirAlwaysChanged,
"benchmarks/milestone_fs/test_badread2.sh" : error_report.BadFSComp,
"benchmarks/milestone_fs/test_unreachable1.sh" : error_report.UnreachableCode,
}
SC_MILESTONE_LIST = [
"benchmarks/milestone_sc/SC2080.sh",
"benchmarks/milestone_sc/SC2286.sh",
"benchmarks/milestone_sc/SC2123.sh",
"benchmarks/milestone_sc/SC2241.sh",
"benchmarks/milestone_sc/SC2152.sh",
"benchmarks/milestone_sc/SC2195.sh",
"benchmarks/milestone_sc/SC2214.sh",
"benchmarks/milestone_sc/SC2220.sh",
"benchmarks/milestone_sc/SC2153.sh",
"benchmarks/milestone_sc/SC2221.sh",
"benchmarks/milestone_sc/SC2072.sh",
"benchmarks/milestone_sc/SC2079.sh",
"benchmarks/milestone_sc/SC2119.sh",
"benchmarks/milestone_sc/SC2170_1.sh",
"benchmarks/milestone_sc/SC2269.sh",
"benchmarks/milestone_sc/SC2309.sh",
"benchmarks/milestone_sc/SC2249.sh",
"benchmarks/milestone_sc/SC2123_1.sh",
"benchmarks/milestone_sc/SC2317.sh",
"benchmarks/milestone_sc/SC2034.sh",
"benchmarks/milestone_sc/SC2222.sh",
"benchmarks/milestone_sc/SC2071.sh",
"benchmarks/milestone_sc/SC2193.sh",
"benchmarks/milestone_sc/SC2154.sh",
"benchmarks/milestone_sc/SC2115.sh",
"benchmarks/milestone_sc/SC2030.sh",
"benchmarks/milestone_sc/SC2170.sh",
"benchmarks/milestone_sc/SC2130.sh",
"benchmarks/milestone_sc/SC2252.sh",
"benchmarks/milestone_sc/SC2114.sh",
"benchmarks/milestone_sc/SC2213.sh",
"benchmarks/milestone_sc/SC2242.sh",
"benchmarks/milestone_sc/SC2120.sh",
"benchmarks/milestone_sc/SC2031.sh",
"benchmarks/milestone_sc/SC2151.sh",
"benchmarks/milestone_sc/SC2050.sh"
]
SO_MAP = {
"benchmarks/so/48184376.sh" : [error_report.CpMissingDestination],
"benchmarks/so/50285538.sh" : [error_report.BadFSCompConcrete],
"benchmarks/so/48300215.sh" : [error_report.GlobDisabled],
"benchmarks/so/48195715.sh" : [error_report.ForLoopSingleConstantArg],
"benchmarks/so/48674985.sh" : [error_report.UnRaisableError],
"benchmarks/so/49095721.sh" : [error_report.UnRaisableError],
"benchmarks/so/49120250.sh" : [error_report.ForLoopSingleConstantArg],
"benchmarks/so/50302681.sh" : [error_report.UnRaisableError],
"benchmarks/so/48004671.sh" : [error_report.VarUndefined,error_report.UnreachableCode],
"benchmarks/so/48405265.sh" : [error_report.TestOpExpectedNumber],
"benchmarks/so/48499526.sh" : [error_report.VarUndefined],
"benchmarks/so/48568740.sh" : [error_report.VarUndefined],
"benchmarks/so/48677144.sh" : [error_report.UnRaisableError],
"benchmarks/so/48919816.sh" : [error_report.IndexVariableOverride],
"benchmarks/so/49186261.sh" : [error_report.UnRaisableError],
"benchmarks/so/49346953.sh" : [error_report.VarUndefined],
"benchmarks/so/49562688.sh" : [error_report.ForLoopSingleConstantArg],
"benchmarks/so/49951879.sh" : [error_report.VarModInSubshell],
"benchmarks/so/50220100.sh" : [error_report.UnRaisableError],
"benchmarks/so/50349389.sh" : [error_report.VarUndefined],
"benchmarks/so/55507229.sh" : [error_report.UnRaisableError],
"benchmarks/so/48213856.sh" : [error_report.UnreachableCode,],
"benchmarks/so/48706718.sh" : [error_report.VarUndefined],
"benchmarks/so/48262206.sh" : [error_report.UnRaisableError],
"benchmarks/so/48738856.sh" : [error_report.VarUndefined],
"benchmarks/so/49138117.sh" : [error_report.FileOverride],
"benchmarks/so/48429400.sh" : [error_report.UnRaisableError],
"benchmarks/so/48037269.sh" : [error_report.UnRaisableError],
"benchmarks/so/54903374.sh" : [error_report.UnRaisableError],
"benchmarks/so/48251283.sh" : [error_report.UnRaisableError],
"benchmarks/so/48325444.sh" : [error_report.CommandNotFoundLocally],
"benchmarks/so/49605847.sh" : [error_report.UnRaisableError],
"benchmarks/so/49681271.sh" : [error_report.UnRaisableError],
"benchmarks/so/48018851.sh" : [error_report.UnRaisableError],
"benchmarks/so/48105228.sh" : [error_report.UnRaisableError],
"benchmarks/so/48219839.sh" : [error_report.UnRaisableError],
"benchmarks/so/48298276.sh" : [error_report.UnRaisableError],
"benchmarks/so/48854121.sh" : [error_report.UnRaisableError],
"benchmarks/so/48939517.sh" : [error_report.UnRaisableError],
"benchmarks/so/49043790.sh" : [error_report.RedirectFunction],
"benchmarks/so/49139325.sh" : [error_report.UnRaisableError],
"benchmarks/so/49512954.sh" : [error_report.UnRaisableError],
"benchmarks/so/49629366.sh" : [error_report.UnRaisableError],
"benchmarks/so/50331556.sh" : [error_report.UnreachableCode],
"benchmarks/so/50472295.sh" : [error_report.UnRaisableError],
"benchmarks/so/50705070.sh" : [error_report.UnRaisableError],
"benchmarks/so/54421229.sh" : [error_report.UnRaisableError],
"benchmarks/so/55323391.sh" : [error_report.VarUndefined,error_report.SystemDirAlwaysChanged],
"benchmarks/so/47033419.sh" : [error_report.CDCouldFail],
"benchmarks/so/47108858.sh" : [error_report.UnRaisableError],
}
VARIANTS_MAP = {
"test_benchmarks/highprofile/variants/itunes.sh":[error_report.VarUndefined],
"test_benchmarks/highprofile/variants/org.sh":[error_report.SystemDirChanged],
"test_benchmarks/highprofile/variants/safefix.sh":[],
"test_benchmarks/highprofile/variants/subtle.sh":[error_report.SystemDirChanged],
"test_benchmarks/highprofile/variants/unsafefix.sh":[error_report.SystemDirChanged],
"test_benchmarks/highprofile/variants/n.sh" : [error_report.SystemDirChanged],
"test_benchmarks/highprofile/variants/redhat.sh" : [error_report.SystemDirChanged],
"test_benchmarks/highprofile/variants/squid_no_const.sh" : [error_report.SystemDirChanged],
}
HF_ORIG_MAP = {
"test_benchmarks/highprofile/original/backup_methods.sh" : [], # dead code -- maybe warrants an error with some heuristic about rm guarded by a constant if?
"test_benchmarks/highprofile/original/bumblebee_install.sh" : [error_report.SystemDirChanged],
"test_benchmarks/highprofile/original/n.sh" : [error_report.SystemDirChanged],
"test_benchmarks/highprofile/original/squid.init.sh" : [],
"test_benchmarks/highprofile/original/steam.sh" : [error_report.SystemDirChanged],
"test_benchmarks/highprofile/original/itunes.sh":[error_report.SystemDirChanged],
}
def get_error_codes(ls):
    return [x[0] for x in ls]

@dataclass()
class ExpectedResult:
    error_codes : Optional[list[tuple[str,bool]]]
    results : Optional[list[ShseerResult]]
    


def get_expected_result(dirname,basename) -> ExpectedResult:
    match dirname:
        case "benchmarks/milestone_fs":
            error_codeclass = FS_MILESTONE_MAP.get(os.path.join(dirname,basename),None)
            assert error_codeclass is not None, f"Error code class not found for {basename}"
            return ExpectedResult([(error_codeclass().code,True)],None)
        case "benchmarks/milestone_sc":
            basename = basename.split("_")[0]
            error_codeclass = SC_MAP.get(Path(basename).stem,error_report.UnRaisableError)
            return ExpectedResult([(error_codeclass().code,True)],None)
        case "benchmarks/sc_good":
            error_codeclass = SC_MAP.get(Path(basename).stem,error_report.UnRaisableError)
            return ExpectedResult([(error_codeclass().code,False)], [ShseerResult.SymbOk,ShseerResult.UNKNOWN])
        case "benchmarks/sc_bad":
            error_codeclass = SC_MAP.get(Path(basename).stem,error_report.UnRaisableError)
            return ExpectedResult([(error_codeclass().code,True)], None)
        case "benchmarks/highprofile/original":
            error_codes = HF_ORIG_MAP.get(os.path.join(dirname,basename),[error_report.UnRaisableError])
            return ExpectedResult([(i().code,True) for i in error_codes], None)
        case "benchmarks/highprofile/variants":
             error_codes = VARIANTS_MAP.get(os.path.join(dirname,basename),[error_report.UnRaisableError])
             return ExpectedResult([(i().code,True) for i in error_codes], None)
        case "benchmarks/so":
             pth = os.path.join(dirname,basename)
             error_codes : list[error_report.ShseerError | error_report.ShseerWarning] = SO_MAP.get(pth,[error_report.UnRaisableError])
            #  print("codes is ",error_codes)
             return ExpectedResult([(i().code,True) for i in error_codes], None)
        case _:
            raise ValueError(f"unexpected: {dirname}")
    
def check_log_file(log_file_path,ensure_all:bool):
    errors = []
    processed_files = set()
    failed_files = []
    
    bench_results = defaultdict(lambda: defaultdict(int))
    timeout_limit = "?"

    try:
        with open(log_file_path, 'r') as log_file:
            for line in log_file:
                try:
                    data = json.loads(line)
                    filename = data.get('filename')
                    result = data.get('result')
                    timeout_limit = data.get('timeout_limit', timeout_limit)
                    dirname = os.path.dirname(filename)
                    basename = os.path.basename(filename)
                
                    actual_result = ShseerResult[result]
                    processed_files.add(filename)
                    
                    if actual_result == ShseerResult.TIMEOUT:
                        errors.append(f"Timeout for {filename}")
                        failed_files.append(filename)
                        bench_results[dirname]["timeout"] += 1
                        continue
                    
                    if actual_result == ShseerResult.ShseerException or not filename or not result:
                        failed_files.append(filename)
                        errors.append(f"Invalid data in line: {line.strip()}")
                        bench_results[dirname]["exception"] += 1
                        continue
                    

                    if "smoosh" in dirname:
                        print(f"Checking smoosh for {filename}")
                        exit_code_suc = SmooshResult[data["SMOOSH_EXIT_CODE"]] != SmooshResult.MISMATCH
                        stdout_suc = SmooshResult[data["SMOOSH_STDOUT"]] != SmooshResult.MISMATCH
                        total_suc = exit_code_suc and stdout_suc
                        if not total_suc:
                            failed_files.append(filename)
                            errors.append(f"Mismatch for {filename}: expected exit code and stdout to match, got {data['SMOOSH_EXIT_CODE'],data['SMOOSH_STDOUT']}")
                            bench_results[dirname]["mismatch"] += 1
                        else:
                            bench_results[dirname]["correct"] += 1                  
                    else:
                        exp_struct =  get_expected_result(dirname,basename)
                        exp_result  = exp_struct.results
                        actual_error_codes = get_error_codes(data.get('error_messages', []))
                        codes_suc = True 
                        if exp_struct.error_codes is not None:
                            for code in exp_struct.error_codes:
                                exp_code,exp_present = code
                                assert isinstance(exp_code,str)
                                assert isinstance(exp_present,bool)
                                code_res = (exp_code in actual_error_codes) == exp_present 
                                if not code_res:
                                    codes_suc = False
                                    break
                                
                        result_suc = (exp_result is not None and actual_result in exp_result) or (exp_result is None)
                        total_suc = codes_suc and result_suc
                        if not total_suc:
                            failed_files.append(filename)
                            errors.append(f"Mismatch for {filename}: expected one of {(exp_struct.error_codes,exp_result)}, got {actual_error_codes,actual_result}")
                            bench_results[dirname]["mismatch"] += 1
                        else:
                            bench_results[dirname]["correct"] += 1
                except json.JSONDecodeError:
                    errors.append(f"Invalid JSON in line: {line.strip()}")

    except IOError as e:
        print(f"Error reading log file: {e}")
        sys.exit(1)

    # Check if all files in testscripts/good and testscripts/bad have results

    if errors:
        print(f"Passed {len(processed_files)-len(errors)}/{len(processed_files)} files, failed {len(errors)} checks:")
        for error in errors:
            print(f"- {error}")
        open("fail.txt","w").write("\n".join(failed_files))
        print("Failures written to fail.txt")
       
    else:
        print("All checks passed successfully.")
    all_catgs = ["correct","mismatch","timeout","exception","total"]
    for benchmark in bench_results:
        for catg in all_catgs:
            if catg not in bench_results[benchmark]:
                bench_results[benchmark][catg] = 0
        bench_results[benchmark]["total"] = sum(bench_results[benchmark].values())
    #Formatting overkill
    df = pd.DataFrame.from_dict(bench_results, orient='index')
    print(f"Timeout limit: {timeout_limit}")
    print(df)
    s = df.sum(numeric_only=True)
    print(s)
    # prepend s to the progress log, annotated by the git repo status
    with open(PROGRESS_LOG, "r+") as f:
        old = f.read()
        f.seek(0, 0)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        f.write(f"{git_repo_status()} timeout {timeout_limit} run {timestamp}\n{df}\n{s}\n\n\n")
        f.write(old)


def git_repo_status():
    """Return the current commit hash as a string, suffixed with `(dirty)` if the working directory is dirty."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True
        )
        commit_hash = result.stdout.strip()
        dirty = re.search(r"^\s*M", subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True
        ).stdout)
        # Get the commit message
        msg_result = subprocess.run(
            ["git", "log", "-1", "--pretty=%s"],
            capture_output=True,
            text=True,
            check=True
        )
        commit_msg = msg_result.stdout.strip()
        return f"{commit_hash} '{commit_msg}'{' (dirty)' if dirty else ''}"
    except Exception as e:
        return f"Error getting git status: {e}"


def checkAllToBool (val):
    """Convert a string representation of truth to true (1) or false (0).
    True values are 'y', 'yes', 't', 'true', 'on', and '1'; false values
    are 'n', 'no', 'f', 'false', 'off', and '0'.  Raises ValueError if
    'val' is anything else.
    """
    val = val.lower()
    if val == "false":
        return False
    else:
        return True
if __name__ == "__main__":
    log_file_path = "results.log"
    arg_parser = ArgumentParser(
        prog="CheckLogs",
        description="Check logs of integ tests",
    )
    arg_parser.add_argument(
        "checkall",
        nargs="?",
        default="true",
        help="ensure whether logs for all testscripts are present",
    )
    arg_dict = vars(arg_parser.parse_args(sys.argv[1:]))
    checkall  = checkAllToBool(arg_dict["checkall"])
    
    check_log_file(log_file_path,checkall)
