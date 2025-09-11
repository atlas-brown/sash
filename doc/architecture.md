# System architecture
``` text
                                                      single
                        +---------------------------+ command     +---------------+
  +----------+          |                           |------------>|  symbolic     |
  |shasta AST|--------->|      interpreter          |<------------|  expander     |
  +----------+          |                           | all poss    +---------------+
 (from libdash)   ,---->+---------------------------+  expansions
                 /              ↓  ↑                 \  + constraints
                /        +----------------+          |
               /         | set of Traces  |          |
  +--------------+       +----------------+           \
  |  command     |                ↑                   |
  |  specs       |       +----------------+           |
  +--------------+       |    Trace       |           | after
                         | = seq of states|           | interpretation
                         +----------------+           `-.
                                  ↑                      \___
                         +----------------+                  `-> +-----------------+
                         |   Symbolic     |                      |                 |
                        /|   state        |                      |   Conditions    |
                      /  +----------------+                      |(root del'd? etc)|
                      |           ↑             FS arrays ,----> +-----------------+
                     /            |             ,---------            /
                    /    +---------------+ -----                     /
                   /     |  FS model     |                           |
                  /      +---------------+                          /
                  |      = array[SymbStr] : FileState               | assertions
                  |                     \                          /  over
                  |                      \  FS transformation     /   FS arrays
                   \---                   \ array sequence        |
                       \---                \__                   /
                           \----              ↓                  ↓
                    constraints \---         +--------------------+
                    over            \---     |                    |
                    shell               \--->|     Z3 solver      |
                    state & strings          |                    |
                                             +--------------------+
```

# Component interfaces

## interp: AST, Traces -> Traces

## Trace := sequence of states

## Symbolic state := model of shell and FS states
Immutable.

Collects and tracks:
- local variables
- function definitions
- model FS state
- pathcondition under which this state is valid

## FS model := dict[SymbStr] : FileState
FileState := File | Dir | Deleted | Unknown

Explicit limitation of this model: file permissions, ownership, etc are out of scope.

Encoded in Z3 with arrays.

FS model component should maintain
1. a python representation of that filesystem model (the dict)
2. a z3 identifier for that state
3. a z3 assertion defining the delta of that FS state with respect to the prior FS state

Example:
Say the starting FS is called `fs0`, and just maps `"/"` to `Dir` and `"/usr"` to `Dir`.
Another filesystem model where the root is deleted would look like this:

``` json
{
  summary: {"/": Deleted, "/usr": Dir},
  z3id: "fs1",
  z3expr: "(assert fs1 (update fs0 "/" Deleted))"
}
```

Hence the z3exprs of a sequence of filesystem states are a chain of assertions defining the shape of each FS relative to the prior state.


Note that there's an important cascading effect of this design of having "Unknown" states that requires a design decision:
when assertions demand that a path is file, is it OK if it's unknown?
That means that we do not know anything about the path.
This could be a warning, but a simplifying design choice is to say that unknown is compatible with any state.
To support this, we will have a notion of state compatibility in the solver, defined as a function.

TODO: this compatibility design does preclude Sash from reporting things like required files possibly not being present. We conjecture that for practical scripts, this is probably desirable to reduce noise in reports.
We can evaluate this down the road to quantify the difference.
We can also have a "warning" or "strict" mode down the road that checks extra conditions like whether any commands receive paths that are not explicitly known to be of the right type.


## Command specs := function (CommandNode -> Spec)

Given a "substituted/expanded as much as possible" command node, return a spec describing the success conditions and effects of the command.

A Spec is a pair:

``` json
{
  precond: function (FS-model -> constraints),
  effects: {
    success: function (FS-model -> (FS-model, constraints)),
    failure: function (FS-model -> (FS-model, constraints))
  }
}
```

Notice that the command node may contain symbolic strings as arguments etc, so the spec returned can already have baked in some constraints about those symbolic strings.

For example, given `rm /a $2`, the spec returned could look something like:

``` json
{
  precond: (λ fs . (compatible(fs["/a"], File) ∧ compatible(fs[$2], File)))
  effects: {
    success: (λ fs . (update(update(fs, "/a", Deleted), $2, Deleted),
                      (fs["/a"] == File ∧ fs[$2] == File))),
    failure: (λ fs . (fs,
                      not(compatible(fs["/a"], File) ∧ compatible(fs[$2], File))))
  }
}
```

This example makes clear why it's valuable to have extra constraints provided for each of the success and failure cases.
While it might seem sufficient to just use the precondition and its negation, we may learn more specific info as a result of success or failure.
In the example, we learn in the success state that `fs["/a"]` and `fs[$2]` must both be files, which is more than the precond tells us (since that allows for them being unknown).


## Symbolic expander: ASTNode -> listof (ASTNode x (listof constraint))

The symbolic expander produces a list of all possible ways of that a command could be expanded, accounting for word splitting possibilities.



# Design Considerations

## Shell variables
In contrast to existing approaches, Sash gives unknown shell variables full treatment, reasoning about them symbolically.
This allows it to work on real shell scripts that regularly have unknown variables like script arguments.

## Loops
Explicit design choice: We will unroll loops up to a constant limit.
This introduces unsoundness.

## TODO Informative error messages
Open Q: What are the best ways to extract useful error and debugging info from Z3?

## TODO Concretization
Design approach here demands some thought. Need to survey which benchmarks require it, and in what ways.
