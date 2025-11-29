import inspect
import logging
import sys
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from math import inf

import shasta.ast_node as AST
from pash_annotations.parser import parser as pash_annot_parser

from sash.constraints import (
    And,
    CommandExists,
    Constraint,
    Empty,
    IOType,
    IsDeleted,
    IsDir,
    IsFile,
    IsUnread,
    StringEq,
    IsRead
)
from sash.frozen import freeze_thing
from sash.state import (
    ArbitraryType,
    CompletelyArbitrary,
    Field,
    SymStr,
    SymVar,
    WordCount,
)


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
    check: Constraint # condition to check before executing the command to detect possible bugs
    success_postcond: Constraint # post-condition if exit code is 0
    failure_postcond: Constraint # post-condition if exit code is non-0
    io: IOType = IOType.UNKNOWN # whether the command does IO on stdin/stdout
    min_operands: int = 0 # minimum number of operands required for command to succeed (for guarding against commands that can only fail)


# If a command is not present here github.com/binpash/annotations/tree/main/pash_annotations/parser/command_flag_option_info/data
# the returned invocation will only contain operands (no flags/options), containing every part of the command after the command name
def parse_command(cmd_inv: tuple[Field, ...]) -> CmdInvocation:
    """
    Parses a command invocation from a list of Fields into a CmdInvocation object.
    The CmdInvocation only contains flags in their short form (e.g., '-l' instead of '--long').
    """
    logging.debug("Parsing command from fields: %s", cmd_inv)

    stringified_cmd = ""
    for field in cmd_inv:
        match field.content:
            case SymStr(parts):
                part_strs = []
                assert all(isinstance(part, str) for part in parts), "SymStr with SymVars not supported in Z3 translation yet"
                for part in parts:
                    match part:
                        case str(s):
                            if " " in s:
                                # If an part contains spaces, that means it was quoted in the original command (TODO: verify this assumption)
                                s = f'"{s}"'
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
    logging.debug("Parsed command: %s", cmd_parsed)
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


def extract_flags_naively(cmd: str, operands: list[Field]) -> tuple[set[str], list[Field]]:
    """
    A naive way to extract flags from operands.
    This function assumes that flags are of the form '-x'.
    It does not handle combined short flags (e.g., '-abc'), long flags (e.g., '--long') or flags with arguments (e.g., '--option value').
    """
    flags = set()
    remaining_operands = []

    for idx, operand in enumerate(operands):
        if isinstance(operand.content, CompletelyArbitrary):
            remaining_operands.append(operand)
            continue

        # Operand is SymStr
        if len(operand.content.parts) == 0 or len(operand.content.parts) > 1:
            remaining_operands.append(operand)
            continue

        # Operand is SymStr with exactly one part
        part = operand.content.parts[0]
        if isinstance(part, SymVar):
            remaining_operands.append(operand)
            continue

        # Operand is SymStr with exactly one str part
        if part.startswith('-') and len(part) == 2:
            flags.add(part)
        elif part.startswith('--') and len(part) > 2:
            logging.debug("Found long flag '%s' in %s invocation", part, cmd)
        elif part == "--":
            # Stop flag parsing after '--'
            remaining_operands.extend(operands[idx + 1:])
            break
        else:
            remaining_operands.append(operand)

    return flags, remaining_operands


# -- Specs start here --


def alias_spec(cmd: CmdInvocation) -> CmdSpec:
    # https://pubs.opengroup.org/onlinepubs/9799919799/utilities/alias.html

    (name, flags, options, operands) = (cmd.cmd_name, cmd.flags, cmd.options, cmd.operands)
    io = IOType.NONE

    # NOTE:
    #   'alias name=newcmd' gets parsed as an operand with content SymStr(("name=newcmd",))
    #   'alias name=newcmd cmdflags...' gets parsed as more than one operand with content SymStr(("name=newcmd",)), SymStr(("cmdflags",)), SymStr(("...",))

    assert name == SymStr(("alias",)), f"Expected alias command, got: {name}"
    assert len(flags) == 0 and len(options) == 0, f"The current parsing function does not produce flags/options for alias; something changed?"

    flags, operands = extract_flags_naively("alias", operands)

    if flags == set() and len(operands) == 0: # alias
        # prints all aliases to stdout

        # check:        none
        # z-postcond:   none
        # nz-postcond:  none

        check = Empty()
        success_postcond = Empty()
        failure_postcond = Empty()
        io = IOType.add_stdout(io)

    elif flags == set() and len(operands) >= 1: # alias name[=value] ...
        # defines aliases

        # check:        none
        # z-postcond:   for each operand of the form name[=value], name is a command
        # nz-postcond:  none

        check = Empty()
        success_postcond = Empty()
        failure_postcond = Empty()

        for op in operands:
            if isinstance(op.content, SymStr) and isinstance(op.content.parts[0], str):
                if '=' in op.content.parts[0]: # alias name=value ...
                    name, _ = op.content.parts[0].split('=', 1)
                    success_postcond = success_postcond & CommandExists(Field(SymStr((name,)), WordCount(1,1)))
                else: # alias name ...
                    io = IOType.add_stdout(io)
            # TODO: unclear whether (and when) the previous 'if' case can be false

    else: # non-POSIX
        log_crit_unhandled_inv(cmd)
        return handle_non_posix(cmd)

    return CmdSpec(check, success_postcond, failure_postcond, io)


