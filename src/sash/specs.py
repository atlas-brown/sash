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
    check: Constraint # condition to check before executing the command to detect possible bugs
    success_postcond: Constraint # post-condition if exit code is 0
    failure_postcond: Constraint # post-condition if exit code is non-0
    io: IOType = IOType.UNKNOWN # whether the command does IO on stdin/stdout


def parse_command(cmd_inv: tuple[Field, ...]) -> CmdInvocation:
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

def alias_spec(cmd_: tuple[Field, ...]) -> CmdSpec:
    # https://pubs.opengroup.org/onlinepubs/9799919799/utilities/alias.html

    cmd = parse_command(cmd_)
    (name, flags, _, operands) = (cmd.cmd_name, cmd.flags, cmd.options, cmd.operands)
    io = IOType.NONE

    # NOTE:
    #   'alias name=newcmd' gets parsed as an operand with content SymStr(("name=newcmd",))
    #   'alias name=newcmd cmdflags...' gets parsed as more than one operand with content SymStr(("name=newcmd",)), SymStr(("cmdflags",)), SymStr(("...",))

    assert name == SymStr(("alias",)), f"Expected alias command, got: {name}"

    if flags == set() and len(operands) == 0: # alias (print all aliases)
        # check:        none
        # z-postcond:   none
        # nz-postcond:  none

        check = Empty()
        success_postcond = Empty()
        failure_postcond = Empty()
        io = IOType.STDOUT

    elif flags == set() and len(operands) >= 1 and isinstance(operands[0].content, SymStr) and isinstance(operands[0].content.parts[0], str): # alias name[=value [cmdflags...]]
        if '=' in operands[0].content.parts[0]:
            # alias name=value ... (this should be the most common case)

            # check:        none
            # z-postcond:   none
            # nz-postcond:  none

            name, _ = operands[0].content.parts[0].split('=', 1)

            check = Empty()
            success_postcond = CommandExists(Field(SymStr((name,)), WordCount(1,1)))
            failure_postcond = Empty()

        else:
            # alias name...

            # check:        none
            # z-postcond:   none
            # nz-postcond:  none

            check = Empty()
            success_postcond = Empty()
            failure_postcond = Empty()
            io = IOType.STDOUT

    else:
        logging.critical(f"Unhandled alias invocation:\n{cmd_}\n{cmd}; treating as no-op")

        check = Empty()
        success_postcond = Empty()
        failure_postcond = Empty()

    return CmdSpec(check, success_postcond, failure_postcond, io)


def cd_spec(cmd_: tuple[Field, ...]) -> CmdSpec:
    # https://pubs.opengroup.org/onlinepubs/9799919799/utilities/cd.html

    # NOTE:
    #   All flags determine how `..` is handled with respect to symlinks.
    #   Since we do not model symlinks, we do not handle these flags either.

    # NOTE:
    #   Invocations with multiple operands should fail, but we treat them as no-ops here.

    cmd = parse_command(cmd_)
    (name, flags, _, operands) = (cmd.cmd_name, cmd.flags, cmd.options, cmd.operands)
    io = IOType.NONE

    assert name == SymStr(("cd",)), f"Expected cd command, got: {name}"

    # NOTE: we might want to ignore the postconditions, i suspect they might overcomplicate things
    # NOTE: cd also interacts with CDPATH, do we care? the interaction is quite complex

    home_var = Field(SymStr(("HOME",)), WordCount(1,1))
    pwd_var = Field(SymStr(("PWD",)), WordCount(1,1))
    oldpwd_var = Field(SymStr(("OLDPWD",)), WordCount(1,1))
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
        failure_postcond=Empty() # (1)

    else:
        # check:
        #   (1): none
        # z-postcond:
        #   (1) PWD is a directory
        #   (2) OLWDPWD is equal to the previous PWD
        # nz-postcond:
        #   (1) none

        logging.critical(f"Unhandled cd invocation:\n{cmd_}\n{cmd}")

        check = Empty() # (1)
        success_postcond = (
            IsDir(pwd_var)) # (1)
            # StringEq(oldpwd_var, Prev(pwd_var))) # (2)
        failure_postcond = Empty()

    return CmdSpec(check, success_postcond, failure_postcond, io)


