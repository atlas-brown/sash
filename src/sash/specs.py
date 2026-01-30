import inspect
import logging
import sys
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from math import inf
import shlex
from dataclasses import replace

import shasta.ast_node as AST
from pash_annotations.parser import parser as pash_annot_parser

from sash.constraints import (
    And,
    CommandExists,
    Constraint,
    Empty,
    Not,
    IOType,
    IsDeleted,
    IsDir,
    IsFile,
    IsRead,
    StringEq,
)
from sash.frozen import freeze_thing
from sash.symbolic.strings import (
    ArbitraryType,
    CompletelyArbitrary,
    Field,
    SymStr,
    SymVar,
    WordCount,
)
from sash.symbolic.state import RefineableConstraint, SimpleConstraint
import sash.reporter as reporter


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
    check: RefineableConstraint | None # conditions to check before executing the command to detect possible bugs
    success_postcond: Constraint | tuple[Constraint, Constraint] # post-condition if exit code is 0; if pair, it's info about state before exec, and info about state after exec
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
    for i, field in enumerate(cmd_inv):
        curr_field = ""
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
                curr_field = "".join(part_strs)
            case CompletelyArbitrary():
                curr_field = f"$arbitrary__idx__{cmd_inv.index(field)}"

        stringified_cmd += " " + curr_field

    shlex_map = {}
    for i, tok in enumerate(shlex.split(stringified_cmd)):
        shlex_map[tok] = i

    cmd_parsed = pash_annot_parser.parse(stringified_cmd.strip())
    logging.debug("Parsed command: %s", cmd_parsed)
    cmd_flags = set()
    cmd_options = dict()
    cmd_operands = []

    def get_corresponding_field(s: str) -> Field:
        if "__idx__" in s:
            idx = int(s.split("__idx__")[-1])
            return cmd_inv[idx]
        return cmd_inv[shlex_map[s]]
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
        if part == "--":
            # Stop flag parsing after '--'
            remaining_operands.extend(operands[idx + 1:])
            break
        elif part.startswith('-') and len(part) == 2:
            flags.add(part)
        elif part.startswith('--') and len(part) > 2:
            logging.debug("Found long flag '%s' in %s invocation", part, cmd)
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
        succ         = Empty()
        io = IOType.add_stdout(io)

    elif flags == set() and len(operands) >= 1: # alias name[=value] ...
        # defines aliases
        succ = Empty()

        for op in operands:
            if isinstance(op.content, SymStr) and isinstance(op.content.parts[0], str):
                if '=' in op.content.parts[0]: # alias name=value ...
                    name, _ = op.content.parts[0].split('=', 1)
                    succ = succ & CommandExists(Field(SymStr((name,)), WordCount(1,1)))
                else: # alias name ...
                    io = IOType.add_stdout(io)
            # TODO: unclear whether (and when) the previous 'if' case can be false

    else: # non-POSIX
        log_crit_unhandled_inv(cmd)
        return handle_non_posix(cmd)

    return CmdSpec(None, succ, Empty(), io)


def cd_spec(cmd: CmdInvocation) -> CmdSpec:
    # https://pubs.opengroup.org/onlinepubs/9799919799/utilities/cd.html
    (name, flags, options, operands) = (cmd.cmd_name, cmd.flags, cmd.options, cmd.operands)
    io = IOType.NONE

    assert name == SymStr(("cd",)), f"Expected cd command, got: {name}"
    assert len(flags) == 0 and len(options) == 0, f"The current parsing function does not produce flags/options for cd; something changed?"

    flags, operands = extract_flags_naively("cd", operands)

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

    if flags == set() and len(operands) == 0: # cd
        assertion    = SimpleConstraint(IsDir(home_var), lambda line: reporter.ExpectedPathState("cd", 'directory', (home_var,), line))
        succ         = IsDir(home_var) & StringEq(pwd_var, home_var) & IsDir(pwd_var)
        succ_no_impl = succ
    elif flags == set() and len(operands) == 1 and operands[0].try_to_str() == "-": # cd -
        assertion    = SimpleConstraint(IsDir(oldpwd_var), lambda line: reporter.ExpectedPathState("cd", 'directory', (oldpwd_var,), line))
        succ         = IsDir(pwd_var)
        succ_no_impl = succ
        io = IOType.add_stdout(io)
    elif flags == set() and len(operands) == 1: # cd dir
        d = operands[0]
        assertion    = SimpleConstraint(IsDir(d), lambda line: reporter.ExpectedPathState("cd", 'directory', (d,), line))
        succ         = IsDir(d) & StringEq(pwd_var, d) & IsDir(pwd_var)
        succ_no_impl = succ
    else:
        log_crit_unhandled_inv(cmd)

        assertion    = None
        succ         = IsDir(pwd_var)
        succ_no_impl = succ

    return CmdSpec(assertion, succ, Empty(), io)