def cd_spec(cmd: CmdInvocation) -> CmdSpec:
    # https://pubs.opengroup.org/onlinepubs/9799919799/utilities/cd.html

    # NOTE:
    #   All flags determine how `..` is handled with respect to symlinks.
    #   Since we do not model symlinks, we do not handle these flags either.

    # NOTE:
    #   Invocations with multiple operands should fail, but we treat them as no-ops here.

    (name, flags, options, operands) = (cmd.cmd_name, cmd.flags, cmd.options, cmd.operands)
    io = IOType.NONE

    assert name == SymStr(("cd",)), f"Expected cd command, got: {name}"
    assert len(flags) == 0 and len(options) == 0, f"The current parsing function does not produce flags/options for cd; something changed?"

    flags, operands = extract_flags_naively("cd", operands)

    # NOTE: we might want to ignore the postconditions, i suspect they might overcomplicate things
    # NOTE: cd also interacts with CDPATH, do we care? the interaction is quite complex

    make_ast = lambda var: AST.VArgChar("Normal", False, var, [])
    home_var = Field(
        CompletelyArbitrary(freeze_thing(make_ast("HOME")),
                            ArbitraryType.ENVIRONMENT,
                            None),
                 WordCount(0, inf))
    pwd_var = Field(
        CompletelyArbitrary(freeze_thing(make_ast("PWD")),
                            ArbitraryType.ENVIRONMENT,
                            None),
                 WordCount(0, inf))
    oldpwd_var = Field(
        CompletelyArbitrary(freeze_thing(make_ast("OLDPWD")),
                            ArbitraryType.ENVIRONMENT,
                            None),
                 WordCount(0, inf))
    dash = Field(SymStr(("-",)), WordCount(1, 1))

    if flags == set() and len(operands) == 0: # cd
        # check:
        #   (1) HOME must be a directory [undefined behavior / bug]
        # z-postcond:
        #   (1) HOME is a directory
        #   (2) PWD is equal to HOME
        #   (3) PWD is a directory [follows from (1), (2)]
        #   (4) OLDPWD is equal to previous PWD (how do I denote this?)
        # nz-postcond:
        #   (1) none (maybe HOME not set, maybe HOME not a directory, maybe permission issue, etc.)

        # ASSUMPTION:
        #   if HOME is not set behavior is undefined
        #   the spec assumes that if HOME is not set the command fails
        #   technically that might not be the case

        check = IsDir(home_var) # (1)
        success_postcond = (
            IsDir(home_var) &               # (1)
            StringEq(pwd_var, home_var))    # (2)
            # StringEq(oldpwd_var, Prev(pwd_var)) # (4)
        failure_postcond = Empty() # (1)

    elif flags == set() and len(operands) == 1 and operands[0] == dash: # cd -
        # check:
        #   (1) OLDPWD is a directory
        # z-postcond:
        #   (1) PWD is a directory
        #   (2) PWD is equal to the previous OLDPWD (how do I denote this?)
        #   (3) OLDPWD is equal to the previous PWD (how do I denote this?)
        # nz-postcond:
        #   (1) none (maybe OLDPWD not set, maybe OLDPWD not a directory, maybe permission issue, etc.)

        check = IsDir(oldpwd_var)
        success_postcond = (
            IsDir(pwd_var)) # (1)
            #StringEq(pwd_var, Prev(oldpwd_var)) &
            #StringEq(oldpwd_var, Prev(pwd_var)))
        failure_postcond = Empty()
        io = IOType.add_stdout(io)

    elif flags == set() and len(operands) == 1: # cd dir
        # precond:
        #   (1) operand is a directory
        # z-postcond:
        #   (1) operand is a directory
        #   (2) PWD is equal to the operand
        #   (3) PWD is a directory [follows from (1), (2)]
        #   (4) OLDPWD is equal to the previous PWD (how do I denote this?)
        # nz-postcond:
        #   (1) none (maybe operand not a directory, maybe permission issue, etc.)

        d = operands[0]
        check = IsDir(d) # (1)
        success_postcond = (
            IsDir(d) &            # (1)
            StringEq(pwd_var, d)) # (2)
            # StringEq(oldpwd_var, Prev(pwd_var)) # (4)
        failure_postcond = Empty() # (1)

    else:
        # check:
        #   (1): none
        # z-postcond:
        #   (1) PWD is a directory
        #   (2) OLWDPWD is equal to the previous PWD
        # nz-postcond:
        #   (1) none

        log_crit_unhandled_inv(cmd)

        check = Empty() # (1)
        success_postcond = (
            IsDir(pwd_var)) # (1)
            # StringEq(oldpwd_var, Prev(pwd_var))) # (2)
        failure_postcond = Empty()

    return CmdSpec(check, success_postcond, failure_postcond, io)


def command_spec(cmd: CmdInvocation) -> CmdSpec:
    # https://pubs.opengroup.org/onlinepubs/9799919799/utilities/command.html

    return Command.handle_invocation(cmd)


