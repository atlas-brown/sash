from dataclasses import dataclass, field, replace
from collections.abc import Callable
import shasta.ast_node as AST
from enum import Enum

from sash.symbolic.strings import Field
from sash.symbolic.state import Trace, Traces
from sash.constraints import Constraint, Empty

NodeCB = Callable[[Traces, AST.AstNode], list | None]
ExpandedCmdCB = Callable[[list[Field]], None]
TraceCollapser = Callable[[Traces], tuple[Traces, Traces]]

class BranchDecision(Enum):
    ALL = 0
    FIRST = 1
    SECOND = 2


@dataclass(frozen=True)
class BranchSelection:
    decision: BranchDecision
    case_index: int | None = None


def select_all() -> BranchSelection:
    return BranchSelection(BranchDecision.ALL)


def select_first() -> BranchSelection:
    return BranchSelection(BranchDecision.FIRST)


def select_second() -> BranchSelection:
    return BranchSelection(BranchDecision.SECOND)


def select_case_index(index: int) -> BranchSelection:
    return BranchSelection(BranchDecision.FIRST, case_index=index)

BranchPolicy = Callable[[AST.AstNode, Traces, Traces], tuple[Traces, Traces]]
BranchPolicyPre = Callable[[AST.AstNode], BranchSelection] # This decides which branches to take before evaluating them

class UnboundVariablePolicy(Enum):
    EMPTY = 0
    SYMBOLIC = 1

@dataclass(frozen=True)
class InterpConfig:
    node_cbs: list[NodeCB] = field(default_factory=list)
    expanded_command_cbs: list[ExpandedCmdCB] = field(default_factory=list)
    trace_collapser: TraceCollapser = lambda ts: (ts, [])
    disable_trace_collapsing: bool = False
    in_checked_position: bool = False
    force_fork_all: bool = False
    max_loop_unroll: int = 2
    unbound_policy: UnboundVariablePolicy = UnboundVariablePolicy.SYMBOLIC
    DFS_first: bool = True
    branch_policy: BranchPolicy = lambda n, t_then, t_else: (t_then, t_else)
    branch_policy_pre: BranchPolicyPre | None = None
    ignore_function_calls: bool = False
    ignore_function_calls_for: frozenset[str] = field(default_factory=frozenset)
    current_pass: str = "default"
    current_pass_constraint: Constraint = Empty()
    debug_instrumentation: bool = False
    disable_solver_optimizations: bool = False
    pwd_init_var: str = "PWD_INIT"

    def add_node_callback(self, cb: NodeCB) -> 'InterpConfig':
        return replace(self, node_cbs=(self.node_cbs + [cb]))

    def add_expanded_command_callback(self, cb: ExpandedCmdCB) -> 'InterpConfig':
        return replace(self, expanded_command_cbs=(self.expanded_command_cbs + [cb]))

    def set_trace_collapser(self, f: TraceCollapser) -> 'InterpConfig':
        return replace(self, trace_collapser=f)

    def apply_node_cbs(self, traces: Traces, node: AST.AstNode) -> Traces:
        res = traces
        for node_cb in self.node_cbs:
            external_fields = node_cb(res, node)
            if external_fields:
                assert len(external_fields) == len(traces)
                res = [trace.extend(lambda s: s.set_external(new_val)) for trace, new_val in zip(res, external_fields)]
        return res

    def apply_expanded_command_cbs(self, args: list[Field]) -> None:
        for cb in self.expanded_command_cbs:
            cb(args)
