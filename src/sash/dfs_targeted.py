import logging
from dataclasses import dataclass, replace
from typing import Callable

import shasta.ast_node as AST

import sash.util as util
from sash.interpreter_config import BranchDecision, InterpConfig, UnboundVariablePolicy
from sash.specs import CMD_SPECS
from sash.symbolic.state import Trace, Traces


@dataclass(frozen=True)
class TargetedDfsResult:
    traces: Traces


def run_targeted_dfs(nodes: list,
                     config: InterpConfig,
                     symb_engine: Callable[[list, InterpConfig], Traces],
                     func_defs: dict,
                     ignore_function_calls_for: frozenset[str],
                     trace_cap: int = 8) -> TargetedDfsResult:
    def constant_word(arg: list[AST.ArgChar]) -> str | None:
        chars: list[str] = []
        for c in arg:
            if isinstance(c, AST.CArgChar):
                chars.append(chr(c.char))
            else:
                return None
        return "".join(chars)

    def command_name(node: AST.CommandNode) -> str | None:
        if not node.arguments:
            return None
        return constant_word(node.arguments[0])

    def has_spec(name: str) -> bool:
        return name in CMD_SPECS

    def count_cmds(node: AST.Command,
                   matcher: Callable[[str, AST.CommandNode], bool],
                   seen_funcs: set[str] | None = None) -> int:
        seen_funcs = seen_funcs or set()
        count = 0
        for cmd in util.iter_ast_command(node):
            if not isinstance(cmd, AST.CommandNode):
                continue
            name = command_name(cmd)
            if name is None:
                continue
            if matcher(name, cmd):
                count += 1
                continue
            if name in seen_funcs:
                continue
            func_node = func_defs.get(name)
            if func_node is None:
                continue
            seen_funcs.add(name)
            count += count_cmds(func_node, matcher, seen_funcs)
        return count

    def count_spec_cmds(node: AST.Command, seen_funcs: set[str] | None = None) -> int:
        return count_cmds(node, lambda name, _: has_spec(name), seen_funcs)

    def get_spec_coverage(state) -> set[int]:
        data = state.external_data
        if isinstance(data, (set, frozenset)):
            return set(data)
        return set()

    def spec_coverage_cb(traces: Traces, node: AST.AstNode) -> list[frozenset[int]] | None:
        if isinstance(node, AST.CommandNode):
            name = command_name(node)
            if name is not None and has_spec(name):
                line = node.line_number
                return [frozenset(get_spec_coverage(t.latest_state) | {line}) for t in traces]
        return None

    def collapse_traces_with_spec_coverage(traces: Traces, cap: int) -> Traces:
        if len(traces) <= cap:
            return traces
        remaining = list(traces)
        remaining.sort(key=lambda t: len(get_spec_coverage(t.latest_state)), reverse=True)
        selected: list[Trace] = []
        covered: set[int] = set()
        while remaining and len(selected) < cap:
            best_idx = max(
                range(len(remaining)),
                key=lambda i: len(get_spec_coverage(remaining[i].latest_state) - covered),
            )
            best = remaining.pop(best_idx)
            selected.append(best)
            covered |= get_spec_coverage(best.latest_state)
        if len(selected) < cap:
            remaining.sort(key=lambda t: len(get_spec_coverage(t.latest_state)), reverse=True)
            selected.extend(remaining[:cap - len(selected)])
        return selected

    def find_dangerous_lines() -> list[int]:
        lines: set[int] = set()
        for wrapped in nodes:
            for cmd in util.iter_ast_command(wrapped.ast_node):
                if isinstance(cmd, AST.CommandNode):
                    name = command_name(cmd) or ""
                    if is_dangerous_command(name):
                        lines.add(cmd.line_number)
        return sorted(lines)

    def branch_decider_prefer_spec(node: AST.AstNode) -> BranchDecision:
        if isinstance(node, AST.IfNode):
            then_score = count_spec_cmds(node.then_b)
            else_score = count_spec_cmds(node.else_b) if node.else_b is not None else 0
            return BranchDecision.FIRST if then_score >= else_score else BranchDecision.SECOND
        if isinstance(node, AST.AndNode) or isinstance(node, AST.OrNode):
            right_score = count_spec_cmds(node.right_operand)
            return BranchDecision.FIRST if right_score > 0 else BranchDecision.SECOND
        if isinstance(node, AST.WhileNode):
            body_score = count_spec_cmds(node.body)
            return BranchDecision.FIRST if body_score > 0 else BranchDecision.SECOND
        return BranchDecision.FIRST

    dangerous_lines = find_dangerous_lines()
    all_traces: list[Trace] = []
    for target_line in dangerous_lines:
        def branch_decider_for_target(node: AST.AstNode) -> BranchDecision | None:
            if isinstance(node, AST.IfNode):
                then_score = count_spec_cmds(node.then_b)
                else_score = count_spec_cmds(node.else_b) if node.else_b is not None else 0
                if then_score == 0 and else_score == 0:
                    return None
                return BranchDecision.FIRST if then_score >= else_score else BranchDecision.SECOND
            if isinstance(node, AST.AndNode) or isinstance(node, AST.OrNode):
                right_score = count_spec_cmds(node.right_operand)
                return BranchDecision.FIRST if right_score > 0 else None
            if isinstance(node, AST.WhileNode):
                body_score = count_spec_cmds(node.body)
                return BranchDecision.FIRST if body_score > 0 else None
            return None

        def branch_decider_target(node: AST.AstNode) -> BranchDecision:
            decision = branch_decider_for_target(node)
            return decision if decision is not None else branch_decider_prefer_spec(node)

        logging.info("DFS run: targeting dangerous command at line %d", target_line)
        target_traces = symb_engine(nodes, replace(
            config,
            branch_decider=branch_decider_target,
            unbound_policy=UnboundVariablePolicy.EMPTY,
            trace_collapser=lambda ts: collapse_traces_with_spec_coverage(ts, trace_cap),
            node_cbs=config.node_cbs + [spec_coverage_cb],
            ignore_function_calls_for=ignore_function_calls_for,
        ))
        all_traces.extend(target_traces)
        if any(target_line in get_spec_coverage(t.latest_state) for t in target_traces):
            logging.info("DFS run: reached dangerous command at line %d", target_line)
        else:
            logging.info("DFS run: did not reach dangerous command at line %d", target_line)

    if not dangerous_lines:
        target_traces = symb_engine(nodes, replace(
            config,
            branch_decider=branch_decider_prefer_spec,
            unbound_policy=UnboundVariablePolicy.EMPTY,
            trace_collapser=lambda ts: collapse_traces_with_spec_coverage(ts, 16),
            node_cbs=config.node_cbs + [spec_coverage_cb],
            ignore_function_calls_for=ignore_function_calls_for,
        ))
        all_traces.extend(target_traces)

    return TargetedDfsResult(traces=all_traces)

def is_dangerous_command(cmd_name: str) -> bool:
    # TODO: this should check the command's spec and return whether it can have any IsDeleted effects
    return cmd_name in {"rm", "rmdir", "mv", "xargs", "sudo"}