def cp_spec(cmd: CmdInvocation) -> CmdSpec:
    # https://pubs.opengroup.org/onlinepubs/9799919799/utilities/cp.html

    # NOTE:
    #   cp follows symbolic links by default, unless -P is present
    #   cp prompts before overwriting non-writable files, unless -f is present
    #   the current spec is modeled as if -P and -f are present on every call

    (name, flags, options, operands) = (cmd.cmd_name, cmd.flags, cmd.options, cmd.operands)

    assert name == SymStr(("cp",)), f"Expected cp command, got: {name}"
    assert len(flags) == 0 and len(options) == 0, f"The current parsing function does not produce flags/options for cp; something changed?"

    flags, operands = extract_flags_naively("cp", operands)

    flags.discard("-p") # -p is used to control metadata of the created files
    flags.discard("-P") # -P specifies that all actions be done on symbolic links themselves instead of their targets, see note above
    flags.discard("-f") # -f makes the command silently overwrite non-writable files, without asking for confirmation, see note above
    io = IOType.STDIN if "-i" in flags else IOType.NONE
    flags.discard("-i")

    if flags == set() and len(operands) == 2: # cp [-Pfip] source target
        # check:
        #   (1) source must be a file [command fails / bug]
        #   (2) source must not be target [command fails] (could be removed from the check)
        #   (3a) if target is a file, then target must not be unread (i.e., it must be read) [bug] (to track overwrite bugs across loop iterations)
        #   (3b) if target is a directory, then target/source must not be unread [bug]
        # z-postcond:
        #   (1) source is a file
        #   (2) source is not target
        #   (3) (target is an unread file) or (target is a directory and target/source is an unread file)
        #   (4) source is read
        # nz-postcond:
        #   (1) none (command can fail due to reasons we don't model, such as permissions)

        s, t = operands[0], operands[1]
        check = (
            IsFile(s) &                 # (1)
            ~StringEq(s, t) &           # (2)
            (IsFile(t) >> IsRead(t)))   # (3a)
                                        # TODO: how to denote created files like this?
                                        # & (IsDir(t) >> ~IsUnread(ConcatPath(t, s))),

        success_postcond = (
            # TODO: decide whether target should be considered unread by default or inherit from source
            IsRead(s) &                             # (4)
            IsFile(s) &                             # (1)
            ~StringEq(s, t) &                       # (2)
            ((IsFile(t) & IsUnread(t)) | IsDir(t))) # (3)
                                                # & IsFile(ConcatPath(t, s)) & IsUnread(ConcatPath(t, s))
        failure_postcond = Empty()

    elif flags == set() and len(operands) >= 2: # cp [-Pfip] source... target
        # check:
        #   (1) all sources must be files [command fails / bug]
        #   (2) no sources must be target [command fails] (redundant? could be removed from the check)
        #   (3) target must be a directory [command fails / bug]
        #   (4) if target/sources are files then they must not be unread [bug]
        # z-postcond:
        #   (1) all sources are files
        #   (2) no sources are target (redundant?)
        #   (3) target is a directory
        #   (4) target/sources are unread files
        # nz-postcond:
        #   (1) none (command can fail due to reasons we don't model, such as permissions)

        ss, t = operands[:-1], operands[-1]
        check = (
            And.from_field_iter(ss, IsFile) &                    # (1)
            And.from_field_iter(ss, lambda s: ~StringEq(s, t)) & # (2)
            IsDir(t))                                            # (3)
            # & And.from_field_iter(ss, lambda s: IsFile(ConcatPath(t, s) >> ~IsUnread(ConcatPath(t, s))) # (4)
        success_postcond = (
            And.from_field_iter(ss, IsFile) &                    # (1)
            And.from_field_iter(ss, lambda s: ~StringEq(s, t)) & # (2)
            IsDir(t))                                            # (3)
            # & And.from_field_iter(ss, lambda path: IsFile(ConcatPath(t, s)) & IsUnread(ConcatPath(t, s))), (4)
        failure_postcond = Empty()

    elif "-R" in flags and len(operands) >= 2: # cp -R [-H|-L|-P] [-fip] source... target
        # check:
        #   (1) all sources must not be deleted [command fails / bug]
        #   (2) target must be a directory [command fails / bug]
        #   (3) if target/sources are files then they must not be unread
        # z-postcond:
        #   (1) all sources are not deleted
        #   (2) target is a directory
        #   (3) (if souces are files then target/sources are files) and (if sources are firectories then target/sources are directories)
        #   (4) if target/sources are files then they are unread
        # nz-postcond:
        #   (1) none (command can fail due to reasons we don't model, such as permissions)

        log_crit_unhandled_inv(cmd)

        ss, t = operands[:-1], operands[-1]
        check = (
            And.from_field_iter(ss, lambda s: ~IsDeleted(s)) & # (1)
            IsDir(t))                                          # (2)
            # & And.from_field_iter(ss, lambda s: IsFile(ConcatPath(t, s) >> ~IsUnread(ConcatPath(t, s))) # (3)
        success_postcond = (
            And.from_field_iter(ss, lambda s: ~IsDeleted(s)) & # (1)
            IsDir(t))                                          # (2)
            # & And.from_field_iters(ss, lambda s: (IsFile(s) >> IsFile(ConcatPath(t, s)) & (IsDir(s) >> IsDir(ConcatPath(t, s))))) # (3)
            # & And.from_field_iter(ss, lambda s: IsFile(ConcatPath(t, s) >> ~IsUnread(ConcatPath(t, s))) # (4)
        failure_postcond = Empty()

    else:
        if len(operands) < 2:
            logging.error("cp command with less than 2 operands is invalid; treating as no-op")
        else:
            return handle_non_posix(cmd)

        check = Empty()
        success_postcond = Empty()
        failure_postcond = Empty()
        io = IOType.UNKNOWN

    return CmdSpec(check, success_postcond, failure_postcond, io)


def echo_spec(cmd: CmdInvocation) -> CmdSpec:
    # https://pubs.opengroup.org/onlinepubs/9799919799/utilities/echo.html

    (name, flags, options, operands) = (cmd.cmd_name, cmd.flags, cmd.options, cmd.operands)
    io = IOType.STDOUT

    assert name == SymStr(("echo",)), f"Expected echo command, got: {name}"
    assert len(flags) == 0 and len(options) == 0, f"The current parsing function does not produce flags/options for echo; something changed?"

    flags, operands = extract_flags_naively("echo", operands)

    if flags != set(): # POSIX does not define any flags for echo
        return handle_non_posix(cmd)

    # check:        none
    # z-postcond:   none
    # nz-postcond:  none

    return CmdSpec(Empty(), Empty(), Empty(), io)