def command_spec(cmd: CmdInvocation) -> CmdSpec:
    # https://pubs.opengroup.org/onlinepubs/9799919799/utilities/command.html

    return Command.handle_invocation(cmd)


def cp_spec(cmd: CmdInvocation) -> CmdSpec:
    # https://pubs.opengroup.org/onlinepubs/9799919799/utilities/cp.html
    (name, flags, options, operands) = (cmd.cmd_name, cmd.flags, cmd.options, cmd.operands)

    assert name == SymStr(("cp",)), f"Expected cp command, got: {name}"
    assert len(flags) == 0 and len(options) == 0, f"The current parsing function does not produce flags/options for cp; something changed?"

    flags, operands = extract_flags_naively("cp", operands)

    io = IOType.NONE
    io = IOType.add_stdin(io) if "-i" in flags else io
    io = IOType.remove_stdin(io) if "-f" in flags else io

    flags.discard("-p") # -p is used to control metadata of the created files
    flags.discard("-P") # -P specifies that all actions be done on symbolic links themselves instead of their targets
    flags.discard("-f") # -f makes the command silently overwrite non-writable files, without asking for confirmation
    flags.discard("-i")

    if len(operands) < 2:
        logging.error("cp command with less than 2 operands is invalid; treating as no-op")
        return CmdSpec(None, Empty(), Empty(), IOType.UNKNOWN)

    srcs, dst = operands[:-1], operands[-1]
    if len(operands) > 2 or is_definitely_dir(dst) or any(will_definitely_expand(op) for op in srcs):
        # Copying to a directory
        if "-R" not in flags:
            assertion    = RefineableConstraint(IsDir(dst) & And.from_field_iter(srcs, IsFile),
                                                ((IsDir(dst), lambda line: reporter.ExpectedPathState("cp", 'directory', (dst,), line)),
                                                 (And.from_field_iter(srcs, IsFile), lambda line: reporter.ExpectedPathState("cp", 'files', tuple(srcs), line))))
            succ         = IsDir(dst) & And.from_field_iter(srcs, IsRead)
            succ_no_impl = succ
        else:
            assertion    = RefineableConstraint(IsDir(dst) & And.from_field_iter(srcs, lambda op: ~IsDeleted(op)),
                                                ((IsDir(dst), lambda line: reporter.ExpectedPathState("cp", 'directory', (dst,), line)),
                                                 (And.from_field_iter(srcs, lambda op: ~IsDeleted(op)), lambda line: reporter.ExpectedPathState("cp", 'existant', tuple(srcs), line))))
            succ         = IsDir(dst) & And.from_field_iter(srcs, lambda op: IsFile(op) >> IsRead(op))
            succ_no_impl = IsDir(dst) & And.from_field_iter(srcs, lambda op: IsRead(op) | IsDir(op))
    else:
        # Copying to a file or directory or nothing
        if "-R" not in flags:
            # assertion    = (IsRead(dst) | ~IsFile(dst)) & IsFile(srcs[0])
            assertion    = RefineableConstraint((IsRead(dst) | ~IsFile(dst)) & IsFile(srcs[0]),
                                                ((IsFile(dst) >> IsRead(dst), lambda line: reporter.DataLoss("cp", (dst,), line)),
                                                 (IsFile(srcs[0]), lambda line: reporter.ExpectedPathState("cp", 'file', (srcs[0],), line))))
            succ         = (((IsRead(dst) | IsDeleted(dst)) >> IsFile(dst))) & IsRead(srcs[0])
            succ_no_impl = (IsDir(dst) | IsFile(dst)) & IsRead(srcs[0])
        else:
            # assertion    = ((IsRead(dst) | ~IsFile(dst)) & IsFile(srcs[0])) | (~IsFile(dst) & IsDir(srcs[0]))
            assertion    = RefineableConstraint(((IsRead(dst) | ~IsFile(dst)) & IsFile(srcs[0])) | \
                                                (~IsFile(dst) & IsDir(srcs[0])),
                                                ((IsFile(dst) >> IsRead(dst), lambda line: reporter.DataLoss("cp", (dst,), line)),
                                                 (IsFile(srcs[0]) | IsDir(srcs[0]), lambda line: reporter.ExpectedPathState("cp", 'file or directory', (srcs[0],), line)),
                                                 (IsDir(srcs[0]) >> ~IsFile(dst), lambda line: reporter.ExpectedPathState("cp", f'not a file (because {srcs[0]} must be a directory)', (dst,), line))))
            succ         = (IsFile(srcs[0]) >> IsRead(srcs[0])) & \
                           (IsRead(dst) >> IsFile(dst)) & \
                           (IsDeleted(dst) >> \
                               (IsFile(srcs[0]) >> IsFile(dst)) & \
                               (IsDir(srcs[0]) >> IsDir(dst)))
            succ_no_impl = (IsRead(srcs[0]) & (IsFile(dst) | IsDir(dst))) | \
                           (IsDir(srcs[0]) & IsDir(dst))

    return CmdSpec(assertion, succ, Empty(), io)


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

    return CmdSpec(None, Empty(), Empty(), io)


