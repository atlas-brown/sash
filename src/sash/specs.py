import inspect
import logging
import sys
from dataclasses import dataclass, replace
from functools import reduce

from pash_annotations.parser import parser as pash_annot_parser

from sash.constraints import *
from sash.state import *


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

# -- Specs start here --

def cd_spec(cmd_: tuple[Field]) -> CmdSpec:
    # https://pubs.opengroup.org/onlinepubs/9799919799/utilities/cd.html

    # Note:
    #     All flags determine how `..` is handled with respect to symlinks.
    #     Since we do not model symlinks, we do not handle these flags either.

    # Note:
    #     Invocations with multiple operands should fail, but we treat them as no-ops here.

    cmd = parse_command(cmd_)
    (name, flags, _, operands) = (cmd.cmd_name, cmd.flags, cmd.options, cmd.operands)

    assert name == SymStr(("cd",)), f"Expected cd command, got: {name}"

    # NOTE: we might want to ignore the postconditions, i suspect they might overcomplicate things
    # NOTE: cd also interacts with CDPATH sometimes, do we care?

    if flags == set() and len(operands) == 0: # cd
        # precond:      $HOME is a directory
        # z-postcond:   $HOME is a directory, $PWD is $HOME, $OLDPWD is previous $PWD
        # nz-postcond:  none (maybe $HOME not set, maybe $HOME not a directory, maybe permission issue, etc.)
        return CmdSpec( # ? is this correct?
            precond=IsDir(Field(SymStr(("HOME",)), WordCount(1,1))),
            success_postcond=And(
                IsDir(Field(SymStr(("HOME",)), WordCount(1,1))),
                And(
                    StringEq(
                        Field(SymStr(("OLDPWD",)), WordCount(1,1)), Field(SymStr(("PWD",)), WordCount(1,1))),
                    StringEq(
                        Field(SymStr(("PWD",)), WordCount(1,1)), Field(SymStr(("HOME",)), WordCount(1,1)))
                )
            ),
            failure_postcond=Empty())
    elif flags == set() and len(operands) == 1 and operands[0] == Field(SymStr(("-",)), WordCount(1,1)): # cd -
        # precond:      $OLDPWD is a directory
        # z-postcond:   $OLDPWD is a directory, $PWD is $OLDPWD, $OLDPWD is previous $PWD
        # nz-postcond:  none (maybe $OLDPWD not set, maybe $OLDPWD not a directory, maybe permission issue, etc.)
        return CmdSpec( # ? is this correct?
            precond=IsDir(Field(SymStr(("OLDPWD",)), WordCount(1,1))),
            success_postcond=And(
                IsDir(Field(SymStr(("OLDPWD",)), WordCount(1,1))),
                And(
                    StringEq(
                        Field(SymStr(("OLDPWD",)), WordCount(1,1)), Field(SymStr(("PWD",)), WordCount(1,1))),
                    StringEq(
                        Field(SymStr(("PWD",)), WordCount(1,1)), Field(SymStr(("OLDPWD",)), WordCount(1,1)))
                )
            ),
            failure_postcond=Empty())
    elif flags == set() and len(operands) == 1: # cd dir
        # precond:      operand is a directory
        # z-postcond:   operand is a directory
        # nz-postcond:  none (maybe operand not a directory, maybe permission issue, etc.)
        return CmdSpec(
            precond=IsDir(operands[0]),
            success_postcond=IsDir(operands[0]),
            failure_postcond=Empty())
    else:
        # TODO: implement a default case (need to look at every cd flag and derive the most detailed but correct spec)
        raise NotImplementedError(f"Unhandled cd invocation:\n{cmd_}\n{cmd}")