def grep_spec(cmd: CmdInvocation) -> CmdSpec:
    # https://pubs.opengroup.org/onlinepubs/9799919799/utilities/grep.html

    (name, flags, _, operands) = (cmd.cmd_name, cmd.flags, cmd.options, cmd.operands)
    io = IOType.STDOUT if "-q" not in flags else IOType.NONE
    flags.discard("-q")

    assert name == SymStr(("grep",)), f"Expected grep command, got: {name}"

    if flags == set() and len(operands) == 1: # grep pattern
        # check:        none
        # z-postcond:   none
        # nz-postcond:  none

        check = Empty()
        success_postcond = Empty()
        failure_postcond = Empty()
        io = IOType.add_stdin(io)

    elif flags == set() and len(operands) >= 1: # grep pattern file...
        # check:        all operands must be files
        # z-postcond:   all operands are files
        # nz-postcond:  none (maybe permission issue, etc.)

        files = operands[1:]
        check = And.from_field_iter(files, IsFile)
        success_postcond = And.from_field_iter(files, IsFile)
        failure_postcond = Empty()

    else:
        log_crit_unhandled_inv(cmd)

        check = Empty()
        success_postcond = Empty()
        failure_postcond = Empty()
        io = IOType.UNKNOWN

    return CmdSpec(check, success_postcond, failure_postcond, io)


def mkdir_spec(cmd: CmdInvocation) -> CmdSpec:
    # https://pubs.opengroup.org/onlinepubs/9799919799/utilities/mkdir.html

    # Note:
    #   The file system model is a flat map, there is no hierarchy of directories
    #   So `mkdir -p a/b` will not be assumed to fail if `a` is a file, even though in reality it would

    (name, flags, options, operands) = (cmd.cmd_name, cmd.flags, cmd.options, cmd.operands)

    assert name == SymStr(("mkdir",)), f"Expected mkdir command, got: {name}"
    assert len(flags) == 0 and len(options) == 0, f"The current parsing function does not produce flags/options for mkdir; something changed?"

    flags, operands = extract_flags_naively("mkdir", operands)

    flags.discard("-m") # -m is used to control permissions, which we do not model
    flags.discard("--") # -- is used to indicate the end of flags
    io = IOType.NONE
    io = IOType.add_stdout(io) if "-v" in flags else io

    if flags == set(["-p"]): # mkdir [-m mode] -p dir...
        # check:
        #   (1) all operands must not be files (mkdir -p doesn't error if directories exist)
        # z-postcond:
        #   (1) all operands are directories
        # nz-postcond:
        #   (1) none (maybe permission issue, etc.)

        check = And.from_field_iter(operands, lambda op: ~IsFile(op))
        success_postcond = And.from_field_iter(operands, IsDir)
        failure_postcond = Empty()

    elif flags == set(["-v"]): # mkdir [-m mode] -v dir...
        # check:
        #   (1) all operands must not be files or directories
        # z-postcond:
        #   (1) all operands are directories
        # nz-postcond:
        #   (1) none (maybe permission issue, etc.)

        check = And.from_field_iter(operands, lambda op: ~(IsFile(op) | IsDir(op)))
        success_postcond = And.from_field_iter(operands, IsDir)
        failure_postcond = Empty()

    elif flags == set(): # mkdir [-m mode] dir...
        # check:
        #   (1) all operands must not be files or directories
        # z-postcond:
        #   (1) all operands are directories
        # nz-postcond:
        #   (1) none (maybe permission issue, etc.)

        check = And.from_field_iter(operands, lambda op: ~(IsFile(op) | IsDir(op)))
        success_postcond = And.from_field_iter(operands, IsDir)
        failure_postcond = Empty()

    else:
        return handle_non_posix(cmd)

    return CmdSpec(check, success_postcond, failure_postcond, io, min_operands=1)


def mv_spec(cmd: CmdInvocation) -> CmdSpec:
    # https://pubs.opengroup.org/onlinepubs/9799919799/utilities/mv.html

    return Mv.handle_invocation(cmd)


def rm_spec(cmd: CmdInvocation) -> CmdSpec:
    # https://pubs.opengroup.org/onlinepubs/9799919799/utilities/rm.html

    return Rm.handle_invocation(cmd)


def sudo_spec(cmd: CmdInvocation) -> CmdSpec | None:

    operands = cmd.operands

    # Return the spec of the underlying command
    # TODO: A lot of interesting things can be modeled about sudo itself (e.g., permission denied, env vars, etc.)
    assert len(operands) >= 1, f"Expected at least one operand for sudo, got: {operands} for command {cmd}"
    assert isinstance(operands[0].content, SymStr), f"Expected first operand of sudo to be a command string, got: {operands[0].content}"
    if isinstance(operands[0].content.parts[0], str):
        return get_spec(operands[0].content.parts[0], tuple(operands))
    else:
        logging.critical("Got non-str command name in sudo:%s\n%s", cmd, cmd)
        return CmdSpec(Empty(), Empty(), Empty())


def test_spec(cmd: CmdInvocation) -> CmdSpec:
    # https://pubs.opengroup.org/onlinepubs/9799919799/utilities/test.html

    return Test.handle_invocation(cmd)


