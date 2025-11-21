from dataclasses import dataclass
from sash.state import Traces, Field
from dataclasses import field, replace
from collections.abc import Callable
import shasta.ast_node as AST

NodeCB = Callable[[Traces, AST.AstNode], list | None]
ExpandedCmdCB = Callable[[list[Field]], None]
TraceCollapser = Callable[[Traces], Traces]

@dataclass(frozen=True)
class InterpConfig:
    node_cbs: list[NodeCB] = field(default_factory=list)
    expanded_command_cbs: list[ExpandedCmdCB] = field(default_factory=list)
    trace_collapser: TraceCollapser = lambda ts: ts
    in_checked_position: bool = False
    max_loop_unroll: int = 2

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
