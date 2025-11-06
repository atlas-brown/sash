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
def parse_command(cmd_inv: list[Field]) -> CmdInvocation:
    logging.debug(f"Parsing command from fields: {cmd_inv}")

    stringified_cmd = ""
    for field in cmd_inv:
        match field.content:
            case SymStr(parts):
                part_strs = []
                for part in parts:
                    match part:
                        case str(s):
                            part_strs.append(s)
                        case SymVar(name):
                            part_strs.append(f"${{{name}}}__idx__{cmd_inv.index(field)}")
                stringified_cmd += f" {' '.join(part_strs)}"
            case CompletelyArbitrary():
                stringified_cmd += f" $arbitrary__idx__{cmd_inv.index(field)}"
            case _:
                stringified_cmd += " $UNKNOWN"

    cmd_parsed = pash_annot_parser.parse(stringified_cmd.strip())
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


def rm_spec(cmd_: list[Field]) -> CmdSpec:

    cmd = parse_command(cmd_)

    logging.debug(f"Ignored irrelevant flags for rm: {cmd.flags - {'-r', '-f'}}")
    cmd = replace(cmd, flags={flag for flag in cmd.flags if flag in {"-r", "-f"}})

    # Supported invocations:
    # rm $PATH
    # rm -f $PATH
    # rm -r $PATH
    # rm -r -f $PATH
    match cmd:
        # ? is Reads() useful in rm? we implicitly get the same information through IsDeleted()
        case CmdInvocation(SymStr(["rm"]), flags, {}, operands) if not flags:
            # ? should a precond (in all cases) also be Not(IsDeleted())?
            # precond: all operands are files
            # z-postcond: all operands are deleted
            # nz-postcond: none (maybe all operands weren't files, maybe one operand wasn't a file, maybe it was a permission issue, etc.)
            return CmdSpec(
                precond=reduce(lambda acc, path: And(acc, IsFile(path.content)), operands, Empty()),
                success_postcond=reduce(lambda acc, path: And(acc, And(IsDeleted(path.content), Reads(path.content))), operands, Empty()),
                failure_postcond=Empty())
        case CmdInvocation(SymStr(["rm"]), flags, {}, operands) if flags == set(["-f"]):
            # precond: all operands are not directories [and for bug-catching purposes: all operands are not deleted]
            # z-postcond: all operands are deleted
            # nz-postcond: none (maybe permission issue, etc.)
            return CmdSpec(
                precond=reduce(lambda acc, path: And(acc, And(Not(IsDir(path.content)), Not(IsDeleted(path.content)))), operands, Empty()),
                success_postcond=reduce(lambda acc, path: And(acc, And(IsDeleted(path.content), Reads(path.content))), operands, Empty()),
                failure_postcond=Empty())
        case CmdInvocation(SymStr(["rm"]), flags, {}, operands) if flags == set(["-r"]):
            # precond: all operands are files or directories
            # z-postcond: all operands are deleted
            # nz-postcond: none (maybe permission issue, etc.)
            return CmdSpec(
                precond=reduce(lambda acc, path: And(acc, Or(IsFile(path.content), IsDir(path.content))), operands, Empty()),
                success_postcond=reduce(lambda acc, path: And(acc, And(IsDeleted(path.content), Reads(path.content))), operands, Empty()),
                failure_postcond=Empty())
        case CmdInvocation(SymStr(["rm"]), flags, {}, operands) if flags == set(["-r", "-f"]):
            # precond: all operands are not deleted
            # z-postcond: all operands are deleted
            # nz-postcond: none (maybe permission issue, etc.)
            return CmdSpec(
                precond=reduce(lambda acc, path: And(acc, Not(IsDeleted(path.content))), operands, Empty()),
                success_postcond=reduce(lambda acc, path: And(acc, And(IsDeleted(path.content), Reads(path.content))), operands, Empty()),
                failure_postcond=Empty())
        case CmdInvocation(_, _, _, path):
            # treat all other invocations as no-ops (that read the operands)
            # ? what if we know that an operand doesn't exist? is it a good idea to have the Reads() postcond?
            return CmdSpec(
                precond=Empty(),
                success_postcond=reduce(lambda acc, p: And(acc, Reads(p.content)), path, Empty()),
                failure_postcond=Empty())

    assert False, "unreachable"
    return CmdSpec(
        precond=Empty(),
        success_postcond=Empty(),
        failure_postcond=Empty()
    )
