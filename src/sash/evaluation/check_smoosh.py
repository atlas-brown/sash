import os
from os.path import dirname
from shseer.nodelist import NodeList
from shseer.solver_datatypes import QueryResult
from shseer.symb import nodes_from_file
from shseer.symb_node import SymbNode, Symbstr
from typing import Optional
from shseer.controlflow import Exit,Done
from shseer import reporter, specs
from enum import Enum
from shseer.reporter import SmooshResult
import json
from pathlib import Path
import sys
import logging

SMOOSH_DIR =  os.path.join(dirname(os.path.realpath(__file__)),"benchmarks","smoosh","tests")
print("SMOOSH_DIR",SMOOSH_DIR)
assert os.path.isdir(SMOOSH_DIR)

def stdout_file_lines(test_name:str) -> Optional[list[str]]:
    outfile = test_name + ".out"
    stdout_file_path=os.path.join(SMOOSH_DIR, outfile)
    if not os.path.exists(stdout_file_path):
        print(f"stdout file {stdout_file_path} does not exist")
        return None
   
    with open(stdout_file_path) as f:
        return [line.rstrip("\n") for line in f.readlines()]
    
def ec_file_lines(test_name:str) -> Optional[int]:
    outfile = test_name + ".ec"
    ec_file_path=os.path.join(SMOOSH_DIR, outfile)
    if not os.path.exists(ec_file_path):
        print(f"ec file {ec_file_path} does not exist")
        return None
    with open(ec_file_path) as f:
        return int(f.readline().rstrip("\n"))
    
def nodes_from_name(name:str):
    return nodes_from_file(name)

def conc_stdout_panic(n : SymbNode) -> list[str]:
    # assert not n.symb_halt
    lines = n.stdout
    res = []
    for line in lines:
        nls = []
        for c in line:
            match c:
                case str(vl):
                    nls.append(vl)
                case _:
                    assert False
        res.append("".join(nls))
    return res

def symbout_to_str(symbout : Symbstr) -> Optional[str]:
    return None
    if all([isinstance(i,str) for i in symbout]):
        return "".join(symbout)
    else:
        return None

def check_stdout(expected_stdout :Optional[list[str]],n : NodeList) -> SmooshResult:
    return SmooshResult.MISMATCH
    if expected_stdout is None:
        return SmooshResult.ABSTRACT
    if len(expected_stdout) != len(n.stdout):
        print(f"Expected {len(expected_stdout)} lines got {len(n.stdout)}")
        return SmooshResult.MISMATCH 
    all_conc = True 
    for expec_line,act_line in zip(expected_stdout,n.stdout):
        if (conc_act_line := symbout_to_str(act_line)) is not None:
            if expec_line != act_line:
                # print(f"{filename} Expected output {expec_line} got {act_line}")
                return SmooshResult.MISMATCH
        else:
            all_conc = False   
            if not n.query_eq(specs.make_arg_sexp(expec_line),specs.make_argls_sexp(act_line)):
                # print(f"{filename} Expected output {expec_line} got Symb {act_line} that doesn't match")
                return SmooshResult.MISMATCH
    if all_conc:
        return SmooshResult.CONCRETE
    else:
        return SmooshResult.ABSTRACT
        
        
    


def check_exit_code(n : NodeList,exit_code:Optional[int]) ->SmooshResult:
    return SmooshResult.MISMATCH
    if exit_code is None:
        return SmooshResult.ABSTRACT
    match n.cflow:
        case Done() |  Exit(_):
            act_code_arg = specs.make_argls_sexp(n.get_exit_code().to_symbstr())
            expected_code_str = str(exit_code)
            match res :=  n.query_eq(act_code_arg,specs.make_arg_sexp(expected_code_str)):
               case QueryResult.Always:
                   return SmooshResult.CONCRETE
               case QueryResult.Feasible:
                   return SmooshResult.ABSTRACT
               case _:
                   pass
        case _:
            raise AssertionError(f"Expected Done or Exit got {n.cflow} ")
    # print(f"Expected {exit_code} code but got {act_code_arg}")
    return SmooshResult.MISMATCH
        
def file_test(filepath,noec_ok:bool = True) -> str:
    curn = nodes_from_name(filepath)
    basename_stem = Path(filepath).stem
    stdout_lines = stdout_file_lines(basename_stem)
    exit_code = ec_file_lines(basename_stem)
    assert noec_ok or exit_code is not None
    if len(curn.nodes) == 0:
        print(f"File {filepath} has no nodes")
        return json.dumps(reporter.REPORTER.get_smoosh_report(SmooshResult.MISMATCH,SmooshResult.MISMATCH))
    all_code_res = SmooshResult.CONCRETE
    all_stdout_res = SmooshResult.CONCRETE
    exit_code_res = check_exit_code(curn,exit_code)
    stdout_res = check_stdout(stdout_lines,curn)
    if exit_code_res == SmooshResult.MISMATCH or stdout_res == SmooshResult.MISMATCH:
        return json.dumps(reporter.REPORTER.get_smoosh_report(exit_code_res,stdout_res))
    if exit_code_res == SmooshResult.ABSTRACT:
        all_code_res= SmooshResult.ABSTRACT
    if stdout_res == SmooshResult.ABSTRACT:
            all_stdout_res = SmooshResult.ABSTRACT 
    return json.dumps(reporter.REPORTER.get_smoosh_report(all_code_res,all_stdout_res))

if __name__ == "__main__":
    filearg = sys.argv[1]
    logging.basicConfig(level=logging.CRITICAL)
    print(file_test(filearg))
    
        