def touch_spec(cmd: CmdInvocation) -> CmdSpec:
    # https://pubs.opengroup.org/onlinepubs/9799919799/utilities/touch.html

    (name, flags, options, operands) = (cmd.cmd_name, cmd.flags, cmd.options, cmd.operands)

    assert name == SymStr(("touch",)), f"Expected touch command, got: {name}"
    assert len(flags) == 0 and len(options) == 0, f"The current parsing function does not produce flags/options for touch; something changed?"

    flags, operands = extract_flags_naively("touch", operands)

    io = IOType.NONE
    flags.discard("-a") # -a changes access time, which we do not model
    flags.discard("-d") # -d changes time to a specific date, which we do not model
    flags.discard("-m") # -m changes modification time, which we do not model
    flags.discard("-r") # -r changes time to that of a reference file, which we do not model
    flags.discard("-t") # -t changes time to a specific time, which we do not model

    if flags == set(): # touch file...
        # NOTE: Touch does not create unread files since they are empty upon creation
        # check:
        #   (1) none
        # z-postcond:
        #   (1) all operands are files or directories
        # nz-postcond:
        #   (1) none (maybe permission issue, etc.)

        check = Empty()
        success_postcond = And.from_field_iter(operands, lambda p: (~IsFile(p) & ~IsDir(p)) >> IsFile(p))
        failure_postcond = Empty()

    elif flags == set(["-c"]): # touch -c file...
        # -c tells touch to not create files if they do not exist, turning it essentially into a no-op for us

        check = Empty()
        success_postcond = Empty()
        failure_postcond = Empty()

    else:
        return handle_non_posix(cmd)

    return CmdSpec(check, success_postcond, failure_postcond, io)


def cat_spec(cmd: CmdInvocation) -> CmdSpec:
    # https://pubs.opengroup.org/onlinepubs/9799919799/utilities/cat.html

    (name, flags, _, operands) = (cmd.cmd_name, cmd.flags, cmd.options, cmd.operands)
    io = IOType.STDOUT

    assert name == SymStr(("cat",)), f"Expected cat command, got: {name}"

    if flags == set(): # cat file...
        # check:
        #   (1) all operands must be files
        # z-postcond:
        #   (1) all operands are files or directories
        # nz-postcond:
        #   (1) none (maybe permission issue, etc.)

        check = And.from_field_iter(operands, IsFile)
        success_postcond = And.from_field_iter(operands, IsFile) & And.from_field_iter(operands, IsRead)
        failure_postcond = Empty()

    else:
        return handle_non_posix(cmd)

    return CmdSpec(check, success_postcond, failure_postcond, io)


def file_spec(cmd: CmdInvocation) -> CmdSpec:
    # https://pubs.opengroup.org/onlinepubs/9799919799/utilities/file.html
    # The spec is (almost) identical to the `cat` spec other than the command name.

    (name, flags, _, operands) = (cmd.cmd_name, cmd.flags, cmd.options, cmd.operands)
    io = IOType.STDOUT

    assert name == SymStr(("file",)), f"Expected file command, got: {name}"

    if flags == set(): # file file...
        # check:
        #   (1) all operands must be files
        # z-postcond:
        #   (1) all operands are files or directories
        # nz-postcond:
        #   (1) none (maybe permission issue, etc.)

        check = And.from_field_iter(operands, IsFile)
        success_postcond = And.from_field_iter(operands, IsFile) & And.from_field_iter(operands, IsRead)
        failure_postcond = Empty()

    else:
        return handle_non_posix(cmd)

    return CmdSpec(check, success_postcond, failure_postcond, io)


def env_spec(cmd: CmdInvocation) -> CmdSpec:
    # https://pubs.opengroup.org/onlinepubs/9799919799/utilities/env.html

    return Env.handle_invocation(cmd)

# -- Specs end here --
# Do not define specs below this line, they will not be registered!

current_module = sys.modules[__name__]
CMD_SPECS: dict[str, Callable] = {}

for name, func in inspect.getmembers(current_module, inspect.isfunction):
    if name.endswith("_spec") and not name.startswith("get_"):
        # derive command name by removing the "_spec" suffix
        cmd_name = name.removesuffix("_spec")
        CMD_SPECS[cmd_name] = func
        if cmd_name == "test":
            CMD_SPECS["["] = func # register '[' as an alias for 'test'


def get_spec(cmd_name: str | None, cmd_: tuple[Field, ...]) -> CmdSpec | None:
    if cmd_name in CMD_SPECS:
        return CMD_SPECS[cmd_name](parse_command(cmd_))
    logging.info("Specs are %s", CMD_SPECS)
    logging.warning("No spec found for command '%s', treating as no-op.", cmd_name)


def log_crit_unhandled_inv(cmd: CmdInvocation) -> None:
    import json

    cmd_json = {
        "cmd_invocation" : {
            "cmd_name": cmd.cmd_name,
            "flags": list(cmd.flags),
            "options": {k: v for k, v in cmd.options.items()},
            "operands": [op for op in cmd.operands]
        }
    }

    logging.critical("Unhandled invocation for command '%s':\n%s",
                     cmd.cmd_name, json.dumps(cmd_json, indent=2, default=str))


def handle_non_posix(cmd: CmdInvocation) -> CmdSpec:
    import json

    cmd_json = {
        "cmd_invocation" : {
            "cmd_name": cmd.cmd_name,
            "flags": list(cmd.flags),
            "options": {k: v for k, v in cmd.options.items()},
            "operands": [op for op in cmd.operands]
        }
    }

    logging.critical("Unsupported inv:\n%s", json.dumps(cmd_json, indent=2, default=str))

    #logging.warning("Non-POSIX '%s' handling is not supported; treating as no-op", cmd_name)
    return CmdSpec(Empty(), Empty(), Empty(), IOType.UNKNOWN) # no-op spec


