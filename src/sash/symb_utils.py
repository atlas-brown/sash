from typing import Optional

import shortuuid
from shasta.ast_node import QUOTED, UNQUOTED, ArgChar, EArgChar, QArgChar, VArgChar
import z3
from sash.state import SymStr, SymVar


def string_of_arg(args, quote_mode=UNQUOTED):
    i = 0
    text = []
    while i < len(args):
        if isinstance(args[i],str):
            text.append(args[i])
            i = i+1
            continue
        c = args[i].pretty(quote_mode=quote_mode)
        if c == "$" and (i+1 < len(args)) and isinstance(args[i+1],EArgChar):
            c = "\\$"
        text.append(c)

        i = i+1

    text = "".join(text)

    return text

def symb_string_of_arg(args : list[ArgChar], quote_mode=UNQUOTED) -> SymStr:
    i = 0
    text = []
    for i in range(len(args)):
        if isinstance(args[i], VArgChar):
            raise ValueError("Trying to turn variable into string")
        if isinstance(args[i], SymVar):
            text.append(args[i])
            continue
        if isinstance(args[i],QArgChar):
            text.extend(symb_string_of_arg(args[i].arg,quote_mode=QUOTED))
            continue
        if isinstance(args[i],str):
            text.append(args[i])
            continue
        c = args[i].pretty(quote_mode=quote_mode) # type: ignore
        if c == "$" and (i + 1 < len(args)) and isinstance(args[i + 1], EArgChar):
            c = "\\$"
        text.append(c)
    # Join all the strs
    result = []
    current_str = ""
    for item in text:
        if isinstance(item, str):
            current_str += item
        else:
            if current_str:
                result.append(current_str)
                current_str = ""
            result.append(item)
    if current_str:
        result.append(current_str)
    return result

def symbstr_to_str(symbstr : list[str | SymVar]) -> Optional[str]:
    nls : list[str] = []
    for i in symbstr:
        if isinstance(i,str):
            nls.append(i)
        else:
            return None
    return "".join(nls)

def argchar_conc_panic(ls : list[ArgChar],panic_msg:str="") -> str:
    if (res := argchar_conc(ls)) is not None:
        return res
    else:
        raise ValueError(f"{panic_msg}: Expected concrete string but got symbolic string")

def argchar_conc(ls : list[ArgChar]) -> Optional[str]:
    symb_ls = symb_string_of_arg(ls)
    conc_str = symbstr_to_str(symb_ls)
    return conc_str

#TODO-R eventually make this determinstic?
def create_fresh_varname(prefix:Optional[str] = None) -> str:
    prefix = prefix if prefix is not None else "vr"
    return str(z3.FreshConst(z3.StringSort(),prefix))

def create_fresh_var(prefix:Optional[str] = None) -> SymVar:
    return SymbArgChar(create_fresh_varname(prefix))

def is_float(s: str) -> bool:
    try:
        float(s)
        return True
    except Exception:
        return False

def is_special_param(s:str) -> bool:
    return s in ["@", "*", "#", "$", "?", "_","-"]

def is_pos_param(s:str) -> bool:
    return s.isnumeric()


def repr_symbstr(symbstr : SymStr) -> str:
    return "".join([i.pretty() if isinstance(i,SymbArgChar) else i  for i in symbstr])


def assert_issymbstr(arg_ls : list[ArgChar]):
    for arg in arg_ls:
        assert isinstance(arg,SymbArgChar) or isinstance(arg,str)

