import dataclasses
import logging
import pathlib
import sys

import libdash
import shasta.ast_node as AST
from pash_annotations.datatypes.BasicDatatypes import FlagOption, Operand
from shasta.json_to_ast import to_ast_node

@dataclasses.dataclass(frozen=True)
class WrappedAst:
    ast_node: AST.AstNode
    rawtext: str
    line_before: int
    line_after: int  # relevant for mysterious shell reasons

    def get_line_number(self) -> int:
        """
        Returns the line number of the node.
        """
        res = self.line_before + 1
        if hasattr(self.ast_node, "line_number"):
            ln = getattr(self.ast_node, "line_number")
            if ln is not None:
                res = ln

        return res


@dataclasses.dataclass(frozen=True)
class ParsedCommand:
    name: str
    flag_options: list[FlagOption]
    operands: list[Operand]
    str_to_idx: list[int]


# Parse a shell script to an AST straight
# through python without calling an external executable
LIBDASH_INITIALIZED = False
def parse_shell_script(script_path: str) -> list[WrappedAst]:
    return parse_with_libdash(script_path)


def parse_with_libdash(script_path: str) -> list[WrappedAst]:
    global LIBDASH_INITIALIZED

    logging.debug("Parsing %s", script_path)
    try:
        parsed_data = [l for l in libdash.parse(script_path, init=not LIBDASH_INITIALIZED)]
    except libdash.parser.ParsingException as e:
        with pathlib.Path(script_path).open() as f:
            _ = f.readline()
            nl = f.newlines

        if nl == "\r\n":
            print("Parsing error most likely caused by CRLF line endings")
            print("Are you using Windows? Convert your script to LF line endings and try again")
            sys.exit(1)
        else:
            raise e

    LIBDASH_INITIALIZED = True

    # Transform the untyped ast objects to typed ones
    wrapped_nodes = []
    for libdash_node, rawtext, linno_before, linno_after in parsed_data:
        shasta_node = to_ast_node(libdash_node)
        wrapped_node = WrappedAst(shasta_node, rawtext or "", linno_before, linno_after)
        wrapped_nodes.append(wrapped_node)

    logging.debug(
        "Finished parsing; script consists of %d node%s",
        len(wrapped_nodes),
        '' if len(wrapped_nodes) == 1 else 's'
    )

    return wrapped_nodes