class Cmd(ABC):
    name: str
    posix_flags: set[str]
    supported_flags: set[str]

    @classmethod
    def handle_invocation(cls, cmd: CmdInvocation) -> CmdSpec:
        if cmd.flags - cls.posix_flags != set():
            # At least one flag is non-POSIX
            spec = cls._handle_non_posix(cmd)
        elif cmd.flags - cls.supported_flags != set():
            # At least one flag is non-supported
            spec = cls._handle_non_supported(cmd)
        else:
            # All flags are supported
            spec = cls._handle_supported(cmd)
        return spec

    @classmethod
    def _handle_non_posix(cls, cmd: CmdInvocation) -> CmdSpec:
        logging.warning("Non-POSIX handling for command '%s' is not supported; treating as no-op", cmd.cmd_name)
        return cls._handle_non_supported(cmd)

    @classmethod
    def _handle_non_supported(cls, cmd: CmdInvocation) -> CmdSpec:
        import json

        cmd_json = {
            "cmd_invocation" : {
                "cmd_name": cmd.cmd_name,
                "flags": list(cmd.flags),
                "options": {k: v for k, v in cmd.options.items()},
                "operands": [op for op in cmd.operands]
            }
        }

        logging.critical("Unhandled invocation for command '%s':\n%s; treating as no-op",
                         cmd.cmd_name, json.dumps(cmd_json, indent=2, default=str))
        return CmdSpec(Empty(), Empty(), Empty(), IOType.UNKNOWN)

    @classmethod
    @abstractmethod
    def _handle_supported(cls, cmd: CmdInvocation) -> CmdSpec:
        pass


class Command(Cmd):
    # https://pubs.opengroup.org/onlinepubs/9799919799/utilities/command.html

    # NOTE: The current parsing function does not produce flags/options for test, and instead encodes everything in operands

    name = "command"
    posix_flags     = set()
    supported_flags = set()

    @classmethod
    def _handle_supported(cls, cmd: CmdInvocation) -> CmdSpec:
        # implement this similar to Test._handle_supported
        (name, flags, options, operands) = (cmd.cmd_name, cmd.flags, cmd.options, cmd.operands)

        assert name == SymStr((cls.name,)), f"Expected command command, got: {cmd}"
        assert len(flags) == 0 and len(options) == 0, f"The current parsing function does not produce flags/options for {cls.name}; something changed?"

        check = Empty()
        success_postcond = Empty()
        failure_postcond = Empty()
        io = IOType.NONE

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

        if len(operands) > 0 and operands[0].content == SymStr(("-p",)): # command -p cmd args...
            operands = operands[1:] # discard -p operand
            # proceed as normal without -p
            # if -v/-V are first, we don't mind -p as no subcommands are called

        if len(operands) > 0 and (operands[0].content == SymStr(("-v",)) or \
                                  operands[0].content == SymStr(("-V",))): # command -v/-V subcmd
            subcmd = operands[1]
            failure_postcond = ~CommandExists(subcmd)
            io = IOType.add_stdout(io)

        else: # command cmd args...
            subcmd = operands[0]
            cmd_name = parse_command((subcmd,)).cmd_name.parts[0] # hack to get the command name
            if isinstance(cmd_name, str):
                if spec := get_spec(cmd_name, tuple(operands)): # this call will log any unhandled invocations
                    return spec

        return CmdSpec(check, success_postcond, failure_postcond, io)

def Field_trimR(f: Field, suffix: str) -> Field:
    match f:
        case Field(SymStr(parts), wc) if len(parts) >= 1 and isinstance(parts[-1], str) and parts[-1].endswith(suffix):
            return Field(SymStr(parts[:-1] + (parts[-1][:-len(suffix)],)), wc)
        case _:
            assert False, f"Attempted to trimr {suffix} from {f}, which doesn't have that suffix"

class Mv(Cmd):
    # https://pubs.opengroup.org/onlinepubs/9799919799/utilities/mv.html

    name = "mv"
    posix_flags     = {"-f", "-i"}
    supported_flags = {"-f", "-i"}

    @classmethod
    def _handle_supported(cls, cmd: CmdInvocation) -> CmdSpec:
        (name, flags, _, operands) = (cmd.cmd_name, cmd.flags, cmd.options, cmd.operands)
        io = IOType.NONE
        io = IOType.add_stdin(io) if "-i" in flags else io

        # NOTE: To be completely correct, the order of -f and -i matters, but we ignore that for now
        # NOTE + TODO: due to the simplicity of the fs model, `mv src/* dst` would lead to the z-postcond of `IsDeleted(src/*)`
        #              one refinement to this would be checking if the src string contains any slashes and adjusting the postcondition accordingly
        #              however, we currently handle that during constraints-to-z3 translation by not considering src itself as deleted
        flags.discard("-i")
        flags.discard("-f") # ignore -f flag, i think it does not affect preconds/postconds

        assert name == SymStr((cls.name,)), f"Expected mv command, got: {name}"

        if len(operands) < 2:
            logging.error("mv command with less than 2 operands is invalid; treating as no-op")
            return CmdSpec(Empty(), Empty(), Empty(), IOType.UNKNOWN)

        elif len(operands) == 2: # mv src dst (dst can be a file or a dir)
            src, dst = operands[0], operands[1]
            if isinstance(dst.content, SymStr) and \
               isinstance(dst.content.parts[-1], str) and \
               dst.content.parts[-1].endswith("/"): # dst cannot be a file
                check = (
                    ~IsDeleted(src) &                   # src must exist
                    ~IsFile(dst) &                      # dst cannot be a file
                    (IsFile(src) >> ~IsDeleted(dst)) &  # if src is a file, dst must exist
                    ~StringEq(src, dst)                 # src and dst cannot be the same
                )
                success_postcond = (
                    IsDeleted(src) &    # src is deleted
                    IsDir(dst)          # dst is a dir
                )
                failure_postcond = Empty()
            elif isinstance(src.content, SymStr) and \
                 isinstance(src.content.parts[-1], str) and \
                 src.content.parts[-1].endswith("/*"): # src is the stuff in a directory
                check = (
                    IsRead(dst) &
                    IsDir(Field_trimR(src, "/*")) &
                    IsDir(dst) &
                    ~IsDeleted(dst) &
                    ~StringEq(src, dst)
                )
                success_postcond = (
                    IsDir(dst)
                )
                failure_postcond = Empty()
            else: # dst can be a file or a dir
                check = (
                    IsRead(dst) &
                    ~IsDeleted(src) &                      # src must exist
                    (IsDir(src) >> ~IsFile(dst)) &         # if src is a dir, dst cannot be a file
                    (IsFile(dst) >> ~IsUnread(dst)) &      # if dst is a file, it must not be unread
                    ((IsDir(src) | IsDir(dst)) >> ~StringEq(src, dst)) # src and dst cannot be the same directory
                )
                success_postcond = (
                    IsRead(src) &
                    IsDeleted(src) &            # src is deleted
                    (IsFile(dst) | IsDir(dst))  # dst is a file or dir
                )
                failure_postcond = Empty()

        else: # mv src... dst (dst must be a dir)
            srcs, dst = operands[:-1], operands[-1]
            check = And.from_field_iter(srcs, lambda path: ~IsDeleted(path)) & IsDir(dst)
            success_postcond = And.from_field_iter(srcs, IsDeleted)
            failure_postcond = Empty()

        return CmdSpec(check, success_postcond, failure_postcond, io)