def grep_spec(cmd: CmdInvocation) -> CmdSpec:
    # https://pubs.opengroup.org/onlinepubs/9799919799/utilities/grep.html
    (name, flags, _, operands) = (cmd.cmd_name, cmd.flags, cmd.options, cmd.operands)
    io = IOType.STDOUT if "-q" not in flags else IOType.NONE
    flags.discard("-q")

    assert name == SymStr(("grep",)), f"Expected grep command, got: {name}"

    if flags == set() and len(operands) == 1: # grep pattern
        check = None
        success_postcond = Empty()
        failure_postcond = Empty()
        io = IOType.add_stdin(io)

    elif flags == set() and len(operands) >= 1: # grep pattern file...
        files = operands[1:]
        check = SimpleConstraint(And.from_field_iter(files, IsFile),
                                 lambda line: reporter.ExpectedPathState("grep", 'file', tuple(files), line))
        success_postcond = And.from_field_iter(files, IsRead)
        failure_postcond = Empty()

    else:
        log_crit_unhandled_inv(cmd)

        check = None
        success_postcond = Empty()
        failure_postcond = Empty()
        io = IOType.UNKNOWN

    return CmdSpec(check, success_postcond, failure_postcond, io)


def mkdir_spec(cmd: CmdInvocation) -> CmdSpec:
    # https://pubs.opengroup.org/onlinepubs/9799919799/utilities/mkdir.html
    (name, flags, options, operands) = (cmd.cmd_name, cmd.flags, cmd.options, cmd.operands)

    assert name == SymStr(("mkdir",)), f"Expected mkdir command, got: {name}"
    assert len(flags) == 0 and len(options) == 0, f"The current parsing function does not produce flags/options for mkdir; something changed?"

    flags, operands = extract_flags_naively("mkdir", operands)

    flags.discard("-m") # -m is used to control permissions, which we do not model
    flags.discard("--") # -- is used to indicate the end of flags
    io = IOType.NONE
    io = IOType.add_stdout(io) if "-v" in flags else io
    flags.discard("-v")

    if flags == set(["-p"]):
        assertion = SimpleConstraint(And.from_field_iter(operands, lambda op: ~IsFile(op)),
                                     lambda line: reporter.ExpectedPathState("mkdir", 'non-files', tuple(operands), line))

    elif flags == set():
        assertion = SimpleConstraint(And.from_field_iter(operands, lambda op: ~(IsFile(op) | IsDir(op))),
                                     lambda line: reporter.ExpectedPathState("mkdir", 'non-existant', tuple(operands), line))

    else:
        return handle_non_posix(cmd)

    succ         = And.from_field_iter(operands, IsDir)
    succ_no_impl = succ

    return CmdSpec(assertion, succ, Empty(), io, min_operands=1)