def command_spec(cmd_: tuple[Field, ...]) -> CmdSpec:
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
    flags.discard("-p") # -p tells command to use a default value for PATH, guaranteed to find all standard utilities
    io = IOType.NONE

    assert name == SymStr(("command",)), f"Expected command command, got: {name}"

    # TODO: Model `alias` to create CommandExists constraints for aliases
    # Otherwise the following (plausible) code would be marked as buggy:
    # ```
    # if ! command -v my_alias; then
    #     alias my_alias='ls -l'
    # fi
    # my_alias

    if flags == set(["-v"]) or flags == set(["-V"]) and len(operands) == 1: # command -v/-V cmd
        # check:
        #   (1) none
        # z-postcond:
        #   (1) none (cmd might be a reserved word, see note above)
        # nz-postcond:
        #   (1) cmd is not a command
        cmd = operands[0]
        check = Empty()
        success_postcond = Empty()
        failure_postcond = ~CommandExists(cmd)
        io = IOType.add_stdout(io)

    else: # command cmd args...
        logging.critical(f"Unhandled command invocation:\n{cmd_}\n{cmd}; falling back to default case")

        cmd = operands[0]
        cmd_name = parse_command((cmd,)).cmd_name.parts[0] # hack to get the command name
        if isinstance(cmd_name, str):
            spec = get_spec(cmd_name, tuple(operands))
            if spec:
                return spec

        check = Empty()
        success_postcond = Empty()
        failure_postcond = Empty()

    return CmdSpec(check, success_postcond, failure_postcond, io)