class Rm(Cmd):
    # https://pubs.opengroup.org/onlinepubs/9799919799/utilities/rm.html

    name = "rm"
    posix_flags     = {"-d", "-f", "-i", "-R", "-r", "-v"}
    supported_flags = {"-d", "-f", "-i", "-R", "-r", "-v"}

    @classmethod
    def _handle_supported(cls, cmd: CmdInvocation) -> CmdSpec:
        (name, flags, options, operands) = (cmd.cmd_name, cmd.flags, cmd.options, cmd.operands)

        assert name == SymStr((cls.name,)), f"Expected rm command, got: {cmd}"
        assert len(options) == 0, f"Expected no options for rm, got: {cmd}"

        io = IOType.NONE
        io = IOType.add_stdin(io) if "-i" in flags else io
        io = IOType.add_stdout(io) if "-v" in flags else io
        io = IOType.remove_stdin(io) if "-f" in flags else io # 'rm -if ...' would not prompt
                                                              # 'rm -fi ...' would prompt, but we assume '-if' order as it is more dangerous

        flags.discard("-i")
        flags.discard("-v")
        if "-R" in flags or "-d" in flags:
            flags.discard("-R") # -R is equivalent to -r
            flags.discard("-d") # -d allows deletion of empty directories
            flags.add("-r") # same postconditions as -d (since we cannot reason about emptiness)

        success_postcond = And.from_field_iter(operands, IsDeleted)
        failure_postcond = Empty() # due to not modeling permissions, we never gain info on failure

        if flags == set([]):
            check = And.from_field_iter(operands, lambda op: IsFile(op) & ~IsUnread(op))
        elif flags == set(["-f"]):
            check = And.from_field_iter(operands, lambda op: (IsFile(op) >> ~IsUnread(op)) & ~IsDir(op) & ~IsDeleted(op))
        elif flags == set(["-r"]):
            check = And.from_field_iter(operands, lambda op: (IsFile(op) & ~IsUnread(op)) | IsDir(op))
        elif flags == set(["-f", "-r"]):
            check = And.from_field_iter(operands, lambda op: (IsFile(op) >> ~IsUnread(op)) & ~IsDeleted(op))
        else:
            assert False, f"unreachable; received flags: {flags}"

        return CmdSpec(check, success_postcond, failure_postcond, io)


