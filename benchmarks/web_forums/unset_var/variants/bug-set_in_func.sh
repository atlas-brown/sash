#!/bin/sh
file ${foo:=$1}
echo "foo >$foo<"
set_bar() { : "${bar:=$1}"; } # diff: set bar in a function but forget to call it
file $bar | cat
echo "bar >$bar<" # bug here: bar is unset

