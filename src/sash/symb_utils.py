from typing import Iterable, Optional
import z3
from sash.state import SymStr, SymVar, Field


def symbstr_to_str(symbstr : Iterable[str | SymVar]) -> str | None:
    nls : list[str] = []
    for i in symbstr:
        if isinstance(i,str):
            nls.append(i)
        else:
            return None
    return "".join(nls)

def is_constant(field: Field) -> bool:
    return isinstance(field.content, SymStr) and symbstr_to_str(field.content.parts) is not None

def create_fresh_varname(prefix:Optional[str] = None) -> str:
    prefix = prefix if prefix is not None else "vr"
    return str(z3.FreshConst(z3.StringSort(),prefix))

def create_fresh_var(prefix:Optional[str] = None) -> SymVar:
    return SymVar(create_fresh_varname(prefix))