class Test(Cmd):
    # https://pubs.opengroup.org/onlinepubs/9799919799/utilities/test.html

    # NOTE: The current parsing function does not produce flags/options for test, and instead encodes everything in operands
    # NOTE: Arithmetic expressions are not supported, so the only information we can get when encountering numeric comparisons
    #       is whether the strings are NOT equal (because '05' and '5' are different as strings, but the same as numbers)

    name = "test"
    posix_flags     = set()
    supported_flags = set()

    @classmethod
    def _handle_supported(cls, cmd: CmdInvocation) -> CmdSpec:
        (name, flags, options, operands) = (cmd.cmd_name, cmd.flags, cmd.options, cmd.operands)

        assert name == SymStr((cls.name,)) or name == SymStr(("[",)), f"Expected test command, got: {cmd}"
        assert len(flags) == 0 and len(options) == 0, f"The current parsing function does not produce flags/options for {cls.name}; something changed?"

        check = Empty()
        success_postcond = None
        failure_postcond = None
        io = IOType.NONE

        if name == SymStr(("[" ,)):
            if len(operands) == 0 or operands[-1] != Field(SymStr(("]",)), WordCount(1,1)):
                # TODO: malformed command; will produce error
                logging.debug("Malformed test command with missing closing ']'; treating it as properly closed")
            else:
                operands = operands[:-1] # remove closing ']'

        negated = False
        if len(operands) > 0 and operands[0] == Field(SymStr(("!",)), WordCount(1,1)):
            negated = True
            operands = operands[1:]

        match len(operands):
            case 0: # test
                success_postcond = Empty()
                failure_postcond = Empty()
            case 1: # test string
                op = operands[0]
                # z-postcond is the negation of nz-postcond
                failure_postcond = StringEq(op, Field(SymStr(("",)), WordCount(1,1)))
            case 2: # test -flag operand
                op = operands[1]
                flag = operands[0].content.parts[0] if isinstance(operands[0].content, SymStr) else None
                empty_str_var = Field(SymStr(("",)), WordCount(0,0))

                match flag:
                    case "-d":
                        success_postcond = IsDir(op)
                        # nz-postcond is the negation of z-postcond
                    case "-f":
                        success_postcond = IsFile(op)
                        # nz-postcond is the negation of z-postcond
                    case "-e":
                        success_postcond = IsFile(op) | IsDir(op)
                        # nz-postcond is the negation of z-postcond
                    case "-n":
                        # z-postcond is the negation of nz-postcond
                        failure_postcond = StringEq(op, empty_str_var)
                    case "-r" | "-w" | "-x":
                        success_postcond = IsFile(op) | IsDir(op)
                        failure_postcond = Empty()
                    case "-z":
                        success_postcond = StringEq(op, empty_str_var)
                        # nz-postcond is the negation of z-postcond
                    case _:
                        logging.debug("Unhandled test invocation: %s; treating as no-op", cmd)
                        success_postcond = Empty()
                        failure_postcond = Empty()
            case 3: # test operand1 operator operand2
                op1 = operands[0]
                operator = operands[1].content.parts[0] if isinstance(operands[1].content, SymStr) else None
                op2 = operands[2]

                match operator:
                    case "=":
                        success_postcond = StringEq(op1, op2)
                        # nz-postcond is the negation of z-postcond
                    case "!=":
                        # z-postcond is the negation of nz-postcond
                        failure_postcond = StringEq(op1, op2)
                    case "-eq" | "-ge" | "-le":
                        success_postcond = Empty() # no info gained about string equality
                                                   # -eq: 'test 05 -eq 5' and 'test 5 -eq 5' both succeed, but strings are not necessarily equal
                                                   # -ge/-le: 'test 5 -ge/-le 5' and 'test 05 -ge/-le 5' both succeed, but strings are not necessarily equal
                        failure_postcond = ~StringEq(op1, op2)
                    case "-ne" | "-gt" | "-lt":
                        success_postcond = ~StringEq(op1, op2)
                        failure_postcond = Empty() # no info gained about string equality
                                                   # -ne: 'test 05 -ne 5' and 'test 5 -ne 5' both fail, but strings are not necessarily equal
                                                   # -gt/-lt: 'test 5 -gt/-lt 5' and 'test 05 -gt/-lt 5' both fail, but strings are not necessarily equal
                    case _:
                        logging.debug("Unhandled test invocation: %s; treating as no-op", cmd)
                        success_postcond = Empty()
                        failure_postcond = Empty()
            case _:
                # NOTE: According to POSIX, in this case "the results are unspecified"
                logging.warning("%s invocation with more than 4 operands is unspecified: %s; treating as no-op", cls.name, cmd)
                success_postcond = Empty()
                failure_postcond = Empty()

        # In most cases the postconditions are negations of each other, so use this pattern to reduce code and possible mistakes
        match success_postcond, failure_postcond:
            case None, None:
                assert False, f"Unreachable, yet reached with invocation: {cmd}"
            case None, _:
                success_postcond = ~failure_postcond
            case _, None:
                failure_postcond = ~success_postcond

        if negated:
            (success_postcond, failure_postcond) = (failure_postcond, success_postcond)

        return CmdSpec(check, success_postcond, failure_postcond, io)


class Env(Cmd):
    name = "env"
    posix_flags     = set()
    supported_flags = set()

    @classmethod
    def _handle_supported(cls, cmd: CmdInvocation) -> CmdSpec:
        (name, flags, options, operands) = (cmd.cmd_name, cmd.flags, cmd.options, cmd.operands)
        assert name == SymStr((cls.name,)), f"Expected env command, got: {name}"
        assert len(flags) == 0 and len(options) == 0, f"The current parsing function does not produce flags/options for env; something changed?"

        check = Empty()
        success_postcond = Empty()
        failure_postcond = Empty()
        io = IOType.NONE

        def _is_env_assignment(field: Field) -> bool:
            """Helper to check if a `Field` is of the form `VAR=VALUE`."""
            if not isinstance(field.content, SymStr):
                return False
            parts = field.content.parts
            if len(parts) != 1 or not isinstance(parts[0], str):
                return False
            s = parts[0]
            if "=" not in s:
                return False
            var_name, _ = s.split("=", 1)
            if var_name == "":
                return False
            # https://stackoverflow.com/a/2821183
            # https://pubs.opengroup.org/onlinepubs/9699919799/
            if not (var_name[0].isalpha() or var_name[0] == "_"):
                return False
            for ch in var_name[1:]:
                if not (ch.isalnum() or ch == "_"):
                    return False
            return True

        # Skip leading `VAR=VALUE` assignments that appear before the subcommand, if any.
        start_idx = 0
        while start_idx < len(operands) and _is_env_assignment(operands[start_idx]):
            start_idx += 1

        if start_idx < len(operands):
            subcmd = operands[start_idx]
            subcmd_name = parse_command((subcmd,)).cmd_name.parts[0]
            if isinstance(subcmd_name, str):
                if spec := get_spec(subcmd_name, tuple(operands[start_idx:])):
                    return spec
                # Since the sub-command has no spec, it might not exist.
                return CmdSpec(check, success_postcond, ~CommandExists(subcmd), io)

        return CmdSpec(check, success_postcond, failure_postcond, io)
