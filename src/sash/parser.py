import dataclasses
import logging
import pathlib
import sys

import libdash
import shasta.ast_node as AST
import shasta.gosh_to_shasta_ast as shasta_shfmt_to_ast
from pash_annotations.datatypes.BasicDatatypes import Flag, FlagOption, Operand, Option
from pash_annotations.parser.parser import (
    are_all_individually_flags,
    get_dict_flag_to_primary_repr,
    get_dict_option_to_primary_repr,
    get_set_of_all_flags,
    get_set_of_all_options,
)
from pash_annotations.parser.util_parser import get_json_data
from shasta.json_to_ast import to_ast_node


@dataclasses.dataclass(frozen=True)
class WrappedAst:
    ast_node: AST.AstNode
    rawtext: str
    line_before: int
    line_after: int  # relevant for mysterious shell reasons
    cmd_linno: int | None = None

    def get_line_number(self) -> int:
        """
        Returns the line number of the node.
        If the node is an `NCMD` node and the `linno` field is available, returns its value.
        Otherwise, returns the line number before the node.
        """
        return self.cmd_linno or self.line_before + 1


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
    parser_backend = detect_script_parser(script_path)
    logging.debug("Selected parser backend '%s' for %s", parser_backend, script_path)
    if parser_backend == "shfmt":
        return parse_with_shasta_shfmt_bridge(script_path)
    return parse_with_libdash(script_path)


def detect_script_parser(script_path: str) -> str:
    shebang_line = read_shebang_line(script_path)
    if shebang_line is None:
        return "libdash" # Default to libdash if no shebang line is present

    interpreter = parse_shebang_interpreter(shebang_line)
    if interpreter in {"bash"}:
        return "shfmt"
    return "libdash"


def read_shebang_line(script_path: str) -> str | None:
    try:
        with open(script_path, encoding="utf-8", errors="surrogateescape") as script_file:
            first_line = script_file.readline()
    except OSError:
        return None

    if not first_line.startswith("#!"):
        return None
    return first_line[2:].strip()


def parse_shebang_interpreter(shebang_line: str) -> str | None:
    if shebang_line == "":
        return None

    tokens = shebang_line.split()
    if not tokens:
        return None

    executable = pathlib.Path(tokens[0]).name
    if executable != "env":
        return executable

    for token in tokens[1:]:
        # Skip env flags (e.g., `-S`) and use the first positional executable.
        if token.startswith("-"):
            continue
        return pathlib.Path(token).name
    return None


def parse_with_shasta_shfmt_bridge(script_path: str) -> list[WrappedAst]:
    print("Bash scripts are not suported yet")
    print("Change your shebang to 'sh' and make sure you only use POSIX-compatible constructs")
    sys.exit(1)

    # TODO: Fix shfmt bridge

    logging.debug("Parsing %s using shasta shfmt bridge", script_path)
    shasta_nodes = shasta_shfmt_to_ast.parse(script_path)
    wrapped_nodes = []
    for idx, shasta_node in enumerate(shasta_nodes):
        node_line_number = getattr(shasta_node, "line_number", None)
        if isinstance(node_line_number, int) and node_line_number > 0:
            line_before = node_line_number - 1
            cmd_linno = node_line_number
        else:
            line_before = idx
            cmd_linno = None
        wrapped_nodes.append(
            WrappedAst(
                ast_node=shasta_node,
                rawtext="",
                line_before=line_before,
                line_after=line_before,
                cmd_linno=cmd_linno,
            )
        )
    return wrapped_nodes


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

        # Extract the `linno` field from `NCMD` nodes if available.
        # Only `NCMD` nodes have the `ncmd` structure attribute with a `linno` field, while other node types do not have this field.
        cmd_linno = getattr(libdash_node, 'ncmd', {}).get('linno', None)

        wrapped_node = WrappedAst(shasta_node, rawtext or "", linno_before, linno_after, cmd_linno)
        wrapped_nodes.append(wrapped_node)

    logging.debug(
        "Finished parsing; script consists of %d node%s",
        len(wrapped_nodes),
        '' if len(wrapped_nodes) == 1 else 's'
    )

    return wrapped_nodes


# TODO: Delete this
def annot_parser_wrapper(str_ls_args: list[str]) -> ParsedCommand:
    # split all terms (command, flags, options, arguments, operands)
    parsed_elements_list: list[str] = str_ls_args

    cmd_name: str = parsed_elements_list[0]
    json_data = get_json_data(cmd_name)
    # TODO: if there is an element "\n", we lose the quotation marks currently

    set_of_all_flags: set[str] = get_set_of_all_flags(json_data)
    dict_flag_to_primary_repr: dict[str, str] = get_dict_flag_to_primary_repr(json_data)
    set_of_all_options: set[str] = get_set_of_all_options(json_data)
    dict_option_to_primary_repr: dict[str, str] = get_dict_option_to_primary_repr(
        json_data
    )
    # dict_option_to_class_for_arg: dict[str, WhichClassForArg] = get_dict_option_to_class_for_arg(json_data)

    # parse list of command invocation terms
    flag_option_list: list[FlagOption] = []
    i = 1
    while i < len(parsed_elements_list):
        potential_flag_or_option = parsed_elements_list[i]
        if potential_flag_or_option in set_of_all_flags:
            flag_name_as_string: str = dict_flag_to_primary_repr.get(
                potential_flag_or_option, potential_flag_or_option
            )
            flag: Flag = Flag(flag_name_as_string)
            flag_option_list.append(flag)
        elif (potential_flag_or_option in set_of_all_options) and (
            (i + 1) < len(parsed_elements_list)
        ):
            option_name_as_string: str = dict_option_to_primary_repr.get(
                potential_flag_or_option, potential_flag_or_option
            )
            option_arg_as_string: str = parsed_elements_list[i + 1]
            option = Option(option_name_as_string, option_arg_as_string)
            flag_option_list.append(option)
            i += 1  # since we consumed another term for the argument
        elif are_all_individually_flags(potential_flag_or_option, set_of_all_flags):
            for split_el in list(potential_flag_or_option[1:]):
                flag: Flag = Flag(f"-{split_el}")
                flag_option_list.append(flag)
        else:
            break  # next one is Operand, and we keep these in separate list
        i += 1

    # we would probably want to skip '--' but then the unparsed command could have a different meaning so we'd need to keep it
    # for now, omitted
    # if parsed_elements_list[i] == '--':
    #     i += 1

    # operand_list = [Operand(operand_name) for operand_name in parsed_elements_list[i:]]
    operand_list = []
    idx_list = []
    for idx in range(i, len(parsed_elements_list)):
        operand_list.append(Operand(parsed_elements_list[idx]))
        idx_list.append(idx)

    return ParsedCommand(cmd_name,
                         flag_option_list,
                         operand_list,
                         idx_list)