def mv_spec(cmd: CmdInvocation) -> CmdSpec:
    # https://pubs.opengroup.org/onlinepubs/9799919799/utilities/mv.html

    return Mv.handle_invocation(cmd)


def rm_spec(cmd: CmdInvocation) -> CmdSpec:
    # https://pubs.opengroup.org/onlinepubs/9799919799/utilities/rm.html

    return Rm.handle_invocation(cmd)

def mktemp_spec(cmd: CmdInvocation) -> CmdSpec:
    return Mktemp.handle_invocation(cmd)


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
        return CmdSpec(None, Empty(), Empty())


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
        assertion    = None
        succ         = And.from_field_iter(operands, IsRead)
        succ_no_impl = succ

    elif flags == set(["-c"]): # touch -c file...
        # -c tells touch to not create files if they do not exist, turning it essentially into a no-op for us

        assertion    = None
        succ         = Empty()
        succ_no_impl = succ

    else:
        return handle_non_posix(cmd)

    return CmdSpec(assertion, succ, Empty(), io)


def cat_spec(cmd: CmdInvocation) -> CmdSpec:
    # https://pubs.opengroup.org/onlinepubs/9799919799/utilities/cat.html

    (name, flags, _, operands) = (cmd.cmd_name, cmd.flags, cmd.options, cmd.operands)
    io = IOType.STDOUT

    assert name == SymStr(("cat",)), f"Expected cat command, got: {name}"

    if flags == set(): # cat file...
        assertion        = SimpleConstraint(And.from_field_iter(operands, IsFile),
                                            lambda line: reporter.ExpectedPathState("cat", 'files', tuple(operands), line))
        success_postcond = And.from_field_iter(operands, IsRead)

    else:
        return handle_non_posix(cmd)

    return CmdSpec(assertion, success_postcond, Empty(), io)


def file_spec(cmd: CmdInvocation) -> CmdSpec:
    # https://pubs.opengroup.org/onlinepubs/9799919799/utilities/file.html
    # The spec is (almost) identical to the `cat` spec other than the command name.

    (name, flags, _, operands) = (cmd.cmd_name, cmd.flags, cmd.options, cmd.operands)
    io = IOType.STDOUT

    assert name == SymStr(("file",)), f"Expected file command, got: {name}"

    if flags == set(): # file file...
        check            = SimpleConstraint(And.from_field_iter(operands, IsFile),
                                            lambda line: reporter.ExpectedPathState("file", 'files', tuple(operands), line))
        success_postcond = And.from_field_iter(operands, IsFile)

    else:
        return handle_non_posix(cmd)

    return CmdSpec(check, success_postcond, Empty(), io)


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
    logging.debug("Unknown command '%s'; treating as no-op.", cmd_name)


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
    return CmdSpec(None, Empty(), Empty(), IOType.UNKNOWN) # no-op spec


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

        logging.debug("Unhandled invocation for command '%s':\n%s; treating as no-op",
                         cmd.cmd_name, json.dumps(cmd_json, indent=2, default=str))
        return CmdSpec(None, Empty(), Empty(), IOType.UNKNOWN)

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

        check = None
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
            success_postcond = CommandExists(subcmd)
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
            return f
            assert False, f"Attempted to trimr {suffix} from {f}, which doesn't have that suffix"