def command_spec(cmd_: tuple[Field]) -> CmdSpec:
    # https://pubs.opengroup.org/onlinepubs/9799919799/utilities/command.html

    # A note on 'command -v/-V cmd':
    # - Currently, we only care about the failure case, which informs us that a command does not exist
    # - This works because our default assumption for unknown commands is that they exist
    # - If at any point we decide to change this default assumption, we might need to revisit this spec
    #
    # Issue when modeling the success case:
    # - 'command -v/-V cmd' identifies commands, built-ins, aliases, functions, *and reserved words*
    # - The first four are covered by a simple CommandExists(cmd) constraint, but reserved words are tricky
    #
    # Consider the following example:
    # ```
    # if ! command -v if; then # output: "if", exit code: 0
    #     exit 1
    # fi
    # \if # error: "if: command not found", exit code: non-0
    # ```
    #
    # - The previously mentioned success spec would make this code seem not buggy, even though it is
    # - Realistically this will never be an issue, however it is still technically incorrect
    # - In order to model this correctly we would need to have a way to check if cmd is a shell reserved word
    #   - But what if a CompletelyArbitrary is passed as cmd?
    #
    # - Another idea (which would probably be useless in practice) is to have the precondition that cmd cannot be a reserved word,
    #   with the "intuition" being that the command turns to a no-op (at best) or to a const cond (at worst)

    cmd = parse_command(cmd_)
    (name, flags, _, operands) = (cmd.cmd_name, cmd.flags, cmd.options, cmd.operands)

    assert name == SymStr(("command",)), f"Expected command command, got: {name}"

    # TODO: Model `alias` to create CommandExists constraints for aliases
    # Otherwise the following (plausible) code would be marked as buggy:
    # ```
    # if ! command -v my_alias; then
    #     alias my_alias='ls -l'
    # fi
    # my_alias

    if flags == set(["-v"]) and len(operands) == 1: # command -v cmd
        # precond:      none
        # z-postcond:   none (cmd might be a reserved word, see note above)
        # nz-postcond:  cmd is not a command
        return CmdSpec(
            precond=Empty(),
            success_postcond=Empty(),
            failure_postcond=Not(CommandExists(operands[0])))
    else:
        assert False, f"Unhandled command invocation:\n{cmd_}\n{cmd}"


def cp_spec(cmd_: tuple[Field]) -> CmdSpec:
    # https://pubs.opengroup.org/onlinepubs/9799919799/utilities/cp.html

    cmd = parse_command(cmd_)
    (name, flags, _, operands) = (cmd.cmd_name, cmd.flags, cmd.options, cmd.operands)

    assert name == SymStr(("cp",)), f"Expected cp command, got: {name}"

    if flags == set() and len(operands) == 2: # cp src dst
        # precond:      src is a file, dst is not a directory or an unread file
        # z-postcond:   src is a file, dst is an unread file
        # nz-postcond:  none (maybe permission issue)
        src, dst = operands[0], operands[1]
        return CmdSpec(
            precond=IsFile(src) & (~IsDir(dst) & (IsFile(dst) >> ~IsUnread(dst))),
            success_postcond=IsFile(src) & (IsFile(dst) & IsUnread(dst)),
            failure_postcond=Empty())
    elif flags == set() and len(operands) >= 2: # cp src... dst
        # precond:      all src are files, dst is a directory
        # z-postcond:   all src are files, dst is a directory, all src are copied as unread files into dst
        # nz-postcond:  none (maybe permission issue)
        srcs, dst = operands[:-1], operands[-1]
        return CmdSpec(
            precond=And.from_field_iter(srcs, IsFile) & IsDir(dst),
            success_postcond=And.from_field_iter(srcs, IsFile) & IsDir(dst),
                # TODO: how to do that? should we do that?
                # & And.from_field_iter(srcs, lambda path: IsUnread(ConcatPath(dst, path))),
            failure_postcond=Empty())
    else:
        assert False, f"Unhandled cp invocation:\n{cmd_}\n{cmd}"


def echo_spec(cmd_: tuple[Field]) -> CmdSpec:
    # https://pubs.opengroup.org/onlinepubs/9799919799/utilities/echo.html

    # TODO: if HasStdout() is not handled here somehow, this spec is useless

    cmd = parse_command(cmd_)
    (name, flags, _, operands) = (cmd.cmd_name, cmd.flags, cmd.options, cmd.operands)

    assert name == SymStr(("echo",)), f"Expected echo command, got: {name}"

    if flags == set(): # echo arg...
        # precond:      none
        # z-postcond:   none
        # nz-postcond:  none
        return CmdSpec(
            precond=Empty(),
            success_postcond=Empty(),
            failure_postcond=Empty())
    else: # POSIX actually does not define any flags for echo
        assert False, f"Unhandled echo invocation:\n{cmd_}\n{cmd}"


def grep_spec(cmd_: tuple[Field]) -> CmdSpec:
    # https://pubs.opengroup.org/onlinepubs/9799919799/utilities/grep.html

    cmd = parse_command(cmd_)
    (name, flags, _, operands) = (cmd.cmd_name, cmd.flags, cmd.options, cmd.operands)

    assert name == SymStr(("grep",)), f"Expected grep command, got: {name}"

    if flags == set() and len(operands) == 0:
        # TODO: how to encode that this case reads from stdin?
        # precond:      none
        # z-postcond:   none
        # nz-postcond:  none
        return CmdSpec(
            precond=Empty(),
            success_postcond=Empty(),
            failure_postcond=Empty())
    elif flags == set() and len(operands) >= 1: # grep pattern file...
        # precond:      all operands are files
        # z-postcond:   all operands are files
        # nz-postcond:  none (maybe permission issue, etc.)
        files = operands[1:]
        return CmdSpec(
            precond=And.from_field_iter(files, IsFile),
            success_postcond=And.from_field_iter(files, IsFile),
            failure_postcond=Empty())
    else:
        assert False, f"Unhandled grep invocation:\n{cmd_}\n{cmd}"


