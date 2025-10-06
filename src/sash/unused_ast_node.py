# isort: skip_file

# This file re-exports all AST node classes from shasta.ast_node with more user-friendly names.
# It also serves as a reference for all AST node types.
# For reference: https://github.com/binpash/shasta/blob/main/shasta/ast_node.py

# Base AST class: all other classes are subclasses of this
from shasta.ast_node import AstNode as BaseAstNode

# Secondary AST classes: all other classes are subclasses of at least one of these
from shasta.ast_node import (
    Command as BaseCommandNode,
    ArgChar as BaseCharNode,
    RedirectionNode as BaseRedirectionNode,
    BashNode as BaseBashNode,
)

# Exception: the only other BaseAstNode-only subclass
from shasta.ast_node import (
    AssignNode as AssignmentNode,  # ...=...
)

# BaseCommandNode subclasses
from shasta.ast_node import (
    PipeNode as PipelineNode,  # ... | ... | ...
    CommandNode as CommandNode,  # any simple command
    SubshellNode as SubshellNode,  # (...)
    AndNode as AndNode,  # ... && ...
    OrNode as OrNode,  # ... || ...
    SemiNode as SemicolonNode,  # ... ; ...
    NotNode as NotNode,  # ! ...
    RedirNode as RedirectionNode,  # any compound command, such as loops and control constructs, or {...} > ...
    BackgroundNode as BackgroundNode,  # ... &
    DefunNode as FunctionDefinitionNode,  # func() {...}
    ForNode as ForLoopNode,  # for ... in ...; do ...; done
    WhileNode as WhileLoopNode,  # while ...; do ...; done
    IfNode as IfNode,  # if ...; then ...; else ...; fi
    CaseNode as CaseNode,  # case ... in ...) ... ;; ...) ... ; esac
)

# BaseCharNode subclasses
from shasta.ast_node import (
    CArgChar as LiteralCharNode,  # any character which is interpreted literally
    EArgChar as EscapedCharNode,  # any escaped character
    TArgChar as TildeCharNode,  # the tilde (~) character when it's interpreted as $HOME
    AArgChar as ArithmeticCharNode,  # $((...))
    VArgChar as VariableCharNode,  # $...
    QArgChar as QuoteCharNode,  # "..."
    BArgChar as BackquoteCharNode,  # `...` or $(...)
)

# BaseRedirectionNode subclasses
from shasta.ast_node import (
    FileRedirNode as FileRedirectionNode,  # >..., >>..., <..., <<..., <>...
    DupRedirNode as DupRedirectionNode,  # >&..., <&...
    HeredocRedirNode as HeredocRedirectionNode,  # <<EOF...EOF
)

# BaseBashNode subclasses are not imported (but they exist)
