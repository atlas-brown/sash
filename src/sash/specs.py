from sash.state import *
from sash.constraints import *
from dataclasses import dataclass, replace
import logging
from pash_annotations.parser import parser as pash_annot_parser
from functools import reduce

@dataclass(frozen=True)
class CmdInvocation:
    cmd_name: SymStr
    flags: set[str] # -l, --long, etc.
    options: dict[str, Field] # --option value
    operands: list[Field] # positional arguments

    def __str__(self) -> str:
        arg_str = " ".join([str(arg) for arg in self.operands])
        flag_str = " ".join(self.flags)
        return f"{self.cmd_name} {flag_str} {arg_str}"

@dataclass(frozen=True)
class CmdSpec:
    precond: Constraint
    success_postcond: Constraint # post-condition if exit code is 0
    failure_postcond: Constraint # post-condition if exit code is non-0

# TODO: plug in annotations parsing code
def parse_command(cmd_inv: tuple[Field]) -> CmdInvocation:
    """
    Parses a command invocation from a list of Fields into a CmdInvocation object.
    The CmdInvocation only contains flags in their short form (e.g., '-l' instead of '--long').
    """
    logging.debug(f"Parsing command from fields: {cmd_inv}")

    stringified_cmd = ""
    for field in cmd_inv:
        match field.content:
            case SymStr(parts):
                part_strs = []
                assert all(isinstance(part, str) for part in parts), "SymStr with SymVars not supported in Z3 translation yet"
                for part in parts:
                    match part:
                        case str(s):
                            part_strs.append(s)
                        case SymVar(name):
                            # TODO: This might not work when we handle SymVars
                            part_strs.append(f"${{{name}}}__idx__{cmd_inv.index(field)}")
                stringified_cmd += f" {''.join(part_strs)}"
            case CompletelyArbitrary():
                stringified_cmd += f" $arbitrary__idx__{cmd_inv.index(field)}"
            case _:
                stringified_cmd += " $UNKNOWN"

    cmd_parsed = pash_annot_parser.parse(stringified_cmd.strip())
    logging.debug(f"Parsed command: {cmd_parsed}")
    cmd_flags = set()
    cmd_options = dict()
    cmd_operands = []

    def get_corresponding_field(s: str) -> Field:
        if "__idx__" in s:
            idx = int(s.split("__idx__")[-1])
            return cmd_inv[idx]
        return Field(SymStr((s,)), WordCount(1,1))

    for flag_option in cmd_parsed.flag_option_list:
        match flag_option:
            case pash_annot_parser.Flag():
                cmd_flags.add(flag_option.flag_name)
            case pash_annot_parser.Option():
                option_arg = get_corresponding_field(flag_option.option_arg)
                cmd_options[flag_option.option_name] = option_arg
    for operand in cmd_parsed.operand_list:
        cmd_operands.append(get_corresponding_field(operand.name))

    return CmdInvocation(
        cmd_name=SymStr((cmd_parsed.cmd_name,)),
        flags=cmd_flags,
        options=cmd_options,
        operands=cmd_operands
    )


def get_spec(cmd_name: str | None, cmd_: tuple[Field]) -> CmdSpec | None:
    match cmd_name:
        case "rm":
            return rm_spec(cmd_)
        case _:
            logging.warning(f"No spec found for command '{cmd_name}', treating as no-op.")
            return None


def rm_spec(cmd_: tuple[Field]) -> CmdSpec:
    # https://pubs.opengroup.org/onlinepubs/9799919799/utilities/rm.html

    cmd = parse_command(cmd_)
    logging.debug(f"Ignored irrelevant flags for rm: {cmd.flags - {'-r', '-f'}}")
    (name, flags, options, operands) = (cmd.cmd_name, cmd.flags, cmd.options, cmd.operands)
    cmd = replace(cmd, flags={flag for flag in cmd.flags if flag in {"-r", "-f"}})

    assert name == SymStr(("rm",)), f"Expected rm command, got:\nOriginal: {cmd_}\nPaSh: {cmd}"
    assert len(options) == 0, f"Expected no options for rm, got:\nOriginal: {cmd_}\nPaSh: {cmd}"

    if flags == set(): # rm file...
        # precond:      all operands are files
        # z-postcond:   all operands are deleted
        # nz-postcond:  none (maybe all operands weren't files, maybe one operand wasn't a file, maybe it was a permission issue, etc.)
        return CmdSpec(
            precond=reduce(lambda acc, path: And(acc, IsFile(path)), operands, Empty()),
            success_postcond=reduce(lambda acc, path: And(acc, IsDeleted(path)), operands, Empty()),
            failure_postcond=Empty())
    elif flags == set(["-f"]): # rm -f file...
        # precond:      all operands are not directories [and for bug-catching purposes: all operands are not deleted]
        # z-postcond:   all operands are deleted
        # nz-postcond:  none (maybe permission issue, etc.)
        return CmdSpec(
            precond=reduce(lambda acc, path: And(acc, And(Not(IsDir(path)), Not(IsDeleted(path)))), operands, Empty()),
            success_postcond=reduce(lambda acc, path: And(acc, IsDeleted(path)), operands, Empty()),
            failure_postcond=Empty())
    elif flags == set(["-r"]): # rm -r file...
        # precond:      all operands are files or directories
        # z-postcond:   all operands are deleted
        # nz-postcond:  none (maybe permission issue, etc.)
        return CmdSpec(
            precond=reduce(lambda acc, path: And(acc, Or(IsFile(path), IsDir(path))), operands, Empty()),
            success_postcond=reduce(lambda acc, path: And(acc, IsDeleted(path)), operands, Empty()),
            failure_postcond=Empty())
    elif flags == set(["-r", "-f"]): # rm -r -f file...
        # precond:      [for bug catching purposes: all operands are not deleted]
        # z-postcond:   all operands are deleted
        # nz-postcond:  none (maybe permission issue, etc.)
        return CmdSpec(
            precond=reduce(lambda acc, path: And(acc, Not(IsDeleted(path))), operands, Empty()),
            success_postcond=reduce(lambda acc, path: And(acc, IsDeleted(path)), operands, Empty()),
            failure_postcond=Empty())
    else:
        # TODO: implement a default case (need to look at every rm flag and derive the most detailed but correct spec)
        raise NotImplementedError(f"Unhandled rm invocation:\n{cmd_}\n{cmd}")


