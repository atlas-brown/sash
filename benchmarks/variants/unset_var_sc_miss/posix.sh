#!/bin/sh

file ${foo:=$1}
echo "foo >$foo<"

_set_bar() {
    : "${bar:=$1}"
}

file $bar | cat
echo "bar >$bar<"