def mkdir_spec(cmd_: tuple[Field]) -> CmdSpec:
    # https://pubs.opengroup.org/onlinepubs/9799919799/utilities/mkdir.html

    # Note:
    #     The file system model is a flat map, there is no hierarchy of directories.
    #     So `mkdir -p a/b` will not be assumed to fail if `a` is a file, even though in reality it would.

    cmd = parse_command(cmd_)
    (name, flags, _, operands) = (cmd.cmd_name, cmd.flags, cmd.options, cmd.operands)
    logging.debug(f"Ignored irrelevant flags for rm: {cmd.flags - {'-p'}}")
    flags.discard("-m") # ignore -m flag

    assert name == SymStr(("mkdir",)), f"Expected mkdir command, got: {name}"

    if flags == set(["-p"]): # mkdir -p dir...
        # precond:      all operands are not files (mkdir -p doesn't error if directories exists)
        # z-postcond:   all operands are directories
        # nz-postcond:  none (maybe permission issue, etc.)
        return CmdSpec(
            precond=reduce(lambda acc, path: And(acc, Not(IsFile(path))), operands, Empty()),
            success_postcond=reduce(lambda acc, path: And(acc, IsDir(path)), operands, Empty()),
            failure_postcond=Empty())
    elif flags == set(): # mkdir dir...
        # precond:      all operands do not exist
        # z-postcond:   all operands are directories
        # nz-postcond:  none (maybe permission issue, etc.)
        return CmdSpec(
            precond=reduce(lambda acc, path: And(acc, Not(Or(IsFile(path), IsDir(path)))), operands, Empty()),
            success_postcond=reduce(lambda acc, path: And(acc, IsDir(path)), operands, Empty()),
            failure_postcond=Empty())
    else:
        # TODO: implement a default case (need to look at every mkdir flag and derive the most detailed but correct spec)
        assert False, f"Unhandled mkdir invocation:\n{cmd_}\n{cmd}"


def mv_spec(cmd_: tuple[Field]) -> CmdSpec:
    # https://pubs.opengroup.org/onlinepubs/9799919799/utilities/mv.html

    cmd = parse_command(cmd_)
    (name, flags, _, operands) = (cmd.cmd_name, cmd.flags, cmd.options, cmd.operands)

    raise NotImplementedError("mv spec not implemented yet")


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


def sudo_spec(cmd_: tuple[Field]) -> CmdSpec | None:

    cmd = parse_command(cmd_)
    operands = cmd.operands

    # Return the spec of the underlying command
    # TODO: A lot of interesting things can be modeled about sudo itself (e.g., permission denied, env vars, etc.)
    assert len(operands) >= 1, f"Expected at least one operand for sudo, got: {operands} for command {cmd_}"
    assert isinstance(operands[0].content, SymStr), f"Expected first operand of sudo to be a command string, got: {operands[0].content}"
    return get_spec(operands[0].content.parts[0], tuple(operands[1:]))


def touch_spec(cmd_: tuple[Field]) -> CmdSpec:

    cmd = parse_command(cmd_)
    (name, flags, _, operands) = (cmd.cmd_name, cmd.flags, cmd.options, cmd.operands)
    logging.debug(f"Ignored irrelevant flags for touch: {cmd.flags}")

    if flags == set(): # touch file...
        return CmdSpec(
            precond=Empty(),
            success_postcond=reduce(lambda acc, path: And(acc, IsFile(path)), operands, Empty()),
            failure_postcond=Empty())
    else:
        assert False, f"Unhandled touch invocation:\n{cmd_}\n{cmd}"

# -- Specs end here --
# Do not define specs below this line, they will not be registered!

current_module = sys.modules[__name__]
CMD_SPECS: dict[str, Callable] = {}

for name, func in inspect.getmembers(current_module, inspect.isfunction):
    if name.endswith("_spec") and not name.startswith("get_"):
        # derive command name by removing the "_spec" suffix
        cmd_name = name.removesuffix("_spec")
        CMD_SPECS[cmd_name] = func


def get_spec(cmd_name: str | None, cmd_: tuple[Field]) -> CmdSpec | None:
    if cmd_name in CMD_SPECS:
        return CMD_SPECS[cmd_name](cmd_)
    logging.info(f"Specs are {CMD_SPECS}")
    logging.warning(f"No spec found for command '{cmd_name}', treating as no-op.")
    assert False, f"No spec found for command '{cmd_name}'"
    return None


# NOTE: in the postconds add env vars that change (e.g. PWD, OLDPWD, etc.)
# generally any information that can be conveyed through the constraints should be added here
# TODO: comments with explanations for the default cases (why are they needed, what can go wrong otherwise?)