def mkdir_spec(cmd_: list[Field]) -> CmdSpec:
    # https://pubs.opengroup.org/onlinepubs/9799919799/
    # mkdir [-p] [-m mode] dir...
    #
    # The mkdir utility shall create the directories specified by the operands, in the order specified.
    #
    # -m mode
    #     Set the file mode (permissions) of the new directories to mode.
    # -p
    #     Create any missing intermediate pathname components.
    #     Each dir operand that names an existing directory shall be ignored without error.

    # Keep in mind:
    #     The file system model is a flat map, there is no hierarchy of directories.
    #     So `mkdir -p a/b` will not be assumed to fail if `a` is a file, even though in reality it would.

    cmd = parse_command(cmd_)
    (name, flags, _, operands) = (cmd.cmd_name, cmd.flags, cmd.options, cmd.operands)
    logging.debug(f"Ignored irrelevant flags for rm: {cmd.flags - {'-p'}}")
    flags.discard("-m")  # ignore -m flag

    assert name == SymStr(("mkdir",)), "Expected mkdir command, got: {name}"

    if flags == set(["-p"]):
        # precond: all operands are not files (mkdir -p doesn't error if directories exists)
        # z-postcond: all operands are directories
        # nz-postcond: none (maybe permission issue, etc.)
        return CmdSpec(
            precond=reduce(lambda acc, path: And(acc, Not(IsFile(path))), operands, Empty()),
            success_postcond=reduce(lambda acc, path: And(acc, IsDir(path)), operands, Empty()),
            failure_postcond=Empty())
    elif flags == set():
        # precond: all operands do not exist
        # z-postcond: all operands are directories
        # nz-postcond: none (maybe permission issue, etc.)
        return CmdSpec(
            precond=reduce(lambda acc, path: And(acc, Not(Or(IsFile(path), IsDir(path)))), operands, Empty()),
            success_postcond=reduce(lambda acc, path: And(acc, IsDir(path)), operands, Empty()),
            failure_postcond=Empty())
    else:
        logging.warning(f"Encountered unsupported mkdir invocation: {cmd_}")
        # treat all other invocations as no-ops
        return CmdSpec(
            precond=Empty(),
            success_postcond=Empty(),
            failure_postcond=Empty())


def cd_spec(cmd_: list[Field]) -> CmdSpec:
    # https://pubs.opengroup.org/onlinepubs/9799919799/
    # cd [-L] [directory]
    # cd -P [-e] [directory]
    #
    # The cd utility shall change the working directory of the current shell execution environment
    #
    # -e
    #     If the -P option is in effect, the current working directory is successfully changed, and the correct value of the PWD environment variable cannot be determined, exit with exit status 1.
    # -L
    #     Handle the operand dot-dot logically; symbolic link components shall not be resolved before dot-dot components are processed (see steps 8. and 9. in the DESCRIPTION).
    # -P
    #     Handle the operand dot-dot physically; symbolic link components shall be resolved before dot-dot components are processed (see step 7. in the DESCRIPTION).
    #
    # If both -L and -P options are specified, the last of these options shall be used and all others ignored. If neither -L nor -P is specified, the operand shall be handled dot-dot logically; see the DESCRIPTION.

    # Note:
    #     All flags determine how `..` is handled with respect to symlinks.
    #     Since we do not model symlinks, we do not handle these flags either.
    # Note:
    #     Invocations with multiple operands should fail, but we treat them as no-ops here.

    cmd = parse_command(cmd_)
    (name, flags, _, operands) = (cmd.cmd_name, cmd.flags, cmd.options, cmd.operands)

    assert name == SymStr(("cd",)), "Expected cd command, got: {name}"

    # TODO?: handle case of zero operands (cd to home directory)
    # ? cd also has many interactions with environment variables (PWD, HOME, OLDPWD, CDPATH)

    if flags == set() and len(operands) == 1:
        # TODO?: handle case of '-' operand (cd to previous directory)
        # precond: operand is a directory
        # z-postcond: operand is a directory
        # nz-postcond: none (maybe operand not a directory, maybe permission issue, etc.)
        return CmdSpec(
            precond=IsDir(operands[0].content),
            success_postcond=IsDir(operands[0].content),
            failure_postcond=Empty())
    else:
        logging.warning(f"Encountered unsupported cd invocation: {cmd_}")
        # treat all other invocations as no-ops
        return CmdSpec(
            precond=Empty(),
            success_postcond=Empty(),
            failure_postcond=Empty())


def cp_spec(cmd_: list[Field]) -> CmdSpec:

    raise NotImplementedError("cp spec not implemented yet")

def mv_spec(cmd_: list[Field]) -> CmdSpec:
    raise NotImplementedError("mv spec not implemented yet")

def grep_spec(cmd_: list[Field]) -> CmdSpec:
    raise NotImplementedError("grep spec not implemented yet")

def echo_spec(cmd_: list[Field]) -> CmdSpec:
    raise NotImplementedError("echo spec not implemented yet")

def command_spec(cmd_: list[Field]) -> CmdSpec:
    raise NotImplementedError("command spec not implemented yet (the builtin)")
