# Notes

## Ways that a variable can be modified in a shell script

### Directly
- `var=val`
- `export`
- `readonly`
- `read`
- `unset`
- `alias`
- `unalias`
- `set -- val1 val2 ...` (sets positional parameters)
- `for var in list` (changes the value of `var` on every iteration of the loop)
- `arr+=val` (not POSIX, append to array)
- `arr[0]=val` (not POSIX, change array element)
- `local` (not POSIX)
- `declare` (not POSIX)
- `typeset` (not POSIX)

### Indirectly
- `${var=val}` (if `var` is unset, assign `val` to it)
- `${var:=val}` (if `var` is unset or empty, assign `val` to it)
- `.`
- `eval`
- `func()` (function calls)
- `$((...))` (arithmetic expansion)
- `cd` (affects `PWD` and `OLDPWD`)
- `getops` (affects `OPTARG` and `OPTIND`)
- `shift` (affects positional parameters)
- `source` (not POSIX)

### Automatically
- Commands exiting (`?`)
- Number of positional parameters (`#`)
- Positional parameters (`0`, `1`, ...)
- All positional parameters (`@`, `*`)
- Last argument of previous command (`_`, not POSIX)
- Status of each command in a pipeline (`PIPESTATUS`, not POSIX)
- Incremented each second (`SECONDS`, not POSIX)
- Changes any time it's read (`RANDOM`, not POSIX)
- Current line number in executing script (`LINENO`, not POSIX)


## Optimization opportunities
- Collapse traces
- Avoid forking in the first place
- Keep track of decisions about whether a variable is unset to avoid impossible traces where we decided at one point that the var is unset, and at another point that it is set (e.g. consider `something ${1:-default} ${1:-default}` -- there's only two possibilities here, either `$1` is unset and both expansions become default, or it is set and both become the same arbitrary)
