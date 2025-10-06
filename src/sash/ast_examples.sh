#! /bin/sh

echo "Hello"
# EchoHelloNode:
# CommandNode {
#   arguments: [[
#       CArgChar(e),
#       CArgChar(c),
#       CArgChar(h),
#       CArgChar(o),
#     ], [
#       QArgChar {
#         arg: [
#           CArgChar(H),
#           CArgChar(e),
#           CArgChar(l),
#           CArgChar(l),
#           CArgChar(o)
#         ]
#       ]
#     }
#   ]
# }

(echo "Hello")
# SubshellNode {
#   body: EchoHelloNode
# }

`echo "Hello"`
# BackquotedNode
# CommandNode {
#   arguments: [[
#     BArgChar {
#       node: EchoHelloNode
#     }
#   ]]
# }

$(echo "Hello")
# BackquotedNode

echo "Hello" | echo "Hello" | echo "Hello"
# PipeNode {
#   items: [
#     EchoHelloNode,
#     EchoHelloNode,
#     EchoHelloNode
#   ]
# }

echo "Hello" > file
# CommandNode {
#   arguments: [...],
#   redir_list: [
#     FileRedirNode {
#       arg: [
#         CArgChar(f),
#         CArgChar(i),
#         CArgChar(l),
#         CArgChar(e)
#       ],
#       redir_type: To
#     }
#   ]
# }

echo "Hello" >&2
# CommandNode {
#   arguments: [...],
#   redir_list: [
#     DupRedirNode {
#       arg: (var, [2])
#       dup_type: ToFD
#     }
#   ]
# }

b <<EOF
Hello
EOF
# CommandNode {
#   arguments: [[
#     b
#   ]],
#   redir_list: [
#     HeredocRedirNode {
#       arg: [
#         CArgChar(H),
#         CArgChar(e),
#         CArgChar(l),
#         CArgChar(l),
#         CArgChar(o),
#         CArgChar()
#       ]
#     }
#   ]
# }

echo "Hello" &
# BackgroundNode {
#   node: EchoHelloNode
# }

hello=world
# CommandNode {
#   assignments: [
#     AssignNode {
#       var: hello,
#       val: [
#         CArgChar(w),
#         CArgChar(o),
#         CArgChar(r),
#         CArgChar(l),
#         CArgChar(d)
#       ]
#     }
#   ]
# }

$hello
# CommandNode {
#   arguments: [[
#     VArgChar {
#       var: hello
#     }
#   ]]
# }

_=$((1+2))
# CommandNode {
#   assignments: [
#     AssignNode {
#       var: _
#       val: [
#         AArgChar {
#           arg: [
#             1,
#             +,
#             2
#           ]
#         }
#       ]
#     }
#   ]
# }

{ echo "Hello" ; echo "Hello" ; }
# SemiNode {
#   left_operand: EchoHelloNode,
#   right_operand: EchoHelloNode,
# }

{ echo "Hello" ; } > file
# RedirNode {
#   node: EchoHelloNode,
#   redir_list: [
#     FileRedirNode {...}
#   ]
# }

echo ~Hello/World
# CommandNode {
#   arguments: [[
#     CArgChar(e),
#     CArgChar(c),
#     CArgChar(h),
#     CArgChar(o),
#   ], [
#     TArgChar {
#       string: [
#         Some,
#         Hello
#       ]
#     },
#     CArgChar(/),
#     CArgChar(W),
#     CArgChar(o),
#     CArgChar(r),
#     CArgChar(l),
#     CArgChar(d)
#   ]]
# }
