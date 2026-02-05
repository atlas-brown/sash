#!/bin/sh

file ${foo:=$1}
echo "foo >$foo<"

_set_bar() {
    : "${bar:=$1}" # variant: `bar` is unbound unless `_set_bar` is invoked before the use of `bar`.
}

file $bar | cat
echo "bar >$bar<" # bug here: bar is unset