def cp_spec(cmd_: tuple[Field, ...]) -> CmdSpec:
    # https://pubs.opengroup.org/onlinepubs/9799919799/utilities/cp.html

    # NOTE:
    #   cp follows symbolic links by default, unless -P is present
    #   cp prompts before overwriting non-writable files, unless -f is present
    #   the current spec is modeled as if -P and -f are present on every call

    cmd = parse_command(cmd_)
    (name, flags, _, operands) = (cmd.cmd_name, cmd.flags, cmd.options, cmd.operands)

    flags.discard("-p") # -p is used to control metadata of the created files
    flags.discard("-P") # -P specifies that all actions be done on symbolic links themselves instead of their targets, see note above
    flags.discard("-f") # -f makes the command silently overwrite non-writable files, without asking for confirmation, see note above
    io = IOType.STDIN if "-i" in flags else IOType.NONE
    flags.discard("-i")

    assert name == SymStr(("cp",)), f"Expected cp command, got: {name}"

    if flags == set() and len(operands) == 2: # cp [-Pfip] source target
        # check:
        #   (1) source must be a file [command fails / bug]
        #   (2) source must not be target [command fails] (could be removed from the check)
        #   (3) (if target is a file then target must not be unread) and (if target is a directory then target/source must not be unread) [bug]
        # z-postcond:
        #   (1) source is a file
        #   (2) source is not target
        #   (3) (target is an unread file) or (target is a directory and target/source is an unread file)
        # nz-postcond:
        #   (1) none (command can fail due to reasons we don't model, such as permissions)

        s, t = operands[0], operands[1]
        check = (
            IsFile(s) &                  # (1)
            ~StringEq(s, t) &            # (2)
            (IsFile(t) >> ~IsUnread(t))) # (3)
                                        # TODO: how to denote created files like this?
                                        # & (IsDir(t) >> ~IsUnread(ConcatPath(t, s))),
        success_postcond = (
            # TODO: decide whether target should be considered unread by default or inherit from source
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

        logging.critical(f"Unhandled cp invocation:\n{cmd_}\n{cmd}; falling back to default case")

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
        logging.critical(f"Unhandled cp invocation:\n{cmd_}\n{cmd}")

        if len(operands) < 2:
            raise NotImplementedError("invalid cp handling (< 2 operands) has not been implemented yet")

        # TODO: handle malformed cp calls (non-POSIX flags)
        raise NotImplementedError("non-POSIX cp handling has not been implemented yet")

    return CmdSpec(check, success_postcond, failure_postcond, io)


def echo_spec(cmd_: tuple[Field, ...]) -> CmdSpec:
    # https://pubs.opengroup.org/onlinepubs/9799919799/utilities/echo.html

    cmd = parse_command(cmd_)
    (name, flags, _, _) = (cmd.cmd_name, cmd.flags, cmd.options, cmd.operands)

    assert name == SymStr(("echo",)), f"Expected echo command, got: {name}"

    if flags != set(): # POSIX does not define any flags for echo
        logging.critical(f"Unhandled echo invocation:\n{cmd_}\n{cmd}")
        raise NotImplementedError(f"non-POSIX echo handling has not been implemented yet")

    # check:        none
    # z-postcond:   none
    # nz-postcond:  none

    return CmdSpec(Empty(), Empty(), Empty(), IOType.STDOUT)


def grep_spec(cmd_: tuple[Field, ...]) -> CmdSpec:
    # https://pubs.opengroup.org/onlinepubs/9799919799/utilities/grep.html

    cmd = parse_command(cmd_)
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
        logging.critical(f"Unhandled grep invocation:\n{cmd_}\n{cmd}; falling back to default case")

        check = Empty()
        success_postcond = Empty()
        failure_postcond = Empty()

    return CmdSpec(check, success_postcond, failure_postcond, io)


def mkdir_spec(cmd_: tuple[Field, ...]) -> CmdSpec:
    # https://pubs.opengroup.org/onlinepubs/9799919799/utilities/mkdir.html

    # Note:
    #   The file system model is a flat map, there is no hierarchy of directories
    #   So `mkdir -p a/b` will not be assumed to fail if `a` is a file, even though in reality it would

    cmd = parse_command(cmd_)
    (name, flags, _, operands) = (cmd.cmd_name, cmd.flags, cmd.options, cmd.operands)
    flags.discard("-m") # -m is used to control permissions, which we do not model
    io = IOType.NONE

    assert name == SymStr(("mkdir",)), f"Expected mkdir command, got: {name}"

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
        logging.critical(f"Unhandled mkdir invocation:\n{cmd_}\n{cmd}")

        # TODO: handle malformed mkdir calls (non-POSIX flags)
        raise NotImplementedError("non-POSIX mkdir handling has not been implemented yet")

    return CmdSpec(check, success_postcond, failure_postcond, io)


def mv_spec(cmd_: tuple[Field, ...]) -> CmdSpec:
    # https://pubs.opengroup.org/onlinepubs/9799919799/utilities/mv.html

    cmd = parse_command(cmd_)
    (name, flags, _, operands) = (cmd.cmd_name, cmd.flags, cmd.options, cmd.operands)
    flags.discard("-f") # ignore -f flag, i think it does not affect preconds/postconds

    assert name == SymStr(("mv",)), f"Expected mv command, got: {name}"

    if flags == set() and len(operands) == 2: # mv src dst
        # NOTE: `mv file nonexistent_dir/` fails (notice the trailing slash)
        # precond:      src must not be deleted, dst must not be an unread file
        # z-postcond:   too much to write here
        # nz-postcond:  none (maybe permission issue, etc.)
        src, dst = operands[0], operands[1]
        return CmdSpec(
            check=~IsDeleted(src) & # src must exist
                    (IsDir(src) >> ~IsFile(dst)) & # if src is a dir, dst must not be a file
                    (IsFile(dst) >> ~IsUnread(dst)), # if dst is a file, it must not be unread
            success_postcond=~StringEq(src, dst) >> (IsDeleted(src) & (IsFile(dst) | IsDir(dst))) # if src != dst, src is deleted, dst is a file or dir
                            & (StringEq(src, dst) >> (IsFile(dst) | IsDir(dst))) # if src == dst, nothing gets deleted, dst is a file or dir
                            & ((IsUnread(src) & ~IsDir(dst)) >> IsUnread(dst)), # if src was unread and dst wasn't a dir, dst is unread
                            # TODO: similar to cp, this postcond does not encode the new files created in the dir
            failure_postcond=Empty())
    if flags == set() and len(operands) > 2: # mv src... dst
        # precond:      all src must not be deleted, dst must be dir
        # z-postcond:   all src are deleted (unless dir moved to self...?)
        # nz-postcond:  none (maybe permission issue, etc.)
        srcs, dst = operands[:-1], operands[-1]
        return CmdSpec(
            check=And.from_field_iter(srcs, lambda path: ~IsDeleted(path)) & IsDir(dst),
            success_postcond=And.from_field_iter(srcs, IsDeleted), # TODO: similar to cp, this postcond does not encode the new files created in the dir
            failure_postcond=Empty())
    else:
        assert False, f"Unhandled mv invocation:\n{cmd_}\n{cmd}"


def rm_spec(cmd_: tuple[Field, ...]) -> CmdSpec:
    # https://pubs.opengroup.org/onlinepubs/9799919799/utilities/rm.html

    cmd = parse_command(cmd_)
    (name, flags, options, operands) = (cmd.cmd_name, cmd.flags, cmd.options, cmd.operands)
    io = IOType.STDIN if "-i" in flags else IOType.NONE
    io = IOType.add_stdout(io) if "-v" in flags else io
    io = IOType.remove_stdin(io) if "-f" in flags else io # 'rm -if ...' would not prompt
                                                          # 'rm -fi ...' would prompt, but we assume '-if' order as it is more dangerous
    flags.discard("-i")
    flags.discard("-v")
    if "-R" in flags or "-d" in flags:
        flags.discard("-R") # -R is equivalent to -r
        flags.discard("-d") # -d allows deletion of empty directories (which we cannot reason about)
        flags.add("-r") # same postconditions as -d

    assert name == SymStr(("rm",)), f"Expected rm command, got:\nOriginal: {cmd_}\nPaSh: {cmd}"
    assert len(options) == 0, f"Expected no options for rm, got:\nOriginal: {cmd_}\nPaSh: {cmd}"

    if flags == set(): # rm [-iv] file...
        # check:
        #   (1) all operands are files
        # z-postcond:
        #   (1) all operands are deleted
        # nz-postcond:
        #   (1) none (maybe all operands weren't files, maybe one operand wasn't a file, maybe it was a permission issue, etc.)

        check = And.from_field_iter(operands, IsFile)
        success_postcond = And.from_field_iter(operands, IsDeleted)
        failure_postcond = Empty()

    elif flags == set(["-f"]): # rm [-iv] -f file...
        # check:
        #   (1) all operands are not directories [and for bug-catching purposes: all operands are not deleted]
        # z-postcond:   all operands are deleted
        # nz-postcond:  none (maybe permission issue, etc.)
        check = And.from_field_iter(operands, lambda op: ~IsDir(op) & ~IsDeleted(op))
        success_postcond = And.from_field_iter(operands, IsDeleted)
        failure_postcond = Empty()

    elif flags == set(["-r"]): # rm [-iv] -d/-r/-R file...
        # check:
        #   (1) all operands are files or directories
        # z-postcond:
        #   (1) all operands are deleted
        # nz-postcond:
        #   (1) none (maybe permission issue, etc.)
        check = And.from_field_iter(operands, lambda op: IsFile(op) | IsDir(op))
        success_postcond = And.from_field_iter(operands, IsDeleted)
        failure_postcond = Empty()

    elif flags == set(["-r", "-f"]): # rm [-iv] -d/-r/-R -f file...
        # check:
        #   (1) [for bug catching purposes: all operands are not deleted]
        # z-postcond:
        #   (1) all operands are deleted
        # nz-postcond:
        #   (1) none (maybe permission issue, etc.)

        check = And.from_field_iter(operands, lambda op: ~IsDeleted(op))
        success_postcond = And.from_field_iter(operands, IsDeleted)
        failure_postcond = Empty()

    else:
        logging.critical(f"Unhandled mkdir invocation:\n{cmd_}\n{cmd}")

        # TODO: handle malformed rm calls (non-POSIX flags)
        raise NotImplementedError("non-POSIX rm handling has not been implemented yet")

    return CmdSpec(check, success_postcond, failure_postcond, io)


def sudo_spec(cmd_: tuple[Field, ...]) -> CmdSpec | None:

    cmd = parse_command(cmd_)
    operands = cmd.operands

    # Return the spec of the underlying command
    # TODO: A lot of interesting things can be modeled about sudo itself (e.g., permission denied, env vars, etc.)
    assert len(operands) >= 1, f"Expected at least one operand for sudo, got: {operands} for command {cmd_}"
    assert isinstance(operands[0].content, SymStr), f"Expected first operand of sudo to be a command string, got: {operands[0].content}"
    if isinstance(operands[0].content.parts[0], str):
        return get_spec(operands[0].content.parts[0], tuple(operands[1:]))
    else:
        logging.critical(f"Got non-str command name in sudo:{cmd_}\n{cmd}")
        return CmdSpec(Empty(), Empty(), Empty())


def touch_spec(cmd_: tuple[Field, ...]) -> CmdSpec:
    # https://pubs.opengroup.org/onlinepubs/9799919799/utilities/touch.html

    cmd = parse_command(cmd_)
    (name, flags, _, operands) = (cmd.cmd_name, cmd.flags, cmd.options, cmd.operands)
    io = IOType.NONE
    flags.discard("-a") # -a changes access time, which we do not model
    flags.discard("-d") # -d changes time to a specific date, which we do not model
    flags.discard("-m") # -m changes modification time, which we do not model
    flags.discard("-r") # -r changes time to that of a reference file, which we do not model
    flags.discard("-t") # -t changes time to a specific time, which we do not model

    assert name == SymStr(("touch",)), f"Expected touch command, got: {name}"

    if flags == set(): # touch file...
        # NOTE: Touch does not create unread files since they are empty upon creation
        # check:
        #   (1) none
        # z-postcond:
        #   (1) all operands are files or directories
        # nz-postcond:
        #   (1) none (maybe permission issue, etc.)

        check = Empty()
        success_postcond = And.from_field_iter(operands, lambda p: IsFile(p) | IsDir(p))
        failure_postcond = Empty()

    elif flags == set(["-c"]): # touch -c file...
        # -c tells touch to not create files if they do not exist, turning it essentially into a no-op for us

        check = Empty()
        success_postcond = Empty()
        failure_postcond = Empty()

    else:
        logging.critical(f"Unhandled touch invocation:\n{cmd_}\n{cmd}")

        raise NotImplementedError("non-POSIX touch handling has not been implemented yet")

    return CmdSpec(check, success_postcond, failure_postcond, io)

# -- Specs end here --
# Do not define specs below this line, they will not be registered!

current_module = sys.modules[__name__]
CMD_SPECS: dict[str, Callable] = {}

for name, func in inspect.getmembers(current_module, inspect.isfunction):
    if name.endswith("_spec") and not name.startswith("get_"):
        # derive command name by removing the "_spec" suffix
        cmd_name = name.removesuffix("_spec")
        CMD_SPECS[cmd_name] = func


def get_spec(cmd_name: str | None, cmd_: tuple[Field, ...]) -> CmdSpec | None:
    if cmd_name in CMD_SPECS:
        return CMD_SPECS[cmd_name](cmd_)
    logging.info(f"Specs are {CMD_SPECS}")
    logging.warning(f"No spec found for command '{cmd_name}', treating as no-op.")
    logging.critical(f"No spec found for '{cmd_name}'")


# TODO: in the postconds add env vars that change (e.g. PWD, OLDPWD, etc.)
# TODO: add default cases
# TODO: add comments with explanations for the default cases (why are they needed, what can go wrong otherwise?)
# TODO: add io information for each invocation (DoesIO constraints)
