import shasta.ast_node as AST

from sash import (
    casep,
    specs,
    syfi,
    symb_datatypes,
    symb_utils,
)
from sash.defs import Symbstr
from sash.expansionstate import SetOptionStore, SymbExitCode, VarStore
from sash.nodelist import NodeList, get_path_var

def handle_unset(curn: NodeList, proccmd: AST.CommandNode) -> NodeList:
    arg_chars = proccmd.arguments[1:]  # Skip the "unset" command itself
    args = [
        symb_utils.argchar_conc_panic(arg, "unset expected concrete argument")
        for arg in arg_chars
    ]
    curn.add_commandtrack(f"unset {args}", 0,curn.checked_pos)
    unset_var = True
    index = 0
    if args[0] == "-f":
        unset_var = False
        index += 1
    elif args[0] == "-v":
        index += 1
    for arg in args[index:]:
        if unset_var:
            curn.unset_variable(arg)
        else:
            curn.unset_function(arg)
    return curn



def handle_export(curn: NodeList, proccmd: AST.CommandNode) -> NodeList:
    # LL TODO: refactor to reuse assignment code
    arg_chars : list[Symbstr] = proccmd.arguments[1:]  # type: ignore 
    curn.add_commandtrack(f"export {str(arg_chars)}", 0,curn.checked_pos)
    for arg in arg_chars:
        if arg== "-p":
            continue
        elif arg == "--":
            break
        elif any( isinstance(c,str) and "=" in c  for c in arg):
            idx = 0
            sidx = 0 
            for idx, c in enumerate(arg):
                if isinstance(c,str) and "=" in c:
                    sidx = c.index("=")
                    break
            vr = arg[:idx] + [arg[idx][:sidx]]
            val = [arg[idx][sidx+1:]] + arg[idx+1:] # type: ignore
            vr = symb_utils.symbstr_to_str(vr)
            assert vr is not None 
            curn.add_varstore(
                vr,
                VarStore(
                    flag=None,
                    value=val,
                    is_loop_index=False,
                    readonly=False,
                    export=False,
                ),
            )
        else:
            # Just a var
            varname = symb_utils.symbstr_to_str( arg)
            assert varname is not None
            curn.set_readonly(varname)
    return curn


# TODO-R this probably needs to be cleaned up
def handle_local_vars(node: AST.CommandNode, curn: NodeList) -> bool:
    if (
        len(node.arguments) == 0
        or symb_utils.string_of_arg(node.arguments[0]) != "local"
    ):
        return False
    if not curn.is_function_body():
        raise SyntaxError("Detected local keyword outside of function body")

    if len(node.arguments) != 2:
        logging.info(
            f"Expected local to only take a single argument. Got {len(node.arguments)}"
        )

    found = False
    idx = 0
    for idx in range(len(node.arguments[1])):
        if str(node.arguments[1][idx]) == "=":
            found = True
            break
    if not found:
        logging.debug("Did not find assignment. Assuming just declaration")
        idx = len(node.arguments[1])

    # TODO-RRRR this does not account for malformed variable names
    vr = symb_utils.string_of_arg([i for i in node.arguments[1][:idx]])
    vl = symb_utils.symb_string_of_arg([i for i in node.arguments[1][idx + 1 :]])
    vl : Symbstr = vl if len(vl) > 0 else [""]
    curn.add_variable(vr,None,vl,local=True)
    node.arguments = []  # Clear it to avoid any further processing TODO-RRR
    node.assignments = []
    return True