class Mv(Cmd):
    # https://pubs.opengroup.org/onlinepubs/9799919799/utilities/mv.html
    name = "mv"
    posix_flags     = {"-f", "-i", "-t"}
    supported_flags = {"-f", "-i", "-t"}

    @classmethod
    def _handle_supported(cls, cmd: CmdInvocation) -> CmdSpec:
        (name, flags, options, operands) = (cmd.cmd_name, cmd.flags, cmd.options, cmd.operands)
        io = IOType.NONE
        io = IOType.add_stdin(io) if "-i" in flags else io
        io = IOType.remove_stdin(io) if "-f" in flags else io

        flags.discard("-i")
        flags.discard("-f")

        operands = [o for o in operands if o != Field(content=SymStr(parts=('--',)), count=WordCount(min=1, max=1))]

        assert name == SymStr((cls.name,)), f"Expected mv command, got: {name}"

        if len(operands) < (2 if "-t" not in options else 1):
            logging.error("mv command with less than 2 operands is invalid; treating as no-op")
            return CmdSpec(None, Empty(), Empty(), IOType.UNKNOWN)

        if "-t" in options:
            srcs, dst = operands, options["-t"]
        else:
            srcs, dst = operands[:-1], operands[-1]

        if "-t" in options or len(operands) > 2 or is_definitely_dir(dst) or any(will_definitely_expand(op) for op in srcs): # Todo: what if dst expands?
            # Moving to a directory
            #srcs = [Field_trimR(s, "/*") for s in srcs]
            glob_srcs = []
            other_srcs = []
            for src in srcs:
                match src:
                    case Field(SymStr(parts), wc) if len(parts) >= 1 and isinstance(parts[-1], str) and parts[-1].endswith("/*"):
                        trimmed = Field_trimR(replace(src, count=WordCount(1, 1)), "*")
                        glob_srcs.append(trimmed)
                    case _:
                        other_srcs.append(src)

            # assertion    = IsDir(dst) \
            #              & And.from_field_iter(other_srcs, lambda src: ~IsDeleted(src)) \
            #              & And.from_field_iter(glob_srcs, IsDir)
            assertion    = RefineableConstraint(IsDir(dst) \
                                                & And.from_field_iter(other_srcs, lambda src: ~IsDeleted(src)) \
                                                & And.from_field_iter(glob_srcs, IsDir),
                                                ((IsDir(dst) & And.from_field_iter(glob_srcs, IsDir),
                                                  lambda line: reporter.ExpectedPathState('mv', 'directories', tuple([dst] + glob_srcs), line)),
                                                 (And.from_field_iter(other_srcs, lambda src: ~IsDeleted(src)),
                                                  lambda line: reporter.ExpectedPathState('mv', 'existant', tuple(other_srcs), line))))
            succ         = IsDir(dst) \
                         & And.from_field_iter(other_srcs, IsDeleted)
            succ_no_impl = succ
        else:
            # Moving to a file or directory
            # assertion = ( IsRead(dst) | ~IsFile(dst) ) & ~IsDeleted(srcs[0])
            # assertion = ~IsDeleted(srcs[0]) & (IsRead(dst) | IsDir(dst))
            assertion = ((And.from_field_iter(srcs, lambda src: ~IsDeleted(src)),
                          lambda line: reporter.ExpectedPathState('mv', 'existant', tuple(srcs), line)),)
            if len(srcs) > 1:
                assertion = assertion + ((IsDir(dst), lambda line: reporter.ExpectedPathState('mv', 'directory', (dst,), line)),)
            else:
                assertion = assertion + (((IsFile(dst) >> IsFile(srcs[0])),
                                          lambda line: reporter.ExpectedPathState('mv', 'file (because destination is a file)', (srcs[0],), line)),
                                         ((IsFile(dst) >> IsRead(dst)) & (~IsFile(dst) >> IsDir(dst)),
                                          lambda line: reporter.DataLoss('mv', (dst,), line)),)
            assertion = RefineableConstraint(And.from_field_iter(srcs, lambda src: ~IsDeleted(src)) & (IsRead(dst) | IsDir(dst)) & (IsFile(dst) >> IsFile(srcs[0])),
                                             assertion)
            succ = (And.from_field_iter(srcs, lambda src: ~IsDeleted(src)) & (IsFile(dst) >> IsFile(srcs[0])),
                    IsDeleted(srcs[0]) & ((IsFile(srcs[0]) & ~IsDir(dst)) >> IsFile(dst)) & (IsDir(srcs[0]) >> IsDir(dst)))
            # succ         = IsDeleted(srcs[0]) & \
            #                (IsDeleted(dst) >> \
            #                    (IsFile(srcs[0]) >> IsFile(dst)) & \
            #                    (IsDir(srcs[0]) >> IsDir(dst)))
            succ_no_impl = IsDeleted(srcs[0]) & (IsFile(dst) | IsDir(dst))

        return CmdSpec(assertion, succ, Empty(), io)


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

        recursive = any(f in flags for f in ("-d", "-r", "-R")) # -R is equivalent to -r
                                                                # -d allows deletion of empty directories (we don't model emptiness, so we overapproximate)

        if recursive and operands:
            # And.from_field_iter(operands, lambda op: IsRead(op) | IsDir(op)),
            assertion = RefineableConstraint(And.from_field_iter(operands, lambda op: IsRead(op) | IsDir(op)),
                                             ((And.from_field_iter(operands, lambda op: IsFile(op) >> IsRead(op)),
                                               lambda line: reporter.DataLoss('rm', operands, line)),
                                              (And.from_field_iter(operands, lambda op: ~IsFile(op) >> IsDir(op)),
                                               lambda line: reporter.ExpectedPathState('rm', 'existant', tuple(operands), line))))
        elif operands:
            assertion = RefineableConstraint(And.from_field_iter(operands, IsRead),
                                             ((And.from_field_iter(operands, IsFile),
                                               lambda line: reporter.ExpectedPathState('rm', 'existant', tuple(operands), line)),
                                              (And.from_field_iter(operands, IsRead),
                                               lambda line: reporter.DataLoss('rm', operands, line))))
        else:
            assertion = None

        succ         = And.from_field_iter(operands, IsDeleted)
        succ_no_impl = succ

        return CmdSpec(assertion, succ, Empty(), io)


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

        check = None
        succ = None
        fail = None
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
                succ = Empty()
                fail = Empty()
            case 1: # test string
                op = operands[0]
                if op.try_to_str() == '=':
                    fail = StringEq(Field(SymStr(("",)), WordCount(1,1)), Field(SymStr(("",)), WordCount(1,1))) # TODO: Remove, this is caused by a bug during expansion
                else:
                    # z-postcond is the negation of nz-postcond
                    fail = StringEq(op, Field(SymStr(("",)), WordCount(1,1)))
            case 2: # test -flag operand
                op = operands[1]
                flag = operands[0].content.parts[0] if isinstance(operands[0].content, SymStr) else None
                empty_str_var = Field(SymStr(("",)), WordCount(0,0))

                match flag:
                    case "-d":
                        succ = IsDir(op)
                        # nz-postcond is the negation of z-postcond
                    case "-f":
                        succ = IsFile(op)
                        # nz-postcond is the negation of z-postcond
                    case "-e":
                        succ = IsFile(op) | IsDir(op)
                        # nz-postcond is the negation of z-postcond
                    case "-n":
                        # z-postcond is the negation of nz-postcond
                        fail = StringEq(op, empty_str_var)
                    case "-r" | "-w" | "-x":
                        succ = IsFile(op) | IsDir(op)
                        fail = Empty()
                    case "-z":
                        # TODO: (false positives) this test is often used to check if an env var is set, and if not to set it
                        # to reason about this kind of usage, we should add a failure postcondition if `op` is a var capturing that it is defined and has a value
                        succ = StringEq(op, empty_str_var)
                        # nz-postcond is the negation of z-postcond
                    case _:
                        logging.debug("Unhandled test invocation: %s; treating as no-op", cmd)
                        succ = Empty()
                        fail = Empty()
            case 3: # test operand1 operator operand2
                op1 = operands[0]
                operator = operands[1].content.parts[0] if isinstance(operands[1].content, SymStr) else None
                op2 = operands[2]

                match operator:
                    case "=":
                        succ = StringEq(op1, op2)
                        # nz-postcond is the negation of z-postcond
                    case "!=":
                        # z-postcond is the negation of nz-postcond
                        fail = StringEq(op1, op2)
                    case "-eq" | "-ge" | "-le":
                        succ = Empty() # no info gained about string equality
                                                   # -eq: 'test 05 -eq 5' and 'test 5 -eq 5' both succeed, but strings are not necessarily equal
                                                   # -ge/-le: 'test 5 -ge/-le 5' and 'test 05 -ge/-le 5' both succeed, but strings are not necessarily equal
                        fail = ~StringEq(op1, op2)
                    case "-ne" | "-gt" | "-lt":
                        succ = ~StringEq(op1, op2)
                        fail = Empty() # no info gained about string equality
                                                   # -ne: 'test 05 -ne 5' and 'test 5 -ne 5' both fail, but strings are not necessarily equal
                                                   # -gt/-lt: 'test 5 -gt/-lt 5' and 'test 05 -gt/-lt 5' both fail, but strings are not necessarily equal
                    case _:
                        logging.debug("Unhandled test invocation: %s; treating as no-op", cmd)
                        succ = Empty()
                        fail = Empty()
            case _:
                # NOTE: According to POSIX, in this case "the results are unspecified"
                logging.warning("%s invocation with more than 4 operands is unspecified: %s; treating as no-op", cls.name, cmd)
                succ = Empty()
                fail = Empty()

        # In most cases the postconditions are negations of each other, so use this pattern to reduce code and possible mistakes
        match succ, fail:
            case None, None:
                assert False, f"Unreachable, yet reached with invocation: {cmd}"
            case None, _:
                succ = ~fail
            case _, None:
                fail = ~succ

        if negated:
            (succ, fail) = (fail, succ)

        return CmdSpec(check, succ, fail, io)


