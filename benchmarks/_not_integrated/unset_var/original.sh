#!/bin/bash
# https://stackoverflow.com/questions/26526776/bash-variable-defaulting-doesnt-work-if-followed-by-pipe-bash-bug
file ${foo:=$1}
echo "foo >$foo<"
file ${bar:=$1} | cat
echo "bar >$bar<" # unset var, shellcheck warns
