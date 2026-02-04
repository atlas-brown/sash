#!/bin/sh

file ${foo:=$1}
echo "foo >$foo<"

set_bar() {
    : "${bar:=$1}"
}

set_bar "$1"
file $bar | cat
echo "bar >$bar<"