class Env(Cmd):
    name = "env"
    posix_flags     = set()
    supported_flags = set()

    @classmethod
    def _handle_supported(cls, cmd: CmdInvocation) -> CmdSpec:
        (name, flags, options, operands) = (cmd.cmd_name, cmd.flags, cmd.options, cmd.operands)
        assert name == SymStr((cls.name,)), f"Expected env command, got: {name}"
        assert len(flags) == 0 and len(options) == 0, f"The current parsing function does not produce flags/options for env; something changed?"

        check = None
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


class Mktemp(Cmd):
    # https://pubs.opengroup.org/onlinepubs/9799919799/utilities/rm.html
    name = "mktemp"
    posix_flags     = {"-q", "-d"}
    supported_flags = posix_flags

    @classmethod
    def _handle_supported(cls, cmd: CmdInvocation) -> CmdSpec:
        (name, flags, options, operands) = (cmd.cmd_name, cmd.flags, cmd.options, cmd.operands)
        flags, operands = extract_flags_naively("mktemp", operands)

        assert name == SymStr((cls.name,)), f"Expected mktemp command, got: {cmd}"
        assert len(options) == 0, f"Expected no options for mktemp, got: {cmd}"

        io = IOType.STDOUT_DIR if "-d" in flags else IOType.STDOUT_FILE
        return CmdSpec(None, Empty(), Empty(), io)


def is_definitely_dir(field: Field) -> bool:
    return (
        isinstance(field.content, SymStr) and
        isinstance(last_part := field.content.parts[-1], str) and (
            last_part == "." or
            last_part == ".." or
            last_part.endswith("/") or
            last_part.endswith("/.") or
            last_part.endswith("/..")
        )
    )


def will_definitely_expand(field: Field) -> bool:
    if field.count.min > 1:
        return True

    if field.count.max < 2 or isinstance(field.content, CompletelyArbitrary):
        return False

    # * Assumption: If a glob is present, it will match more than one path
    for part in field.content.parts:
        if isinstance(part, str) and "*" in part:
            return True

    return False
